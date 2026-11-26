"""Adopt the existing local rows. Store reviews in SQLite and observe portable tags separately."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import time
import uuid

import dates

# These describe a release's on-disk identity and order. A pending edit must
# never change grouping or create a playback alias for an uninstalled title.
ORGANIZATION = {'album', 'albumArtist', 'discNumber', 'discTotal', 'trackNumber',
                'trackTotal', 'discSubtitle'}
PORTABLE = ORGANIZATION | {'title', 'artist', 'genre', 'composer', 'conductor',
    'orchestra', 'soloist', 'performer', 'work', 'movementName', 'movementNumber',
    'recordingDate', 'releaseDate', 'originalReleaseDate', 'compositionDate'}

ALIASES = {
    'albumartist': 'albumArtist', 'album artist': 'albumArtist', 'album_artist': 'albumArtist',
    'disc': 'discNumber', 'discnumber': 'discNumber', 'disctotal': 'discTotal',
    'totaldiscs': 'discTotal', 'track': 'trackNumber', 'tracknumber': 'trackNumber',
    'tracktotal': 'trackTotal', 'totaltracks': 'trackTotal', 'discsubtitle': 'discSubtitle',
    'soloists': 'soloist', 'movementname': 'movementName', 'movementnumber': 'movementNumber',
}
ALIASES.update({key.casefold(): key for key in PORTABLE})


def normalized(fields):
    result = {}
    for key, value in fields.items():
        result[ALIASES.get(key.casefold(), key)] = value
    for number, total in [('trackNumber', 'trackTotal'), ('discNumber', 'discTotal')]:
        if number in result:
            parts = str(result[number]).split('/')
            if len(parts) == 2 and all(v.isdecimal() for v in parts):
                result[number] = parts[0]
                result.setdefault(total, parts[1])
    for field in ('trackNumber', 'trackTotal', 'discNumber', 'discTotal'):
        if str(result.get(field)).isdecimal():
            result[field] = str(int(result[field]))
    return result


def projections(payload, registry):
    from music_catalog import tag_fields
    tags = tag_fields(payload['tags'])
    observed = {k:v for k,v in normalized(tags).items() if k in PORTABLE}
    research = normalized(payload.get('catalog_research', {}))
    reviewed = normalized(payload.get('reviewed', {}).get('fields', {}))
    protected = normalized(payload.get('overrides', {}).get('fields', {}))
    display = dict(tags)
    display.update(research)
    display.update(reviewed)
    display.update(observed)
    display.update(protected)
    # A proposed release grouping is visible only once the tags reflect it.
    for key in ORGANIZATION:
        if key in observed:
            display[key] = observed[key]
        else:
            display.pop(key, None)
    requested = {}
    for origin, values in [('research', research), ('reviewed', reviewed), ('protected', protected)]:
        for field, value in values.items():
            if field in PORTABLE and value not in (None, ''):
                requested[field] = {'origin': origin, 'value': value,
                    'status': 'observed_in_tags' if observed.get(field) == value else 'proposed'}
    # A source attached to a conflicting reviewed date cannot substantiate a
    # different raw date. Keep its evidence verbatim in the catalog record.
    for field, source in [('compositionDate', 'composition_source'), ('recordingDate', 'recording_source')]:
        if field in requested and requested[field]['value'] != display.get(field):
            display.pop(source, None)
    display = dates.catalog_fields(display, registry)
    # Preserve explicit review qualifiers while deriving normalized years.
    for key in ('composition_date_status', 'recording_date_status'):
        for layer in (research, reviewed, protected):
            if key in layer:
                display[key] = layer[key]
    return display, observed, requested


@contextmanager
def maintenance_lock():
    import fcntl
    lock = Path.home()/'.local/state/listening-room/library.lock'
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def publication_tracks(db, root):
    """Only exact, verified receipt members can share a released identity."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intake_publications'").fetchone():
        return {}
    tracks = {}
    for edition, destination, state, raw in db.execute(
            'SELECT edition,destination,state,manifest FROM intake_publications'):
        receipt = json.loads(raw)
        for track in receipt['tracks']:
            path = root / destination / track['path']
            if not path.is_relative_to(root) or '..' in path.parts or path in tracks:
                raise ValueError('Ambiguous or invalid publication path in catalog')
            tracks[path] = (edition, state, track['prepared_sha256'])
    return tracks


def adopted(db, root):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_authority_roots'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM library_authority_roots WHERE root=? AND version=1',
                           (str(Path(root).resolve()),)).fetchone())


def migrate_payload(payload, registry):
    from music_catalog import tag_fields
    if payload.get('authority_version') == 1:
        return
    previous = dict(tag_fields(payload['tags']))
    previous.update(payload.get('reviewed', {}).get('fields', {}))
    previous.update(payload.get('overrides', {}).get('fields', {}))
    baseline = dates.catalog_fields(previous, registry)
    # Legacy nightly and targeted catalog writers sometimes saved directly to
    # fields. Retain every non-derived deviation with its associated evidence.
    extras = {k:v for k,v in payload['fields'].items() if baseline.get(k) != v}
    extras.update(payload.get('catalog_research', {}))
    payload['catalog_research'] = extras
    payload['authority_version'] = 1


def save_fields(db, path, root, size, mtime, data, registry):
    from music_catalog import local_match, timing
    import itertools
    fields, _, _ = projections(data, registry)
    data['fields'] = fields
    db.execute('INSERT OR REPLACE INTO local_tracks VALUES (?,?,?,?,?)',
               (str(path), str(root), size, mtime, json.dumps(data, ensure_ascii=False)))
    db.execute('DELETE FROM aliases WHERE path=?', (str(path),))
    duration = timing.seconds(data['tags'].get('duration_seconds'))
    if duration:
        names = local_match.identities({}, {}, fields, data['tags'])
        db.executemany('INSERT OR IGNORE INTO aliases VALUES (?,?,?,?,?)',
            [(t,a,r,duration,str(path)) for t,a,r in itertools.product(*names)])


def baseline_files(store, root, snapshot, expected_sha):
    """Pin existing identities to independently selected recovery bytes before migration."""
    raw = Path(snapshot).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('Snapshot differs from independently selected generation')
    manifest = json.loads(raw)
    if (manifest.get('format') != 'listening-room-music/v2' or manifest.get('complete') is not True
            or manifest.get('scope') != root.name or manifest.get('coverage') != 'audio-and-companions'):
        raise ValueError('Expected a complete collection recovery manifest')
    files = {r['path']:r for r in manifest['files']}
    if len(files) != manifest['paths'] or len(files) != len(manifest['files']):
        raise ValueError('Invalid snapshot paths')
    with store.connect() as db:
        paths = db.execute('SELECT path,size,mtime FROM local_tracks WHERE root=?',(str(root),)).fetchall()
    stamps = {}
    for name, size, mtime in paths:
        path = Path(name)
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('Catalog path is redirected')
        if not path.exists():  # Preserve retired catalog records without binding new files to them.
            continue
        rel = str(path.relative_to(root.parent))
        row = files.get(rel)
        if not row or row['bytes'] != size or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('Catalog item differs from recovery inventory')
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size,mtime):
            raise ValueError('Catalog file changed before recovery verification')
        with path.open('rb') as handle:
            actual = hashlib.file_digest(handle,'sha256').hexdigest()
        if actual != row['sha256']:
            raise ValueError('Observed file differs from recovery object')
        stamps[name] = (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,actual)
    return stamps, len(paths)


def adopt(store, root, checkpoint, snapshot, expected_sha, frame_config=None):
    """Under the shared maintenance lock, save a verified full preimage once."""
    root = Path(root).resolve()
    if not root.is_dir() or not store.path.is_file():
        raise ValueError('Expected an existing library and catalog')
    with store.connect() as db:
        if adopted(db,root):
            return {'adopted': True, 'changed': 0}
    stamps, expected_count = baseline_files(store,root,snapshot,expected_sha)
    registry = store.document('work_dates', []) or []
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if adopted(db, root):
            return {'adopted': True, 'changed': 0}
        art = None
        if frame_config is not None:
            config_path = Path(frame_config).resolve()
            config = json.loads(config_path.read_text())
            if Path(config['library_root']).resolve() != root:
                raise ValueError('Frame configuration belongs to another library')
            art = config.get('local_art_overrides', {})
            if not isinstance(art, dict) or any(not isinstance(v,str) for v in art.values()):
                raise ValueError('Invalid artwork associations')
            # Validate the imported sources the running bridge currently uses.
            sources = {'liner':config.get('liner_notes_file'), 'music_context':config.get('music_context_file')}
            for kind,file in [('catalog_repairs','catalog-repairs.json'), ('work_dates','work-dates.json'),
                              ('recording_dates','recording-dates.json')]:
                sources[kind] = config_path.parent/file
            for kind, source_path in sources.items():
                if source_path and Path(source_path).is_file():
                    current = db.execute('SELECT payload FROM documents WHERE kind=?',(kind,)).fetchone()
                    if not current or json.loads(current[0]) != json.loads(Path(source_path).read_text()):
                        raise ValueError('Catalog and Frame source disagree for '+kind)
        disk = {}
        for kind, file in [('reviewed','enriched.json'), ('overrides','track-overrides.json')]:
            source = root/'.listening-room-metadata'/file
            if source.exists():
                document = json.loads(source.read_text())
                disk[kind] = document['tracks'] if kind == 'reviewed' else document
        rows = db.execute('SELECT path,size,mtime,payload FROM local_tracks WHERE root=?',
                          (str(root),)).fetchall()
        if len(rows) != expected_count:
            raise ValueError('Catalog changed during file verification')
        if not rows:
            raise ValueError('Run the existing library inventory before adoption')
        names = set()
        for path, size, mtime, raw in rows:
            file = Path(path)
            observed = stamps.get(path)
            if file.exists():
                st = file.stat()
                if (not observed or observed[:5] != (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
                        or (st.st_size,st.st_mtime_ns) != (size,mtime)):
                    raise ValueError('File changed after recovery verification')
            elif observed is not None:
                raise ValueError('File disappeared during recovery verification')
            relative = file.relative_to(root).as_posix()
            names.add(relative)
            old = json.loads(raw)
            for kind, entries in disk.items():
                if old.get(kind,{}) != entries.get(relative,{}):
                    raise ValueError('Catalog and sidecar disagree; reconcile before adoption')
        unbound = {}
        for kind, entries in disk.items():
            extra = set(entries) - names
            unbound[kind] = len(extra)
            if extra:
                doc_kind = 'enriched' if kind == 'reviewed' else 'overrides'
                saved = db.execute('SELECT payload FROM documents WHERE kind=?',(doc_kind,)).fetchone()
                snapshot = json.loads(saved[0]) if saved else {}
                catalog_entries = snapshot.get('tracks', {}) if kind == 'reviewed' else snapshot
                if any(catalog_entries.get(name) != entries[name] for name in extra):
                    raise ValueError('Unindexed sidecar evidence is missing from catalog documents')
        # A second connection reads the version protected by BEGIN IMMEDIATE.
        snapshot = Path(checkpoint).resolve()
        if snapshot.is_relative_to(root):
            raise ValueError('Checkpoint must be outside the served library')
        with snapshot.open('xb') as f:
            os.fchmod(f.fileno(), 0o600)
        with closing(sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro',uri=True)) as src:
            with closing(sqlite3.connect(snapshot)) as dest:
                src.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Checkpoint integrity check failed')
        for path, size, mtime, raw in rows:
            data = json.loads(raw)
            migrate_payload(data, registry)
            if path in stamps:
                data['verified_file_sha256'] = stamps[path][-1]
            save_fields(db, path, root, size, mtime, data, registry)
        db.execute('CREATE TABLE IF NOT EXISTS library_authority_roots (root TEXT PRIMARY KEY, '
                   'version INTEGER NOT NULL, adopted_at REAL NOT NULL, unbound_evidence TEXT NOT NULL, artwork TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS library_identity_pending (root TEXT, path TEXT, '
                   'size INTEGER, mtime INTEGER, PRIMARY KEY(root,path))')
        db.execute('INSERT INTO library_authority_roots VALUES(?,?,?,?,?)',
                   (str(root), 1, time.time(), json.dumps(unbound), json.dumps(art) if art is not None else None))
        return {'adopted':True, 'changed':len(rows), 'unbound_evidence':unbound, 'checkpoint':str(snapshot)}


def patch_review(store, root, proposal, undo=None):
    """Apply sourced fill-only reviews or guarded undo as a single transaction."""
    root = Path(root).resolve()
    registry = store.document('work_dates', []) or []
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not adopted(db, root):
            raise ValueError('The library must be adopted before applying a review')
        db.execute('CREATE TABLE IF NOT EXISTS local_review_undo (batch TEXT, root TEXT, '
                   'path TEXT, delta TEXT, PRIMARY KEY(batch,root,path))')
        batch = undo or uuid.uuid4().hex
        if undo:
            records = db.execute('SELECT path,delta FROM local_review_undo WHERE batch=? AND root=?',
                                 (batch,str(root))).fetchall()
            if not records:
                raise ValueError('Unknown or already undone batch')
            entries = {p:json.loads(d) for p,d in records}
        else:
            entries = proposal['tracks']
        changes = 0
        for name, entry in entries.items():
            rel = Path(name)
            path = (root/rel).resolve()
            if rel.is_absolute() or not path.is_relative_to(root) or path != root/rel or not path.is_file():
                raise ValueError('Expected a canonical local file')
            row = db.execute('SELECT size,mtime,payload FROM local_tracks WHERE path=? AND root=?',
                             (str(path),str(root))).fetchone()
            stamp = path.stat()
            if not row or row[:2] != (stamp.st_size,stamp.st_mtime_ns):
                raise ValueError('Local file identity needs verification')
            data = json.loads(row[2])
            saved = data.setdefault('reviewed', {})
            fields = saved.setdefault('fields', {})
            sources = saved.setdefault('provenance', {})
            protected = normalized(data.get('overrides', {}).get('fields', {}))
            delta = {}
            for field, value in entry['fields'].items():
                if undo:
                    if fields.get(field) != value or sources.get(field) != entry['provenance'][field]:
                        raise ValueError('Later research conflicts with undo')
                    del fields[field]
                    if field in entry.get('prior_sources', {}):
                        sources[field] = entry['prior_sources'][field]
                    else:
                        sources.pop(field, None)
                    changes += 1
                else:
                    canonical = ALIASES.get(field.casefold(), field)
                    source = entry.get('provenance', {}).get(field)
                    if not isinstance(source, str) or not source.strip() or value in ('',None):
                        raise ValueError('Every field requires a value and source')
                    if canonical in protected or canonical in normalized(fields):
                        continue
                    delta[field] = value
            if delta:
                old_sources = {f:sources[f] for f in delta if f in sources}
                evidence = {f: entry['provenance'][f] for f in delta}
                fields.update(delta)
                sources.update(evidence)
                db.execute('INSERT INTO local_review_undo VALUES (?,?,?,?)', (batch,str(root),name,
                    json.dumps({'fields':delta,'provenance':evidence,'prior_sources':old_sources})))
                changes += len(delta)
            if delta or undo:
                save_fields(db, path, root, *row[:2], data, registry)
        if undo:
            db.execute('DELETE FROM local_review_undo WHERE batch=? AND root=?', (batch,str(root)))
        return {'changes':changes, 'batch':batch if changes else None}


def edit_document(store, root, edit=None, undo=None):
    """Compare and swap one reviewed record. Keep its inverse in the same commit."""
    root = str(Path(root).resolve())
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not adopted(db, root):
            raise ValueError('An adopted library is required')
        db.execute('CREATE TABLE IF NOT EXISTS document_record_undo (batch TEXT PRIMARY KEY, '
                   'root TEXT NOT NULL, delta TEXT NOT NULL)')
        if undo:
            item = db.execute('SELECT delta FROM document_record_undo WHERE batch=? AND root=?',
                              (undo,root)).fetchone()
            if not item:
                raise ValueError('Unknown or already undone document edit')
            edit = json.loads(item[0])
            before, after = edit['after'], edit['before']
        else:
            before, after = edit['before'], edit['after']
            if after is None or not isinstance(edit.get('source'),str) or not edit['source'].strip():
                raise ValueError('The reviewed edit needs a replacement record and source')
        kind = edit['kind']
        if kind == 'artwork':
            current = db.execute('SELECT artwork FROM library_authority_roots WHERE root=?',(root,)).fetchone()[0]
            data = json.loads(current) if current else {}
            records = data
        else:
            if kind not in ('music_context','liner','work_dates','recording_dates','catalog_repairs'):
                raise ValueError('Expected a supported catalog document')
            row = db.execute('SELECT payload FROM documents WHERE kind=?',(kind,)).fetchone()
            if not row:
                raise ValueError('Missing catalog document')
            data = json.loads(row[0])
            records = data['albums'] if kind in ('music_context','liner') else data
        if isinstance(records, dict):
            key = edit['key']
            if not isinstance(key,str) or not key:
                raise ValueError('Expected an exact nonempty record key')
            if records.get(key) != before:
                if not undo and records.get(key) == after:
                    return {'changes':0}
                raise ValueError('Document changed since review')
            if after is None:
                del records[key]
            else:
                if kind == 'artwork' and not isinstance(after,str):
                    raise ValueError('Expected an artwork locator')
                records[key] = after
        else:
            if not isinstance(records,list) or (after is not None and not isinstance(after,dict)):
                raise ValueError('Expected a single catalog record')
            if before is None:
                if after in records:
                    return {'changes':0}
                records.append(after)
            else:
                if records.count(before) != 1:
                    raise ValueError('Document changed or source record is ambiguous')
                at = records.index(before)
                if after is None:
                    del records[at]
                elif after != before and after in records:
                    raise ValueError('Duplicate target record')
                else:
                    records[at] = after
        if before == after:
            return {'changes':0}
        raw = json.dumps(data, ensure_ascii=False)
        if kind == 'artwork':
            db.execute('UPDATE library_authority_roots SET artwork=? WHERE root=?',(raw,root))
        else:
            db.execute('UPDATE documents SET payload=?,saved_at=? WHERE kind=?',(raw,time.time(),kind))
        if undo:
            db.execute('DELETE FROM document_record_undo WHERE batch=?',(undo,))
            return {'changes':1,'undone':undo}
        batch = uuid.uuid4().hex
        db.execute('INSERT INTO document_record_undo VALUES(?,?,?)',(batch,root,json.dumps(edit)))
        return {'changes':1,'batch':batch}


if __name__ == '__main__':
    from music_catalog import Store
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    commands = parser.add_subparsers(dest='action',required=True)
    migration = commands.add_parser('adopt')
    migration.add_argument('--checkpoint', required=True, type=Path)
    migration.add_argument('--snapshot', required=True, type=Path)
    migration.add_argument('--snapshot-sha256', required=True)
    migration.add_argument('--frame-config', required=True, type=Path)
    commands.add_parser('review').add_argument('proposal',type=Path)
    commands.add_parser('undo').add_argument('batch')
    commands.add_parser('pending')
    commands.add_parser('status').add_argument('path',type=Path)
    commands.add_parser('edit-record').add_argument('proposal',type=Path)
    commands.add_parser('undo-record').add_argument('batch')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Expected an existing catalog')
    store = Store(args.database)
    if args.action == 'pending':
        with store.connect() as db:
            result = [dict(path=p, size=b, mtime_ns=t) for p,b,t in db.execute(
                'SELECT path,size,mtime FROM library_identity_pending WHERE root=? ORDER BY path',
                (str(args.root.resolve()),))]
    elif args.action == 'status':
        from urllib.parse import quote
        path = (args.root/args.path).resolve().relative_to(args.root.resolve())
        result = store.portable_status('http://local.invalid/music/'+quote(str(path)), args.root, '/music/')
    else:
        with maintenance_lock():
            if args.action == 'adopt':
                result = adopt(store, args.root, args.checkpoint, args.snapshot, args.snapshot_sha256, args.frame_config)
            elif args.action == 'edit-record':
                result = edit_document(store,args.root,json.loads(args.proposal.read_text()))
            elif args.action == 'undo-record':
                result = edit_document(store,args.root,undo=args.batch)
            elif args.action == 'review':
                result = patch_review(store, args.root, json.loads(args.proposal.read_text()))
            else:
                result = patch_review(store, args.root, None, args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))

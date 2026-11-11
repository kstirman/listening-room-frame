"""Offline catalog audit and consistent, credential-free metadata backup."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile
import classical
import dates
import local_composition
import timing


def audit(database):
    """Report missing evidence; never infer that a fragment is a whole work."""
    with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro', uri=True) as db:
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise ValueError('Catalog integrity check failed')
        local = [(p, size, mtime, json.loads(payload)) for p, size, mtime, payload in db.execute(
            'SELECT path,size,mtime,payload FROM local_tracks')]
        editions = [(key, json.loads(payload)) for key, payload in db.execute('SELECT id,payload FROM editions')]
        recording_row = db.execute("SELECT payload FROM documents WHERE kind='recording_dates'").fetchone()
        recording_registry = json.loads(recording_row[0]) if recording_row else {}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        pending = db.execute('SELECT count(*) FROM encountered_albums e WHERE e.album_id IS NULL OR NOT EXISTS (SELECT 1 FROM editions d WHERE d.id=e.album_id)').fetchone()[0] if 'encountered_albums' in tables else 0
    counts = collections.Counter(); issues = []; groups = collections.defaultdict(dict)
    for path, size, mtime, payload in local:
        f = payload['fields']; p = Path(path)
        if not p.is_file():
            counts['missing_files'] += 1
            continue
        stat = p.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size, mtime):
            counts['changed_files'] += 1
            continue
        counts['current_files'] += 1
        if not dates.years(f.get('recordingDate')):
            counts['missing_recording_date'] += 1
        if f.get('work') and f.get('composer'):
            groups[(f['work'], f['composer'])][path] = f
        elif classical.normalize(f):
            counts['classical_without_explicit_work'] += 1
            issues.append({'path': path, 'reason': 'work_identity_needs_review', 'title': f.get('title')})
        if not timing.seconds(f.get('duration_seconds')):
            counts['missing_duration'] += 1
    for candidates in groups.values():
        for path, fields in candidates.items():
            context = local_composition.build(candidates, path)
            if context:
                counts['tracks_with_work_context'] += 1
            elif fields.get('movementName') or fields.get('movementNumber'):
                counts['movement_tracks_without_context'] += 1
                issues.append({'path': path, 'reason': 'movement_group_needs_review', 'work': fields['work'], 'title': fields.get('title')})
    stream = collections.Counter()
    for album_id, original in editions:
        rows = classical.catalog(original)
        stream['tracks'] += len(rows)
        if any(r.get('_catalog_complete') is False for r in rows.values()):
            stream['incomplete_editions'] += 1
        for key, row in rows.items():
            date = dates.extract(dict(row, object_id='qobuz/track/' + str(row.get('_catalog_track_id', key))), recording_registry=recording_registry, work_registry=[])
            if not date.get('recording_year'):
                stream['missing_recording_date'] += 1
            if timing.composition(rows, key):
                stream['tracks_with_work_context'] += 1
            elif row.get('movementName'):
                stream['movement_tracks_without_context'] += 1
                issues.append({'album_id': album_id, 'track_id': key, 'reason': 'streamed_movement_group_needs_review', 'work': row.get('work'), 'title': row.get('title')})
    return {'integrity': integrity, 'local_records': len(local), 'local': dict(counts),
            'editions': len(editions), 'streamed': dict(stream), 'pending_albums': pending,
            'issues': issues, 'note': 'Review flags are not confirmed errors; standalone works and excerpts may legitimately have no group.'}


def backup(database, output):
    """Snapshot SQLite, export durable source documents, and verify every byte."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); snapshot = root / 'music-catalog.sqlite3'
        with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro', uri=True) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
            sources = []
            for kind, source_path, payload in dest.execute('SELECT kind,source,payload FROM documents'):
                if not kind.replace('_', '').isalnum():
                    raise ValueError('Unsafe document name')
                json.loads(payload)
                path = root / 'documents' / (kind + '.json'); path.parent.mkdir(exist_ok=True)
                path.write_text(payload)
                sources.append({'kind': kind, 'original_path': source_path, 'archive_path': str(path.relative_to(root))})
        (root/'RESTORE.json').write_text(json.dumps({'documents': sources, 'instructions':
            'Stop only the artwork service before replacing its SQLite database. Preserve newer data first. Restore source documents only after comparing current versions; they remain authoritative. Restart artwork and verify. This archive excludes audio, photos, tokens, passwords and application configuration.'}, indent=2))
        files = sorted(p for p in root.rglob('*') if p.is_file())
        manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        (root/'SHA256.json').write_text(json.dumps(manifest, indent=2))
        temp = output.with_suffix(output.suffix+'.tmp')
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(root.rglob('*')):
                if p.is_file(): archive.write(p, str(p.relative_to(root)))
        temp.chmod(0o600)
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip(): raise ValueError('Archive CRC failed')
            for name, expected in manifest.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError('Archive checksum failed')
        temp.replace(output)
    return {'path': str(output), 'size': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': len(manifest)+1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'backup'])
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        print(json.dumps(backup(args.database, args.output)))
    else:
        report = audit(args.database)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in report.items() if k != 'issues'}))

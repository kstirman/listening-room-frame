"""Private, permanent recording catalog shared by playback sources.

Source editions and local files retain independent identities. Similar titles
never merge recordings. JSON payloads retain original fields and provenance;
SQLite indexes exact aliases for bounded, conservative playback lookup.
"""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import functools
import itertools
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import quote
import local_composition
import local_match
import timing
import classical

LOG = logging.getLogger(__name__)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalogs(album TEXT, artist TEXT, version INTEGER,
 saved_at REAL, payload TEXT, PRIMARY KEY(album,artist));
CREATE TABLE IF NOT EXISTS editions(id TEXT PRIMARY KEY, saved_at REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS documents(kind TEXT PRIMARY KEY, source TEXT, stamp TEXT,
 saved_at REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS local_tracks(path TEXT PRIMARY KEY, root TEXT,
 size INTEGER, mtime INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS aliases(title TEXT, album TEXT, artist TEXT, duration REAL,
 path TEXT, PRIMARY KEY(title,album,artist,path));
CREATE INDEX IF NOT EXISTS alias_lookup ON aliases(title,album,artist,duration);
CREATE TABLE IF NOT EXISTS observations(identity TEXT PRIMARY KEY, saved_at REAL,
 payload TEXT);
'''


def key(value):
    return local_match.key(value)


def available(default=None):
    """Storage failures degrade to existing metadata paths, never stop artwork."""
    def decorate(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:return fn(*args, **kwargs)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                LOG.warning('Music catalog unavailable: %s', fn.__name__)
                return default
        return call
    return decorate


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.sync_lock = threading.Lock()
        self.last_sync = time.monotonic()
        self.revision = 0

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.executescript(SCHEMA)
            with db:
                yield db

    def migrate(self, old):
        """Idempotent copy; never delete or mutate the legacy cache."""
        if not Path(old).is_file():
            return
        with closing(sqlite3.connect('file:'+str(Path(old).resolve())+'?mode=ro', uri=True)) as src:
            rows = src.execute('SELECT album,artist,version,saved_at,payload FROM catalogs').fetchall()
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO catalogs VALUES(?,?,?,?,?)', rows)

    def load(self, lookup_key):
        if not self.path.exists():
            return None
        try:
            with self.connect() as db:
                row = db.execute('SELECT saved_at,payload FROM catalogs WHERE album=? AND artist=? AND version=1', lookup_key).fetchone()
            if not row or len(row[1]) > 2_000_000:
                return None
            albums = json.loads(row[1])
            if not isinstance(albums, list) or not 1 <= len(albums) <= 12 or any(
                    not isinstance(rows, dict) or not 1 <= len(rows) <= 2048 or
                    any(not isinstance(v, dict) for v in rows.values()) for rows in albums):
                return None
            for rows in albums:
                for track_id, fields in rows.items():
                    fields.setdefault('_catalog_track_id', track_id)
                    fields.setdefault('_provenance', 'saved public catalog')
            # Keep original source fields on disk; apply current grouping rules
            # when reading saved editions so fixes do not require a refetch.
            return [classical.catalog(rows) for rows in albums], not 0 <= time.time()-row[0] < 7*86400
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None

    def save(self, lookup_key, albums):
        payload = json.dumps(albums)
        if not albums or len(payload) > 2_000_000:
            return
        try:
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO catalogs VALUES(?,?,?,?,?)', (*lookup_key,1,time.time(),payload))
                for rows in albums:
                    ids = {r.get('_catalog_album_id') for r in rows.values()}
                    if len(ids) == 1 and None not in ids:
                        db.execute('INSERT OR REPLACE INTO editions VALUES(?,?,?)',
                                   (next(iter(ids)),time.time(),json.dumps(rows)))
        except (OSError, sqlite3.Error):
            LOG.warning('Music catalog write unavailable')

    def edition(self, album_id):
        try:
            with self.connect() as db:
                row = db.execute('SELECT payload FROM editions WHERE id=?',(album_id,)).fetchone()
            return classical.catalog(json.loads(row[0])) if row else None
        except (OSError, sqlite3.Error, ValueError):
            return None

    @available()
    def put_edition(self, album_id, rows, album, artist):
        """Save an explicit edition and add it to, never replace, known alternatives."""
        if not re.fullmatch(r'[A-Za-z0-9]{1,64}', album_id) or not rows:
            return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO editions VALUES(?,?,?)', (album_id,time.time(),json.dumps(rows)))
        if album and artist:
            lookup_key = (key(album),key(artist))
            prior = self.load(lookup_key)
            alternatives = prior[0] if prior else []
            alternatives = [r for r in alternatives if not all(v.get('_catalog_album_id') == album_id for v in r.values())]
            if len(alternatives) < 12:
                self.save(lookup_key, alternatives+[rows])

    def sync_documents(self, config, source_dir):
        paths = {'liner':config.get('liner_notes_file'),
                 'work_dates':Path(source_dir)/'work-dates.json',
                 'recording_dates':Path(source_dir)/'recording-dates.json'}
        with self.connect() as db:
            for kind, filename in paths.items():
                if not filename or not Path(filename).is_file():
                    continue
                p = Path(filename); s = p.stat(); stamp = f'{s.st_mtime_ns}:{s.st_size}'
                old = db.execute('SELECT source,stamp FROM documents WHERE kind=?',(kind,)).fetchone()
                if old == (str(p),stamp):
                    continue
                payload = p.read_text(); json.loads(payload)
                db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)', (kind,str(p),stamp,time.time(),payload))
                self.revision += 1

    @available()
    def document(self, kind, default=None):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM documents WHERE kind=?',(kind,)).fetchone()
        return json.loads(row[0]) if row else default

    def sync_library(self, root):
        """Read tags only for new/changed files; reviewed overlays always win.

        No audio writes. A failed probe is reported and never replaces good data.
        Retired paths remain in the catalog but cannot match a missing file.
        """
        root = Path(root).resolve(); meta = root/'.listening-room-metadata'
        inventory_path = meta/'inventory.json'
        inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else {}
        reviewed_path = meta/'enriched.json'; overrides_path = meta/'track-overrides.json'
        reviewed = json.loads(reviewed_path.read_text()).get('tracks',{}) if reviewed_path.exists() else {}
        overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
        paths = sorted(p for p in root.rglob('*') if p.suffix.lower() in {'.flac','.wav','.aiff','.aif','.m4a','.mp3','.dsf','.ape','.ogg','.opus','.wv'} and p.is_file())
        result = {'files':len(paths),'saved':0,'probed':0,'errors':0}
        with self.connect() as db:
            prior = {name:(size,mtime,payload) for name,size,mtime,payload in db.execute(
                'SELECT path,size,mtime,payload FROM local_tracks WHERE root=?',(str(root),))}
        def read(path):
            p = path.resolve()
            if not p.is_relative_to(root):return None
            try:
                stat = p.stat(); old = prior.get(str(p))
                unchanged = old and old[:2] == (stat.st_size,stat.st_mtime_ns)
                tags = json.loads(old[2])['tags'] if unchanged else local_composition.probe(p)
                after = p.stat()
                if (after.st_size,after.st_mtime_ns) != (stat.st_size,stat.st_mtime_ns):return None
                return p,stat,old,tags,not unchanged
            except Exception:return None
        with ThreadPoolExecutor(max_workers=4) as readers, self.connect() as db:
            for item in readers.map(read,paths):
                if item is None:
                    result['errors'] += 1
                    continue
                p,stat,old,tags,probed = item
                result['probed'] += int(probed)
                name = str(p.relative_to(root))
                try:
                    fields = tag_fields(tags)
                    fields.update(reviewed.get(name,{}).get('fields',{}))
                    fields.update(overrides.get(name,{}).get('fields',{}))
                    # Current tags plus reviewed aliases, not unverified old inventory.
                    identities = local_match.identities({}, {}, fields, tags)
                    payload = json.dumps({'tags':tags,'fields':fields,
                        'reviewed':reviewed.get(name,{}),'overrides':overrides.get(name,{}),
                        'source':str(p),'review_source':str(reviewed_path),'override_source':str(overrides_path)})
                    if old and old[2] == payload:
                        result['saved'] += 1
                        continue
                    db.execute('INSERT OR REPLACE INTO local_tracks VALUES(?,?,?,?,?)', (str(p),str(root),stat.st_size,stat.st_mtime_ns,payload))
                    db.execute('DELETE FROM aliases WHERE path=?',(str(p),))
                    duration = timing.seconds(tags.get('duration_seconds'))
                    if duration:
                        db.executemany('INSERT OR IGNORE INTO aliases VALUES(?,?,?,?,?)',
                            [(t,a,r,duration,str(p)) for t,a,r in itertools.product(*identities)])
                    result['saved'] += 1
                    if result['saved'] % 25 == 0:db.commit()
                except Exception:
                    result['errors'] += 1
            # Keep inventory and reviewed source provenance within the same DB.
            for kind,p in [('inventory',inventory_path),('enriched',reviewed_path),('overrides',overrides_path)]:
                if p.exists():
                    db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)', (kind,str(p),str(p.stat().st_mtime_ns),time.time(),p.read_text()))
        self.revision += 1
        return result

    def refresh(self, config, source_dir):
        if time.monotonic()-self.last_sync < 900 or not self.sync_lock.acquire(False):
            return
        self.last_sync = time.monotonic()
        def run():
            try:
                self.sync_documents(config, source_dir)
                if config.get('library_root'):
                    self.sync_library(config['library_root'])
            except Exception as error:
                LOG.warning('Music catalog refresh deferred: %s',type(error).__name__)
            finally:
                self.sync_lock.release()
        threading.Thread(target=run, daemon=True).start()

    @available()
    def local(self, resource, root, prefix):
        target = local_composition.target_file(resource,root,prefix)
        if not target:
            return None
        with self.connect() as db:
            row = db.execute('SELECT size,mtime,payload FROM local_tracks WHERE path=?',(str(target),)).fetchone()
            if not row:
                return None
            stat = target.stat()
            if row[:2] != (stat.st_size,stat.st_mtime_ns):
                return None
            fields = json.loads(row[2])['fields']
            context = None
            if fields.get('work') and fields.get('composer'):
                candidates = {}
                for path,size,mtime,payload in db.execute('SELECT path,size,mtime,payload FROM local_tracks WHERE root=?',(str(Path(root).resolve()),)):
                    item = json.loads(payload)['fields']
                    if (item.get('work'),item.get('composer')) != (fields['work'],fields['composer']):
                        continue
                    p = Path(path)
                    if not p.is_file() or (p.stat().st_size,p.stat().st_mtime_ns) != (size,mtime):
                        return fields,None
                    candidates[path] = item
                context = local_composition.build(candidates,str(target))
            return fields,context

    @available((None, 'local_unavailable'))
    def match_local(self, metadata, root, prefix):
        wanted = tuple(key(metadata.get(k)) for k in ('title','album','artist'))
        duration = timing.seconds(metadata.get('duration_seconds'))
        if not all(wanted) or not duration:
            return None,'local_insufficient'
        with self.connect() as db:
            paths = db.execute('SELECT DISTINCT path FROM aliases WHERE title=? AND album=? AND artist=? AND duration BETWEEN ? AND ?',(*wanted,duration-1,duration+1)).fetchall()
        matches = []
        root = Path(root).resolve()
        for (name,) in paths:
            path = Path(name)
            if not path.is_relative_to(root):
                continue
            resource = 'http://local.invalid'+prefix+quote(str(path.relative_to(root)),safe='/')
            item = self.local(resource,root,prefix)
            if item:
                matches.append((resource,item[0]))
        if len(matches)>1:
            return None,'local_ambiguous'
        return (matches[0],'local_matched') if matches else (None,'local_unmatched')

    def remember(self, metadata, details):
        """Retain resolved facts; never use opaque proxy URLs as recording IDs."""
        if not details or details.get('catalog_status') not in ('matched','matched_track','local_matched','catalog_id'):
            return
        identity = json.dumps([metadata.get(k) for k in ('album_id','album','artist','title','duration_seconds')])
        # Source identifiers and complete fields remain in editions/local_tracks.
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?)',(identity,time.time(),json.dumps(details)))


def tag_fields(tags):
    fields = dict(tags)
    aliases = {'albumartist':'albumArtist','album_artist':'albumArtist','movementname':'movementName',
        'movementnumber':'movementNumber','recordingdate':'recordingDate','recording_date':'recordingDate',
        'compositiondate':'compositionDate','composition_date':'compositionDate',
        'originaldate':'originalReleaseDate','originalyear':'originalReleaseDate',
        'releasedate':'releaseDate','soloists':'soloist','year':'date','ensemble':'orchestra',
        'composition':'work','recordeddate':'recordingDate','recordingyear':'recordingDate',
        'recording_year':'recordingDate','compositionyear':'compositionDate',
        'composition_year':'compositionDate','composedate':'compositionDate',
        'originalreleasedate':'originalReleaseDate'}
    for src,dest in aliases.items():
        if tags.get(src):fields[dest]=tags[src]
    if tags.get('movement'):
        field = 'movementNumber' if re.fullmatch(r'\d+(?:/\d+)?',tags['movement']) else 'movementName'
        fields.setdefault(field,tags['movement'])
    return fields


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--library-root')
    parser.add_argument('--legacy-cache')
    args = parser.parse_args(); store = Store(args.database)
    if args.legacy_cache:store.migrate(args.legacy_cache)
    if args.library_root:print(json.dumps(store.sync_library(args.library_root)))

"""Permanent local music facts; no TTL, automatic refresh, or deletion.

JPLAY identifies editions/tracks. MusicBrainz discovery runs off the display
thread. Search candidates are evidence, not permission to merge editions.
The shared catalog contains all records and previous versions for backup.
"""
import argparse
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import gzip
import requests

LOG = logging.getLogger(__name__)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata_records(
 provider TEXT, identity TEXT, saved_at REAL, payload TEXT,
 PRIMARY KEY(provider,identity));
CREATE TABLE IF NOT EXISTS metadata_versions(
 provider TEXT, identity TEXT, digest TEXT, saved_at REAL, payload TEXT,
 PRIMARY KEY(provider,identity,digest));
CREATE TABLE IF NOT EXISTS metadata_attempts(
 provider TEXT, identity TEXT, saved_at REAL, status TEXT,
 PRIMARY KEY(provider,identity));
'''
UA = 'ListeningRoomFrame/1.0 (https://github.com/kstirman/listening-room-frame)'


def canonical(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def filled(value):
    return value is not None and value != '' and value != [] and value != {}


def merge_missing(old, new):
    """Learn missing fields without erasing or silently revising prior work."""
    result = dict(old)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = merge_missing(result[k], v)
        elif not filled(result.get(k)) and filled(v):
            result[k] = v
    return result


def existing_digests(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'metadata_%'")]
    return {name:hashlib.sha256(json.dumps(db.execute('SELECT * FROM "'+name.replace('"','""')+'" ORDER BY rowid').fetchall(),sort_keys=True).encode()).hexdigest() for name in names}


class Cache:
    def __init__(self, path):
        self.path = Path(path)

    def get(self, provider, identity):
        if not self.path.exists():return None
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                row = db.execute('SELECT payload FROM metadata_records WHERE provider=? AND identity=?',
                                 (provider, identity)).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError):return None

    def attempted(self, provider, identity):
        if not self.path.exists():return False
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
                return db.execute('SELECT 1 FROM metadata_attempts WHERE provider=? AND identity=?',
                                  (provider,identity)).fetchone() is not None
        except sqlite3.Error:return False

    def cooling_down(self, provider, identity, seconds):
        if not self.path.exists():return False
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
                row = db.execute('SELECT saved_at FROM metadata_attempts WHERE provider=? AND identity=?',
                                 (provider,identity)).fetchone()
            return bool(row and time.time()-row[0] < seconds)
        except sqlite3.Error:return False

    def write(self, records, attempt=None, verify_existing=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.executescript(SCHEMA)
            with db:
                db.execute('BEGIN IMMEDIATE')
                before = existing_digests(db) if verify_existing else None
                for provider, identity, payload in records:
                    if not isinstance(payload, dict) or not payload:continue
                    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    stamp = time.time()
                    db.execute('INSERT OR IGNORE INTO metadata_versions VALUES(?,?,?,?,?)',
                               (provider,identity,digest,stamp,text))
                    row = db.execute('SELECT payload FROM metadata_records WHERE provider=? AND identity=?',
                                     (provider,identity)).fetchone()
                    merged = merge_missing(json.loads(row[0]), payload) if row else payload
                    updated = json.dumps(merged, ensure_ascii=False, sort_keys=True)
                    if not row or row[0] != updated:
                        db.execute('INSERT OR REPLACE INTO metadata_records VALUES(?,?,?,?)',
                                   (provider,identity,stamp,updated))
                if attempt:
                    db.execute('INSERT OR REPLACE INTO metadata_attempts VALUES(?,?,?,?)',
                               (attempt[0],attempt[1],time.time(),attempt[2]))
                if verify_existing:
                    assert existing_digests(db) == before, 'Unrelated catalog data changed'
                    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'

    def jplay(self, metadata):
        aid = metadata.get('album_id')
        album = self.get('jplay', aid) if aid else None
        if not album:return {}, None
        # An exact provider ID is mandatory; never infer a track from duration.
        result = {k: album[k] for k in ('album', 'artist', 'releaseDate', 'genre') if album.get(k)}
        if album.get('artist'):result['albumArtist'] = album['artist']
        match = re.fullmatch(r'qobuz/track/(\d+)', metadata.get('object_id', ''))
        track = album.get('tracks', {}).get(match[1]) if match else None
        if track:
            # Reject incompatible metadata even when a caller supplies an ID.
            expected, actual = track.get('duration_seconds'), metadata.get('duration_seconds')
            title = metadata.get('title')
            if ((actual and expected and abs(float(actual)-float(expected)) > 1)
                    or (title and canonical(title) != canonical(track.get('title')))):
                return result, album
            result.update({k: track[k] for k in ('title','artist','composer','work','duration_seconds') if track.get(k)})
        return result, album


def jplay_records(document):
    if document.get('schema') != 1 or document.get('source') != 'JPLAY local music database':
        raise ValueError('Not a JPLAY music export')
    records = []
    for album in document['albums']:
        aid = str(album['album_id'])
        if not re.fullmatch(r'[A-Za-z0-9]{1,64}', aid):raise ValueError('Invalid album ID')
        if not album.get('album') or not album.get('artist'):continue
        safe = {k: album[k] for k in ('album_id','album','artist','version','expected_tracks',
                'volumes','duration_seconds','releaseDate','genre') if filled(album.get(k))}
        safe['tracks'] = {}
        for tid, track in album.get('tracks', {}).items():
            if not re.fullmatch(r'\d+', tid) or not track.get('title'):raise ValueError('Invalid track')
            if track.get('album') and canonical(track['album']) != canonical(album['album']):
                raise ValueError('Conflicting album identity')
            safe['tracks'][tid] = {k: track[k] for k in ('title','artist','album','composer','work',
                                  'version','track_number','duration_seconds','releaseDate') if filled(track.get(k))}
        safe['provenance'] = {'source':'JPLAY local music database', 'identity':'provider album and track IDs'}
        records.append(('jplay', aid, safe))
    return records


class MusicBrainz:
    def __init__(self, cache, get=requests.get):
        self.cache, self.get = cache, get
        self.request_lock = threading.Lock()
        self.last_request = 0

    def request(self, entity, params):
        with self.request_lock:
            return self._request(entity, params)

    def _request(self, entity, params):
        params = dict(params, fmt='json')
        identity = entity+'?'+json.dumps(params, sort_keys=True)
        saved = self.cache.get('musicbrainz-response', identity)
        if saved is not None:return saved
        delay = 1.1 - (time.monotonic() - self.last_request)
        if delay > 0:time.sleep(delay)
        self.last_request = time.monotonic()
        r = self.get('https://musicbrainz.org/ws/2/'+entity, params=params,
                     headers={'User-Agent':UA}, timeout=(4,12))
        r.raise_for_status()
        if len(r.content)>4_000_000:raise ValueError('Metadata response too large')
        payload = r.json()
        if not isinstance(payload,dict):raise ValueError('Invalid metadata response')
        self.cache.write([('musicbrainz-response',identity,payload)])
        return payload

    def discover(self, album):
        def quote(s):return '"'+re.sub(r'([\\"+\-!(){}\[\]^~*?:/])', r'\\\1', s)+'"'
        search = self.request('release', {'query':'release:'+quote(album['album'])+
                     ' AND artist:'+quote(album['artist']), 'limit':5})
        candidates = []
        for hit in search.get('releases', []):
            if canonical(hit.get('title')) != canonical(album['album']):continue
            if not re.fullmatch(r'[0-9a-f-]{36}', hit.get('id','')):continue
            release = self.request('release/'+hit['id'], {'inc':
                'artist-credits+recordings+recording-level-rels+work-rels+artist-rels+url-rels+labels'})
            candidates.append(release)
        # Search similarity alone cannot establish edition identity.
        exact = [r for r in candidates if exact_release(album, r)]
        result = {'candidates':[r['id'] for r in candidates], 'status':'needs_review',
                  'source':'https://musicbrainz.org', 'fields':{}, 'tracks':{}}
        if len(exact) == 1:
            result.update(mb_facts(album, exact[0]), status='exact_edition')
        elif not candidates:result['status']='not_found'
        return result


def exact_release(album, release):
    aid = album['album_id']
    linked = False
    for rel in release.get('relations', []):
        u = urlparse(rel.get('url', {}).get('resource', ''))
        if u.hostname in ('qobuz.com','www.qobuz.com','open.qobuz.com','play.qobuz.com'):
            parts = u.path.rstrip('/').split('/')
            if 'album' in parts and parts[-1] == aid:linked = True
    if not linked:return False
    tracks = album.get('tracks', {})
    observed = [t for m in release.get('media', []) for t in m.get('tracks', [])]
    if not tracks or len(tracks) != album.get('expected_tracks') or len(tracks) != len(observed):return False
    remaining = list(observed)
    for track in tracks.values():
        matches = [t for t in remaining if canonical(t.get('title')) == canonical(track.get('title'))
                   and t.get('length') and track.get('duration_seconds')
                   and abs(t['length']/1000-float(track['duration_seconds'])) <= 2]
        if len(matches) != 1:return False
        remaining.remove(matches[0])
    return True


def mb_facts(album, release):
    fields = {}
    if release.get('date'):fields['releaseDate'] = release['date']
    labels = list(dict.fromkeys(r['label']['name'] for r in release.get('label-info', []) if r.get('label',{}).get('name')))
    if labels:fields['label'] = '; '.join(labels)
    observed = [t for m in release.get('media', []) for t in m.get('tracks', [])]
    tracks = {}
    for tid, row in album['tracks'].items():
        match = next(t for t in observed if canonical(t.get('title')) == canonical(row['title'])
                     and abs(t['length']/1000-float(row['duration_seconds'])) <= 2)
        recording = match.get('recording', {})
        facts = {'musicbrainz_recording_id':recording.get('id')}
        # Keep relationships as sourced evidence. They need review before work
        # grouping, date interpretation, or an influence claim can be displayed.
        facts['relationships'] = recording.get('relations', [])
        tracks[tid] = facts
    links = [r.get('url',{}).get('resource') for r in release.get('relations', [])]
    discogs = [u for u in links if u and re.fullmatch(r'https?://(?:www\.)?discogs\.com/release/\d+(?:-[^/?#]+)?',u)]
    return {'fields':fields, 'tracks':tracks,'release_id':release['id'],
            'source':'https://musicbrainz.org/release/'+release['id'], 'discogs_links':discogs}


class Resolver:
    def __init__(self, path, enabled=False, get=requests.get):
        self.cache = Cache(Path(path).resolve())
        self.enabled = enabled
        self.provider = MusicBrainz(self.cache, get)
        self.lock = threading.Lock()
        self.busy = False
        self.attempted = set()

    @staticmethod
    def identity(album):
        return album['album_id']+':'+hashlib.sha256(json.dumps(album,sort_keys=True).encode()).hexdigest()

    def lookup(self, metadata):
        fields, album = self.cache.jplay(metadata)
        if not album:return fields
        identity = self.identity(album)
        saved = self.cache.get('musicbrainz-album',identity)
        if saved:
            fields = merge_missing(fields, saved.get('fields', {}))
            if saved.get('status') == 'exact_edition':
                for link in saved.get('discogs_links', []):
                    match = re.search(r'/release/(\d+)', link)
                    release = self.cache.get('discogs-cc0',match[1]) if match else None
                    if release and discogs_matches(album, release):
                        fields = merge_missing(fields, discogs_facts(release))
        elif self.enabled and not self.cache.attempted('musicbrainz','__access__'):
            with self.lock:
                if not self.busy and identity not in self.attempted and not self.cache.attempted('musicbrainz',identity):
                    self.busy = True
                    self.attempted.add(identity)
                    threading.Thread(target=self._run,args=(identity,album),daemon=True).start()
        return fields

    def _run(self, identity, album):
        try:
            result = self.provider.discover(album)
            self.cache.write([('musicbrainz-album',identity,result)], ('musicbrainz',identity,result['status']))
        except Exception as error:
            # Failed lookups cannot replace any saved facts. Explicit retry is
            # available after a provider outage; no periodic network work.
            try:
                self.cache.write([], ('musicbrainz',identity,type(error).__name__))
                if getattr(getattr(error, 'response', None), 'status_code', None) in (401,403,429):
                    self.cache.write([], ('musicbrainz','__access__',type(error).__name__))
            except (OSError,sqlite3.Error):pass
            LOG.warning('MusicBrainz lookup deferred: %s',type(error).__name__)
        finally:
            with self.lock:self.busy = False


def discogs_matches(album, release):
    if canonical(album['album']) != canonical(release.get('title')):return False
    if canonical(album['artist']) not in {canonical(a) for a in release.get('artists', [])}:return False
    rows = release.get('tracks', [])
    if len(rows) != album.get('expected_tracks') or len(rows) != len(album.get('tracks', {})):return False
    for track in album['tracks'].values():
        matches = [r for r in rows if canonical(r.get('title')) == canonical(track.get('title'))]
        if len(matches) != 1:return False
        duration = matches[0].get('duration') or ''
        if not re.fullmatch(r'\d+:\d{2}', duration):return False
        minutes, seconds = map(int,duration.split(':'))
        if seconds >= 60 or abs(minutes*60+seconds-float(track.get('duration_seconds',0))) > 2:return False
    return True


def discogs_facts(release):
    result = {}
    if release.get('labels'):result['label'] = '; '.join(release['labels'])
    # Album-level production credits only; track-specific personnel need review.
    for credit in release.get('credits', []):
        if credit.get('tracks') or not credit.get('name'):continue
        field = {'producer':'producer','engineer':'engineer'}.get(canonical(credit.get('role')))
        if field:result[field] = '; '.join(filter(None, [result.get(field),credit['name']]))
    return result


def discogs_records(path, ids):
    """Import selected releases from a local CC0 XML export, never the API.

    Memory is bounded to one release. Images, user/marketplace data and prose
    notes are excluded. No automatic matching to another provider's edition.
    """
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rb') as stream:
        events = ET.iterparse(stream, events=('start','end'))
        _, root = next(events)
        for event, el in events:
            if event != 'end' or el.tag != 'release':continue
            rid = el.get('id')
            if rid in ids:
                yield ('discogs-cc0',rid,{'title':el.findtext('title'), 'released':el.findtext('released'),
                    'artists':[a.findtext('name') for a in el.findall('artists/artist')],
                    'labels':[a.get('name') for a in el.findall('labels/label') if a.get('name')],
                    'credits':[{'name':a.findtext('name'),'role':a.findtext('role'),'tracks':a.findtext('tracks')} for a in el.findall('extraartists/artist')],
                    'tracks':[{'title':t.findtext('title'),'position':t.findtext('position'),'duration':t.findtext('duration')} for t in el.findall('tracklist/track')],
                    'source':'https://www.discogs.com/release/'+rid,'provenance':'Discogs CC0 local XML export'})
            root.clear()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--jplay',type=Path)
    p.add_argument('--backup-dir',type=Path)
    p.add_argument('--discogs-xml',type=Path)
    p.add_argument('--release-ids',nargs='+',default=[])
    p.add_argument('--apply',action='store_true')
    p.add_argument('--retry-musicbrainz',metavar='ALBUM_ID',help='Explicitly retry unsuccessful lookups; saved facts remain untouched')
    a=p.parse_args()
    cache=Cache(a.catalog.resolve())
    records=jplay_records(json.loads(a.jplay.read_text())) if a.jplay else []
    if a.discogs_xml:records.extend(discogs_records(a.discogs_xml,set(a.release_ids)))
    summary={'records':len(records),'tracks':sum(len(r[2].get('tracks',{})) for r in records),'applied':False}
    if a.apply:
        if a.catalog.exists():
            backup=(a.backup_dir or a.catalog.parent)/(a.catalog.name+'.before-import-'+str(time.time_ns()))
            backup.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(a.catalog)) as src, closing(sqlite3.connect(backup)) as dest:src.backup(dest)
            backup.chmod(0o600)
            summary['backup']=str(backup)
        cache.write(records,verify_existing=True)
        summary['unrelated_tables_preserved']=True
        if a.retry_musicbrainz:
            if not re.fullmatch(r'[A-Za-z0-9]{1,64}',a.retry_musicbrainz):raise ValueError('Invalid album ID')
            with closing(sqlite3.connect(a.catalog)) as db, db:
                db.execute("DELETE FROM metadata_attempts WHERE provider='musicbrainz' AND (identity LIKE ? OR identity='__access__')",(a.retry_musicbrainz+':%',))
        summary['applied']=True
    print(json.dumps(summary))


if __name__=='__main__':main()

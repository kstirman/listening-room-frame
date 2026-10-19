"""Bounded background catalog discovery; no credentials or playback control."""
import json
from html.parser import HTMLParser
import re
import threading
import time
import unicodedata
from urllib.parse import urlparse
import requests
import classical
import timing
import music_catalog
import liner_notes
from reviewed_catalog import select

MAX_CANDIDATES = 12
CACHE_VERSION = 1
CACHE_AGE = 7 * 86400


class CatalogCache(music_catalog.Store):
    """Backward-compatible interface to the permanent shared catalog."""
    pass


def canonical(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def records(page):
    for text in re.findall(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        yield from walk(data)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def candidates(page, album):
    ids = []
    for item in records(page):
        if item.get('@type') != 'MusicAlbum' or canonical(item.get('name')) not in {canonical(album), canonical(re.sub(r' \([^()]+\)$', '', album))}:
            continue
        url = urlparse(item.get('url', ''))
        if url.hostname not in (None, 'www.qobuz.com', 'qobuz.com'):
            continue
        match = re.fullmatch(r'/[a-z]{2}-[a-z]{2}/album/[^/]+/([A-Za-z0-9]{1,64})', url.path)
        if match and match[1] not in ids:
            ids.append(match[1])
    return ids


class Headings(HTMLParser):
    def __init__(self):
        super().__init__(); self.active = False; self.text = []; self.headings = []
    def handle_starttag(self, tag, attrs):
        if tag == 'h1': self.active = True; self.text = []
    def handle_data(self, data):
        if self.active: self.text.append(data)
    def handle_endtag(self, tag):
        if tag == 'h1' and self.active:
            self.headings.append(''.join(self.text)); self.active = False


def parsed_rows(page, album_id=None):
    parser = classical.Tracks(); parser.feed(page)
    rows = classical.catalog(parser.tracks)
    releases = [r for r in records(page) if r.get("@type") in ("Product", "MusicAlbum")]
    for track_id, row in rows.items():
        row["_catalog_track_id"] = track_id
        if album_id:
            row["_catalog_album_id"] = album_id
            row["_source"] = "https://www.qobuz.com/us-en/album/-/" + album_id
        for item in releases:
            date = item.get("releaseDate") or item.get("datePublished")
            if date:row.setdefault("releaseDate", date)
            label = item.get("recordLabel")
            if isinstance(label, dict):label = label.get("name")
            if isinstance(label, str):row.setdefault("label", label)
        row.update(liner_notes.from_credits(row.get("credits", "")))
    return rows


def album_rows(page, album, artist):
    products = [r for r in records(page) if r.get('@type') == 'Product']
    matching = [p for p in products if canonical(p.get('name')) == canonical(album)]
    # Search results/Product may omit an edition subtitle. Require the exact
    # full album in the visible catalog heading before accepting that candidate.
    headings = Headings(); headings.feed(page)
    full_heading = any(canonical(h).startswith(canonical(album) + ' by ') for h in headings.headings)
    if not matching and not full_heading:return None
    rows = parsed_rows(page)
    album_artist = any(isinstance(p.get('brand'), dict) and
                       canonical(p['brand'].get('name')) == canonical(artist) for p in (matching or products))
    for row in rows.values():
        names = [person.partition(', ')[0] for person in row.get('credits', '').split(' - ')
                 if 'MainArtist' in [r.strip() for r in person.partition(', ')[2].split(',')]]
        row['_artist_matches'] = album_artist or canonical(artist) in {canonical(n) for n in names}
    return rows if any(row['_artist_matches'] for row in rows.values()) else None


def fetch(path, params=None):
    response = requests.get('https://www.qobuz.com/us-en/' + path, params=params,
                            timeout=(3, 5), headers={'User-Agent': 'ListeningRoomArtwork/1.0'})
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError('Catalog response too large')
    return response.text


class Resolver:
    def __init__(self, get=fetch, cache_path=None):
        self.get = get
        self.disk = CatalogCache(cache_path) if cache_path else None
        self.cache = {}
        self.revision = 0
        self.lock = threading.Lock()
        self.busy = False

    def discover(self, album, artist):
        ids = candidates(self.get('search', {'q': album, 'i': 'boutique'}), album)
        if len(ids) > MAX_CANDIDATES:
            return [], 'ambiguous'
        albums = []
        for album_id in ids:
            rows = album_rows(self.get('album/-/' + album_id), album, artist)
            if rows:
                for row in rows.values():
                    row["_catalog_album_id"] = album_id
                    row["_source"] = "https://www.qobuz.com/us-en/album/-/" + album_id
                albums.append(rows)
        return albums, 'ready' if albums else 'unmatched'

    def _run(self, key, album, artist, keep_ready=False):
        try:
            albums, status = self.discover(album, artist)
        except Exception:
            albums, status = [], 'unavailable'
        if self.disk and status == 'ready':
            self.disk.save(key, albums)
        with self.lock:
            if keep_ready and status != 'ready' and key in self.cache and self.cache[key][2] == 'ready':
                # A transient outage must not discard a usable saved recording.
                _, albums, status = self.cache[key]
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), albums, status)
            self.revision += 1
            self.busy = False

    def lookup(self, metadata):
        album, artist = metadata.get('album'), metadata.get('artist')
        if not album or not artist or not metadata.get('title') or not timing.seconds(metadata.get('duration_seconds')):
            return None, 'insufficient'
        key = (canonical(album), canonical(artist))
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < (1800 if cached[2] == 'ready' else 300):
                _, albums, status = cached
            else:
                saved = self.disk.load(key) if self.disk else None
                if saved:
                    albums, stale = saved
                    status = 'ready'
                    if len(self.cache) >= 32:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = (time.monotonic(), albums, status)
                    if stale and not self.busy:
                        self.busy = True
                        threading.Thread(target=self._run, args=(key, album, artist, True), daemon=True).start()
                elif not self.busy:
                    self.busy = True
                    threading.Thread(target=self._run, args=(key, album, artist), daemon=True).start()
                    return None, 'searching'
                else:
                    return None, 'searching'
        if status != 'ready':
            return None, status
        # Reject editions that match the track even if only one has a complete work.
        selected = [select(metadata, rows) for rows in albums]
        selected = [item for item in selected if item is not None and item[0].get('_artist_matches', True)]
        if len(selected) > 1:
            return None, 'ambiguous'
        if not selected:
            return None, 'track_mismatch'
        row, context = selected[0]
        # A unique recording/track match still supplies authoritative credits
        # when it is a standalone work or has incomplete movement timing.
        # Only the strip depends on a complete composition group.
        return (row, context), 'matched' if context else 'matched_track'

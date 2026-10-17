"""Bounded background catalog discovery; no credentials or playback control."""
import json
import re
import threading
import time
import unicodedata
from urllib.parse import urlparse
import requests
import classical
import timing
from reviewed_catalog import select


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
        if item.get('@type') != 'MusicAlbum' or canonical(item.get('name')) != canonical(album):
            continue
        url = urlparse(item.get('url', ''))
        if url.hostname not in (None, 'www.qobuz.com', 'qobuz.com'):
            continue
        match = re.fullmatch(r'/[a-z]{2}-[a-z]{2}/album/[^/]+/([A-Za-z0-9]{1,64})', url.path)
        if match and match[1] not in ids:
            ids.append(match[1])
    return ids


def album_rows(page, album, artist):
    products = [r for r in records(page) if r.get('@type') == 'Product']
    if not any(canonical(p.get('name')) == canonical(album) and
               isinstance(p.get('brand'), dict) and
               canonical(p['brand'].get('name')) == canonical(artist) for p in products):
        return None
    parser = classical.Tracks(); parser.feed(page)
    return classical.catalog(parser.tracks)


def fetch(path, params=None):
    response = requests.get('https://www.qobuz.com/us-en/' + path, params=params,
                            timeout=(3, 5), headers={'User-Agent': 'ListeningRoomArtwork/1.0'})
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError('Catalog response too large')
    return response.text


class Resolver:
    def __init__(self, get=fetch):
        self.get = get
        self.cache = {}
        self.lock = threading.Lock()
        self.busy = False

    def discover(self, album, artist):
        ids = candidates(self.get('search', {'q': album, 'i': 'boutique'}), album)
        if len(ids) > 4:
            return [], 'ambiguous'
        albums = []
        for album_id in ids:
            rows = album_rows(self.get('album/-/' + album_id), album, artist)
            if rows:
                albums.append(rows)
        return albums, 'ready' if albums else 'unmatched'

    def _run(self, key, album, artist):
        try:
            albums, status = self.discover(album, artist)
        except Exception:
            albums, status = [], 'unavailable'
        with self.lock:
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), albums, status)
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
                if not self.busy:
                    self.busy = True
                    threading.Thread(target=self._run, args=(key, album, artist), daemon=True).start()
                return None, 'searching'
        if status != 'ready':
            return None, status
        # Reject editions that match the track even if only one has a complete work.
        selected = [select(metadata, rows) for rows in albums]
        selected = [item for item in selected if item is not None]
        if len(selected) > 1:
            return None, 'ambiguous'
        if not selected:
            return None, 'track_mismatch'
        row, context = selected[0]
        return ((row, context), 'matched') if context else (None, 'incomplete')

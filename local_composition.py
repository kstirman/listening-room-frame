"""Bounded, read-only composition context from reviewed local-library metadata."""
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
import timing


def target_file(resource, root, prefix):
    root = Path(root).resolve()
    path = urlparse(resource).path
    if not path.startswith(prefix):
        return None
    relative = unquote(re.sub(r'\*([0-9a-fA-F]{2})', r'%\1', path[len(prefix):]))
    target = (root / relative).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


def probe(path):
    result = subprocess.run(['ffprobe','-v','quiet','-analyzeduration','0','-probesize','32768',
        '-show_entries','format=duration:format_tags','-of','json',str(path)],
        capture_output=True,text=True,check=True,timeout=2)
    data = json.loads(result.stdout).get('format', {})
    tags = {k.lower():v for k,v in data.get('tags', {}).items()}
    tags['duration_seconds'] = timing.seconds(data.get('duration'))
    return tags


def positive(value):
    match = re.fullmatch(r'(\d+)(?:/\d+)?', str(value or '').strip())
    return int(match[1]) if match and int(match[1]) > 0 else None


def album_identity(tags):
    album = str(tags.get('album', '')).strip()
    # Some legacy rips have separate album names ending in a disc number.
    match = re.fullmatch(r'(.+?)\s+(?:(?:CD|Disc|Disk)\s*)?(\d+)', album, re.I)
    disc = positive(tags.get('discnumber') or tags.get('disc'))
    if not disc and match:
        return match[1], int(match[2])
    return album, disc or 1


def build(candidates, current):
    """Candidates already match reviewed work/composer; isolate the recording."""
    target = candidates.get(current)
    if not target:
        return None
    base, _ = album_identity(target)
    artist = target.get('albumartist') or target.get('artist')
    if not base or not artist:
        return None
    rows = []
    for key, item in candidates.items():
        album, disc = album_identity(item)
        if album != base or (item.get('albumartist') or item.get('artist')) != artist:
            continue
        track = positive(item.get('tracknumber') or item.get('track'))
        length = timing.seconds(item.get('duration_seconds'))
        if not track or not length:
            return None
        rows.append(((disc,track), key, item, length))
    rows.sort(key=lambda row: row[0])
    orders = [r[0] for r in rows]
    if not 2 <= len(rows) <= 128 or len(set(orders)) != len(rows):
        return None
    # Reject missing tracks within a disc or missing intermediate discs.
    discs = sorted(set(d for d, _ in orders))
    if discs != list(range(discs[0], discs[-1]+1)):
        return None
    for disc in discs:
        tracks = [n for d,n in orders if d == disc]
        if tracks != list(range(tracks[0], tracks[-1]+1)):
            return None
    numbers = [timing.number(row[2]) for row in rows]
    # Partial explicitly numbered movement sequences are not whole works.
    numbered = any(n is not None for n in numbers)
    if numbered and numbers != list(range(1,len(rows)+1)):
        return None
    index = next((i for i,row in enumerate(rows) if row[1] == current), None)
    if index is None:
        return None
    return {'index':index, 'durations':[r[3] for r in rows],
            'label':'Movement' if numbered else 'Part'}


class Catalog:
    def __init__(self):
        self.cache = {}

    def context(self, resource, root, fields, prefix='/minimserver/*/AudirvanaLibrary/'):
        target = target_file(resource, root, prefix)
        if not target or not fields.get('work') or not fields.get('composer'):
            return None
        root = Path(root).resolve()
        db = root / '.listening-room-metadata' / 'enriched.json'
        overrides = db.with_name('track-overrides.json')
        stamp = (db.stat().st_mtime_ns, overrides.stat().st_mtime_ns if overrides.exists() else 0)
        key = (str(root), fields['work'], fields['composer'], stamp)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return build(cached[1], str(target))
        data = json.loads(db.read_text()).get('tracks', {})
        if overrides.exists():
            for name, entry in json.loads(overrides.read_text()).items():
                data.setdefault(name, {'fields':{}})['fields'].update(entry.get('fields', {}))
        matches = []
        for name, entry in data.items():
            item = entry.get('fields', {})
            if item.get('work') == fields['work'] and item.get('composer') == fields['composer']:
                path = (root / name).resolve()
                if path.is_relative_to(root) and path.is_file():
                    matches.append((path,item))
        if not 2 <= len(matches) <= 128:
            return None
        rows = {}
        deadline = time.monotonic() + 10
        for path, item in matches:
            if time.monotonic() >= deadline:
                return None
            tags = probe(path)
            tags.update({k:v for k,v in item.items() if k in ('work','movementName','movementNumber','composer')})
            rows[str(path)] = tags
        if len(self.cache) >= 8:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (time.monotonic(), rows)
        return build(rows, str(target))

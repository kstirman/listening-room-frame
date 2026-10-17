"""Resolve proxy metadata to a unique reviewed local file without network access."""
import json
import unicodedata
from pathlib import Path
from urllib.parse import quote
import local_composition
import timing


def key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def values(value):
    return value if isinstance(value, list) else [value] if value else []


def identities(track, album, fields, tags=None):
    tags = tags if tags is not None else track.get('tags', {})
    title = values(tags.get('title')) + [track.get('title'), fields.get('title')]
    albums = values(tags.get('album')) + [fields.get('album')]
    if not any(albums):
        albums = [album.get('album')]
    artists = values(tags.get('artist')) + values(tags.get('albumartist')) + values(tags.get('album_artist')) + [fields.get('artist'), fields.get('albumArtist')]
    # Controllers may expose a conductor/ensemble instead of the combined artist.
    # Accept exact reviewed performer credits, never split names or use composer.
    for role in ('conductor', 'orchestra', 'soloists'):
        artists += values(fields.get(role))
    if not any(artists):
        artists = [album.get('artist')]
    return [{key(v) for v in group if v} for group in (title, albums, artists)]


class Index:
    def __init__(self):
        self.stamp = None
        self.rows = []
        self.probes = {}

    def lookup(self, metadata, root, prefix='/minimserver/*/AudirvanaLibrary/'):
        wanted = tuple(key(metadata.get(k)) for k in ('title', 'album', 'artist'))
        duration = timing.seconds(metadata.get('duration_seconds'))
        if not all(wanted) or not duration:
            return None, 'local_insufficient'
        root = Path(root).resolve(); db = root/'.listening-room-metadata'
        files = [db/'inventory.json', db/'enriched.json', db/'track-overrides.json']
        if not files[0].is_file() or not files[1].is_file():
            return None, 'local_unmatched'
        stamp = (str(root), tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in files))
        if stamp != self.stamp:
            inventory = json.loads(files[0].read_text())
            reviewed = json.loads(files[1].read_text()).get('tracks', {})
            if files[2].exists():
                for name, entry in json.loads(files[2].read_text()).items():
                    reviewed.setdefault(name, {'fields': {}})['fields'].update(entry.get('fields', {}))
            rows = []
            for album in inventory.get('albums', []):
                for track in album.get('tracks', []):
                    name = track.get('path', '')
                    fields = reviewed.get(name, {}).get('fields', {})
                    if fields and name:
                        rows.append((name, track, album, fields, identities(track, album, fields)))
            self.rows, self.stamp = rows, stamp
            self.probes.clear()
        candidates = []
        for name, track, album, fields, aliases in self.rows:
            if not all(v in names for v, names in zip(wanted, aliases)):
                continue
            path = (root/name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            candidates.append((path, track, album, fields))
        # Never select from a truncated candidate list.
        if len(candidates) > 8:
            return None, 'local_ambiguous'
        matches = {}
        for path, track, album, fields in candidates:
            stat = path.stat(); cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
            try:
                if cache_key not in self.probes:
                    if len(self.probes) >= 64:
                        self.probes.pop(next(iter(self.probes)))
                    self.probes[cache_key] = local_composition.probe(path)
                tags = self.probes[cache_key]
            except Exception:
                return None, 'local_unavailable'
            # Require current file identity too; stale inventory aliases alone
            # cannot identify a file whose tags/content have changed.
            actual = identities({}, {}, fields, tags)
            length = timing.seconds(tags.get('duration_seconds'))
            if all(v in names for v, names in zip(wanted, actual)) and length and abs(length-duration) <= 1:
                matches[str(path)] = (path, dict(fields))
        if len(matches) > 1:
            return None, 'local_ambiguous'
        if not matches:
            return None, 'local_unmatched'
        path, fields = next(iter(matches.values()))
        resource = 'http://local.invalid'+prefix+quote(str(path.relative_to(root)), safe='/')
        return (resource, fields), 'local_matched'

"""Reviewed liner notes, matched to a track rather than a whole album cast."""
import json
from pathlib import Path
import unicodedata
import timing


def key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).replace('’', "'").casefold().split())


def text(value):
    return ' '.join(value.split())[:1200] if isinstance(value, str) else ''


def extract(fields):
    if not isinstance(fields, dict):
        return {}
    notes = {k: text(fields.get(k)) for k in (
        'compositionDate', 'composition_source', 'recordingDate', 'releaseDate', 'originalReleaseDate', 'recordingLocation',
        'label', 'songwriters', 'producer', 'engineer', 'note') if text(fields.get(k))}
    personnel = fields.get('personnel', [])
    if isinstance(personnel, list):
        people = [{'name': text(p.get('name')), 'role': text(p.get('role'))}
                  for p in personnel[:100] if isinstance(p, dict)
                  and text(p.get('name')) and text(p.get('role'))][:12]
        if people:
            notes['personnel'] = people
    return notes


def lookup(metadata, config):
    """Require exact artist/album/title, known duration and a single reviewed row."""
    path = config.get('liner_notes_file')
    if not path:
        return {}
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            return {}
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return {}
    return lookup_data(metadata, data)


def lookup_data(metadata, data):
    if not isinstance(data, dict) or not isinstance(data.get('albums'), list):
        return {}
    length = timing.seconds(metadata.get('duration_seconds'))
    if not length or not all(metadata.get(k) for k in ('album', 'artist', 'title')):
        return {}
    matches = []
    for album in data['albums']:
        if not isinstance(album, dict) or album.get('review_status') != 'reviewed' or not album.get('source'):
            continue
        if key(album.get('album')) != key(metadata['album']) or key(album.get('artist')) != key(metadata['artist']):
            continue
        tracks = album.get('tracks', [])
        if not isinstance(tracks, list):
            continue
        for track in tracks:
            if not isinstance(track, dict):
                continue
            duration = timing.seconds(track.get('duration_seconds'))
            if key(track.get('title')) == key(metadata['title']) and duration and abs(duration-length) <= 2:
                fields = extract(album.get('fields'))
                track_fields = extract(track.get('fields'))
                fields.update(track_fields)
                # Album-wide personnel must never masquerade as track-specific credits.
                fields.pop('personnel', None)
                # A compilation has no single songwriting date. Require the
                # reviewed date on the exact track, never inherit an album date.
                fields.pop('compositionDate', None);fields.pop('composition_source', None)
                for name in ('compositionDate','composition_source'):
                    if track_fields.get(name):fields[name]=track_fields[name]
                if track_fields.get('personnel'):
                    fields['personnel'] = track_fields['personnel']
                matches.append(fields)
    return matches[0] if len(matches) == 1 else {}


def from_credits(credits):
    """Only explicit per-track role labels, no inferred instrument or personnel."""
    fields = {}; people = []; roles = {}
    for credit in credits.split(' - '):
        name, sep, listed = credit.partition(', ')
        if not sep:continue
        for role in [r.strip() for r in listed.split(',')]:
            roles.setdefault(role, []).append(name)
            if role in {'Piano','Vocals','Vocal','Guitar','Bass','Double Bass','Drums',
                        'Violin','Viola','Cello','Saxophone','Trumpet','Percussion',
                        'Flute','Clarinet','Organ','Keyboards','Harp','Trombone'}:
                people.append({'name':name,'role':role})
    for field,labels in [('songwriters',('Composer','Lyricist','Author','Writer')),
                         ('producer',('Producer',)),('engineer',('Engineer','Recording Engineer','Mixing Engineer'))]:
        names = list(dict.fromkeys(n for label in labels for n in roles.get(label,[])))
        if names:fields[field]='; '.join(names)
    if people:fields['personnel']=people
    return fields

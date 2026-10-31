"""Reviewed musical relationships; resemblance alone never becomes influence."""
from datetime import date
from urllib.parse import urlparse
import unicodedata

LABELS = {'influence': 'Documented influence', 'earlier_version': 'Earlier version',
          'shared_musicians': 'Shared musicians', 'development': 'Artistic development',
          'quotation': 'Musical quotation', 'comparison': 'Listening comparison'}


def key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def url(value):
    try:
        p = urlparse(value)
        return isinstance(value, str) and p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password
    except (ValueError, TypeError, AttributeError):
        return False


def clean(row):
    if not isinstance(row, dict) or row.get('review_status') != 'reviewed':
        return None
    if any(not isinstance(row.get(k), str) for k in ('kind', 'scope', 'evidence')):
        return None
    kind = row.get('kind')
    if kind not in LABELS or row.get('scope') not in {'album', 'composition', 'recording', 'track'}:
        return None
    if kind == 'influence' and row.get('evidence') != 'explicit_attribution':
        return None
    if kind != 'influence' and row.get('evidence') not in {'documented', 'editorial_comparison'}:
        return None
    if row.get('evidence') == 'editorial_comparison' and kind != 'comparison':
        return None
    fields = ('target_title', 'target_artist', 'explanation', 'source_title', 'reviewed_at')
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in fields) or not url(row.get('source_url')):
        return None
    try:
        date.fromisoformat(row['reviewed_at'])
    except ValueError:
        return None
    return {**{k: ' '.join(row[k].split())[:1200] for k in fields},
            'source_url': row['source_url'], 'kind': kind, 'label': LABELS[kind],
            'scope': row['scope'], 'evidence': row['evidence']}


def lookup(metadata, document):
    """Exact album/artist matching with optional edition, work and track guards.

    Album-scope claims describe the album generally. Recording/track claims must
    explicitly identify their source edition/track; they cannot spread to covers.
    """
    if not isinstance(document, dict) or not isinstance(document.get('albums'), list):
        return []
    if not metadata.get('album') or not metadata.get('artist'):
        return []
    matches = []
    for album in document['albums']:
        if not isinstance(album, dict) or any(key(album.get(k)) != key(metadata[k]) for k in ('album', 'artist')):
            continue
        if album.get('source_album_key') and album['source_album_key'] != metadata.get('source_album_key'):
            continue
        if album.get('album_id') and album['album_id'] != metadata.get('album_id'):
            continue
        matches.append(album)
    if len(matches) != 1:
        return []
    album = matches[0]
    rows = album.get('connections')
    if not isinstance(rows, list):
        return []
    found = []
    for row in rows[:100]:
        item = clean(row)
        if not item:
            continue
        if item['scope'] in {'recording', 'track'} and not (album.get('source_album_key') or album.get('album_id')):
            continue
        if item['scope'] == 'track' and (not row.get('track_id') or row['track_id'] != metadata.get('object_id')):
            continue
        if item['scope'] == 'composition' and (not row.get('work') or key(row['work']) != key(metadata.get('work'))):
            continue
        found.append(item)
    return found[:6]

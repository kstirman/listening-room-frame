"""Opt-in recording matches for sources that omit streaming catalog IDs."""
import re
import classical
import timing


def lookup(metadata, mappings, fetch, catalog=None):
    if not isinstance(mappings, list):
        return None
    title = metadata.get('title')
    duration = timing.seconds(metadata.get('duration_seconds'))
    if not title or not duration or not metadata.get('album') or not metadata.get('artist'):
        return None
    matches = [m for m in mappings[:128] if isinstance(m, dict) and
               m.get('album') == metadata['album'] and m.get('artist') == metadata['artist']]
    if len(matches) != 1:
        return None
    album_id = matches[0].get('qobuz_album_id', '')
    if not isinstance(album_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,64}', album_id):
        return None
    rows = catalog.edition(album_id) if catalog else None
    if not rows:
        if catalog:
            from auto_catalog import parsed_rows
            rows = parsed_rows(fetch(album_id), album_id)
            catalog.put_edition(album_id, rows, metadata['album'], metadata['artist'])
        else:
            parser = classical.Tracks()
            parser.feed(fetch(album_id))
            rows = classical.catalog(parser.tracks)
    match = select(metadata, rows)
    return match if match and match[1] else None


def select(metadata, rows):
    """Shared exact track/duration matching for reviewed and discovered albums."""
    title = metadata.get('title')
    duration = timing.seconds(metadata.get('duration_seconds'))
    if not title or not duration:
        return None
    selected = [key for key, row in rows.items() if row.get('title') == title and
                timing.seconds(row.get('duration_seconds')) and
                abs(timing.seconds(row['duration_seconds']) - duration) <= 1]
    if len(selected) != 1:
        return None
    key = selected[0]
    context = timing.composition(rows, key)
    return rows[key], context

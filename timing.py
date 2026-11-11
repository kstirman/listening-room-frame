"""Validated track lengths and conservative, ordered composition context."""
import math
import re


def seconds(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if ':' in value:
            if not re.fullmatch(r'\d+:\d{2}(?::\d{2})?(?:\.\d+)?', value):
                return None
            parts = [float(p) for p in value.split(':')]
            if any(p >= 60 for p in parts[1:]):
                return None
            value = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    try:
        value = float(value)
        return int(value + .5) if math.isfinite(value) and .5 <= value <= 86400 else None
    except (TypeError, ValueError, OverflowError):
        return None


def label(value):
    value = seconds(value)
    if not value:
        return ''
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'


def number(track):
    explicit = str(track.get('movementNumber', '')).split('/')[0]
    if explicit.isdigit():
        return int(explicit)
    title = track.get('movementName') or track.get('title', '')
    work = track.get('work', '')
    if work and title.startswith(work + ':'):
        title = title[len(work)+1:].strip()
    numbered_piece = re.match(r'^No\.\s*(\d+)(?=[\s,.:]|$)', title)
    if numbered_piece:
        return int(numbered_piece[1])
    match = re.match(r'^(\d+[A-Za-z]?|[IVXLCDM]+)[.,:]\s*', title)
    if not match:
        return None
    text = match[1]
    if text[0].isdigit():
        return int(re.match(r'\d+', text)[0])
    romans = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII','XIV','XV','XVI']
    return romans.index(text) + 1 if text in romans else None


def composition(tracks, current):
    """Contiguous, fully timed numbered movements or explicit named work parts.

    Tracks must be in authoritative album order; never sort a playback queue or
    combine repeated performances by work title alone. Scope IDs isolate headings.
    """
    if current not in tracks:
        return None
    # Never label a paginated fragment (including legacy 50-row caches) a whole work.
    import catalog_pages
    if catalog_pages.needs_refresh(tracks):
        return None
    # Catalog normalization marks only validated, explicitly labeled audience
    # interludes. They remain album tracks but are not musical movements.
    if tracks[current].get('non_music_interlude'):
        return None
    entries = [(key,row) for key,row in tracks.items() if not row.get('non_music_interlude')]
    pos = next(i for i, (key, _) in enumerate(entries) if key == current)
    target = entries[pos][1]
    if target.get('composition_group'):
        # Explicit reviewed groups can span a movement split across tracks.
        # Require one verified edition, complete consecutive ordering and the
        # same work/composer; never turn a partial cache into a whole work.
        group = [(key, row) for key, row in entries
                 if row.get('composition_group') == target['composition_group']]
        def integer(value):
            return int(str(value)) if re.fullmatch(r'[1-9]\d*', str(value)) else None
        count = integer(target.get('composition_count'))
        edition = target.get('_edition_verified')
        if not count or not 2 <= count <= 128 or len(group) != count or not edition:
            return None
        if any(row.get('_edition_verified') != edition or
               row.get('_catalog_album_id') != edition or
               (row.get('work'), row.get('composer')) != (target.get('work'), target.get('composer')) or
               not row.get('work') or not row.get('composer') or
               integer(row.get('composition_count')) != count or
               not seconds(row.get('duration_seconds')) for _, row in group):
            return None
        if [integer(row.get('composition_index')) for _, row in group] != list(range(1, count + 1)):
            return None
        positions = [i for i, (_, row) in enumerate(entries)
                     if row.get('composition_group') == target['composition_group']]
        if positions != list(range(positions[0], positions[-1] + 1)):
            return None
        return {'index': next(i for i, (key, _) in enumerate(group) if key == current),
                'durations': [seconds(row['duration_seconds']) for _, row in group], 'label': 'Part'}
    work = target.get('work')
    if re.search(r'(?: / |, )Appendix$', work or '', re.I):
        return None
    if not work or not (number(target) or target.get('part_group')):
        return None
    def same(item):
        return (item.get('work') == work and
                item.get('work_scope') == target.get('work_scope') and
                item.get('composer', '') == target.get('composer', ''))
    left = pos
    while left and same(entries[left - 1][1]):
        left -= 1
    right = pos + 1
    while right < len(entries) and same(entries[right][1]):
        right += 1
    group = [item for _, item in entries[left:right]]
    lengths = [seconds(item.get('duration_seconds')) for item in group]
    if not 2 <= len(group) <= 128 or not all(lengths):
        return None
    numbers = [number(item) for item in group]
    numbered_pieces = all(re.match(r'^No\.\s*\d+(?=[\s,.:]|$)',
                                   item.get('movementName') or item.get('title', ''))
                          for item in group)
    # A source-identified excerpts release is a selection, never a whole work.
    albums = {item.get('_identity_album', '') for item in group}
    if (len(albums) == 1 and re.search(r'\bexcerpts\b', next(iter(albums)), re.I)
            and len({item.get('_edition_verified') for item in group}) == 1
            and all(item.get('part_group') and item.get('_edition_verified') for item in group)
            and all(n is not None for n in numbers)
            and all(a < b for a,b in zip(numbers,numbers[1:]))):
        return {'index': pos-left, 'durations': lengths, 'label': 'Excerpt'}
    if numbers == list(range(1, len(group) + 1)) and (len(group) <= 16 or numbered_pieces):
        result = {'index': pos - left, 'durations': lengths}
        if numbered_pieces:
            result['label'] = 'Part'
        return result
    if all(item.get('part_group') for item in group) and any(n is None for n in numbers):
        titles = [item.get('movementName') for item in group]
        # Duplicate sections can signal concatenated performances: omit rather
        # than claim they are one complete performance.
        if len(set(titles)) == len(titles):
            return {'index': pos - left, 'durations': lengths, 'label': 'Part'}
    return None

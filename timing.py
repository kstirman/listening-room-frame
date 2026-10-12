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
    match = re.match(r'^(?:No\.\s*)?(\d+|[IVXLCDM]+)[.,:]\s*', title)
    if not match:
        return None
    text = match[1]
    if text.isdigit():
        return int(text)
    romans = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII','XIV','XV','XVI']
    return romans.index(text) + 1 if text in romans else None


def composition(tracks, current):
    """Only a contiguous, fully timed, numbered work starting at movement one.

    Tracks must be in authoritative album order; never sort a playback queue or
    combine repeated performances by work title alone. Scope IDs isolate headings.
    """
    if current not in tracks:
        return None
    entries = list(tracks.items())
    pos = next(i for i, (key, _) in enumerate(entries) if key == current)
    target = entries[pos][1]
    work = target.get('work')
    if not work or not number(target):
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
    if not 2 <= len(group) <= 16 or not all(lengths):
        return None
    if [number(item) for item in group] != list(range(1, len(group) + 1)):
        return None
    return {'index': pos - left, 'durations': lengths}

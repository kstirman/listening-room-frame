"""Private, offline photo playlists. No network requests in the display loop."""
from datetime import date
import json
import random
from pathlib import Path
from itertools import combinations
from PIL import Image
from photo_exclusions import Exclusions


def clean(value, limit=200):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def caption(row, today=None):
    """Only display sourced dates/places; never infer either from pixels."""
    parts = []
    today = today or date.today()
    if row.get('date_source') and row.get('date'):
        try:
            day = date.fromisoformat(row['date'])
            exact = day.strftime('%B ') + str(day.day) + day.strftime(', %Y')
            years = today.year - day.year - ((today.month, today.day) < (day.month, day.day))
            anniversary = (day.month, day.day) == (today.month, today.day)
            relative = str(years) + (' year ago' if years == 1 else ' years ago')
            if years > 0 and anniversary:
                parts.extend(['On this day ' + relative, exact])
            else:
                parts.append(exact)
                if years > 0:
                    parts.append(relative)
        except (ValueError, TypeError):
            pass
    if row.get('location_source') and clean(row.get('location')):
        parts.append(clean(row['location']))
    return '  ·  '.join(parts)


def load(config):
    """Load only relative, existing image paths under the private manifest root."""
    path = config.get('idle_photos_file')
    if not config.get('idle_photos_enabled', False) or not path:
        return []
    try:
        manifest = Path(path).resolve()
        with manifest.open('rb') as stream:
            raw = stream.read(5_000_001)
        if len(raw) > 5_000_000:
            return []
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('photos'), list):
            return []
        exclusions = Exclusions(config)
        photos = []
        seen = set()
        for row in data['photos'][:10000]:
            if not isinstance(row, dict) or not isinstance(row.get('file'), str):
                continue
            if not exclusions.allows(row):
                continue
            relative = Path(row['file'])
            image = (manifest.parent / relative).resolve()
            if (relative.is_absolute() or not image.is_relative_to(manifest.parent)
                    or image.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp')
                    or not image.is_file() or image in seen):
                continue
            seen.add(image)
            day = row.get('date') if row.get('date_source') else ''
            try:
                day = date.fromisoformat(day).isoformat()
            except (ValueError, TypeError):
                day = ''
            photos.append({'path': image, 'key': relative.as_posix(),
                           'date': day,
                           'caption': caption(row)})
        return photos
    except (OSError, ValueError, TypeError):
        return []


def interval(config):
    try:
        return max(20, min(86400, int(config.get('idle_photo_interval_seconds', 120))))
    except (ValueError, TypeError, OverflowError):
        return 120


def choose(photos, state, excluded=(), today=None, rng=None):
    """Balance dates, avoid recent images/days, and occasionally favor past seasons."""
    today = today or date.today()
    rng = rng or random
    candidates = [i for i in range(len(photos)) if i not in excluded]
    if not candidates:
        return None
    previous = state.get('photo_index')
    last_key = state.get('photo_key')
    last_day = state.get('photo_date')
    if not last_key and type(previous) is int and 0 <= previous < len(photos):
        last_key, last_day = photos[previous]['key'], photos[previous]['date']
    different = [i for i in candidates if photos[i]['key'] != last_key]
    candidates = different or candidates
    different_day = [i for i in candidates if photos[i]['date'] and photos[i]['date'] != last_day]
    candidates = different_day or candidates
    recent = state.get('photo_recent', [])
    recent = recent if isinstance(recent, list) else []
    fresh = [i for i in candidates if photos[i]['key'] not in recent]
    candidates = fresh or candidates
    recent_days = state.get('photo_recent_dates', [])
    recent_days = recent_days if isinstance(recent_days, list) else []
    fresh_days = [i for i in candidates if photos[i]['date'] not in recent_days[-7:]]
    candidates = fresh_days or candidates
    groups = {}
    for i in candidates:
        # Unknown dates remain eligible, but never become seasonal evidence.
        group = photos[i]['date'] or 'undated'
        groups.setdefault(group, []).append(i)
    seasonal, exact = [], []
    for day in groups:
        try:
            captured = date.fromisoformat(day)
            if captured.year >= today.year:
                continue
            a = date(2000, captured.month, captured.day).timetuple().tm_yday
            b = date(2000, today.month, today.day).timetuple().tm_yday
            gap = min(abs(a-b), 366-abs(a-b))
            if gap <= 14: seasonal.append(day)
            if gap == 0: exact.append(day)
        except (ValueError, TypeError):
            pass
    days = list(groups)
    if seasonal and rng.random() < 1/3:
        days = exact or seasonal
    return rng.choice(groups[rng.choice(days)])


def remember(photos, index, state):
    """Persist selection only after the TV has accepted it."""
    row = photos[index]
    recent = state.get('photo_recent', [])
    recent = recent if isinstance(recent, list) else []
    days = state.get('photo_recent_dates', [])
    days = days if isinstance(days, list) else []
    state.update(photo_index=index, photo_key=row['key'], photo_date=row['date'],
                 photo_recent=(recent + [row['key']])[-100:],
                 photo_recent_dates=(days + [row['date']])[-7:])


def portrait_group(photos, primary, state):
    """Use only already eligible, prepared photos; no network or extra prefetch."""
    ratios = {}
    for i, row in enumerate(photos):
        try:
            with Image.open(row['path']) as image:
                width, height = image.size
                if image.getexif().get(274) in (5, 6, 7, 8):
                    width, height = height, width
            if 0 < width < height:
                ratios[i] = width / height
        except (OSError, ValueError):
            pass
    if primary not in ratios:
        return [primary]
    others = [i for i in ratios if i != primary]
    fresh = [i for i in others if photos[i]['key'] not in state.get('photo_recent', [])]
    others = (fresh or others)[:20]
    options = [(primary,) + group for count in (1, 2)
               for group in combinations(others, count)]
    if not options:
        return [primary]
    # Prefer the combination that fills most screen area without cropping.
    def score(group):
        ratio = sum(ratios[i] for i in group) / (16 / 9)
        return min(ratio, 1 / ratio)
    return list(max(options, key=score))

"""Private, offline photo playlists. No network requests in the display loop."""
from datetime import date
import json
from pathlib import Path


def clean(value, limit=200):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def caption(row):
    """Only display sourced dates/places; never infer either from pixels."""
    parts = []
    if row.get('date_source') and row.get('date'):
        try:
            day = date.fromisoformat(row['date'])
            parts.append(day.strftime('%B ') + str(day.day) + day.strftime(', %Y'))
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
        photos = []
        seen = set()
        for row in data['photos'][:10000]:
            if not isinstance(row, dict) or not isinstance(row.get('file'), str):
                continue
            relative = Path(row['file'])
            image = (manifest.parent / relative).resolve()
            if (relative.is_absolute() or not image.is_relative_to(manifest.parent)
                    or image.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp')
                    or not image.is_file() or image in seen):
                continue
            seen.add(image)
            photos.append({'path': image, 'caption': caption(row)})
        return photos
    except (OSError, ValueError, TypeError):
        return []


def interval(config):
    try:
        return max(30, min(86400, int(config.get('idle_photo_interval_seconds', 120))))
    except (ValueError, TypeError, OverflowError):
        return 120

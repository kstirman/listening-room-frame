"""Read the local Roon extension snapshot. No playback-control commands."""
import json
import re
import time
from pathlib import Path


class RoonSource:
    def __init__(self, directory):
        self.root = Path(directory).expanduser().resolve()

    def playback(self):
        try:
            data = json.loads((self.root / 'now-playing.json').read_text())
            if not isinstance(data, dict):
                return 'STOPPED', {}
            age = time.time() - float(data['updated_at'])
            if data.get('version') != 1 or not -5 <= age <= 30:
                return 'STOPPED', {}
            status = data['status']
            if status != 'PLAYING':
                return ('PAUSED_PLAYBACK' if status == 'PAUSED_PLAYBACK' else 'STOPPED'), {}
            lines = data['lines']
            if not isinstance(lines, list) or len(lines) != 3 or not all(isinstance(x, str) for x in lines):
                return 'STOPPED', {}
            cover = data.get('cover', '')
            if not re.fullmatch(r'[a-f0-9]{64}\.jpg', cover):
                return 'PLAYING', {}  # No supported art: bridge restores original.
            if not all(re.fullmatch(r'roon:[a-f0-9]{64}', data.get(k, '')) for k in ('album_key', 'track_key')):
                return 'STOPPED', {}
            return 'PLAYING', {'source': 'roon', 'source_album_key': data['album_key'],
                'object_id': data['track_key'], 'roon_cover': cover, 'roon_lines': lines,
                'album': lines[2] or lines[0], 'artist': lines[1], 'title': lines[0]}
        except (OSError, ValueError, KeyError, TypeError):
            return 'STOPPED', {}

    def cover(self, metadata):
        name = metadata['roon_cover']
        if not re.fullmatch(r'[a-f0-9]{64}\.jpg', name):
            raise ValueError('Invalid Roon cover name')
        p = self.root / name
        if p.is_symlink() or p.stat().st_size > 20_000_000:
            raise ValueError('Invalid Roon cover file')
        return p.read_bytes()

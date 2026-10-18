"""Private, permanent photo exclusions. No network or identity recognition."""
import json
from pathlib import Path
import re


def valid_key(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


class Exclusions:
    def __init__(self, config):
        self.enabled = bool(config.get('photo_exclusions_file'))
        self.valid = not self.enabled
        self.content_keys = set()
        self.media_keys = set()
        if not self.enabled:
            return
        try:
            with Path(config['photo_exclusions_file']).expanduser().open('rb') as stream:
                raw = stream.read(5_000_001)
            if len(raw) > 5_000_000:
                return
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('schema') != 1:
                return
            for field in ('content_keys', 'media_keys'):
                values = data.get(field)
                if not isinstance(values, list) or not all(valid_key(v) for v in values):
                    return
                setattr(self, field, set(values))
            self.valid = True
        except (OSError, ValueError, TypeError):
            pass

    def allows(self, row):
        if not self.enabled:
            return True
        # Missing/corrupt exclusions or unclassified photos must never fail open.
        if not self.valid or not valid_key(row.get('content_key')):
            return False
        key = row.get('key') or Path(row.get('file', '')).stem
        return key not in self.media_keys and row['content_key'] not in self.content_keys

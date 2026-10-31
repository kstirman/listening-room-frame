"""Read the authorized Spotify account's current track; never control playback."""
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlparse

import requests

SCOPE = 'user-read-currently-playing'
TOKEN_URL = 'https://accounts.spotify.com/api/token'
PLAYBACK_URL = 'https://api.spotify.com/v1/me/player/currently-playing'
ID = re.compile(r'[A-Za-z0-9]{22}')
LOG = logging.getLogger('frame-art')


class SpotifyError(RuntimeError):
    """Safe, actionable message without response bodies or credentials."""


class RetryLater(SpotifyError):
    def __init__(self, seconds):
        super().__init__('Spotify is temporarily unavailable; retrying later.')
        self.seconds = seconds


def retry_after(response):
    try:
        value = float(response.headers.get('Retry-After', 60))
        return value if math.isfinite(value) and value >= 1 else 60
    except (ValueError, TypeError):
        return 60


def save_tokens(path, data):
    """Atomic private storage; no token data is returned to the UI or logs."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SpotifyError('Spotify token file must not be a symbolic link.')
    fd, name = tempfile.mkstemp(prefix='.spotify-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def token_response(response, client_id, previous=None):
    if response.status_code == 429:
        raise RetryLater(retry_after(response))
    if response.status_code >= 500:
        raise RetryLater(30)
    if response.status_code != 200:
        raise SpotifyError('Spotify authorization failed. Run spotify_auth.py to reconnect.')
    data = response.json()
    if not isinstance(data, dict):
        raise SpotifyError('Unexpected Spotify authorization response.')
    previous = previous or {}
    access = data.get('access_token')
    refresh = data.get('refresh_token') or previous.get('refresh_token')
    scope = data.get('scope', previous.get('scope', ''))
    expires = data.get('expires_in')
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or not isinstance(scope, str) or SCOPE not in scope.split()
            or not isinstance(data.get('token_type'), str) or data['token_type'].lower() != 'bearer'
            or isinstance(expires, bool) or not isinstance(expires, (int, float))
            or not math.isfinite(expires) or expires <= 0):
        raise SpotifyError('Spotify did not grant the required playback authorization.')
    return {'client_id': client_id, 'access_token': access, 'refresh_token': refresh,
            'scope': scope, 'expires_at': time.time() + expires}


def artwork_url(value):
    try:
        parsed = urlparse(value)
        return (parsed.scheme == 'https' and parsed.hostname == 'i.scdn.co'
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/image/[A-Za-z0-9]+', parsed.path) is not None)
    except (TypeError, ValueError, AttributeError):
        return False


def track_metadata(data):
    """Provider identity stays separate from Qobuz and local recording facts."""
    if not isinstance(data, dict):
        raise ValueError('Invalid playback data')
    if data.get('is_playing') is not True:
        return 'PAUSED_PLAYBACK', {}
    track = data.get('item')
    if data.get('currently_playing_type') != 'track' or not isinstance(track, dict):
        return 'STOPPED', {}  # Ads, episodes and unavailable items have no album card.
    if track.get('type') != 'track' or track.get('is_local'):
        return 'PLAYING', {}
    album = track.get('album')
    if not isinstance(album, dict) or not ID.fullmatch(str(track.get('id', ''))) or not ID.fullmatch(str(album.get('id', ''))):
        return 'PLAYING', {}
    images = album.get('images') or []
    if not isinstance(images, list):
        return 'PLAYING', {}
    images = [row for row in images if isinstance(row, dict) and artwork_url(row.get('url'))]
    if not images:
        return 'PLAYING', {}
    def width(row):
        value = row.get('width')
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
    image = max(images, key=width)
    def text(value):
        return value if isinstance(value, str) else ''
    def artists(rows):
        return '; '.join(text(row.get('name')) for row in rows if isinstance(row, dict)) if isinstance(rows, list) else ''
    duration = track.get('duration_ms')
    duration = duration / 1000 if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0 else None
    release = text(album.get('release_date'))
    if not re.fullmatch(r'\d{4}(?:-\d{2})?(?:-\d{2})?', release):
        release = ''
    return 'PLAYING', {
        'source': 'spotify', 'source_album_key': 'spotify:album:' + album['id'],
        'object_id': 'spotify:track:' + track['id'], 'spotify_cover': image['url'],
        'source_url': 'https://open.spotify.com/track/' + track['id'],
        'title': text(track.get('name')), 'album': text(album.get('name')),
        'artist': artists(track.get('artists')), 'albumArtist': artists(album.get('artists')),
        'duration_seconds': duration, 'releaseDate': release,
    }


class SpotifySource:
    def __init__(self, token_file, session=None, clock=None):
        if not isinstance(token_file, str) or not token_file.startswith(('/', '~/')):
            raise ValueError('Set spotify_token_file to the absolute path created by spotify_auth.py.')
        self.path = Path(token_file).expanduser()
        self.session = session or requests.Session()
        self.clock = clock or time.monotonic
        self.next_request = 0
        self.last_good = float('-inf')
        self.last = ('STOPPED', {})
        self.error = None

    def access_token(self, force=False):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 64_000:
                raise ValueError()
            data = json.loads(self.path.read_text())
            client = data['client_id']
            if not isinstance(client, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', client):
                raise ValueError()
            if SCOPE not in data.get('scope', '').split() or not isinstance(data.get('refresh_token'), str) or not data['refresh_token']:
                raise ValueError()
            expires = float(data.get('expires_at', 0))
            if not force and math.isfinite(expires) and expires > time.time() + 60 and isinstance(data.get('access_token'), str) and data['access_token']:
                return data['access_token']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise SpotifyError('Connect Spotify with spotify_auth.py before starting the display.') from None
        response = self.session.post(TOKEN_URL, data={'grant_type': 'refresh_token',
            'client_id': client, 'refresh_token': data['refresh_token']}, timeout=10, allow_redirects=False)
        if response.status_code in (400, 401):
            # Revoked/expired grants need fresh user authorization, not a retry loop.
            try:
                invalid = response.json().get('error') == 'invalid_grant'
            except (ValueError, AttributeError):
                invalid = False
            if invalid:
                save_tokens(self.path, {'client_id': client, 'authorization_required': True})
        updated = token_response(response, client, data)
        save_tokens(self.path, updated)
        return updated['access_token']

    def playback(self):
        now = self.clock()
        if now < self.next_request:
            return self.last if now - self.last_good <= 30 else ('STOPPED', {})
        self.next_request = now + 5
        try:
            token = self.access_token()
            for attempt in range(2):
                response = self.session.get(PLAYBACK_URL, headers={'Authorization': 'Bearer ' + token},
                                            timeout=10, allow_redirects=False)
                if response.status_code != 401 or attempt:
                    break
                token = self.access_token(force=True)
            if response.status_code == 429:
                raise RetryLater(retry_after(response))
            if response.status_code == 403:
                raise SpotifyError('Spotify denied access. Check Premium and the app user allowlist.')
            if response.status_code == 401:
                raise SpotifyError('Spotify authorization expired. Run spotify_auth.py to reconnect.')
            if response.status_code == 204:
                value = ('STOPPED', {})
            elif response.status_code == 200:
                value = track_metadata(response.json())
            else:
                raise RetryLater(30)
            self.last, self.last_good, self.error = value, now, None
            return value
        except RetryLater as error:
            self.next_request = self.clock() + error.seconds
            self.error = str(error)
        except SpotifyError as error:
            self.next_request = self.clock() + 60
            self.last = ('STOPPED', {})
            self.error = str(error)
        except (requests.RequestException, OSError, ValueError, TypeError, KeyError):
            self.next_request = self.clock() + 30
            self.error = 'Spotify could not be reached. Retrying shortly.'
        LOG.warning('%s', self.error)
        return self.last if self.clock() - self.last_good <= 30 else ('STOPPED', {})

    def cover(self, metadata):
        url = metadata.get('spotify_cover')
        if not artwork_url(url):
            raise ValueError('Unsupported Spotify artwork URL')
        # Separate unauthenticated request: never send the API token to the CDN.
        with requests.get(url, timeout=10, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError('Spotify artwork unavailable')
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 20_000_000:
                    raise ValueError('Spotify cover exceeds size limit')
            return bytes(content)

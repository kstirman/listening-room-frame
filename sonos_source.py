"""Read-only Sonos room metadata through optional SoCo. Never controls playback."""
import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests


def key(values):
    return 'sonos:' + hashlib.sha256(json.dumps(values).encode()).hexdigest()


class SonosSource:
    def __init__(self, uid, soco_module=None):
        if not uid or not isinstance(uid, str):
            raise ValueError('Set sonos_uid using sonos_source.py --discover')
        if soco_module is None:
            try:
                import soco as soco_module
            except ImportError:
                raise RuntimeError('Install requirements-sonos.txt for Sonos support') from None
        self.soco = soco_module
        self.soco.config.REQUEST_TIMEOUT = 4
        self.uid = uid
        self.speaker = None
        self.discovered_at = float('-inf')

    def playback(self):
        try:
            if self.speaker is None:
                if time.monotonic() - self.discovered_at < 30:
                    return 'STOPPED', {}
                self.discovered_at = time.monotonic()
                self.speaker = next((s for s in (self.soco.discover(timeout=4) or [])
                                     if s.uid == self.uid), None)
                if self.speaker is None:
                    return 'STOPPED', {}
            group = self.speaker.group
            if group is None:
                return 'STOPPED', {}
            player = group.coordinator
            status = player.get_current_transport_info()['current_transport_state']
            if status != 'PLAYING':
                return status, {}
            info = player.get_current_track_info()
            lines = [info.get(k, '') or '' for k in ('title', 'artist', 'album')]
            art = info.get('album_art', '')
            if not art:
                # SoCo can omit artwork when it successfully parses radio stream credits.
                raw = info.get('metadata', '')
                if raw and len(raw) < 1_000_000:
                    try:
                        art = next((el.text for el in ET.fromstring(raw).iter()
                                    if el.tag == '{urn:schemas-upnp-org:metadata-1-0/upnp/}albumArtURI'), '')
                    except ET.ParseError:
                        pass
            if not art:
                return 'PLAYING', {}
            art = urljoin('http://' + player.ip_address + ':1400/', art)
            album = key([lines[2], lines[1], art])
            return 'PLAYING', {'source': 'sonos', 'source_album_key': album,
                'object_id': key([album, lines[0], info.get('uri', '')]),
                'sonos_cover': art, 'sonos_lines': lines,
                'title': lines[0], 'artist': lines[1], 'album': lines[2] or lines[0]}
        except Exception as exc:
            # Do not emit exception messages: devices can include signed stream URLs.
            logging.warning('Sonos metadata unavailable (%s)', type(exc).__name__)
            self.speaker = None
            return 'STOPPED', {}

    def cover(self, metadata):
        url = metadata['sonos_cover']
        # Artwork URLs come from the selected trusted LAN speaker, never its audio URI.
        for _ in range(5):
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Unsupported Sonos artwork URL')
            with requests.get(url, timeout=10, stream=True, allow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['Location'])
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 20_000_000:
                        raise ValueError('Sonos cover exceeds size limit')
                return bytes(content)
        raise ValueError('Too many Sonos artwork redirects')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover', action='store_true', required=True)
    parser.parse_args()
    import soco
    soco.config.REQUEST_TIMEOUT = 4
    for speaker in sorted(soco.discover(timeout=5) or [], key=lambda s: s.player_name):
        print(json.dumps({'room': speaker.player_name, 'uid': speaker.uid}))

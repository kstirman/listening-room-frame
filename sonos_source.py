"""Read-only Sonos room metadata through optional SoCo. Never controls playback."""
import hashlib
import timing
import json
import logging
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import safe_requests
import requests
from playback_text import limit_texts


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
        self.artwork_origin = None
        self.session = safe_requests.Session()

    def playback(self):
        self.artwork_origin = None
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
            lines = limit_texts([info.get(k, '') or '' for k in ('title', 'artist', 'album')])
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
            artwork_origin = safe_requests.literal_http_origin(player.ip_address, 1400)
            art = urljoin(artwork_origin + '/', art)
            self.artwork_origin = artwork_origin
            album = key([lines[2], lines[1], art])
            return 'PLAYING', {'source': 'sonos', 'source_album_key': album,
                'object_id': key([album, lines[0], info.get('uri', '')]),
                'sonos_cover': art, 'sonos_lines': lines,
                'duration_seconds': timing.seconds(info.get('duration')),
                'title': lines[0], 'artist': lines[1], 'album': lines[2] or lines[0]}
        except Exception as exc:
            # Do not emit exception messages: devices can include signed stream URLs.
            logging.warning('Sonos metadata unavailable (%s)', type(exc).__name__)
            self.speaker = None
            return 'STOPPED', {}

    def cover(self, metadata):
        url = metadata['sonos_cover']
        # Permit the exact selected coordinator origin plus public service artwork.
        trusted = (self.artwork_origin,) if self.artwork_origin else ()
        response = self.session.get(url, timeout=10, max_redirects=5,
                                    max_bytes=20_000_000, trusted_origins=trusted)
        response.raise_for_status()
        if len(response.content) > 20_000_000:
            raise ValueError('Sonos cover exceeds size limit')
        return response.content


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover', action='store_true', required=True)
    parser.parse_args()
    import soco
    soco.config.REQUEST_TIMEOUT = 4
    for speaker in sorted(soco.discover(timeout=5) or [], key=lambda s: s.player_name):
        print(json.dumps({'room': speaker.player_name, 'uid': speaker.uid}))

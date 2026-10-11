"""Local discovery and configuration validation for the setup UI."""
import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests


def local_host(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', value):
        raise ValueError('Enter a local hostname or IPv4 address, without a URL or port.')
    addresses = socket.getaddrinfo(value, None, family=socket.AF_INET)
    if not addresses or not all(ipaddress.ip_address(a[4][0]).is_private and
                                not ipaddress.ip_address(a[4][0]).is_loopback for a in addresses):
        raise ValueError('The device must have a private local-network address.')
    return value


def probe_tv(host):
    host = local_host(host)
    with requests.get(f'http://{host}:8001/api/v2/', timeout=4, allow_redirects=False) as r:
        r.raise_for_status()
        if len(r.content) > 100_000:
            raise ValueError('Unexpected TV response')
        device = r.json()['device']
    model = device.get('modelName', '')
    if not model or not isinstance(model, str):
        raise ValueError('TV did not provide a model identifier')
    return {'host': host, 'model': model, 'name': str(device.get('name') or model)[:120]}


def discover_tvs():
    message = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
               'MAN: "ssdp:discover"\r\nMX: 1\r\nST: ssdp:all\r\n\r\n')
    hosts = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(.3)
        sock.sendto(message.encode(), ('239.255.255.250', 1900))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(hosts) < 16:
            try:
                _, address = sock.recvfrom(65536)
                hosts.add(address[0])  # Never follow an untrusted LOCATION header.
            except socket.timeout:
                pass
    def probe(host):
        try:
            return probe_tv(host)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [tv for tv in pool.map(probe, sorted(hosts)) if tv]


def discover_rooms():
    import soco
    soco.config.REQUEST_TIMEOUT = 4
    return sorted([{'name': s.player_name, 'uid': s.uid}
                   for s in soco.discover(timeout=4) or []], key=lambda s:s['name'])


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Configuration must be an object')
    result = {'source': data.get('source', 'sonos'), 'tv': local_host(data.get('tv', '')),
              'tv_model': data.get('tv_model', ''), 'poll_seconds': 2, 'idle_seconds': 120}
    if not isinstance(result['tv_model'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', result['tv_model']):
        raise ValueError('Check the TV connection to obtain its model identifier.')
    source = result['source']
    if source == 'sonos':
        uid = data.get('sonos_uid', '')
        if not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', uid):
            raise ValueError('Choose a Sonos room.')
        result['sonos_uid'] = uid
        name = data.get('sonos_room_name','')
        if isinstance(name,str) and not name.startswith('Saved room'):
            result['sonos_room_name'] = name[:120]
    elif source == 'roon':
        path = data.get('roon_state_dir', '')
        if not isinstance(path, str) or not path.startswith(('/', '~/')) or len(path) > 1024:
            raise ValueError('Enter the absolute Roon extension state-directory path.')
        result['roon_state_dir'] = path
    elif source == 'upnp':
        url = data.get('renderer_control_url', '')
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Enter the renderer AVTransport control URL.')
        local_host(parsed.hostname)
        result['renderer_control_url'] = url
        hosts = data.get('local_art_hosts', [])
        if not isinstance(hosts, list) or len(hosts) > 10:
            raise ValueError('Enter up to ten local artwork hosts.')
        result['local_art_hosts'] = [local_host(h) for h in hosts]
        # Preserve advanced local-library settings when supplied through the API.
        for field in ('minim_control', 'library_root', 'library_resource_prefix'):
            if field in data and isinstance(data[field], str):
                result[field] = data[field]
    else:
        raise ValueError('Choose Sonos, Roon or UPnP.')
    return result

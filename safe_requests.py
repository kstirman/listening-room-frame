"""Requests transport that pins validated HTTP(S) destinations."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from requests.cookies import extract_cookies_to_jar


_REDIRECTS = {301, 302, 303, 307, 308}


def address_allowed(value, allow_private=False):
    """Return whether an address is usable by a public or approved-local request."""
    address = ipaddress.ip_address(value.split('%', 1)[0])
    if getattr(address, 'ipv4_mapped', None):
        address = address.ipv4_mapped
    if (address.is_loopback or address.is_link_local or address.is_multicast
            or address.is_unspecified or address.is_reserved):
        return False
    return address.is_global or (allow_private and address.is_private)


def literal_http_origin(value, port):
    """Build an HTTP origin for a selected device's already-known IP address."""
    address = ipaddress.ip_address(value)
    if not address_allowed(str(address), allow_private=True):
        raise ValueError('Unsafe local device address')
    host = f'[{address.compressed}]' if address.version == 6 else address.compressed
    return f'http://{host}:{port}'


def _hostname(value):
    if not isinstance(value, str) or not value:
        raise ValueError('HTTP destination has no hostname')
    try:
        return value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ValueError('HTTP destination has an invalid hostname') from error


def _destination(url):
    try:
        parsed = urlsplit(url)
        hostname = _hostname(parsed.hostname)
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid HTTP destination') from error
    if (parsed.scheme not in ('http', 'https') or parsed.username is not None
            or parsed.password is not None or parsed.fragment or not 1 <= port <= 65535):
        raise ValueError('Unsupported HTTP destination')
    return parsed, hostname, port


def _origin(url):
    parsed, hostname, port = _destination(url)
    return parsed.scheme, hostname, port


class _PinnedHTTPSAdapter(HTTPAdapter):
    """Connect to a pinned IP while authenticating the original TLS hostname."""
    def __init__(self, server_hostname):
        self.server_hostname = server_hostname
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs['server_hostname'] = self.server_hostname
        pool_kwargs['assert_hostname'] = self.server_hostname
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class Session(requests.Session):
    """A direct HTTP session with destination validation and DNS pinning."""
    def __init__(self, trusted_hosts=(), trusted_origins=()):
        super().__init__()
        self.trusted_hosts = {_hostname(host) for host in trusted_hosts}
        self.trusted_origins = {_origin(url) for url in trusted_origins}
        self._trusted_addresses = {}

    def pin(self, url, address):
        """Reuse an address already validated for a configured destination."""
        parsed, hostname, port = _destination(url)
        if (hostname not in self.trusted_hosts
                and (parsed.scheme, hostname, port) not in self.trusted_origins):
            raise ValueError('Cannot pin an unapproved private destination')
        address = str(ipaddress.ip_address(address.split('%', 1)[0]))
        if not address_allowed(address, allow_private=True):
            raise ValueError('Cannot pin a disallowed address')
        self._trusted_addresses[(hostname, port)] = address

    def _resolve(self, url, extra_hosts=(), extra_origins=()):
        parsed, hostname, port = _destination(url)
        trusted_hosts = self.trusted_hosts | {_hostname(host) for host in extra_hosts}
        trusted_origins = self.trusted_origins | {_origin(origin) for origin in extra_origins}
        allow_private = hostname in trusted_hosts or (parsed.scheme, hostname, port) in trusted_origins
        cache_key = hostname, port
        if allow_private and cache_key in self._trusted_addresses:
            return parsed, hostname, self._trusted_addresses[cache_key]
        try:
            answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise requests.ConnectionError('Unable to resolve approved HTTP destination') from error
        addresses = []
        for answer in answers:
            address = answer[4][0].split('%', 1)[0]
            if address not in addresses:
                addresses.append(address)
        if not addresses or not all(address_allowed(address, allow_private) for address in addresses):
            raise ValueError('HTTP destination resolves to a disallowed address')
        if allow_private:
            # Configured LAN names remain bound to the peer approved on first use.
            self._trusted_addresses[cache_key] = addresses[0]
        return parsed, hostname, addresses[0]

    @staticmethod
    def _pin_url(parsed, address):
        host = f'[{address}]' if ':' in address else address
        if parsed.port is not None:
            host += f':{parsed.port}'
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ''))

    @staticmethod
    def _host_header(parsed, hostname):
        host = f'[{hostname}]' if ':' in hostname else hostname
        return f'{host}:{parsed.port}' if parsed.port is not None else host

    def _send_pinned(self, prepared, hostname, timeout, verify, cert):
        adapter = (_PinnedHTTPSAdapter(hostname)
                   if prepared.url.startswith('https:') else HTTPAdapter())
        try:
            response = adapter.send(prepared, stream=True, timeout=timeout,
                                    verify=verify, cert=cert, proxies={})
            return response, adapter
        except Exception:
            adapter.close()
            raise

    @staticmethod
    def _consume(response, maximum):
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if maximum is not None and len(content) > maximum:
                raise ValueError('HTTP response exceeds size limit')
        response._content = bytes(content)
        response._content_consumed = True

    def request(self, method, url, params=None, data=None, headers=None, cookies=None,
                files=None, auth=None, timeout=None, allow_redirects=True, proxies=None,
                hooks=None, stream=None, verify=None, cert=None, json=None,
                trusted_hosts=(), trusted_origins=(), max_redirects=5, max_bytes=None):
        """Send directly to a validated peer and revalidate each allowed redirect."""
        if proxies:
            raise ValueError('Proxies are unsupported for destination-pinned requests')
        if max_redirects < 0:
            raise ValueError('Invalid redirect limit')
        current_url = url
        current_params = params
        history = []
        method = method.upper()
        while True:
            request = requests.Request(method=method, url=current_url, headers=headers,
                                       files=files, data=data, json=json, params=current_params,
                                       auth=auth, cookies=cookies, hooks=hooks)
            prepared = self.prepare_request(request)
            logical_url = prepared.url
            parsed, hostname, address = self._resolve(
                logical_url, extra_hosts=trusted_hosts, extra_origins=trusted_origins)
            prepared.url = self._pin_url(parsed, address)
            prepared.headers['Host'] = self._host_header(parsed, hostname)
            response = adapter = None
            try:
                response, adapter = self._send_pinned(
                    prepared, hostname, timeout,
                    self.verify if verify is None else verify,
                    self.cert if cert is None else cert)
                response.url = logical_url
                extract_cookies_to_jar(self.cookies, prepared, response.raw)
                location = response.headers.get('Location')
                redirect = response.status_code in _REDIRECTS and location is not None
                if redirect and allow_redirects:
                    if method not in ('GET', 'HEAD'):
                        raise requests.TooManyRedirects('Refusing to redirect a non-read request')
                    if len(history) >= max_redirects:
                        raise requests.TooManyRedirects('Too many HTTP redirects')
                    response.history = list(history)
                    history.append(response)
                    current_url = urljoin(logical_url, location)
                    current_params = None
                    response.close()
                    adapter.close()
                    continue
                if stream:
                    close_response = response.close
                    def close_stream(close_response=close_response, adapter=adapter):
                        try:
                            close_response()
                        finally:
                            adapter.close()
                    response.close = close_stream
                    response.history = history
                    return response
                self._consume(response, max_bytes)
                response.history = history
                response.close()
                adapter.close()
                return response
            except Exception:
                if response is not None:
                    response.close()
                if adapter is not None:
                    adapter.close()
                raise

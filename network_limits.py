"""Bounded retrieval and parsing for untrusted network responses."""
from collections.abc import Mapping
import json
import re
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from requests.structures import CaseInsensitiveDict

try:
    from urllib3.exceptions import HTTPError as Urllib3Error
except ImportError:  # Requests always supplies urllib3; retain a safe fallback.
    Urllib3Error = OSError


CHUNK_SIZE = 64 * 1024


class ResponseLimitError(requests.RequestException):
    """A peer exceeded an explicit response or parser budget."""


class ResponseDeadlineError(requests.Timeout):
    """A peer exceeded the total response deadline."""


class LimitedResponse:
    """The small response surface callers need after the socket is closed."""
    def __init__(self, content, response):
        self.content = content
        headers = response.headers if isinstance(response.headers, Mapping) else {}
        self.headers = CaseInsensitiveDict(headers)
        status = getattr(response, 'status_code', None)
        self.status_code = status if isinstance(status, int) else None
        encoding = getattr(response, 'encoding', None)
        self.encoding = encoding if isinstance(encoding, str) else None

    @property
    def text(self):
        encoding = self.encoding or requests.utils.get_encoding_from_headers(self.headers) or 'utf-8'
        try:
            return self.content.decode(encoding, errors='replace')
        except LookupError:
            return self.content.decode('utf-8', errors='replace')

    def json(self, **limits):
        return json_value(self.content, **limits)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ResponseDeadlineError('Network response exceeded total deadline')
    return remaining


def _bounded_timeout(timeout, total_seconds):
    def bound(value):
        try:
            return min(float(value), total_seconds)
        except (TypeError, ValueError):
            return total_seconds
    if isinstance(timeout, tuple):
        return tuple(bound(value) for value in timeout)
    return bound(timeout)


def _socket(raw):
    """Locate urllib3's active socket so each read observes the total deadline."""
    paths = (('_fp', 'fp', 'raw', '_sock'), ('_connection', 'sock'))
    for path in paths:
        value = raw
        for name in path:
            value = getattr(value, name, None)
            if value is None:
                break
        if value is not None and callable(getattr(value, 'settimeout', None)):
            return value
    return None


def _append(content, chunk, max_bytes):
    if not chunk:
        return False
    if len(content) + len(chunk) > max_bytes:
        raise ResponseLimitError('Network response exceeds size limit')
    content.extend(chunk)
    return True


def _read(response, max_bytes, deadline):
    headers = response.headers if isinstance(response.headers, Mapping) else {}
    length = str(headers.get('Content-Length', '')).strip()
    if length:
        if not length.isdigit():
            raise ResponseLimitError('Invalid network Content-Length')
        if int(length) > max_bytes:
            raise ResponseLimitError('Network response exceeds size limit')
    encoding = str(headers.get('Content-Encoding', '')).strip().lower()
    if encoding not in ('', 'identity'):
        # Every request below explicitly negotiates identity. Refusing an
        # unsolicited encoding prevents decompression from bypassing byte caps.
        raise ResponseLimitError('Unsupported network Content-Encoding')

    content = bytearray()
    raw = getattr(response, 'raw', None)
    read1 = getattr(raw, 'read1', None)
    if (raw is not None and callable(read1)
            and raw.__class__.__module__.startswith('urllib3')):
        while True:
            remaining = _remaining(deadline)
            sock = _socket(raw)
            if sock is not None:
                sock.settimeout(remaining)
            amount = min(CHUNK_SIZE, max_bytes + 1 - len(content))
            try:
                try:
                    chunk = read1(amount, decode_content=False)
                except TypeError:
                    chunk = read1(amount)
            except (Urllib3Error, OSError) as error:
                raise requests.ConnectionError(error) from error
            if not _append(content, chunk, max_bytes):
                break
        return bytes(content)

    # Test doubles and non-default Requests adapters may not expose urllib3's
    # raw stream. They still pass through the same cumulative byte/deadline cap.
    try:
        chunks = iter(response.iter_content(chunk_size=CHUNK_SIZE))
    except TypeError:
        cached = getattr(response, 'content', b'')
        chunks = iter((cached,)) if isinstance(cached, (bytes, bytearray)) else iter(())
    for chunk in chunks:
        _remaining(deadline)
        _append(content, chunk, max_bytes)
    _remaining(deadline)
    return bytes(content)


def _same_origin(source, target):
    try:
        first, second = urlparse(source), urlparse(target)
        def port(parsed):
            return parsed.port or {'http': 80, 'https': 443}.get(parsed.scheme.lower())
        return (first.scheme.lower(), first.hostname, port(first)) == (
            second.scheme.lower(), second.hostname, port(second)) and not (
                second.username or second.password)
    except (TypeError, ValueError):
        return False


def fetch(request, url, *, max_bytes, total_seconds, same_origin_redirects=0,
          **kwargs):
    """Request one response without allowing Requests to buffer it unbounded."""
    if max_bytes <= 0 or total_seconds <= 0:
        raise ValueError('Response limits must be positive')
    deadline = time.monotonic() + total_seconds
    headers = dict(kwargs.pop('headers', {}) or {})
    headers['Accept-Encoding'] = 'identity'
    kwargs['headers'] = headers
    kwargs['stream'] = True
    kwargs.pop('allow_redirects', None)
    kwargs['allow_redirects'] = False
    original_timeout = kwargs.get('timeout')
    current = url
    for redirect in range(same_origin_redirects + 1):
        remaining = _remaining(deadline)
        kwargs['timeout'] = _bounded_timeout(
            original_timeout, total_seconds if redirect == 0 else remaining)
        response = request(current, **kwargs)
        try:
            response.raise_for_status()
            status = getattr(response, 'status_code', None)
            if isinstance(status, int) and 300 <= status < 400:
                location = response.headers.get('Location') if isinstance(response.headers, Mapping) else None
                target = urljoin(current, location) if isinstance(location, str) else ''
                if redirect >= same_origin_redirects or not _same_origin(current, target):
                    raise requests.TooManyRedirects('Unexpected network redirect')
                current = target
                # Requests applies params once before following Location.
                kwargs.pop('params', None)
                continue
            content = _read(response, max_bytes, deadline)
            return LimitedResponse(content, response)
        finally:
            response.close()
    raise requests.TooManyRedirects('Too many network redirects')


def json_value(data, *, max_bytes, max_depth=64, max_nodes=100_000,
               max_text=1_000_000):
    """Decode bounded JSON and reject excessive nested materialization."""
    if isinstance(data, str):
        raw = data.encode('utf-8')
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raise ValueError('JSON response must be text')
    if len(raw) > max_bytes:
        raise ResponseLimitError('JSON response exceeds size limit')
    value = json.loads(raw)
    nodes = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise ResponseLimitError('JSON response exceeds structure limit')
        if isinstance(item, str) and len(item) > max_text:
            raise ResponseLimitError('JSON text exceeds size limit')
        if isinstance(item, dict):
            if any(not isinstance(key, str) or len(key) > max_text for key in item):
                raise ResponseLimitError('JSON key exceeds size limit')
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


class _LimitedTreeBuilder:
    def __init__(self, max_depth, max_nodes, max_text):
        self.builder = ET.TreeBuilder()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_text = max_text
        self.depth = 0
        self.nodes = 0
        self.text_size = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > self.max_depth or self.nodes > self.max_nodes:
            raise ResponseLimitError('XML response exceeds structure limit')
        return self.builder.start(tag, attrs)

    def data(self, data):
        self.text_size += len(data)
        if self.text_size > self.max_text:
            raise ResponseLimitError('XML text exceeds size limit')
        return self.builder.data(data)

    def end(self, tag):
        element = self.builder.end(tag)
        self.depth -= 1
        return element

    def close(self):
        return self.builder.close()


def xml_root(data, *, max_bytes, max_depth=32, max_nodes=10_000,
             max_text=1_000_000):
    """Parse bounded XML without DTDs, entities, or unbounded tree structure."""
    if isinstance(data, str):
        raw = data.encode('utf-8')
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raise ValueError('XML response must be text')
    if len(raw) > max_bytes:
        raise ResponseLimitError('XML response exceeds size limit')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise ResponseLimitError('XML declarations are not supported')
    builder = _LimitedTreeBuilder(max_depth, max_nodes, max_text)
    parser = ET.XMLParser(target=builder)
    for offset in range(0, len(raw), CHUNK_SIZE):
        parser.feed(raw[offset:offset + CHUNK_SIZE])
    root = parser.close()
    if root is None:
        raise ValueError('XML response has no root element')
    return root

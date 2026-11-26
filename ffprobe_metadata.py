"""Resource-bounded ffprobe format metadata extraction."""
import json
import os
import selectors
import subprocess
import time

try:
    import resource
except ImportError:  # pragma: no cover - Linux is the deployment target.
    resource = None


MAX_OUTPUT_BYTES = 1024 * 1024
MAX_TAGS = 256
MAX_TAG_KEY_BYTES = 512
MAX_TAG_VALUE_BYTES = 64 * 1024
MAX_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
MAX_CPU_SECONDS = 8


def _child_limits():
    """Restrict ffprobe without raising inherited resource ceilings."""
    for name, ceiling in (('RLIMIT_CPU', MAX_CPU_SECONDS),
                          ('RLIMIT_AS', MAX_ADDRESS_SPACE_BYTES)):
        kind = getattr(resource, name, None)
        if kind is None:
            continue
        try:
            soft, hard = resource.getrlimit(kind)
            finite = [ceiling]
            if soft != resource.RLIM_INFINITY:
                finite.append(soft)
            if hard != resource.RLIM_INFINITY:
                finite.append(hard)
            limit = min(finite)
            resource.setrlimit(kind, (limit, limit))
        except (OSError, ValueError):
            # The wall-clock and output limits remain mandatory on platforms
            # that expose a resource constant but do not permit this limit.
            pass


def _read_stdout(process, command, timeout):
    deadline = time.monotonic() + timeout
    output = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(output))
            if not selector.select(remaining):
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(output))
            chunk = os.read(process.stdout.fileno(), min(65536, MAX_OUTPUT_BYTES + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_OUTPUT_BYTES:
                raise ValueError('ffprobe output exceeds byte limit')
    remaining = max(0, deadline - time.monotonic())
    returncode = process.wait(timeout=remaining)
    if returncode:
        raise subprocess.CalledProcessError(returncode, command, output=bytes(output))
    return bytes(output)


def _run(command, timeout):
    options = {
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.PIPE,
        'stderr': subprocess.DEVNULL,
    }
    if resource is not None and os.name == 'posix':
        options['preexec_fn'] = _child_limits
    process = subprocess.Popen(command, **options)
    try:
        return _read_stdout(process, command, timeout)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def read(path, timeout=8, probe_args=()):
    """Return normalized format tags and duration within fixed resource budgets."""
    command = ['ffprobe', '-nostdin', '-v', 'quiet', *probe_args,
               '-show_entries', 'format=duration:format_tags', '-of', 'json', str(path)]
    document = json.loads(_run(command, timeout))
    if not isinstance(document, dict):
        raise ValueError('ffprobe document is not an object')
    data = document.get('format', {})
    if not isinstance(data, dict):
        raise ValueError('ffprobe format is not an object')
    raw_tags = data.get('tags', {})
    if not isinstance(raw_tags, dict) or len(raw_tags) > MAX_TAGS:
        raise ValueError('ffprobe tags exceed count limit')
    tags = {}
    for key, value in raw_tags.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError('ffprobe tag is not text')
        try:
            key_size = len(key.encode('utf-8'))
            value_size = len(value.encode('utf-8'))
        except UnicodeEncodeError as error:
            raise ValueError('ffprobe tag is not valid UTF-8') from error
        if key_size > MAX_TAG_KEY_BYTES or value_size > MAX_TAG_VALUE_BYTES:
            raise ValueError('ffprobe tag exceeds value limit')
        tags[key.lower()] = value
    return {'duration': data.get('duration'), 'tags': tags}

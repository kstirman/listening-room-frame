"""Opt-in private preservation of already accepted Qobuz cover bytes.

No network, caption interpretation, or provider track metadata is involved.
Objects are immutable. A manifest is committed only after its object is durable.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlparse

from PIL import Image


class Unavailable(ValueError):
    pass


class Archive:
    # Bridge is the sole writer. Backup only reads immutable objects and atomic
    # manifests. Do not invoke preserve concurrently from another process.
    def __init__(self, root, mount):
        self.root = Path(root)
        self.mount = Path(mount)

    def checked_root(self):
        # A missing mount must never redirect archive writes onto the root disk.
        if not self.mount.is_absolute() or not os.path.ismount(self.mount):
            raise Unavailable('Evidence mount unavailable')
        if self.mount.is_symlink() or self.mount.resolve() != self.mount:
            raise Unavailable('Evidence mount is not canonical')
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise Unavailable('Evidence root is not canonical')
        if self.mount not in self.root.parents:
            raise Unavailable('Evidence root is outside configured mount')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_dev != self.mount.stat().st_dev:
            raise Unavailable('Evidence root device mismatch')
        return self.root

    @staticmethod
    def atomic(path, content):
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def preserve(self, album_key, content, source_url=None):
        match = re.fullmatch(r'qobuz:([A-Za-z0-9]{4,64})', album_key or '')
        if not match:
            raise Unavailable('Exact Qobuz identity required')
        album_id = match[1]
        if not content or len(content) > 20_000_000:
            raise Unavailable('Evidence size rejected')
        with Image.open(io.BytesIO(content)) as picture:
            picture.verify()
        with Image.open(io.BytesIO(content)) as picture:
            dimensions = list(picture.size)
            if picture.format != 'JPEG':
                raise Unavailable('Expected normalized cached JPEG')
        root = self.checked_root()
        for name in ('objects', 'albums'):
            p = root / name
            if p.is_symlink():
                raise Unavailable('Evidence directory is a symlink')
            p.mkdir(mode=0o700, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        relative = 'objects/' + digest + '.jpg'
        target = root / relative
        manifest = root / 'albums' / (album_id + '.json')
        if target.is_symlink() or manifest.is_symlink():
            raise Unavailable('Evidence file is a symlink')
        if target.exists():
            if target.read_bytes() != content:
                raise Unavailable('Evidence object collision or corruption')
        else:
            space = os.statvfs(root)
            if space.f_bavail * space.f_frsize < len(content) + 16 * 1024 * 1024:
                raise Unavailable('Evidence filesystem reserve reached')
            self.atomic(target, content)
        data = json.loads(manifest.read_text()) if manifest.exists() else {
            'schema_version': 1, 'identity': album_key, 'album_id': album_id, 'objects': []}
        if data.get('schema_version') != 1 or data.get('identity') != album_key or data.get('album_id') != album_id:
            raise Unavailable('Evidence manifest identity mismatch')
        if any(row.get('sha256') == digest for row in data['objects']):
            self.verify_manifest(root, data)
            return relative
        row = {'sha256': digest, 'path': relative, 'size': len(content),
               'dimensions': dimensions, 'saved_at': time.time(),
               'provenance': 'accepted_cached_cover',
               'encoding': 'normalized_jpeg', 'source_method': 'unknown'}
        parsed = urlparse(source_url or '')
        if (parsed.scheme == 'https' and parsed.netloc == 'static.qobuz.com'
                and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/images/covers/[A-Za-z0-9]{2}/[A-Za-z0-9]{2}/' + re.escape(album_id) + r'_[0-9]{2,4}\.jpg', parsed.path)):
            row['source_url'] = source_url
        data['objects'].append(row)
        self.verify_manifest(root, data)
        self.atomic(manifest, json.dumps(data, indent=2, sort_keys=True).encode())
        return relative

    @staticmethod
    def verify_manifest(root, data):
        """Offline restore check; no absolute paths or executable configuration."""
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root or (root / 'objects').is_symlink():
            raise Unavailable('Noncanonical evidence root')
        if not isinstance(data, dict) or not isinstance(data.get('objects'), list) or not data['objects']:
            raise Unavailable('Invalid evidence manifest')
        album_id = data.get('album_id', '')
        if (data.get('schema_version') != 1 or not re.fullmatch(r'[A-Za-z0-9]{4,64}', album_id)
                or data.get('identity') != 'qobuz:' + album_id):
            raise Unavailable('Invalid evidence identity')
        for row in data['objects']:
            if not isinstance(row, dict):
                raise Unavailable('Invalid evidence object')
            digest = row.get('sha256', '')
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or row.get('path') != 'objects/' + digest + '.jpg':
                raise Unavailable('Invalid evidence object path')
            path = Path(root) / row['path']
            if path.is_symlink() or path.resolve().parent != (Path(root) / 'objects').resolve():
                raise Unavailable('Unsafe evidence object')
            content = path.read_bytes()
            if len(content) != row['size'] or hashlib.sha256(content).hexdigest() != digest:
                raise Unavailable('Evidence object verification failed')
            with Image.open(io.BytesIO(content)) as picture:
                if picture.format != 'JPEG' or list(picture.size) != row.get('dimensions'):
                    raise Unavailable('Evidence image dimensions or type mismatch')
                picture.verify()
        return True

    def backup(self, output):
        """Private companion ZIP; stage beside output, never system /tmp."""
        import zipfile
        root = self.checked_root()
        output = Path(output)
        if (root / 'albums').is_symlink() or (root / 'objects').is_symlink():
            raise Unavailable('Unsafe evidence directory')
        if output.exists():
            raise FileExistsError(output)
        contents = {}
        for path in sorted((root / 'albums').glob('*.json')):
            if path.is_symlink():
                raise Unavailable('Unsafe manifest')
            raw = path.read_bytes()
            data = json.loads(raw)
            self.verify_manifest(root, data)
            if path.name != data['album_id'] + '.json':
                raise Unavailable('Manifest filename mismatch')
            contents['albums/' + path.name] = raw
            for row in data['objects']:
                contents[row['path']] = root / row['path']
        contents['RESTORE.json'] = json.dumps({'schema_version': 1,
            'instructions': 'Restore into a separate private directory. Verify SHA256.json and every album manifest with Archive.verify_manifest before using. Contains private Qobuz cover evidence, not verified track metadata. Never overwrite newer evidence blindly.'}).encode()
        hashes = {}
        fd, name = tempfile.mkstemp(prefix='.evidence-backup-', dir=output.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, value in sorted(contents.items()):
                    raw = value.read_bytes() if isinstance(value, Path) else value
                    hashes[name] = hashlib.sha256(raw).hexdigest()
                    archive.writestr(name, raw)
                archive.writestr('SHA256.json', json.dumps(hashes, indent=2))
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip():
                    raise Unavailable('Evidence archive CRC failed')
                for name, expected in hashes.items():
                    if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                        raise Unavailable('Evidence archive hash failed')
            # Link provides no-overwrite commit even if output appeared mid-backup.
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {'path': str(output), 'objects': sum(n.startswith('objects/') for n in hashes), 'files': len(hashes)}

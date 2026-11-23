"""No-follow filesystem operations for library metadata tools."""
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | _CLOEXEC
_WRITE_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC


def _relative_parts(relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part == '..' for part in path.parts):
        raise ValueError('Path must be relative and remain beneath the library root')
    return path.parts


def _validate_private_directory(fd, label):
    details = os.fstat(fd)
    if (not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid()
            or details.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise ValueError(f'{label} must be a private, operator-owned real directory')
    return details


@contextmanager
def open_root(root):
    """Hold the selected root open so later operations cannot redirect it."""
    try:
        fd = os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ValueError('Library root must be an existing real directory') from error
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError('Library root must be an existing real directory')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def open_private_directory(parent_fd, name, *, create=False, missing_message=None):
    """Open one private child directory without following a preexisting link."""
    if len(_relative_parts(name)) != 1:
        raise ValueError('Metadata directory name must be one path component')
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError as error:
        if missing_message:
            raise ValueError(missing_message) from error
        raise
    except OSError as error:
        raise ValueError(f'{name} must be a private, operator-owned real directory') from error
    try:
        _validate_private_directory(fd, name)
        yield fd
    finally:
        os.close(fd)


def _open_parent(root_fd, relative):
    parts = _relative_parts(relative)
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = next_fd
        return current, parts[-1]
    except OSError as error:
        os.close(current)
        raise ValueError('Path must use real directories beneath the library root') from error


@contextmanager
def open_regular_beneath(root_fd, relative):
    """Open a regular file through link-free components beneath root_fd."""
    parent_fd, name = _open_parent(root_fd, relative)
    file_fd = None
    try:
        try:
            expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(expected.st_mode):
                raise ValueError('Path must name a non-symlink regular file beneath the library root')
            file_fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ValueError('Path must name a non-symlink regular file beneath the library root') from error
        opened = os.fstat(file_fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)):
            raise ValueError('Path must name a non-symlink regular file beneath the library root')
        stream = os.fdopen(file_fd, 'rb')
        file_fd = None
        with stream:
            yield stream
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def require_regular_beneath(root_fd, relative):
    with open_regular_beneath(root_fd, relative):
        return True


def regular_file_beneath(root_fd, relative):
    try:
        return require_regular_beneath(root_fd, relative)
    except (FileNotFoundError, ValueError):
        return False


def read_json_at(directory_fd, name, default):
    try:
        with open_regular_beneath(directory_fd, name) as stream:
            return json.loads(stream.read().decode('utf-8'))
    except FileNotFoundError:
        return default


def regular_file_exists_at(directory_fd, name):
    try:
        return require_regular_beneath(directory_fd, name)
    except FileNotFoundError:
        return False


def write_new_bytes_at(directory_fd, name, data):
    """Create one regular file exclusively; never open an existing leaf."""
    if len(_relative_parts(name)) != 1:
        raise ValueError('Output name must be one path component')
    file_fd = None
    created = False
    try:
        try:
            file_fd = os.open(name, _WRITE_NEW_FLAGS, 0o600, dir_fd=directory_fd)
            created = True
        except FileExistsError:
            return False
        with os.fdopen(file_fd, 'wb') as stream:
            file_fd = None
            stream.write(data)
        return True
    except Exception:
        if created:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _validate_output(directory_fd, name):
    try:
        details = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f'Refusing to replace non-regular metadata output: {name}')


def write_atomic_bytes_at(directory_fd, name, data):
    """Write through an unpredictable exclusive temporary file and replace atomically."""
    if len(_relative_parts(name)) != 1:
        raise ValueError('Output name must be one path component')
    _validate_output(directory_fd, name)
    temporary = None
    temporary_details = None
    file_fd = None
    try:
        for _ in range(128):
            candidate = f'.{name}.{secrets.token_hex(16)}.tmp'
            try:
                file_fd = os.open(candidate, _WRITE_NEW_FLAGS, 0o600, dir_fd=directory_fd)
                temporary = candidate
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError('Unable to allocate an exclusive metadata temporary file')
        stream = os.fdopen(file_fd, 'wb')
        file_fd = None
        with stream:
            stream.write(data)
            stream.flush()
            temporary_details = os.fstat(stream.fileno())

        _validate_private_directory(directory_fd, 'Metadata storage')
        current = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (temporary_details.st_dev, temporary_details.st_ino)):
            raise ValueError('Metadata temporary file changed before replacement')
        _validate_output(directory_fd, name)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def write_json_atomic_at(directory_fd, name, data, *, ensure_ascii=False, indent=2):
    payload = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent).encode('utf-8')
    write_atomic_bytes_at(directory_fd, name, payload)


def copy_file_exclusive_at(directory_fd, source, destination):
    with open_regular_beneath(directory_fd, source) as stream:
        content = stream.read()
    if not write_new_bytes_at(directory_fd, destination, content):
        raise FileExistsError(f'Metadata backup already exists: {destination}')

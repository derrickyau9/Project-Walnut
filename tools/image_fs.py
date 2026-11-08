"""Build and inspect regular-file ext4 images without mounts or host privilege.

Only a non-root Linux process with existing e2fsprogs is supported. File
descriptors pin every tool input/output; no block/character device is accepted.
Failed images and their configuration remain for diagnosis, never as success.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid as uuid_module


MAX_IMAGE_BYTES = 4096 * 1024**2
MAX_EXTRACT_BYTES = 64 * 1024**2
MAX_ENTRIES = 200000
MAX_TOOL_OUTPUT = 128 * 1024
CONFIG = '''[defaults]
base_features = sparse_super,large_file,filetype,resize_inode,dir_index,ext_attr
blocksize = 4096
inode_size = 256
inode_ratio = 16384
[fs_types]
small = {
}
ext4 = {
features = has_journal,extent,huge_file,flex_bg,metadata_csum,64bit,dir_nlink,extra_isize
}
'''


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _plain(info, kind):
    return kind(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _input(path):
    path = Path(os.path.abspath(os.fspath(path)))
    before = path.lstat()
    if not _plain(before, stat.S_ISREG) or path.resolve(strict=True) != path:
        raise ValueError('image inputs must be canonical regular files without symbolic links')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        opened = os.fstat(descriptor)
        if not _plain(opened, stat.S_ISREG) or _snapshot(opened) != _snapshot(before):
            raise ValueError('image input changed during open')
        return path, os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def _destination(path):
    path = Path(os.path.abspath(os.fspath(path)))
    parent = path.parent
    info = parent.lstat()
    if (not _plain(info, stat.S_ISDIR) or parent.resolve(strict=True) != parent or
            not path.name or path.name in ('.', '..') or '\0' in path.name):
        raise ValueError('destination parent must be an existing canonical directory')
    if os.path.lexists(path):
        raise ValueError('destination must not exist')
    return path, _snapshot(info)


@contextmanager
def _parent(path, before):
    # Actual operations require Linux. The fallback supports pure guard tests
    # with mocked tools on hosts that cannot perform dir_fd operations.
    if os.open not in os.supports_dir_fd:
        yield None
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        if (not _plain(opened, stat.S_ISDIR) or (opened.st_dev, opened.st_ino) != before[:2] or
                path.resolve(strict=True) != path):
            raise ValueError('destination parent changed during open')
        yield descriptor
    finally:
        os.close(descriptor)


def _new_file(path, parent_descriptor=None):
    descriptor = os.open(path.name if parent_descriptor is not None else path,
                         os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600,
                         dir_fd=parent_descriptor)
    try:
        if not _plain(os.fstat(descriptor), stat.S_ISREG):
            raise ValueError('destination is not a regular file')
        return os.fdopen(descriptor, 'w+b')
    except BaseException:
        os.close(descriptor)
        raise


def _name(value, *, root=False):
    if not isinstance(value, str) or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('unsafe archive path')
    value = value.rstrip('/')
    while value.startswith('./'):
        value = value[2:]
    if root and value == '.':
        return ''
    if not value or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('archive member names must be relative paths without traversal')
    encoded = value.encode('utf-8')
    if len(encoded) > 4096 or any(len(part.encode('utf-8')) > 255 for part in value.split('/')):
        raise ValueError('archive path exceeds filesystem bounds')
    return value


def _link_target(name, target, *, hard=False):
    if (not isinstance(target, str) or not target or '\\' in target or
            any(ord(c) < 32 or ord(c) == 127 for c in target)):
        raise ValueError('unsafe archive link target')
    target.encode('utf-8')
    # Absolute link targets are guest-root paths, never host files. Relative
    # symlinks can use .. only while remaining inside that same guest root.
    base = '' if hard or target.startswith('/') else posixpath.dirname(name)
    result = posixpath.normpath(posixpath.join(base, target.lstrip('/')))
    if result == '..' or result.startswith('../'):
        raise ValueError('archive link escapes the guest root')
    return '' if result == '.' else _name(result)


def _validate_tar(stream, maximum):
    initial = os.fstat(stream.fileno())
    if not 0 < initial.st_size <= MAX_IMAGE_BYTES:
        raise ValueError('tar archive exceeds supported bounds')
    stream.seek(0)
    entries, links, regular_bytes = {}, {}, 0
    try:
        with tarfile.open(fileobj=stream, mode='r:') as archive:
            for member in archive:
                if len(entries) >= MAX_ENTRIES:
                    raise ValueError('too many root filesystem entries')
                name = _name(member.name, root=member.isdir())
                if name in entries:
                    raise ValueError('duplicate archive member')
                if not (member.isdir() or member.isreg() or member.issym() or member.islnk()) or member.sparse:
                    raise ValueError('only regular files, directories, and internal links are supported')
                if (type(member.uid) is not int or type(member.gid) is not int or
                        not 0 <= member.uid <= 0xffffffff or not 0 <= member.gid <= 0xffffffff or
                        not isinstance(member.mtime, (int, float)) or not math.isfinite(member.mtime) or
                        not 0 <= member.mtime <= 0xffffffff or not 0 <= member.mode <= 0o7777):
                    raise ValueError('unsupported archive ownership, mode, or time')
                if member.isreg():
                    if type(member.size) is not int or member.size < 0:
                        raise ValueError('invalid archive file size')
                    regular_bytes += member.size
                    if regular_bytes > maximum:
                        raise ValueError('root filesystem file data exceeds image size')
                if member.issym() or member.islnk():
                    links[name] = (_link_target(name, member.linkname, hard=member.islnk()), member.islnk())
                entries[name] = member
    except (tarfile.TarError, UnicodeError, OverflowError) as error:
        raise ValueError('invalid root filesystem tar archive') from error
    for name in entries:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in links for i in range(1, len(parts))):
            raise ValueError('archive entry traverses a link parent')
    for name, (target, hard) in links.items():
        if not hard:
            continue
        visited = {name}
        while target in links and links[target][1]:
            if target in visited:
                raise ValueError('cyclic archive hard links')
            visited.add(target)
            target = links[target][0]
        if target not in entries or not entries[target].isreg():
            raise ValueError('archive hard link must resolve to a regular archive member')
    if _snapshot(os.fstat(stream.fileno())) != _snapshot(initial):
        raise ValueError('tar archive changed during validation')
    stream.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    stream.seek(0)
    return {'sha256': digest.hexdigest(), 'entries': len(entries), 'regular_bytes': regular_bytes}


def validate_tar(tar_path, max_bytes=MAX_IMAGE_BYTES):
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise ValueError('invalid tar file-data budget')
    _, stream = _input(tar_path)
    with stream:
        return _validate_tar(stream, max_bytes)


def _tool(name):
    path = shutil.which(name)
    if not path:
        path = next((str(Path(prefix) / name) for prefix in ('/usr/sbin', '/sbin')
                     if (Path(prefix) / name).is_file()), None)
    if not path:
        raise RuntimeError('required existing image tool is unavailable: ' + name)
    return path


def _linux():
    if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() == 0:
        raise RuntimeError('image operations require a non-root Linux process')
    if not Path('/proc/self/fd').is_dir():
        raise RuntimeError('pinned image tools require Linux /proc/self/fd')


def _run(command, *, pass_fds=(), env=None, timeout=120):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                   pass_fds=pass_fds, env=env)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError('owned image tool exceeded its time budget')
                if max(os.fstat(output.fileno()).st_size, os.fstat(error.fileno()).st_size) > MAX_TOOL_OUTPUT:
                    raise RuntimeError('image tool output exceeded its limit')
                time.sleep(.02)
            if max(os.fstat(output.fileno()).st_size, os.fstat(error.fileno()).st_size) > MAX_TOOL_OUTPUT:
                raise RuntimeError('image tool output exceeded its limit')
            output.seek(0)
            error.seek(0)
            return {'command': [str(part) for part in command], 'returncode': process.returncode,
                    'stdout': output.read(MAX_TOOL_OUTPUT).decode('utf-8', 'replace'),
                    'stderr': error.read(MAX_TOOL_OUTPUT).decode('utf-8', 'replace')}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def _unchanged(path, stream, initial):
    current = path.lstat()
    if (not _plain(current, stat.S_ISREG) or _snapshot(current) != _snapshot(os.fstat(stream.fileno())) or
            _snapshot(current) != initial):
        raise RuntimeError('pinned filesystem input changed during image operation')


def _hash(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def create_ext4(tar_path, destination, *, size_mib, label, uuid, epoch=1700000000):
    """Create and verify one new regular ext4 image; never replace an entry."""
    if type(size_mib) is not int or not 32 <= size_mib <= 4096:
        raise ValueError('size_mib must be an integer from 32 to 4096')
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,16}', label):
        raise ValueError('label must contain 1 to 16 portable ASCII characters')
    try:
        if not isinstance(uuid, str) or str(uuid_module.UUID(uuid)) != uuid:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('uuid must be a canonical lowercase UUID') from None
    if type(epoch) is not int or not 0 <= epoch <= 0xffffffff:
        raise ValueError('epoch must be an unsigned 32-bit integer')
    destination, parent_before = _destination(destination)
    tar_path, source = _input(tar_path)
    with source:
        tar_info = _validate_tar(source, size_mib * 1024**2)
        source_before = _snapshot(os.fstat(source.fileno()))
        _linux()
        mkfs, fsck = _tool('mke2fs'), _tool('e2fsck')
        versions = {name: _run([tool, '-V'], timeout=10) for name, tool in (('mke2fs', mkfs), ('e2fsck', fsck))}
        config_path = destination.with_name(destination.name + '.mke2fs.conf')
        with _parent(destination.parent, parent_before) as parent_descriptor, \
                _new_file(config_path, parent_descriptor) as config, _new_file(destination, parent_descriptor) as image:
            config.write(CONFIG.encode('ascii'))
            config.flush()
            os.fsync(config.fileno())
            image.truncate(size_mib * 1024**2)
            image.flush()
            descriptors = (source.fileno(), image.fileno(), config.fileno())
            env = os.environ.copy()
            env.update({'E2FSPROGS_FAKE_TIME': str(epoch), 'SOURCE_DATE_EPOCH': str(epoch),
                        'MKE2FS_CONFIG': '/proc/self/fd/' + str(config.fileno()), 'LC_ALL': 'C', 'TZ': 'UTC'})
            command = [mkfs, '-q', '-F', '-t', 'ext4', '-b', '4096', '-I', '256', '-m', '0',
                       '-U', uuid, '-L', label, '-E',
                       f'root_owner=0:0,hash_seed={uuid},lazy_itable_init=0,lazy_journal_init=0',
                       '-d', '/proc/self/fd/' + str(source.fileno()), '/proc/self/fd/' + str(image.fileno())]
            created = _run(command, pass_fds=descriptors, env=env)
            if created['returncode']:
                raise RuntimeError('mke2fs failed; regular image/configuration preserved for diagnosis: ' + created['stderr'][:1000])
            os.fsync(image.fileno())
            checked = _run([fsck, '-f', '-n', '/proc/self/fd/' + str(image.fileno())], pass_fds=(image.fileno(),), env=env)
            if checked['returncode'] != 0:
                raise RuntimeError('read-only e2fsck did not verify the new image; image preserved')
            _unchanged(tar_path, source, source_before)
            current = destination.lstat()
            if (not _plain(current, stat.S_ISREG) or
                    (current.st_dev, current.st_ino) != (os.fstat(image.fileno()).st_dev, os.fstat(image.fileno()).st_ino) or
                    current.st_size != size_mib * 1024**2 or
                    destination.parent.resolve(strict=True) != destination.parent or
                    _snapshot(destination.parent.lstat())[:2] != parent_before[:2]):
                raise RuntimeError('image output path changed during creation')
            return {'format': 'hf-ext4-image-v1', 'sha256': _hash(image), 'size_bytes': current.st_size,
                    'destination': str(destination), 'command': command, 'tool_versions': versions,
                    'tar_sha256': tar_info['sha256'], 'tar_entries': tar_info['entries'],
                    'uuid': uuid, 'label': label, 'epoch': epoch, 'non_root_uid': os.geteuid(),
                    'configuration': str(config_path), 'config_sha256': hashlib.sha256(CONFIG.encode()).hexdigest(),
                    'mke2fs': created, 'e2fsck': checked, 'mounted': False}


def extract_file(image, guest_absolute_path, destination, max_bytes=MAX_EXTRACT_BYTES):
    """Read one regular guest file with debugfs into a new pinned host file."""
    if (not isinstance(guest_absolute_path, str) or
            not re.fullmatch(r'/[A-Za-z0-9._/-]+', guest_absolute_path) or
            guest_absolute_path.endswith('/') or '//' in guest_absolute_path or
            any(part in ('.', '..') for part in guest_absolute_path.split('/')[1:])):
        raise ValueError('guest path must be a canonical portable absolute file path')
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_EXTRACT_BYTES:
        raise ValueError('max_bytes must be within the bounded extraction limit')
    destination, parent_before = _destination(destination)
    image_path, source = _input(image)
    with source:
        initial = _snapshot(os.fstat(source.fileno()))
        if not 32 * 1024**2 <= initial[2] <= MAX_IMAGE_BYTES:
            raise ValueError('unsupported image size')
        _linux()
        debugfs = _tool('debugfs')
        source_name = '/proc/self/fd/' + str(source.fileno())
        info = _run([debugfs, '-R', 'stat ' + guest_absolute_path, source_name], pass_fds=(source.fileno(),), timeout=30)
        size = re.search(r'\bSize: (\d+)', info['stdout'])
        if (info['returncode'] or not re.search(r'^Inode:\s+\d+\s+Type:\s+regular\b', info['stdout'], re.M) or
                size is None or int(size[1]) > max_bytes):
            raise ValueError('guest path is not a bounded regular file')
        expected = int(size[1])
        with _parent(destination.parent, parent_before) as parent_descriptor, _new_file(destination, parent_descriptor) as output:
            result = _run([debugfs, '-R', 'dump ' + guest_absolute_path + ' /proc/self/fd/' + str(output.fileno()), source_name],
                          pass_fds=(source.fileno(), output.fileno()), timeout=30)
            output.flush()
            os.fsync(output.fileno())
            _unchanged(image_path, source, initial)
            current = destination.lstat()
            opened = os.fstat(output.fileno())
            if (result['returncode'] or opened.st_size != expected or not _plain(current, stat.S_ISREG) or
                    (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or
                    _snapshot(destination.parent.lstat())[:2] != parent_before[:2]):
                raise RuntimeError('debugfs extraction failed; new output preserved for diagnosis')
            return {'format': 'hf-image-extract-v1', 'guest_path': guest_absolute_path,
                    'destination': str(destination), 'size_bytes': expected, 'sha256': _hash(output),
                    'command': result['command'], 'debugfs': result, 'mounted': False}


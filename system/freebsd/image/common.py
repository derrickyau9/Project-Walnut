"""Pure validation helpers for create-only DaOS FreeBSD appliance images."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess

MACHINES = {'arm64': 183, 'amd64': 62}
HOST_ARCH = {'arm64': 'arm64', 'aarch64': 'arm64', 'amd64': 'amd64', 'x86_64': 'amd64'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('invalid relative image path')
    path = PurePosixPath(value.removeprefix('./'))
    if path.is_absolute() or '..' in path.parts or value.startswith('/') or not path.parts:
        raise ValueError('image path leaves its private tree')
    return path.as_posix()


def require_freebsd(architecture=None):
    if (platform.system() != 'FreeBSD' or not hasattr(os, 'uname')
            or os.uname().sysname != 'FreeBSD'):
        raise ValueError('actual native FreeBSD is required; no Linux/Windows fallback')
    actual = HOST_ARCH.get(platform.machine().lower())
    if actual is None or architecture is not None and actual != architecture:
        raise ValueError('runtime collection requires the actual selected FreeBSD architecture')
    jailed = subprocess.check_output(['/sbin/sysctl', '-n', 'security.jail.jailed'], text=True, timeout=5).strip()
    if jailed != '0':
        raise ValueError('a native FreeBSD guest is required, not a compatibility jail')
    return actual


def new_path(value, prefix):
    path = Path(value).absolute()
    if '..' in path.parts or not path.name.startswith(prefix) or len(path.parts) < 3 or os.path.lexists(path):
        raise ValueError('use a new dedicated ' + prefix + '* directory')
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError('linked destination ancestors are refused')
    parent = path.parent.resolve(strict=True)
    path = parent / path.name
    if not parent.is_dir() or parent.stat().st_uid not in {os.geteuid() if hasattr(os, 'geteuid') else 0, 0}:
        raise ValueError('destination parent must be an owned directory')
    forbidden = ('/etc', '/boot', '/bin', '/sbin', '/lib', '/libexec', '/dev', '/usr', '/proc', '/sys')
    if os.name == 'posix' and any(path == Path(item) or Path(item) in path.parents for item in forbidden):
        raise ValueError('destination is inside an installed system tree')
    return parent / path.name


def no_link_parents(path):
    for parent in Path(path).absolute().parents:
        if parent.is_symlink() or hasattr(parent, 'is_junction') and parent.is_junction():
            raise ValueError('a filesystem parent is redirected')


def confined(root, rel, *, allow_leaf_link=False):
    root = Path(root).absolute()
    no_link_parents(root)
    if root.is_symlink(): raise ValueError('private root is redirected')
    if rel == '.': return root
    rel = relative(rel)
    target = root / rel
    no_link_parents(target)
    if not allow_leaf_link and target.is_symlink(): raise ValueError('private destination is redirected')
    return target


def validate_tree(entries):
    for name in entries:
        if name == '.': continue
        relative(name)
        for parent in Path(name).parents:
            key=parent.as_posix()
            if key in entries and entries[key]['type'] != 'dir':
                raise ValueError('manifest entry descends through a non-directory/link')


def elf(path, architecture, *, kernel=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('native input must be a regular non-linked ELF')
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:6] != b'\x7fELF\x02\x01' or header[7] not in ((0, 9) if kernel else (9,))
            or int.from_bytes(header[18:20], 'little') != MACHINES[architecture]):
        raise ValueError('native FreeBSD ELF architecture/OSABI mismatch')
    return {'machine': MACHINES[architecture], 'osabi': header[7], 'sha256': sha(path)}


def pe(path, architecture):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('EFI loader lacks a PE DOS header')
        offset = int.from_bytes(header[60:64], 'little')
        if not 64 <= offset <= 1024 * 1024:
            raise ValueError('EFI PE offset is outside bounds')
        stream.seek(offset)
        signature = stream.read(6)
    expected = 0xaa64 if architecture == 'arm64' else 0x8664
    if signature[:4] != b'PE\0\0' or int.from_bytes(signature[4:], 'little') != expected:
        raise ValueError('EFI loader architecture differs from the image target')
    return {'pe_machine': expected, 'sha256': sha(path)}


def stable_copy(source, destination):
    """Copy one regular file without following a leaf symlink or a changed file."""
    source, destination = Path(source), Path(destination)
    no_link_parents(source); no_link_parents(destination)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024**2:
        raise ValueError('source is not a bounded regular file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(source, flags)
    value = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    no_link_parents(destination)
    def identity(item):
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns
    try:
        if identity(info) != identity(os.fstat(descriptor)):
            raise ValueError('source changed before copying')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream, destination.open('xb') as target:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                target.write(block); value.update(block)
            target.flush(); os.fsync(target.fileno())
        if identity(info) != identity(os.fstat(descriptor)) or identity(info) != identity(source.lstat()):
            raise ValueError('source changed while copying')
    finally:
        os.close(descriptor)
    return value.hexdigest()

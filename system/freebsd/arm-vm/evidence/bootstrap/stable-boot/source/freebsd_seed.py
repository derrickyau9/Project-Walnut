"""Create a small ISO9660/Rock Ridge NoCloud seed using only the stdlib.

The image has volume label CIDATA and exactly two regular files, with Rock
Ridge NM entries preserving the names ``meta-data`` and ``user-data``.  It is
data media, not a boot image.  No mount, external ISO tool or guest execution
is needed.  ISO9660 fields follow ECMA-119; the SUSP/RRIP fields are restricted
to SP, ER, RR, PX, NM and TF (no links or continuation areas).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import struct


SECTOR_BYTES = 2048
MAX_FILE_BYTES = 1024 * 1024
_ROOT_SECTOR = 20
_DATE = bytes((124, 1, 1, 0, 0, 0, 0))  # 2024-01-01 UTC, reproducible.
_LONG_DATE = b'2024010100000000\x00'


def _both16(value):
    return struct.pack('<H', value) + struct.pack('>H', value)


def _both32(value):
    return struct.pack('<I', value) + struct.pack('>I', value)


def _susp(signature, payload=b''):
    return signature + bytes((4 + len(payload), 1)) + payload


def _rr(*, directory=False, name=None, root=False):
    result = b''
    if root:
        result += _susp(b'SP', b'\xbe\xef\x00')
        identifier = b'RRIP_1991A'
        result += _susp(b'ER', bytes((len(identifier), 0, 0, 1)) + identifier)
    flags = 0x81 | (0x08 if name is not None else 0)  # PX, TF, optional NM.
    result += _susp(b'RR', bytes((flags,)))
    mode = (stat.S_IFDIR | 0o555) if directory else (stat.S_IFREG | 0o444)
    result += _susp(b'PX', _both32(mode) + _both32(2 if directory else 1) + _both32(0) + _both32(0))
    if name is not None:
        result += _susp(b'NM', b'\x00' + name.encode('ascii'))
    result += _susp(b'TF', b'\x02' + _DATE)  # Short-format modification time.
    # The directory record's length already terminates the System Use area.
    # Omitting the optional ST also preserves readers that treat root ST as
    # disabling SUSP for subsequent directory entries.
    return result


def _record(identifier, sector, size, *, directory=False, system_use=b''):
    # A directory record's identifier is followed by a padding byte when its
    # length is even, putting the System Use area at an even offset.
    length = 33 + len(identifier) + (not len(identifier) % 2) + len(system_use)
    length += length % 2
    record = bytearray(length)
    record[0] = length
    record[2:10] = _both32(sector)
    record[10:18] = _both32(size)
    record[18:25] = _DATE
    record[25] = 2 if directory else 0
    record[28:32] = _both16(1)
    record[32] = len(identifier)
    record[33:33 + len(identifier)] = identifier
    offset = 33 + len(identifier) + (not len(identifier) % 2)
    record[offset:offset + len(system_use)] = system_use
    return bytes(record)


def _image(user_data, meta_data):
    files = [('meta-data', b'META_DAT.;1', meta_data), ('user-data', b'USER_DAT.;1', user_data)]
    cursor = _ROOT_SECTOR + 1
    extents = []
    for name, identifier, payload in files:
        extents.append((name, identifier, payload, cursor))
        # Even empty files get a distinct valid extent within the volume.
        cursor += max(1, (len(payload) + SECTOR_BYTES - 1) // SECTOR_BYTES)
    sectors = max(32, cursor)
    image = bytearray(sectors * SECTOR_BYTES)
    primary = memoryview(image)[16 * SECTOR_BYTES:17 * SECTOR_BYTES]
    primary[0:7] = b'\x01CD001\x01'
    primary[8:40] = b'PYTHON'.ljust(32, b' ')
    primary[40:72] = b'CIDATA'.ljust(32, b' ')
    primary[80:88] = _both32(sectors)
    primary[120:124] = _both16(1)
    primary[124:128] = _both16(1)
    primary[128:132] = _both16(SECTOR_BYTES)
    primary[132:140] = _both32(10)
    primary[140:144] = struct.pack('<I', 18)
    primary[148:152] = struct.pack('>I', 19)
    primary[156:190] = _record(b'\x00', _ROOT_SECTOR, SECTOR_BYTES, directory=True)
    primary[190:813] = b' ' * (813 - 190)
    primary[574:702] = b'HASSLEFREE NOCLOUD SEED'.ljust(128, b' ')
    primary[813:830] = _LONG_DATE
    primary[830:847] = _LONG_DATE
    primary[847:864] = b'0' * 16 + b'\x00'
    primary[864:881] = _LONG_DATE
    primary[881] = 1
    image[17 * SECTOR_BYTES:17 * SECTOR_BYTES + 7] = b'\xffCD001\x01'
    for sector, order in ((18, '<'), (19, '>')):
        table = b'\x01\x00' + struct.pack(order + 'I', _ROOT_SECTOR) + struct.pack(order + 'H', 1) + b'\x00\x00'
        image[sector * SECTOR_BYTES:sector * SECTOR_BYTES + len(table)] = table
    records = [
        _record(b'\x00', _ROOT_SECTOR, SECTOR_BYTES, directory=True, system_use=_rr(directory=True, root=True)),
        _record(b'\x01', _ROOT_SECTOR, SECTOR_BYTES, directory=True, system_use=_rr(directory=True)),
    ]
    for name, identifier, payload, sector in extents:
        records.append(_record(identifier, sector, len(payload), system_use=_rr(name=name)))
        start = sector * SECTOR_BYTES
        image[start:start + len(payload)] = payload
    directory = b''.join(records)
    if len(directory) > SECTOR_BYTES:
        raise RuntimeError('fixed seed directory exceeded its sector')
    image[_ROOT_SECTOR * SECTOR_BYTES:_ROOT_SECTOR * SECTOR_BYTES + len(directory)] = directory
    return bytes(image)


def _plain(info, predicate):
    return predicate(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _identity(info):
    return info.st_dev, info.st_ino


def create_seed(destination, *, user_data: bytes, meta_data: bytes):
    """Write NEW regular seed media; return hashes of the actual durable bytes.

    Each payload must be bytes of at most 1 MiB.  The destination's parent must
    already exist at its canonical path. Existing entries are never replaced,
    and incomplete new outputs are retained on failure for diagnosis.
    """
    for payload in (user_data, meta_data):
        if type(payload) is not bytes or len(payload) > MAX_FILE_BYTES:
            raise ValueError('NoCloud payloads must be bytes of at most 1 MiB each')
    path = Path(os.path.abspath(os.fspath(destination)))
    parent = path.parent
    parent_info = parent.lstat()
    if not _plain(parent_info, stat.S_ISDIR) or parent.resolve(strict=True) != parent:
        raise ValueError('seed destination parent must be an existing canonical directory')
    if os.path.lexists(path):
        raise ValueError('seed destination must be NEW')
    data = _image(user_data, meta_data)
    expected_hash = hashlib.sha256(data).hexdigest()
    directory_fd = None
    descriptor = None
    try:
        if os.open in os.supports_dir_fd:
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            opened_parent = os.fstat(directory_fd)
            if (not _plain(opened_parent, stat.S_ISDIR) or _identity(opened_parent) != _identity(parent_info)
                    or parent.resolve(strict=True) != parent):
                raise ValueError('seed destination parent changed during open')
        descriptor = os.open(path.name if directory_fd is not None else path,
                             os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                             0o600, dir_fd=directory_fd)
        with os.fdopen(descriptor, 'w+b') as output:
            descriptor = None
            if not _plain(os.fstat(output.fileno()), stat.S_ISREG):
                raise ValueError('seed output must be a regular file')
            if output.write(data) != len(data):
                raise OSError('incomplete seed write')
            output.flush()
            os.fsync(output.fileno())
            written = os.fstat(output.fileno())
            output.seek(0)
            actual_hash = hashlib.sha256(output.read(len(data) + 1)).hexdigest()
            after = os.fstat(output.fileno())
            current = path.lstat()
            if (actual_hash != expected_hash or after.st_size != len(data)
                    or (after.st_mtime_ns, after.st_ctime_ns) != (written.st_mtime_ns, written.st_ctime_ns)
                    or not _plain(current, stat.S_ISREG) or _identity(current) != _identity(after)
                    or parent.resolve(strict=True) != parent or _identity(parent.lstat()) != _identity(parent_info)):
                raise RuntimeError('seed output changed while it was being written')
            if directory_fd is not None:
                os.fsync(directory_fd)
        return {'format': 'hf-nocloud-iso9660-v1', 'destination': str(path), 'size_bytes': len(data),
                'sha256': actual_hash, 'volume_id': 'CIDATA',
                'files': {name: {'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                          for name, payload in (('meta-data', meta_data), ('user-data', user_data))}}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)

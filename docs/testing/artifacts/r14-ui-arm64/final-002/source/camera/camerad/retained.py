"""Verified reads and local exports of committed synthetic capture files.

The journal establishes completion. A file is then opened by its canonical
request ID beneath the authority's photos directory, pinned, and inspected.
Recorded file_path values are never followed. Thumbnails use those retained
samples and a simple uncalibrated linear RGGB rendering.
"""
from __future__ import annotations

import bisect
import math
import os
from pathlib import Path
import stat
import struct
import tempfile
import uuid

from camera.dng.inspect import inspect_dng
from camera.pipeline.preview import _png


MAX_DNG_BYTES = 6064 * 4040 * 2 + 65536
MAX_THUMB_BYTES = 256 * 1024
_MODES = {'sim-small': (128, 96), 'sim-full': (6064, 4040)}
_REPARSE_POINT = 0x400


def _error(code, message):
    # service.py imports these helpers lazily as well, keeping one error type
    # without making module initialization depend on an import cycle.
    from camera.camerad.service import CameraError
    return CameraError(code, message)


def canonical_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise _error('invalid_request', 'request_id must be a lowercase canonical UUID') from None
    return value


def _complete(record):
    if record is None:
        raise _error('not_found', 'capture request not found')
    if not isinstance(record, dict):
        raise _error('capture_unavailable', 'capture metadata is unavailable')
    if record.get('status') != 'complete':
        raise _error('capture_not_complete', 'only a completed capture can be opened')
    request_id = canonical_id(record.get('request_id'))
    if record.get('capture_id') != request_id or record.get('frame_id') != request_id:
        raise _error('capture_unavailable', 'capture identity is inconsistent')
    return request_id


def dimensions_for_record(record):
    _complete(record)
    dimensions = _MODES.get(record.get('sensor_mode')) if isinstance(record.get('sensor_mode'), str) else None
    if dimensions is None:
        raise _error('capture_unavailable', 'capture sensor mode is unavailable')
    return dimensions


def _positive_finite(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and lower <= value <= upper
    except OverflowError:
        return False


def summary(record, width, height):
    request_id = _complete(record)
    if (type(width) is not int or type(height) is not int or
            (width, height) != dimensions_for_record(record)):
        raise _error('capture_unavailable', 'capture dimensions are inconsistent')
    timestamp = record.get('timestamp')
    if not _positive_finite(timestamp, 0, 1e20):
        raise _error('capture_unavailable', 'capture timestamp is unavailable')
    for key in ('requested_exposure_us', 'actual_exposure_us'):
        if type(record.get(key)) is not int or not 100 <= record[key] <= 30000000:
            raise _error('capture_unavailable', 'capture exposure metadata is unavailable')
    for key in ('requested_analogue_gain', 'actual_analogue_gain'):
        if not _positive_finite(record.get(key), 1, 16):
            raise _error('capture_unavailable', 'capture gain metadata is unavailable')
    for key in ('sensor_id', 'calibration_id'):
        value = record.get(key)
        if (not isinstance(value, str) or not value or len(value) > 256 or '\0' in value or
                any(0xd800 <= ord(character) <= 0xdfff for character in value)):
            raise _error('capture_unavailable', 'capture provenance is unavailable')
    fields = ('request_id', 'capture_id', 'frame_id', 'timestamp', 'sensor_mode',
              'requested_exposure_us', 'actual_exposure_us', 'requested_analogue_gain',
              'actual_analogue_gain', 'sensor_id', 'calibration_id')
    result = {key: record[key] for key in fields}
    result.update(status='complete', width=width, height=height, bit_depth=14,
                  cfa='RGGB', simulated=True, filename=request_id + '.dng')
    return result


def _not_reparse(info):
    return not getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT


def _regular(info):
    return stat.S_ISREG(info.st_mode) and _not_reparse(info)


def _directory(info):
    return stat.S_ISDIR(info.st_mode) and _not_reparse(info)


def _identity(info):
    return info.st_dev, info.st_ino


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _open_windows(path):
    """Open the final entry itself and retain a read-only, non-delete share."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    final_name = kernel.GetFinalPathNameByHandleW
    final_name.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_name.restype = wintypes.DWORD
    # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        length = final_name(handle, None, 0, 0)
        if not length or length > 32768:
            raise OSError('retained path cannot be resolved')
        buffer = ctypes.create_unicode_buffer(length + 1)
        count = final_name(handle, buffer, length + 1, 0)
        if not count or count > length:
            raise OSError('retained path cannot be resolved')
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        if Path(value) != path:
            raise OSError('retained file is outside its canonical path')
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        transferred = True
        try:
            return os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise
    finally:
        if not transferred:
            close(handle)


def _open_posix(root, photos, filename):
    root_descriptor = photos_descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        root_descriptor = os.open(root, flags)
        if not _directory(os.fstat(root_descriptor)):
            raise OSError('retained root is not a directory')
        photos_descriptor = os.open('photos', flags, dir_fd=root_descriptor)
        if not _directory(os.fstat(photos_descriptor)):
            raise OSError('retained photos is not a directory')
        if (_identity(root.lstat()) != _identity(os.fstat(root_descriptor)) or
                _identity(photos.lstat()) != _identity(os.fstat(photos_descriptor))):
            raise OSError('retained directory changed during open')
        descriptor = os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                             getattr(os, 'O_NONBLOCK', 0), dir_fd=photos_descriptor)
        try:
            return os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise
    finally:
        if photos_descriptor is not None:
            os.close(photos_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def open_retained(root, record, asset=None):
    """Return an owned pinned binary handle at offset zero after verification.

    The caller closes the returned handle. No path supplied in a record or an
    asset descriptor is opened, and no capture is replayed to obtain a file.
    """
    request_id = _complete(record)
    dimensions = dimensions_for_record(record)
    filename = request_id + '.dng'
    if asset is not None and (not isinstance(asset, dict) or asset.get('request_id') != request_id or
                              asset.get('filename') != filename or asset.get('media_type') != 'image/x-adobe-dng' or
                              type(asset.get('byte_count')) is not int or not 8 <= asset['byte_count'] <= MAX_DNG_BYTES):
        raise _error('capture_unavailable', 'capture asset metadata is inconsistent')
    stream = None
    try:
        root = Path(os.path.abspath(os.fspath(root)))
        photos = root / 'photos'
        path = photos / filename
        root_before, photos_before, file_before = root.lstat(), photos.lstat(), path.lstat()
        if (not _directory(root_before) or not _directory(photos_before) or not _regular(file_before) or
                root.resolve(strict=True) != root or photos.resolve(strict=True) != photos or
                path.resolve(strict=True) != path or not 8 <= file_before.st_size <= MAX_DNG_BYTES):
            raise OSError('capture path is not a canonical regular retained file')
        stream = _open_windows(path) if os.name == 'nt' else _open_posix(root, photos, filename)
        initial = os.fstat(stream.fileno())
        if (not _regular(initial) or _identity(initial) != _identity(file_before) or
                not dimensions[0] * dimensions[1] * 2 < initial.st_size <= dimensions[0] * dimensions[1] * 2 + 65536 or
                asset is not None and initial.st_size != asset['byte_count']):
            raise OSError('capture file identity or size changed')
        structure = inspect_dng(stream)
        if ((structure.get('width'), structure.get('height')) != dimensions or
                structure.get('bits_per_sample') != 16 or structure.get('cfa') != 'RGGB' or
                structure.get('black_level') != 64 or structure.get('white_level') != 16383 or
                structure.get('metadata_state') != 'valid' or structure.get('request_id') != request_id):
            raise ValueError('retained DNG does not match its committed capture')
        if (_snapshot(initial) != _snapshot(os.fstat(stream.fileno())) or
                _identity(root_before) != _identity(root.lstat()) or
                _identity(photos_before) != _identity(photos.lstat()) or
                _identity(initial) != _identity(path.lstat()) or
                not _directory(root.lstat()) or not _directory(photos.lstat()) or not _regular(path.lstat())):
            raise OSError('retained file or directory changed during verification')
        stream.seek(0)
        stream.retained_structure = structure
        stream.retained_snapshot = _snapshot(initial)
        return stream
    except (OSError, ValueError, TypeError, OverflowError) as error:
        if stream is not None:
            stream.close()
        raise _error('capture_unavailable', 'completed capture file is missing, changed, or invalid') from error


def asset_metadata(root, record):
    request_id = _complete(record)
    with open_retained(root, record) as stream:
        return {'request_id': request_id, 'filename': request_id + '.dng',
                'byte_count': os.fstat(stream.fileno()).st_size, 'media_type': 'image/x-adobe-dng'}


def thumbnail_retained(root, record, asset=None):
    """Render at most 320x240 pixels from actual retained RGGB sample blocks."""
    with open_retained(root, record, asset) as stream:
        structure = stream.retained_structure
        width, height = structure['width'], structure['height']
        block_width, block_height = width // 2, height // 2
        scale = min(1.0, 320 / block_width, 240 / block_height)
        output_width = max(1, int(block_width * scale))
        output_height = max(1, int(block_height * scale))
        row_bytes = width * 2
        starts, strips = [], []
        first_row = 0
        for offset, byte_count in zip(structure['strip_offsets'], structure['strip_byte_counts']):
            rows = byte_count // row_bytes
            starts.append(first_row)
            strips.append((first_row, rows, offset))
            first_row += rows
        if first_row != height:
            raise _error('capture_unavailable', 'retained sample layout is invalid')
        endian = '<' if stream.read(2) == b'II' else '>'
        pixels = bytearray()
        def row(number):
            first, count, offset = strips[bisect.bisect_right(starts, number) - 1]
            if not first <= number < first + count:
                raise _error('capture_unavailable', 'retained sample row is invalid')
            stream.seek(offset + (number - first) * row_bytes)
            value = stream.read(row_bytes)
            if len(value) != row_bytes:
                raise _error('capture_unavailable', 'retained samples were truncated')
            return value
        def channel(value):
            return max(0, min(255, (value - 64) * 255 // (16383 - 64)))
        for y in range(output_height):
            site_y = 2 * min(block_height - 1, y * block_height // output_height)
            upper, lower = row(site_y), row(site_y + 1)
            for x in range(output_width):
                site_x = 2 * min(block_width - 1, x * block_width // output_width)
                red, green_one = struct.unpack_from(endian + 'HH', upper, site_x * 2)
                green_two, blue = struct.unpack_from(endian + 'HH', lower, site_x * 2)
                if max(red, green_one, green_two, blue) > 16383:
                    raise _error('capture_unavailable', 'retained sample exceeds synthetic white level')
                pixels.extend((channel(red), channel((green_one + green_two) // 2), channel(blue)))
        if _snapshot(os.fstat(stream.fileno())) != stream.retained_snapshot:
            raise _error('capture_unavailable', 'retained samples changed during rendering')
        result = _png(bytes(pixels), output_width, output_height)
        if len(result) > MAX_THUMB_BYTES:
            raise _error('capture_unavailable', 'retained thumbnail exceeds size limit')
        return result


def _verified_stream(source, request_id):
    """Validate a caller-owned, regular DNG stream without closing it."""
    try:
        initial = os.fstat(source.fileno())
        if not _regular(initial) or not 8 <= initial.st_size <= MAX_DNG_BYTES:
            raise ValueError('capture stream is not a bounded regular file')
        source.seek(0)
        structure = inspect_dng(source)
        dimensions = structure.get('width'), structure.get('height')
        if (dimensions not in _MODES.values() or
                structure.get('bits_per_sample') != 16 or structure.get('cfa') != 'RGGB' or
                structure.get('black_level') != 64 or structure.get('white_level') != 16383 or
                structure.get('metadata_state') != 'valid' or structure.get('request_id') != request_id or
                not dimensions[0] * dimensions[1] * 2 < initial.st_size <= dimensions[0] * dimensions[1] * 2 + 65536 or
                _snapshot(initial) != _snapshot(os.fstat(source.fileno()))):
            raise ValueError('capture stream does not match its capture identity or mode')
        source.seek(0)
        return _snapshot(initial)
    except (AttributeError, OSError, ValueError, TypeError, OverflowError) as error:
        raise _error('capture_unavailable', 'capture stream is changed or invalid') from error


def export_stream(source, destination, request_id):
    """Publish a verified caller-owned stream to a new caller-local file.

    This is also the export path for a completed bounded HTTP download. The
    source must be a regular seekable file, stays open, and is rewound after use.
    """
    from camera.camerad.service import _publish_capture_file
    request_id = canonical_id(request_id)
    snapshot = _verified_stream(source, request_id)
    byte_count = snapshot[2]
    temporary = None
    try:
        destination = Path(os.path.abspath(os.fspath(destination)))
        parent = destination.parent.resolve(strict=True)
        if not parent.is_dir() or not destination.name or destination.name in ('.', '..'):
            raise ValueError('invalid destination')
        destination = parent / destination.name
        if os.path.lexists(destination):
            raise _error('export_conflict', 'export destination already exists')
        descriptor, name = tempfile.mkstemp(prefix='.hf-export-', suffix='.partial', dir=parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as output:
            remaining = byte_count
            while remaining:
                try:
                    block = source.read(min(65536, remaining))
                except (OSError, ValueError, TypeError) as error:
                    raise _error('capture_unavailable', 'capture stream could not be read') from error
                if not isinstance(block, bytes) or not block or len(block) > remaining:
                    raise _error('capture_unavailable', 'capture stream changed during export')
                if output.write(block) != len(block):
                    raise OSError('short export write')
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
        try:
            unchanged = _snapshot(os.fstat(source.fileno())) == snapshot
        except (OSError, ValueError, TypeError) as error:
            raise _error('capture_unavailable', 'capture stream changed during export') from error
        if not unchanged:
            raise _error('capture_unavailable', 'capture stream changed during export')
        _publish_capture_file(temporary, destination)
        temporary = None
        return {'request_id': request_id, 'filename': request_id + '.dng',
                'byte_count': byte_count, 'destination': str(destination)}
    except (OSError, ValueError, TypeError) as error:
        conflict = isinstance(error, FileExistsError) or getattr(error, 'winerror', None) in (80, 183)
        raise _error('export_conflict' if conflict else 'export_unavailable',
                     'export destination already exists' if conflict else 'capture export could not be completed') from error
    finally:
        try:
            source.seek(0)
        except (OSError, ValueError, TypeError):
            pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def export_retained(root, record, destination, asset=None):
    """Copy one completed retained capture to a new caller-local file."""
    request_id = _complete(record)
    with open_retained(root, record, asset) as source:
        return export_stream(source, destination, request_id)

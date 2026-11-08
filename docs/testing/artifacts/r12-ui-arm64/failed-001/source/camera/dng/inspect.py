"""Bounded structural inspection for the direct CFA DNG files used by HF-Sim.

This is deliberately narrower than a TIFF/DNG decoder: one classic TIFF image,
16-bit unsigned uncompressed rectangular RGGB samples, and optional Exif/GPS metadata IFDs.
Linearization tables and staggered mosaics require a different renderer and are unsupported.
It reads metadata only, never image samples, and makes no image-quality or
hardware-calibration claim. Missing optional black/white levels use TIFF/DNG
container defaults (0 and 65535).
"""
from __future__ import annotations

import json
import math
import os
from contextlib import nullcontext
from pathlib import Path
import stat
import struct
import uuid

_MAX_IFD_ENTRIES = 1024
_MAX_DIRECTORIES = 16
_MAX_FIELD_BYTES = 65536
_MAX_METADATA_BYTES = 1024 * 1024
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1,
               8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_SELECTED = {256, 257, 258, 259, 262, 270, 273, 274, 277, 278, 279,
             284, 33421, 33422, 339, 50706, 50707, 50710, 50711, 50713, 50714, 50717}
_POINTERS = {34665, 34853, 40965}
_NAMESPACE = 'org.hasslefree.hf-sim.capture.v1'


def _require(condition):
    if not condition:
        raise ValueError('invalid or unsupported classic CFA DNG')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate key')
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError('non-finite JSON')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite JSON')
    return result


def _metadata(raw):
    if raw is None:
        return None, 'missing'
    try:
        value = json.loads(raw.rstrip(b'\0').decode('ascii'),
                           object_pairs_hook=_unique_object,
                           parse_float=_finite_float,
                           parse_constant=_reject_constant)
        if (type(value) is not dict or value.get('namespace') != _NAMESPACE
                or value.get('simulated') is not True
                or type(value.get('capture')) is not dict):
            return None, 'invalid'
        rid = value['capture'].get('request_id')
        if rid is None:
            return None, 'valid'
        if not isinstance(rid, str) or str(uuid.UUID(rid)) != rid:
            return None, 'invalid'
        return rid, 'valid'
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        return None, 'invalid'


def inspect_dng(path):
    """Return allowlisted structure, or raise ValueError/OSError.

    ``path`` may also be a caller-owned, seekable binary file with ``fileno``.
    Such a file stays open; inspection changes its position. ValueError covers
    invalid or unsupported structures; OSError covers filesystem
    failures. All field allocations, IFD traversal and metadata reads are bounded.
    ImageDescription is interpreted only as HF-Sim's namespaced capture envelope;
    arbitrary metadata and exception messages are never returned.
    """
    info = None
    if hasattr(path, 'read'):
        context = nullcontext(path)
    else:
        path = Path(path)
        info = path.lstat()
        _require(stat.S_ISREG(info.st_mode)
                 and not getattr(info, 'st_file_attributes', 0) & 0x400)
        context = path.open('rb')
    with context as stream:
        initial = os.fstat(stream.fileno())
        _require(stat.S_ISREG(initial.st_mode)
                 and not getattr(initial, 'st_file_attributes', 0) & 0x400)
        if info is not None:
            _require((info.st_dev, info.st_ino) == (initial.st_dev, initial.st_ino))
        size = initial.st_size
        budget = 0
        tables = [(0, 8)]
        values = []
        visited = set()

        def read_at(offset, count):
            nonlocal budget
            _require(type(offset) is int and type(count) is int
                     and offset >= 0 and count >= 0 and offset + count <= size)
            budget += count
            _require(budget <= _MAX_METADATA_BYTES)
            stream.seek(offset)
            raw = stream.read(count)
            _require(len(raw) == count)
            return raw

        header = read_at(0, 8)
        endian = {b'II': '<', b'MM': '>'}.get(header[:2])
        _require(endian is not None)
        _require(struct.unpack(endian + 'H', header[2:4])[0] == 42)
        first = struct.unpack(endian + 'I', header[4:])[0]

        def directory(offset, primary=False):
            _require(offset >= 8 and offset not in visited
                     and len(visited) < _MAX_DIRECTORIES)
            visited.add(offset)
            count = struct.unpack(endian + 'H', read_at(offset, 2))[0]
            _require(1 <= count <= _MAX_IFD_ENTRIES)
            end = offset + 2 + count * 12 + 4
            _require(end <= size)
            tables.append((offset, end))
            raw = read_at(offset + 2, count * 12 + 4)
            tags, pointers, seen = {}, [], set()
            for index in range(count):
                entry = raw[index * 12:(index + 1) * 12]
                tag, kind, length = struct.unpack(endian + 'HHI', entry[:8])
                _require(tag not in seen and kind in _TYPE_SIZES and length > 0)
                seen.add(tag)
                total = _TYPE_SIZES[kind] * length
                _require(total <= _MAX_FIELD_BYTES)
                payload = None
                if total > 4:
                    location = struct.unpack(endian + 'I', entry[8:])[0]
                    _require(location >= 8 and location + total <= size)
                    values.append((location, location + total))
                else:
                    location = None
                # A direct raw IFD is supported; thumbnail/SubIFD image trees
                # need a full DNG decoder and are rejected rather than guessed.
                _require(tag != 330)
                # Stored samples with a lookup table are not the linear values
                # consumed by our retained-DNG display conversion.
                _require(not primary or tag != 50712)
                if tag in _POINTERS:
                    _require(kind in (4, 13) and length == 1)
                    pointers.append(struct.unpack(endian + 'I', entry[8:])[0])
                if primary and tag in _SELECTED:
                    payload = entry[8:8 + total] if location is None else read_at(location, total)
                    tags[tag] = (kind, length, payload)
            _require(struct.unpack(endian + 'I', raw[count * 12:])[0] == 0)
            for pointer in pointers:
                directory(pointer)
            return tags

        tags = directory(first, primary=True)

        def integers(tag, *, optional=None):
            if tag not in tags:
                _require(optional is not None)
                return optional
            kind, length, raw = tags[tag]
            _require(kind in (1, 3, 4))
            return list(struct.unpack(endian + {1: 'B', 3: 'H', 4: 'I'}[kind] * length, raw))

        def scalar(tag, *, optional=None):
            data = integers(tag, optional=None if optional is None else [optional])
            _require(len(data) == 1)
            return data[0]

        def bytes_tag(tag):
            _require(tag in tags and tags[tag][0] == 1)
            return tags[tag][2]

        def level(tag, default):
            if tag not in tags:
                return default
            kind, length, raw = tags[tag]
            _require(length == 1)
            if kind == 5:
                numerator, denominator = struct.unpack(endian + 'II', raw)
                _require(denominator != 0)
                result = numerator / denominator
                _require(math.isfinite(result))
                return result
            return scalar(tag)

        width, height = scalar(256), scalar(257)
        bits, samples, compression = scalar(258), scalar(277), scalar(259)
        _require(width >= 2 and height >= 2 and bits == 16 and samples == 1
                 and compression == 1 and scalar(262) == 32803
                 and scalar(274, optional=1) == 1 and scalar(284, optional=1) == 1
                 and scalar(339, optional=1) == 1)
        # CFAPattern contains plane indices, not fixed RGB color values. DNG
        # defaults to RGB planes and a rectangular layout when tags are absent.
        # Equivalent RGB plane permutations remain supported; staggered CFA
        # geometries need a different renderer and must not be called RGGB here.
        planes = bytes_tag(50710) if 50710 in tags else bytes((0, 1, 2))
        pattern = bytes_tag(33422)
        _require(integers(33421) == [2, 2] and len(planes) == 3
                 and sorted(planes) == [0, 1, 2] and len(pattern) == 4
                 and all(index < len(planes) for index in pattern)
                 and bytes(planes[index] for index in pattern) == bytes((0, 1, 1, 2)))
        if 50711 in tags:
            _require(tags[50711][:2] == (3, 1) and scalar(50711) == 1)
        version = bytes_tag(50706)
        _require(len(version) == 4 and version[0] == 1)
        if 50707 in tags:
            backward = bytes_tag(50707)
            _require(len(backward) == 4 and backward[0] == 1 and backward <= version)
        if 50713 in tags:
            _require(integers(50713) == [1, 1])
        black, white = level(50714, 0), level(50717, 65535)
        _require(0 <= black < white <= 65535)
        offsets, lengths = integers(273), integers(279)
        rows = scalar(278, optional=height)
        _require(rows > 0 and len(offsets) == len(lengths)
                 and len(offsets) == (height + rows - 1) // rows)
        for index, (offset, count) in enumerate(zip(offsets, lengths)):
            expected = width * min(rows, height - index * rows) * 2
            _require(offset >= 8 and count == expected and offset + count <= size)
        _require(sum(lengths) == width * height * 2)

        def overlap(a, b):
            return a[0] < b[1] and b[0] < a[1]

        # Check metadata layout as well as pixel extents. Shared exact metadata
        # spans are allowed; partially overlapping values or tables are not.
        for index, table in enumerate(tables):
            _require(not any(overlap(table, other) for other in tables[index + 1:]))
            _require(not any(overlap(table, value) for value in values))
        ordered_values = sorted(set(values))
        for left, right in zip(ordered_values, ordered_values[1:]):
            _require(not overlap(left, right))
        strips = sorted((offset, offset + length) for offset, length in zip(offsets, lengths))
        for left, right in zip(strips, strips[1:]):
            _require(not overlap(left, right))
        for strip in strips:
            _require(not any(overlap(strip, metadata) for metadata in tables + values))
        description = tags.get(270)
        request_id, metadata_state = _metadata(
            description[2] if description is not None and description[0] == 2 else None)
        if description is not None and description[0] != 2:
            metadata_state = 'invalid'
        final = os.fstat(stream.fileno())
        _require((initial.st_size, initial.st_mtime_ns) == (final.st_size, final.st_mtime_ns))
        return {'width': width, 'height': height, 'bits_per_sample': bits,
                'samples_per_pixel': samples, 'compression': compression, 'cfa': 'RGGB',
                'strip_offsets': offsets, 'strip_byte_counts': lengths,
                'black_level': black, 'white_level': white,
                'request_id': request_id, 'metadata_state': metadata_state}

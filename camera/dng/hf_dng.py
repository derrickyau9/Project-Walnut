"""Write small uncompressed CFA DNG files for HF-Sim.

This is a deliberately narrow TIFF/DNG writer, not a general DNG SDK. Pixel
values are 14-bit samples in 16-bit containers. Calibration here is synthetic.
It makes no claim about actual FF-1 hardware or color accuracy.
"""

from array import array
from contextlib import nullcontext
import json
import math
import os
import struct
import sys


def _short(*values):
    return struct.pack('<' + 'H' * len(values), *values)


def _long(*values):
    return struct.pack('<' + 'I' * len(values), *values)


def _rationals(*values, signed=False):
    fmt = 'ii' if signed else 'II'
    return b''.join(struct.pack('<' + fmt, numerator, denominator)
                    for numerator, denominator in values)


def _ascii(value):
    return value.encode('ascii') + b'\0'


def _metadata(values):
    """Validate and encode a deliberately small, namespaced metadata set."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError('metadata must be a dictionary')
    identifier_keys = {'request_id', 'capture_id', 'frame_id', 'sensor_id', 'calibration_id'}
    exposure_keys = {'requested_exposure_us', 'actual_exposure_us'}
    gain_keys = {'requested_analogue_gain', 'actual_analogue_gain'}
    allowed = identifier_keys | exposure_keys | gain_keys | {'make', 'model'}
    if set(values) - allowed:
        raise ValueError('unsupported metadata keys')
    for key in identifier_keys | {'make', 'model'}:
        if key in values and (not isinstance(values[key], str) or not values[key] or len(values[key]) > 256 or '\0' in values[key]):
            raise ValueError(f'{key} must be a nonempty string up to 256 characters without NUL')
    for key in exposure_keys:
        value = values.get(key)
        if key in values and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xffffffff):
            raise ValueError(f'{key} must be a positive 32-bit integer')
    for key in gain_keys:
        value = values.get(key)
        if key in values and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
            raise ValueError(f'{key} must be positive and finite')
    try:
        make = _ascii(values.get('make', 'HassleFree'))
        model = _ascii(values.get('model', 'HF-Sim'))
    except UnicodeEncodeError as exc:
        raise ValueError('make and model must be ASCII') from exc
    fields = {key: values[key] for key in sorted(identifier_keys | exposure_keys | gain_keys) if key in values}
    description = _ascii(json.dumps({'namespace': 'org.hasslefree.hf-sim.capture.v1',
                                     'simulated': True, 'capture': fields},
                                    ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    return make, model, description, values.get('actual_exposure_us')


def write_dng(path, width: int, height: int, pixels, metadata=None):
    """Write a simulated uncompressed RGGB DNG; return the path.

    `metadata` may contain ASCII make/model, request/capture/frame/sensor and
    calibration IDs, and requested/actual exposure and analogue gain. Exif
    ExposureTime is actual simulated exposure only. Other supplied fields are
    stored as namespaced JSON in ImageDescription; gain is not presented as ISO.
    A path is opened directly with the existing overwrite behavior. Callers
    needing exclusive creation may instead pass an already-open binary stream;
    its lifetime, flushing and transaction policy remain with the caller.
    Color matrix, neutral,
    and black/white levels describe the synthetic image, not calibration.
    """
    if (isinstance(width, bool) or isinstance(height, bool) or
            not isinstance(width, int) or not isinstance(height, int) or
            width < 2 or height < 2):
        raise ValueError('width and height must be integers of at least 2')
    if width * height > (0xFFFFFFFF // 2):
        raise ValueError('image exceeds classic TIFF limits')
    make, model, description, actual_exposure = _metadata(metadata)
    if len(pixels) != width * height:
        raise ValueError('pixel count does not match image dimensions')
    data = array('H')
    for pixel in pixels:
        if isinstance(pixel, bool) or not isinstance(pixel, int) or not 0 <= pixel <= 16383:
            raise ValueError('pixels must be integer 14-bit values')
        data.append(pixel)
    if sys.byteorder != 'little':
        data.byteswap()
    image_bytes = data.tobytes()
    # Each entry is (tag, TIFF type, count, encoded value). Values >4 bytes are
    # stored after the IFD. A placeholder for StripOffsets is patched below.
    entries = [
        (254, 4, 1, _long(0)), (256, 4, 1, _long(width)),
        (257, 4, 1, _long(height)), (258, 3, 1, _short(16)),
        (259, 3, 1, _short(1)), (262, 3, 1, _short(32803)),
        (270, 2, len(description), description),
        (271, 2, len(make), make), (272, 2, len(model), model),
        (273, 4, 1, _long(0)), (274, 3, 1, _short(1)),
        (277, 3, 1, _short(1)), (278, 4, 1, _long(height)),
        (279, 4, 1, _long(len(image_bytes))), (284, 3, 1, _short(1)),
        (33421, 3, 2, _short(2, 2)), (33422, 1, 4, bytes((0, 1, 1, 2))),
        (50706, 1, 4, bytes((1, 4, 0, 0))),
        (50707, 1, 4, bytes((1, 1, 0, 0))),
        (50708, 2, len(model), model),
        (50710, 1, 3, bytes((0, 1, 2))), (50711, 3, 1, _short(1)),
        (50713, 3, 2, _short(1, 1)), (50714, 4, 1, _long(64)),
        (50717, 4, 1, _long(16383)),
        (50719, 4, 2, _long(0, 0)), (50720, 4, 2, _long(width, height)),
        (50721, 10, 9, _rationals(*[(int(i % 4 == 0), 1) for i in range(9)], signed=True)),
        (50728, 5, 3, _rationals((1, 1), (1, 1), (1, 1))),
        (50778, 3, 1, _short(21)),
        (50829, 4, 4, _long(0, 0, height, width)),
    ]
    if actual_exposure is not None:
        # Exif IFD pointer; the IFD and RATIONAL are appended below.
        entries.append((34665, 4, 1, _long(0)))
    entries.sort(key=lambda item: item[0])
    extra_start = 8 + 2 + 12 * len(entries) + 4
    extra = bytearray()
    encoded = []
    for tag, typ, count, value in entries:
        if len(value) > 4:
            if len(extra) % 2:
                extra.append(0)
            value_field = _long(extra_start + len(extra))
            extra.extend(value)
        else:
            value_field = value.ljust(4, b'\0')
        encoded.append([tag, typ, count, value_field])
    if len(extra) % 2:
        extra.append(0)
    if actual_exposure is not None:
        exif_offset = extra_start + len(extra)
        common = math.gcd(actual_exposure, 1000000)
        numerator, denominator = actual_exposure // common, 1000000 // common
        # IFD: count, ExposureTime RATIONAL entry, next-IFD pointer, value.
        rational_offset = exif_offset + 2 + 12 + 4
        extra.extend(_short(1) + struct.pack('<HHI', 33434, 5, 1) + _long(rational_offset) + _long(0))
        extra.extend(_rationals((numerator, denominator)))
        for entry in encoded:
            if entry[0] == 34665:
                entry[3] = _long(exif_offset)
    image_offset = extra_start + len(extra)
    if image_offset + len(image_bytes) > 0xFFFFFFFF:
        raise ValueError('image exceeds classic TIFF limits')
    for entry in encoded:
        if entry[0] == 273:
            entry[3] = _long(image_offset)
    is_stream = hasattr(path, 'write')
    with nullcontext(path) if is_stream else open(path, 'wb') as output:
        output.write(b'II' + _short(42) + _long(8))
        output.write(_short(len(encoded)))
        for tag, typ, count, value_field in encoded:
            output.write(struct.pack('<HHI', tag, typ, count) + value_field)
        output.write(_long(0))
        output.write(extra)
        output.write(image_bytes)
    return path if is_stream else os.fspath(path)

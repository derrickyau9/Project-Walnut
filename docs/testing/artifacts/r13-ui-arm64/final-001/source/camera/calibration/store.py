"""Small JSON package store for offline HF-Sim calibration bookkeeping.

Only synthetic statistics and explicit uncalibrated markers are supported. A
checksum detects accidental changes, not malicious authors or authenticity.
Callers choose a persistent data directory separately from the system image.
"""

import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from simulation.bayer import generate_bayer


NOTICE = 'Not measured hardware calibration; synthetic software data or an uncalibrated marker only.'
MAX_PACKAGE_BYTES = 65536
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_CHECKSUM = re.compile(r'sha256:[0-9a-f]{64}\Z')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
             *(f'LPT{i}' for i in range(1, 10))}
_FIELDS = {'schema_version', 'sensor_id', 'package_id', 'package_version',
           'status', 'hardware_calibrated', 'source', 'notice', 'reason',
           'data', 'checksum'}
_DATA_FIELDS = {'width', 'height', 'cfa', 'bit_depth', 'exposure_us',
                'analogue_gain', 'dark', 'flat'}
_STAT_FIELDS = {'sample_count', 'minimum', 'maximum', 'mean', 'population_stddev'}


class CalibrationError(ValueError):
    """A diagnosable package/storage failure with a stable machine-readable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, message, code='invalid_package'):
    if not condition:
        raise CalibrationError(code, message)


def _identifier(value, label):
    _require(isinstance(value, str) and _ID.fullmatch(value) is not None
             and not value.endswith('.')
             and value.split('.')[0].upper() not in _RESERVED,
             f'{label} must be a portable identifier of 1-64 characters; paths are forbidden',
             'invalid_identifier')
    return value


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def _number(value, lower, upper):
    return (type(value) in (int, float) and lower <= value <= upper
            and math.isfinite(value))


def _canonical_bytes(package):
    _require(type(package) is dict, 'package must be a JSON object')
    try:
        return json.dumps({k: v for k, v in package.items() if k != 'checksum'},
                          sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                          allow_nan=False).encode('ascii')
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise CalibrationError('invalid_package', 'package is not finite JSON data') from exc


def canonical_checksum(package):
    """SHA-256 of sorted compact ASCII JSON, excluding the checksum field."""
    return 'sha256:' + hashlib.sha256(_canonical_bytes(package)).hexdigest()


def _validate_data(data):
    _require(type(data) is dict and set(data) == _DATA_FIELDS,
             'synthetic data has missing or unknown fields')
    _require(_integer(data['width'], 2, 256) and data['width'] % 2 == 0
             and _integer(data['height'], 2, 256) and data['height'] % 2 == 0,
             'synthetic width and height must be even integers from 2 through 256')
    _require(data['cfa'] == 'RGGB' and type(data['bit_depth']) is int
             and data['bit_depth'] == 14, 'only synthetic 14-bit RGGB is supported')
    _require(_integer(data['exposure_us'], 1, 0xffffffff)
             and _number(data['analogue_gain'], 0.000001, 64),
             'invalid synthetic exposure or analogue gain')
    count = data['width'] * data['height']
    for scene in ('dark', 'flat'):
        summary = data[scene]
        _require(type(summary) is dict and set(summary) == _STAT_FIELDS,
                 f'{scene} statistics have missing or unknown fields')
        _require(type(summary['sample_count']) is int and summary['sample_count'] == count,
                 f'{scene} sample_count does not match frame dimensions')
        low, high = summary['minimum'], summary['maximum']
        _require(_integer(low, 0, 16383) and _integer(high, low, 16383)
                 and _number(summary['mean'], low, high)
                 and _number(summary['population_stddev'], 0, (high - low) / 2),
                 f'{scene} statistics are outside the declared 14-bit range')


def _validate_package_content(package, *, expected_sensor_id=None):
    """Shared v1 semantics, independent of package/binding checksum encoding."""
    _require(type(package) is dict and set(package) == _FIELDS - {'checksum'},
             'package has missing or unknown fields')
    _require(type(package['schema_version']) is int and package['schema_version'] == 1,
             'unsupported calibration schema_version')
    for field in ('sensor_id', 'package_id', 'package_version'):
        _identifier(package[field], field)
    if expected_sensor_id is not None:
        _identifier(expected_sensor_id, 'expected_sensor_id')
        _require(package['sensor_id'] == expected_sensor_id,
                 f"package sensor {package['sensor_id']!r} does not match {expected_sensor_id!r}",
                 'sensor_mismatch')
    _require(package['hardware_calibrated'] is False and package['notice'] == NOTICE,
             'packages must explicitly disclaim measured hardware calibration')
    if package['status'] == 'uncalibrated':
        _require(package['source'] == 'none' and package['data'] is None,
                 'uncalibrated packages cannot contain calibration data')
        _require(isinstance(package['reason'], str) and 1 <= len(package['reason']) <= 512
                 and package['reason'].strip() == package['reason']
                 and all(ord(char) >= 32 and not 0xd800 <= ord(char) <= 0xdfff
                         for char in package['reason']),
                 'uncalibrated packages need a nonempty, printable reason')
    elif package['status'] == 'synthetic':
        _require(package['source'] == 'simulation.bayer.v1' and package['reason'] is None,
                 'synthetic packages must identify the simulator source')
        _validate_data(package['data'])
    else:
        raise CalibrationError('invalid_package', 'status must be synthetic or uncalibrated')


def validate_package(package, *, expected_sensor_id=None):
    """Validate identity, exact v1 schema, provenance and checksum; return a copy."""
    _require(type(package) is dict and set(package) == _FIELDS,
             'package has missing or unknown fields')
    _validate_package_content({k: v for k, v in package.items() if k != 'checksum'},
                              expected_sensor_id=expected_sensor_id)
    checksum = package['checksum']
    _require(isinstance(checksum, str) and _CHECKSUM.fullmatch(checksum) is not None,
             'checksum must be a lowercase sha256 digest')
    _require(hmac.compare_digest(checksum, canonical_checksum(package)),
             'calibration checksum mismatch; package may be corrupt', 'checksum_mismatch')
    _require(len(_encode(package)) <= MAX_PACKAGE_BYTES, 'package exceeds the size limit')
    return copy.deepcopy(package)


def _base(sensor_id, package_id, version, status):
    return {'schema_version': 1, 'sensor_id': sensor_id, 'package_id': package_id,
            'package_version': version, 'status': status, 'hardware_calibrated': False,
            'source': 'none' if status == 'uncalibrated' else 'simulation.bayer.v1',
            'notice': NOTICE, 'reason': None, 'data': None}


def _seal(package):
    package['checksum'] = canonical_checksum(package)
    return validate_package(package)


def create_uncalibrated_package(sensor_id, package_id, version, *,
                                reason='No hardware calibration has been measured.'):
    """Record absence of calibration without inventing any calibration values."""
    package = _base(sensor_id, package_id, version, 'uncalibrated')
    package['reason'] = reason
    return _seal(package)


def _statistics(pixels):
    mean = math.fsum(pixels) / len(pixels)
    return {'sample_count': len(pixels), 'minimum': min(pixels), 'maximum': max(pixels),
            'mean': mean,
            'population_stddev': math.sqrt(math.fsum((p - mean) ** 2 for p in pixels) / len(pixels))}


def create_synthetic_package(sensor_id, package_id, version, *, width=32, height=24,
                             exposure_us=10000, gain=1.0):
    """Summarize bounded deterministic simulated dark/flat frames, never hardware."""
    _require(_integer(width, 2, 256) and width % 2 == 0
             and _integer(height, 2, 256) and height % 2 == 0,
             'synthetic width and height must be even integers from 2 through 256')
    _require(_integer(exposure_us, 1, 0xffffffff) and _number(gain, 0.000001, 64),
             'invalid synthetic exposure or analogue gain')
    package = _base(sensor_id, package_id, version, 'synthetic')
    package['data'] = {'width': width, 'height': height, 'cfa': 'RGGB', 'bit_depth': 14,
                       'exposure_us': exposure_us, 'analogue_gain': float(gain),
                       **{scene: _statistics(generate_bayer(width, height, exposure_us, gain, scene))
                          for scene in ('dark', 'flat')}}
    return _seal(package)


def _encode(package):
    return (json.dumps(package, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _read(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_PACKAGE_BYTES + 1)
        _require(len(raw) <= MAX_PACKAGE_BYTES, 'package exceeds the size limit')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: _reject_constant(value))
    except FileNotFoundError as exc:
        raise CalibrationError('not_found', f'calibration package not found: {path}') from exc
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CalibrationError('invalid_package', f'invalid calibration JSON: {path}') from exc
    except ValueError as exc:
        if isinstance(exc, CalibrationError):
            raise
        raise CalibrationError('invalid_package', f'invalid calibration JSON: {path}') from exc
    except OSError as exc:
        raise CalibrationError('storage_error', f'cannot read calibration package: {path}') from exc


def _reject_constant(value):
    raise CalibrationError('invalid_package', f'non-finite JSON constant: {value}')


def _no_links(path):
    """Reject symlinks and Windows reparse points in any existing component."""
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CalibrationError('storage_error', f'cannot inspect calibration path: {component}') from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise CalibrationError('invalid_path', f'calibration paths cannot use links: {component}')


def _absolute(path):
    try:
        result = Path(os.path.abspath(os.fspath(path)))
        _no_links(result)
        return result
    except (TypeError, ValueError, OSError) as exc:
        if isinstance(exc, CalibrationError):
            raise
        raise CalibrationError('invalid_path', 'invalid calibration filesystem path') from exc


def _within(path, root):
    return path == root or root in path.parents


def _atomic_write(path, content, *, replace):
    """Synchronize a sibling temporary file, then commit a complete pathname."""
    temporary = None
    try:
        _no_links(path)
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.hf-calibration-', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _no_links(path)
        if replace:
            os.replace(temporary, path)
        else:
            # Unlike check-then-replace, this cannot overwrite an identity
            # committed concurrently by another store instance.
            os.link(temporary, path)
        if os.name != 'nt':
            try:
                descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                pass  # Directory synchronization is not supported by every FS.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CalibrationStore:
    """Immutable versioned packages below an explicit persistent data directory.

    Pass system_image_dir to enforce disjoint image/data roots. There is no
    default image-relative or source-tree store. Atomic commits require a local
    filesystem with hard-link support. This is not a privileged filesystem
    sandbox or a claim of arbitrary power-loss durability.
    """

    def __init__(self, data_dir, *, system_image_dir=None):
        self.data_dir = _absolute(data_dir)
        if system_image_dir is not None:
            image = _absolute(system_image_dir)
            _require(not _within(self.data_dir, image) and not _within(image, self.data_dir),
                     'calibration data and system image directories must be disjoint', 'invalid_path')
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot create calibration data directory') from exc

    def _path(self, sensor_id, package_id, version):
        for label, value in (('sensor_id', sensor_id), ('package_id', package_id),
                             ('package_version', version)):
            _identifier(value, label)
        path = self.data_dir / sensor_id / package_id / (version + '.json')
        _no_links(path)
        _require(_within(path, self.data_dir), 'package path escapes calibration data directory', 'invalid_path')
        return path

    def load(self, sensor_id, package_id, version):
        """Read and revalidate every time; corruption is never silently ignored."""
        package = validate_package(_read(self._path(sensor_id, package_id, version)),
                                   expected_sensor_id=sensor_id)
        _require(package['package_id'] == package_id and package['package_version'] == version,
                 'stored package identity does not match its pathname', 'identity_mismatch')
        return package

    def put(self, package, *, expected_sensor_id):
        """Commit a validated package; same identity/content is an idempotent no-op."""
        package = validate_package(package, expected_sensor_id=expected_sensor_id)
        path = self._path(package['sensor_id'], package['package_id'], package['package_version'])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, _encode(package), replace=False)
        except FileExistsError:
            existing = self.load(package['sensor_id'], package['package_id'], package['package_version'])
            _require(existing['checksum'] == package['checksum'],
                     'package identity already exists with different content; use a new version', 'conflict')
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot commit calibration package') from exc
        return copy.deepcopy(package)

    def import_package(self, source, *, expected_sensor_id):
        """Import one bounded JSON file; reject mismatched identity before writing."""
        return self.put(_read(_absolute(source)), expected_sensor_id=expected_sensor_id)

    def export_package(self, sensor_id, package_id, version, destination):
        """Atomically replace an external export file with a validated package."""
        package = self.load(sensor_id, package_id, version)
        target = _absolute(destination)
        _require(not _within(target, self.data_dir),
                 'export destination must be outside the immutable package store', 'invalid_path')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, _encode(package), replace=True)
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot export calibration package') from exc
        return target

    def list_packages(self, sensor_id):
        """Return validated packages for a sensor, ordered by ID/version text."""
        _identifier(sensor_id, 'sensor_id')
        folder = self.data_dir / sensor_id
        _no_links(folder)
        if not folder.exists():
            return []
        result = []
        try:
            for group in sorted(folder.iterdir()):
                _no_links(group)
                _require(group.is_dir(), 'unexpected entry in sensor package directory', 'invalid_path')
                _identifier(group.name, 'package_id')
                for path in sorted(group.iterdir()):
                    if path.name.startswith('.hf-calibration-') and path.suffix == '.tmp':
                        continue  # A crash may leave an uncommitted staging file.
                    _require(path.suffix == '.json', 'unexpected package filename', 'invalid_path')
                    result.append(self.load(sensor_id, group.name, path.stem))
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot list calibration packages') from exc
        return result

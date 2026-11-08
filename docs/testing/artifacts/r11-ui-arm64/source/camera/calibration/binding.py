"""Content-addressed, unprivileged calibration snapshots for daemon activation.

Creating or storing an artifact never activates it. The capture authority must
verify it independently, enforce its control lease, and durably pin the selected
artifact when accepting a capture. These artifacts never alter pixel values.
"""

import copy
import hashlib
import hmac
import json
import re

from .store import (
    CalibrationError, MAX_PACKAGE_BYTES, _absolute, _atomic_write,
    _canonical_bytes, _CHECKSUM, _no_links, _reject_constant, _require, _unique_object,
    _validate_package_content, _within, canonical_checksum, validate_package,
)


BINDING_DOMAIN = b'HF-Sim calibration binding v1\n'
MAX_BINDING_BYTES = 131072
EXPECTED_SENSOR_ID = 'HF-Sim'
_BINDING_ID = re.compile(r'cb1-[0-9a-f]{64}\Z')
_BINDING_FIELDS = {'binding_schema_version', 'binding_id', 'sensor_id',
                   'package_checksum', 'package_payload',
                   'hardware_calibrated', 'pixel_corrections_applied'}


def _payload_bytes(payload):
    _require(type(payload) is str, 'package_payload must be an ASCII JSON string',
             'invalid_binding')
    try:
        raw = payload.encode('ascii')
    except UnicodeError as exc:
        raise CalibrationError('invalid_binding', 'package_payload must be ASCII JSON bytes') from exc
    _require(0 < len(raw) <= MAX_PACKAGE_BYTES, 'binding package_payload exceeds the size limit',
             'invalid_binding')
    return raw


def _parse(raw, description):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CalibrationError):
            raise
        raise CalibrationError('invalid_binding', f'invalid {description} JSON') from exc


def _binding_id(raw):
    return 'cb1-' + hashlib.sha256(BINDING_DOMAIN + raw).hexdigest()


def _encode(binding):
    try:
        return (json.dumps(binding, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    except (TypeError, ValueError, RecursionError) as exc:
        raise CalibrationError('invalid_binding', 'binding is not finite JSON data') from exc


def validate_binding(binding, *, expected_sensor_id=EXPECTED_SENSOR_ID):
    """Verify exact wire hashes, strict package semantics and sensor identity.

    Hashes cover the exact ASCII package_payload bytes. Consumers never
    reserialize floats to verify them. Alternate valid JSON spellings therefore
    have different binding IDs, even when their parsed values compare equal.
    """
    _require(type(binding) is dict and set(binding) == _BINDING_FIELDS,
             'binding has missing or unknown fields', 'invalid_binding')
    _require(type(binding['binding_schema_version']) is int
             and binding['binding_schema_version'] == 1,
             'unsupported binding_schema_version', 'invalid_binding')
    _require(binding['hardware_calibrated'] is False
             and binding['pixel_corrections_applied'] is False,
             'bindings cannot claim measured calibration or applied pixel corrections',
             'invalid_binding')
    _require(type(binding['binding_id']) is str
             and _BINDING_ID.fullmatch(binding['binding_id']) is not None,
             'invalid calibration binding_id', 'invalid_binding')
    _require(type(binding['sensor_id']) is str
             and binding['sensor_id'] == expected_sensor_id,
             'binding sensor does not match the expected sensor', 'sensor_mismatch')
    raw = _payload_bytes(binding['package_payload'])
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    _require(type(binding['package_checksum']) is str
             and _CHECKSUM.fullmatch(binding['package_checksum']) is not None
             and hmac.compare_digest(binding['package_checksum'], digest),
             'binding package checksum mismatch; snapshot may be corrupt', 'checksum_mismatch')
    _require(hmac.compare_digest(binding['binding_id'], _binding_id(raw)),
             'calibration binding_id does not match its snapshot', 'checksum_mismatch')
    _validate_package_content(_parse(raw, 'binding package payload'),
                              expected_sensor_id=expected_sensor_id)
    _require(len(_encode(binding)) <= MAX_BINDING_BYTES, 'binding exceeds the size limit',
             'invalid_binding')
    return copy.deepcopy(binding)


def create_binding(package, *, expected_sensor_id=EXPECTED_SENSOR_ID):
    """Snapshot an already valid standalone package; this does not activate it."""
    package = validate_package(package, expected_sensor_id=expected_sensor_id)
    raw = _canonical_bytes(package)
    binding = {'binding_schema_version': 1, 'binding_id': _binding_id(raw),
               'sensor_id': package['sensor_id'], 'package_checksum': package['checksum'],
               'package_payload': raw.decode('ascii'), 'hardware_calibrated': False,
               'pixel_corrections_applied': False}
    return validate_binding(binding, expected_sensor_id=expected_sensor_id)


def binding_package(binding, *, expected_sensor_id=EXPECTED_SENSOR_ID):
    """Reconstruct a detached standalone package with a derived canonical hash.

    Use binding['package_checksum'] for the exact wire-byte digest. A binding
    may use different JSON spelling, so this derived standalone checksum need
    not equal the binding wire checksum. It does not replace that trace value.
    """
    verified = validate_binding(binding, expected_sensor_id=expected_sensor_id)
    package = _parse(verified['package_payload'], 'binding package payload')
    package['checksum'] = canonical_checksum(package)
    return validate_package(package, expected_sensor_id=expected_sensor_id)


def read_binding(source, *, expected_sensor_id=EXPECTED_SENSOR_ID):
    """Read one bounded external artifact and fully validate it before use."""
    path = _absolute(source)
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BINDING_BYTES + 1)
        _require(len(raw) <= MAX_BINDING_BYTES, 'binding exceeds the size limit',
                 'invalid_binding')
        return validate_binding(_parse(raw.decode('utf-8'), 'binding'),
                                expected_sensor_id=expected_sensor_id)
    except FileNotFoundError as exc:
        raise CalibrationError('not_found', f'calibration binding not found: {path}') from exc
    except UnicodeError as exc:
        raise CalibrationError('invalid_binding', f'invalid binding JSON: {path}') from exc
    except OSError as exc:
        raise CalibrationError('storage_error', f'cannot read calibration binding: {path}') from exc


class BindingStore:
    """Immutable artifacts at an explicit persistent root; no active selection.

    The daemon, not this helper, owns lease checks and authoritative activation.
    No valid artifact is substituted for a missing or corrupt requested ID.
    """

    def __init__(self, data_dir, *, system_image_dir=None):
        self.data_dir = _absolute(data_dir)
        if system_image_dir is not None:
            image = _absolute(system_image_dir)
            _require(not _within(self.data_dir, image) and not _within(image, self.data_dir),
                     'binding data and system image directories must be disjoint', 'invalid_path')
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot create binding data directory') from exc

    def _path(self, binding_id):
        _require(type(binding_id) is str and _BINDING_ID.fullmatch(binding_id) is not None,
                 'binding_id must be cb1- followed by 64 lowercase hex characters',
                 'invalid_identifier')
        path = self.data_dir / (binding_id + '.json')
        _no_links(path)
        return path

    def load(self, binding_id):
        binding = read_binding(self._path(binding_id))
        _require(binding['binding_id'] == binding_id,
                 'stored binding identity does not match its pathname', 'identity_mismatch')
        return binding

    def put(self, binding):
        binding = validate_binding(binding)
        path = self._path(binding['binding_id'])
        try:
            _atomic_write(path, _encode(binding), replace=False)
        except FileExistsError:
            existing = self.load(binding['binding_id'])
            _require(existing == binding, 'binding identity already has different content', 'conflict')
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot commit calibration binding') from exc
        return copy.deepcopy(binding)

    def import_binding(self, source):
        return self.put(read_binding(source))

    def export_binding(self, binding_id, destination):
        binding = self.load(binding_id)
        target = _absolute(destination)
        _require(not _within(target, self.data_dir),
                 'export destination must be outside the immutable binding store', 'invalid_path')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, _encode(binding), replace=True)
        except OSError as exc:
            raise CalibrationError('storage_error', 'cannot export calibration binding') from exc
        return target

    def snapshot(self, packages, package_id, version):
        """Verify the source on disk and persist its detached HF-Sim snapshot."""
        return self.put(create_binding(packages.load(EXPECTED_SENSOR_ID, package_id, version)))

"""Pure validation of synthetic calibration summaries and capture context.

These checks establish internal consistency, not authenticity or measured
calibration. No file is opened and no device state is changed.
"""
import math
import re


_SUMMARY_FIELDS = {'sensor_id', 'binding_id', 'calibration_id', 'package_checksum',
                   'hardware_calibrated', 'pixel_corrections_applied'}
_BINDING_ID = re.compile(r'cb1-[0-9a-f]{64}\Z')
_CHECKSUM = re.compile(r'sha256:[0-9a-f]{64}\Z')
_BINDING_FIELDS = {'capture_record_version', 'calibration_binding',
                   'hardware_calibrated', 'pixel_corrections_applied'}
_IDENTITIES = ('request_id', 'sensor_id', 'calibration_id')
_EXPOSURES = ('requested_exposure_us', 'actual_exposure_us')
_GAINS = ('requested_analogue_gain', 'actual_analogue_gain')


def validate_calibration_summary(value):
    """Validate a present summary; callers may separately allow legacy absence."""
    if (not isinstance(value, dict) or set(value) != _SUMMARY_FIELDS
            or value['sensor_id'] != 'HF-Sim'
            or value['hardware_calibrated'] is not False
            or value['pixel_corrections_applied'] is not False):
        raise ValueError('synthetic calibration summary is malformed')
    binding_id = value['binding_id']
    if binding_id is None:
        if value['calibration_id'] != 'synthetic-uncalibrated' or value['package_checksum'] is not None:
            raise ValueError('uncalibrated summary has inconsistent identity')
    elif (not isinstance(binding_id, str) or _BINDING_ID.fullmatch(binding_id) is None
            or value['calibration_id'] != binding_id
            or not isinstance(value['package_checksum'], str)
            or _CHECKSUM.fullmatch(value['package_checksum']) is None):
        raise ValueError('bound calibration summary has inconsistent identity')
    return value


def _gain(value):
    if type(value) not in (int, float) or not 1 <= value <= 16:
        return False
    return math.isfinite(value)


def validate_capture_calibration_context(record):
    """Validate versioned binding and any nested native evidence against it.

    Legacy unbound records need no new fields. A native evidence object is
    optional, but if supplied it must describe this same frame and settings.
    Legacy worker evidence may omit capture_id. Unknown outcomes may retain
    worker evidence while withholding confirmed outer frame/actual fields;
    validation never fills those fields or promotes the outcome to complete.
    """
    if not isinstance(record, dict):
        raise ValueError('capture must be an object')
    if (_BINDING_FIELDS.intersection(record)
            or str(record.get('calibration_id', '')).startswith('cb1-')):
        from camera.calibration import validate_binding, CalibrationError
        try:
            binding = validate_binding(record.get('calibration_binding'))
        except CalibrationError as exc:
            raise ValueError('invalid capture calibration snapshot') from exc
        if (type(record.get('capture_record_version')) is not int
                or record['capture_record_version'] != 2
                or record.get('calibration_id') != binding['binding_id']
                or record.get('sensor_id') != 'HF-Sim'
                or record.get('hardware_calibrated') is not False
                or record.get('pixel_corrections_applied') is not False):
            raise ValueError('bound capture identity is inconsistent')
    if 'native_evidence' not in record:
        return record
    evidence = record['native_evidence']
    if not isinstance(evidence, dict):
        raise ValueError('native capture evidence must be an object')
    for name in _IDENTITIES:
        value = evidence.get(name)
        if not isinstance(value, str) or not value or value != record.get(name):
            raise ValueError('native capture evidence identity differs from its record')
    if ('capture_id' in evidence and
            (not isinstance(evidence['capture_id'], str)
             or evidence['capture_id'] != record.get('request_id')
             or evidence['capture_id'] != record.get('capture_id'))):
        raise ValueError('native capture evidence capture identity differs from its record')
    frame = evidence.get('frame_id')
    unknown = record.get('status') == 'unknown'
    if (not isinstance(frame, str) or not frame or frame != record.get('request_id')
            or (record.get('frame_id') is not None or not unknown) and frame != record.get('frame_id')):
        raise ValueError('native capture evidence frame identity differs from its record')
    for name in _EXPOSURES:
        value = evidence.get(name)
        expected = record.get(name)
        if name == 'actual_exposure_us' and expected is None and unknown:
            expected = record.get('requested_exposure_us')
        if (type(value) is not int or not 100 <= value <= 30000000
                or type(expected) is not int or value != expected):
            raise ValueError('native capture evidence exposure differs from its record')
    for name in _GAINS:
        value = evidence.get(name)
        expected = record.get(name)
        if name == 'actual_analogue_gain' and expected is None and unknown:
            expected = record.get('requested_analogue_gain')
        if not _gain(value) or not _gain(expected) or value != expected:
            raise ValueError('native capture evidence gain differs from its record')
    for name in ('hardware_calibrated', 'pixel_corrections_applied'):
        if name in evidence and evidence[name] is not False:
            raise ValueError('native evidence cannot claim hardware calibration or pixel correction')
    if 'simulated' in evidence and evidence['simulated'] is not True:
        raise ValueError('native evidence cannot claim physical acquisition')
    return record

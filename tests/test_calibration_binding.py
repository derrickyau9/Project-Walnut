import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from camera.calibration import (
    BindingStore, CalibrationError, CalibrationStore, binding_package,
    create_binding, create_synthetic_package, create_uncalibrated_package,
    read_binding, validate_binding, validate_package,
)
from camera.calibration.binding import BINDING_DOMAIN


class CalibrationBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packages = CalibrationStore(self.root / 'packages')
        self.bindings = BindingStore(self.root / 'bindings', system_image_dir=self.root / 'image')
        self.package = create_synthetic_package('HF-Sim', 'synthetic-dark-flat', '1.0.0',
                                                 width=4, height=4)
        self.binding = create_binding(self.package)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(CalibrationError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def wire_binding(self, payload, **changes):
        result = {**self.binding, 'package_payload': payload,
                  'package_checksum': 'sha256:' + hashlib.sha256(payload.encode('ascii')).hexdigest(),
                  'binding_id': 'cb1-' + hashlib.sha256(
                      b'HF-Sim calibration binding v1\n' + payload.encode('ascii')).hexdigest()}
        result.update(changes)
        return result

    def persisted_path(self):
        return self.bindings.data_dir / (self.binding['binding_id'] + '.json')

    def test_exact_domain_bytes_and_hashes_match_hashlib(self):
        self.assertEqual(BINDING_DOMAIN, b'HF-Sim calibration binding v1\n')
        payload = self.binding['package_payload'].encode('ascii')
        self.assertEqual(self.binding['package_checksum'], self.package['checksum'])
        self.assertEqual(self.binding['package_checksum'],
                         'sha256:' + hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.binding['binding_id'], 'cb1-' + hashlib.sha256(
            b'HF-Sim calibration binding v1\n' + payload).hexdigest())
        self.assertIs(self.binding['hardware_calibrated'], False)
        self.assertIs(self.binding['pixel_corrections_applied'], False)
        self.assertEqual(binding_package(self.binding), self.package)

    def test_noncanonical_wire_bytes_are_verified_without_float_reserialization(self):
        payload = json.loads(self.binding['package_payload'])
        wire = json.dumps(payload, indent=2, ensure_ascii=True).replace('1.0,', '1e0,')
        alternate = self.wire_binding(wire)
        self.assertEqual(validate_binding(alternate), alternate)
        self.assertNotEqual(alternate['binding_id'], self.binding['binding_id'])
        self.assertNotEqual(alternate['package_checksum'], self.package['checksum'])
        reconstructed = binding_package(alternate)
        self.assertEqual(reconstructed, self.package)
        self.assertEqual(validate_package(reconstructed), reconstructed)
        self.assertNotEqual(reconstructed['checksum'], alternate['package_checksum'])

    def test_snapshot_revalidates_source_store_and_cannot_trust_a_verified_flag(self):
        self.packages.put(self.package, expected_sensor_id='HF-Sim')
        corrupted = copy.deepcopy(self.package)
        corrupted['data']['flat']['mean'] += 1
        source = self.packages.data_dir / 'HF-Sim' / 'synthetic-dark-flat' / '1.0.0.json'
        source.write_text(json.dumps(corrupted), encoding='ascii')
        self.assert_code('checksum_mismatch', self.bindings.snapshot,
                         self.packages, 'synthetic-dark-flat', '1.0.0')
        self.assertEqual(list(self.bindings.data_dir.iterdir()), [])
        self.assert_code('invalid_binding', validate_binding, {**self.binding, 'verified': True})

    def test_wrong_source_sensor_is_rejected_before_writing_binding(self):
        wrong = create_uncalibrated_package('different-sensor', 'factory', '0')
        self.assert_code('sensor_mismatch', create_binding, wrong)
        self.packages.put(self.package, expected_sensor_id='HF-Sim')
        source = self.packages.data_dir / 'HF-Sim' / 'synthetic-dark-flat' / '1.0.0.json'
        source.write_text(json.dumps(wrong), encoding='ascii')
        self.assert_code('sensor_mismatch', self.bindings.snapshot,
                         self.packages, 'synthetic-dark-flat', '1.0.0')
        self.assertEqual(list(self.bindings.data_dir.iterdir()), [])

    def test_snapshot_is_detached_from_source_changes_and_survives_restart(self):
        self.packages.put(self.package, expected_sensor_id='HF-Sim')
        snapshot = self.bindings.snapshot(self.packages, 'synthetic-dark-flat', '1.0.0')
        original = self.persisted_path().read_bytes()
        self.package['data']['flat']['mean'] = 0
        snapshot['package_payload'] = '{}'
        source = self.packages.data_dir / 'HF-Sim' / 'synthetic-dark-flat' / '1.0.0.json'
        source.write_text('corrupt source after snapshot creation', encoding='ascii')
        restarted = BindingStore(self.bindings.data_dir, system_image_dir=self.root / 'new-image')
        self.assertEqual(restarted.load(self.binding['binding_id']), self.binding)
        self.assertEqual(self.persisted_path().read_bytes(), original)
        self.assertEqual(binding_package(self.binding)['data']['dark']['mean'], 64)

    def test_tampered_persisted_artifact_is_fenced_on_restart_without_fallback(self):
        self.bindings.put(self.binding)
        corrupted = {**self.binding, 'package_payload': self.binding['package_payload'] + ' '}
        self.persisted_path().write_text(json.dumps(corrupted), encoding='ascii')
        before = self.persisted_path().read_bytes()
        restarted = BindingStore(self.bindings.data_dir)
        self.assert_code('checksum_mismatch', restarted.load, self.binding['binding_id'])
        self.assert_code('checksum_mismatch', restarted.put, self.binding)
        self.assertEqual(self.persisted_path().read_bytes(), before)
        self.assertEqual(len(list(restarted.data_dir.glob('*.json'))), 1)

    def test_binding_path_identity_and_requested_id_are_checked(self):
        self.bindings.put(self.binding)
        other = create_binding(create_uncalibrated_package('HF-Sim', 'factory', '0'))
        self.persisted_path().write_text(json.dumps(other), encoding='ascii')
        self.assert_code('identity_mismatch', self.bindings.load, self.binding['binding_id'])
        for bad in ('../escape', 'cb1-' + 'G' * 64, 'cb1-' + '0' * 63, '', 'NUL'):
            self.assert_code('invalid_identifier', self.bindings.load, bad)
        self.assert_code('not_found', self.bindings.load, 'cb1-' + '0' * 64)

    def test_export_import_round_trip_is_immutable_and_idempotent(self):
        self.bindings.put(self.binding)
        self.bindings.put(self.binding)
        exported = self.bindings.export_binding(self.binding['binding_id'], self.root / 'backup.json')
        self.assertEqual(read_binding(exported), self.binding)
        other = BindingStore(self.root / 'other-bindings')
        self.assertEqual(other.import_binding(exported), self.binding)
        self.assertEqual(other.load(self.binding['binding_id']), self.binding)
        self.assertEqual(len(list(self.bindings.data_dir.glob('*.json'))), 1)

    def test_bad_import_and_interrupted_commit_leave_no_partial_artifact(self):
        source = self.root / 'incoming.json'
        source.write_text(json.dumps({**self.binding, 'hardware_calibrated': True}), encoding='ascii')
        self.assert_code('invalid_binding', self.bindings.import_binding, source)
        self.assertEqual(list(self.bindings.data_dir.iterdir()), [])
        with patch('camera.calibration.store.os.link', side_effect=OSError('injected commit failure')):
            self.assert_code('storage_error', self.bindings.put, self.binding)
        self.assertEqual(list(self.bindings.data_dir.iterdir()), [])

    def test_interrupted_export_preserves_previous_artifact(self):
        self.bindings.put(self.binding)
        target = self.root / 'backup.json'
        target.write_bytes(b'previous complete backup')
        with patch('camera.calibration.store.os.replace', side_effect=OSError('injected export failure')):
            self.assert_code('storage_error', self.bindings.export_binding,
                             self.binding['binding_id'], target)
        self.assertEqual(target.read_bytes(), b'previous complete backup')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_wrapper_schema_false_claims_hash_tampering_and_mismatch_are_rejected(self):
        for change in ({'binding_schema_version': 2}, {'binding_schema_version': True},
                       {'hardware_calibrated': True}, {'hardware_calibrated': 0},
                       {'pixel_corrections_applied': True}, {'verified': True}):
            self.assert_code('invalid_binding', validate_binding, {**self.binding, **change})
        self.assert_code('sensor_mismatch', validate_binding,
                         {**self.binding, 'sensor_id': 'different-sensor'})
        self.assert_code('checksum_mismatch', validate_binding,
                         {**self.binding, 'package_checksum': 'sha256:' + '0' * 64})
        self.assert_code('checksum_mismatch', validate_binding,
                         {**self.binding, 'package_checksum': 'sha256:漢'})
        self.assert_code('checksum_mismatch', validate_binding,
                         {**self.binding, 'binding_id': 'cb1-' + '0' * 64})
        payload = json.loads(self.binding['package_payload'])
        payload['sensor_id'] = 'different-sensor'
        self.assert_code('sensor_mismatch', validate_binding, self.wire_binding(json.dumps(payload)))

    def test_valid_hash_does_not_excuse_invalid_package_semantics(self):
        for change in ({'schema_version': 2}, {'hardware_calibrated': True},
                       {'status': 'measured'}, {'source': 'physical-hardware'},
                       {'notice': 'measured calibration'}, {'checksum': 'forbidden'},
                       {'verified': True}):
            payload = {**json.loads(self.binding['package_payload']), **change}
            self.assert_code('invalid_package', validate_binding,
                             self.wire_binding(json.dumps(payload)))
        for field, value in [('width', True), ('height', 3), ('analogue_gain', 65),
                             ('analogue_gain', float('inf')), ('bit_depth', 16)]:
            payload = json.loads(self.binding['package_payload'])
            payload['data'][field] = value
            self.assert_code('invalid_package', validate_binding,
                             self.wire_binding(json.dumps(payload)))

    def test_duplicate_nonfinite_recursive_and_oversized_payloads_fail(self):
        payload = self.binding['package_payload']
        duplicate = payload[:-1] + ',"schema_version":1}'
        self.assert_code('invalid_package', validate_binding, self.wire_binding(duplicate))
        for malformed in ('{', '[]', '{"x":NaN}', '{"x":Infinity}', '[' * 2000 + ']' * 2000):
            with self.assertRaises(CalibrationError):
                validate_binding(self.wire_binding(malformed))
        self.assert_code('invalid_binding', validate_binding,
                         {**self.binding, 'package_payload': ' ' * 65537})
        self.assert_code('invalid_binding', validate_binding,
                         {**self.binding, 'package_payload': '{"literal":"漢"}'})
        source = self.root / 'huge.json'
        source.write_bytes(b' ' * 131073)
        self.assert_code('invalid_binding', read_binding, source)
        source.write_text('{"binding_schema_version":1,"binding_schema_version":1}', encoding='ascii')
        self.assert_code('invalid_package', read_binding, source)

    def test_unicode_codepoint_length_finite_extremes_and_scalar_validation(self):
        reason = '漢' + '🚀' * 510 + '字'
        self.assertEqual(len(reason), 512)
        self.assertGreater(len(reason.encode('utf-8')), 512)
        unicode_binding = create_binding(create_uncalibrated_package('HF-Sim', 'unicode', '1',
                                                                      reason=reason))
        self.assertEqual(binding_package(unicode_binding)['reason'], reason)
        for invalid in ('a' * 513, '\ud800', '\udfff', 'a\n', '\u2000reason', 'reason\u3000'):
            self.assert_code('invalid_package', create_uncalibrated_package,
                             'HF-Sim', 'invalid', '1', reason=invalid)
        extreme = create_synthetic_package('HF-Sim', 'extreme', '1', width=2, height=2,
                                            exposure_us=4294967295, gain=0.000001)
        self.assertEqual(binding_package(create_binding(extreme)), extreme)

    def test_frozen_native_interop_fixtures_are_self_consistent(self):
        fixture_dir = Path(__file__).resolve().parents[1] / 'camera' / 'calibration' / 'fixtures'
        for kind in ('uncalibrated', 'synthetic', 'unicode'):
            with self.subTest(fixture=kind):
                artifact = read_binding(fixture_dir / f'binding-{kind}.json')
                raw = artifact['package_payload'].encode('ascii')
                self.assertEqual(artifact['package_checksum'], 'sha256:' + hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifact['binding_id'], 'cb1-' + hashlib.sha256(
                    b'HF-Sim calibration binding v1\n' + raw).hexdigest())
                self.assertEqual(validate_package(binding_package(artifact))['sensor_id'], 'HF-Sim')

    def test_binding_store_paths_and_image_separation(self):
        self.assert_code('invalid_path', BindingStore, self.root / 'image' / 'bindings',
                         system_image_dir=self.root / 'image')
        self.bindings.put(self.binding)
        self.assert_code('invalid_path', self.bindings.export_binding,
                         self.binding['binding_id'], self.bindings.data_dir / 'other.json')

    def test_binding_artifact_symlink_is_rejected_without_following_target(self):
        external = self.root / 'external.json'
        external.write_text(json.dumps(self.binding), encoding='ascii')
        try:
            self.persisted_path().symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'OS does not permit creating test symlinks: {exc}')
        self.assert_code('invalid_path', self.bindings.load, self.binding['binding_id'])
        self.assert_code('invalid_path', self.bindings.put, self.binding)
        self.assertEqual(json.loads(external.read_text()), self.binding)


if __name__ == '__main__':
    unittest.main()

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from camera.calibration import (
    CalibrationError, CalibrationStore, canonical_checksum,
    create_synthetic_package, create_uncalibrated_package, validate_package,
)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = CalibrationStore(self.root / 'persistent-calibration',
                                      system_image_dir=self.root / 'system-image')
        self.package = create_synthetic_package('sim-sensor-001', 'dark-flat', '1.0.0',
                                                 width=4, height=4)

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(CalibrationError) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def source(self, content):
        path = self.root / 'incoming.json'
        path.write_text(content, encoding='utf-8')
        return path

    def reseal(self, package):
        package['checksum'] = canonical_checksum(package)
        return package

    def test_synthetic_statistics_are_deterministic_and_not_hardware_calibration(self):
        self.assertEqual(self.package,
                         create_synthetic_package('sim-sensor-001', 'dark-flat', '1.0.0',
                                                   width=4, height=4))
        self.assertEqual(self.package['status'], 'synthetic')
        self.assertIs(self.package['hardware_calibrated'], False)
        self.assertIn('Not measured hardware calibration', self.package['notice'])
        self.assertEqual(self.package['source'], 'simulation.bayer.v1')
        self.assertEqual(self.package['data']['dark'], {
            'sample_count': 16, 'minimum': 64, 'maximum': 64, 'mean': 64.0,
            'population_stddev': 0.0})
        self.assertEqual(self.package['data']['flat']['minimum'], 1292)
        self.assertEqual(self.package['data']['flat']['maximum'], 2112)

    def test_uncalibrated_marker_contains_no_inferred_values(self):
        package = create_uncalibrated_package('physical-sensor-unknown', 'factory', '0',
                                               reason='Hardware measurements pending.')
        self.assertEqual(package['status'], 'uncalibrated')
        self.assertEqual(package['source'], 'none')
        self.assertIsNone(package['data'])
        self.assertIs(package['hardware_calibrated'], False)
        self.assertEqual(validate_package(package), package)

    def test_checksum_canonical_order_whitespace_and_copy_isolation(self):
        self.assertEqual(canonical_checksum({'b': 2, 'a': 1}),
                         'sha256:' + hashlib.sha256(b'{"a":1,"b":2}').hexdigest())
        self.assertEqual(canonical_checksum({'a': 1, 'b': 2, 'checksum': 'ignored'}),
                         canonical_checksum({'b': 2, 'a': 1}))
        reformatted = self.source(json.dumps(dict(reversed(list(self.package.items()))), indent=4))
        imported = self.store.import_package(reformatted, expected_sensor_id='sim-sensor-001')
        imported['data']['dark']['mean'] = 0
        self.assertEqual(self.store.load('sim-sensor-001', 'dark-flat', '1.0.0'), self.package)

    def test_import_export_round_trip_and_restart_use_persistent_data(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        export = self.store.export_package('sim-sensor-001', 'dark-flat', '1.0.0',
                                           self.root / 'backup' / 'package.json')
        other = CalibrationStore(self.root / 'other-body')
        self.assertEqual(other.import_package(export, expected_sensor_id='sim-sensor-001'), self.package)
        restarted = CalibrationStore(self.store.data_dir, system_image_dir=self.root / 'new-image')
        self.assertEqual(restarted.load('sim-sensor-001', 'dark-flat', '1.0.0'), self.package)
        self.assertEqual(restarted.list_packages('sim-sensor-001'), [self.package])
        self.assertEqual(restarted.list_packages('different-sensor'), [])

    def test_identity_is_immutable_and_idempotent(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        changed = create_uncalibrated_package('sim-sensor-001', 'dark-flat', '1.0.0')
        self.assert_code('conflict', self.store.put, changed, expected_sensor_id='sim-sensor-001')
        self.assertEqual(self.store.load('sim-sensor-001', 'dark-flat', '1.0.0'), self.package)
        changed['package_version'] = '2.0.0'
        self.store.put(self.reseal(changed), expected_sensor_id='sim-sensor-001')
        self.assertEqual(len(self.store.list_packages('sim-sensor-001')), 2)

    def test_mismatched_sensor_is_diagnosed_before_creating_package_paths(self):
        incoming = self.source(json.dumps(self.package))
        self.assert_code('sensor_mismatch', self.store.import_package, incoming,
                         expected_sensor_id='replacement-sensor')
        self.assertEqual(list(self.store.data_dir.iterdir()), [])

    def test_corrupt_package_is_not_loaded_or_overwritten(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        saved = self.store.data_dir / 'sim-sensor-001' / 'dark-flat' / '1.0.0.json'
        changed = copy.deepcopy(self.package)
        changed['data']['flat']['mean'] += 1
        saved.write_text(json.dumps(changed), encoding='utf-8')
        self.assert_code('checksum_mismatch', self.store.load, 'sim-sensor-001', 'dark-flat', '1.0.0')
        self.assert_code('checksum_mismatch', self.store.put, self.package,
                         expected_sensor_id='sim-sensor-001')
        self.assertEqual(json.loads(saved.read_text())['data']['flat']['mean'],
                         changed['data']['flat']['mean'])

    def test_stored_sensor_and_package_path_identity_are_rechecked(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        saved = self.store.data_dir / 'sim-sensor-001' / 'dark-flat' / '1.0.0.json'
        for field, value, code in [('sensor_id', 'replacement-sensor', 'sensor_mismatch'),
                                    ('package_id', 'other', 'identity_mismatch'),
                                    ('package_version', '2.0.0', 'identity_mismatch')]:
            changed = {**self.package, field: value}
            saved.write_text(json.dumps(self.reseal(changed)), encoding='utf-8')
            with self.subTest(field=field):
                self.assert_code(code, self.store.load, 'sim-sensor-001', 'dark-flat', '1.0.0')

    def test_invalid_import_never_replaces_existing_valid_package(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        incoming = self.source(json.dumps({**self.package, 'checksum': 'sha256:' + '0' * 64}))
        self.assert_code('checksum_mismatch', self.store.import_package, incoming,
                         expected_sensor_id='sim-sensor-001')
        self.assertEqual(self.store.load('sim-sensor-001', 'dark-flat', '1.0.0'), self.package)

    def test_strict_schema_provenance_and_numeric_validation(self):
        cases = [
            {'schema_version': True}, {'schema_version': 2}, {'unknown': 'field'},
            {'hardware_calibrated': True}, {'hardware_calibrated': 0},
            {'status': 'measured'}, {'source': 'physical-sensor'}, {'notice': 'calibrated'},
            {'reason': 'measured'}, {'checksum': 'sha256:' + 'A' * 64},
        ]
        for change in cases:
            with self.subTest(change=change):
                self.assert_code('invalid_package', validate_package, {**self.package, **change})
        for field, value in [('width', True), ('height', 3), ('exposure_us', False),
                             ('analogue_gain', float('nan')), ('analogue_gain', 10 ** 1000),
                             ('bit_depth', True), ('cfa', 'BGGR')]:
            changed = copy.deepcopy(self.package)
            changed['data'][field] = value
            with self.subTest(field=field, value=repr(value)[:60]):
                self.assert_code('invalid_package', validate_package, changed)
        for field, value in [('sample_count', True), ('sample_count', 1),
                             ('minimum', -1), ('maximum', 20000), ('mean', float('inf')),
                             ('population_stddev', -1)]:
            changed = copy.deepcopy(self.package)
            changed['data']['flat'][field] = value
            with self.subTest(statistic=field):
                self.assert_code('invalid_package', validate_package, changed)
        marker = create_uncalibrated_package('sensor', 'package', '1')
        for change in ({'reason': ''}, {'reason': '\n'}, {'data': {}}, {'source': 'simulator'}):
            self.assert_code('invalid_package', validate_package, {**marker, **change})

    def test_malformed_duplicate_nonfinite_oversized_and_nonobject_json(self):
        for content in ('{', '[]', '{"sensor_id":"one","sensor_id":"two"}',
                        '{"value":NaN}', '{"value":Infinity}', ' ' * 65537,
                        '{"n":' + '9' * 5000 + '}'):
            with self.subTest(content=content[:60]):
                self.assert_code('invalid_package', self.store.import_package,
                                 self.source(content), expected_sensor_id='sim-sensor-001')
        binary = self.root / 'binary.json'
        binary.write_bytes(b'\xff\xfe')
        self.assert_code('invalid_package', self.store.import_package, binary,
                         expected_sensor_id='sim-sensor-001')

    def test_identifiers_reject_traversal_absolute_paths_and_windows_devices(self):
        invalid = ['../escape', '..', '/absolute', 'C:\\escape', 'one/two', 'one\\two',
                   '', ' leading', 'trailing.', 'NUL', 'con.txt', 'LPT9', 'a' * 65]
        for value in invalid:
            for field in ('sensor_id', 'package_id', 'package_version'):
                changed = {**self.package, field: value}
                with self.subTest(field=field, value=value):
                    self.assert_code('invalid_identifier', self.store.put, changed,
                                     expected_sensor_id='sim-sensor-001')
            self.assert_code('invalid_identifier', self.store.load, value, 'dark-flat', '1.0.0')
        self.assertFalse((self.root / 'escape').exists())

    def test_system_image_and_calibration_roots_must_be_disjoint(self):
        for data, image in [('image/calibration', 'image'), ('image', 'image'), ('data', 'data/image')]:
            with self.subTest(data=data, image=image):
                self.assert_code('invalid_path', CalibrationStore, self.root / data,
                                 system_image_dir=self.root / image)

    def test_atomic_export_failure_preserves_existing_file_and_cleans_staging_file(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        target = self.root / 'export.json'
        target.write_bytes(b'previous complete backup')
        with patch('camera.calibration.store.os.replace', side_effect=OSError('injected replace failure')):
            self.assert_code('storage_error', self.store.export_package,
                             'sim-sensor-001', 'dark-flat', '1.0.0', target)
        self.assertEqual(target.read_bytes(), b'previous complete backup')
        self.assertEqual(list(self.root.glob('.hf-calibration-*')), [])
        self.store.export_package('sim-sensor-001', 'dark-flat', '1.0.0', target)
        self.assertEqual(json.loads(target.read_text()), self.package)

    def test_atomic_import_failure_leaves_no_partial_visible_package(self):
        with patch('camera.calibration.store.os.link', side_effect=OSError('injected link failure')):
            self.assert_code('storage_error', self.store.put, self.package,
                             expected_sensor_id='sim-sensor-001')
        self.assertEqual(list(self.store.data_dir.rglob('*.json')), [])
        self.assertEqual(list(self.store.data_dir.rglob('*.tmp')), [])

    def test_failed_synchronization_does_not_commit(self):
        with patch('camera.calibration.store.os.fsync', side_effect=OSError('injected sync failure')):
            self.assert_code('storage_error', self.store.put, self.package,
                             expected_sensor_id='sim-sensor-001')
        self.assertEqual(list(self.store.data_dir.rglob('*.*')), [])

    def test_concurrent_import_cannot_overwrite_committed_identity(self):
        alternate = create_uncalibrated_package('sim-sensor-001', 'dark-flat', '1.0.0')

        def commit(package):
            try:
                self.store.put(package, expected_sensor_id='sim-sensor-001')
                return 'committed'
            except CalibrationError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(commit, [self.package, alternate]))
        self.assertCountEqual(results, ['committed', 'conflict'])
        self.assertIn(self.store.load('sim-sensor-001', 'dark-flat', '1.0.0'),
                      [self.package, alternate])
        self.assertEqual(list(self.store.data_dir.rglob('*.tmp')), [])

    def test_export_cannot_overwrite_store_identity(self):
        self.store.put(self.package, expected_sensor_id='sim-sensor-001')
        self.assert_code('invalid_path', self.store.export_package,
                         'sim-sensor-001', 'dark-flat', '1.0.0', self.store.data_dir / 'other.json')

    def test_symlink_escape_is_rejected_when_platform_allows_symlinks(self):
        outside = self.root / 'outside'
        outside.mkdir()
        linked_sensor = self.store.data_dir / 'sim-sensor-001'
        try:
            linked_sensor.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'OS does not permit creating test symlinks: {exc}')
        self.assert_code('invalid_path', self.store.put, self.package,
                         expected_sensor_id='sim-sensor-001')
        self.assert_code('invalid_path', self.store.list_packages, 'sim-sensor-001')
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_and_unexpected_entries_have_explicit_diagnostics(self):
        self.assert_code('not_found', self.store.load, 'sim-sensor-001', 'dark-flat', '1.0.0')
        sensor = self.store.data_dir / 'sim-sensor-001'
        sensor.mkdir()
        (sensor / 'unexpected-file').write_text('invalid')
        self.assert_code('invalid_path', self.store.list_packages, 'sim-sensor-001')


if __name__ == '__main__':
    unittest.main()

"""Actual native-process proofs for binding activation and capture traceability."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
import uuid

from camera.camerad.service import CameraError, DEFAULTS
from tests import test_native_daemon as _native
from tests import test_native_settings as _settings
from tests import test_native_worker as _worker


ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'hf-active-calibration-v1'


class NativeCalibrationBindingTests(unittest.TestCase):
    # Reuse process lifecycle helpers without inheriting another TestCase's tests.
    @classmethod
    def setUpClass(cls):
        _settings.NativeSettingsTests.setUpClass.__func__(cls)
        cls.fixtures = {
            name: json.loads((ROOT / 'camera/calibration/fixtures' / ('binding-' + name + '.json')).read_text(encoding='ascii'))
            for name in ('uncalibrated', 'synthetic', 'unicode')
        }

    @classmethod
    def tearDownClass(cls):
        _settings.NativeSettingsTests.tearDownClass.__func__(cls)

    setUp = _settings.NativeSettingsTests.setUp
    tearDown = _settings.NativeSettingsTests.tearDown
    start = _settings.NativeSettingsTests.start
    restart = _settings.NativeSettingsTests.restart
    configure = _settings.NativeSettingsTests.configure
    metadata = _settings.NativeSettingsTests.metadata
    execute = _settings.NativeSettingsTests.execute
    wait = _native.NativeDaemonTests.wait

    @staticmethod
    def descriptor(binding):
        return {'sensor_id': 'HF-Sim', 'binding_id': binding['binding_id'] if binding else None,
                'calibration_id': binding['binding_id'] if binding else 'synthetic-uncalibrated',
                'package_checksum': binding['package_checksum'] if binding else None,
                'hardware_calibrated': False, 'pixel_corrections_applied': False}

    def bind(self, binding, fault=None, lease=None):
        lease = self.lease if lease is None else lease
        params = dict(token=lease['token'], epoch=lease['epoch'], binding=binding)
        if fault is not None:
            params['test_fault'] = fault
        return self.client._rpc('bind_calibration', params)

    def clear(self, fault=None, lease=None):
        lease = self.lease if lease is None else lease
        params = dict(token=lease['token'], epoch=lease['epoch'])
        if fault is not None:
            params['test_fault'] = fault
        return self.client._rpc('clear_calibration', params)

    def request(self, **changes):
        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=self.lease['token'],
                       control_epoch=self.lease['epoch'])
        request.update(changes)
        return request

    def capture(self, **changes):
        request = self.request(**changes)
        self.client.capture(request)
        record = self.wait(request['request_id'])
        self.assertEqual('complete', record['status'], record)
        return request, record

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(CameraError) as raised:
            action(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)
        return raised.exception

    def assert_active(self, binding):
        self.assertEqual(self.descriptor(binding), self.client.get_state()['calibration'])
        stored = self.metadata()
        self.assertEqual(FORMAT, stored['calibration_binding_format'])
        self.assertEqual({'format': FORMAT, 'binding': binding}, json.loads(stored['calibration_binding']))

    @staticmethod
    def reseal(binding, payload):
        raw = payload.encode('ascii')
        return dict(binding, package_payload=payload,
                    package_checksum='sha256:' + hashlib.sha256(raw).hexdigest(),
                    binding_id='cb1-' + hashlib.sha256(b'HF-Sim calibration binding v1\n' + raw).hexdigest())

    def test_shared_fixtures_and_noncanonical_spelling_survive_restart_then_clear(self):
        baseline = self.configure({'requested_exposure_us': 24680, 'requested_analogue_gain': 2.5,
                                   'simulation_scene': 'dark'})
        values = [self.fixtures[name] for name in ('uncalibrated', 'synthetic', 'unicode')]
        synthetic = self.fixtures['synthetic']
        alternative = '\n\t' + synthetic['package_payload'].replace('1e-06', '0.000001') + ' \r\n'
        values.append(self.reseal(synthetic, alternative))
        self.assertNotEqual(synthetic['binding_id'], values[-1]['binding_id'])
        for binding in values:
            with self.subTest(binding=binding['binding_id']):
                self.assertEqual(self.descriptor(binding), self.bind(binding))
                self.assert_active(binding)
                self.assertEqual(baseline, self.client.get_state()['settings'])
                self.restart()
                self.assert_active(binding)
                self.assertEqual(baseline, self.client.get_state()['settings'])
        self.assertEqual(self.descriptor(None), self.clear())
        self.restart()
        self.assert_active(None)
        self.assertEqual(baseline, self.client.get_state()['settings'])
        self.assertEqual([], list(self.client.photos.glob('*.dng')))

    def test_bound_capture_snapshots_v2_trace_without_changing_pixels_or_legacy_shape(self):
        baseline = self.configure({'requested_exposure_us': 23456, 'simulation_scene': 'dark'})
        _, unbound = self.capture(requested_exposure_us=10000, requested_analogue_gain=1.5, simulation_scene='flat')
        for field in ('capture_record_version', 'calibration_binding', 'hardware_calibrated', 'pixel_corrections_applied'):
            self.assertNotIn(field, unbound, 'unbound capture must preserve its legacy v1 shape')
        self.assertEqual('synthetic-uncalibrated', unbound['calibration_id'])
        binding = self.fixtures['synthetic']
        self.bind(binding)
        _, bound = self.capture(requested_exposure_us=10000, requested_analogue_gain=1.5, simulation_scene='flat')
        self.assertEqual(2, bound['capture_record_version'])
        self.assertEqual(binding, bound['calibration_binding'])
        self.assertEqual(binding['binding_id'], bound['calibration_id'])
        self.assertIs(bound['hardware_calibrated'], False)
        self.assertIs(bound['pixel_corrections_applied'], False)
        self.assertEqual(binding['binding_id'], bound['native_evidence']['calibration_id'])
        _, _, before_pixels, before_description = _worker._tiff(Path(unbound['file_path']))
        _, _, bound_pixels, bound_description = _worker._tiff(Path(bound['file_path']))
        self.assertEqual(before_pixels, bound_pixels, 'binding unexpectedly applied pixel correction')
        self.assertEqual('synthetic-uncalibrated', before_description['capture']['calibration_id'])
        self.assertEqual(binding['binding_id'], bound_description['capture']['calibration_id'])
        self.assertEqual(baseline, self.client.get_state()['settings'])
        self.restart()
        self.assertEqual(unbound, self.client.query_capture(unbound['request_id']))
        self.assertEqual(bound, self.client.query_capture(bound['request_id']))

    def test_duplicate_request_retains_original_binding_after_selection_changes_and_restart(self):
        first, second = self.fixtures['uncalibrated'], self.fixtures['unicode']
        self.bind(first)
        request, original = self.capture()
        self.bind(second)
        self.assertEqual(original, self.client.capture(request))
        self.assertEqual(first, original['calibration_binding'])
        self.clear()
        self.assertEqual(original, self.client.capture(request))
        self.restart()
        request.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(original, self.client.capture(request))
        self.assertEqual(original, self.client.query_capture(request['request_id']))
        self.assertEqual(1, len(list(self.client.photos.glob('*.dng'))))
        self.assert_active(None)

    def test_bad_binding_is_refused_before_any_state_or_settings_change(self):
        confirmed = self.fixtures['uncalibrated']
        self.bind(confirmed)
        baseline = self.configure({'requested_exposure_us': 22222, 'requested_analogue_gain': 3.0})
        before = self.metadata()
        semantic = json.loads(confirmed['package_payload'])
        semantic['schema_version'] = True
        duplicate = confirmed['package_payload'][:-1] + ',"status":"uncalibrated"}'
        null_suffix = confirmed['package_payload'] + '\0junk'
        cases = [
            ('checksum_mismatch', dict(confirmed, package_payload=confirmed['package_payload'] + ' ')),
            ('checksum_mismatch', dict(confirmed, package_checksum='sha256:' + '0' * 64)),
            ('checksum_mismatch', dict(confirmed, binding_id='cb1-' + '0' * 64)),
            ('sensor_mismatch', dict(confirmed, sensor_id='Other-Sensor')),
            ('invalid_binding', dict(confirmed, binding_schema_version=True)),
            ('invalid_binding', dict(confirmed, hardware_calibrated=True)),
            ('invalid_binding', dict(confirmed, pixel_corrections_applied=True)),
            ('invalid_binding', dict(confirmed, verified=True)),
            ('invalid_package', self.reseal(confirmed, json.dumps(semantic, separators=(',', ':')))),
            ('invalid_package', self.reseal(confirmed, duplicate)),
            ('invalid_binding', self.reseal(confirmed, null_suffix)),
            ('invalid_binding', None),
        ]
        for code, binding in cases:
            with self.subTest(code=code, binding=repr(binding)[:80]):
                self.assert_code(code, self.bind, binding)
                self.assertEqual(before, self.metadata())
                state = self.client.get_state()
                self.assertEqual(self.descriptor(confirmed), state['calibration'])
                self.assertEqual(baseline, state['settings'])
                self.assertTrue(state['journal_healthy'])

    def test_busy_and_stale_control_cannot_bind_or_clear(self):
        first, second = self.fixtures['uncalibrated'], self.fixtures['synthetic']
        self.bind(first)
        before = self.metadata()
        request = self.request(fault='delay')
        self.client.capture(request)
        self.assertEqual('capturing', self.client.get_state()['camera_state'])
        self.assert_code('camera_busy', self.bind, second)
        self.assert_code('camera_busy', self.clear)
        self.assertEqual(before['calibration_binding'], self.metadata()['calibration_binding'])
        self.assertEqual('complete', self.wait(request['request_id'])['status'])
        stale = self.lease.copy()
        self.client.release(stale['token'], stale['epoch'])
        self.lease = self.client.acquire('new-binding-owner')
        self.assert_code('control_denied', self.bind, second, lease=stale)
        self.assert_code('control_denied', self.clear, lease=stale)
        self.assert_active(first)

    def test_binding_and_clear_transaction_failures_roll_back_then_fence(self):
        first, second = self.fixtures['uncalibrated'], self.fixtures['synthetic']
        self.bind(first)
        baseline = self.configure({'requested_exposure_us': 24680})
        for operation in ('bind', 'clear'):
            with self.subTest(operation=operation):
                before = self.metadata()
                if operation == 'bind':
                    self.assert_code('journal_error', self.bind, second, 'binding_commit_failure')
                else:
                    self.assert_code('journal_error', self.clear, 'binding_commit_failure')
                self.assertEqual(before, self.metadata())
                state = self.client.get_state()
                self.assertEqual(self.descriptor(first), state['calibration'])
                self.assertEqual(baseline, state['settings'])
                self.assertFalse(state['journal_healthy'])
                self.assert_code('journal_error', self.bind, second)
                self.restart()
                self.assert_active(first)
                self.assertTrue(self.client.get_state()['journal_healthy'])

    def test_commit_before_memory_crash_recovers_binding_and_clear(self):
        first, second = self.fixtures['uncalibrated'], self.fixtures['unicode']
        baseline = self.configure({'requested_analogue_gain': 2.5, 'simulation_scene': 'flat'})
        self.bind(first)
        with self.assertRaises(CameraError):
            self.bind(second, 'crash_after_binding_commit')
        self.assertEqual(89, self.client.process.wait(timeout=5))
        self.restart()
        self.assert_active(second)
        self.assertEqual(baseline, self.client.get_state()['settings'])
        with self.assertRaises(CameraError):
            self.clear('crash_after_binding_commit')
        self.assertEqual(89, self.client.process.wait(timeout=5))
        self.restart()
        self.assert_active(None)
        self.assertEqual(baseline, self.client.get_state()['settings'])

    def test_tampered_or_partial_persisted_binding_refuses_startup_before_epoch_write(self):
        binding = self.fixtures['uncalibrated']
        self.bind(binding)
        original = self.metadata()
        envelope = {'format': FORMAT, 'binding': binding}
        corrupt = copy.deepcopy(envelope)
        corrupt['binding']['package_payload'] += ' '
        cases = [('{', FORMAT),
                 (json.dumps(corrupt), FORMAT),
                 (json.dumps(dict(envelope, format='hf-active-calibration-v99')), FORMAT),
                 (json.dumps(dict(envelope, extra=True)), FORMAT),
                 ('{"format":"bad",' + json.dumps(envelope)[1:], FORMAT),
                 (json.dumps(envelope) + '\0junk', FORMAT),
                 (sqlite3.Binary(b'not-text'), FORMAT),
                 (json.dumps(envelope), 'hf-active-calibration-v99'),
                 (None, FORMAT), (json.dumps(envelope), None)]
        for value, marker in cases:
            with self.subTest(value=repr(value)[:60], marker=marker):
                self.client.abort()
                self.execute("DELETE FROM meta WHERE key IN ('calibration_binding','calibration_binding_format')")
                if value is not None:
                    self.execute("INSERT INTO meta VALUES('calibration_binding',?)", (value,))
                if marker is not None:
                    self.execute("INSERT INTO meta VALUES('calibration_binding_format',?)", (marker,))
                before = self.metadata()
                unexpected = None
                try:
                    with self.assertRaises(CameraError) as failed:
                        unexpected = self.start()
                    self.assertEqual('journal_error', failed.exception.code)
                finally:
                    if unexpected is not None:
                        unexpected.abort()
                self.assertEqual(before, self.metadata(), 'failed startup rewrote persisted state or epoch')
        self.execute("DELETE FROM meta WHERE key IN ('calibration_binding','calibration_binding_format')")
        self.execute("INSERT INTO meta VALUES('calibration_binding',?)", (original['calibration_binding'],))
        self.execute("INSERT INTO meta VALUES('calibration_binding_format',?)", (original['calibration_binding_format'],))
        self.client = self.start()
        self.assert_active(binding)

    def test_corrupt_committed_capture_snapshot_fences_instead_of_substituting_active_binding(self):
        binding = self.fixtures['synthetic']
        self.bind(binding)
        _, record = self.capture()
        rid = record['request_id']
        with closing(sqlite3.connect(self.root / 'journal.sqlite3')) as database:
            serialized = database.execute('SELECT record_json FROM captures WHERE request_id=?', (rid,)).fetchone()[0]
        replacement = 'sha256:' + '0' * 64
        self.assertIn(binding['package_checksum'], serialized)
        corrupted = serialized.replace(binding['package_checksum'], replacement, 1)
        self.execute('UPDATE captures SET record_json=? WHERE request_id=?', (corrupted, rid))
        self.assert_code('journal_error', self.client.query_capture, rid)
        state = self.client.get_state()
        self.assertFalse(state['journal_healthy'])
        self.assertEqual(self.descriptor(binding), state['calibration'])
        self.assert_code('journal_error', self.client.capture, self.request())
        self.client.abort()
        self.client = self.start()
        self.assertFalse(self.client.get_state()['journal_healthy'])
        self.assert_code('journal_error', self.client.query_capture, rid)
        with closing(sqlite3.connect(self.root / 'journal.sqlite3')) as database:
            self.assertEqual(corrupted, database.execute('SELECT record_json FROM captures WHERE request_id=?', (rid,)).fetchone()[0])
            self.assertEqual(1, database.execute('SELECT COUNT(*) FROM captures').fetchone()[0])

    def test_calibration_crash_and_rollback_faults_require_explicit_test_flag(self):
        self.client.close()
        self.client = self.start(faults=False)
        self.lease = self.client.acquire('ordinary-calibration-client')
        before = self.metadata()
        for operation in ('bind', 'clear'):
            for fault in ('binding_commit_failure', 'crash_after_binding_commit'):
                with self.subTest(operation=operation, fault=fault):
                    if operation == 'bind':
                        self.assert_code('test_fault_disabled', self.bind, self.fixtures['uncalibrated'], fault)
                    else:
                        self.assert_code('test_fault_disabled', self.clear, fault)
                    self.assertEqual(before, self.metadata())
                    self.assertEqual(self.descriptor(None), self.client.get_state()['calibration'])
                    self.assertTrue(self.client.get_state()['journal_healthy'])


if __name__ == '__main__':
    unittest.main()

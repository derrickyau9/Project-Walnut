"""Actual native process/SQLite proofs for durable configured device settings."""
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from camera.camerad.native_client import NativeClient
from camera.camerad.service import CameraError, DEFAULTS
from tests import test_native_daemon as native_tests


FORMAT = 'hf-device-settings-v1'


class NativeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native_tests.NativeDaemonTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): native_tests.NativeDaemonTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-native-settings-')
        self.root = Path(self.temporary.name)
        self.client = self.start()
        self.lease = self.client.acquire('durable-settings-test')

    def tearDown(self):
        self.client.abort(); self.temporary.cleanup()

    def start(self, faults=True):
        return NativeClient(self.root, command=[str(self.executable)] + (['--enable-test-faults'] if faults else []), timeout_seconds=5)

    def restart(self):
        self.client.abort(); self.client = self.start(); self.lease = self.client.acquire('settings-after-restart')

    def configure(self, settings, fault=None):
        params = dict(token=self.lease['token'], epoch=self.lease['epoch'], settings=settings)
        if fault is not None: params['test_fault'] = fault
        return self.client._rpc('configure', params)

    def metadata(self):
        with closing(sqlite3.connect(self.root / 'journal.sqlite3')) as database:
            return dict(database.execute('SELECT key,value FROM meta'))

    def execute(self, sql, args=()):
        with closing(sqlite3.connect(self.root / 'journal.sqlite3')) as database:
            database.execute(sql, args); database.commit()

    def test_restart_and_abrupt_process_death_keep_last_configured_baseline(self):
        accepted = self.configure({'requested_exposure_us':23456, 'requested_analogue_gain':2.5, 'simulation_scene':'flat'})
        stored = self.metadata()
        self.assertEqual({'format':FORMAT, 'settings':accepted}, json.loads(stored['settings']))
        self.assertEqual(FORMAT, stored['settings_format'])
        old_lease = self.lease.copy()
        self.restart()
        self.assertEqual(accepted, self.client.get_state()['settings'])
        self.assertGreater(self.lease['epoch'], old_lease['epoch'])
        with self.assertRaises(CameraError) as denied:
            self.client.configure(old_lease['token'], old_lease['epoch'], {'requested_exposure_us':34567})
        self.assertEqual('control_denied', denied.exception.code)
        self.assertEqual(stored['settings'], self.metadata()['settings'])

    def test_commit_before_memory_crash_recovers_committed_settings(self):
        expected = dict(DEFAULTS, requested_exposure_us=34567, simulation_scene='dark')
        with self.assertRaises(CameraError): self.configure({'requested_exposure_us':34567, 'simulation_scene':'dark'}, 'crash_after_settings_commit')
        self.assertEqual(88, self.client.process.wait(timeout=5))
        self.restart()
        self.assertEqual(expected, self.client.get_state()['settings'])

    def test_failed_transaction_keeps_confirmed_memory_and_row_then_fences(self):
        accepted = self.configure({'requested_exposure_us':24680})
        before = self.metadata()['settings']
        with self.assertRaises(CameraError) as failed:
            self.configure({'requested_exposure_us':13579}, 'settings_commit_failure')
        self.assertEqual('journal_error', failed.exception.code)
        self.assertEqual(accepted, self.client.get_state()['settings'])
        self.assertFalse(self.client.get_state()['journal_healthy'])
        self.assertEqual(before, self.metadata()['settings'])
        with self.assertRaises(CameraError): self.configure({'requested_exposure_us':22222})
        self.restart()
        self.assertEqual(accepted, self.client.get_state()['settings'])

    def test_real_sqlite_write_failure_does_not_claim_success(self):
        accepted = self.configure({'requested_analogue_gain':3.0})
        before = self.metadata()['settings']
        self.execute("CREATE TRIGGER deny_settings BEFORE INSERT ON meta WHEN NEW.key='settings' BEGIN SELECT RAISE(ABORT,'test storage rejection'); END")
        with self.assertRaises(CameraError) as failed: self.configure({'requested_exposure_us':7777})
        self.assertEqual('journal_error', failed.exception.code)
        self.assertEqual(accepted, self.client.get_state()['settings'])
        self.assertFalse(self.client.get_state()['journal_healthy'])
        self.assertEqual(before, self.metadata()['settings'])
        self.execute('DROP TRIGGER deny_settings')
        self.restart()
        self.assertEqual(accepted, self.client.get_state()['settings'])

    def test_explicit_capture_success_failure_and_duplicate_do_not_change_baseline(self):
        accepted = self.configure({'requested_exposure_us':24680, 'requested_analogue_gain':3.0, 'simulation_scene':'flat'})
        for fault in (None, 'storage_full'):
            request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=self.lease['token'], control_epoch=self.lease['epoch'])
            if fault: request['fault'] = fault
            self.client.capture(request)
            end = time.monotonic() + 5
            while time.monotonic() < end:
                result = self.client.query_capture(request['request_id'])
                if result['status'] in {'complete','failed'} and self.client.get_state()['camera_state'] == 'idle': break
                time.sleep(.01)
            else: self.fail('capture never settled')
            self.assertEqual('failed' if fault else 'complete', result['status'])
            self.assertEqual(result, self.client.capture(request))
            self.assertEqual(accepted, self.client.get_state()['settings'])
        self.restart()
        self.assertEqual(accepted, self.client.get_state()['settings'])

    def test_legacy_migration_requires_both_rows_absent(self):
        self.client.close()
        self.execute("DELETE FROM meta WHERE key IN ('settings','settings_format')")
        self.client = self.start()
        self.assertEqual(DEFAULTS, self.client.get_state()['settings'])
        self.assertEqual(FORMAT, self.metadata()['settings_format'])
        self.assertEqual(DEFAULTS, json.loads(self.metadata()['settings'])['settings'])

    def test_invalid_or_partial_persisted_state_refuses_without_reset_or_epoch_write(self):
        envelope = {'format':FORMAT, 'settings':DEFAULTS}
        original = self.metadata()
        cases = [('{', FORMAT),
                 (json.dumps(dict(envelope, format='hf-device-settings-v99')), FORMAT),
                 (json.dumps(dict(envelope, extra=True)), FORMAT),
                 (json.dumps({'format':FORMAT,'settings':dict(DEFAULTS, requested_exposure_us=True)}), FORMAT),
                 (json.dumps({'format':FORMAT,'settings':dict(DEFAULTS, requested_analogue_gain=float('nan'))}), FORMAT),
                 (json.dumps({'format':FORMAT,'settings':{k:v for k,v in DEFAULTS.items() if k!='simulation_scene'}}), FORMAT),
                 ('{"format":"bad",' + json.dumps(envelope)[1:], FORMAT),
                 (json.dumps(envelope) + '\0junk', FORMAT), (' ' * 16385, FORMAT),
                 (json.dumps(envelope), 'hf-device-settings-v99'), (None, FORMAT), (json.dumps(envelope), None)]
        for value, marker in cases:
            with self.subTest(value=repr(value)[:60], marker=marker):
                self.client.abort()
                self.execute("DELETE FROM meta WHERE key IN ('settings','settings_format')")
                if value is not None: self.execute("INSERT INTO meta VALUES('settings',?)", (value,))
                if marker is not None: self.execute("INSERT INTO meta VALUES('settings_format',?)", (marker,))
                before = self.metadata()
                unexpected = None
                try:
                    with self.assertRaises(CameraError) as failed: unexpected = self.start()
                    self.assertEqual('journal_error', failed.exception.code)
                finally:
                    if unexpected is not None: unexpected.abort()
                self.assertEqual(before, self.metadata())
        self.execute("DELETE FROM meta WHERE key IN ('settings','settings_format')")
        self.execute("INSERT INTO meta VALUES('settings',?)", (original['settings'],))
        self.execute("INSERT INTO meta VALUES('settings_format',?)", (original['settings_format'],))
        self.client = self.start()

    def test_test_faults_are_disabled_without_explicit_flag(self):
        self.client.close(); self.client = self.start(faults=False); self.lease = self.client.acquire('ordinary-client')
        before = self.client.get_state()['settings']
        with self.assertRaises(CameraError): self.configure({'requested_exposure_us':12345}, 'crash_after_settings_commit')
        self.assertEqual(before, self.client.get_state()['settings'])
        self.assertTrue(self.client.get_state()['journal_healthy'])


if __name__ == '__main__': unittest.main()

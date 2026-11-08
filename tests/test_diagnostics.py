"""Real journal/filesystem fixtures for the non-mutating diagnostic tool."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from camera.calibration import CalibrationStore, create_uncalibrated_package
from camera.camerad.service import CameraService
from camera.dng.hf_dng import write_dng
from daos.capability_runtime.bracket import BracketRunner
from tools.diagnostics import diagnose


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def rid(number):
    return str(uuid.UUID(int=number))


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'camera'
        self.root.mkdir()
        (self.root / 'photos').mkdir()
        (self.root / '.service.lock').write_bytes(b'0')
        with database(self.root / 'journal.sqlite3') as db:
            db.executescript('''
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE captures (request_id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                                       record_json TEXT NOT NULL, status TEXT NOT NULL);
            ''')

    def record(self, number, status='complete', **changes):
        request_id = rid(number)
        record = {'request_id': request_id, 'status': status,
                  'file_path': str(self.root / 'photos' / (request_id + '.dng')) if status == 'complete' else None}
        record.update(changes)
        with database(self.root / 'journal.sqlite3') as db:
            db.execute('INSERT INTO captures VALUES(?,?,?,?)',
                       (request_id, '{}', json.dumps(record), status))
        return request_id

    def dng(self, number, *, metadata_id=None, partial=False):
        request_id = rid(number)
        path = self.root / 'photos' / (request_id + '.dng' + ('.partial' if partial else ''))
        write_dng(path, 4, 4, [64] * 16,
                  metadata={'request_id': metadata_id if metadata_id is not None else request_id})
        return path

    def snapshot(self, root=None):
        result = {}
        for path in sorted((root or self.root).rglob('*')):
            info = path.lstat()
            if path.is_file():
                # An OS-held service lock can prevent reading its locked byte.
                digest = None if path.name.endswith('.lock') else hashlib.sha256(path.read_bytes()).hexdigest()
                result[str(path)] = (info.st_size, info.st_mtime_ns, digest)
            else:
                result[str(path)] = ('directory', info.st_mtime_ns)
        return result

    def checked(self, **kwargs):
        before = self.snapshot()
        result = diagnose(self.root, **kwargs)
        self.assertEqual(before, self.snapshot(), 'diagnostics changed its input tree')
        self.assertEqual(result, diagnose(self.root, **kwargs), 'unchanged inputs must produce deterministic reports')
        return result

    def codes(self, result):
        return {issue['code'] for issue in result['issues']}

    def test_valid_complete_is_read_only_and_deterministic(self):
        self.record(1)
        self.dng(1)
        result = self.checked(offline_only=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual('acquired', result['consistency']['service_lock'])
        self.assertEqual('ok', result['camera_journal']['integrity'])
        self.assertEqual('stable', result['camera_journal']['snapshot'])
        self.assertEqual('matches', result['files'][0]['dng']['request_identity'])

    def test_empty_native_lock_is_read_only_and_exclusive(self):
        # Native CreateFile/flock ownership does not need a marker byte.
        # Windows supports byte-range locking beyond EOF without extending it.
        (self.root / '.service.lock').write_bytes(b'')
        self.record(1)
        self.dng(1)
        result = self.checked(offline_only=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual('acquired', result['consistency']['service_lock'])
        self.assertEqual(0, (self.root / '.service.lock').stat().st_size)

    def test_missing_complete_and_corrupt_dng_are_errors(self):
        self.record(1)
        self.record(2)
        self.dng(2).write_bytes(b'not a TIFF file')
        result = self.checked()
        self.assertIn('missing_complete_file', self.codes(result))
        self.assertIn('invalid_dng_structure', self.codes(result))
        missing = [i['request_id'] for i in result['issues'] if i['code'] == 'missing_complete_file']
        self.assertEqual([rid(1)], missing)

    def test_unknown_accepted_orphan_and_partial_keep_request_relations(self):
        self.record(1, 'accepted')
        self.record(2, 'unknown')
        self.dng(1)
        self.dng(2, partial=True)
        self.dng(3)
        self.dng(4, partial=True)
        result = self.checked()
        self.assertEqual(['accepted', 'unknown'], [c['status'] for c in result['captures']])
        self.assertEqual(['recorded_request', 'recorded_request', 'orphan', 'orphan'],
                         [f['relation'] for f in result['files']])
        self.assertTrue({'unresolved_capture', 'final_file_without_confirmed_completion',
                         'orphan_final_file', 'orphan_partial_file', 'partial_file'} <= self.codes(result))

    def test_dng_request_identity_mismatch_is_reported(self):
        self.record(1)
        self.dng(1, metadata_id=rid(2))
        result = self.checked()
        self.assertIn('dng_request_identity_unconfirmed', self.codes(result))
        self.assertEqual('mismatch_or_missing', result['files'][0]['dng']['request_identity'])

    def test_recorded_path_must_stay_in_photos_and_match_request(self):
        external = Path(self.temporary.name) / 'outside.dng'
        external.write_bytes(b'do not read or modify this external file')
        self.record(1, file_path=str(external))
        self.record(2, file_path=str(self.root / 'photos' / (rid(3) + '.dng')))
        before = self.snapshot(Path(self.temporary.name))
        result = self.checked()
        self.assertIn('output_path_outside_photos', self.codes(result))
        self.assertIn('output_path_request_mismatch', self.codes(result))
        self.assertEqual(before, self.snapshot(Path(self.temporary.name)))
        self.assertNotIn(str(external), json.dumps(result))

    def test_corrupt_journal_is_reported_without_repair_or_false_orphans(self):
        (self.root / 'journal.sqlite3').write_bytes(b'SQLite format 3\0' + b'broken' * 100)
        self.dng(7)
        result = self.checked()
        self.assertEqual('failed', result['camera_journal']['integrity'])
        self.assertIn('sqlite_unreadable_or_schema_invalid', self.codes(result))
        self.assertEqual('journal_unavailable', result['files'][0]['relation'])
        self.assertNotIn('orphan_final_file', self.codes(result))

    def test_malformed_record_is_diagnosed_and_raw_content_never_echoed(self):
        secret = 'secret-lease-token-must-never-appear'
        self.record(1, 'unknown', token=secret, error=secret, arbitrary={'token': secret})
        with database(self.root / 'journal.sqlite3') as db:
            db.execute('INSERT INTO captures VALUES(?,?,?,?)', (rid(2), secret, secret, 'unknown'))
        result = self.checked()
        self.assertIn('invalid_capture_record_json', self.codes(result))
        self.assertNotIn(secret, json.dumps(result))

    def test_live_service_lock_and_wal_are_observed_without_source_writes(self):
        service = CameraService(self.root)
        self.addCleanup(service.close)
        secret = service.acquire('diagnostic-test')['token']
        request_id = rid(1)
        record = {'request_id': request_id, 'status': 'accepted', 'file_path': None, 'token': secret}
        service.db.execute('INSERT INTO captures VALUES(?,?,?,?)',
                           (request_id, '{}', json.dumps(record), 'accepted'))
        service.db.commit()
        result = self.checked()
        self.assertEqual('held_or_unavailable', result['consistency']['service_lock'])
        self.assertEqual('accepted', result['captures'][0]['status'])
        self.assertEqual('ok', result['camera_journal']['integrity'])
        self.assertIn('service_may_be_active', self.codes(result))
        self.assertNotIn(secret, json.dumps(result))
        refused = self.checked(offline_only=True)
        self.assertIn('offline_exclusion_unavailable', self.codes(refused))
        self.assertEqual([], refused['captures'])
        # This deliberately incomplete diagnostic fixture is not a valid camera
        # record. Inspect the SQL value directly: a normal authority query now
        # correctly fences malformed records, while diagnostics must not repair it.
        stored = service.db.execute('SELECT record_json,status FROM captures WHERE request_id=?',
                                    (request_id,)).fetchone()
        self.assertEqual(json.dumps(record), stored['record_json'])
        self.assertEqual('accepted', stored['status'])

    def test_missing_root_and_lock_never_created(self):
        missing = Path(self.temporary.name) / 'does-not-exist'
        result = diagnose(missing, offline_only=True, calibration_root=missing / 'calibration')
        self.assertFalse(missing.exists())
        self.assertIn('offline_exclusion_unavailable', self.codes(result))
        (self.root / '.service.lock').unlink()
        result = self.checked()
        self.assertFalse((self.root / '.service.lock').exists())
        self.assertEqual('missing', result['consistency']['service_lock'])

    def test_workflow_unresolved_ids_and_camera_mismatch(self):
        journal = self.root / 'workflows.sqlite3'
        runner = BracketRunner(None, journal)
        runner.prepare('interrupted', {'exposures_us': [5000, 10000]})
        runner._reserve('interrupted', 0, rid(1))
        runner._set_step('interrupted', 1, rid(2), 'completed')
        runner._set_job('interrupted', 'unknown')
        self.record(1, 'unknown', error='private control token')
        result = self.checked()
        self.assertEqual(rid(1), result['workflow']['steps'][0]['request_id'])
        self.assertEqual('unknown', result['workflow']['steps'][0]['camera_status'])
        self.assertTrue({'unresolved_workflow_job', 'unresolved_workflow_request',
                         'workflow_capture_unknown', 'workflow_completion_unconfirmed'} <= self.codes(result))
        self.assertNotIn('private control token', json.dumps(result))

    def test_workflow_cli_parent_root_is_detected(self):
        parent = Path(self.temporary.name) / 'workflow-cli'
        parent.mkdir()
        self.root.rename(parent / 'simulator')
        self.root = parent / 'simulator'
        runner = BracketRunner(None, parent / 'workflow.sqlite3')
        runner.prepare('queued', {'exposures_us': [5000]})
        before = self.snapshot(parent)
        result = diagnose(parent)
        self.assertEqual(str(self.root), result['data_dir'])
        self.assertEqual('queued', result['workflow']['jobs'][0]['job_id'])
        self.assertEqual(before, self.snapshot(parent))

    def test_calibration_reuses_checksum_schema_and_identity_validation(self):
        root = self.root / 'calibration'
        store = CalibrationStore(root)
        package = create_uncalibrated_package('sensor', 'factory', '1', reason='private reason')
        store.put(package, expected_sensor_id='sensor')
        result = self.checked(calibration_root=root)
        self.assertEqual('valid', result['calibration']['packages'][0]['state'])
        self.assertFalse(result['calibration']['packages'][0]['hardware_calibrated'])
        self.assertNotIn('private reason', json.dumps(result))
        path = root / 'sensor' / 'factory' / '1.json'
        changed = json.loads(path.read_text())
        changed['checksum'] = 'sha256:' + '0' * 64
        path.write_text(json.dumps(changed))
        result = self.checked(calibration_root=root)
        self.assertIn('calibration_checksum_mismatch', self.codes(result))
        missing = root / 'missing'
        result = self.checked(calibration_root=missing)
        self.assertIn('calibration_root_missing', self.codes(result))
        self.assertFalse(missing.exists())

    def test_hot_rollback_journal_is_identified_without_recovery(self):
        # Spill an uncommitted transaction, then exit without SQLite cleanup.
        child_code = (
            'import os,sqlite3,sys\n'
            'db=sqlite3.connect(sys.argv[1]); db.execute("PRAGMA cache_size=1")\n'
            'db.execute("BEGIN IMMEDIATE")\n'
            'db.execute("INSERT INTO captures VALUES(?,?,?,?)",'
            '(sys.argv[2],"{}","x"*100000,"accepted"))\n'
            'os._exit(0)\n'
        )
        completed = subprocess.run([sys.executable, '-B', '-c', child_code,
                                    str(self.root / 'journal.sqlite3'), rid(1)],
                                   text=True, capture_output=True, timeout=20)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue((self.root / 'journal.sqlite3-journal').is_file())
        result = self.checked()
        self.assertIn('sqlite_recovery_required', self.codes(result))
        self.assertEqual('recovery_required', result['camera_journal']['integrity'])

    def test_changed_database_copy_is_inconclusive(self):
        from tools import diagnostics
        original_copy = diagnostics.shutil.copyfile
        self.record(1)  # A mixed snapshot must not assert this complete file is missing.
        changed = False
        def interleaved_copy(source, target):
            nonlocal changed
            result = original_copy(source, target)
            if not changed and Path(source) == self.root / 'journal.sqlite3':
                changed = True
                self.record(4, 'accepted')
            return result
        with patch('tools.diagnostics.shutil.copyfile', side_effect=interleaved_copy):
            result = diagnose(self.root)
        self.assertIn('journal_changed_during_snapshot', self.codes(result))
        self.assertEqual('inconclusive', result['camera_journal']['integrity'])
        self.assertEqual('changed', result['camera_journal']['snapshot'])
        self.assertEqual([], result['captures'])
        self.assertNotIn('missing_complete_file', self.codes(result))

    def test_unreadable_calibration_subdirectory_is_not_silently_skipped(self):
        from tools import diagnostics
        root = self.root / 'calibration'
        store = CalibrationStore(root)
        store.put(create_uncalibrated_package('sensor', 'factory', '1'), expected_sensor_id='sensor')
        original_scandir = diagnostics.os.scandir
        def blocked_scandir(path):
            # POSIX shutil cleanup also calls scandir with an open directory FD.
            if not isinstance(path, int) and Path(path) == root / 'sensor':
                raise PermissionError('private-path-must-not-be-echoed')
            return original_scandir(path)
        before = self.snapshot()
        with patch('tools.diagnostics.os.scandir', side_effect=blocked_scandir):
            result = diagnose(self.root, calibration_root=root)
        self.assertEqual(before, self.snapshot())
        self.assertEqual('unavailable', result['calibration']['state'])
        self.assertIn('calibration_unreadable', self.codes(result))
        self.assertNotIn('private-path-must-not-be-echoed', json.dumps(result))


    def test_unrecognized_output_names_are_not_copied_into_report(self):
        secret = 'secret-token-in-a-filename'
        (self.root / 'photos' / secret).write_text('arbitrary file content')
        result = self.checked()
        self.assertIn('unrecognized_output_entry', self.codes(result))
        self.assertNotIn(secret, json.dumps(result))


    def test_cli_emits_json_and_warning_error_exit_codes(self):
        self.record(1, 'unknown')
        before = self.snapshot()
        command = [sys.executable, '-B', '-m', 'tools.diagnostics', '--data-dir', str(self.root)]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=20)
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(0, json.loads(completed.stdout)['summary']['errors'])
        self.assertEqual(before, self.snapshot())
        (self.root / 'journal.sqlite3').write_bytes(b'broken database')
        completed = subprocess.run(command, text=True, capture_output=True, timeout=20)
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertGreater(json.loads(completed.stdout)['summary']['errors'], 0)


if __name__ == '__main__':
    unittest.main()

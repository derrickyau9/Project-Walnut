"""Real-process tests for the native synthetic camera control authority."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
import uuid
from contextlib import closing

from camera.camerad.native_client import NativeClient
from camera.camerad.service import CameraError, DEFAULTS
from camera.dng.inspect import inspect_dng
from tools.build_native import find_cmake


ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'complete', 'failed', 'cancelled', 'unknown'}


class NativeDaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._build_directory = None
        configured = os.environ.get('HF_NATIVE_DAEMON')
        if configured:
            cls.executable = Path(configured).resolve()
            if not cls.executable.is_file():
                raise RuntimeError('HF_NATIVE_DAEMON does not point to a file')
            return
        filename = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
        for candidate in (ROOT / 'build/native/Release' / filename, ROOT / 'build/native' / filename):
            if candidate.is_file():
                cls.executable = candidate
                return
        try:
            cmake = find_cmake()
        except RuntimeError as error:
            raise unittest.SkipTest(str(error) + '; set HF_NATIVE_DAEMON to a built daemon')
        cls._build_directory = tempfile.TemporaryDirectory(prefix='hf-daemon-tests-')
        build = Path(cls._build_directory.name)
        configure = [str(cmake), '-S', str(ROOT / 'camera/native'), '-B', str(build),
                     '-DHF_BUILD_DAEMON=ON', '-DCMAKE_BUILD_TYPE=Release']
        if os.name == 'nt':
            configure += ['-G', 'Visual Studio 17 2022', '-A', 'x64']
        for command in (configure, [str(cmake), '--build', str(build), '--config', 'Release',
                                    '--target', 'hf-camerad', '--parallel']):
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if completed.returncode:
                cls._build_directory.cleanup()
                raise RuntimeError('native daemon build failed:\n' + completed.stdout + completed.stderr)
        cls.executable = next((path for path in (build / 'Release' / filename, build / filename) if path.is_file()), None)
        if cls.executable is None:
            cls._build_directory.cleanup()
            raise RuntimeError('native daemon build produced no executable')

    @classmethod
    def tearDownClass(cls):
        if cls._build_directory is not None:
            cls._build_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hf-native-daemon-')
        self.client = self.start_client()
        self.lease = self.client.acquire('native-integration-test')

    def tearDown(self):
        self.client.abort()
        self.directory.cleanup()

    def start_client(self, *, lease_seconds=30, test_faults=True):
        command = [str(self.executable)] + (['--enable-test-faults'] if test_faults else [])
        return NativeClient(self.directory.name, command=command, lease_seconds=lease_seconds, timeout_seconds=5)

    def request(self, **changes):
        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=self.lease['token'],
                       control_epoch=self.lease['epoch'])
        request.update(changes)
        return request

    def wait(self, request_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.client.query_capture(request_id)
            if record and record['status'] in TERMINAL:
                # A terminal record may become visible just before the worker
                # releases its slot; the state query synchronizes that handoff.
                if self.client.get_state()['camera_state'] != 'capturing':
                    return record
            time.sleep(.01)
        self.fail('native capture did not reach a terminal state')

    def restart(self):
        self.client.abort()
        self.client = self.start_client()
        self.lease = self.client.acquire('after-restart')

    def test_capture_identity_metadata_and_no_duplicate_execution(self):
        request = self.request()
        accepted = self.client.capture(request)
        self.assertEqual(accepted['request_id'], request['request_id'])
        complete = self.wait(request['request_id'])
        self.assertEqual(complete['status'], 'complete')
        self.assertEqual(complete['frame_id'], request['request_id'])
        self.assertEqual(complete['capture_id'], request['request_id'])
        self.assertEqual(complete['actual_exposure_us'], request['requested_exposure_us'])
        self.assertEqual(complete['actual_analogue_gain'], request['requested_analogue_gain'])
        self.assertEqual(self.client.capture(request)['status'], 'complete')
        files = list(self.client.photos.glob('*.dng'))
        self.assertEqual(len(files), 1)
        dng = inspect_dng(files[0])
        self.assertEqual(dng['request_id'], request['request_id'])
        self.assertEqual(dng['metadata_state'], 'valid')
        self.assertEqual((dng['width'], dng['height']), (128, 96))
        with self.assertRaises(CameraError) as error:
            self.client.capture(dict(request, requested_exposure_us=20000))
        self.assertEqual(error.exception.code, 'request_conflict')
        state = self.client.get_state()
        self.assertEqual(state['backend'], 'native')
        self.assertEqual(state['authority'], 'native')
        self.assertTrue(state['simulated'])
        self.assertIn('pipeline_generation', state)
        self.assertNotIn('pixels', state)
        self.assertLess(len(json.dumps(state)), 65536)

    def test_real_process_exclusivity_and_epoch_fencing(self):
        with self.assertRaises(CameraError) as error:
            self.start_client()
        self.assertEqual(error.exception.code, 'already_running')
        previous = dict(self.lease)
        self.client.release(previous['token'], previous['epoch'])
        self.lease = self.client.acquire('next-owner')
        self.assertGreater(self.lease['epoch'], previous['epoch'])
        with self.assertRaises(CameraError) as error:
            self.client.configure(previous['token'], previous['epoch'], {})
        self.assertEqual(error.exception.code, 'control_denied')
        epoch = self.lease['epoch']
        self.client.close()
        self.client = self.start_client()
        self.lease = self.client.acquire('next-process')
        self.assertGreater(self.lease['epoch'], epoch)
        with self.assertRaises(CameraError):
            self.client.heartbeat(previous['token'], previous['epoch'])

    def test_real_lease_renewal_expiry_and_new_owner(self):
        self.client.close()
        self.client = self.start_client(lease_seconds=.6)
        old = self.client.acquire('short-lease')
        time.sleep(.15)
        self.client.heartbeat(old['token'], old['epoch'])
        self.assertEqual(self.client.get_state()['control']['owner'], 'short-lease')
        with self.assertRaises(CameraError) as error:
            self.client.acquire('too-early')
        self.assertEqual(error.exception.code, 'control_busy')
        deadline = time.monotonic() + 3
        while self.client.get_state()['control'] is not None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(self.client.get_state()['control'])
        with self.assertRaises(CameraError) as error:
            self.client.heartbeat(old['token'], old['epoch'])
        self.assertEqual(error.exception.code, 'control_denied')
        self.lease = self.client.acquire('after-expiry')
        self.assertGreater(self.lease['epoch'], old['epoch'])

    def test_capture_slot_cancel_deadline_and_close_busy(self):
        request = self.request(fault='delay')
        self.client.capture(request)
        with self.assertRaises(CameraError) as error:
            self.client.close()
        self.assertEqual(error.exception.code, 'camera_busy')
        self.assertIsNone(self.client.process.poll())
        with self.assertRaises(CameraError) as error:
            self.client.capture(self.request())
        self.assertEqual(error.exception.code, 'camera_busy')
        self.assertTrue(self.client.cancel_capture(self.lease['token'], self.lease['epoch'], request['request_id'])['requested'])
        self.assertEqual(self.wait(request['request_id'])['status'], 'cancelled')
        expired = self.request(fault='delay', deadline_unix_ms=int(time.time() * 1000) + 100)
        self.client.capture(expired)
        self.assertEqual(self.wait(expired['request_id'])['status'], 'failed')
        self.assertFalse(list(self.client.photos.glob('*.dng')))

    def test_disconnect_and_authorized_reconnect(self):
        request = self.request(fault='disconnect')
        self.client.capture(request)
        self.assertEqual(self.wait(request['request_id'])['status'], 'failed')
        self.assertFalse(self.client.get_state()['connected'])
        with self.assertRaises(CameraError) as error:
            self.client.capture(self.request())
        self.assertEqual(error.exception.code, 'device_disconnected')
        with self.assertRaises(CameraError):
            self.client.reconnect('wrong-token', self.lease['epoch'])
        self.assertTrue(self.client.reconnect(self.lease['token'], self.lease['epoch'])['connected'])
        recovered = self.request()
        self.client.capture(recovered)
        self.assertEqual(self.wait(recovered['request_id'])['status'], 'complete')

    def test_accepted_request_survives_process_kill_as_unknown(self):
        request = self.request(fault='delay')
        self.client.capture(request)
        previous_epoch = self.lease['epoch']
        self.restart()
        self.assertGreater(self.lease['epoch'], previous_epoch)
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'unknown')
        request.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(self.client.capture(request)['status'], 'unknown')
        self.assertFalse(list(self.client.photos.glob('*.dng')))

    def test_crash_after_admission_is_unknown_and_never_reexecuted(self):
        request = self.request(fault='crash_after_accept')
        with self.assertRaises(CameraError) as error:
            self.client.capture(request)
        self.assertEqual(error.exception.code, 'outcome_unknown')
        self.assertEqual(getattr(error.exception, 'request_id', None), request['request_id'])
        self.restart()
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'unknown')
        request.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(self.client.capture(request)['status'], 'unknown')
        self.assertFalse(list(self.client.photos.glob('*.dng')))

    def test_crash_after_rename_is_unknown_with_exactly_one_existing_dng(self):
        request = self.request(fault='crash_after_rename')
        try:
            self.client.capture(request)
        except CameraError as error:
            self.assertEqual(error.code, 'outcome_unknown')
        self.client.process.wait(timeout=10)
        self.restart()
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'unknown')
        files = list(self.client.photos.glob('*.dng'))
        self.assertEqual(len(files), 1)
        before = files[0].read_bytes()
        request.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(self.client.capture(request)['status'], 'unknown')
        self.assertEqual(list(self.client.photos.glob('*.dng')), files)
        self.assertEqual(files[0].read_bytes(), before)
        self.assertFalse(list(self.client.photos.glob('*.partial')))

    def test_worker_start_failure_is_journaled_and_releases_slot(self):
        request = self.request(fault='worker_start_failure')
        with self.assertRaises(CameraError) as error:
            self.client.capture(request)
        self.assertEqual(error.exception.code, 'worker_start_failed')
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'failed')
        self.assertEqual(self.client.capture(request)['status'], 'failed')
        following = self.request()
        self.client.capture(following)
        self.assertEqual(self.wait(following['request_id'])['status'], 'complete')

    def test_failed_admission_commit_fences_authority_without_execution(self):
        request = self.request(fault='admission_commit_failure')
        with self.assertRaises(CameraError) as error:
            self.client.capture(request)
        self.assertEqual(error.exception.code, 'journal_error')
        self.assertFalse(self.client.get_state()['journal_healthy'])
        self.assertIsNone(self.client.query_capture(request['request_id']))
        self.assertFalse(list(self.client.photos.glob('*.dng')))
        with self.assertRaises(CameraError) as error:
            self.client.capture(self.request())
        self.assertEqual(error.exception.code, 'journal_error')
        self.restart()
        self.assertIsNone(self.client.query_capture(request['request_id']))

    def test_failed_completion_commit_is_unknown_after_rename(self):
        request = self.request(fault='completion_commit_failure')
        self.client.capture(request)
        record = self.wait(request['request_id'])
        self.assertEqual(record['status'], 'unknown')
        self.assertFalse(self.client.get_state()['journal_healthy'])
        self.assertEqual(len(list(self.client.photos.glob('*.dng'))), 1)
        self.restart()
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'unknown')
        request.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(self.client.capture(request)['status'], 'unknown')
        self.assertEqual(len(list(self.client.photos.glob('*.dng'))), 1)

    def test_dangerous_faults_require_explicit_cli_flag(self):
        self.client.close()
        self.client = self.start_client(test_faults=False)
        self.lease = self.client.acquire('normal-client')
        request = self.request(fault='crash_after_accept')
        with self.assertRaises(CameraError) as error:
            self.client.capture(request)
        self.assertEqual(error.exception.code, 'test_fault_disabled')
        self.assertIsNone(self.client.query_capture(request['request_id']))
        self.assertTrue(self.client.get_state()['journal_healthy'])

    def test_corrupt_persisted_rows_fence_queries_and_admission_without_rewriting(self):
        request = self.request()
        self.client.capture(request)
        self.assertEqual(self.wait(request['request_id'])['status'], 'complete')
        final = self.client.photos / (request['request_id'] + '.dng')
        original_file = final.read_bytes()
        self.client.close()
        journal = Path(self.directory.name) / 'journal.sqlite3'
        with closing(sqlite3.connect(journal)) as database:
            original_payload, original_text, original_status = database.execute(
                'SELECT request_json,record_json,status FROM captures WHERE request_id=?',
                (request['request_id'],)).fetchone()
        original = json.loads(original_text)

        def encoded(changes):
            return json.dumps(dict(original, **changes), separators=(',', ':'), sort_keys=True)

        corruptions = {
            'missing_outcome_fields': json.dumps({'request_id': request['request_id'], 'status': 'complete',
                                                  'file_path': 'missing-audit-file.dng'}),
            'wrong_record_uuid': encoded({'request_id': str(uuid.uuid4())}),
            'status_column_mismatch': encoded({'status': 'failed'}),
            'changed_immutable_exposure': encoded({'requested_exposure_us': 20000}),
            'changed_actual_exposure': encoded({'actual_exposure_us': 20000}),
            'outside_photo_path': encoded({'file_path': str(Path(self.directory.name).parent / 'outside.dng')}),
            'malformed_json': '{',
            # Last-key-wins parsing would otherwise hide the first status.
            'duplicate_json_key': '{"status":"invented",' + original_text[1:],
            'embedded_nul_suffix': original_text + '\0junk',
        }
        try:
            for name, corrupt_text in corruptions.items():
                with self.subTest(corruption=name):
                    with closing(sqlite3.connect(journal)) as database:
                        database.execute('UPDATE captures SET request_json=?,record_json=?,status=? WHERE request_id=?',
                                         (original_payload, corrupt_text, original_status, request['request_id']))
                        database.commit()
                    self.client = self.start_client()
                    state = self.client.get_state()
                    self.assertFalse(state['journal_healthy'])
                    self.assertIsNone(state['last_capture'])
                    for action in (lambda: self.client.query_capture(request['request_id']),
                                   lambda: self.client.acquire('must-be-fenced'),
                                   lambda: self.client.capture(request)):
                        with self.assertRaises(CameraError) as error:
                            action()
                        self.assertEqual(error.exception.code, 'journal_error')
                    self.client.close()
                    with closing(sqlite3.connect(journal)) as database:
                        persisted = database.execute('SELECT record_json FROM captures WHERE request_id=?',
                                                     (request['request_id'],)).fetchone()[0]
                    self.assertEqual(persisted, corrupt_text)
                    self.assertEqual(final.read_bytes(), original_file)
                    self.assertEqual(len(list(final.parent.glob('*.dng'))), 1)
        finally:
            self.client.abort()
            with closing(sqlite3.connect(journal)) as database:
                database.execute('UPDATE captures SET request_json=?,record_json=?,status=? WHERE request_id=?',
                                 (original_payload, original_text, original_status, request['request_id']))
                database.commit()

    def test_invalid_native_metadata_does_not_truncate_or_wrap(self):
        self.client.close()
        journal = Path(self.directory.name) / 'journal.sqlite3'
        with closing(sqlite3.connect(journal)) as database:
            original = dict(database.execute("SELECT key,value FROM meta WHERE key IN ('authority','epoch')"))
        corruptions = [('authority', 'native\0foreign'), ('epoch', '-3'), ('epoch', '+3'),
                       ('epoch', ' 3'), ('epoch', '3 '), ('epoch', '03'), ('epoch', '3\0junk')]
        try:
            for key, value in corruptions:
                with self.subTest(key=key, value=repr(value)):
                    with closing(sqlite3.connect(journal)) as database:
                        for original_key, original_value in original.items():
                            database.execute('UPDATE meta SET value=? WHERE key=?', (original_value, original_key))
                        database.execute('UPDATE meta SET value=? WHERE key=?', (value, key))
                        database.commit()
                    unexpected_client = None
                    try:
                        with self.assertRaises(CameraError) as error:
                            unexpected_client = self.start_client()
                    finally:
                        if unexpected_client is not None:
                            unexpected_client.abort()
                    self.assertEqual(error.exception.code, 'journal_error')
                    with closing(sqlite3.connect(journal)) as database:
                        persisted = database.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0]
                    self.assertEqual(persisted, value)
        finally:
            with closing(sqlite3.connect(journal)) as database:
                for key, value in original.items():
                    database.execute('UPDATE meta SET value=? WHERE key=?', (value, key))
                database.commit()
            self.client = self.start_client()
            self.lease = self.client.acquire('metadata-restored')

    def test_context_exit_does_not_leave_native_process_or_directory_lock(self):
        self.client.close()
        owner = self.start_client()
        with self.assertRaises(CameraError) as error:
            with owner:
                lease = owner.acquire('context-owner')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'],
                               control_epoch=lease['epoch'], fault='delay')
                owner.capture(request)
        self.assertEqual(error.exception.code, 'camera_busy')
        self.assertTrue(owner.closed)
        self.assertIsNotNone(owner.process.poll())
        self.client = self.start_client()
        self.lease = self.client.acquire('after-context-exit')
        self.assertEqual(self.client.query_capture(request['request_id'])['status'], 'unknown')
        self.assertFalse(list(self.client.photos.glob('*.dng')))

    def test_late_output_conflicts_are_never_overwritten_or_removed(self):
        for conflict in ('staging', 'final'):
            with self.subTest(conflict=conflict):
                request = self.request(fault='delay')
                self.client.capture(request)
                staging = self.client.photos / (request['request_id'] + '.dng.partial')
                final = self.client.photos / (request['request_id'] + '.dng')
                target = staging if conflict == 'staging' else final
                target.write_bytes(b'foreign ' + conflict.encode())
                record = self.wait(request['request_id'])
                self.assertNotEqual(record['status'], 'complete')
                self.assertEqual(target.read_bytes(), b'foreign ' + conflict.encode())
                self.assertEqual(self.client.capture(request)['status'], record['status'])
                if conflict == 'staging':
                    self.assertFalse(final.exists())
                else:
                    self.assertFalse(staging.exists())

    @unittest.skipUnless(os.name == 'posix', 'requires unprivileged POSIX directory symlinks')
    def test_startup_rejects_redirected_photos_and_releases_ownership(self):
        self.client.close()
        photos = Path(self.directory.name) / 'photos'
        photos.rmdir()
        with tempfile.TemporaryDirectory(prefix='hf-native-outside-') as temporary:
            outside = Path(temporary)
            sentinel = outside / 'keep.bin'
            sentinel.write_bytes(b'foreign storage')
            photos.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(CameraError) as caught:
                unexpected = self.start_client()
                unexpected.abort()
            self.assertEqual(caught.exception.code, 'storage_error')
            self.assertEqual(list(outside.iterdir()), [sentinel])
            photos.unlink()
            photos.mkdir()
        self.client = self.start_client()
        self.lease = self.client.acquire('restored-photo-directory')
        request = self.request()
        self.client.capture(request)
        self.assertEqual(self.wait(request['request_id'])['status'], 'complete')

    def test_replaced_photo_directory_fences_admission_and_delayed_worker(self):
        photos = self.client.photos
        saved = photos.with_name('original-photos')
        photos.rename(saved)
        photos.mkdir()
        rejected = self.request()
        try:
            with self.assertRaises(CameraError) as caught:
                self.client.capture(rejected)
            self.assertEqual(caught.exception.code, 'storage_error')
            self.assertIsNone(self.client.query_capture(rejected['request_id']))
            self.assertFalse(list(photos.iterdir()))
        finally:
            photos.rmdir()
            saved.rename(photos)
        request = self.request(fault='delay')
        self.client.capture(request)
        photos.rename(saved)
        photos.mkdir()
        foreign = photos / (request['request_id'] + '.dng.partial')
        foreign.write_bytes(b'foreign replacement-directory staging')
        try:
            record = self.wait(request['request_id'])
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(foreign.read_bytes(), b'foreign replacement-directory staging')
            self.assertEqual(list(photos.iterdir()), [foreign])
            self.assertFalse(list(saved.iterdir()))
            self.assertTrue(self.client.get_state()['journal_healthy'])
        finally:
            foreign.unlink()
            photos.rmdir()
            saved.rename(photos)
        self.assertEqual(self.client.capture(request)['status'], 'failed')
        self.assertFalse(list(photos.iterdir()))

    @unittest.skipUnless(os.name == 'posix', 'requires unprivileged POSIX directory symlinks')
    def test_delayed_capture_rejects_late_photo_symlink_without_external_write(self):
        photos = self.client.photos
        saved = photos.with_name('original-photos')
        request = self.request(fault='delay')
        self.client.capture(request)
        photos.rename(saved)
        with tempfile.TemporaryDirectory(prefix='hf-native-outside-') as temporary:
            outside = Path(temporary)
            foreign = outside / (request['request_id'] + '.dng.partial')
            foreign.write_bytes(b'foreign outside staging')
            photos.symlink_to(outside, target_is_directory=True)
            try:
                self.assertEqual(self.wait(request['request_id'])['status'], 'failed')
                self.assertEqual(foreign.read_bytes(), b'foreign outside staging')
                self.assertEqual(list(outside.iterdir()), [foreign])
                self.assertFalse(list(saved.iterdir()))
            finally:
                photos.unlink()
                saved.rename(photos)

    def test_retained_pages_are_durable_bounded_complete_only_and_do_not_replay(self):
        completed = []
        for exposure in (5000, 10000, 15000):
            request = self.request(requested_exposure_us=exposure)
            self.client.capture(request)
            self.assertEqual(self.wait(request['request_id'])['status'], 'complete')
            completed.append(request['request_id'])
        failed = self.request(fault='storage_full')
        self.client.capture(failed)
        self.assertEqual(self.wait(failed['request_id'])['status'], 'failed')
        first = self.client.list_captures(limit=2)
        self.assertEqual([item['request_id'] for item in first['items']], list(reversed(completed[1:])))
        self.assertEqual(first['next_cursor'], completed[1])
        self.assertEqual(first['order'], 'newest_first')
        for item in first['items']:
            self.assertEqual(item['status'], 'complete')
            self.assertEqual(item['filename'], item['request_id'] + '.dng')
            self.assertEqual((item['width'], item['height'], item['bit_depth']), (128, 96, 14))
            self.assertNotIn('file_path', item)
            self.assertNotIn('pixels', item)
        later = self.request()
        self.client.capture(later)
        self.assertEqual(self.wait(later['request_id'])['status'], 'complete')
        second = self.client.list_captures(limit=2, cursor=first['next_cursor'])
        self.assertEqual([item['request_id'] for item in second['items']], [completed[0]])
        self.assertIsNone(second['next_cursor'])
        self.restart()
        page = self.client.list_captures(limit=50)
        self.assertEqual([item['request_id'] for item in page['items']], [later['request_id'], *reversed(completed)])
        self.assertEqual(len(list(self.client.photos.glob('*.dng'))), 4)
        for limit in (0, 51, -1, True, 1.5):
            with self.assertRaises(CameraError) as error:
                self.client.list_captures(limit=limit)
            self.assertEqual(error.exception.code, 'invalid_request')
        for cursor in ('../photo.dng', str(uuid.uuid4()), failed['request_id']):
            with self.assertRaises(CameraError) as error:
                self.client.list_captures(cursor=cursor)
            self.assertEqual(error.exception.code, 'invalid_request')

    def test_retained_asset_descriptors_require_committed_regular_expected_files(self):
        request = self.request(fault='delay')
        self.client.capture(request)
        with self.assertRaises(CameraError) as error:
            self.client.get_capture_asset(request['request_id'])
        self.assertEqual(error.exception.code, 'capture_not_complete')
        record = self.wait(request['request_id'])
        self.assertEqual(record['status'], 'complete')
        self.client.release(self.lease['token'], self.lease['epoch'])
        descriptor = self.client.get_capture_asset(request['request_id'])
        self.assertEqual(set(descriptor), {'request_id', 'filename', 'byte_count', 'media_type'})
        self.assertEqual(descriptor['filename'], request['request_id'] + '.dng')
        self.assertEqual(descriptor['media_type'], 'image/x-adobe-dng')
        path = self.client.photos / descriptor['filename']
        self.assertEqual(descriptor['byte_count'], path.stat().st_size)
        path.write_bytes(b'corrupt')
        with self.assertRaises(CameraError) as error:
            self.client.get_capture_asset(request['request_id'])
        self.assertEqual(error.exception.code, 'capture_unavailable')
        path.unlink()
        with self.assertRaises(CameraError) as error:
            self.client.get_capture_asset(request['request_id'])
        self.assertEqual(error.exception.code, 'capture_unavailable')
        self.assertTrue(self.client.get_state()['journal_healthy'])
        self.assertEqual(self.client.list_captures()['items'][0]['request_id'], request['request_id'])
        for request_id in ('../secret', request['request_id'].upper(), str(uuid.uuid4())):
            with self.assertRaises(CameraError) as error:
                self.client.get_capture_asset(request_id)
            self.assertIn(error.exception.code, {'invalid_request', 'not_found'})

    def test_rpc_malformed_duplicate_depth_types_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = [b'{malformed', b'{"id":1,"id":2,"method":"get_state"}',
                   b'{"id":true,"method":"get_state"}', b'{"id":1,"method":"get_state","params":[]}',
                   json.dumps({'id': 1, 'method': 'get_state', 'params': {'nested': [0]}}).encode(),
                   b'{"id":1,"method":"get_state","params":' + b'[' * 40 + b'0' + b']' * 40 + b'}']
            good = [b'{"id":100,"method":"get_state"}', b'{"id":101,"method":"unimplemented"}',
                    b'{"id":102,"method":"close"}']
            completed = subprocess.run([str(self.executable), '--data-dir', directory],
                                       input=b'\n'.join(bad + good) + b'\n', capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, b'')
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual(len(responses), len(bad) + len(good))
            for response in responses[:len(bad)]:
                self.assertEqual(response['error']['code'], 'invalid_request')
            self.assertEqual(responses[-3]['id'], 100)
            self.assertTrue(responses[-3]['result']['simulated'])
            self.assertEqual(responses[-2]['error']['code'], 'method_not_found')
            self.assertTrue(responses[-1]['result']['closed'])

    def test_rpc_input_limit_is_bounded_and_closes_process(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([str(self.executable), '--data-dir', directory],
                                       input=b'x' * 70000, capture_output=True, timeout=10)
            self.assertNotEqual(completed.returncode, 0)
            responses = completed.stdout.splitlines()
            self.assertEqual(len(responses), 1)
            self.assertEqual(json.loads(responses[0])['error']['code'], 'invalid_request')
            self.assertLess(len(responses[0]), 1024)


if __name__ == '__main__':
    unittest.main()

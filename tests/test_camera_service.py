import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

from camera.camerad.service import CameraError, CameraService, DEFAULTS


class CameraServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = CameraService(self.tmp.name)
        self.lease = self.svc.acquire('test')

    def tearDown(self):
        if self.svc:
            for _ in range(500):
                try:
                    self.svc.close()
                    break
                except CameraError as e:
                    if e.code != 'camera_busy':
                        raise
                    time.sleep(.01)
        self.tmp.cleanup()

    def request(self, **kw):
        req = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=self.lease['token'], control_epoch=self.lease['epoch'])
        req.update(kw)
        return req

    def wait(self, rid):
        for _ in range(500):
            result = self.svc.query_capture(rid)
            if result['status'] in ('complete', 'failed', 'cancelled', 'unknown'):
                return result
            time.sleep(.01)
        self.fail('capture did not terminate')

    def test_capture_identity_and_payload_conflict(self):
        req = self.request()
        first = self.svc.capture(req)
        result = self.wait(first['request_id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['requested_exposure_us'], result['actual_exposure_us'])
        self.assertTrue(result['file_path'].endswith('.dng'))
        self.assertEqual(self.svc.capture(req)['frame_id'], result['frame_id'])
        with self.assertRaises(CameraError) as e:
            self.svc.capture(dict(req, requested_exposure_us=20000))
        self.assertEqual(e.exception.code, 'request_conflict')
        self.assertEqual(len(list(self.svc.photos.glob('*.dng'))), 1)

    def test_stale_epoch_and_restart(self):
        old = self.lease.copy()
        self.svc.release(old['token'], old['epoch'])
        self.lease = self.svc.acquire('second')
        with self.assertRaises(CameraError):
            self.svc.capture(dict(self.request(), token=old['token'], control_epoch=old['epoch']))
        current_epoch = self.lease['epoch']
        self.svc.close()
        self.svc = CameraService(self.tmp.name)
        self.lease = self.svc.acquire('after-restart')
        self.assertGreater(self.lease['epoch'], current_epoch)
        with self.assertRaises(CameraError):
            self.svc.configure(old['token'], old['epoch'], {})

    def test_one_instance_per_data_dir(self):
        with self.assertRaises(CameraError) as e:
            CameraService(self.tmp.name)
        self.assertEqual(e.exception.code, 'already_running')

    def test_thread_start_failure_releases_slot_and_journals_failure(self):
        req = self.request()
        with mock.patch('camera.camerad.service.threading.Thread.start', side_effect=RuntimeError('no thread')):
            with self.assertRaises(CameraError) as error:
                self.svc.capture(req)
        self.assertEqual(error.exception.code, 'worker_start_failed')
        self.assertEqual(self.svc.query_capture(req['request_id'])['status'], 'failed')
        self.assertIsNone(self.svc.active)
        self.assertEqual(self.svc.capture(req)['status'], 'failed')
        next_req = self.request()
        self.svc.capture(next_req)
        self.assertEqual(self.wait(next_req['request_id'])['status'], 'complete')

    def test_failed_admission_commit_rolls_back_and_stops_service(self):
        real_db = self.svc.db
        class FailCommit:
            def __init__(self, wrapped):
                self.wrapped = wrapped
            def __getattr__(self, name):
                return getattr(self.wrapped, name)
            def commit(self):
                raise sqlite3.OperationalError('injected commit failure')
        self.svc.db = FailCommit(real_db)
        req = self.request()
        with self.assertRaises(CameraError) as e:
            self.svc.capture(req)
        self.assertEqual(e.exception.code, 'journal_error')
        self.assertFalse(real_db.in_transaction)
        self.assertIsNone(self.svc.active)
        db = sqlite3.connect(self.tmp.name + '/journal.sqlite3')
        self.assertIsNone(db.execute('SELECT request_id FROM captures WHERE request_id=?', (req['request_id'],)).fetchone())
        db.close()
        self.assertFalse(list(self.svc.photos.iterdir()))
        with self.assertRaises(CameraError):
            self.svc.capture(req)
        self.svc.db = real_db
        self.svc.close()
        self.svc = CameraService(self.tmp.name)
        self.lease = self.svc.acquire('after restart')
        self.assertIsNone(self.svc.query_capture(req['request_id']))

    def test_lease_heartbeat_and_expiry_fence(self):
        # Control the monotonic clock to avoid timing-dependent tests.
        self.svc.release(self.lease['token'], self.lease['epoch'])
        with mock.patch('camera.camerad.service.time.monotonic', return_value=100.0) as clock:
            old = self.svc.acquire('owner')
            clock.return_value = 120.0
            self.svc.heartbeat(old['token'], old['epoch'])
            clock.return_value = 145.0
            self.assertEqual(self.svc.get_state()['control']['owner'], 'owner')
            clock.return_value = 150.0
            self.assertIsNone(self.svc.get_state()['control'])
            with self.assertRaises(CameraError) as e:
                self.svc.heartbeat(old['token'], old['epoch'])
            self.assertEqual(e.exception.code, 'control_denied')
            self.lease = self.svc.acquire('new owner')
            self.assertGreater(self.lease['epoch'], old['epoch'])
            with self.assertRaises(CameraError):
                self.svc.configure(old['token'], old['epoch'], {})

    def test_expiration_does_not_interrupt_capture_or_allow_overlap(self):
        self.svc.release(self.lease['token'], self.lease['epoch'])
        with mock.patch('camera.camerad.service.time.monotonic', return_value=100.0) as clock:
            old = self.svc.acquire('owner')
            req = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=old['token'], control_epoch=old['epoch'], fault='delay')
            self.svc.capture(req)
            clock.return_value = 131.0
            self.lease = self.svc.acquire('new owner')
            with self.assertRaises(CameraError) as e:
                self.svc.capture(self.request())
            self.assertEqual(e.exception.code, 'camera_busy')
            with self.assertRaises(CameraError):
                self.svc.cancel_capture(old['token'], old['epoch'], req['request_id'])
            self.assertEqual(self.wait(req['request_id'])['status'], 'complete')

    def test_actual_process_lock_and_abrupt_exit_recovery(self):
        # Real OS process, not an in-process stand-in for file locks or restart.
        with tempfile.TemporaryDirectory() as directory:
            hold_script = (
                'import sys\nfrom camera.camerad.service import CameraService\n'
                's=CameraService(sys.argv[1]); l=s.acquire("child"); '
                'print(l["epoch"], flush=True); input(); s.close()\n'
            )
            child = subprocess.Popen([sys.executable, '-u', '-c', hold_script, directory],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            try:
                line = child.stdout.readline().strip()
                self.assertTrue(line, child.stderr.read() if child.poll() is not None else 'child did not report epoch')
                old_epoch = int(line)
                with self.assertRaises(CameraError) as e:
                    CameraService(directory)
                self.assertEqual(e.exception.code, 'already_running')
                child.stdin.write('\n')
                child.stdin.flush()
                self.assertEqual(child.wait(timeout=10), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()
            crash_script = (
                'import os,sys,uuid\nfrom camera.camerad.service import CameraService,DEFAULTS\n'
                's=CameraService(sys.argv[1]); l=s.acquire("crash"); '
                'r=dict(DEFAULTS,request_id=str(uuid.uuid4()),token=l["token"],'
                'control_epoch=l["epoch"],fault="delay"); s.capture(r); '
                'print(r["request_id"],l["epoch"],flush=True); os._exit(0)\n'
            )
            result = subprocess.run([sys.executable, '-u', '-c', crash_script, directory],
                                    capture_output=True, text=True, timeout=10, check=True)
            rid, crash_epoch = result.stdout.strip().split()
            recovered = CameraService(directory)
            try:
                record = recovered.query_capture(rid)
                self.assertEqual(record['status'], 'unknown')
                self.assertGreater(recovered.acquire('recovered')['epoch'], max(old_epoch, int(crash_epoch)))
                self.assertFalse(list(recovered.photos.glob('*.dng')))
            finally:
                recovered.close()

    def test_process_crash_after_rename_is_unknown_and_not_reexecuted(self):
        with tempfile.TemporaryDirectory() as directory:
            crash_script = '''
import os, sys, uuid, time
from camera.camerad.service import CameraService, DEFAULTS
s=CameraService(sys.argv[1])
l=s.acquire('child')
r=dict(DEFAULTS,request_id=str(uuid.uuid4()),token=l['token'],control_epoch=l['epoch'])
original=s._save
def crash_on_completion(record):
    if record['status']=='complete': os._exit(0)
    original(record)
s._save=crash_on_completion
print(r['request_id'], flush=True)
s.capture(r)
time.sleep(10)
os._exit(2)
'''
            result = subprocess.run([sys.executable, '-u', '-c', crash_script, directory],
                                    capture_output=True, text=True, timeout=15, check=True)
            rid = result.stdout.strip()
            recovered = CameraService(directory)
            try:
                self.assertEqual(recovered.query_capture(rid)['status'], 'unknown')
                files = list(recovered.photos.glob('*.dng'))
                self.assertEqual(len(files), 1)
                lease = recovered.acquire('recovered')
                req = dict(DEFAULTS, request_id=rid, token=lease['token'], control_epoch=lease['epoch'])
                self.assertEqual(recovered.capture(req)['status'], 'unknown')
                self.assertEqual(list(recovered.photos.glob('*.dng')), files)
            finally:
                recovered.close()

    def test_interrupted_record_becomes_unknown_without_reexecution(self):
        req = self.request(fault='disconnect')
        self.svc.capture(req)
        self.assertEqual(self.wait(req['request_id'])['status'], 'failed')
        # Synthesize the persisted state left by a process crash in capture.
        for _ in range(100):
            try:
                self.svc.close()
                break
            except CameraError:
                time.sleep(.01)
        self.svc = None
        db = sqlite3.connect(self.tmp.name + '/journal.sqlite3')
        record = json.loads(db.execute('SELECT record_json FROM captures').fetchone()[0])
        record.update(status='capturing', error=None)
        db.execute('UPDATE captures SET status=?, record_json=?', ('capturing', json.dumps(record)))
        db.commit()
        db.close()
        self.svc = CameraService(self.tmp.name)
        self.lease = self.svc.acquire('restarted')
        self.assertEqual(self.svc.query_capture(req['request_id'])['status'], 'unknown')
        req.update(token=self.lease['token'], control_epoch=self.lease['epoch'])
        self.assertEqual(self.svc.capture(req)['status'], 'unknown')
        self.assertEqual(len(list(self.svc.photos.glob('*.dng'))), 0)

    def test_no_completion_before_file_commit(self):
        entered = threading.Event()
        proceed = threading.Event()
        import camera.camerad.service as module
        original = module._publish_capture_file
        def paused_replace(*args):
            entered.set()
            if not proceed.wait(5):
                raise RuntimeError('test timed out')
            return original(*args)
        req = self.request()
        with mock.patch.object(module, '_publish_capture_file', side_effect=paused_replace):
            self.svc.capture(req)
            self.assertTrue(entered.wait(5))
            # Read journal from a separate connection; service lock is held during rename.
            db = sqlite3.connect(self.tmp.name + '/journal.sqlite3')
            row = db.execute('SELECT status, record_json FROM captures WHERE request_id=?', (req['request_id'],)).fetchone()
            db.close()
            self.assertEqual(row[0], 'capturing')
            self.assertIsNone(json.loads(row[1])['file_path'])
            proceed.set()
            self.assertEqual(self.wait(req['request_id'])['status'], 'complete')

    def test_files_created_after_admission_are_never_overwritten_or_deleted(self):
        for final_conflict in (False, True):
            with self.subTest(final_conflict=final_conflict):
                request = self.request(fault='delay')
                self.svc.capture(request)
                staging = self.svc.photos / (request['request_id'] + '.dng.partial')
                final = self.svc.photos / (request['request_id'] + '.dng')
                staging.write_bytes(b'foreign staging must survive')
                if final_conflict:
                    final.write_bytes(b'foreign final must survive')
                result = self.wait(request['request_id'])
                for _ in range(100):
                    if not self.svc.active:
                        break
                    time.sleep(.01)
                self.assertEqual(staging.read_bytes(), b'foreign staging must survive')
                self.assertFalse(list(self.svc.photos.glob('.capture-*')))
                if final_conflict:
                    self.assertEqual(result['status'], 'unknown')
                    self.assertEqual(final.read_bytes(), b'foreign final must survive')
                    self.assertEqual(self.svc.capture(request)['status'], 'unknown')
                else:
                    self.assertEqual(result['status'], 'complete')
                    self.assertTrue(final.read_bytes().startswith(b'II*\x00'))

    def test_fault_and_cancellation(self):
        req = self.request(fault='storage_full')
        self.svc.capture(req)
        self.assertEqual(self.wait(req['request_id'])['status'], 'failed')
        # Wait for the worker to release the active slot after committing status.
        for _ in range(100):
            if not self.svc.active:
                break
            time.sleep(.01)
        req2 = self.request(fault='delay')
        self.svc.capture(req2)
        self.svc.cancel_capture(self.lease['token'], self.lease['epoch'], req2['request_id'])
        self.assertEqual(self.wait(req2['request_id'])['status'], 'cancelled')
        self.assertFalse(list(self.svc.photos.glob('*.dng')))

    def test_disconnect_persists_until_authorized_reconnect(self):
        self.svc.start_preview(self.lease['token'], self.lease['epoch'])
        req = self.request(fault='disconnect')
        self.svc.capture(req)
        self.assertEqual(self.wait(req['request_id'])['status'], 'failed')
        for _ in range(100):
            if not self.svc.active:
                break
            time.sleep(.01)
        self.assertFalse(self.svc.get_state()['connected'])
        self.assertEqual(self.svc.capture(req)['status'], 'failed')
        for action in (lambda: self.svc.capture(self.request()),
                       lambda: self.svc.configure(self.lease['token'], self.lease['epoch'], {}),
                       lambda: self.svc.start_preview(self.lease['token'], self.lease['epoch'])):
            with self.assertRaises(CameraError) as e:
                action()
            self.assertEqual(e.exception.code, 'device_disconnected')
        with self.assertRaises(CameraError):
            self.svc.reconnect('wrong', self.lease['epoch'])
        self.assertTrue(self.svc.reconnect(self.lease['token'], self.lease['epoch'])['connected'])
        self.assertTrue(self.svc.get_state()['connected'])
        req2 = self.request()
        self.svc.capture(req2)
        self.assertEqual(self.wait(req2['request_id'])['status'], 'complete')


if __name__ == '__main__':
    unittest.main()

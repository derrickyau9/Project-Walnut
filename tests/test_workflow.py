import tempfile
import json
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from daos.capability_runtime.bracket import BracketRunner, WorkflowError
from daos.capability_runtime.manager import WorkflowManager
from camera.camerad.service import CameraService


class FakeCamera:
    def __init__(self, mode='normal'):
        self.mode = mode
        self.settings = {'sensor_mode': 'sim-small', 'requested_exposure_us': 12000,
                         'requested_analogue_gain': 1.0, 'output_format': 'dng'}
        self.records = {}
        self.calls = 0
        self.configurations = []
        self.cancel_calls = 0
    def acquire(self, owner): return {'token':'local-test-token', 'epoch':1}
    def release(self, token, epoch): pass
    def get_state(self): return {'settings': dict(self.settings), 'camera_state':'idle'}
    def configure(self, token, epoch, settings):
        self.settings = dict(settings)
        self.configurations.append(dict(settings))
        return dict(settings)
    def capture(self, request):
        self.calls += 1
        if self.mode == 'ambiguous': raise TimeoutError('link dropped')
        status = 'capturing' if self.mode == 'wait' else 'complete'
        self.records[request['request_id']] = {'request_id':request['request_id'], 'status':status,
                                               'actual_exposure_us':request['requested_exposure_us'], 'actual_analogue_gain':request['requested_analogue_gain'], 'file_path':'synthetic.dng'}
        return self.records[request['request_id']]
    def query_capture(self, rid): return self.records.get(rid)
    def cancel_capture(self, token, epoch, rid):
        self.cancel_calls += 1
        self.records[rid]['status'] = 'cancelled'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'workflow.sqlite3'
        self.plan = {'exposures_us':[5000,10000,20000]}

    def test_simulator_full_workflow_and_idempotent_reopen(self):
        service = CameraService(Path(self.tmp.name) / 'camera')
        self.addCleanup(service.close)
        before = service.get_state()['settings']
        runner = BracketRunner(service, self.db, poll_interval=.01, capture_timeout=10)
        result = runner.run('bracket-1', self.plan)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(3, len(result['steps']))
        self.assertEqual([5000,10000,20000], [s['result']['actual_exposure_us'] for s in result['steps']])
        self.assertTrue(all(Path(s['result']['file_path']).is_file() for s in result['steps']))
        self.assertEqual(before, service.get_state()['settings'])
        self.assertIsNone(service.get_state()['control'])
        second = BracketRunner(service, self.db).run('bracket-1', self.plan)
        self.assertEqual([s['request_id'] for s in result['steps']], [s['request_id'] for s in second['steps']])
        self.assertEqual(3, len(list((Path(self.tmp.name) / 'camera' / 'photos').glob('*.dng'))))
        with self.assertRaises(WorkflowError): runner.run('bracket-1', {'exposures_us':[300]})

    def test_ambiguous_submission_is_never_retried(self):
        service = FakeCamera('ambiguous')
        runner = BracketRunner(service, self.db)
        one = runner.run('ambiguous', self.plan)
        self.assertEqual('unknown', one['status'])
        two = BracketRunner(service, self.db).run('ambiguous', self.plan)
        self.assertEqual('unknown', two['status'])
        self.assertEqual(1, service.calls)
        self.assertEqual(one['steps'][0]['request_id'], two['steps'][0]['request_id'])
        self.assertEqual(12000, service.settings['requested_exposure_us'])

    def test_late_completion_is_reconciled_using_original_request(self):
        service = FakeCamera('ambiguous')
        runner = BracketRunner(service, self.db)
        first = runner.run('late', self.plan)
        rid = first['steps'][0]['request_id']
        service.records[rid] = {'request_id':rid, 'status':'complete', 'actual_exposure_us':5000, 'actual_analogue_gain':1.0, 'file_path':'synthetic.dng'}
        service.mode = 'normal'
        resumed = BracketRunner(service, self.db).run('late', self.plan)
        self.assertEqual('completed', resumed['status'])
        self.assertEqual(rid, resumed['steps'][0]['request_id'])
        self.assertEqual(3, service.calls)  # first attempted + two remaining; no duplicate first

    def test_wrong_capture_identity_is_unknown_then_reconciles_original(self):
        service = FakeCamera()
        capture = service.capture
        def wrong_response(request):
            record = capture(request)
            return dict(record, request_id='different-request')
        service.capture = wrong_response
        runner = BracketRunner(service, self.db)
        first = runner.run('identity', {'exposures_us':[5000]})
        self.assertEqual('unknown', first['status'])
        rid = first['steps'][0]['request_id']
        self.assertNotEqual('different-request', rid)
        final = runner.run('identity', {'exposures_us':[5000]})
        self.assertEqual('completed', final['status'])
        self.assertEqual(rid, final['steps'][0]['request_id'])
        self.assertEqual(1, service.calls)

    def test_query_failure_after_submission_is_unknown(self):
        service = FakeCamera('ambiguous')
        def broken_query(rid): raise ConnectionError('offline')
        service.query_capture = broken_query
        runner = BracketRunner(service, self.db)
        result = runner.run('offline', self.plan)
        self.assertEqual('unknown', result['status'])
        self.assertEqual('unknown', result['steps'][0]['status'])
        self.assertEqual(1, service.calls)

    def test_cancellation_inflight_requests_cancel_and_restores(self):
        service = FakeCamera('wait')
        runner = BracketRunner(service, self.db, poll_interval=.005, capture_timeout=1)
        cancel = threading.Event()
        capture = service.capture
        def admitted(request):
            record = capture(request)
            cancel.set()  # The request is admitted before cancellation arrives.
            return record
        service.capture = admitted
        result = runner.run('cancel', self.plan, cancel)
        self.assertEqual('cancelled', result['status'], result)
        self.assertEqual(1, service.calls)
        self.assertEqual(1, service.cancel_calls)
        self.assertEqual(12000, service.settings['requested_exposure_us'])

    def test_resume_restores_settings_of_current_lease_after_clean_pause(self):
        camera = FakeCamera()
        runner = BracketRunner(camera, self.db)
        capture = camera.capture
        def pause_after_admission(request):
            record = capture(request)
            runner.set_desired('lease-settings', 'pause')
            return record
        camera.capture = pause_after_admission
        first = runner.run('lease-settings', {'exposures_us':[5000,10000]})
        self.assertEqual('paused', first['status'])
        self.assertEqual(12000, camera.settings['requested_exposure_us'])
        # Another controller changes settings while the paused job has no lease.
        camera.settings = dict(camera.settings, requested_exposure_us=17000, requested_analogue_gain=2.0)
        expected = dict(camera.settings)
        camera.capture = capture
        runner.set_desired('lease-settings', 'run')
        final = runner.run('lease-settings', {'exposures_us':[5000,10000]})
        self.assertEqual('completed', final['status'])
        self.assertEqual(expected, camera.settings)
        self.assertEqual(first['steps'][0]['request_id'], final['steps'][0]['request_id'])
        self.assertEqual(2, camera.calls)

    def test_cli_run_and_inspect(self):
        data = str(Path(self.tmp.name) / 'cli-data')
        base = [sys.executable, '-m', 'daos.capability_runtime', '--data-dir', data]
        cmd = base + ['run', '--job', 'cli-job', '--exposures-us', '5000', '10000']
        executed = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        self.assertEqual(0, executed.returncode, executed.stderr + executed.stdout)
        result = json.loads(executed.stdout)
        self.assertEqual('completed', result['status'])
        self.assertEqual(2, len(result['steps']))
        inspected = subprocess.run(base + ['inspect', '--job', 'cli-job'], text=True, capture_output=True, timeout=30)
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        self.assertEqual(result, json.loads(inspected.stdout))
        again = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual([x['request_id'] for x in result['steps']],
                         [x['request_id'] for x in json.loads(again.stdout)['steps']])
        self.assertEqual(2, len(list((Path(data) / 'simulator' / 'photos').glob('*.dng'))))

    def test_manager_pause_resume_interval_and_restart_recovery(self):
        service = CameraService(Path(self.tmp.name) / 'manager-camera')
        self.addCleanup(service.close)
        manager = WorkflowManager(service, self.db)
        capture = service.capture
        def pause_after_admission(request):
            record = capture(request)
            manager.pause('interval')
            return record
        service.capture = pause_after_admission
        manager.start('interval', {'kind':'interval', 'exposures_us':[5000,5000,5000], 'interval_ms':1000})
        limit = time.monotonic() + 8
        while time.monotonic() < limit:
            job = manager.inspect('interval')
            if len(job['steps']) == 1 and job['steps'][0]['status'] == 'completed': break
            time.sleep(.01)
        else: self.fail('first interval step never completed')
        while time.monotonic() < limit:
            job = manager.inspect('interval')
            if job['status'] == 'paused' and not manager._active(): break
            time.sleep(.01)
        self.assertEqual('paused', job['status'])
        self.assertEqual(1, len(job['steps']))
        self.assertIsNone(service.get_state()['control'])
        service.capture = capture
        # A fresh manager sees the durable paused state, then resumes same ID.
        manager = WorkflowManager(service, self.db)
        self.assertEqual('paused', manager.inspect('interval')['status'])
        manager.resume('interval')
        limit = time.monotonic() + 8
        while time.monotonic() < limit:
            job = manager.inspect('interval')
            if job['status'] in ('completed','failed','unknown') and not manager._active(): break
            time.sleep(.01)
        self.assertEqual('completed', job['status'], job)
        self.assertEqual(3, len(job['steps']))
        self.assertEqual(3, len(set(x['request_id'] for x in job['steps'])))
        times = [x['result']['workflow_completed_unix'] for x in job['steps']]
        self.assertGreaterEqual(times[2] - times[1], .95)

    def test_manager_cancel_during_interval(self):
        service = CameraService(Path(self.tmp.name) / 'manager-camera')
        self.addCleanup(service.close)
        manager = WorkflowManager(service, self.db)
        manager.start('cancel-sequence', {'kind':'interval', 'exposures_us':[5000,5000], 'interval_ms':5000})
        limit = time.monotonic() + 8
        while time.monotonic() < limit:
            job = manager.inspect('cancel-sequence')
            if len(job['steps']) == 1 and job['steps'][0]['status'] == 'completed': break
            time.sleep(.01)
        manager.cancel('cancel-sequence')
        while time.monotonic() < limit:
            job = manager.inspect('cancel-sequence')
            if job['status'] == 'cancelled' and not manager._active(): break
            time.sleep(.01)
        self.assertEqual('cancelled', job['status'], job)
        self.assertEqual(1, len(job['steps']))
        self.assertIsNone(service.get_state()['control'])

    def test_interval_renews_short_lease(self):
        # Exercise scheduling deterministically without asserting that Windows
        # disk scheduling can honor a 300 ms hard real-time deadline.
        class LeasedFake(FakeCamera):
            def __init__(self):
                super().__init__()
                self.renewals = 0
            def acquire(self, owner):
                return {'token':'local-test-token', 'epoch':1, 'lease_seconds':.3}
            def heartbeat(self, token, epoch):
                self.renewals += 1
        service = LeasedFake()
        runner = BracketRunner(service, self.db, poll_interval=.02, capture_timeout=10)
        job = runner.run('lease-test', {'kind':'interval', 'exposures_us':[5000,5000], 'interval_ms':400})
        self.assertEqual('completed', job['status'], job)
        self.assertEqual(2, len(job['steps']))
        # Includes forced renewal at camera operation boundaries and periodic
        # renewal while waiting between frames.
        self.assertGreaterEqual(service.renewals, 7)

    def test_manager_rejects_unaddressable_job_id(self):
        manager = WorkflowManager(FakeCamera(), self.db)
        with self.assertRaises(WorkflowError):
            manager.start('not/url safe', {'exposures_us':[5000]})

    def test_failed_worker_start_is_journaled(self):
        manager = WorkflowManager(FakeCamera(), self.db)
        with patch('threading.Thread.start', side_effect=RuntimeError('no threads')):
            with self.assertRaises(WorkflowError):
                manager.start('start-failure', {'exposures_us':[5000]})
        job = manager.inspect('start-failure')
        self.assertEqual('failed', job['status'])
        self.assertIn('could not start', job['error'])

    def test_manager_restart_reconciles_unconfirmed_submission_without_recapture(self):
        camera = FakeCamera()
        runner = BracketRunner(camera, self.db)
        runner.prepare('interrupted', {'exposures_us':[5000]})
        import uuid
        rid = str(uuid.uuid4())
        self.assertTrue(runner._reserve('interrupted', 0, rid))
        runner._set_job('interrupted', 'running')
        manager = WorkflowManager(camera, self.db)
        self.assertEqual('paused', manager.inspect('interrupted')['status'])
        manager.resume('interrupted')
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            job = manager.inspect('interrupted')
            if job['status'] == 'unknown' and not manager._active(): break
            time.sleep(.01)
        self.assertEqual('unknown', job['status'], job)
        self.assertEqual(rid, job['steps'][0]['request_id'])
        self.assertEqual(0, camera.calls)

    def test_legacy_terminal_label_with_pending_request_is_recoverable(self):
        import uuid
        camera = FakeCamera('wait')
        runner = BracketRunner(camera, self.db, poll_interval=.005)
        for old_status in ('cancelled','failed','completed'):
            with self.subTest(old_status=old_status):
                job_id = 'legacy-' + old_status
                runner.prepare(job_id, {'exposures_us':[5000,10000]})
                rid = str(uuid.uuid4())
                runner._reserve(job_id, 0, rid)
                camera.records[rid] = {'request_id':rid, 'status':'capturing'}
                runner._set_job(job_id, old_status)
                manager = WorkflowManager(camera, self.db)
                self.assertEqual('unknown', manager.inspect(job_id)['status'])
                self.assertEqual('cancel', manager.inspect(job_id)['desired'])
                manager.cancel(job_id)
                deadline = time.monotonic() + 5
                while manager._active() and time.monotonic() < deadline: time.sleep(.005)
                self.assertFalse(manager._active())
                final = manager.inspect(job_id)
                self.assertEqual('cancelled', final['status'], final)
                self.assertEqual(rid, final['steps'][0]['request_id'])
                self.assertEqual('cancelled', final['steps'][0]['status'])
        self.assertEqual(0, camera.calls)
        self.assertEqual(3, camera.cancel_calls)

    def test_dark_and_flat_sequences_label_synthetic_scene(self):
        service = CameraService(Path(self.tmp.name) / 'manager-camera')
        self.addCleanup(service.close)
        runner = BracketRunner(service, self.db, poll_interval=.01, capture_timeout=10)
        for scene in ('dark','flat'):
            result = runner.run('cal-' + scene, {'kind':scene, 'exposures_us':[10000]})
            self.assertEqual('completed', result['status'], result)
            self.assertEqual(scene, result['steps'][0]['result']['simulation_scene'])
            self.assertTrue(Path(result['steps'][0]['result']['file_path']).exists())

    def test_invalid_plan_has_no_camera_effect(self):
        service = FakeCamera()
        runner = BracketRunner(service, self.db)
        with self.assertRaises(WorkflowError): runner.run('invalid', {'exposures_us':[]})
        self.assertEqual(0, service.calls)


if __name__ == '__main__': unittest.main()

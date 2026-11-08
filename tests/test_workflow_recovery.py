"""Recovery must reconcile reserved IDs before cancellation or new execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from daos.capability_runtime.bracket import BracketRunner, WorkflowError
from daos.capability_runtime.manager import WorkflowManager


ROOT = Path(__file__).resolve().parents[1]


class RecoveryCamera:
    """A strict owned camera that can retain an operation across runner death."""
    def __init__(self, *, blocking=False):
        self.lock = threading.RLock()
        self.settings = {
            'sensor_mode': 'sim-small', 'requested_exposure_us': 12000,
            'requested_analogue_gain': 1.0, 'output_format': 'dng',
            'simulation_scene': 'scene',
        }
        self.records = {}
        self.active = None
        self.lease = None
        self.epoch = 0
        self.externally_owned = False
        self.blocking = blocking
        self.started = threading.Event()
        self.captures = []
        self.cancellations = []
        self.events = []

    def _authorize(self, token, epoch):
        if self.lease != (token, epoch):
            raise WorkflowError('stale test camera lease')

    def acquire(self, owner):
        with self.lock:
            if self.externally_owned or self.lease is not None:
                raise WorkflowError('control is held by another owner')
            self.epoch += 1
            self.lease = ('test-token-' + str(self.epoch), self.epoch)
            self.events.append(('acquire', owner))
            return {'token': self.lease[0], 'epoch': self.epoch, 'lease_seconds': 30}

    def heartbeat(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)

    def release(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            self.events.append(('release',))
            self.lease = None

    def get_state(self):
        with self.lock:
            return {'settings': copy.deepcopy(self.settings),
                    'camera_state': 'capturing' if self.active else 'idle'}

    def configure(self, token, epoch, settings):
        with self.lock:
            self._authorize(token, epoch)
            if self.active:
                raise WorkflowError('cannot configure while capture is active')
            self.settings = copy.deepcopy(settings)
            self.events.append(('configure', copy.deepcopy(settings)))
            return copy.deepcopy(settings)

    def capture(self, request):
        with self.lock:
            self._authorize(request['token'], request['control_epoch'])
            if self.active:
                raise WorkflowError('capture already active')
            rid = request['request_id']
            if rid in self.records:
                raise WorkflowError('test detected a duplicate capture submission')
            self.captures.append(copy.deepcopy(request))
            self.records[rid] = {
                'request_id': rid, 'status': 'capturing' if self.blocking else 'complete',
                'actual_exposure_us': request['requested_exposure_us'],
                'actual_analogue_gain': request['requested_analogue_gain'],
                'file_path': 'retained-' + rid + '.dng',
            }
            if self.blocking:
                self.active = rid
            self.started.set()
            return copy.deepcopy(self.records[rid])

    def query_capture(self, rid):
        with self.lock:
            record = self.records.get(rid)
            self.events.append(('query', rid, record['status'] if record else None))
            return copy.deepcopy(record)

    def cancel_capture(self, token, epoch, rid):
        with self.lock:
            self._authorize(token, epoch)
            self.cancellations.append(rid)
            self.events.append(('cancel', rid))
            record = self.records.get(rid)
            if record and record['status'] not in {'complete', 'failed', 'cancelled', 'unknown'}:
                record['status'] = 'cancelled'
                self.active = None
            return {'request_id': rid, 'requested': record is not None}

    def finish_active(self):
        with self.lock:
            if self.active:
                self.records[self.active]['status'] = 'complete'
                self.active = None


class WorkflowRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.journal = self.root / 'workflow.sqlite3'
        self.plan = {'exposures_us': [5000, 10000]}

    def seed_reserved(self, camera, *, status='running', backend='capturing', plan=None):
        plan = self.plan if plan is None else plan
        runner = BracketRunner(camera, self.journal, poll_interval=.005, capture_timeout=2)
        runner.prepare('recovered', plan)
        rid = str(uuid.uuid4())
        self.assertTrue(runner._reserve('recovered', 0, rid))
        original = copy.deepcopy(camera.settings)
        with runner._db() as db:
            db.execute('UPDATE jobs SET original=?, status=? WHERE job_id=?',
                       (json.dumps(original), status, 'recovered'))
        camera.settings['requested_exposure_us'] = 5000
        if backend is not None:
            camera.records[rid] = {
                'request_id': rid, 'status': backend,
                'actual_exposure_us': 5000, 'actual_analogue_gain': 1.0,
                'file_path': 'retained-original.dng',
            }
            if backend == 'capturing':
                camera.active = rid
        return runner, rid, original

    def wait_manager(self, manager, job_id='recovered', timeout=5):
        deadline = time.monotonic() + timeout
        while manager._active() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(manager._active(), 'workflow worker did not stop within timeout')
        return manager.inspect(job_id)

    def test_cancel_recovered_active_request_observes_terminal_and_restores(self):
        camera = RecoveryCamera()
        _, rid, original = self.seed_reserved(camera)
        manager = WorkflowManager(camera, self.journal)
        self.assertEqual('paused', manager.inspect('recovered')['status'])
        manager.cancel('recovered')
        result = self.wait_manager(manager)
        self.assertEqual('cancelled', result['status'], result)
        self.assertEqual([(rid, 'cancelled')], [(step['request_id'], step['status']) for step in result['steps']])
        self.assertEqual([rid], camera.cancellations)
        self.assertEqual([], camera.captures, 'recovery must not submit another capture')
        self.assertEqual(original, camera.settings)
        self.assertIsNone(camera.lease)
        observed = camera.events.index(('query', rid, 'cancelled'))
        restored = camera.events.index(('configure', original))
        self.assertLess(observed, restored, 'restore preceded terminal capture reconciliation')

    def test_cancel_missing_unknown_request_remains_unknown_and_keeps_id(self):
        camera = RecoveryCamera()
        _, rid, _ = self.seed_reserved(camera, status='unknown', backend=None)
        manager = WorkflowManager(camera, self.journal)
        manager.cancel('recovered')
        result = self.wait_manager(manager)
        self.assertEqual('unknown', result['status'], result)
        self.assertEqual([(rid, 'unknown')], [(step['request_id'], step['status']) for step in result['steps']])
        self.assertTrue(any(event[:2] == ('query', rid) for event in camera.events))
        self.assertEqual([], camera.captures)
        self.assertEqual([], camera.cancellations)
        self.assertIsNone(camera.lease)

    def test_cancel_recovered_late_completion_checkpoints_without_future_shot(self):
        camera = RecoveryCamera()
        _, rid, original = self.seed_reserved(camera, backend='complete')
        manager = WorkflowManager(camera, self.journal)
        manager.cancel('recovered')
        result = self.wait_manager(manager)
        self.assertEqual('cancelled', result['status'], result)
        self.assertEqual(1, len(result['steps']))
        step = result['steps'][0]
        self.assertEqual(rid, step['request_id'])
        self.assertEqual('completed', step['status'])
        self.assertEqual(5000, step['result']['actual_exposure_us'])
        self.assertEqual('retained-original.dng', step['result']['file_path'])
        self.assertEqual([], camera.captures)
        self.assertEqual([], camera.cancellations)
        self.assertEqual(original, camera.settings)

    def test_preserved_cancel_intent_reconciles_before_runner_boundary_check(self):
        camera = RecoveryCamera()
        runner, rid, _ = self.seed_reserved(camera)
        runner.set_desired('recovered', 'cancel')
        result = runner.run('recovered', self.plan)
        self.assertEqual('cancelled', result['status'], result)
        self.assertEqual([rid], camera.cancellations)
        self.assertEqual('cancelled', result['steps'][0]['status'])
        self.assertEqual([], camera.captures)

    def test_recovered_acquire_failure_preserves_unknown_for_later_reconciliation(self):
        camera = RecoveryCamera()
        plan = {'exposures_us': [5000]}
        runner, rid, _ = self.seed_reserved(camera, status='unknown', backend='complete', plan=plan)
        camera.externally_owned = True
        result = runner.run('recovered', plan)
        self.assertEqual('unknown', result['status'], result)
        self.assertEqual(rid, result['steps'][0]['request_id'])
        self.assertEqual([], camera.captures)
        self.assertFalse(any(event[0] == 'configure' for event in camera.events))
        camera.externally_owned = False
        resumed = runner.run('recovered', plan)
        self.assertEqual('completed', resumed['status'], resumed)
        self.assertEqual(rid, resumed['steps'][0]['request_id'])
        self.assertEqual('completed', resumed['steps'][0]['status'])
        self.assertEqual([], camera.captures)

    def test_second_manager_does_not_recover_or_interrupt_live_first_manager(self):
        camera = RecoveryCamera(blocking=True)
        first = WorkflowManager(camera, self.journal)
        first.start('live', {'exposures_us': [5000]})
        self.assertTrue(camera.started.wait(3), 'first manager never submitted its capture')
        try:
            before = first.inspect('live')
            self.assertEqual('running', before['status'])
            self.assertEqual('run', before['desired'])
            second = WorkflowManager(camera, self.journal)
            after = second.inspect('live')
            self.assertEqual('running', after['status'])
            self.assertEqual('run', after['desired'])
            self.assertEqual(before['events'], after['events'])
            with self.assertRaises(WorkflowError):
                second.start('live', {'exposures_us': [5000]})
            self.assertEqual('run', first.inspect('live')['desired'])
            self.assertEqual(1, len(camera.captures))
        finally:
            camera.finish_active()
            finished = self.wait_manager(first, 'live')
        self.assertEqual('completed', finished['status'], finished)
        self.assertEqual(1, len(camera.captures))

    def test_second_direct_runner_cannot_change_live_job_or_create_another(self):
        camera = RecoveryCamera(blocking=True)
        first = BracketRunner(camera, self.journal, poll_interval=.005, capture_timeout=3)
        results, failures = [], []
        def work():
            try:
                results.append(first.run('live', {'exposures_us': [5000]}))
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(camera.started.wait(3), 'first direct runner never submitted')
        try:
            before = first.inspect('live')
            second = BracketRunner(camera, self.journal)
            with self.assertRaises(WorkflowError):
                second.run('live', {'exposures_us': [5000]})
            self.assertEqual(before, first.inspect('live'))
            with self.assertRaises(WorkflowError):
                second.run('intruder', {'exposures_us': [5000]})
            with self.assertRaises(WorkflowError):
                second.inspect('intruder')
            self.assertEqual(1, len(camera.captures))
        finally:
            camera.finish_active()
            thread.join(5)
        self.assertFalse(thread.is_alive(), 'first direct runner failed to stop')
        self.assertEqual([], failures)
        self.assertEqual('completed', results[0]['status'], results)

    def test_execution_lock_is_released_when_owner_process_is_killed(self):
        ready = self.root / 'owner-ready'
        code = (
            'from pathlib import Path\n'
            'import sys,time\n'
            'from daos.capability_runtime.bracket import BracketRunner\n'
            'runner=BracketRunner(None,Path(sys.argv[1]))\n'
            'with runner.execution():\n'
            ' Path(sys.argv[2]).write_text("locked",encoding="ascii")\n'
            ' time.sleep(60)\n'
        )
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.journal), str(ready)],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'lock owner failed before ready: ' +
                            (child.stderr.read() if child.poll() is not None else 'timed out'))
            camera = RecoveryCamera()
            runner = BracketRunner(camera, self.journal)
            with self.assertRaises(WorkflowError):
                runner.run('locked', {'exposures_us': [5000]})
            self.assertEqual([], camera.captures)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        # A stale lock-file path is harmless: the OS lock belonged to the process.
        camera = RecoveryCamera()
        result = BracketRunner(camera, self.journal).run('after-kill', {'exposures_us': [5000]})
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(1, len(camera.captures))


if __name__ == '__main__':
    unittest.main()

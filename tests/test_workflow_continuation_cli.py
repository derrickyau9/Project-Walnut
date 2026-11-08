"""Exercise saved-plan continuation and camera-free controls through the CLI."""
from __future__ import annotations

import builtins
from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

from daos.capability_runtime import __main__ as workflow_cli
from daos.capability_runtime.bracket import BracketRunner, WorkflowError


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContinuationCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / 'session'
        self.journal = self.data / 'workflow.sqlite3'

    def command(self, *arguments):
        return [sys.executable, '-B', '-m', 'daos.capability_runtime',
                '--data-dir', str(self.data), *arguments]

    def cli(self, *arguments, timeout=25):
        return subprocess.run(self.command(*arguments), cwd=ROOT, capture_output=True,
                              text=True, timeout=timeout)

    def result(self, completed, expected_code):
        self.assertEqual(expected_code, completed.returncode, completed.stderr + completed.stdout)
        try:
            return json.loads(completed.stdout)
        except (TypeError, ValueError) as error:
            self.fail('CLI did not emit JSON: ' + completed.stdout + completed.stderr + str(error))

    def seed(self, job='saved', *, plan=None, status='paused', reserved=False):
        plan = {'exposures_us': [5000]} if plan is None else plan
        runner = BracketRunner(None, self.journal)
        normalized = runner.prepare(job, plan)
        runner.set_desired(job, 'pause')
        runner._set_job(job, status)
        rid = None
        if reserved:
            rid = str(uuid.uuid4())
            self.assertTrue(runner._reserve(job, 0, rid))
        return runner, normalized, rid

    def photos(self):
        return sorted((self.data / 'simulator/photos').glob('*.dng'))

    def test_resume_uses_saved_plan_once_and_terminal_reopen_keeps_request_and_file(self):
        runner, plan, _ = self.seed(plan={'kind': 'flat', 'exposures_us': [7500],
                                          'requested_analogue_gain': 1.5})
        self.assertFalse((self.data / 'simulator').exists())
        result = self.result(self.cli('resume', '--job', 'saved'), 0)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(plan, result['plan'])
        self.assertEqual(1, len(result['steps']))
        step = result['steps'][0]
        self.assertEqual(7500, step['result']['actual_exposure_us'])
        self.assertEqual(1.5, step['result']['actual_analogue_gain'])
        self.assertEqual('flat', step['result']['simulation_scene'])
        self.assertEqual(1, len(self.photos()))
        photo = self.photos()[0]
        before = hashlib.sha256(photo.read_bytes()).hexdigest()
        with closing(sqlite3.connect(self.data / 'simulator/journal.sqlite3')) as camera:
            epoch = camera.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]
            self.assertEqual(1, camera.execute('SELECT COUNT(*) FROM captures').fetchone()[0])
        reopened = self.result(self.cli('resume', '--job', 'saved'), 0)
        self.assertEqual(result, reopened)
        self.assertEqual(step['request_id'], reopened['steps'][0]['request_id'])
        self.assertEqual(before, hashlib.sha256(photo.read_bytes()).hexdigest())
        with closing(sqlite3.connect(self.data / 'simulator/journal.sqlite3')) as camera:
            self.assertEqual(epoch, camera.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0],
                             'terminal reopen unnecessarily opened a camera authority')
            self.assertEqual(1, camera.execute('SELECT COUNT(*) FROM captures').fetchone()[0])
        self.assertEqual('run', runner.desired('saved'))

    def test_explicit_run_deliberately_continues_a_saved_paused_plan(self):
        runner, _, _ = self.seed()
        result = self.result(self.cli('run', '--job', 'saved', '--exposures-us', '5000'), 0)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual('run', runner.desired('saved'))
        self.assertEqual(1, len(result['steps']))
        self.assertEqual(1, len(self.photos()))

    def test_reserved_absent_cancel_stays_unknown_without_submitting_a_capture(self):
        _, _, rid = self.seed(plan={'exposures_us': [5000, 10000]}, reserved=True)
        cancelled = self.result(self.cli('cancel', '--job', 'saved'), 2)
        self.assertEqual('unknown', cancelled['status'], cancelled)
        self.assertEqual([(rid, 'unknown')], [(step['request_id'], step['status']) for step in cancelled['steps']])
        again = self.result(self.cli('cancel', '--job', 'saved'), 2)
        resumed = self.result(self.cli('resume', '--job', 'saved'), 2)
        for result in (again, resumed):
            self.assertEqual('unknown', result['status'])
            self.assertEqual(rid, result['steps'][0]['request_id'])
            self.assertEqual(1, len(result['steps']))
        self.assertEqual([], self.photos())
        with closing(sqlite3.connect(self.data / 'simulator/journal.sqlite3')) as camera:
            self.assertEqual(0, camera.execute('SELECT COUNT(*) FROM captures').fetchone()[0],
                             'reconciling an absent reserved ID submitted a new shot')

    def test_pause_list_and_inspect_neither_open_nor_import_an_authority(self):
        runner, _, _ = self.seed(status='pending')
        original_import = builtins.__import__
        def no_native_authority(name, *args, **kwargs):
            if name == 'camera.camerad.native_client' or name.startswith('camera.camerad.native_client.'):
                raise AssertionError('camera-free command imported a native authority')
            return original_import(name, *args, **kwargs)
        with mock.patch.object(workflow_cli, 'CameraService', side_effect=AssertionError('camera was opened')), \
             mock.patch('builtins.__import__', side_effect=no_native_authority):
            for arguments in (['pause', '--job', 'saved'], ['list'], ['inspect', '--job', 'saved']):
                with self.subTest(command=arguments[0]):
                    output, errors = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        code = workflow_cli.main(['--data-dir', str(self.data), '--authority', 'native', *arguments])
                    if arguments[0] == 'inspect':
                        # Inspect preserves the CLI's status-specific exit code;
                        # a saved pending/paused job is readable without a camera.
                        self.assertIn(code, (0, 1), errors.getvalue())
                    else:
                        self.assertEqual(0, code, errors.getvalue())
                    result = json.loads(output.getvalue())
                    self.assertTrue(result)
        self.assertEqual('pause', runner.desired('saved'))
        self.assertFalse((self.data / 'simulator').exists())

    def test_terminal_resume_and_run_do_not_open_an_authority(self):
        first = self.result(self.cli('run', '--job', 'done', '--exposures-us', '5000'), 0)
        with mock.patch.object(workflow_cli, 'CameraService', side_effect=AssertionError('terminal job opened a camera')):
            for arguments in (['resume', '--job', 'done'], ['run', '--job', 'done', '--exposures-us', '5000']):
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = workflow_cli.main(['--data-dir', str(self.data), *arguments])
                self.assertEqual(0, code, errors.getvalue())
                self.assertEqual(first, json.loads(output.getvalue()))
        self.assertEqual(1, len(self.photos()))

    def test_list_without_a_journal_does_not_create_camera_or_data_directory(self):
        result = self.result(self.cli('list'), 0)
        self.assertEqual([], result)
        self.assertFalse(self.data.exists())

    def start_interval_after_first_shot(self, job):
        child = subprocess.Popen(self.command('run', '--job', job, '--kind', 'interval',
                                             '--interval-ms', '60000', '--exposures-us', '5000', '10000'),
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_child, child)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if child.poll() is not None:
                output, errors = child.communicate(timeout=2)
                self.fail('interval CLI exited before first shot: ' + errors + output)
            if self.journal.exists():
                try:
                    result = BracketRunner(None, self.journal).inspect(job)
                    if (result['status'] == 'running' and len(result['steps']) == 1 and
                            result['steps'][0]['status'] == 'completed'):
                        return child, result['steps'][0]['request_id']
                except (WorkflowError, sqlite3.Error):
                    pass
            time.sleep(.01)
        self.fail('interval CLI did not reach its first completed shot')

    @staticmethod
    def stop_child(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)

    def test_pause_signals_running_cli_without_starting_a_competing_camera(self):
        child, rid = self.start_interval_after_first_shot('live-pause')
        ack = self.result(self.cli('pause', '--job', 'live-pause'), 0)
        self.assertEqual('pause', ack['desired'])
        output, errors = child.communicate(timeout=10)
        result = json.loads(output)
        self.assertEqual('paused', result['status'], errors + output)
        self.assertEqual([(rid, 'completed')], [(step['request_id'], step['status']) for step in result['steps']])
        self.assertEqual(1, len(self.photos()))
        cancelled = self.result(self.cli('cancel', '--job', 'live-pause'), 130)
        self.assertEqual('cancelled', cancelled['status'], cancelled)
        self.assertEqual(rid, cancelled['steps'][0]['request_id'])
        self.assertEqual(1, len(self.photos()))

    def test_cancel_signals_running_cli_and_distinguishes_ack_from_final_result(self):
        child, rid = self.start_interval_after_first_shot('live-cancel')
        ack = self.result(self.cli('cancel', '--job', 'live-cancel'), 0)
        self.assertEqual('cancel', ack['desired'])
        output, errors = child.communicate(timeout=10)
        self.assertEqual(130, child.returncode, errors + output)
        result = json.loads(output)
        self.assertEqual('cancelled', result['status'], result)
        self.assertEqual([(rid, 'completed')], [(step['request_id'], step['status']) for step in result['steps']])
        self.assertEqual(1, len(self.photos()))
        inspected = self.result(self.cli('inspect', '--job', 'live-cancel'), 130)
        self.assertEqual(result, inspected)


if __name__ == '__main__':
    unittest.main()

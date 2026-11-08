import http.client
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from camera.camerad.native_service import NativeService
from camera.camerad.service import CameraService, CameraError, DEFAULTS
from camera.camerad.server import BoundedHTTPServer, handler_for

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [Path(os.environ['HF_NATIVE_DAEMON'])] if os.environ.get('HF_NATIVE_DAEMON') else [
    ROOT / 'build/native/Release/hf-camerad.exe', ROOT / 'build/native/hf-camerad']
DAEMON = next((path.resolve() for path in CANDIDATES if path.is_file()), None)


@unittest.skipUnless(DAEMON, 'build hf-camerad with python tools/build_native.py')
class NativeAuthorityHttpTests(unittest.TestCase):
    def request(self, server, method, route, body=None, binary=False):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            connection.request(method, route, json.dumps(body) if body is not None else None,
                               {'Content-Type': 'application/json'} if body is not None else {})
            response = connection.getresponse()
            data = response.read()
            return response.status, data if binary else json.loads(data)
        finally:
            connection.close()

    def test_native_authority_preview_capture_and_workflow_through_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = NativeService(tmp, command=[str(DAEMON)])
            server = BoundedHTTPServer(('127.0.0.1', 0), handler_for(service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                _, state = self.request(server, 'GET', '/api/v1/state')
                self.assertEqual(state['authority'], 'native')
                _, lease = self.request(server, 'POST', '/api/v1/control/acquire', {'owner': 'native-http'})
                auth = {'token': lease['token'], 'epoch': lease['epoch']}
                status, _ = self.request(server, 'POST', '/api/v1/preview/start', auth)
                self.assertEqual(status, 200)
                status, image = self.request(server, 'GET', '/api/v1/preview.png', binary=True)
                self.assertEqual(status, 200)
                self.assertTrue(image.startswith(b'\x89PNG'))
                rid = str(uuid.uuid4())
                request = dict(DEFAULTS, request_id=rid, token=lease['token'], control_epoch=lease['epoch'])
                status, accepted = self.request(server, 'POST', '/api/v1/captures', request)
                self.assertEqual(status, 200)
                self.assertEqual(accepted['authority'], 'native')
                end = time.monotonic() + 10
                while time.monotonic() < end:
                    _, record = self.request(server, 'GET', '/api/v1/captures/' + rid)
                    if record['status'] == 'complete': break
                    time.sleep(.02)
                self.assertEqual(record['status'], 'complete', record)
                self.assertTrue(Path(record['file_path']).is_file())
                self.request(server, 'POST', '/api/v1/control/release', auth)
                status, _ = self.request(server, 'POST', '/api/v1/workflows',
                                         {'job_id': 'native-http-sequence', 'plan': {'exposures_us': [5000, 10000]}})
                self.assertEqual(status, 200)
                while time.monotonic() < end:
                    _, job = self.request(server, 'GET', '/api/v1/workflows/native-http-sequence')
                    if job['status'] in {'completed', 'failed', 'unknown'}: break
                    time.sleep(.02)
                self.assertEqual(job['status'], 'completed', job)
                self.assertTrue(all(step['result']['authority'] == 'native' for step in job['steps']))
            finally:
                server.shutdown(); server.server_close(); thread.join()
                service.close()

    def test_authorities_fence_each_other_and_reject_foreign_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            native_root = Path(tmp) / 'native'
            native = NativeService(native_root, command=[str(DAEMON)])
            try:
                with self.assertRaises(CameraError):
                    CameraService(native_root)
            finally:
                native.close()
            with self.assertRaisesRegex(CameraError, 'another authority'):
                CameraService(native_root)
            with closing(sqlite3.connect(native_root / 'journal.sqlite3')) as database:
                self.assertEqual(database.execute("SELECT value FROM meta WHERE key='authority'").fetchone()[0], 'native')
            reference_root = Path(tmp) / 'reference'
            reference = CameraService(reference_root)
            try:
                with self.assertRaises(CameraError):
                    NativeService(reference_root, command=[str(DAEMON)])
            finally:
                reference.close()
            with self.assertRaises(CameraError):
                NativeService(reference_root, command=[str(DAEMON)])

    def test_independent_native_cli_workflow_and_offline_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_root = Path(tmp) / 'capture'
            command = [sys.executable, '-m', 'camera.camerad', '--data-dir', str(capture_root),
                       '--authority', 'native', '--native-daemon', str(DAEMON), 'capture', '--exposure-us', '12000']
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            record = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(record['status'], 'complete')
            self.assertEqual(record['authority'], 'native')
            diagnose = subprocess.run([sys.executable, '-m', 'tools.diagnostics', '--data-dir', str(capture_root), '--offline-only'],
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(diagnose.returncode, 0, diagnose.stdout + diagnose.stderr)
            self.assertTrue(json.loads(diagnose.stdout)['ok'])
            command = [sys.executable, '-m', 'daos.capability_runtime', '--data-dir', str(Path(tmp) / 'workflow'),
                       '--authority', 'native', '--native-daemon', str(DAEMON), 'run', '--job', 'authority-interval',
                       '--kind', 'interval', '--interval-ms', '10', '--exposures-us', '5000', '10000']
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            job = json.loads(result.stdout)
            self.assertEqual(job['status'], 'completed')
            self.assertTrue(all(step['result']['authority'] == 'native' for step in job['steps']))

    @unittest.skipUnless(os.environ.get('HF_FULL_FRAME_TEST') == '1' and importlib.util.find_spec('tifffile'),
                         'enable full-frame test and install independent TIFF decoder')
    def test_native_authority_full_frame_matches_python_bayer(self):
        import numpy as np
        import tifffile
        from simulation.bayer import generate_bayer
        with tempfile.TemporaryDirectory() as tmp:
            service = NativeService(tmp, command=[str(DAEMON)])
            try:
                lease = service.acquire('full-frame-authority')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'],
                               sensor_mode='sim-full', requested_exposure_us=12500, requested_analogue_gain=1.25)
                service.capture(request)
                end = time.monotonic() + 20
                while service.active and time.monotonic() < end:
                    time.sleep(.02)
                record = service.query_capture(request['request_id'])
                self.assertEqual(record['status'], 'complete', record)
                decoded = tifffile.imread(record['file_path'])
                expected = generate_bayer(6064, 4040, 12500, 1.25)
                self.assertEqual(decoded.shape, (4040, 6064))
                self.assertTrue(np.array_equal(decoded.reshape(-1), np.frombuffer(expected, dtype=np.uint16)))
            finally:
                try:
                    service.close()
                except CameraError:
                    service.abort()


if __name__ == '__main__':
    unittest.main()

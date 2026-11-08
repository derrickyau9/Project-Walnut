import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from camera.camerad.service import CameraService, DEFAULTS, CameraError
from camera.dng.inspect import inspect_dng
from simulation.bayer import generate_bayer

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [Path(os.environ['HF_NATIVE_WORKER'])] if os.environ.get('HF_NATIVE_WORKER') else [
    ROOT / 'build/native/Release/hf-native-worker.exe', ROOT / 'build/native/hf-native-worker']
WORKER = next((path.resolve() for path in CANDIDATES if path.is_file()), None)


@unittest.skipUnless(WORKER, 'build native worker with python tools/build_native.py')
class NativeServiceTests(unittest.TestCase):
    def test_native_worker_service_preserves_late_output_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CameraService(tmp, backend='native', native_worker=WORKER)
            try:
                lease = service.acquire('native-collision-test')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'],
                               control_epoch=lease['epoch'], fault='delay')
                service.capture(request)
                staging = service.photos / (request['request_id'] + '.dng.partial')
                final = service.photos / (request['request_id'] + '.dng')
                staging.write_bytes(b'foreign staging')
                final.write_bytes(b'foreign final')
                end = time.monotonic() + 10
                while service.active and time.monotonic() < end:
                    time.sleep(.01)
                self.assertFalse(service.active)
                self.assertEqual(service.query_capture(request['request_id'])['status'], 'unknown')
                self.assertEqual(staging.read_bytes(), b'foreign staging')
                self.assertEqual(final.read_bytes(), b'foreign final')
                self.assertFalse(list(service.photos.glob('.capture-*')))
            finally:
                service.close()

    def test_compiled_worker_commit_roundtrip_and_duplicate_fencing(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CameraService(tmp, backend='native', native_worker=WORKER)
            try:
                lease = service.acquire('native-integration')
                for scene in ('scene', 'dark', 'flat'):
                    request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'],
                                   requested_exposure_us=12500, requested_analogue_gain=1.5, simulation_scene=scene)
                    self.assertEqual(service.capture(request)['backend'], 'native')
                    end = time.monotonic() + 10
                    while service.active and time.monotonic() < end:
                        time.sleep(.01)
                    record = service.query_capture(request['request_id'])
                    self.assertEqual(record['status'], 'complete', record)
                    self.assertEqual(record['backend'], 'native')
                    path = Path(record['file_path'])
                    self.assertEqual(inspect_dng(path)['request_id'], request['request_id'])
                    if importlib.util.find_spec('tifffile'):
                        import tifffile
                        self.assertEqual(tifffile.imread(path).reshape(-1).tolist(), list(generate_bayer(128, 96, 12500, 1.5, scene=scene)))
                    before = path.stat().st_mtime_ns
                    self.assertEqual(service.capture(request), record)
                    self.assertEqual(path.stat().st_mtime_ns, before)
                    with self.assertRaises(CameraError):
                        service.capture(dict(request, requested_exposure_us=13000))
                self.assertEqual(len(list(service.photos.glob('*.dng'))), 3)
            finally:
                service.close()

    def test_native_offline_interval_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, '-m', 'daos.capability_runtime', '--data-dir', tmp,
                       '--backend', 'native', '--native-worker', str(WORKER), 'run', '--job', 'native-interval',
                       '--kind', 'interval', '--interval-ms', '10', '--exposures-us', '5000', '10000']
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            job = json.loads(result.stdout)
            self.assertEqual(job['status'], 'completed')
            self.assertTrue(all(step['result']['backend'] == 'native' for step in job['steps']))
            self.assertEqual(len(list((Path(tmp) / 'simulator/photos').glob('*.dng'))), 2)


if __name__ == '__main__':
    unittest.main()

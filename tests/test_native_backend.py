import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

from camera.camerad.native_backend import NativeWorker, NativeBackendError
from camera.camerad.service import CameraService, DEFAULTS

FAKE = '''
import sys,json,time,os
from pathlib import Path
from camera.dng.hf_dng import write_dng
from simulation.bayer import generate_bayer
mode=MODE
args=dict(zip(sys.argv[1::2],sys.argv[2::2]))
if mode=='hang': time.sleep(10)
path=Path(args['--output'])
width,height=int(args['--width']),int(args['--height'])
exposure,gain=int(args['--exposure-us']),float(args['--gain'])
if mode!='no-output':
 write_dng(path,width,height,generate_bayer(width,height,exposure,gain),
  {'request_id':args['--request-id'] if mode!='wrong-dng-id' else 'other'})
if mode=='exit': sys.exit(7)
if mode=='bad-json': print('{'); sys.exit(0)
if mode=='large-output': print('x'*100000); sys.exit(0)
result=dict(schema_version=1,source='synthetic-native',simulated=True,width=width,height=height,
 bit_depth=14,cfa='RGGB',request_id=args['--request-id'],frame_id=args['--request-id'],
 sensor_id=args['--sensor-id'],calibration_id=args['--calibration-id'],
 requested_exposure_us=exposure,actual_exposure_us=exposure,
 requested_analogue_gain=gain,actual_analogue_gain=gain,
 captured_at_unix_ns=time.time_ns(),output_bytes=path.stat().st_size if path.exists() else 0)
if mode=='wrong-identity': result['request_id']='wrong'
if mode=='false-actual': result['actual_exposure_us']=0
print(json.dumps(result))
'''


class NativeAdapterTests(unittest.TestCase):
    def worker(self, root, mode, timeout=3):
        script = Path(root) / (mode + '.py')
        script.write_text('import sys\nsys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[1])) + ')\nMODE=' + repr(mode) + '\n' + FAKE, encoding='utf-8')
        return NativeWorker([sys.executable, str(script)], timeout_seconds=timeout)

    def record(self):
        rid = str(uuid.uuid4())
        return dict(DEFAULTS, request_id=rid, capture_id=rid, sensor_id='HF-Sim', calibration_id='synthetic-uncalibrated', deadline_unix_ms=None)

    def test_success_and_failure_protocol_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.record()
            result = self.worker(tmp, 'ok').capture(Path(tmp) / 'ok.dng', 32, 24, record, threading.Event())
            self.assertEqual(result['request_id'], record['request_id'])
            for mode in ('no-output', 'exit', 'bad-json', 'large-output', 'wrong-identity', 'false-actual', 'wrong-dng-id'):
                with self.subTest(mode=mode), self.assertRaises(NativeBackendError):
                    self.worker(tmp, mode).capture(Path(tmp) / (mode + '.dng'), 32, 24, record, threading.Event())

    def test_timeout_cancel_and_expired_deadline_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.record()
            start = time.monotonic()
            with self.assertRaisesRegex(NativeBackendError, 'time budget'):
                self.worker(tmp, 'hang', .1).capture(Path(tmp) / 'timeout.dng', 32, 24, record, threading.Event())
            self.assertLess(time.monotonic() - start, 3)
            cancel = threading.Event()
            timer = threading.Timer(.1, cancel.set)
            timer.start()
            try:
                with self.assertRaisesRegex(NativeBackendError, 'cancelled'):
                    self.worker(tmp, 'hang').capture(Path(tmp) / 'cancel.dng', 32, 24, record, cancel)
            finally:
                timer.join()
            record['deadline_unix_ms'] = 0
            with self.assertRaisesRegex(NativeBackendError, 'deadline'):
                self.worker(tmp, 'ok').capture(Path(tmp) / 'expired.dng', 32, 24, record, threading.Event())
            self.assertFalse((Path(tmp) / 'expired.dng').exists())

    def test_service_commits_only_valid_worker_and_reconciles_same_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CameraService(Path(tmp) / 'data', backend='native', native_worker=sys.executable)
            try:
                service.native_worker = self.worker(tmp, 'wrong-identity')
                lease = service.acquire('bridge-test')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'])
                service.capture(request)
                end = time.monotonic() + 10
                while service.active and time.monotonic() < end:
                    time.sleep(.01)
                self.assertEqual(service.query_capture(request['request_id'])['status'], 'failed')
                self.assertFalse(list(service.photos.glob('*.dng')))
                service.native_worker = self.worker(tmp, 'ok')
                request['request_id'] = str(uuid.uuid4())
                service.capture(request)
                while service.active and time.monotonic() < end:
                    time.sleep(.01)
                complete = service.query_capture(request['request_id'])
                self.assertEqual(complete['status'], 'complete', complete)
                self.assertEqual(complete['backend'], 'native')
                path = Path(complete['file_path'])
                prior = path.stat().st_mtime_ns
                self.assertEqual(service.capture(request), complete)
                self.assertEqual(prior, path.stat().st_mtime_ns)
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()

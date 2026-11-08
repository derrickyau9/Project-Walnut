"""Actual loopback calibration API and bound-record SDK readback."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid

from camera.calibration import create_binding,create_uncalibrated_package
from camera.camerad.http_client import HttpCameraClient
from camera.camerad.server import BoundedHTTPServer,handler_for
from camera.camerad.service import CameraService,CameraError,DEFAULTS,TERMINAL


class CalibrationHttpTests(unittest.TestCase):
    authority='python'

    def test_controlled_binding_capture_readback_clear_and_persistent_configure(self):
        with tempfile.TemporaryDirectory() as tmp:
            if self.authority=='native':
                from camera.camerad.native_service import NativeService
                service=NativeService(tmp,command=[os.environ['HF_NATIVE_DAEMON']])
            else:service=CameraService(tmp)
            server=BoundedHTTPServer(('127.0.0.1',0),handler_for(service))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            client=HttpCameraClient('http://127.0.0.1:'+str(server.server_port),timeout_seconds=5)
            rid=str(uuid.uuid4())
            try:
                lease=client.acquire('http-calibration')
                client.configure(lease['token'],lease['epoch'],{'requested_exposure_us':17600})
                binding=create_binding(create_uncalibrated_package('HF-Sim','explicit-none','1'))
                with self.assertRaises(CameraError):client.bind_calibration('stale',lease['epoch'],binding)
                self.assertIsNone(client.get_state()['calibration']['binding_id'])
                selected=client.bind_calibration(lease['token'],lease['epoch'],binding)
                self.assertEqual(selected['binding_id'],binding['binding_id'])
                client.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                end=time.monotonic()+10
                while time.monotonic()<end:
                    record=client.query_capture(rid)
                    if record['status'] in TERMINAL:break
                    time.sleep(.01)
                self.assertEqual(record['status'],'complete',record)
                self.assertEqual(record['calibration_binding'],binding)
                self.assertEqual(record['capture_record_version'],2)
                self.assertIs(record['hardware_calibrated'],False)
                while client.get_state()['camera_state']!='idle':time.sleep(.01)
                cleared=client.clear_calibration(lease['token'],lease['epoch'])
                self.assertIsNone(cleared['binding_id'])
                self.assertEqual(client.query_capture(rid)['calibration_id'],binding['binding_id'])
                self.assertEqual(client.get_state()['settings']['requested_exposure_us'],17600)
                client.release(lease['token'],lease['epoch'])
            finally:
                client.close();server.shutdown();server.server_close();worker.join(timeout=5)
                while service.active:time.sleep(.01)
                service.close()


@unittest.skipUnless(os.environ.get('HF_NATIVE_DAEMON'),'explicit R9 native authority required')
class NativeCalibrationHttpTests(CalibrationHttpTests):
    authority='native'


if __name__=='__main__':unittest.main()

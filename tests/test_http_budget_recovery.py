"""A real expired HTTP reply must not undo or replay an admitted capture."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from camera.camerad.http_client import HttpCameraClient, HttpClientError
from camera.camerad.server import BoundedHTTPServer, handler_for
from camera.camerad.service import CameraService


class AcceptedCaptureDeadlineTests(unittest.TestCase):
    def test_reply_deadline_preserves_one_admitted_capture_for_original_id_query(self):
        with tempfile.TemporaryDirectory(prefix='hf-http-admitted-') as directory:
            service=CameraService(directory)
            server=BoundedHTTPServer(('127.0.0.1',0),handler_for(service),max_workers=1,request_timeout_seconds=2)
            thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
            thread.start()
            client=HttpCameraClient('http://127.0.0.1:'+str(server.server_port),timeout_seconds=4)
            admitted=threading.Event(); release=threading.Event()
            original=service.capture
            def hold_reply(body):
                record=original(body)
                admitted.set()
                if not release.wait(5):
                    raise RuntimeError('test reply gate not released')
                return record
            rid=str(uuid.uuid4())
            lease=service.acquire('deadline-reconciliation')
            request={'request_id':rid,'token':lease['token'],'control_epoch':lease['epoch'],
                     'sensor_mode':'sim-small','requested_exposure_us':10000,
                     'requested_analogue_gain':1.0,'output_format':'dng'}
            try:
                with mock.patch.object(service,'capture',side_effect=hold_reply) as capture:
                    try:
                        with self.assertRaises(HttpClientError) as caught:
                            client.capture(request)  # Sent once; deadline closes only the reply.
                        self.assertEqual(caught.exception.code,'outcome_unknown')
                        self.assertEqual(caught.exception.request_id,rid)
                        self.assertTrue(admitted.wait(1))
                    finally:
                        release.set()
                    until=time.monotonic()+3
                    while (server._slots._value!=1 or service.get_state()['camera_state']!='idle') and time.monotonic()<until:
                        time.sleep(.01)
                    self.assertEqual(server._slots._value,1)
                    server.request_timeout_seconds=10  # Reconciliation is not a 2s storage benchmark.
                    record=client.query_capture(rid)
                    self.assertEqual(record['status'],'complete')
                    self.assertEqual(record['request_id'],rid)
                    self.assertEqual(capture.call_count,1)
                    self.assertEqual(len(list((Path(directory)/'photos').glob('*.dng'))),1)
                    self.assertEqual(service.db.execute('SELECT COUNT(*) FROM captures').fetchone()[0],1)
                service.release(lease['token'],lease['epoch'])
            finally:
                release.set(); client.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)
                service.close()


if __name__=='__main__': unittest.main()

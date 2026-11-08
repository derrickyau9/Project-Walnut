import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
import uuid
from camera.camerad.server import handler_for, BoundedHTTPServer
from camera.camerad.service import CameraService, DEFAULTS


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = CameraService(self.tmp.name)
        self.server = BoundedHTTPServer(('127.0.0.1', 0), handler_for(self.service))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        while self.service.active:
            time.sleep(.01)
        self.service.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=headers or {})
        response = conn.getresponse()
        data = json.loads(response.read())
        status = response.status
        conn.close()
        return status, data

    def test_control_capture_and_query(self):
        status, caps = self.request('GET', '/api/v1/capabilities')
        self.assertEqual(status, 200)
        self.assertTrue(caps['simulated'])
        headers = {'Content-Type': 'application/json'}
        status, lease = self.request('POST', '/api/v1/control/acquire', {'owner': 'HTTP test'}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(lease['lease_seconds'], 30)
        status, renewal = self.request('POST', '/api/v1/control/heartbeat', {'token': lease['token'], 'epoch': lease['epoch']}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(renewal['epoch'], lease['epoch'])
        self.assertNotIn('token', renewal)
        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'])
        status, accepted = self.request('POST', '/api/v1/captures', request, headers)
        self.assertEqual(status, 200)
        self.assertEqual(accepted['status'], 'accepted')
        for _ in range(200):
            _, result = self.request('GET', '/api/v1/captures/' + request['request_id'])
            if result['status'] in ('complete', 'failed', 'cancelled', 'unknown'):
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['actual_exposure_us'], request['requested_exposure_us'])
        status, _ = self.request('POST', '/api/v1/control/release', {'token': lease['token'], 'epoch': lease['epoch']}, headers)
        self.assertEqual(status, 200)

    def test_rejects_foreign_origin_host_and_non_json(self):
        status, _ = self.request('POST', '/api/v1/control/acquire', {'owner': 'x'}, {'Content-Type': 'application/json', 'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/v1/control/acquire', {'owner': 'x'}, {'Content-Type': 'text/plain'})
        self.assertEqual(status, 400)
        status, _ = self.request('GET', '/api/v1/state', headers={'Host': 'evil.example'})
        self.assertEqual(status, 403)
        status, _ = self.request('GET', '/../journal.sqlite3')
        self.assertEqual(status, 404)

    def test_preview_requires_control_to_start_and_serves_png(self):
        headers = {'Content-Type': 'application/json'}
        status, _ = self.request('GET', '/api/v1/preview.png')
        self.assertEqual(status, 503)
        status, _ = self.request('POST', '/api/v1/preview/start', {'token': 'bad', 'epoch': 1}, headers)
        self.assertEqual(status, 403)
        _, lease = self.request('POST', '/api/v1/control/acquire', {'owner': 'preview'}, headers)
        status, state = self.request('POST', '/api/v1/preview/start', {'token': lease['token'], 'epoch': lease['epoch']}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(state['running'])
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('GET', '/api/v1/preview.png')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'image/png')
        self.assertIsNotNone(response.getheader('X-Frame-Id'))
        self.assertTrue(response.read().startswith(b'\x89PNG\r\n\x1a\n'))
        conn.close()
        status, state = self.request('POST', '/api/v1/preview/stop', {'token': lease['token'], 'epoch': lease['epoch']}, headers)
        self.assertEqual(status, 200)
        self.assertFalse(state['running'])

    def test_workflow_execution_via_http(self):
        headers = {'Content-Type': 'application/json'}
        plan = {'kind': 'interval', 'exposures_us': [5000, 10000], 'interval_ms': 100}
        status, _ = self.request('POST', '/api/v1/workflows', {'job_id': 'http-sequence', 'plan': plan}, headers)
        self.assertEqual(status, 200)
        for _ in range(300):
            status, job = self.request('GET', '/api/v1/workflows/http-sequence')
            self.assertEqual(status, 200)
            if job['status'] in ('completed', 'failed', 'cancelled', 'unknown'):
                break
            time.sleep(.02)
        self.assertEqual(job['status'], 'completed', job)
        status, jobs = self.request('GET', '/api/v1/workflows')
        self.assertEqual(status, 200)
        self.assertEqual(jobs[0]['job_id'], 'http-sequence')

    def test_disconnect_and_explicit_reconnect_via_http(self):
        headers = {'Content-Type': 'application/json'}
        _, lease = self.request('POST', '/api/v1/control/acquire', {'owner': 'offline test'}, headers)
        auth = {'token': lease['token'], 'epoch': lease['epoch']}
        self.request('POST', '/api/v1/preview/start', auth, headers)
        req = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'], fault='disconnect')
        self.request('POST', '/api/v1/captures', req, headers)
        for _ in range(200):
            _, record = self.request('GET', '/api/v1/captures/' + req['request_id'])
            if record['status'] == 'failed' and not self.service.active:
                break
            time.sleep(.01)
        _, state = self.request('GET', '/api/v1/state')
        self.assertFalse(state['connected'])
        status, error = self.request('GET', '/api/v1/preview.png')
        self.assertEqual(status, 503)
        self.assertEqual(error['error']['code'], 'device_disconnected')
        status, _ = self.request('POST', '/api/v1/device/reconnect', {'token': 'invalid', 'epoch': lease['epoch']}, headers)
        self.assertEqual(status, 403)
        status, result = self.request('POST', '/api/v1/device/reconnect', auth, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result['connected'])

    def test_request_workers_are_bounded(self):
        server = BoundedHTTPServer(('127.0.0.1', 0), handler_for(self.service), max_workers=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first = second = None
        try:
            first = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
            first.sendall((f'POST /api/v1/control/acquire HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                           'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{').encode())
            for _ in range(100):
                if server._slots._value == 0:
                    break
                time.sleep(.01)
            self.assertEqual(server._slots._value, 0)
            second = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
            second.settimeout(2)
            try:
                second.sendall(b'GET / HTTP/1.1\r\n\r\n')
                self.assertEqual(second.recv(1), b'')
            except (ConnectionResetError, ConnectionAbortedError):
                pass
        finally:
            if first:
                first.close()
            if second:
                second.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()

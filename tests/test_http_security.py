"""Raw loopback probes for strict HTTP admission and finite server budgets."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from camera.camerad.server import BoundedHTTPServer, handler_for
from camera.camerad.service import CameraError, CameraService, DEFAULTS


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _serve(service, *, timeout=10, max_workers=16):
    class Handler(handler_for(service)):
        def log_message(self, *args):
            pass
    server = BoundedHTTPServer(('127.0.0.1', 0), Handler, max_workers=max_workers,
                               request_timeout_seconds=timeout)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(server, method, path, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    payload = json.dumps(body).encode() if body is not None else None
    try:
        connection.request(method, path, payload, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        content = response.read()
        value = json.loads(content) if response.getheader('Content-Type', '').startswith('application/json') else content
        return response.status, value
    finally:
        connection.close()


def _raw(server, body, *, headers=(), target='/api/v1/control/acquire', method='POST',
         length=None, include_length=True):
    fields = [('Host', f'127.0.0.1:{server.server_port}'), ('Content-Type', 'application/json')]
    fields += list(headers)
    if include_length and not any(key.lower() == 'content-length' for key, _ in fields):
        fields.append(('Content-Length', str(len(body) if length is None else length)))
    wire = (method + ' ' + target + ' HTTP/1.1\r\n' +
            ''.join(key + ': ' + value + '\r\n' for key, value in fields) + '\r\n').encode('ascii') + body
    with socket.create_connection(('127.0.0.1', server.server_port), timeout=3) as connection:
        connection.sendall(wire)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b''.join(chunks)
    if not response:
        return None, None
    head, _, content = response.partition(b'\r\n\r\n')
    return int(head.split(b' ', 2)[1]), json.loads(content)


class HttpAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hf-http-admission-')
        self.service = CameraService(self.directory.name)
        self.serving = _serve(self.service)
        self.server = self.serving.__enter__()

    def tearDown(self):
        self.serving.__exit__(None, None, None)
        deadline = time.monotonic() + 5
        while self.service.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.service.close()
        self.directory.cleanup()

    def test_rejects_short_ambiguous_or_unsupported_framing_before_control(self):
        body = b'{"owner":"must-not-acquire"}'
        cases = [
            {'length': len(body) + 50},
            {'headers': [('Content-Length', str(len(body))), ('Content-Length', '0')]},
            {'headers': [('Content-Length', str(len(body))), ('Content-Length', str(len(body)))]},
            {'headers': [('Transfer-Encoding', 'chunked')]},
            {'headers': [('Transfer-Encoding', 'identity')]},
            {'headers': [('Content-Length', '+' + str(len(body)))]},
            {'include_length': False},
            {'headers': [('Content-Type', 'text/plain')]},
            {'headers': [('Content-Type', 'application/json')]},
            {'headers': [('Content-Encoding', 'gzip')]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                status, result = _raw(self.server, body, **changes)
                self.assertEqual(status, 400)
                self.assertEqual(result['error']['code'], 'invalid_request')
                self.assertIsNone(self.service.get_state()['control'])
        status, lease = _request(self.server, 'POST', '/api/v1/control/acquire', {'owner': 'valid-after-malformed'})
        self.assertEqual(status, 200)
        self.service.release(lease['token'], lease['epoch'])

    def test_host_origin_and_request_target_are_unambiguous(self):
        body = b'{"owner":"must-not-acquire"}'
        origin = f'http://127.0.0.1:{self.server.server_port}'
        cases = [
            ({'headers': [('Host', 'foreign.invalid')]}, 403),
            ({'headers': [('Origin', origin), ('Origin', 'http://foreign.invalid')]}, 403),
            ({'headers': [('Origin', 'http://foreign.invalid')]}, 403),
            ({'target': 'http://foreign.invalid/api/v1/control/acquire'}, 400),
            ({'target': '//foreign.invalid/api/v1/control/acquire'}, 400),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                status, _ = _raw(self.server, body, **changes)
                self.assertEqual(status, expected)
                self.assertIsNone(self.service.get_state()['control'])
        status, result = _raw(self.server, b'', method='GET', target='/api/v1/state',
                              headers=[('Origin', origin), ('Origin', 'http://foreign.invalid')])
        self.assertEqual(status, 403)
        self.assertEqual(result['error']['code'], 'control_denied')

    def test_json_is_utf8_unique_finite_and_depth_bounded(self):
        bodies = [
            b'{"owner":"first","owner":"last"}',
            b'{"owner":"unused-number","extra":NaN}',
            b'{"owner":"unused-number","extra":Infinity}',
            b'{"owner":"unused-number","extra":1e999}',
            b'{"owner":"bad-unicode","extra":"\\ud800"}',
            b'{"owner":"bad-key","\\udfff":0}',
            '{"owner":"utf16"}'.encode('utf-16'),
            b'{"owner":"deep","extra":' + b'[' * 40 + b'0' + b']' * 40 + b'}',
            b'{"owner":"recursive","extra":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}',
        ]
        for index, body in enumerate(bodies):
            with self.subTest(index=index):
                status, result = _raw(self.server, body)
                self.assertEqual(status, 400)
                self.assertEqual(result['error']['code'], 'invalid_request')
                self.assertIsNone(self.service.get_state()['control'])
        # Extra fields keep their existing API behavior; this is a JSON parser
        # boundary, not an expansion of the service's allowed-field policy.
        status, lease = _request(self.server, 'POST', '/api/v1/control/acquire',
                                 {'owner': 'camera-\U0001f4f7', 'unused': {'nested': [1, 2]}})
        self.assertEqual(status, 200)
        self.assertEqual(lease['owner'], 'camera-\U0001f4f7')
        self.service.release(lease['token'], lease['epoch'])

    def test_capture_routes_require_exact_segments_before_cancellation(self):
        _, lease = _request(self.server, 'POST', '/api/v1/control/acquire', {'owner': 'route-test'})
        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'],
                       control_epoch=lease['epoch'], fault='delay')
        self.assertEqual(_request(self.server, 'POST', '/api/v1/captures', request)[0], 200)
        request_id = request['request_id']
        status, _ = _request(self.server, 'GET', '/api/v1/captures/extra/' + request_id)
        self.assertEqual(status, 404)
        status, _ = _request(self.server, 'POST', '/api/v1/captures/extra/' + request_id + '/cancel',
                             {'token': lease['token'], 'epoch': lease['epoch']})
        self.assertEqual(status, 404)
        self.assertFalse(self.service.cancel.is_set())
        self.assertEqual(_request(self.server, 'GET', '/api/v1/captures/' + request_id)[0], 200)
        status, result = _request(self.server, 'POST', '/api/v1/captures/' + request_id + '/cancel',
                                  {'token': lease['token'], 'epoch': lease['epoch']})
        self.assertEqual(status, 200)
        self.assertTrue(result['requested'])
        deadline = time.monotonic() + 5
        while self.service.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.service.query_capture(request_id)['status'], 'cancelled')
        self.service.release(lease['token'], lease['epoch'])

    def test_workflow_query_route_and_static_console_remain_exact(self):
        status, _ = _request(self.server, 'POST', '/api/v1/workflows',
                             {'job_id': 'strict-route-workflow', 'plan': {'exposures_us': [10000]}})
        self.assertEqual(status, 200)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, job = _request(self.server, 'GET', '/api/v1/workflows/strict-route-workflow')
            if job['status'] in {'completed', 'failed', 'cancelled', 'unknown'}:
                break
            time.sleep(.01)
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(_request(self.server, 'GET', '/api/v1/workflows/extra/strict-route-workflow')[0], 404)
        status, html = _request(self.server, 'GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<html', html.lower())
        self.assertEqual(_request(self.server, 'GET', '/app.js')[0], 200)
        self.assertEqual(_request(self.server, 'GET', '/../journal.sqlite3')[0], 404)

    def test_get_errors_are_structured_and_do_not_stop_server(self):
        with mock.patch.object(self.service, 'get_state', side_effect=CameraError('journal_error', 'unavailable')):
            status, result = _request(self.server, 'GET', '/api/v1/state')
            self.assertEqual(status, 503)
            self.assertEqual(result['error']['code'], 'journal_error')
        with mock.patch.object(self.service, 'get_state', side_effect=RuntimeError('internal detail')):
            status, result = _request(self.server, 'GET', '/api/v1/state')
            self.assertEqual(status, 500)
            self.assertEqual(result['error']['code'], 'internal_error')
            self.assertNotIn('internal detail', result['error']['message'])
        self.assertEqual(_request(self.server, 'GET', '/api/v1/state')[0], 200)


class HttpServerBudgetTests(unittest.TestCase):
    def test_timeout_configuration_rejects_invalid_or_overflowing_values(self):
        for value in (True, 0, -1, float('nan'), float('inf'), 1e100, 10 ** 400):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    BoundedHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler,
                                      request_timeout_seconds=value)

    def test_header_and_body_dribbles_obey_total_deadline_and_release_slot(self):
        with tempfile.TemporaryDirectory(prefix='hf-http-budget-') as directory:
            service = CameraService(directory)
            try:
                with _serve(service, timeout=.15, max_workers=1) as server:
                    for mode in ('headers', 'body'):
                        with self.subTest(mode=mode):
                            connection = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
                            connection.settimeout(2)
                            stop = threading.Event()
                            if mode == 'headers':
                                prefix = b'POST /api/v1/control/acquire HTTP/1.1\r\n'
                                dribble = f'Host: 127.0.0.1:{server.server_port}\r\n'.encode()
                            else:
                                prefix = (f'POST /api/v1/control/acquire HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                                          'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n').encode()
                                dribble = b'{"owner":"never-admitted"}'
                            def send_slowly():
                                try:
                                    for byte in dribble:
                                        if stop.wait(.04):
                                            return
                                        connection.sendall(bytes([byte]))
                                except OSError:
                                    pass
                            started = time.monotonic()
                            connection.sendall(prefix)
                            writer = threading.Thread(target=send_slowly, daemon=True)
                            writer.start()
                            try:
                                try:
                                    closed = connection.recv(1) == b''
                                except (ConnectionResetError, ConnectionAbortedError):
                                    # Windows may report an abort/reset rather
                                    # than EOF when a deadline shuts down a
                                    # socket with unread request bytes.
                                    closed = True
                                self.assertTrue(closed)
                                self.assertLess(time.monotonic() - started, .6)
                            finally:
                                stop.set()
                                writer.join(timeout=2)
                                connection.close()
                            deadline = time.monotonic() + 1
                            while server._slots._value != 1 and time.monotonic() < deadline:
                                time.sleep(.005)
                            self.assertEqual(server._slots._value, 1)
                            self.assertIsNone(service.get_state()['control'])
                    # The 150 ms budget above tests hostile socket progress,
                    # not the latency of a subsequent SQLite durable commit.
                    # Use the normal service budget after proving both slots
                    # were returned, and deliberately exceed the short budget
                    # so this probe cannot reintroduce that timing assumption.
                    server.request_timeout_seconds = 10
                    original_acquire = service.acquire
                    def delayed_acquire(owner):
                        time.sleep(.22)
                        return original_acquire(owner)
                    with mock.patch.object(service, 'acquire', side_effect=delayed_acquire) as acquire:
                        status, lease = _request(server, 'POST', '/api/v1/control/acquire', {'owner': 'after-dribbles'})
                        acquire.assert_called_once_with('after-dribbles')
                    self.assertEqual(status, 200)
                    self.assertEqual(service.get_state()['control']['owner'], 'after-dribbles')
                    service.release(lease['token'], lease['epoch'])
            finally:
                service.close()


class NativeHttpErrorTests(unittest.TestCase):
    def test_invalid_unicode_cannot_kill_authority_and_dead_authority_returns_json(self):
        from camera.camerad.native_service import NativeService
        configured = os.environ.get('HF_NATIVE_DAEMON')
        name = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
        executable = configured or next((str(path) for path in (ROOT / 'build/native/Release' / name,
                                                                ROOT / 'build/native' / name) if path.is_file()), None)
        if not executable:
            self.skipTest('build hf-camerad or set HF_NATIVE_DAEMON for native HTTP boundary test')
        with tempfile.TemporaryDirectory(prefix='hf-native-http-boundary-') as directory:
            service = NativeService(directory, command=[executable])
            try:
                with _serve(service) as server:
                    pid = service.client.process.pid
                    status, lease = _request(server, 'POST', '/api/v1/control/acquire', {'owner': 'surviving-owner'})
                    self.assertEqual(status, 200)
                    with mock.patch.object(service.client, 'acquire', wraps=service.client.acquire) as forwarded:
                        status, result = _raw(server, b'{"owner":"\\ud800"}')
                        forwarded.assert_not_called()
                    self.assertEqual(status, 400)
                    self.assertEqual(result['error']['code'], 'invalid_request')
                    self.assertFalse(service.client.closed)
                    self.assertIsNone(service.client.process.poll())
                    self.assertEqual(service.client.process.pid, pid)
                    status, state = _request(server, 'GET', '/api/v1/state')
                    self.assertEqual(status, 200)
                    self.assertEqual(state['control']['owner'], 'surviving-owner')
                    self.assertEqual(state['control']['epoch'], lease['epoch'])
                    service.client.process.kill()
                    service.client.process.wait(timeout=3)
                    status, result = _request(server, 'GET', '/api/v1/state')
                    self.assertEqual(status, 503)
                    self.assertEqual(result['error']['code'], 'native_unavailable')
                    self.assertEqual(service.client.process.pid, pid)
                    self.assertIsNotNone(service.client.process.poll())
            finally:
                service.abort()


if __name__ == '__main__':
    unittest.main()

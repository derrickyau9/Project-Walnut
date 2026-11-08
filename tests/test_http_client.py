"""Loopback HTTP client integration and bounded transport fault tests."""
import concurrent.futures
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

from camera.camerad.http_client import HttpCameraClient, HttpClientError
from camera.camerad.server import BoundedHTTPServer, handler_for
from camera.camerad.service import CameraError, CameraService, DEFAULTS


ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'complete', 'failed', 'cancelled', 'unknown'}


class _QuietServer(BoundedHTTPServer):
    def handle_error(self, request, client_address):
        pass


@contextmanager
def _serve(handler):
    class QuietHandler(handler):
        def log_message(self, *args):
            pass
    server = _QuietServer(('127.0.0.1', 0), QuietHandler)
    server.mode = 'normal'
    server.seen = []
    server.records = {}
    server.active_requests = 0
    server.maximum_active = 0
    server.tracking_lock = threading.Lock()
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _origin(server, hostname='127.0.0.1'):
    return f'http://{hostname}:{server.server_port}'


def _request(origin, method, path, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', int(origin.rsplit(':', 1)[1]), timeout=3)
    payload = json.dumps(body).encode() if body is not None else None
    try:
        connection.request(method, path, payload, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _wait_capture(client, request_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = client.query_capture(request_id)
        if record and record['status'] in TERMINAL and client.get_state()['camera_state'] != 'capturing':
            return record
        time.sleep(.01)
    raise AssertionError('capture did not terminate')


class _FaultHandler(BaseHTTPRequestHandler):
    def _response(self, payload, status=200, *, content_type='application/json', length=True, extra=()):
        self.send_response(status)
        if content_type is not None:
            self.send_header('Content-Type', content_type)
        if length is True:
            self.send_header('Content-Length', str(len(payload)))
        elif length is not False:
            self.send_header('Content-Length', str(length))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (ConnectionError, OSError):
            pass

    def _handle(self, method, body=None):
        active_counted = True
        with self.server.tracking_lock:
            self.server.seen.append((method, self.path, body))
            self.server.active_requests += 1
            self.server.maximum_active = max(self.server.maximum_active, self.server.active_requests)
        try:
            mode = self.server.mode
            if method == 'POST' and self.path == '/api/v1/captures':
                self.server.records[body['request_id']] = dict(body, status='accepted')
            if mode == 'drop' and method == 'POST':
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            if mode == 'timeout':
                time.sleep(.35)
            if mode == 'serialized':
                time.sleep(.02)
                # Measure overlapping request work; a completed response may
                # wake its client before this handler returns from write().
                with self.server.tracking_lock:
                    self.server.active_requests -= 1
                    active_counted = False
            faults = {
                'malformed': b'not JSON',
                'duplicate': b'{"simulated":true,"simulated":false}',
                'nonfinite': b'{"bad":NaN}',
                'overflow': b'{"bad":1e999}',
                'array': b'[]',
            }
            if mode in faults:
                return self._response(faults[mode])
            if mode == 'oversized':
                return self._response(b'{}', length=256 * 1024 + 1)
            if mode == 'bad_content_type':
                return self._response(b'{}', content_type='text/html')
            if mode == 'missing_length':
                return self._response(json.dumps(CameraService.capabilities()).encode(), length=False)
            if mode == 'chunked':
                payload = json.dumps(CameraService.capabilities()).encode()
                encoded = format(len(payload), 'x').encode() + b'\r\n' + payload + b'\r\n0\r\n\r\n'
                return self._response(encoded, length=False, extra=(('Transfer-Encoding', 'chunked'),))
            if mode == 'duplicate_length':
                return self._response(b'{}', extra=(('Content-Length', '3'),))
            if mode == 'redirect':
                return self._response(b'{}', status=307,
                                      extra=(('Location', _origin(self.server) + '/must-not-follow'),))
            if mode == 'denied':
                return self._response(b'{"error":{"code":"control_denied","message":"invalid lease"}}', status=403)
            if mode in ('journal_error', 'internal_error'):
                return self._response(json.dumps({'error': {'code': mode, 'message': 'capture admission error'}}).encode(), status=503)
            if mode in ('wrong_id', 'unknown_status', 'dict_status'):
                request_id = body['request_id'] if body is not None else self.path.rsplit('/', 1)[-1]
                record = {'request_id': 'different-request' if mode == 'wrong_id' else request_id,
                          'status': {'unexpected': True} if mode == 'dict_status' else
                                    'invented' if mode == 'unknown_status' else 'accepted'}
                return self._response(json.dumps(record).encode())
            if mode.startswith('complete_'):
                request_id = body['request_id'] if body is not None else self.path.rsplit('/', 1)[-1]
                record = {'request_id': request_id, 'status': 'complete',
                          'frame_id': request_id, 'capture_id': request_id,
                          'actual_exposure_us': 10000, 'actual_analogue_gain': 1.0,
                          'file_path': 'protocol-fixture-only.dng'}
                if mode == 'complete_high_exposure':
                    record['actual_exposure_us'] = 30000001
                elif mode == 'complete_huge_exposure':
                    record['actual_exposure_us'] = 10 ** 1000
                elif mode == 'complete_high_gain':
                    record['actual_analogue_gain'] = 16.01
                elif mode == 'complete_huge_gain':
                    record['actual_analogue_gain'] = 1e300
                return self._response(json.dumps(record).encode())
            if method == 'GET' and self.path.startswith('/api/v1/captures/'):
                record = self.server.records.get(self.path.rsplit('/', 1)[-1])
                if record is None:
                    return self._response(b'{"error":{"code":"not_found","message":"unknown request"}}', status=404)
                return self._response(json.dumps(record).encode())
            if method == 'POST' and self.path == '/api/v1/captures':
                return self._response(json.dumps(self.server.records[body['request_id']]).encode())
            if self.path == '/api/v1/state':
                return self._response(json.dumps({
                    'connected': True, 'simulated': True, 'backend': 'python', 'authority': 'python',
                    'camera_state': 'idle', 'journal_healthy': True, 'control': None,
                    'settings': DEFAULTS, 'last_capture': None, 'pipeline_generation': 0,
                    'preview': {'running': False, 'simulated': True, 'frame_id': 0,
                                'width': 320, 'height': 240, 'max_fps': 5, 'timestamp': None}}).encode())
            return self._response(json.dumps(CameraService.capabilities()).encode())
        finally:
            if active_counted:
                with self.server.tracking_lock:
                    self.server.active_requests -= 1

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self._handle('POST', body)


class HttpClientFaultTests(unittest.TestCase):
    def test_lost_capture_response_retains_id_and_is_never_retried(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server), timeout_seconds=.5)
            request_id = str(uuid.uuid4())
            request = dict(DEFAULTS, request_id=request_id, token='test-token', control_epoch=1)
            server.mode = 'drop'
            with self.assertRaises(HttpClientError) as caught:
                client.capture(request)
            self.assertEqual(caught.exception.code, 'outcome_unknown')
            self.assertTrue(caught.exception.outcome_unknown)
            self.assertEqual(caught.exception.request_id, request_id)
            self.assertEqual(sum(method == 'POST' for method, _, _ in server.seen), 1)
            self.assertFalse(client.closed)
            server.mode = 'normal'
            self.assertEqual(client.query_capture(request_id)['request_id'], request_id)
            replacement = HttpCameraClient(_origin(server))
            self.assertEqual(replacement.query_capture(request_id)['status'], 'accepted')
            self.assertEqual(sum(method == 'POST' for method, _, _ in server.seen), 1)
            replacement.close()
            client.close()

    def test_capture_timeout_has_finite_budget_and_no_retry(self):
        with _serve(_FaultHandler) as server:
            server.mode = 'timeout'
            client = HttpCameraClient(_origin(server), timeout_seconds=.1)
            request_id = str(uuid.uuid4())
            started = time.monotonic()
            with self.assertRaises(HttpClientError) as caught:
                client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
            self.assertLess(time.monotonic() - started, .8)
            self.assertEqual(caught.exception.code, 'outcome_unknown')
            self.assertEqual(caught.exception.request_id, request_id)
            self.assertEqual(sum(method == 'POST' for method, _, _ in server.seen), 1)
            client.close()

    def test_non_capture_timeout_and_bad_protocol(self):
        modes = ('malformed', 'duplicate', 'nonfinite', 'overflow', 'array', 'oversized',
                 'bad_content_type', 'duplicate_length', 'redirect')
        with _serve(_FaultHandler) as server:
            for mode in modes:
                with self.subTest(mode=mode):
                    server.mode = mode
                    client = HttpCameraClient(_origin(server), timeout_seconds=.5)
                    with self.assertRaises(HttpClientError) as caught:
                        client.capabilities()
                    self.assertEqual(caught.exception.code, 'http_protocol_error')
                    self.assertFalse(caught.exception.outcome_unknown)
                    client.close()
            self.assertFalse(any(path == '/must-not-follow' for _, path, _ in server.seen))
            server.mode = 'timeout'
            client = HttpCameraClient(_origin(server), timeout_seconds=.1)
            with self.assertRaises(HttpClientError) as caught:
                client.get_state()
            self.assertEqual(caught.exception.code, 'http_timeout')
            client.close()

    def test_bounded_eof_and_chunked_json_framing(self):
        with _serve(_FaultHandler) as server:
            for mode in ('missing_length', 'chunked'):
                with self.subTest(mode=mode):
                    server.mode = mode
                    client = HttpCameraClient(_origin(server))
                    self.assertTrue(client.capabilities()['simulated'])
                    client.close()

    def test_capture_and_query_reject_wrong_identity_or_status(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server), timeout_seconds=.5)
            for mode in ('wrong_id', 'unknown_status', 'dict_status'):
                with self.subTest(mode=mode):
                    server.mode = mode
                    request_id = str(uuid.uuid4())
                    with self.assertRaises(HttpClientError) as capture:
                        client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
                    self.assertEqual(capture.exception.code, 'outcome_unknown')
                    self.assertEqual(capture.exception.request_id, request_id)
                    with self.assertRaises(HttpClientError) as query:
                        client.query_capture(request_id)
                    self.assertEqual(query.exception.code, 'http_protocol_error')
            client.close()

    def test_malformed_capture_response_is_uncertain_but_known_error_is_preserved(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server))
            request_id = str(uuid.uuid4())
            server.mode = 'malformed'
            with self.assertRaises(HttpClientError) as caught:
                client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
            self.assertEqual(caught.exception.code, 'outcome_unknown')
            self.assertEqual(caught.exception.request_id, request_id)
            server.mode = 'denied'
            with self.assertRaises(HttpClientError) as denied:
                client.capture(dict(DEFAULTS, request_id=str(uuid.uuid4()), token='wrong', control_epoch=1))
            self.assertEqual(denied.exception.code, 'control_denied')
            self.assertFalse(denied.exception.outcome_unknown)
            client.close()

    def test_capture_server_errors_preserve_codes_and_mark_uncertain_admission(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server))
            for code in ('journal_error', 'internal_error'):
                with self.subTest(code=code):
                    server.mode = code
                    request_id = str(uuid.uuid4())
                    before = sum(method == 'POST' for method, _, _ in server.seen)
                    with self.assertRaises(HttpClientError) as caught:
                        client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.request_id, request_id)
                    self.assertTrue(caught.exception.outcome_unknown)
                    self.assertEqual(sum(method == 'POST' for method, _, _ in server.seen), before + 1)
                    self.assertFalse(client.closed)
            client.close()

    def test_query_not_found_and_local_request_bound(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server))
            self.assertIsNone(client.query_capture(str(uuid.uuid4())))
            before = len(server.seen)
            for request in (None, {'request_id': 'bad', 'gain': float('nan')},
                            {'request_id': 'bad-unicode', 'extra': '\ud800'},
                            {'request_id': 'oversized', 'extra': 'x' * 65536}):
                with self.subTest(kind=type(request).__name__):
                    with self.assertRaises(HttpClientError) as caught:
                        client.capture(request)
                    self.assertEqual(caught.exception.code, 'invalid_request')
                    self.assertFalse(caught.exception.outcome_unknown)
            with self.assertRaises(HttpClientError) as caught:
                client.acquire('\ud800')
            self.assertEqual(caught.exception.code, 'invalid_request')
            self.assertFalse(client.closed)
            self.assertEqual(len(server.seen), before)
            client.close()

    def test_loopback_origin_validation_and_proxy_ignored(self):
        invalid = ('https://127.0.0.1:8787', 'http://example.com:8787', 'http://127.1:8787',
                   'http://2130706433:8787', 'http://127.0.0.1.evil.example:8787',
                   'http://localhost.:8787', 'file:///tmp/camera', 'http://user:pass@127.0.0.1:8787',
                   'http://127.0.0.1:8787/api/v1', 'http://127.0.0.1:8787/?q=1',
                   'http://127.0.0.1:8787/#fragment', 'http://[::2]:8787',
                   'http://127.0.0.1:0', 'http://127.0.0.1:65536')
        for origin in invalid:
            with self.subTest(origin=origin):
                with self.assertRaises((ValueError, HttpClientError)):
                    HttpCameraClient(origin)
        with _serve(_FaultHandler) as server:
            resolve = socket.getaddrinfo
            def literal_only(host, *args, **kwargs):
                self.assertEqual(host, '127.0.0.1')
                return resolve(host, *args, **kwargs)
            proxies = {'HTTP_PROXY': 'http://127.0.0.1:1', 'http_proxy': 'http://127.0.0.1:1',
                       'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': '', 'no_proxy': ''}
            with mock.patch.dict(os.environ, proxies), mock.patch('socket.getaddrinfo', side_effect=literal_only):
                client = HttpCameraClient(_origin(server, 'localhost') + '/')
                self.assertTrue(client.capabilities()['simulated'])
                client.close()
            self.assertEqual(len(server.seen), 1)

    def test_calls_serialized_and_close_is_local_only(self):
        with _serve(_FaultHandler) as server:
            server.mode = 'serialized'
            client = HttpCameraClient(_origin(server))
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: client.get_state(), range(12)))
            self.assertTrue(all(result['connected'] for result in results))
            self.assertEqual(server.maximum_active, 1)
            before = len(server.seen)
            client.close()
            client.abort()
            self.assertTrue(client.closed)
            self.assertEqual(len(server.seen), before)
            with self.assertRaises(HttpClientError) as caught:
                client.get_state()
            self.assertEqual(caught.exception.code, 'http_unavailable')
            replacement = HttpCameraClient(_origin(server))
            self.assertTrue(replacement.get_state()['connected'])
            replacement.close()

    def test_close_interrupts_attachment_without_cancelling_server_capture(self):
        with _serve(_FaultHandler) as server:
            server.mode = 'timeout'
            client = HttpCameraClient(_origin(server), timeout_seconds=2)
            request_id = str(uuid.uuid4())
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(client.capture, dict(DEFAULTS, request_id=request_id,
                                                          token='token', control_epoch=1))
                deadline = time.monotonic() + 2
                while request_id not in server.records and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertIn(request_id, server.records)
                started = time.monotonic()
                client.close()
                self.assertLess(time.monotonic() - started, .3)
                with self.assertRaises(HttpClientError) as caught:
                    pending.result(timeout=2)
                self.assertEqual(caught.exception.code, 'outcome_unknown')
                self.assertEqual(caught.exception.request_id, request_id)
            server.mode = 'normal'
            replacement = HttpCameraClient(_origin(server))
            self.assertEqual(replacement.query_capture(request_id)['status'], 'accepted')
            self.assertEqual(sum(method == 'POST' for method, _, _ in server.seen), 1)
            self.assertFalse(any(path.endswith('/cancel') for _, path, _ in server.seen))
            replacement.close()

    def test_known_connect_failure_does_not_claim_capture_uncertainty(self):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
            # The endpoint stays reserved without listening. Depending on the
            # OS, connecting is refused immediately or reaches its timeout.
            client = HttpCameraClient(f'http://127.0.0.1:{port}', timeout_seconds=.5)
            request_id = str(uuid.uuid4())
            with self.assertRaises(HttpClientError) as caught:
                client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
            self.assertIn(caught.exception.code, {'http_unavailable', 'http_timeout'})
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertEqual(caught.exception.request_id, request_id)
            client.close()

    def test_request_thread_start_failure_is_known_not_sent(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server))
            request_id = str(uuid.uuid4())
            with mock.patch('camera.camerad.http_client.threading.Thread.start',
                            side_effect=RuntimeError('cannot start request')):
                with self.assertRaises(HttpClientError) as caught:
                    client.capture(dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1))
            self.assertEqual(caught.exception.code, 'http_unavailable')
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertEqual(caught.exception.request_id, request_id)
            self.assertEqual(server.seen, [])
            self.assertFalse(client.closed)
            client.close()

    def test_timeout_constructor_rejects_nonfinite_or_platform_overflow(self):
        invalid = (float('nan'), float('inf'), -float('inf'), 0, -1, True,
                   1e100, 10 ** 400, threading.TIMEOUT_MAX * 2)
        with _serve(_FaultHandler) as server:
            for timeout in invalid:
                with self.subTest(timeout=repr(timeout)):
                    with self.assertRaises(ValueError):
                        HttpCameraClient(_origin(server), timeout_seconds=timeout)
            self.assertEqual(server.seen, [])

    def test_lone_surrogate_identifier_is_rejected_before_sending(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server))
            for request_id in ('\ud800', 'prefix-\udfff'):
                with self.subTest(request_id=repr(request_id)):
                    with self.assertRaises(HttpClientError) as caught:
                        client.query_capture(request_id)
                    self.assertEqual(caught.exception.code, 'invalid_request')
                    self.assertFalse(caught.exception.outcome_unknown)
            self.assertEqual(server.seen, [])
            self.assertFalse(client.closed)
            client.close()

    def test_completed_capture_actuals_stay_within_advertised_ranges(self):
        with _serve(_FaultHandler) as server:
            client = HttpCameraClient(_origin(server), timeout_seconds=.5)
            request_id = str(uuid.uuid4())
            request = dict(DEFAULTS, request_id=request_id, token='token', control_epoch=1)
            server.mode = 'complete_valid'
            baseline = client.capture(request)
            self.assertEqual(baseline['status'], 'complete')
            self.assertEqual(baseline['actual_exposure_us'], 10000)
            self.assertEqual(client.query_capture(request_id)['actual_analogue_gain'], 1.0)
            for mode in ('complete_high_exposure', 'complete_huge_exposure',
                         'complete_high_gain', 'complete_huge_gain'):
                with self.subTest(mode=mode):
                    server.mode = mode
                    with self.assertRaises(HttpClientError) as capture:
                        client.capture(request)
                    self.assertEqual(capture.exception.code, 'outcome_unknown')
                    self.assertEqual(capture.exception.request_id, request_id)
                    with self.assertRaises(HttpClientError) as query:
                        client.query_capture(request_id)
                    self.assertEqual(query.exception.code, 'http_protocol_error')
            client.close()


class HttpClientReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hf-http-client-')
        self.service = CameraService(self.directory.name)
        self.serving = _serve(handler_for(self.service))
        self.server = self.serving.__enter__()
        self.first = HttpCameraClient(_origin(self.server))
        self.second = HttpCameraClient(_origin(self.server))

    def tearDown(self):
        self.first.close()
        self.second.close()
        self.serving.__exit__(None, None, None)
        deadline = time.monotonic() + 10
        while self.service.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.service.close()
        self.directory.cleanup()

    def test_two_clients_share_lease_and_one_capture_commit(self):
        self.assertTrue(self.first.capabilities()['simulated'])
        lease = self.first.acquire('first HTTP client')
        with self.assertRaises(HttpClientError) as caught:
            self.second.acquire('second HTTP client')
        self.assertEqual(caught.exception.code, 'control_busy')
        self.assertEqual(self.second.get_state()['control']['owner'], 'first HTTP client')
        renewal = self.first.heartbeat(lease['token'], lease['epoch'])
        self.assertEqual(renewal['epoch'], lease['epoch'])
        configured = self.first.configure(lease['token'], lease['epoch'], {'requested_exposure_us': 8000})
        self.assertEqual(configured['requested_exposure_us'], 8000)
        request = dict(DEFAULTS, requested_exposure_us=8000, request_id=str(uuid.uuid4()),
                       token=lease['token'], control_epoch=lease['epoch'])
        self.first.capture(request)
        completed = _wait_capture(self.second, request['request_id'])
        self.assertEqual(completed['status'], 'complete')
        self.assertEqual(self.second.capture(request)['frame_id'], completed['frame_id'])
        self.assertEqual(len(list(self.service.photos.glob('*.dng'))), 1)
        with self.assertRaises(HttpClientError) as conflict:
            self.second.capture(dict(request, requested_exposure_us=16000))
        self.assertEqual(conflict.exception.code, 'request_conflict')
        self.first.close()
        self.assertTrue(self.second.get_state()['connected'])
        self.second.release(lease['token'], lease['epoch'])
        replacement = self.second.acquire('second HTTP client')
        self.assertGreater(replacement['epoch'], lease['epoch'])
        self.second.release(replacement['token'], replacement['epoch'])

    def test_preview_cancel_disconnect_and_reconnect(self):
        lease = self.first.acquire('HTTP operation test')
        token, epoch = lease['token'], lease['epoch']
        self.assertTrue(self.first.start_preview(token, epoch)['running'])
        self.assertTrue(self.second.get_state()['preview']['running'])
        self.assertFalse(self.first.stop_preview(token, epoch)['running'])
        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=token, control_epoch=epoch, fault='delay')
        self.first.capture(request)
        self.second.cancel_capture(token, epoch, request['request_id'])
        self.assertEqual(_wait_capture(self.first, request['request_id'])['status'], 'cancelled')
        disconnected = dict(request, request_id=str(uuid.uuid4()), fault='disconnect')
        self.second.capture(disconnected)
        self.assertEqual(_wait_capture(self.first, disconnected['request_id'])['status'], 'failed')
        self.assertFalse(self.first.get_state()['connected'])
        with self.assertRaises(HttpClientError) as rejected:
            self.first.reconnect('invalid', epoch)
        self.assertEqual(rejected.exception.code, 'control_denied')
        self.assertTrue(self.second.reconnect(token, epoch)['connected'])
        self.first.release(token, epoch)


class HttpClientNativeTests(unittest.TestCase):
    def test_native_http_clients_and_workflow_share_one_authority(self):
        from camera.camerad.native_client import NativeClient
        from camera.camerad.native_service import NativeService
        executable = os.environ.get('HF_NATIVE_DAEMON')
        if not executable:
            name = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
            executable = next((str(path) for path in (ROOT / 'build/native/Release' / name,
                                                      ROOT / 'build/native' / name) if path.is_file()), None)
        if not executable:
            self.skipTest('build hf-camerad or set HF_NATIVE_DAEMON for native HTTP integration')
        with tempfile.TemporaryDirectory(prefix='hf-native-http-client-') as directory:
            service = NativeService(directory, command=[executable])
            try:
                with _serve(handler_for(service)) as server:
                    first = HttpCameraClient(_origin(server))
                    second = HttpCameraClient(_origin(server))
                    try:
                        lease = first.acquire('native HTTP first')
                        with self.assertRaises(HttpClientError) as busy:
                            second.acquire('native HTTP second')
                        self.assertEqual(busy.exception.code, 'control_busy')
                        with self.assertRaises(CameraError) as locked:
                            NativeClient(directory, command=[executable])
                        self.assertEqual(locked.exception.code, 'already_running')
                        request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'],
                                       control_epoch=lease['epoch'])
                        first.capture(request)
                        complete = _wait_capture(second, request['request_id'])
                        self.assertEqual(complete['status'], 'complete')
                        self.assertEqual(complete['backend'], 'native')
                        self.assertEqual(second.capture(request)['frame_id'], request['request_id'])
                        first.close()
                        self.assertEqual(second.get_state()['backend'], 'native')
                        second.release(lease['token'], lease['epoch'])
                        status, _ = _request(_origin(server), 'POST', '/api/v1/workflows', {
                            'job_id': 'native-http-client-workflow',
                            'plan': {'kind': 'interval', 'exposures_us': [5000, 10000], 'interval_ms': 100}})
                        self.assertEqual(status, 200)
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            status, job = _request(_origin(server), 'GET', '/api/v1/workflows/native-http-client-workflow')
                            if job['status'] in {'completed', 'failed', 'cancelled', 'unknown'}:
                                break
                            time.sleep(.02)
                        self.assertEqual(job['status'], 'completed', job)
                        self.assertEqual(len(list((Path(directory) / 'photos').glob('*.dng'))), 3)
                        self.assertEqual(second.get_state()['backend'], 'native')
                    finally:
                        first.close()
                        second.close()
            finally:
                try:
                    service.close()
                except CameraError:
                    service.abort()


if __name__ == '__main__':
    unittest.main()

"""Real pipe/process tests for the reusable native camerad client."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from camera.camerad.native_client import NativeClient, NativeClientError, MAX_REQUEST_BYTES, MAX_STDERR_BYTES


FAKE_DAEMON = r'''
import json, os, sys, time
mode = os.environ.get('HF_FAKE_MODE', 'normal')
log = sys.argv[1]
if mode == 'startup_error':
    print(json.dumps({'id': None, 'error': {'code': 'already_running', 'message': 'authority owns directory'}}), flush=True)
    sys.exit(2)
def reply(number, result):
    print(json.dumps({'id': number, 'result': result}), flush=True)
for line in sys.stdin.buffer:
    request = json.loads(line)
    method, number = request['method'], request['id']
    with open(log, 'a', encoding='utf-8') as output:
        output.write(method + '\n')
        output.flush()
    if method == 'get_state':
        reply(number, {'connected': True, 'camera_state': 'idle'})
        continue
    if method == 'close':
        if mode == 'busy':
            print(json.dumps({'id': number, 'error': {'code': 'camera_busy', 'message': 'wait for capture'}}), flush=True)
            continue
        reply(number, {'closed': True})
        break
    if mode == 'capture_hang' and method == 'capture':
        time.sleep(60)
    elif mode == 'capture_exit' and method == 'capture':
        os._exit(7)
    elif mode in ('journal_error', 'internal_error', 'control_denied') and method == 'capture':
        print(json.dumps({'id': number, 'error': {'code': mode, 'message': 'capture admission error'}}), flush=True)
    elif mode == 'mismatch':
        reply(number + 100, {})
    elif mode == 'bool_id':
        reply(True, {})
    elif mode == 'duplicate':
        print('{"id":%d,"result":{},"result":{}}' % number, flush=True)
    elif mode == 'bad_error':
        print(json.dumps({'id': number, 'error': {'code': True, 'message': 'bad'}}), flush=True)
    elif mode == 'nonfinite':
        print('{"id":%d,"result":{"bad":1e999}}' % number, flush=True)
    elif mode == 'malformed':
        print('malformed response', flush=True)
    elif mode == 'array_result':
        reply(number, [])
    elif mode == 'oversized':
        sys.stdout.write('x' * (256 * 1024 + 1))
        sys.stdout.flush()
        time.sleep(60)
    elif mode == 'stderr':
        sys.stderr.write('e' * (96 * 1024))
        sys.stderr.flush()
        reply(number, {'ok': True})
    elif mode == 'serialized':
        time.sleep(.01)
        reply(number, {'owner': request['params'].get('owner')})
    elif mode in ('wrong_record_id', 'bad_record_status', 'incomplete_completion', 'extreme_actuals') and method in ('capture', 'query_capture'):
        rid=request['params']['request_id']
        record={'request_id':'different-id' if mode=='wrong_record_id' else rid,
                'status':{} if mode=='bad_record_status' else 'complete' if mode in ('incomplete_completion','extreme_actuals') else 'accepted'}
        if mode=='extreme_actuals':
            record.update(capture_id=rid,frame_id=rid,actual_exposure_us=10**1000,
                          actual_analogue_gain=1e300,file_path='synthetic.dng')
        reply(number,record)
    elif method == 'capture':
        reply(number, dict(request['params'], status='accepted'))
    elif method == 'query_capture':
        reply(number, {'request_id':request['params']['request_id'], 'status':'accepted'})
    else:
        reply(number, request['params'])
'''


class NativeClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log = self.root / 'calls.log'
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.abort()
        self.temporary.cleanup()

    def client(self, mode='normal', timeout=2):
        client = NativeClient(self.root / 'data', [sys.executable, '-u', '-c', FAKE_DAEMON, str(self.log)],
                              timeout_seconds=timeout, env={'HF_FAKE_MODE': mode})
        self.clients.append(client)
        return client

    def calls(self):
        return self.log.read_text(encoding='utf-8').splitlines() if self.log.exists() else []

    def test_methods_keep_service_parameter_names(self):
        client = self.client()
        self.assertEqual(client.root, (self.root / 'data').resolve())
        self.assertEqual(client.photos, client.root / 'photos')
        self.assertEqual(client.acquire('operator'), {'owner': 'operator'})
        self.assertEqual(client.heartbeat('token', 8), {'token': 'token', 'epoch': 8})
        self.assertEqual(client.release('token', 8), {'token': 'token', 'epoch': 8})
        self.assertEqual(client.configure('token', 8, {'requested_exposure_us': 10000}),
                         {'token': 'token', 'epoch': 8, 'settings': {'requested_exposure_us': 10000}})
        request = {'request_id': 'persisted-id', 'token': 'token', 'control_epoch': 8}
        self.assertEqual(client.capture(request), dict(request, status='accepted'))
        self.assertEqual(client.query_capture('persisted-id'), {'request_id': 'persisted-id', 'status': 'accepted'})
        self.assertEqual(client.cancel_capture('token', 8, 'persisted-id'),
                         {'token': 'token', 'epoch': 8, 'request_id': 'persisted-id'})
        self.assertEqual(client.reconnect('token', 8), {'token': 'token', 'epoch': 8})
        self.assertEqual(client.close(), {'closed': True})
        self.assertTrue(client.closed)
        self.assertEqual(client.process.poll(), 0)
        self.assertIsNone(client.close())

    def test_capture_timeout_is_unknown_and_never_retried(self):
        client = self.client('capture_hang', timeout=.5)
        started = time.monotonic()
        with self.assertRaises(NativeClientError) as caught:
            client.capture({'request_id': 'capture-timeout'})
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(caught.exception.code, 'outcome_unknown')
        self.assertEqual(caught.exception.request_id, 'capture-timeout')
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(self.calls().count('capture'), 1)
        self.assertTrue(client.closed)
        self.assertIsNotNone(client.process.poll())
        with self.assertRaises(NativeClientError) as second:
            client.capture({'request_id': 'capture-timeout'})
        self.assertEqual(second.exception.code, 'native_unavailable')
        self.assertEqual(self.calls().count('capture'), 1)

    def test_capture_eof_is_unknown_with_request_identity(self):
        client = self.client('capture_exit')
        with self.assertRaises(NativeClientError) as caught:
            client.capture({'request_id': 'capture-eof'})
        self.assertEqual(caught.exception.code, 'outcome_unknown')
        self.assertEqual(caught.exception.request_id, 'capture-eof')
        self.assertEqual(self.calls().count('capture'), 1)
        self.assertTrue(client.closed)

    def test_capture_server_errors_keep_codes_and_mark_uncertainty(self):
        for mode, uncertain in (('journal_error', True), ('internal_error', True), ('control_denied', False)):
            with self.subTest(code=mode):
                client = self.client(mode)
                request_id = 'capture-' + mode
                before = self.calls().count('capture')
                with self.assertRaises(NativeClientError) as caught:
                    client.capture({'request_id': request_id})
                self.assertEqual(caught.exception.code, mode)
                self.assertEqual(caught.exception.request_id, request_id)
                self.assertEqual(caught.exception.outcome_unknown, uncertain)
                self.assertEqual(self.calls().count('capture'), before + 1)
                self.assertFalse(client.closed)
                client.close()

    def test_malformed_envelopes_close_process(self):
        for mode in ('mismatch', 'bool_id', 'duplicate', 'bad_error', 'nonfinite', 'malformed', 'array_result', 'oversized'):
            with self.subTest(mode=mode):
                client = self.client(mode)
                with self.assertRaises(NativeClientError) as caught:
                    client.capabilities()
                self.assertEqual(caught.exception.code, 'native_protocol_error')
                self.assertTrue(client.closed)
                self.assertIsNotNone(client.process.poll())

    def test_startup_error_preserves_authority_error_code(self):
        with self.assertRaises(NativeClientError) as caught:
            self.client('startup_error')
        self.assertEqual(caught.exception.code, 'already_running')

    def test_concurrent_calls_are_serialized(self):
        client = self.client('serialized')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: client.acquire('operator-' + str(i)), range(16)))
        self.assertEqual(results, [{'owner': 'operator-' + str(i)} for i in range(16)])
        self.assertEqual(self.calls().count('acquire'), 16)
        self.assertFalse(client.closed)

    def test_close_busy_preserves_process_until_explicit_abort(self):
        client = self.client('busy')
        with self.assertRaises(NativeClientError) as caught:
            client.close()
        self.assertEqual(caught.exception.code, 'camera_busy')
        self.assertFalse(client.closed)
        self.assertIsNone(client.process.poll())
        self.assertTrue(client.get_state()['connected'])
        client.abort()
        self.assertTrue(client.closed)
        self.assertIsNotNone(client.process.poll())

    def test_stderr_is_drained_and_memory_bounded(self):
        client = self.client('stderr')
        self.assertEqual(client.capabilities(), {'ok': True})
        client.close()
        self.assertEqual(len(client.stderr_tail.encode('utf-8')), MAX_STDERR_BYTES)
        self.assertEqual(set(client.stderr_tail), {'e'})

    def test_invalid_local_payload_does_not_send_or_close(self):
        client = self.client()
        for body in (None, {'request_id': 'bad', 'gain': float('nan')},
                     {'request_id': 'big', 'extra': 'x' * MAX_REQUEST_BYTES}):
            with self.subTest(body_type=type(body).__name__):
                with self.assertRaises(NativeClientError) as caught:
                    client.capture(body)
                self.assertEqual(caught.exception.code, 'invalid_request')
                self.assertFalse(caught.exception.outcome_unknown)
        self.assertNotIn('capture', self.calls())
        self.assertFalse(client.closed)
        self.assertTrue(client.get_state()['connected'])

    def test_writer_start_failure_is_known_not_sent(self):
        client = self.client()
        started = time.monotonic()
        with mock.patch('camera.camerad.native_client.threading.Thread.start',
                        side_effect=RuntimeError('cannot start thread')):
            with self.assertRaises(NativeClientError) as caught:
                client.capture({'request_id': 'not-sent'})
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(caught.exception.code, 'native_unavailable')
        self.assertEqual(caught.exception.request_id, 'not-sent')
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertNotIn('capture', self.calls())
        self.assertTrue(client.closed)
        self.assertIsNotNone(client.process.poll())

    def test_context_manager_closes_or_aborts_owned_process(self):
        client = self.client()
        with client:
            self.assertTrue(client.get_state()['connected'])
        self.assertTrue(client.closed)
        second = self.client()
        with self.assertRaisesRegex(RuntimeError, 'caller failed'):
            with second:
                raise RuntimeError('caller failed')
        self.assertTrue(second.closed)

    def test_context_exit_aborts_when_graceful_close_is_busy(self):
        client = self.client('busy')
        with self.assertRaises(NativeClientError) as caught:
            with client:
                self.assertTrue(client.get_state()['connected'])
        self.assertEqual(caught.exception.code, 'camera_busy')
        self.assertTrue(client.closed)
        self.assertIsNotNone(client.process.poll())

    def test_unsupported_timeout_is_rejected_before_process_launch(self):
        for timeout in (1e100, 10 ** 1000, float('inf'), float('nan'), -1, True):
            with self.subTest(timeout_type=type(timeout).__name__):
                with mock.patch('camera.camerad.native_client.subprocess.Popen') as popen:
                    with self.assertRaises(ValueError):
                        NativeClient(self.root, [sys.executable], timeout_seconds=timeout)
                    popen.assert_not_called()

    def test_invalid_unicode_payload_is_known_not_sent_and_keeps_authority_alive(self):
        client = self.client()
        before = self.calls()
        for action in (lambda: client.acquire('\ud800'),
                       lambda: client.configure('token', 8, {'bad\udfff': 'value'}),
                       lambda: client.capture({'request_id': 'retained-id', 'extra': '\ud800'})):
            with self.assertRaises(NativeClientError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'invalid_request')
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertFalse(client.closed)
        self.assertEqual(self.calls(), before)
        self.assertIsNone(client.process.poll())
        self.assertEqual(client.acquire('operator-\u76f8\u673a'), {'owner': 'operator-\u76f8\u673a'})

    def test_capture_and_query_validate_record_identity_status_and_actuals(self):
        for mode in ('wrong_record_id', 'bad_record_status', 'incomplete_completion', 'extreme_actuals'):
            with self.subTest(mode=mode, operation='capture'):
                client = self.client(mode)
                with self.assertRaises(NativeClientError) as caught:
                    client.capture({'request_id': 'original-request'})
                self.assertEqual(caught.exception.code, 'outcome_unknown')
                self.assertEqual(caught.exception.request_id, 'original-request')
                self.assertTrue(client.closed)
                self.assertIsNotNone(client.process.poll())
            with self.subTest(mode=mode, operation='query'):
                client = self.client(mode)
                with self.assertRaises(NativeClientError) as caught:
                    client.query_capture('original-request')
                self.assertEqual(caught.exception.code, 'native_protocol_error')
                self.assertTrue(client.closed)
                self.assertIsNotNone(client.process.poll())


if __name__ == '__main__':
    unittest.main()

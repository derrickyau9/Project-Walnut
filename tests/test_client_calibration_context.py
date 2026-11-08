"""Client checks for calibration claims and capture context across transports.

These are bounded metadata fixtures. No test admits a shutter request or writes a
camera journal; the HTTP fixture only serves small JSON responses on loopback.
"""

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import unittest
from unittest import mock
import uuid

from camera.camerad.http_client import HttpCameraClient, HttpClientError
from camera.camerad.native_client import NativeClient, NativeClientError
from camera.camerad.service import DEFAULTS


ROOT = Path(__file__).resolve().parents[1]
BINDING = json.loads((ROOT / 'camera/calibration/fixtures/binding-uncalibrated.json').read_text(encoding='ascii'))


def _summary(binding=None):
    return {
        'sensor_id': 'HF-Sim',
        'binding_id': binding['binding_id'] if binding else None,
        'calibration_id': binding['binding_id'] if binding else 'synthetic-uncalibrated',
        'package_checksum': binding['package_checksum'] if binding else None,
        'hardware_calibrated': False,
        'pixel_corrections_applied': False,
    }


def _state(calibration=...):
    result = {
        'connected': True, 'simulated': True, 'backend': 'python', 'authority': 'python',
        'camera_state': 'idle', 'journal_healthy': True, 'control': None,
        'settings': dict(DEFAULTS), 'last_capture': None, 'pipeline_generation': 0,
        'preview': {'running': False},
    }
    if calibration is not ...:
        result['calibration'] = calibration
    return result


def _bound_record(*, evidence=True):
    request_id = str(uuid.uuid4())
    record = {
        'request_id': request_id, 'capture_id': request_id, 'frame_id': request_id,
        'status': 'complete', 'sensor_mode': 'sim-small', 'sensor_id': 'HF-Sim',
        'requested_exposure_us': 10000, 'actual_exposure_us': 10000,
        'requested_analogue_gain': 1.0, 'actual_analogue_gain': 1.0,
        'calibration_id': BINDING['binding_id'], 'capture_record_version': 2,
        'calibration_binding': copy.deepcopy(BINDING),
        'hardware_calibrated': False, 'pixel_corrections_applied': False,
        'file_path': request_id + '.dng',
    }
    if evidence:
        record['native_evidence'] = {
            'schema_version': 1, 'source': 'synthetic-native', 'simulated': True,
            'width': 128, 'height': 96, 'bit_depth': 14, 'cfa': 'RGGB',
            'request_id': request_id, 'capture_id': request_id, 'frame_id': request_id,
            'sensor_id': 'HF-Sim', 'calibration_id': BINDING['binding_id'],
            'requested_exposure_us': 10000, 'actual_exposure_us': 10000,
            'requested_analogue_gain': 1.0, 'actual_analogue_gain': 1.0,
            'captured_at_unix_ns': 1_700_000_000_000_000_000,
            'output_bytes': 25000,
        }
    return record


@contextmanager
def _json_server(status, payload):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            encoded = json.dumps(payload, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class CalibrationStateClientTests(unittest.TestCase):
    def test_present_valid_summaries_and_legacy_absence(self):
        for calibration in (..., _summary(), _summary(BINDING)):
            with self.subTest(calibration=calibration):
                state = _state(calibration)
                self.assertEqual(state, HttpCameraClient._state(copy.deepcopy(state)))
                client = NativeClient.__new__(NativeClient)
                with mock.patch.object(client, '_rpc', return_value=copy.deepcopy(state)):
                    self.assertEqual(state, client.get_state())

    def test_present_malformed_summary_fails_closed_in_both_clients(self):
        malformed = [
            None, {}, {'sensor_id': 'Other-Sensor'},
            dict(_summary(), hardware_calibrated=True),
            dict(_summary(), pixel_corrections_applied=True),
            dict(_summary(), binding_id=BINDING['binding_id']),
            dict(_summary(), calibration_id=BINDING['binding_id']),
            dict(_summary(BINDING), calibration_id='synthetic-uncalibrated'),
            dict(_summary(BINDING), package_checksum=None),
            dict(_summary(), extra='unrecognized'),
        ]
        for value in malformed:
            with self.subTest(value=value):
                state = _state(value)
                with self.assertRaises((ValueError, HttpClientError)):
                    HttpCameraClient._state(copy.deepcopy(state))
                client = NativeClient.__new__(NativeClient)
                # A real native transport aborts its child on a malformed reply.
                # Stub that lifecycle edge so this metadata test needs no process.
                with (mock.patch.object(client, '_rpc', return_value=copy.deepcopy(state)),
                      mock.patch.object(client, '_transport_error',
                                        side_effect=lambda code, message, **_: NativeClientError(code, message))):
                    with self.assertRaises(NativeClientError) as caught:
                        client.get_state()
                    self.assertEqual('native_protocol_error', caught.exception.code)

    def test_http_get_state_rejects_false_calibration_claim(self):
        payload = _state(dict(_summary(), hardware_calibrated=True))
        with _json_server(200, payload) as origin:
            client = HttpCameraClient(origin)
            try:
                with self.assertRaises(HttpClientError) as caught:
                    client.get_state()
                self.assertEqual('http_protocol_error', caught.exception.code)
            finally:
                client.close()


class BoundCaptureClientTests(unittest.TestCase):
    def assert_both_accept(self, record):
        request_id = record['request_id']
        self.assertEqual(record, HttpCameraClient._record(copy.deepcopy(record), request_id))
        self.assertEqual(record, NativeClient._capture_record(copy.deepcopy(record), request_id))

    def assert_both_reject(self, record):
        request_id = record['request_id']
        for validator in (HttpCameraClient._record, NativeClient._capture_record):
            with self.subTest(validator=validator.__qualname__):
                with self.assertRaises(ValueError):
                    validator(copy.deepcopy(record), request_id)

    def test_valid_v2_accepts_optional_native_evidence(self):
        self.assert_both_accept(_bound_record(evidence=False))
        self.assert_both_accept(_bound_record(evidence=True))

    def test_legacy_worker_may_omit_nested_capture_id_but_cannot_contradict_it(self):
        record = _bound_record()
        del record['native_evidence']['capture_id']
        self.assert_both_accept(record)
        record['native_evidence']['capture_id'] = str(uuid.uuid4())
        self.assert_both_reject(record)

    def test_unknown_preserves_evidence_without_inventing_confirmed_outcomes(self):
        record = _bound_record()
        record.update(status='unknown', frame_id=None, file_path=None,
                      actual_exposure_us=None, actual_analogue_gain=None,
                      error='completion transaction was not confirmed')
        original = copy.deepcopy(record)
        self.assert_both_accept(record)
        self.assertEqual(original, record)
        for field, value in (('frame_id', str(uuid.uuid4())),
                             ('capture_id', str(uuid.uuid4())),
                             ('actual_exposure_us', 11000),
                             ('actual_analogue_gain', 2.0)):
            with self.subTest(field=field):
                changed = copy.deepcopy(record)
                changed['native_evidence'][field] = value
                self.assert_both_reject(changed)
        record['status'] = 'complete'
        self.assert_both_reject(record)

    def test_native_evidence_must_agree_with_bound_record(self):
        changes = {
            'request_id': str(uuid.uuid4()),
            'capture_id': str(uuid.uuid4()),
            'frame_id': str(uuid.uuid4()),
            'sensor_id': 'Other-Sensor',
            'calibration_id': 'synthetic-uncalibrated',
            'requested_exposure_us': 11000,
            'actual_exposure_us': 11000,
            'requested_analogue_gain': 2.0,
            'actual_analogue_gain': 2.0,
        }
        for field, changed in changes.items():
            with self.subTest(field=field):
                record = _bound_record()
                record['native_evidence'][field] = changed
                self.assert_both_reject(record)

    def test_partial_or_false_claiming_native_evidence_is_rejected(self):
        for change in (
            lambda record: {},
            lambda record: {'calibration_id': record['calibration_id']},
            lambda record: dict(record['native_evidence'], simulated=False),
            lambda record: dict(record['native_evidence'], hardware_calibrated=True),
            lambda record: dict(record['native_evidence'], pixel_corrections_applied=True),
        ):
            record = _bound_record()
            record['native_evidence'] = change(record)
            with self.subTest(evidence=record['native_evidence']):
                self.assert_both_reject(record)

    def test_nested_numeric_bools_do_not_masquerade_as_settings(self):
        for field in ('requested_exposure_us', 'actual_exposure_us',
                      'requested_analogue_gain', 'actual_analogue_gain'):
            with self.subTest(field=field):
                record = _bound_record()
                record['native_evidence'][field] = True
                self.assert_both_reject(record)

    def test_last_capture_state_readback_checks_nested_binding_context(self):
        valid = _state(_summary())
        valid['last_capture'] = _bound_record()
        self.assertEqual(valid, HttpCameraClient._state(copy.deepcopy(valid)))
        client = NativeClient.__new__(NativeClient)
        with mock.patch.object(client, '_rpc', return_value=copy.deepcopy(valid)):
            self.assertEqual(valid, client.get_state())
        mismatched = copy.deepcopy(valid)
        mismatched['last_capture']['native_evidence']['calibration_id'] = 'synthetic-uncalibrated'
        with self.assertRaises(ValueError):
            HttpCameraClient._state(mismatched)
        with (mock.patch.object(client, '_rpc', return_value=mismatched),
              mock.patch.object(client, '_transport_error',
                                side_effect=lambda code, message, **_: NativeClientError(code, message))):
            with self.assertRaises(NativeClientError) as caught:
                client.get_state()
            self.assertEqual('native_protocol_error', caught.exception.code)

    def test_bound_flags_cannot_claim_correction_or_hardware_calibration(self):
        for field in ('hardware_calibrated', 'pixel_corrections_applied'):
            with self.subTest(field=field):
                record = _bound_record()
                record[field] = True
                self.assert_both_reject(record)


class HttpMissingCaptureTests(unittest.TestCase):
    def test_true_404_not_found_is_none_but_500_not_found_is_error(self):
        request_id = str(uuid.uuid4())
        error = {'error': {'code': 'not_found', 'message': 'capture not found'}}
        with _json_server(404, error) as origin:
            client = HttpCameraClient(origin)
            try:
                self.assertIsNone(client.query_capture(request_id))
            finally:
                client.close()
        with _json_server(500, error) as origin:
            client = HttpCameraClient(origin)
            try:
                with self.assertRaises(HttpClientError) as caught:
                    client.query_capture(request_id)
                self.assertNotEqual('not_found', caught.exception.code)
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()

"""Committed capture library, pinned playback, and local/HTTP export tests."""
from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
import zlib

from camera.camerad.http_client import HttpCameraClient, HttpClientError
from camera.camerad.retained import MAX_DNG_BYTES, open_retained
from camera.camerad.server import BoundedHTTPServer, handler_for
from camera.camerad.service import CameraError, CameraService, DEFAULTS, _publish_capture_file
from camera.dng.hf_dng import write_dng
from camera.dng.inspect import inspect_dng


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FIELDS = {'request_id', 'capture_id', 'frame_id', 'status', 'timestamp', 'sensor_mode',
                  'width', 'height', 'bit_depth', 'cfa', 'requested_exposure_us', 'actual_exposure_us',
                  'requested_analogue_gain', 'actual_analogue_gain', 'sensor_id', 'calibration_id',
                  'simulated', 'filename'}


def _capture(service, lease, **changes):
    request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'])
    request.update(changes)
    service.capture(request)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = service.query_capture(request['request_id'])
        if record['status'] in {'complete', 'failed', 'cancelled', 'unknown'} and service.get_state()['camera_state'] == 'idle':
            return record
        time.sleep(.01)
    raise AssertionError('capture did not terminate')


@contextmanager
def _serve(service=None, custom=None):
    class Handler(custom or handler_for(service)):
        def log_message(self, *args):
            pass
    server = BoundedHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server, 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _png_pixels(data):
    width, height = struct.unpack_from('>II', data, 16)
    cursor, compressed = 8, bytearray()
    while cursor < len(data):
        count = struct.unpack_from('>I', data, cursor)[0]
        if data[cursor + 4:cursor + 8] == b'IDAT':
            compressed.extend(data[cursor + 8:cursor + 8 + count])
        cursor += count + 12
    return width, height, zlib.decompress(compressed)


class ReferenceRetainedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hf-retained-')
        self.service = CameraService(self.directory.name)
        self.lease = self.service.acquire('retained-tests')
        self.records = [_capture(self.service, self.lease, requested_exposure_us=value)
                        for value in (5000, 10000, 15000)]

    def tearDown(self):
        if self.service.active:
            self.service.cancel_capture(self.lease['token'], self.lease['epoch'], self.service.active)
            deadline = time.monotonic() + 5
            while self.service.active and time.monotonic() < deadline:
                time.sleep(.01)
        self.service.close()
        self.directory.cleanup()

    def test_keyset_pages_are_allowlisted_stable_and_survive_restart(self):
        failed = _capture(self.service, self.lease, fault='storage_full')
        self.assertEqual(failed['status'], 'failed')
        first = self.service.list_captures(2)
        self.assertEqual([item['request_id'] for item in first['items']],
                         [self.records[2]['request_id'], self.records[1]['request_id']])
        self.assertEqual(first['next_cursor'], self.records[1]['request_id'])
        for item in first['items']:
            self.assertEqual(set(item), SUMMARY_FIELDS)
            self.assertEqual((item['status'], item['width'], item['height'], item['bit_depth'], item['cfa']),
                             ('complete', 128, 96, 14, 'RGGB'))
        newer = _capture(self.service, self.lease)
        second = self.service.list_captures(2, first['next_cursor'])
        self.assertEqual([item['request_id'] for item in second['items']], [self.records[0]['request_id']])
        self.assertIsNone(second['next_cursor'])
        self.service.close()
        self.service = CameraService(self.directory.name)
        self.lease = self.service.acquire('after-restart')
        self.assertEqual(self.service.list_captures()['items'][0]['request_id'], newer['request_id'])
        self.assertEqual(len(list(self.service.photos.glob('*.dng'))), 4)

    def test_limits_and_cursor_require_completed_canonical_identity(self):
        for value in (True, 0, 51, 1.5, '2'):
            with self.subTest(limit=value), self.assertRaises(CameraError) as caught:
                self.service.list_captures(value)
            self.assertEqual(caught.exception.code, 'invalid_request')
        failed = _capture(self.service, self.lease, fault='storage_full')
        for cursor in ('../outside', str(uuid.uuid4()), failed['request_id']):
            with self.subTest(cursor=cursor), self.assertRaises(CameraError) as caught:
                self.service.list_captures(cursor=cursor)
            self.assertEqual(caught.exception.code, 'invalid_request')

    def test_open_descriptor_and_export_ignore_recorded_arbitrary_path(self):
        record = self.records[0]
        path = self.service.photos / (record['request_id'] + '.dng')
        expected = path.read_bytes()
        outside = Path(self.directory.name) / 'outside-secret.bin'
        outside.write_bytes(b'do-not-open')
        with open_retained(self.service.root, dict(record, file_path=str(outside))) as stream:
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(stream.read(), expected)
        asset = self.service.get_capture_asset(record['request_id'])
        self.assertEqual(set(asset), {'request_id', 'filename', 'byte_count', 'media_type'})
        self.assertEqual(asset['byte_count'], len(expected))
        destination = Path(self.directory.name) / 'exported.dng'
        result = self.service.export_capture(record['request_id'], destination)
        self.assertEqual(set(result), {'request_id', 'filename', 'byte_count', 'destination'})
        self.assertEqual(destination.read_bytes(), expected)
        self.assertEqual(result['destination'], str(destination.resolve()))
        with self.assertRaises(CameraError) as caught:
            self.service.export_capture(record['request_id'], destination)
        self.assertEqual(caught.exception.code, 'export_conflict')
        self.assertEqual(destination.read_bytes(), expected)
        self.assertFalse(list(destination.parent.glob('.hf-export-*.partial')))

    def test_thumbnail_changes_when_actual_retained_samples_change(self):
        record = self.records[1]
        path = self.service.photos / (record['request_id'] + '.dng')
        before = path.read_bytes()
        original = self.service.capture_thumbnail(record['request_id'])
        width, height, pixels = _png_pixels(original)
        self.assertLessEqual(width, 320)
        self.assertLessEqual(height, 240)
        structure = inspect_dng(path)
        changed = bytearray(before)
        start = structure['strip_offsets'][0]
        struct.pack_into('<2H', changed, start, 64, 64)
        struct.pack_into('<2H', changed, start + 128 * 2, 64, 64)
        try:
            path.write_bytes(changed)
            actual = self.service.capture_thumbnail(record['request_id'])
            self.assertNotEqual(actual, original)
            self.assertEqual(_png_pixels(actual)[2][1:4], b'\0\0\0')
            self.assertNotEqual(pixels[1:4], b'\0\0\0')
        finally:
            path.write_bytes(before)

    def test_changed_cfa_color_mapping_or_layout_cannot_be_displayed_or_exported(self):
        record = self.records[0]
        path = self.service.photos / (record['request_id'] + '.dng')
        original = path.read_bytes()
        entries = {struct.unpack_from('<H', original, 10 + 12 * n)[0]: 10 + 12 * n
                   for n in range(struct.unpack_from('<H', original, 8)[0])}
        rows_before = self.service.db.execute('SELECT count(*) FROM captures').fetchone()[0]
        destination = Path(self.directory.name) / 'unsupported-cfa.dng'
        try:
            for tag, value in ((50710, 0x00000102), (50711, 2)):
                with self.subTest(tag=tag):
                    changed = bytearray(original)
                    struct.pack_into('<I', changed, entries[tag] + 8, value)
                    path.write_bytes(changed)
                    for operation in (lambda: self.service.get_capture_asset(record['request_id']),
                                      lambda: self.service.capture_thumbnail(record['request_id']),
                                      lambda: self.service.export_capture(record['request_id'], destination)):
                        with self.assertRaises(CameraError) as caught:
                            operation()
                        self.assertEqual(caught.exception.code, 'capture_unavailable')
                    self.assertFalse(destination.exists())
                    self.assertEqual(path.read_bytes(), changed)
            self.assertEqual(self.service.db.execute('SELECT count(*) FROM captures').fetchone()[0], rows_before)
            self.assertEqual(self.service.query_capture(record['request_id'])['status'], 'complete')
        finally:
            path.write_bytes(original)

    def test_failed_active_unknown_missing_and_corrupt_sources_are_not_replayed(self):
        failed = _capture(self.service, self.lease, fault='storage_full')
        with self.assertRaises(CameraError) as caught:
            self.service.get_capture_asset(failed['request_id'])
        self.assertEqual(caught.exception.code, 'capture_not_complete')
        active_id = str(uuid.uuid4())
        self.service.capture(dict(DEFAULTS, request_id=active_id, token=self.lease['token'],
                                  control_epoch=self.lease['epoch'], fault='delay'))
        with self.assertRaises(CameraError) as caught:
            self.service.get_capture_asset(active_id)
        self.assertEqual(caught.exception.code, 'capture_not_complete')
        self.service.cancel_capture(self.lease['token'], self.lease['epoch'], active_id)
        deadline = time.monotonic() + 5
        while self.service.active and time.monotonic() < deadline:
            time.sleep(.01)
        missing = str(uuid.uuid4())
        with self.assertRaises(CameraError) as caught:
            self.service.open_capture(missing)
        self.assertEqual(caught.exception.code, 'not_found')
        record = self.records[0]
        path = self.service.photos / (record['request_id'] + '.dng')
        original = path.read_bytes()
        count = self.service.db.execute('SELECT count(*) FROM captures').fetchone()[0]
        for corrupt in (b'not a DNG', original[:-1], original + b'x' * 70000):
            with self.subTest(length=len(corrupt)):
                path.write_bytes(corrupt)
                destination = Path(self.directory.name) / 'must-not-export.dng'
                with self.assertRaises(CameraError) as caught:
                    self.service.export_capture(record['request_id'], destination)
                self.assertEqual(caught.exception.code, 'capture_unavailable')
                self.assertFalse(destination.exists())
        path.write_bytes(original)
        unknown = dict(record, status='unknown')
        with self.assertRaises(CameraError) as caught:
            open_retained(self.service.root, unknown)
        self.assertEqual(caught.exception.code, 'capture_not_complete')
        path.unlink()
        with self.assertRaises(CameraError) as caught:
            self.service.get_capture_asset(record['request_id'])
        self.assertEqual(caught.exception.code, 'capture_unavailable')
        self.assertEqual(self.service.db.execute('SELECT count(*) FROM captures').fetchone()[0], count)

    def test_wrong_dng_identity_and_publish_race_do_not_export(self):
        record = self.records[0]
        path = self.service.photos / (record['request_id'] + '.dng')
        original = path.read_bytes()
        write_dng(path, 128, 96, [64] * (128 * 96), {'request_id': str(uuid.uuid4())})
        with self.assertRaises(CameraError) as caught:
            self.service.open_capture(record['request_id'])
        self.assertEqual(caught.exception.code, 'capture_unavailable')
        path.write_bytes(original)
        destination = Path(self.directory.name) / 'raced.dng'
        def race(temporary, final):
            final.write_bytes(b'external-winner')
            _publish_capture_file(temporary, final)
        with mock.patch('camera.camerad.service._publish_capture_file', side_effect=race):
            with self.assertRaises(CameraError) as caught:
                self.service.export_capture(record['request_id'], destination)
        self.assertEqual(caught.exception.code, 'export_conflict')
        self.assertEqual(destination.read_bytes(), b'external-winner')
        self.assertFalse(list(destination.parent.glob('.hf-export-*.partial')))

    def test_corrupt_journal_row_cannot_redirect_a_requested_capture_to_another(self):
        requested, other = self.records[:2]
        request_id = requested['request_id']
        self.service.db.execute('UPDATE captures SET record_json=? WHERE request_id=?',
                                (json.dumps(other), request_id))
        self.service.db.commit()
        destination = Path(self.directory.name) / 'must-not-redirect.dng'
        try:
            operations = (lambda: self.service.get_capture_asset(request_id),
                          lambda: self.service.open_capture(request_id),
                          lambda: self.service.capture_thumbnail(request_id),
                          lambda: self.service.export_capture(request_id, destination),
                          lambda: self.service.list_captures())
            for operation in operations:
                with self.assertRaises(CameraError) as caught:
                    operation()
                # SQL/record identity disagreement now fences the authority,
                # rather than treating it as only one unavailable media asset.
                self.assertEqual(caught.exception.code, 'journal_error')
            self.assertFalse(destination.exists())
        finally:
            self.service.db.execute('UPDATE captures SET record_json=? WHERE request_id=?',
                                    (json.dumps(requested), request_id))
            self.service.db.commit()

    def test_caller_owned_inspector_stream_and_pinned_file_survive_path_changes(self):
        record = self.records[0]
        path = self.service.photos / (record['request_id'] + '.dng')
        expected = path.read_bytes()
        with self.service.open_capture(record['request_id']) as source:
            self.assertEqual(inspect_dng(source)['request_id'], record['request_id'])
            self.assertFalse(source.closed)
            renamed = path.with_suffix('.renamed')
            moved = False
            try:
                path.rename(renamed)
                moved = True
                path.write_bytes(b'replacement must not be read')
            except PermissionError:
                pass  # Windows pinned handle deliberately denies delete/rename.
            source.seek(0)
            self.assertEqual(source.read(), expected)
        if moved:
            path.unlink()
            renamed.rename(path)


class HttpRetainedTests(unittest.TestCase):
    def test_reference_http_list_asset_thumbnail_and_byte_identical_export_without_lease(self):
        with tempfile.TemporaryDirectory(prefix='hf-retained-http-') as directory:
            service = CameraService(directory)
            try:
                lease = service.acquire('camera-owner')
                record = _capture(service, lease)
                with _serve(service) as (server, origin):
                    with HttpCameraClient(origin) as client:
                        page = client.list_captures(1)
                        self.assertEqual(page['items'][0]['request_id'], record['request_id'])
                        self.assertEqual(set(page['items'][0]), SUMMARY_FIELDS)
                        asset = client.get_capture_asset(record['request_id'])
                        self.assertEqual(asset['filename'], record['request_id'] + '.dng')
                        self.assertEqual(client.capture_thumbnail(record['request_id']), service.capture_thumbnail(record['request_id']))
                        destination = Path(directory) / 'http-export.dng'
                        client.export_capture(record['request_id'], destination)
                        source = service.photos / (record['request_id'] + '.dng')
                        self.assertEqual(hashlib.sha256(destination.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest())
                        self.assertEqual(service.get_state()['control']['owner'], 'camera-owner')
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                        connection.request('GET', '/api/v1/captures/' + record['request_id'] + '/file.dng')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader('X-Capture-Request-Id'), record['request_id'])
                        self.assertEqual(response.getheader('Content-Disposition'), f'attachment; filename="{record["request_id"]}.dng"')
                        self.assertEqual(response.read(), source.read_bytes())
                        connection.close()
                        for path in ('/api/v1/captures?limit=0', '/api/v1/captures?limit=1&limit=2',
                                     '/api/v1/captures?path=anything', '/api/v1/captures/' + record['request_id'] + '/file.dng?path=outside'):
                            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                            connection.request('GET', path)
                            response = connection.getresponse()
                            self.assertEqual(response.status, 400)
                            response.read()
                            connection.close()
            finally:
                service.close()

    def test_native_bridge_uses_same_retained_assets_and_no_second_capture(self):
        from camera.camerad.native_service import NativeService
        name = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
        executable = os.environ.get('HF_NATIVE_DAEMON') or next((str(path) for path in (
            ROOT / 'build/native/Release' / name, ROOT / 'build/native' / name) if path.is_file()), None)
        if not executable:
            self.skipTest('build hf-camerad or set HF_NATIVE_DAEMON for native retained integration')
        with tempfile.TemporaryDirectory(prefix='hf-native-retained-http-') as directory:
            service = NativeService(directory, command=[executable])
            try:
                lease = service.acquire('native-retained-owner')
                record = _capture(service, lease)
                service.release(lease['token'], lease['epoch'])
                with _serve(service) as (_, origin), HttpCameraClient(origin) as client:
                    self.assertEqual(client.list_captures()['items'][0]['request_id'], record['request_id'])
                    destination = Path(directory) / 'native-export.dng'
                    result = client.export_capture(record['request_id'], destination)
                    self.assertEqual(result['byte_count'], destination.stat().st_size)
                    self.assertEqual(client.capture_thumbnail(record['request_id']), service.capture_thumbnail(record['request_id']))
                    with service.open_capture(record['request_id']) as stream:
                        self.assertEqual(stream.read(), destination.read_bytes())
                    self.assertEqual(len(list((Path(directory) / 'photos').glob('*.dng'))), 1)
                    self.assertIsNone(service.get_state()['control'])
            finally:
                service.close()

    def test_http_export_rejects_wrong_or_truncated_dng_despite_valid_identity_headers(self):
        request_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix='hf-retained-wire-') as directory:
            path = Path(directory) / 'wire.dng'
            write_dng(path, 128, 96, [64] * (128 * 96), {'request_id': str(uuid.uuid4())})
            payload = path.read_bytes()
            record = dict(DEFAULTS, request_id=request_id, capture_id=request_id, frame_id=request_id,
                          status='complete', actual_exposure_us=10000, actual_analogue_gain=1,
                          file_path='server-local-only.dng')
            class Handler(BaseHTTPRequestHandler):
                mode = 'wrong_id'
                def do_GET(self):
                    if self.path.endswith('/file.dng'):
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/x-adobe-dng')
                        self.send_header('Content-Length', str(len(payload) + (1 if self.mode == 'truncated' else 0)))
                        self.send_header('X-Capture-Request-Id', request_id)
                        self.send_header('Content-Disposition', f'attachment; filename="{request_id}.dng"')
                        self.end_headers()
                        if self.mode == 'timeout':
                            self.wfile.write(payload[:8])
                            self.wfile.flush()
                            time.sleep(.3)
                            return
                        self.wfile.write(payload)
                    else:
                        data = json.dumps(record).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
            with _serve(custom=Handler) as (_, origin), HttpCameraClient(origin, timeout_seconds=.1) as client:
                for mode in ('wrong_id', 'truncated', 'timeout'):
                    Handler.mode = mode
                    destination = Path(directory) / (mode + '.dng')
                    with self.subTest(mode=mode), self.assertRaises(HttpClientError) as caught:
                        client.export_capture(request_id, destination)
                    self.assertEqual(caught.exception.code, 'http_timeout' if mode == 'timeout' else 'http_protocol_error')
                    self.assertFalse(destination.exists())
                self.assertFalse(list(Path(directory).glob('.hf-export-*.partial')))


if __name__ == '__main__':
    unittest.main()

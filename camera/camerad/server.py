"""Local development HTTP server for the simulated camera, using stdlib only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import math
import mimetypes
import os
import socket
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from camera.camerad.service import CameraError, CameraService
from camera.pipeline.preview import PreviewUnavailable
from daos.capability_runtime.manager import WorkflowManager, WorkflowError

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / 'daos' / 'console'


def _request_json(payload):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON member')
            if any(0xd800 <= ord(character) <= 0xdfff for character in key):
                raise ValueError('JSON key contains an unpaired surrogate')
            value[key] = item
        return value
    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('nonfinite JSON number')
        return value
    def reject_constant(_):
        raise ValueError('nonfinite JSON value')
    value = json.loads(payload.decode('utf-8'), object_pairs_hook=unique_object,
                       parse_float=finite_float, parse_constant=reject_constant)
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError('JSON nesting exceeds limit')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and any(0xd800 <= ord(character) <= 0xdfff for character in item):
            raise ValueError('JSON string contains an unpaired surrogate')
    return value


class BoundedHTTPServer(ThreadingHTTPServer):
    """Limit local peer resource use; close excess connections without queuing."""
    def __init__(self, server_address, handler, max_workers=16, request_timeout_seconds=10):
        try:
            valid_timeout = (not isinstance(request_timeout_seconds, bool) and
                             isinstance(request_timeout_seconds, (int, float)) and
                             math.isfinite(request_timeout_seconds) and
                             0 < request_timeout_seconds <= threading.TIMEOUT_MAX)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError('request_timeout_seconds must be positive, finite, and supported by this platform')
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError('max_workers must be a positive integer')
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def handler_for(service):
    workflows = WorkflowManager(service, service.root / 'workflows.sqlite3')
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(self.server.request_timeout_seconds)

        def handle(self):
            self._deadline = time.monotonic() + self.server.request_timeout_seconds
            self._expired = threading.Event()
            def expire():
                self._expired.set()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            watchdog = threading.Timer(self.server.request_timeout_seconds, expire)
            watchdog.daemon = True
            try:
                watchdog.start()
                super().handle()
            except (ConnectionError, TimeoutError):
                # A local client can disconnect while a response is in flight.
                pass
            finally:
                watchdog.cancel()

        def valid_host(self):
            hosts = self.headers.get_all('Host', [])
            return len(hosts) == 1 and hosts[0] in (
                f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def _check_deadline(self):
            if self._expired.is_set() or time.monotonic() >= self._deadline:
                raise CameraError('invalid_request', 'request time budget expired')

        def _path(self):
            # BaseHTTPRequestHandler normalizes leading double slashes. Check
            # the original request target before accepting that normalization.
            target = self.requestline.split()[1]
            if (not target.startswith('/') or target.startswith('//') or
                    any(ord(character) < 32 or ord(character) == 127 for character in target)):
                raise CameraError('invalid_request', 'request target must be an origin-form path')
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or parts.fragment:
                raise CameraError('invalid_request', 'request target must be an origin-form path')
            return parts.path

        def _headers(self, *, body=False):
            self._check_deadline()
            if not self.valid_host():
                raise CameraError('control_denied', 'exactly one valid Host header is required')
            origins = self.headers.get_all('Origin', [])
            if len(origins) > 1 or origins and origins[0] != f'http://{self.headers["Host"]}':
                raise CameraError('control_denied', 'cross-origin or ambiguous Origin request denied')
            if self.headers.get_all('Transfer-Encoding', []):
                raise CameraError('invalid_request', 'request Transfer-Encoding is unsupported')
            lengths = self.headers.get_all('Content-Length', [])
            types = self.headers.get_all('Content-Type', [])
            if len(lengths) > 1 or len(types) > 1:
                raise CameraError('invalid_request', 'ambiguous request framing or Content-Type')
            if body:
                if len(types) != 1 or self.headers.get_content_type() != 'application/json':
                    raise CameraError('invalid_request', 'exactly one application/json Content-Type is required')
                if self.headers.get_content_charset('utf-8').lower() not in ('utf-8', 'utf8'):
                    raise CameraError('invalid_request', 'JSON requests must use UTF-8')
                encodings = self.headers.get_all('Content-Encoding', [])
                if len(encodings) > 1 or encodings and encodings[0].strip().lower() != 'identity':
                    raise CameraError('invalid_request', 'request Content-Encoding is unsupported')
            if not lengths:
                if body:
                    raise CameraError('invalid_request', 'Content-Length is required')
                return 0
            length = lengths[0].strip(' \t')
            if not length.isascii() or not length.isdigit():
                raise CameraError('invalid_request', 'invalid Content-Length')
            length = int(length)
            if body and not 0 < length <= 65536 or not body and length != 0:
                raise CameraError('invalid_request', 'invalid content length')
            return length

        def _run(self, action):
            try:
                action()
            except CameraError as error:
                status = (403 if error.code == 'control_denied' else
                          404 if error.code in {'not_found', 'capture_unavailable'} else
                          503 if error.code in {'journal_error', 'device_disconnected', 'native_unavailable',
                                               'native_timeout', 'native_protocol_error', 'outcome_unknown'} else
                          409 if error.code in {'control_busy', 'camera_busy', 'request_conflict', 'capture_not_complete'} else
                          500 if error.code == 'internal_error' else 400)
                self.reply(status, {'error': {'code': error.code, 'message': str(error)}})
            except WorkflowError as error:
                self.reply(409, {'error': {'code': 'workflow_error', 'message': str(error)}})
            except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
                self.reply(400, {'error': {'code': 'invalid_request', 'message': 'malformed JSON or request'}})
            except (ConnectionError, TimeoutError):
                pass
            except Exception:
                self.reply(500, {'error': {'code': 'internal_error', 'message': 'camera request could not be completed'}})

        def _retained_query(self):
            query = urlsplit(self.requestline.split()[1]).query
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            values = {}
            for key, value in pairs:
                if key not in {'limit', 'cursor'} or key in values:
                    raise CameraError('invalid_request', 'unsupported or duplicate capture list query')
                values[key] = value
            limit = values.get('limit', '20')
            if not limit.isascii() or not limit.isdigit():
                raise CameraError('invalid_request', 'limit must be an integer from 1 to 50')
            return int(limit), values.get('cursor')

        def _retained_file(self, request_id):
            from camera.camerad.retained import MAX_DNG_BYTES
            asset = service.get_capture_asset(request_id)
            with service.open_capture(request_id) as stream:
                initial = os.fstat(stream.fileno())
                size = initial.st_size
                if (asset.get('request_id') != request_id or asset.get('filename') != request_id + '.dng' or
                        asset.get('byte_count') != size or not 0 < size <= MAX_DNG_BYTES):
                    raise CameraError('capture_unavailable', 'retained capture changed before transfer')
                self._check_deadline()
                self.send_response(200)
                self.send_header('Content-Type', 'image/x-adobe-dng')
                self.send_header('Content-Length', str(size))
                self.send_header('Content-Disposition', f'attachment; filename="{request_id}.dng"')
                self.send_header('X-Capture-Request-Id', request_id)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    remaining = size
                    while remaining:
                        block = stream.read(min(65536, remaining))
                        if not block:
                            raise OSError('retained capture was truncated during transfer')
                        self.wfile.write(block)
                        remaining -= len(block)
                    final = os.fstat(stream.fileno())
                    if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                        raise OSError('retained capture changed during transfer')
                except OSError as error:
                    # Headers already describe binary bytes. Close this stream
                    # instead of appending an error JSON object to the DNG.
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    raise ConnectionError('retained capture transfer interrupted') from error

        def _retained_thumbnail(self, request_id):
            from camera.camerad.retained import MAX_THUMB_BYTES
            data = service.capture_thumbnail(request_id)
            if not isinstance(data, bytes) or len(data) > MAX_THUMB_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise CameraError('capture_unavailable', 'retained thumbnail is unavailable')
            self._check_deadline()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Capture-Request-Id', request_id)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def reply(self, status, obj):
            data = json.dumps(obj, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._run(self._get)

        def _get(self):
            self._headers()
            path = self._path()
            if path.startswith('/api/v1/library/assets/') and path.endswith('/file'):
                parts=path.split('/')
                if len(parts)!=7:raise CameraError('invalid_request','invalid library asset route')
                photo=parts[-2];asset=service.get_library_asset(photo)
                with service.open_library_asset(photo) as stream:
                    self.send_response(200);self.send_header('Content-Type',asset['media_type'])
                    self.send_header('Content-Length',str(asset['byte_count']));self.send_header('X-Library-Photo-Id',photo)
                    self.send_header('Cache-Control','no-store');self.end_headers()
                    remaining=asset['byte_count']
                    while remaining:
                        block=stream.read(min(65536,remaining))
                        if not block:raise ConnectionError('library stream truncated')
                        self.wfile.write(block);remaining-=len(block)
                return
            if path == '/api/v1/capabilities':
                return self.reply(200, service.capabilities())
            if path == '/api/v1/state':
                return self.reply(200, service.get_state())
            if path == '/api/v1/captures':
                limit, cursor = self._retained_query()
                return self.reply(200, service.list_captures(limit, cursor))
            if path == '/api/v1/workflows':
                return self.reply(200, workflows.list_jobs())
            if path.startswith('/api/v1/workflows/'):
                parts = path.split('/')
                if len(parts) != 5 or not parts[-1]:
                    return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
                try:
                    return self.reply(200, workflows.inspect(path.split('/')[-1]))
                except WorkflowError as e:
                    return self.reply(409, {'error': {'code': 'workflow_error', 'message': str(e)}})
            if path == '/api/v1/preview.png':
                state = service.get_state()
                if not state['connected']:
                    return self.reply(503, {'error': {'code': 'device_disconnected', 'message': 'simulated device disconnected'}})
                try:
                    settings = {**state['settings'], '_pipeline_generation': state['pipeline_generation']}
                    frame = service.preview.snapshot(settings, capturing=state['camera_state'] == 'capturing')
                except PreviewUnavailable as e:
                    return self.reply(503, {'error': {'code': e.code, 'message': str(e)}})
                after = service.get_state()
                if not after['connected']:
                    return self.reply(503, {'error': {'code': 'device_disconnected', 'message': 'simulated device disconnected'}})
                if not after['preview']['running']:
                    return self.reply(503, {'error': {'code': 'preview_stopped', 'message': 'preview stopped'}})
                if after['pipeline_generation'] != state['pipeline_generation'] or after['camera_state'] == 'capturing':
                    return self.reply(503, {'error': {'code': 'preview_paused_for_capture', 'message': 'capture transition'}})
                data = frame['png']
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Id', str(frame['frame_id']))
                self.send_header('X-Preview-Timestamp', str(frame['timestamp']))
                self.end_headers()
                return self.wfile.write(data)
            if path.startswith('/api/v1/captures/'):
                parts = path.split('/')
                if len(parts) == 6 and parts[-1] in {'file.dng', 'thumbnail.png', 'asset'} and parts[-2]:
                    if urlsplit(self.requestline.split()[1]).query:
                        raise CameraError('invalid_request', 'capture asset routes do not accept query parameters')
                    if parts[-1] == 'file.dng':
                        return self._retained_file(parts[-2])
                    if parts[-1] == 'asset':
                        return self.reply(200, service.get_capture_asset(parts[-2]))
                    return self._retained_thumbnail(parts[-2])
                if len(parts) != 5 or not parts[-1]:
                    return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
                record = service.query_capture(path.rsplit('/', 1)[-1])
                return self.reply(200 if record else 404, record or {'error': {'code': 'not_found', 'message': 'capture not found'}})
            # Only files inside console can be served; do not expose the journal or photos.
            relative = 'index.html' if path == '/' else path.lstrip('/')
            target = (CONSOLE / relative).resolve()
            if CONSOLE.resolve() not in target.parents or not target.is_file():
                return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
            data = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self._run(self._post)

        def _post(self):
            length = self._headers(body=True)
            path = self._path()
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise CameraError('invalid_request', 'request body is shorter than Content-Length')
            body = _request_json(payload)
            if not isinstance(body, dict):
                raise CameraError('invalid_request', 'body must be an object')
            self._check_deadline()
            if path.startswith('/api/v1/library/'):
                operation=path.rsplit('/',1)[-1]
                if operation=='import_path':result=service.import_library_asset(body.get('source'),body.get('photo_uuid'))
                elif operation in {'info','list','changes','get','flags','pin','unpin','purge','register_import'}:result=service.library_rpc(operation,body)
                else:raise CameraError('method_not_found','unsupported library operation')
            elif path == '/api/v1/control/acquire':
                result = service.acquire(body.get('owner'))
            elif path == '/api/v1/control/heartbeat':
                result = service.heartbeat(body.get('token'), body.get('epoch'))
            elif path == '/api/v1/control/release':
                result = service.release(body.get('token'), body.get('epoch'))
            elif path == '/api/v1/configure':
                result = service.configure(body.get('token'), body.get('epoch'), body.get('settings', {}))
            elif path == '/api/v1/calibration/bind':
                if set(body)!={'token','epoch','binding'}: raise CameraError('invalid_request','calibration bind requires token, epoch and binding')
                result = service.bind_calibration(body.get('token'),body.get('epoch'),body.get('binding'))
            elif path == '/api/v1/calibration/clear':
                if set(body)!={'token','epoch'}: raise CameraError('invalid_request','calibration clear requires token and epoch')
                result = service.clear_calibration(body.get('token'),body.get('epoch'))
            elif path == '/api/v1/preview/start':
                result = service.start_preview(body.get('token'), body.get('epoch'))
            elif path == '/api/v1/preview/stop':
                result = service.stop_preview(body.get('token'), body.get('epoch'))
            elif path == '/api/v1/device/reconnect':
                result = service.reconnect(body.get('token'), body.get('epoch'))
            elif path == '/api/v1/workflows':
                result = workflows.start(body.get('job_id'), body.get('plan'))
            elif path.startswith('/api/v1/workflows/'):
                parts = path.split('/')
                if len(parts) != 6 or not parts[-2] or parts[-1] not in ('pause', 'resume', 'cancel'):
                    return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
                result = getattr(workflows, parts[-1])(parts[-2])
            elif path == '/api/v1/captures':
                result = service.capture(body)
            elif path.startswith('/api/v1/captures/') and path.endswith('/cancel'):
                parts = path.split('/')
                if len(parts) != 6 or not parts[-2]:
                    return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
                result = service.cancel_capture(body.get('token'), body.get('epoch'), parts[-2])
            else:
                return self.reply(404, {'error': {'code': 'not_found', 'message': 'not found'}})
            self.reply(200, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description='HassleFree simulated camera local server')
    parser.add_argument('--data-dir', default='var/hf-sim')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--backend', choices=['python', 'native'], default='python')
    parser.add_argument('--native-worker', help='Explicit path to the compiled synthetic capture worker')
    parser.add_argument('--authority', choices=['python', 'native'], default='python', help='Owner of request journal and control state')
    parser.add_argument('--native-daemon', help='Explicit compiled hf-camerad path for native authority')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    if args.authority == 'native':
        from camera.camerad.native_service import NativeService
        service = NativeService(args.data_dir, command=[args.native_daemon] if args.native_daemon else None)
    else:
        service = CameraService(args.data_dir, backend=args.backend, native_worker=args.native_worker)
    server = None
    try:
        server = BoundedHTTPServer(('127.0.0.1', args.port), handler_for(service))
        print(f'HF-Sim: http://127.0.0.1:{server.server_port}/ (simulation only)', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        try:
            service.close()
        except CameraError:
            if hasattr(service, 'abort'):
                service.abort()


if __name__ == '__main__':
    main()

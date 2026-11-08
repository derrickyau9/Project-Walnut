"""Bounded loopback HTTP attachment to an already running camera authority.

This client never starts or stops the authority and never retries a capture.
Keep the request ID before calling capture: a lost response must be reconciled
with query_capture, using the same authority and original request ID.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import queue
import socket
import struct
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit

from camera.camerad.service import CameraError
from camera.camerad.library import LibraryClientMixin
from camera.camerad.calibration_context import (validate_calibration_summary,
                                              validate_capture_calibration_context)


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
_STATUSES = {'accepted', 'capturing', 'complete', 'failed', 'cancelled', 'unknown'}
_DEFINITE_REJECTIONS = {'invalid_request', 'control_denied', 'control_busy', 'camera_busy',
                        'device_disconnected', 'deadline_expired', 'request_conflict',
                        'test_fault_disabled', 'not_found', 'method_not_found'}


class HttpClientError(CameraError):
    def __init__(self, code, message, *, request_id=None, outcome_unknown=None):
        super().__init__(code, message)
        self.request_id = request_id
        self.outcome_unknown = code == 'outcome_unknown' if outcome_unknown is None else bool(outcome_unknown)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def _finite_float(text):
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('nonfinite JSON number')
    return result


def _reject_constant(_):
    raise ValueError('nonfinite JSON constant')


def _positive_finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _decode_json(payload):
    value = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object,
                       parse_float=_finite_float, parse_constant=_reject_constant)
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError('JSON nesting exceeds limit')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


class _Operation:
    def __init__(self):
        self.results = queue.Queue(maxsize=1)
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._socket = None
        self._attempted = False

    def set_socket(self, connection_socket):
        with self._lock:
            self._socket = connection_socket
        if self.cancelled.is_set():
            self.cancel()

    @property
    def attempted(self):
        with self._lock:
            return self._attempted

    def mark_attempted(self):
        with self._lock:
            if self.cancelled.is_set():
                return False
            self._attempted = True
            return True

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            connection_socket = self._socket
        if connection_socket is not None:
            # shutdown interrupts a buffered reader without acquiring its I/O
            # lock. The operation thread owns final connection cleanup.
            try:
                connection_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class HttpCameraClient(LibraryClientMixin):
    """CameraService-compatible client for one explicit loopback HTTP origin.

    ``base_url`` is an origin such as ``http://127.0.0.1:8787``. Literal
    loopback addresses and ``localhost`` are accepted; localhost connects to
    numeric 127.0.0.1 without DNS. Credentials, URL paths, queries, fragments,
    redirects, HTTPS and non-loopback hosts are rejected. Environment proxy
    settings are never consulted.
    """
    backend = 'http'

    def __init__(self, base_url, timeout_seconds=5):
        try:
            valid_timeout = (not isinstance(timeout_seconds, bool) and isinstance(timeout_seconds, (int, float)) and
                             math.isfinite(timeout_seconds) and 0 < timeout_seconds <= threading.TIMEOUT_MAX)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError('timeout_seconds must be positive, finite, and within platform timeout limits')
        if (not isinstance(base_url, str) or not base_url or base_url != base_url.strip() or
                any(ord(character) <= 32 for character in base_url) or '?' in base_url or '#' in base_url):
            raise ValueError('base_url must be an explicit loopback HTTP origin')
        try:
            parts = urlsplit(base_url)
            host = parts.hostname
            port = parts.port if parts.port is not None else 80
            if (parts.scheme != 'http' or not host or parts.username is not None or parts.password is not None or
                    parts.path not in ('', '/') or parts.query or parts.fragment or parts.netloc.endswith(':') or
                    not 1 <= port <= 65535 or '%' in host):
                raise ValueError('unsupported loopback HTTP origin')
            if host == 'localhost':
                connect_host = '127.0.0.1'
            else:
                address = ipaddress.ip_address(host)
                if not address.is_loopback:
                    raise ValueError('HTTP authority must use a loopback address')
                connect_host = str(address)
        except (ValueError, TypeError) as error:
            raise ValueError('base_url must be a loopback HTTP origin without credentials or a path') from error
        self.timeout_seconds = float(timeout_seconds)
        self._connect_host = connect_host
        self._port = port
        self._host_header = ('[' + host + ']' if ':' in host else host) + ':' + str(port)
        self.base_url = 'http://' + self._host_header
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._active = None

    @property
    def closed(self):
        with self._state_lock:
            return self._closed

    def close(self):
        """Close only this attachment; never release control or stop the server."""
        with self._state_lock:
            self._closed = True
            operation = self._active
        if operation is not None:
            operation.cancel()

    def abort(self):
        self.close()

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not value or len(value) > 128 or '\0' in value:
            raise HttpClientError('invalid_request', 'request_id must be a nonempty string up to 128 characters')
        try:
            return quote(value, safe='')
        except UnicodeError as error:
            raise HttpClientError('invalid_request', 'request_id must contain valid Unicode') from error

    @staticmethod
    def _record(value, request_id):
        if (not isinstance(value, dict) or not isinstance(request_id, str) or not request_id
                or value.get('request_id') != request_id or
                not isinstance(value.get('status'), str) or value['status'] not in _STATUSES):
            raise ValueError('capture response identity or status does not match the request')
        validate_capture_calibration_context(value)
        if value['status'] == 'complete':
            exposure = value.get('actual_exposure_us')
            gain = value.get('actual_analogue_gain')
            if (value.get('frame_id') != request_id or value.get('capture_id') != request_id or
                    type(exposure) is not int or not 100 <= exposure <= 30000000 or
                    not _positive_finite(gain) or not 1 <= gain <= 16 or
                    not isinstance(value.get('file_path'), str) or not value['file_path'] or
                    '\0' in value['file_path']):
                raise ValueError('completed capture response is missing actual settings or file identity')
        return value

    @staticmethod
    def _settings(value):
        if (not isinstance(value, dict) or value.get('sensor_mode') not in ('sim-small', 'sim-full') or
                type(value.get('requested_exposure_us')) is not int or not 100 <= value['requested_exposure_us'] <= 30000000 or
                not _positive_finite(value.get('requested_analogue_gain')) or value.get('output_format') != 'dng' or
                not 1 <= value['requested_analogue_gain'] <= 16 or
                value.get('simulation_scene', 'scene') not in ('scene', 'flat', 'dark')):
            raise ValueError('HTTP camera settings are malformed')
        return value

    @classmethod
    def _state(cls, value):
        control = value.get('control')
        if (type(value.get('connected')) is not bool or type(value.get('simulated')) is not bool or
                value.get('camera_state') not in ('idle', 'capturing') or
                type(value.get('pipeline_generation')) is not int or value['pipeline_generation'] < 0 or
                not isinstance(value.get('preview'), dict) or type(value['preview'].get('running')) is not bool or
                control is not None and (not isinstance(control, dict) or not isinstance(control.get('owner'), str) or
                                         type(control.get('epoch')) is not int or control['epoch'] < 0)):
            raise ValueError('HTTP camera state is malformed')
        cls._settings(value.get('settings'))
        if 'calibration' in value:
            validate_calibration_summary(value['calibration'])
        if value.get('last_capture') is not None:
            record = value['last_capture']
            cls._record(record, record.get('request_id') if isinstance(record, dict) else None)
        return value

    @staticmethod
    def _lease(value, *, token_required):
        if (not isinstance(value.get('owner'), str) or not value['owner'] or
                type(value.get('epoch')) is not int or value['epoch'] < 0 or
                not _positive_finite(value.get('lease_seconds')) or value['lease_seconds'] > 3600 or
                token_required and (not isinstance(value.get('token'), str) or not value['token'] or '\0' in value['token'])):
            raise ValueError('HTTP control lease is malformed')
        return value

    def _exchange(self, operation, verb, route, payload, validate):
        connection = http.client.HTTPConnection(self._connect_host, self._port, timeout=self.timeout_seconds)
        response = None
        try:
            # Separate connection setup from sending so a known connect failure
            # cannot be mistaken for an admitted shutter request.
            connection.connect()
            operation.set_socket(connection.sock)
            if not operation.mark_attempted():
                raise OSError('HTTP attachment was closed before sending')
            headers = {'Host': self._host_header, 'Accept': 'application/json', 'Connection': 'close'}
            if payload is not None:
                headers['Content-Type'] = 'application/json; charset=utf-8'
            connection.request(verb, route, body=payload, headers=headers)
            response = connection.getresponse()
            if response.status != 200 and not 400 <= response.status <= 599:
                raise ValueError('unexpected HTTP status or redirect')
            if len(response.headers.get_all('Content-Type', [])) != 1 or response.headers.get_content_type() != 'application/json':
                raise ValueError('HTTP camera response must be JSON')
            if response.headers.get_content_charset('utf-8').lower() not in ('utf-8', 'utf8'):
                raise ValueError('HTTP camera response must use UTF-8')
            lengths = response.headers.get_all('Content-Length', [])
            encodings = response.headers.get_all('Transfer-Encoding', [])
            length = None
            if len(lengths) > 1 or len(encodings) > 1 or lengths and encodings:
                raise ValueError('ambiguous HTTP response framing')
            if encodings and encodings[0].strip().lower() != 'chunked':
                raise ValueError('unsupported HTTP transfer encoding')
            if lengths:
                if not lengths[0].isascii() or not lengths[0].isdigit():
                    raise ValueError('invalid HTTP response length')
                length = int(lengths[0])
                if length > MAX_RESPONSE_BYTES:
                    raise ValueError('HTTP response exceeds size limit')
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES or length is not None and len(data) != length:
                raise ValueError('HTTP response is oversized or truncated')
            value = _decode_json(data)
            if response.status >= 400:
                if (not isinstance(value, dict) or set(value) != {'error'} or not isinstance(value['error'], dict) or
                        set(value['error']) != {'code', 'message'} or not isinstance(value['error']['code'], str) or
                        not value['error']['code'] or not isinstance(value['error']['message'], str)):
                    raise ValueError('malformed HTTP camera error response')
                operation.results.put_nowait(('error', (response.status, value['error']['code'], value['error']['message'])))
            else:
                if not isinstance(value, dict):
                    raise ValueError('HTTP camera result must be an object')
                operation.results.put_nowait(('result', validate(value) if validate else value))
        except (socket.timeout, TimeoutError):
            operation.results.put_nowait(('transport', ('http_timeout', 'HTTP camera operation timed out')))
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError, http.client.HTTPException):
            operation.results.put_nowait(('transport', ('http_protocol_error', 'HTTP camera returned an invalid response')))
        except OSError:
            operation.results.put_nowait(('transport', ('http_unavailable', 'HTTP camera connection was unavailable')))
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _request(self, verb, route, body=None, *, capture_id=None, validate=None, missing_is_none=False):
        payload = None
        if body is not None:
            if not isinstance(body, dict):
                raise HttpClientError('invalid_request', 'HTTP camera request must be an object', request_id=capture_id)
            try:
                payload = json.dumps(body, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            except (ValueError, TypeError, RecursionError) as error:
                raise HttpClientError('invalid_request', 'HTTP camera request must contain finite JSON', request_id=capture_id) from error
            if len(payload) > MAX_REQUEST_BYTES:
                raise HttpClientError('invalid_request', 'HTTP camera request exceeds size limit', request_id=capture_id)
        deadline = time.monotonic() + self.timeout_seconds
        if not self._io_lock.acquire(timeout=self.timeout_seconds):
            raise HttpClientError('http_timeout', 'timed out waiting for another HTTP camera request', request_id=capture_id)
        operation = _Operation()
        try:
            with self._state_lock:
                if self._closed:
                    raise HttpClientError('http_unavailable', 'HTTP camera client is closed', request_id=capture_id)
                self._active = operation
            thread = threading.Thread(target=self._exchange, args=(operation, verb, route, payload, validate),
                                      name='http-camera-request', daemon=True)
            try:
                thread.start()
            except RuntimeError as error:
                raise HttpClientError('http_unavailable', 'could not start HTTP camera request', request_id=capture_id) from error
            try:
                kind, value = operation.results.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                operation.cancel()
                kind, value = 'transport', ('http_timeout', 'HTTP camera operation exceeded its time budget')
            if kind == 'result':
                return value
            if kind == 'error':
                status, code, message = value
                if code == 'not_found' and status != 404:
                    raise HttpClientError('http_protocol_error', 'HTTP not_found response did not have status 404',
                                          request_id=capture_id, outcome_unknown=capture_id is not None)
                if missing_is_none and status == 404 and code == 'not_found':
                    return None
                raise HttpClientError(code, message, request_id=capture_id,
                                      outcome_unknown=capture_id is not None and operation.attempted and
                                      code not in _DEFINITE_REJECTIONS)
            code, message = value
            if capture_id is not None and operation.attempted:
                code = 'outcome_unknown'
                message = 'capture response was not confirmed; query the original request ID on the same authority'
            raise HttpClientError(code, message, request_id=capture_id)
        finally:
            with self._state_lock:
                if self._active is operation:
                    self._active = None
            self._io_lock.release()

    def capabilities(self):
        return self._request('GET', '/api/v1/capabilities')

    def get_state(self):
        return self._request('GET', '/api/v1/state', validate=self._state)

    def acquire(self, owner):
        return self._request('POST', '/api/v1/control/acquire', {'owner': owner},
                             validate=lambda value: self._lease(value, token_required=True))

    def heartbeat(self, token, epoch):
        return self._request('POST', '/api/v1/control/heartbeat', {'token': token, 'epoch': epoch},
                             validate=lambda value: self._lease(value, token_required=False))

    def release(self, token, epoch):
        return self._request('POST', '/api/v1/control/release', {'token': token, 'epoch': epoch})

    def configure(self, token, epoch, settings):
        return self._request('POST', '/api/v1/configure', {'token': token, 'epoch': epoch, 'settings': settings},
                             validate=self._settings)

    def _calibration_selection(self,route,token,epoch,binding=None):
        from camera.calibration import validate_binding
        verified=validate_binding(binding) if binding is not None else None
        expected={'sensor_id':'HF-Sim','binding_id':verified['binding_id'] if verified else None,
                  'calibration_id':verified['binding_id'] if verified else 'synthetic-uncalibrated',
                  'package_checksum':verified['package_checksum'] if verified else None,
                  'hardware_calibrated':False,'pixel_corrections_applied':False}
        def selection(value):
            if (value!=expected or value.get('hardware_calibrated') is not False or value.get('pixel_corrections_applied') is not False):
                raise ValueError('calibration selection response is inconsistent')
            return value
        body={'token':token,'epoch':epoch}
        if binding is not None: body['binding']=verified
        return self._request('POST',route,body,validate=selection)

    def bind_calibration(self,token,epoch,binding):
        if binding is None: raise HttpClientError('invalid_binding','binding must be an artifact')
        return self._calibration_selection('/api/v1/calibration/bind',token,epoch,binding)

    def clear_calibration(self,token,epoch):
        return self._calibration_selection('/api/v1/calibration/clear',token,epoch)

    def capture(self, body):
        if not isinstance(body, dict):
            raise HttpClientError('invalid_request', 'capture request must be an object')
        request_id = body.get('request_id')
        self._identifier(request_id)
        return self._request('POST', '/api/v1/captures', body, capture_id=request_id,
                             validate=lambda value: self._record(value, request_id))

    def query_capture(self, request_id):
        return self._request('GET', '/api/v1/captures/' + self._identifier(request_id),
                             validate=lambda value: self._record(value, request_id), missing_is_none=True)

    @staticmethod
    def _retained_id(value):
        from camera.camerad.retained import canonical_id
        try:
            return canonical_id(value)
        except CameraError as error:
            raise HttpClientError(error.code, str(error)) from error

    def list_captures(self, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise HttpClientError('invalid_request', 'limit must be an integer from 1 to 50')
        route = '/api/v1/captures?limit=' + str(limit)
        if cursor is not None:
            route += '&cursor=' + self._retained_id(cursor)
        def validate(value):
            from camera.camerad.retained import summary
            if (set(value) != {'items', 'next_cursor', 'limit', 'order'} or value.get('limit') != limit or
                    type(value.get('limit')) is not int or value.get('order') != 'newest_first' or
                    not isinstance(value.get('items'), list) or len(value['items']) > limit):
                raise ValueError('invalid retained capture list')
            seen = set()
            for item in value['items']:
                try:
                    valid = isinstance(item, dict) and summary(item, item.get('width'), item.get('height')) == item
                except CameraError as error:
                    raise ValueError('invalid retained capture summary') from error
                if not valid:
                    raise ValueError('invalid retained capture summary')
                if item['request_id'] in seen:
                    raise ValueError('duplicate retained capture')
                seen.add(item['request_id'])
            next_cursor = value['next_cursor']
            if next_cursor is not None and (not value['items'] or next_cursor != value['items'][-1]['request_id']):
                raise ValueError('invalid retained cursor')
            return value
        return self._request('GET', route, validate=validate)

    def get_capture_asset(self, request_id):
        from camera.camerad.retained import MAX_DNG_BYTES
        request_id = self._retained_id(request_id)
        def validate(value):
            if (set(value) != {'request_id', 'filename', 'byte_count', 'media_type'} or
                    value.get('request_id') != request_id or value.get('filename') != request_id + '.dng' or
                    type(value.get('byte_count')) is not int or not 8 <= value['byte_count'] <= MAX_DNG_BYTES or
                    value.get('media_type') != 'image/x-adobe-dng'):
                raise ValueError('invalid capture asset descriptor')
            return value
        return self._request('GET', '/api/v1/captures/' + request_id + '/asset', validate=validate)

    def _exchange_binary(self, operation, request_id, kind, expected_dimensions):
        from camera.camerad.retained import MAX_DNG_BYTES, MAX_THUMB_BYTES
        from camera.dng.inspect import inspect_dng
        connection = http.client.HTTPConnection(self._connect_host, self._port, timeout=self.timeout_seconds)
        response = stream = None
        transferred = False
        try:
            connection.connect()
            operation.set_socket(connection.sock)
            if not operation.mark_attempted():
                raise OSError('HTTP attachment closed')
            suffix = 'file.dng' if kind == 'dng' else 'thumbnail.png'
            connection.request('GET', '/api/v1/captures/' + request_id + '/' + suffix,
                               headers={'Host': self._host_header, 'Connection': 'close'})
            response = connection.getresponse()
            types = response.headers.get_all('Content-Type', [])
            lengths = response.headers.get_all('Content-Length', [])
            if len(types) != 1 or len(lengths) != 1 or response.headers.get_all('Transfer-Encoding', []):
                raise ValueError('ambiguous binary response framing')
            if not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError('invalid binary response length')
            length = int(lengths[0])
            if 400 <= response.status <= 599:
                if response.headers.get_content_type() != 'application/json' or length > MAX_RESPONSE_BYTES:
                    raise ValueError('invalid binary error response')
                error_payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(error_payload) != length:
                    raise ValueError('truncated binary error response')
                value = _decode_json(error_payload)
                if (not isinstance(value, dict) or set(value) != {'error'} or not isinstance(value['error'], dict) or
                        set(value['error']) != {'code', 'message'} or not isinstance(value['error']['code'], str) or
                        not value['error']['code'] or not isinstance(value['error']['message'], str)):
                    raise ValueError('invalid binary error envelope')
                operation.results.put_nowait(('error', (value['error']['code'], value['error']['message'])))
                return
            expected_type = 'image/x-adobe-dng' if kind == 'dng' else 'image/png'
            maximum = MAX_DNG_BYTES if kind == 'dng' else MAX_THUMB_BYTES
            identities = response.headers.get_all('X-Capture-Request-Id', [])
            if (response.status != 200 or response.headers.get_content_type() != expected_type or
                    not 8 <= length <= maximum or identities != [request_id]):
                raise ValueError('binary capture identity or size mismatch')
            if kind == 'dng':
                if response.headers.get_all('Content-Disposition', []) != [f'attachment; filename="{request_id}.dng"']:
                    raise ValueError('capture filename mismatch')
                stream = tempfile.TemporaryFile(mode='w+b')
                remaining = length
                while remaining:
                    block = response.read(min(65536, remaining))
                    if not block or operation.cancelled.is_set():
                        raise ValueError('capture download interrupted')
                    stream.write(block)
                    remaining -= len(block)
                stream.flush()
                structure = inspect_dng(stream)
                if ((structure['width'], structure['height']) != expected_dimensions or
                        structure['request_id'] != request_id or structure['metadata_state'] != 'valid' or
                        structure['bits_per_sample'] != 16 or structure['black_level'] != 64 or
                        structure['white_level'] != 16383 or structure['cfa'] != 'RGGB' or
                        length > expected_dimensions[0] * expected_dimensions[1] * 2 + 65536):
                    raise ValueError('downloaded DNG does not match retained capture')
                stream.seek(0)
                value = stream
            else:
                value = response.read(MAX_THUMB_BYTES + 1)
                if len(value) != length or len(value) < 45 or not value.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('invalid retained PNG')
                width, height = struct.unpack_from('>II', value, 16)
                if value[12:16] != b'IHDR' or not 1 <= width <= 320 or not 1 <= height <= 240:
                    raise ValueError('invalid thumbnail dimensions')
            if operation.cancelled.is_set():
                raise OSError('binary download cancelled')
            operation.results.put_nowait(('result', value))
            transferred = True
        except (socket.timeout, TimeoutError):
            operation.results.put_nowait(('transport', ('http_timeout', 'HTTP capture download timed out')))
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError, http.client.HTTPException):
            operation.results.put_nowait(('transport', ('http_protocol_error', 'HTTP capture download was invalid')))
        except OSError:
            operation.results.put_nowait(('transport', ('http_unavailable', 'HTTP capture download unavailable')))
        finally:
            if stream is not None and (not transferred or operation.cancelled.is_set()):
                stream.close()
            if response is not None:
                response.close()
            connection.close()

    def _binary_request(self, request_id, kind, expected_dimensions=None):
        deadline = time.monotonic() + self.timeout_seconds
        if not self._io_lock.acquire(timeout=self.timeout_seconds):
            raise HttpClientError('http_timeout', 'timed out waiting for another HTTP request')
        operation = _Operation()
        try:
            with self._state_lock:
                if self._closed:
                    raise HttpClientError('http_unavailable', 'HTTP camera client is closed')
                self._active = operation
            thread = threading.Thread(target=self._exchange_binary,
                args=(operation, request_id, kind, expected_dimensions), name='http-capture-download', daemon=True)
            try:
                thread.start()
            except RuntimeError as error:
                raise HttpClientError('http_unavailable', 'could not start capture download') from error
            try:
                kind, value = operation.results.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                operation.cancel()
                raise HttpClientError('http_timeout', 'HTTP capture download exceeded its time budget')
            if kind == 'result':
                if operation.cancelled.is_set():
                    if hasattr(value, 'close'):
                        value.close()
                    raise HttpClientError('http_unavailable', 'HTTP capture download was cancelled')
                return value
            raise HttpClientError(*value)
        finally:
            with self._state_lock:
                if self._active is operation:
                    self._active = None
            self._io_lock.release()

    def capture_thumbnail(self, request_id):
        return self._binary_request(self._retained_id(request_id), 'png')

    def open_capture(self, request_id):
        """Return a verified, owned binary stream for original-pixel review.

        The caller closes the stream. This uses the existing bounded binary
        endpoint; sensor samples are never placed in JSON messages.
        """
        from camera.camerad.retained import dimensions_for_record
        request_id = self._retained_id(request_id)
        dimensions = dimensions_for_record(self.query_capture(request_id))
        return self._binary_request(request_id, 'dng', dimensions)

    def library_rpc(self, operation, params):
        return self._request('POST','/api/v1/library/'+operation,params)

    def import_library_asset(self, source, photo_uuid=None, **kwargs):
        import uuid
        return self.library_rpc('import_path',{'source':str(source),'photo_uuid':photo_uuid or str(uuid.uuid4())})

    def open_library_asset(self, photo_uuid):
        from camera.camerad.library import identity,MAX_IMPORT_BYTES
        photo_uuid=identity(photo_uuid);descriptor=self.get_library_asset(photo_uuid)
        if descriptor['state']!='active':raise HttpClientError('capture_unavailable','library asset is unavailable')
        operation=_Operation();connection=None;stream=None
        if not self._io_lock.acquire(timeout=self.timeout_seconds):raise HttpClientError('http_timeout','library download busy')
        try:
            with self._state_lock:
                if self._closed:raise HttpClientError('closed','HTTP attachment closed')
                self._active=operation
            connection=http.client.HTTPConnection(self._connect_host,self._port,timeout=self.timeout_seconds)
            connection.connect();operation.set_socket(connection.sock);operation.mark_attempted()
            connection.request('GET','/api/v1/library/assets/'+photo_uuid+'/file',headers={'Host':self._host_header,'Connection':'close'})
            response=connection.getresponse()
            size=descriptor['byte_count']
            if (response.status!=200 or response.headers.get('X-Library-Photo-Id')!=photo_uuid
                    or response.headers.get('Content-Length')!=str(size) or not 8<=size<=MAX_IMPORT_BYTES
                    or response.headers.get_content_type()!=descriptor['media_type'] or response.headers.get('Transfer-Encoding')):
                raise HttpClientError('capture_unavailable','invalid library binary response')
            stream=tempfile.TemporaryFile(mode='w+b');remaining=size;deadline=time.monotonic()+self.timeout_seconds
            while remaining:
                if operation.cancelled.is_set() or time.monotonic()>deadline:raise HttpClientError('http_timeout','library download interrupted')
                block=response.read(min(1024*1024,remaining))
                if not block:raise HttpClientError('capture_unavailable','library download truncated')
                stream.write(block);remaining-=len(block)
            stream.flush();stream.seek(0)
            info=os.fstat(stream.fileno())
            stream.library_asset_uuid=photo_uuid;stream.library_original_request_uuid=descriptor['original_request_uuid']
            stream.library_descriptor=descriptor;stream.retained_snapshot=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
            if descriptor['media_type']=='image/x-adobe-dng':
                from camera.dng.inspect import inspect_dng
                structure=inspect_dng(stream)
                if (structure['width'],structure['height'])!=(descriptor['width'],descriptor['height']):raise HttpClientError('capture_unavailable','downloaded structure mismatch')
                stream.retained_structure=structure
            stream.seek(0);result=stream;stream=None;return result
        finally:
            if stream is not None:stream.close()
            if connection is not None:connection.close()
            with self._state_lock:
                if self._active is operation:self._active=None
            self._io_lock.release()

    def export_capture(self, request_id, destination):
        from camera.camerad.retained import dimensions_for_record, export_stream
        request_id = self._retained_id(request_id)
        record = self.query_capture(request_id)
        try:
            dimensions = dimensions_for_record(record)
            with self._binary_request(request_id, 'dng', dimensions) as source:
                return export_stream(source, destination, request_id)
        except CameraError as error:
            if isinstance(error, HttpClientError):
                raise
            raise HttpClientError(error.code, str(error), request_id=request_id) from error

    def cancel_capture(self, token, epoch, request_id):
        return self._request('POST', '/api/v1/captures/' + self._identifier(request_id) + '/cancel',
                             {'token': token, 'epoch': epoch})

    def reconnect(self, token, epoch):
        return self._request('POST', '/api/v1/device/reconnect', {'token': token, 'epoch': epoch})

    def start_preview(self, token, epoch):
        return self._request('POST', '/api/v1/preview/start', {'token': token, 'epoch': epoch})

    def stop_preview(self, token, epoch):
        return self._request('POST', '/api/v1/preview/stop', {'token': token, 'epoch': epoch})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

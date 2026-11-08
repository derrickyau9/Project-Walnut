"""Bounded stdio client for the native, synthetic camera authority.

Each client owns one child process and serializes its RPCs. A transport failure
after a capture send is an uncertain outcome, never a reason to resend. Open a
new client on the same data directory and query the persisted request ID.
Neither pixels nor image files are transported in JSON.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import uuid

from camera.camerad.service import CameraError
from camera.camerad.library import LibraryClientMixin
from camera.camerad.calibration_context import (validate_calibration_summary,
                                              validate_capture_calibration_context)
from camera.pipeline.preview import PreviewUnavailable


MAX_REQUEST_BYTES = 64 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
_DEFINITE_CAPTURE_REJECTIONS = frozenset({
    'invalid_request', 'control_denied', 'control_busy', 'camera_busy',
    'device_disconnected', 'deadline_expired', 'request_conflict',
    'test_fault_disabled', 'not_found', 'method_not_found',
})


class NativeClientError(CameraError):
    """Service-compatible error with capture reconciliation information."""
    def __init__(self, code, message, *, request_id=None, outcome_unknown=None):
        super().__init__(code, message)
        self.request_id = request_id
        self.outcome_unknown = code == 'outcome_unknown' if outcome_unknown is None else bool(outcome_unknown)


class _UnavailablePreview:
    def snapshot(self, *args, **kwargs):
        raise PreviewUnavailable('preview_unavailable')

    def state(self):
        return {'running': False, 'simulated': True, 'available': False}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('nonfinite JSON value')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite JSON value')
    return result


class NativeClient(LibraryClientMixin):
    """Own a native camerad process, with finite serialized RPC timeouts.

    ``command`` is an executable argument list (a single path is also accepted),
    or the executable path in ``HF_NATIVE_DAEMON`` (``HF_NATIVE_CAMERAD`` is an
    accepted compatibility alias). ``env`` contains optional
    overrides of the inherited environment. No shell is involved. Startup probes
    the authority, so lock and initialization errors are raised here.
    """
    backend = 'native-daemon'

    def __init__(self, data_dir, command=None, *, lease_seconds=30,
                 timeout_seconds=5, env=None):
        for value, label, maximum in ((lease_seconds, 'lease_seconds', 3600),
                                       (timeout_seconds, 'timeout_seconds', threading.TIMEOUT_MAX)):
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float)) and
                         math.isfinite(value) and value > 0 and value <= maximum)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(label + ' must be positive, finite, and within its supported range')
        self.root = Path(data_dir).expanduser().resolve()
        self.photos = self.root / 'photos'
        self.lease_seconds = float(lease_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.preview = _UnavailablePreview()
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._responses = queue.Queue(maxsize=2)
        self._reader_fault = None
        self._stderr_bytes = bytearray()
        self._closed = False
        self._next_id = 1
        self.process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._writer_thread = None

        if command is None:
            command = os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
            if not command:
                root = Path(__file__).resolve().parents[2]
                filename = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
                command = next((str(candidate) for candidate in
                                (root / 'build/native/Release' / filename, root / 'build/native' / filename)
                                if candidate.is_file()), None)
        if isinstance(command, (str, os.PathLike)):
            command = [os.fspath(command)]
        if (not isinstance(command, (list, tuple)) or not command or
                any(not isinstance(part, str) or not part or '\0' in part for part in command)):
            raise ValueError('command must be a nonempty executable argument list, or set HF_NATIVE_DAEMON')
        executable = Path(command[0]).expanduser()
        if executable.is_file():
            executable = executable.resolve()
        else:
            found = shutil.which(command[0])
            if not found:
                raise NativeClientError('native_unavailable', 'native camerad executable was not found')
            executable = Path(found).resolve()
        self.command = [str(executable), *command[1:], '--data-dir', str(self.root),
                        '--lease-seconds', format(self.lease_seconds, '.17g')]
        process_env = None
        if env is not None:
            if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) or
                                               '\0' in k or '\0' in v for k, v in env.items()):
                raise ValueError('env must contain string environment overrides')
            process_env = os.environ.copy()
            process_env.update(env)
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=65536, env=process_env)
            self._stdout_thread = threading.Thread(target=self._read_stdout,
                                                   name='native-camerad-stdout', daemon=True)
            self._stderr_thread = threading.Thread(target=self._read_stderr,
                                                   name='native-camerad-stderr', daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()
            self.get_state()
        except NativeClientError:
            self.abort()
            raise
        except (OSError, RuntimeError) as error:
            self.abort()
            raise NativeClientError('native_unavailable', 'could not start native camerad') from error

    @property
    def closed(self):
        with self._state_lock:
            return self._closed

    @property
    def stderr_tail(self):
        with self._state_lock:
            return bytes(self._stderr_bytes).decode('utf-8', errors='replace')

    def _publish(self, item):
        try:
            self._responses.put_nowait(item)
        except queue.Full:
            with self._state_lock:
                self._reader_fault = ('native_protocol_error', 'native camerad emitted unsolicited responses')

    def _read_stdout(self):
        try:
            while True:
                # The size bound also applies when a child emits no newline.
                # The pipe buffer is bounded; no unbounded read is performed.
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    self._publish(('failure', ('native_unavailable', 'native camerad closed its response stream')))
                    return
                if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                    self._publish(('failure', ('native_protocol_error', 'native camerad response exceeded the line limit or was truncated')))
                    return
                try:
                    response = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_object,
                                          parse_constant=_reject_constant, parse_float=_finite_float)
                except (ValueError, UnicodeError, RecursionError):
                    self._publish(('failure', ('native_protocol_error', 'native camerad returned invalid JSON')))
                    return
                if not isinstance(response, dict):
                    self._publish(('failure', ('native_protocol_error', 'native camerad response must be an object')))
                    return
                self._publish(('response', response))
                with self._state_lock:
                    if self._reader_fault is not None:
                        return
        except (OSError, ValueError):
            self._publish(('failure', ('native_unavailable', 'native camerad response stream failed')))
        finally:
            try:
                self.process.stdout.close()
            except (OSError, ValueError):
                pass

    def _read_stderr(self):
        try:
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                with self._state_lock:
                    self._stderr_bytes.extend(chunk)
                    if len(self._stderr_bytes) > MAX_STDERR_BYTES:
                        del self._stderr_bytes[:-MAX_STDERR_BYTES]
        except (OSError, ValueError):
            return
        finally:
            try:
                self.process.stderr.close()
            except (OSError, ValueError):
                pass

    def _stop(self, *, graceful=False):
        process = self.process
        if process is None:
            return
        if graceful and process.poll() is None:
            try:
                process.wait(timeout=min(self.timeout_seconds, 1.0))
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=2)
            except OSError:
                process.wait(timeout=2)
        for thread in (self._stdout_thread, self._stderr_thread, self._writer_thread):
            if thread is not None and thread.ident is not None and thread is not threading.current_thread():
                thread.join(timeout=1)
        streams = ((process.stdin, self._writer_thread), (process.stdout, self._stdout_thread),
                   (process.stderr, self._stderr_thread))
        for stream, thread in streams:
            # A descendant could have inherited a pipe. Closing a buffered
            # stream from here while its reader owns the I/O lock can block.
            # Such daemon threads close their own stream when the pipe ends.
            if stream is not None and (thread is None or not thread.is_alive()):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def abort(self):
        """Stop the owned process without replaying or asserting a capture result."""
        with self._state_lock:
            self._closed = True
        self._stop()

    def _transport_error(self, code, message, *, method, params, attempted):
        request_id = params.get('request_id') if method == 'capture' else None
        if method == 'capture' and attempted:
            code = 'outcome_unknown'
            message = 'capture response was not confirmed; reopen the authority and query the persisted request ID'
        self.abort()
        return NativeClientError(code, message, request_id=request_id)

    def _rpc(self, method, params):
        deadline = time.monotonic() + self.timeout_seconds
        request_id = params.get('request_id') if method == 'capture' and isinstance(params, dict) else None
        if not isinstance(params, dict):
            raise NativeClientError('invalid_request', 'RPC parameters must be an object', request_id=request_id)
        if not self._io_lock.acquire(timeout=self.timeout_seconds):
            raise NativeClientError('native_timeout', 'timed out waiting for another native RPC', request_id=request_id)
        attempted = False
        try:
            with self._state_lock:
                if self._closed:
                    raise NativeClientError('native_unavailable', 'native client is closed', request_id=request_id)
                reader_fault = self._reader_fault
            if reader_fault:
                raise self._transport_error(*reader_fault, method=method, params=params, attempted=False)
            number = self._next_id
            self._next_id += 1
            try:
                payload = (json.dumps({'id': number, 'method': method, 'params': params},
                                      allow_nan=False, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            except (ValueError, TypeError, RecursionError) as error:
                raise NativeClientError('invalid_request', 'RPC parameters are not finite JSON', request_id=request_id) from error
            if len(payload) > MAX_REQUEST_BYTES:
                raise NativeClientError('invalid_request', 'RPC request exceeds the line limit', request_id=request_id)

            # Bound even the write: a stopped/malicious child need not consume
            # stdin, and a direct pipe write can otherwise wait indefinitely.
            sent = queue.Queue(maxsize=1)
            def send():
                try:
                    remaining = memoryview(payload)
                    while remaining:
                        count = self.process.stdin.write(remaining)
                        if not count:
                            raise BrokenPipeError('short native RPC write')
                        remaining = remaining[count:]
                    self.process.stdin.flush()
                    sent.put_nowait(None)
                except (OSError, ValueError) as error:
                    sent.put_nowait(error)
                finally:
                    if self.closed:
                        try:
                            self.process.stdin.close()
                        except (OSError, ValueError):
                            pass
            writer = threading.Thread(target=send, name='native-camerad-write', daemon=True)
            self._writer_thread = writer
            try:
                writer.start()
            except RuntimeError as error:
                raise self._transport_error('native_unavailable', 'could not start the native RPC writer',
                                            method=method, params=params, attempted=False) from error
            attempted = True
            try:
                send_error = sent.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise self._transport_error('native_timeout', 'native camerad request write timed out',
                                            method=method, params=params, attempted=attempted)
            if send_error is not None:
                # A startup error can be waiting on stdout even when the
                # authority has already closed stdin. Preserve its stable code.
                if number != 1:
                    raise self._transport_error('native_unavailable', 'native camerad request stream failed',
                                                method=method, params=params, attempted=attempted)
            try:
                kind, response = self._responses.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise self._transport_error('native_timeout', 'native camerad response timed out',
                                            method=method, params=params, attempted=attempted)
            with self._state_lock:
                reader_fault = self._reader_fault
            if reader_fault:
                raise self._transport_error(*reader_fault, method=method, params=params, attempted=attempted)
            if kind == 'failure':
                raise self._transport_error(*response, method=method, params=params, attempted=attempted)
            startup_error = number == 1 and response.get('id', object()) is None and 'error' in response
            if ((not startup_error and (type(response.get('id')) is not int or response['id'] != number)) or
                    set(response) not in ({'id', 'result'}, {'id', 'error'})):
                raise self._transport_error('native_protocol_error', 'native camerad response envelope did not match the RPC',
                                            method=method, params=params, attempted=attempted)
            if 'error' in response:
                error = response['error']
                if (not isinstance(error, dict) or set(error) != {'code', 'message'} or
                        not isinstance(error['code'], str) or not error['code'] or
                        not isinstance(error['message'], str)):
                    raise self._transport_error('native_protocol_error', 'native camerad error envelope was malformed',
                                                method=method, params=params, attempted=attempted)
                if startup_error:
                    self.abort()
                uncertain = (method == 'capture' and error['code'] not in _DEFINITE_CAPTURE_REJECTIONS)
                raise NativeClientError(error['code'], error['message'], request_id=request_id,
                                        outcome_unknown=uncertain if method == 'capture' else None)
            result = response['result']
            if not isinstance(result, dict) and not (method == 'query_capture' and result is None):
                raise self._transport_error('native_protocol_error', 'native camera result must be a metadata object',
                                            method=method, params=params, attempted=attempted)
            return result
        finally:
            self._io_lock.release()

    def capabilities(self):
        return self._rpc('capabilities', {})

    def get_state(self):
        result = self._rpc('get_state', {})
        try:
            if 'calibration' in result:
                validate_calibration_summary(result['calibration'])
            if result.get('last_capture') is not None:
                record = result['last_capture']
                self._capture_record(record, record.get('request_id') if isinstance(record, dict) else None)
        except ValueError as error:
            raise self._transport_error('native_protocol_error', 'native state calibration or capture context was invalid',
                                        method='get_state', params={}, attempted=False) from error
        return result

    def acquire(self, owner):
        return self._rpc('acquire', {'owner': owner})

    def heartbeat(self, token, epoch):
        return self._rpc('heartbeat', {'token': token, 'epoch': epoch})

    def release(self, token, epoch):
        return self._rpc('release', {'token': token, 'epoch': epoch})

    def configure(self, token, epoch, settings):
        return self._rpc('configure', {'token': token, 'epoch': epoch, 'settings': settings})

    def bind_calibration(self,token,epoch,binding):
        from camera.calibration import validate_binding
        verified=validate_binding(binding)
        result=self._rpc('bind_calibration',{'token':token,'epoch':epoch,'binding':verified})
        if (result!=self._calibration_descriptor(verified) or result.get('hardware_calibrated') is not False
                or result.get('pixel_corrections_applied') is not False):
            raise self._transport_error('native_protocol_error','native calibration selection was inconsistent',method='bind_calibration',params={},attempted=False)
        return result

    def clear_calibration(self,token,epoch):
        result=self._rpc('clear_calibration',{'token':token,'epoch':epoch})
        if (result!=self._calibration_descriptor(None) or result.get('hardware_calibrated') is not False
                or result.get('pixel_corrections_applied') is not False):
            raise self._transport_error('native_protocol_error','native calibration clear was inconsistent',method='clear_calibration',params={},attempted=False)
        return result

    @staticmethod
    def _calibration_descriptor(binding):
        return {'sensor_id':'HF-Sim','binding_id':binding['binding_id'] if binding else None,
                'calibration_id':binding['binding_id'] if binding else 'synthetic-uncalibrated',
                'package_checksum':binding['package_checksum'] if binding else None,
                'hardware_calibrated':False,'pixel_corrections_applied':False}

    @staticmethod
    def _capture_record(result, request_id):
        if (not isinstance(result, dict) or not isinstance(request_id, str) or not request_id or
                result.get('request_id') != request_id or not isinstance(result.get('status'), str) or
                result['status'] not in {'accepted', 'capturing', 'complete', 'failed', 'cancelled', 'unknown'}):
            raise ValueError('native capture result identity or status is invalid')
        validate_capture_calibration_context(result)
        if result['status'] == 'complete':
            exposure, gain = result.get('actual_exposure_us'), result.get('actual_analogue_gain')
            try:
                valid_gain = (not isinstance(gain, bool) and isinstance(gain, (int, float)) and
                              math.isfinite(gain) and 1 <= gain <= 16)
            except OverflowError:
                valid_gain = False
            if (result.get('frame_id') != request_id or result.get('capture_id') != request_id or
                    type(exposure) is not int or not 100 <= exposure <= 30000000 or not valid_gain or
                    not isinstance(result.get('file_path'), str) or not result['file_path'] or
                    '\0' in result['file_path']):
                raise ValueError('native completion is missing actual settings or file identity')
        return result

    def capture(self, body):
        result = self._rpc('capture', body)
        try:
            return self._capture_record(result, body.get('request_id'))
        except ValueError as error:
            raise self._transport_error('native_protocol_error', 'native capture result did not match the request',
                                        method='capture', params=body, attempted=True) from error

    def query_capture(self, request_id):
        params = {'request_id': request_id}
        result = self._rpc('query_capture', params)
        if result is None:
            return None
        try:
            return self._capture_record(result, request_id)
        except ValueError as error:
            raise self._transport_error('native_protocol_error', 'native queried capture identity or metadata was invalid',
                                        method='query_capture', params=params, attempted=False) from error

    @staticmethod
    def _canonical_id(value):
        try:
            valid = isinstance(value, str) and str(uuid.UUID(value)) == value
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise NativeClientError('invalid_request', 'retained capture ID must be a canonical lowercase UUID')
        return value

    def list_captures(self, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise NativeClientError('invalid_request', 'retained page limit must be an integer from 1 to 50')
        if cursor is not None:
            self._canonical_id(cursor)
        result = self._rpc('list_captures', {'limit': limit, 'cursor': cursor})
        try:
            if (set(result) != {'items', 'next_cursor', 'limit', 'order'} or type(result['limit']) is not int or
                    result['limit'] != limit or result['order'] != 'newest_first' or
                    not isinstance(result['items'], list) or len(result['items']) > limit):
                raise ValueError('invalid retained page')
            seen = set()
            for item in result['items']:
                if not isinstance(item, dict):
                    raise ValueError('invalid retained item')
                identity = self._canonical_id(item.get('request_id'))
                if (identity in seen or item.get('capture_id') != identity or item.get('frame_id') != identity or
                        item.get('status') != 'complete' or item.get('filename') != identity + '.dng' or
                        item.get('simulated') is not True or item.get('bit_depth') != 14 or item.get('cfa') != 'RGGB' or
                        {'file_path', 'pixels', 'data', 'bayer'} & item.keys()):
                    raise ValueError('invalid retained identity or format')
                mode = item.get('sensor_mode')
                dimensions = {'sim-small': (128, 96), 'sim-full': (6064, 4040)}.get(mode)
                if dimensions is None or (item.get('width'), item.get('height')) != dimensions:
                    raise ValueError('invalid retained dimensions')
                for key in ('requested_exposure_us', 'actual_exposure_us'):
                    if type(item.get(key)) is not int or not 100 <= item[key] <= 30000000:
                        raise ValueError('invalid retained exposure')
                for key in ('requested_analogue_gain', 'actual_analogue_gain'):
                    if isinstance(item.get(key), bool) or not isinstance(item.get(key), (int, float)) or not 1 <= item[key] <= 16:
                        raise ValueError('invalid retained gain')
                timestamp = item.get('timestamp')
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
                    raise ValueError('invalid retained timestamp')
                for key in ('sensor_id', 'calibration_id'):
                    if not isinstance(item.get(key), str) or not 1 <= len(item[key]) <= 256 or '\0' in item[key]:
                        raise ValueError('invalid retained sensor identity')
                seen.add(identity)
            if result['next_cursor'] is not None:
                self._canonical_id(result['next_cursor'])
                if not result['items'] or result['next_cursor'] != result['items'][-1]['request_id']:
                    raise ValueError('invalid retained cursor')
            return result
        except (ValueError, TypeError, OverflowError, NativeClientError) as error:
            raise self._transport_error('native_protocol_error', 'native retained page was malformed',
                                        method='list_captures', params={}, attempted=False) from error

    def get_capture_asset(self, request_id):
        self._canonical_id(request_id)
        result = self._rpc('get_capture_asset', {'request_id': request_id})
        if (set(result) != {'request_id', 'filename', 'byte_count', 'media_type'} or
                result.get('request_id') != request_id or result.get('filename') != request_id + '.dng' or
                type(result.get('byte_count')) is not int or not 8 < result['byte_count'] <= 6064 * 4040 * 2 + 65536 or
                result.get('media_type') != 'image/x-adobe-dng'):
            raise self._transport_error('native_protocol_error', 'native retained asset descriptor was malformed',
                                        method='get_capture_asset', params={}, attempted=False)
        return result

    def library_rpc(self, operation, params):
        return self._rpc('library_' + operation, params)

    def open_capture(self, request_id):
        from camera.camerad.retained import open_retained
        asset = self.get_capture_asset(request_id)
        try:return self.open_library_asset(request_id)
        except NativeClientError as exc:
            if exc.code!='method_not_found':raise
            return open_retained(self.root, self.query_capture(request_id), asset=asset)

    def export_capture(self, request_id, destination):
        from camera.camerad.retained import export_retained
        asset = self.get_capture_asset(request_id)
        return export_retained(self.root, self.query_capture(request_id), destination, asset=asset)

    def capture_thumbnail(self, request_id):
        from camera.camerad.retained import thumbnail_retained
        asset = self.get_capture_asset(request_id)
        return thumbnail_retained(self.root, self.query_capture(request_id), asset=asset)

    def cancel_capture(self, token, epoch, request_id):
        return self._rpc('cancel_capture', {'token': token, 'epoch': epoch, 'request_id': request_id})

    def reconnect(self, token, epoch):
        return self._rpc('reconnect', {'token': token, 'epoch': epoch})

    def start_preview(self, token, epoch):
        return self._rpc('start_preview', {'token': token, 'epoch': epoch})

    def stop_preview(self, token, epoch):
        return self._rpc('stop_preview', {'token': token, 'epoch': epoch})

    def close(self):
        if self.closed:
            return
        try:
            result = self._rpc('close', {})
        except NativeClientError as error:
            # The caller can continue querying an active capture and close
            # afterward. Explicit abort() is available for forced shutdown.
            if error.code == 'camera_busy':
                raise
            self.abort()
            raise
        except BaseException:
            self.abort()
            raise
        with self._state_lock:
            self._closed = True
        self._stop(graceful=True)
        return result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self.abort()
        else:
            try:
                self.close()
            except BaseException:
                # Explicit close(camera_busy) keeps a process usable. Context
                # exit has no owner left to finish that lifecycle, so release
                # every process/pipe/lock even when close cannot be graceful.
                self.abort()
                raise


"""Bounded subprocess adapter for the optional synthetic C++ capture worker.

The Python service remains the journal/control authority. The worker writes
only the provided staging file; neither output text nor its exit status alone
means a capture has been committed.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

from camera.dng.inspect import inspect_dng


class NativeBackendError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NativeWorker:
    def __init__(self, command, *, timeout_seconds=60):
        if not isinstance(command, (list, tuple)) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError('native command must be a nonempty argument list')
        executable = Path(command[0]).resolve()
        if not executable.is_file():
            raise ValueError('native worker executable does not exist')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('native worker timeout must be positive and finite')
        self.command = [str(executable), *command[1:]]
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def capture(self, output, width, height, record, cancel):
        output = Path(output)
        if output.exists():
            raise NativeBackendError('native_output_conflict', 'native staging file already exists')
        arguments = [*self.command, '--output', str(output), '--width', str(width), '--height', str(height),
                     '--exposure-us', str(record['requested_exposure_us']), '--gain', str(record['requested_analogue_gain']),
                     '--scene', record['simulation_scene'], '--request-id', record['request_id'],
                     '--capture-id', record['capture_id'], '--sensor-id', record['sensor_id'],
                     '--calibration-id', record['calibration_id']]
        started = time.monotonic()
        process = None
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                if cancel.is_set():
                    raise NativeBackendError('cancelled', 'capture cancelled before native launch')
                if record.get('deadline_unix_ms') is not None and time.time() * 1000 > record['deadline_unix_ms']:
                    raise NativeBackendError('deadline_expired', 'native capture deadline expired before launch')
                process = subprocess.Popen(arguments, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL)
                while process.poll() is None:
                    if cancel.is_set():
                        raise NativeBackendError('cancelled', 'native capture cancelled')
                    if time.monotonic() - started > self.timeout_seconds:
                        raise NativeBackendError('native_timeout', 'native capture worker exceeded its time budget')
                    if record.get('deadline_unix_ms') is not None and time.time() * 1000 > record['deadline_unix_ms']:
                        raise NativeBackendError('deadline_expired', 'native capture deadline expired')
                    if os.fstat(stdout.fileno()).st_size > 65536 or os.fstat(stderr.fileno()).st_size > 65536:
                        raise NativeBackendError('native_protocol_error', 'native worker diagnostic output exceeded limit')
                    cancel.wait(.02)
                stdout.seek(0); stderr.seek(0)
                payload, error = stdout.read(65537), stderr.read(65537)
                if len(payload) > 65536 or len(error) > 65536:
                    raise NativeBackendError('native_protocol_error', 'native worker diagnostic output exceeded limit')
                if process.returncode:
                    raise NativeBackendError('native_worker_failed', 'native worker failed (exit ' + str(process.returncode) + ')')
                try:
                    def unique_pairs(pairs):
                        obj = {}
                        for key, value in pairs:
                            if key in obj:
                                raise ValueError('duplicate JSON key')
                            obj[key] = value
                        return obj
                    result = json.loads(payload, object_pairs_hook=unique_pairs,
                                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                except (ValueError, UnicodeError) as exc:
                    raise NativeBackendError('native_protocol_error', 'native worker returned invalid JSON') from exc
                expected = {'schema_version': 1, 'source': 'synthetic-native', 'simulated': True,
                            'width': width, 'height': height, 'bit_depth': 14, 'cfa': 'RGGB',
                            'request_id': record['request_id'], 'frame_id': record['request_id'],
                            'sensor_id': record['sensor_id'], 'calibration_id': record['calibration_id'],
                            'requested_exposure_us': record['requested_exposure_us'],
                            'requested_analogue_gain': record['requested_analogue_gain']}
                integer_keys = {'schema_version', 'width', 'height', 'bit_depth', 'requested_exposure_us'}
                if not isinstance(result, dict) or any(result.get(k) != v or isinstance(result.get(k), bool) != isinstance(v, bool) or k in integer_keys and type(result.get(k)) is not int for k, v in expected.items()):
                    raise NativeBackendError('native_protocol_error', 'native frame identity or format does not match request')
                for actual, requested in (('actual_exposure_us', 'requested_exposure_us'), ('actual_analogue_gain', 'requested_analogue_gain')):
                    value = result.get(actual)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != record[requested] or actual == 'actual_exposure_us' and type(value) is not int:
                        raise NativeBackendError('native_protocol_error', 'native simulated actual settings do not match request')
                stamp = result.get('captured_at_unix_ns')
                if type(stamp) is not int or stamp <= 0:
                    raise NativeBackendError('native_protocol_error', 'native frame timestamp is missing')
                if not output.is_file() or output.is_symlink():
                    raise NativeBackendError('native_protocol_error', 'native output file is missing or invalid')
                size = output.stat().st_size
                if size != result.get('output_bytes') or not width * height * 2 < size < width * height * 2 + 65536:
                    raise NativeBackendError('native_protocol_error', 'native DNG output size is invalid')
                with output.open('rb') as stream:
                    if stream.read(4) != b'II*\x00':
                        raise NativeBackendError('native_protocol_error', 'native DNG header is invalid')
                try:
                    dng = inspect_dng(output)
                except ValueError as exc:
                    raise NativeBackendError('native_protocol_error', 'native DNG structure is invalid') from exc
                if (dng['width'], dng['height'], dng['bits_per_sample'], dng['cfa'], dng['black_level'], dng['white_level'],
                        dng['request_id'], dng['metadata_state']) != (width, height, 16, 'RGGB', 64, 16383, record['request_id'], 'valid'):
                    raise NativeBackendError('native_protocol_error', 'native DNG metadata does not match the accepted capture')
                return result
            except OSError as exc:
                raise NativeBackendError('native_worker_failed', 'native worker or staging file unavailable') from exc
            finally:
                if process is not None:
                    self._stop(process)

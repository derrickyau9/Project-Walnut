"""Qt controller for independent local HF-Sim photography controls.

All camera calls run serially away from the GUI thread. A capture's UUID is
shown before submission and is never automatically replaced after uncertainty.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from pathlib import Path
import threading
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from PyQt5.QtGui import QImage

from camera.pipeline.preview import PreviewStream

_TERMINAL = {'complete', 'failed', 'cancelled', 'unknown'}
_ACTIVE = {'submitting', 'accepted', 'capturing'}
_SHUTDOWN_GRACE_SECONDS = 5.0
_BACKUP_NAME = re.compile(r'device-ui-unknown-[0-9a-f]{32}\.bak\Z')
_DEFINITE_REJECTIONS = {'invalid_request', 'control_denied', 'control_busy', 'camera_busy',
                        'device_disconnected', 'deadline_expired', 'request_conflict'}


def make_client_factory(backend, data_dir, command=None, *, endpoint=None):
    """Choose authority explicitly; native failure never silently changes backend."""
    if backend == 'native':
        from camera.camerad.native_client import NativeClient
        return lambda: NativeClient(data_dir, command=command, timeout_seconds=5)
    if backend == 'http':
        from camera.camerad.http_client import HttpCameraClient
        if endpoint is None:
            raise ValueError('HTTP attach mode requires an endpoint')
        return lambda: HttpCameraClient(endpoint, timeout_seconds=5)
    if backend == 'reference':
        from camera.camerad.service import CameraService
        return lambda: CameraService(data_dir)
    raise ValueError('backend must be native, reference, or http')


class CameraController(QObject):
    changed = pyqtSignal()
    _finished = pyqtSignal(str, object, object)
    _previewFinished = pyqtSignal(object)
    _libraryFinished = pyqtSignal(str, object, object)

    def __init__(self, client_factory, *, backend='native', state_path=None, parent=None, poll_ms=180):
        super().__init__(parent)
        self._factory = client_factory
        self._client = None
        self._lease = None
        self._backend = backend
        self._connection = 'connecting'
        self._status = 'Starting the local camera authority…'
        self._error = ''
        self._exposure = 10000
        self._gain = 1.0
        self._mode = 'sim-small'
        self._settings_queue = {}
        self._settings_active = False
        self._capture = 'idle'
        self._request = ''
        self._acknowledged_unknown_ids = []
        self._preserved_state_backups = []
        self._state_fault = False
        self._state_path = Path(state_path) if state_path is not None else None
        if self._state_path is not None:
            try:
                with self._state_path.open('rb') as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError('UI state exceeds its size bound')
                saved = json.loads(raw.decode('utf-8'))
                if (not isinstance(saved, dict) or type(saved.get('schema_version')) is not int
                        or saved['schema_version'] not in (1, 2, 3)):
                    raise ValueError('invalid UI request state')
                acknowledged = saved.get('acknowledged_unknown_request_ids', [])
                if (not isinstance(acknowledged, list) or len(acknowledged) > 64
                        or any(not isinstance(item, str) or not self._canonical_request_id(item)
                               for item in acknowledged)):
                    raise ValueError('invalid UI acknowledgment trace')
                backups = saved.get('preserved_ui_state_backups', [])
                if (not isinstance(backups, list) or len(backups) > 4
                        or any(not isinstance(item, dict) or set(item) != {'file', 'sha256'}
                               or not isinstance(item['file'], str) or not _BACKUP_NAME.fullmatch(item['file'])
                               or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                               for item in backups)):
                    raise ValueError('invalid preserved state trace')
                candidate = saved.get('request_id')
                if candidate is not None and (not isinstance(candidate, str) or not self._canonical_request_id(candidate)):
                    raise ValueError('invalid UI request identity')
                if candidate is None and (saved['schema_version'] != 3 or not backups):
                    raise ValueError('missing UI request identity')
                self._request = candidate or ''
                self._capture = 'unknown'
                self._acknowledged_unknown_ids = acknowledged
                self._preserved_state_backups = backups
            except FileNotFoundError:
                pass  # A new local UI state directory has no prior request.
            except (OSError, ValueError, TypeError, AttributeError):
                self._state_fault = True
                self._capture = 'unknown'
        self._path = ''
        self._closed = False
        self._pending = 0
        self._cancel_requested = False
        self._last_state = 0.0
        self._heartbeat_at = 0.0
        self._lease_seconds = 30.0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hf-device-camera')
        self._preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hf-device-preview')
        self._preview = PreviewStream(width=320, height=240, fps=3)
        self._preview_enabled = False
        self._preview_pending = False
        self._preview_url = ''
        self._preview_generation = 0
        self._library_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hf-device-library')
        self._library_pending = False
        self._library_items = []
        self._library_next = ''
        self._library_message = 'Load committed captures from the retained journal.'
        self._selected_capture = ''
        self._selected_info = ''
        self._review_url = ''
        self._export_message = ''
        self._libraryFinished.connect(self._on_library_finished)
        self._finished.connect(self._on_finished)
        self._previewFinished.connect(self._on_preview)
        self._timer = QTimer(self)
        self._poll_ms = poll_ms
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(350)
        self._preview_timer.timeout.connect(self._preview_tick)
        self._preview_timer.start()
        QTimer.singleShot(0, lambda: self._submit('connect', self._connect_client))

    @pyqtProperty(str, notify=changed)
    def backendLabel(self):
        if self._backend == 'http':
            return 'HF-Sim - local API session'
        return ('HF-Sim - native C++ authority' if self._backend == 'native'
                else 'HF-Sim - Python reference authority')

    @pyqtProperty(str, notify=changed)
    def connectionState(self): return self._connection

    @pyqtProperty(str, notify=changed)
    def statusText(self): return self._status

    @pyqtProperty(str, notify=changed)
    def errorText(self): return self._error

    @pyqtProperty(int, notify=changed)
    def exposureUs(self): return self._exposure

    @pyqtProperty(float, notify=changed)
    def analogueGain(self): return self._gain

    @pyqtProperty(str, notify=changed)
    def sensorMode(self): return self._mode

    @pyqtProperty(str, notify=changed)
    def captureState(self): return self._capture

    @pyqtProperty(str, notify=changed)
    def lastRequestId(self): return self._request

    @pyqtProperty(str, notify=changed)
    def dngPath(self): return self._path

    @pyqtProperty(bool, notify=changed)
    def busy(self): return self._capture in _ACTIVE or self._connection == 'connecting'

    @pyqtProperty(bool, notify=changed)
    def canCapture(self):
        return (not self._closed and not self._state_fault and self._connection == 'ready'
                and self._capture not in _ACTIVE | {'unknown'}
                and not self._settings_active and not self._settings_queue)

    @pyqtProperty(bool, notify=changed)
    def canAcknowledgeUnknown(self):
        return (not self._closed and not self._pending and self._connection == 'ready'
                and self._capture == 'unknown'
                and bool(self._request or self._state_fault or self._preserved_state_backups))

    @pyqtProperty(bool, notify=changed)
    def previewActive(self): return self._preview_enabled

    @pyqtProperty(str, notify=changed)
    def previewUrl(self): return self._preview_url

    @pyqtProperty(bool, notify=changed)
    def canBrowseCaptures(self):
        return not self._closed and self._client is not None and self._connection != 'connecting'

    @pyqtProperty('QVariantList', notify=changed)
    def libraryItems(self): return self._library_items

    @pyqtProperty(bool, notify=changed)
    def libraryLoading(self): return self._library_pending

    @pyqtProperty(str, notify=changed)
    def libraryMessage(self): return self._library_message

    @pyqtProperty(str, notify=changed)
    def libraryNextCursor(self): return self._library_next

    @pyqtProperty(str, notify=changed)
    def selectedCaptureId(self): return self._selected_capture

    @pyqtProperty(str, notify=changed)
    def selectedCaptureInfo(self): return self._selected_info

    @pyqtProperty(str, notify=changed)
    def reviewUrl(self): return self._review_url

    @pyqtProperty(str, notify=changed)
    def exportMessage(self): return self._export_message

    def _library_submit(self, action, function):
        if not self.canBrowseCaptures or self._library_pending:
            return
        self._library_pending = True
        self.changed.emit()
        future = self._library_executor.submit(function)
        def complete(done):
            try:
                result, error = done.result(), None
            except Exception as exc:
                result, error = None, exc
            if not self._closed:
                self._libraryFinished.emit(action, result, error)
        future.add_done_callback(complete)

    @pyqtSlot()
    def refreshLibrary(self):
        if self.canBrowseCaptures and not self._library_pending:
            self._library_message = 'Loading newest committed captures...'
            self._library_submit('list', lambda: self._client.list_captures(limit=20, cursor=None))

    @pyqtSlot()
    def nextLibraryPage(self):
        if self.canBrowseCaptures and not self._library_pending and self._library_next:
            cursor = self._library_next
            self._library_message = 'Loading older committed captures...'
            self._library_submit('list', lambda: self._client.list_captures(limit=20, cursor=cursor))

    @pyqtSlot(str)
    def selectCapture(self, request_id):
        if not self.canBrowseCaptures or self._library_pending:
            return
        item = next((entry for entry in self._library_items if entry['request_id'] == request_id), None)
        if item is None:
            return
        self._selected_capture = request_id
        self._selected_info = (f"{item['filename']}\n{item['width']} x {item['height']} | "
                               f"{item['actual_exposure_us']} us | {item['actual_analogue_gain']:.2f}x")
        self._review_url = ''
        self._export_message = ''
        self._library_message = 'Reading display conversion from the retained DNG...'
        self._library_submit('thumbnail', lambda: {'request_id': request_id,
            'png': self._client.capture_thumbnail(request_id)})

    @pyqtSlot(str)
    def exportSelected(self, destination):
        if not self.canBrowseCaptures or self._library_pending or not self._selected_capture:
            return
        try:
            if not isinstance(destination, str) or not destination.strip() or any(ord(char) < 32 for char in destination):
                raise ValueError('missing destination')
            destination = destination.strip()
            if destination.startswith('file:'):
                url = QUrl(destination)
                if not url.isLocalFile():
                    raise ValueError('nonlocal destination')
                destination = url.toLocalFile()
            elif '://' in destination:
                raise ValueError('nonlocal destination')
            path = Path(destination)
            if not path.is_absolute() or path.suffix.lower() != '.dng':
                raise ValueError('invalid destination')
        except (TypeError, ValueError):
            self._export_message = 'Choose an absolute local path ending in .dng. Existing files are never overwritten.'
            self.changed.emit()
            return
        request_id = self._selected_capture
        self._export_message = 'Exporting a copy of the retained DNG...'
        self._library_submit('export', lambda: self._client.export_capture(request_id, str(path)))

    def _validate_library(self, result):
        if (not isinstance(result, dict) or result.get('order') != 'newest_first'
                or result.get('limit') != 20 or not isinstance(result.get('items'), list)
                or len(result['items']) > 20):
            raise ValueError('invalid capture page')
        cursor = result.get('next_cursor')
        if cursor is not None and (not isinstance(cursor, str) or not self._canonical_request_id(cursor)):
            raise ValueError('invalid capture cursor')
        items, seen = [], set()
        for item in result['items']:
            if not isinstance(item, dict):
                raise ValueError('invalid capture summary')
            rid = item.get('request_id')
            mode = item.get('sensor_mode')
            dims = {'sim-small': (128, 96), 'sim-full': (6064, 4040)}.get(mode) if isinstance(mode, str) else None
            if (not isinstance(rid, str) or not self._canonical_request_id(rid) or rid in seen
                    or item.get('capture_id') != rid or item.get('frame_id') != rid
                    or item.get('status') != 'complete' or item.get('filename') != rid + '.dng'
                    or dims is None or (item.get('width'), item.get('height')) != dims
                    or type(item.get('width')) is not int or type(item.get('height')) is not int
                    or item.get('bit_depth') != 14 or item.get('cfa') != 'RGGB'
                    or item.get('simulated') is not True
                    or type(item.get('actual_exposure_us')) is not int or not 0 < item['actual_exposure_us'] <= 0xffffffff
                    or not self._positive_finite(item.get('actual_analogue_gain'))):
                raise ValueError('invalid committed capture summary')
            seen.add(rid)
            items.append({key: item[key] for key in ('request_id', 'filename', 'status', 'sensor_mode',
                'width', 'height', 'actual_exposure_us', 'actual_analogue_gain')})
        return items, cursor or ''

    @staticmethod
    def _review_data_url(raw):
        if (not isinstance(raw, bytes) or len(raw) > 256 * 1024 or len(raw) < 33
                or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR'
                or struct.unpack('>I', raw[8:12])[0] != 13):
            raise ValueError('invalid retained thumbnail')
        width, height = struct.unpack('>II', raw[16:24])
        if not 1 <= width <= 320 or not 1 <= height <= 240:
            raise ValueError('oversized retained thumbnail')
        image = QImage.fromData(raw, 'PNG')
        if image.isNull() or image.width() != width or image.height() != height:
            raise ValueError('invalid retained thumbnail')
        return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')

    @pyqtSlot(str, object, object)
    def _on_library_finished(self, action, result, error):
        self._library_pending = False
        if self._closed:
            return
        try:
            if error is not None:
                raise ValueError('retained asset unavailable')
            if action == 'list':
                self._library_items, self._library_next = self._validate_library(result)
                self._selected_capture = self._selected_info = self._review_url = self._export_message = ''
                self._library_message = (f'Showing {len(self._library_items)} committed captures. '
                    'Unknown/failed requests remain available through original-ID query.')
            elif action == 'thumbnail':
                if not isinstance(result, dict) or result.get('request_id') != self._selected_capture:
                    raise ValueError('thumbnail identity changed')
                self._review_url = self._review_data_url(result.get('png'))
                self._library_message = 'Retained DNG display conversion - synthetic and uncalibrated; not live preview.'
            elif action == 'export':
                if (not isinstance(result, dict) or result.get('request_id') != self._selected_capture
                        or result.get('filename') != self._selected_capture + '.dng'
                        or type(result.get('byte_count')) is not int or result['byte_count'] <= 0
                        or not isinstance(result.get('destination'), str)):
                    raise ValueError('invalid export confirmation')
                self._export_message = f"Exported {result['byte_count']} bytes to {result['destination']}"
        except (ValueError, TypeError, OverflowError):
            if action == 'export':
                self._export_message = 'Export was not completed. The source is unchanged; choose a new local destination.'
            else:
                self._library_message = 'Retained captures could not be read or validated. Capture request state is unchanged.'
                self._review_url = ''
        self.changed.emit()

    def _submit(self, action, function):
        if self._closed:
            return
        self._pending += 1
        self.changed.emit()
        future = self._executor.submit(function)
        def complete(done):
            try:
                result, error = done.result(), None
            except Exception as exc:
                result, error = None, exc
            if not self._closed:
                self._finished.emit(action, result, error)
        future.add_done_callback(complete)

    def _renew(self):
        now = time.monotonic()
        if self._lease and now - self._heartbeat_at >= min(5.0, self._lease_seconds / 3):
            self._client.heartbeat(self._lease['token'], self._lease['epoch'])
            self._heartbeat_at = now

    def _accept_lease(self, lease):
        if (not isinstance(lease, dict) or not isinstance(lease.get('token'), str) or not lease['token']
                or type(lease.get('epoch')) is not int or lease['epoch'] < 0
                or not self._positive_finite(lease.get('lease_seconds')) or lease['lease_seconds'] > 3600):
            raise ValueError('invalid authority lease')
        self._lease = lease
        self._lease_seconds = float(lease['lease_seconds'])
        self._heartbeat_at = time.monotonic()

    def _connect_client(self):
        self._client = self._factory()
        self._accept_lease(self._client.acquire('on-device-ui'))
        result = {'state': self._client.get_state()}
        if self._request:
            result['record'] = self._client.query_capture(self._request)
        return result

    def _read_state(self):
        self._renew()
        return {'state': self._client.get_state()}

    def _query(self, request_id):
        # Query remains safe after lease/device loss and never submits a capture.
        return {'record': self._client.query_capture(request_id)}

    def _queue_setting(self, name, value):
        if (self._closed or self._connection != 'ready' or self._state_fault
                or self._capture in _ACTIVE | {'unknown'}):
            return
        self._settings_queue[name] = value
        if name == 'requested_exposure_us': self._exposure = value
        elif name == 'requested_analogue_gain': self._gain = value
        elif name == 'sensor_mode': self._mode = value
        self._status = 'Applying settings - capture held until confirmed'
        self._error = ''
        self._invalidate_preview()
        self.changed.emit()
        self._preview_tick()
        self._dispatch_settings()

    def _dispatch_settings(self):
        if self._closed or self._settings_active or not self._settings_queue:
            return
        batch = self._settings_queue
        self._settings_queue = {}
        self._settings_active = True
        def configure():
            self._renew()
            accepted = self._client.configure(self._lease['token'], self._lease['epoch'], batch)
            return {'settings_batch': batch, 'settings_ack': accepted, 'state': self._client.get_state()}
        self._submit('configure', configure)

    @pyqtSlot(int)
    def setExposureUs(self, value):
        if self.busy or not 100 <= value <= 30000000:
            return
        self._queue_setting('requested_exposure_us', value)

    @pyqtSlot(float)
    def setAnalogueGain(self, value):
        if self.busy or not math.isfinite(value) or not 1 <= value <= 16:
            return
        self._queue_setting('requested_analogue_gain', round(value, 2))

    @pyqtSlot(str)
    def setSensorMode(self, value):
        if self.busy or value not in {'sim-small', 'sim-full'}:
            return
        self._queue_setting('sensor_mode', value)

    @staticmethod
    def _canonical_request_id(value):
        try:
            return str(uuid.UUID(value)) == value
        except (ValueError, TypeError, AttributeError):
            return False

    def _save_request_state(self, request_id, *, acknowledged=None, backups=None):
        if self._state_path is None:
            return
        acknowledged = self._acknowledged_unknown_ids if acknowledged is None else acknowledged
        backups = self._preserved_state_backups if backups is None else backups
        temporary = None
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._state_path.parent,
                                             prefix='.device-ui-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'schema_version': 3, 'request_id': request_id or None,
                           'acknowledged_unknown_request_ids': acknowledged[-64:],
                           'preserved_ui_state_backups': backups[-4:]}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _backup_unrecoverable_state(self):
        if self._state_path is None:
            raise OSError('missing UI state path')
        with self._state_path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise OSError('unrecoverable state exceeds backup bound')
        target = self._state_path.parent / ('device-ui-unknown-' + uuid.uuid4().hex + '.bak')
        created = False
        try:
            with target.open('xb') as stream:
                created = True
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            if created:
                target.unlink(missing_ok=True)
            raise
        return {'file': target.name, 'sha256': hashlib.sha256(raw).hexdigest()}

    def _verify_state_backup(self, entry):
        path = self._state_path.parent / entry['file']
        if path.is_symlink():
            raise OSError('preserved state cannot be a link')
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise OSError('preserved state is unavailable or changed')

    @pyqtSlot()
    def acknowledgeUnknown(self):
        if not self.canAcknowledgeUnknown:
            return
        acknowledged = list(self._acknowledged_unknown_ids)
        backups = list(self._preserved_state_backups)
        try:
            if self._state_fault:
                backups.append(self._backup_unrecoverable_state())
            elif not self._request:
                self._verify_state_backup(backups[-1])
            if self._request and self._request not in acknowledged:
                acknowledged.append(self._request)
            self._save_request_state(self._request, acknowledged=acknowledged, backups=backups)
        except OSError:
            self._status = 'Acknowledgment was not saved - capture held'
            self._error = ('Saved state could not be preserved (64 KiB maximum). Restore it or inspect the journal '
                           'before choosing another UI state directory. No capture was submitted.')
            self.changed.emit()
            return
        self._acknowledged_unknown_ids = acknowledged[-64:]
        self._preserved_state_backups = backups[-4:]
        self._state_fault = False
        # UI permission only: never update/replay the authority's unknown record.
        self._capture = 'acknowledged'
        self._status = 'Unknown record kept. The next shutter press uses a new request ID.'
        self._error = ('The earlier request remains unknown. A new shot may create an additional image.' if self._request
                       else 'The previous request ID is unavailable. Its raw UI state was preserved. A new shot may create an additional image.')
        self._invalidate_preview()
        self.changed.emit()

    @pyqtSlot()
    def shutter(self):
        if not self.canCapture:
            return
        # Persist/display identity before crossing the submission boundary.
        request_id = str(uuid.uuid4())
        try:
            self._save_request_state(request_id)
        except OSError:
            self._error = 'The request ID could not be saved. Capture was not submitted.'
            self._status = 'Storage unavailable'
            self.changed.emit()
            return
        self._request = request_id
        self._capture = 'submitting'
        self._cancel_requested = False
        self._invalidate_preview()
        self._path = ''
        self._error = ''
        self._status = 'Submitting capture · keep this request ID'
        settings = {'sensor_mode': self._mode, 'requested_exposure_us': self._exposure,
                    'requested_analogue_gain': self._gain, 'output_format': 'dng',
                    'simulation_scene': 'scene'}
        self.changed.emit()
        def capture():
            self._renew()
            # Full capture settings are carried by the durable request itself.
            request = dict(settings, request_id=request_id, token=self._lease['token'],
                           control_epoch=self._lease['epoch'])
            return {'record': self._client.capture(request)}
        self._submit('capture', capture)

    @pyqtSlot()
    def queryLast(self):
        if self._request and self._client is not None and not self._closed and not self._pending:
            request_id = self._request
            self._status = 'Querying the original request ID…'
            self.changed.emit()
            self._submit('query', lambda: self._query(request_id))

    @pyqtSlot()
    def cancelCapture(self):
        if self._capture not in _ACTIVE or not self._lease or self._cancel_requested:
            return
        self._cancel_requested = True
        request_id = self._request
        self._status = 'Cancellation requested · waiting for a confirmed result'
        self.changed.emit()
        def cancel():
            self._renew()
            self._client.cancel_capture(self._lease['token'], self._lease['epoch'], request_id)
            return self._query(request_id)
        self._submit('cancel', cancel)

    @pyqtSlot()
    def reconnect(self):
        if self._closed or self._pending or self._connection == 'connecting' or self._capture in _ACTIVE:
            return
        self._connection = 'connecting'
        self._status = 'Reconnecting · the original capture will only be queried'
        self._error = ''
        request_id = self._request
        self.changed.emit()
        def reconnect():
            client = self._client
            alive = client is not None and not getattr(client, 'closed', False)
            process = getattr(client, 'process', None)
            if process is not None and process.poll() is not None:
                alive = False
            if alive:
                try:
                    client.get_state()
                except Exception:
                    alive = False
            if alive:
                try:
                    client.heartbeat(self._lease['token'], self._lease['epoch'])
                except Exception:
                    self._accept_lease(client.acquire('on-device-ui'))
                client.reconnect(self._lease['token'], self._lease['epoch'])
                result = {'state': client.get_state()}
            else:
                if client is not None:
                    if hasattr(client, 'abort'):
                        client.abort()
                    else:
                        try:
                            client.close()
                        except Exception:
                            pass
                result = self._connect_client()
            if request_id:
                result['record'] = self._client.query_capture(request_id)
            return result
        self._submit('reconnect', reconnect)

    def _poll(self):
        if self._closed or self._pending or self._connection != 'ready':
            return
        if self._capture in _ACTIVE and self._request:
            request_id = self._request
            def poll_capture():
                self._renew()
                return self._query(request_id)
            self._submit('poll_capture', poll_capture)
        elif (time.monotonic() - self._last_state >= 1
              or self._lease and time.monotonic() - self._heartbeat_at >= min(5.0, self._lease_seconds / 3)):
            self._last_state = time.monotonic()
            self._submit('state', self._read_state)

    @staticmethod
    def _positive_finite(value):
        try:
            return type(value) in (int, float) and value > 0 and math.isfinite(value)
        except (OverflowError, ValueError):
            return False

    def _unknown_response(self, status, message):
        self._capture = 'unknown'
        self._path = ''
        self._status = status
        self._error = message

    def _apply_record(self, record, *, query=False):
        if record is None:
            if self._request:
                self._unknown_response('No journal record found - outcome remains unconfirmed',
                                       'Keep the original request ID. No capture will be retried.')
            return
        if not isinstance(record, dict) or record.get('request_id') != self._request:
            self._unknown_response('Unexpected response - query the original request ID',
                                   'Capture identity could not be confirmed.')
            return
        status = record.get('status')
        if not isinstance(status, str) or status not in _TERMINAL | {'accepted', 'capturing'}:
            self._unknown_response('Unrecognized capture status - outcome unconfirmed',
                                   'The authority did not return a supported capture result.')
            return
        if status == 'complete':
            path = record.get('file_path')
            exposure = record.get('actual_exposure_us')
            if (record.get('capture_id') != self._request or record.get('frame_id') != self._request
                    or type(exposure) is not int or not 0 < exposure <= 0xffffffff
                    or not self._positive_finite(record.get('actual_analogue_gain'))
                    or not isinstance(path, str) or not path.strip() or '\0' in path):
                self._unknown_response('Completion metadata is inconsistent - outcome unconfirmed',
                                       'Query the original request ID. The response cannot confirm a saved DNG.')
                return
            # Treat the returned path as display text; never open an arbitrary metadata path.
            self._capture = status
            self._path = path
            self._status = 'Capture complete - synthetic DNG saved'
            self._error = ''
        else:
            self._capture = status
            self._path = ''
            if status == 'unknown':
                self._status = 'Capture outcome unknown - keep the original request ID'
                self._error = 'Query this request or reconnect to reconcile. The shutter is held.'
            elif status == 'failed':
                self._status = 'Capture failed - no confirmed DNG'
                self._error = 'The authority reported failure. Inspect the original request before another capture.'
                if (record.get('fault') == 'disconnect' or isinstance(record.get('error_code'), str)
                        and record['error_code'] in {'disconnect', 'device_disconnected'}):
                    self._connection = 'disconnected'
            elif status == 'cancelled':
                self._status = 'Capture cancelled'
                self._error = ''
            else:
                self._status = ('Capturing - synthetic pixels are being written' if status == 'capturing'
                                else 'Capture accepted - waiting for its journal result')

    def _state_values(self, state):
        if not isinstance(state, dict) or type(state.get('connected')) is not bool:
            raise ValueError('invalid authority state')
        settings = state.get('settings')
        if not isinstance(settings, dict):
            raise ValueError('invalid authority settings')
        exposure = settings.get('requested_exposure_us')
        gain = settings.get('requested_analogue_gain')
        mode = settings.get('sensor_mode')
        if (type(exposure) is not int or not 100 <= exposure <= 30000000
                or not self._positive_finite(gain) or not 1 <= gain <= 16
                or not isinstance(mode, str) or mode not in {'sim-small', 'sim-full'}):
            raise ValueError('invalid authority settings')
        return exposure, gain, mode

    @pyqtSlot(str, object, object)
    def _on_finished(self, action, result, error):
        self._pending = max(0, self._pending - 1)
        if self._closed:
            return
        if action == 'configure':
            self._settings_active = False
            try:
                if error is not None:
                    raise ValueError('settings request failed') from error
                if not isinstance(result, dict) or not isinstance(result.get('settings_batch'), dict):
                    raise ValueError('invalid settings response')
                state, ack, batch = result.get('state'), result.get('settings_ack'), result['settings_batch']
                exposure, gain, mode = self._state_values(state)
                ack_values = self._state_values({'connected': True, 'settings': ack})
                owner = state.get('control')
                if (not state['connected'] or not isinstance(owner, dict) or not self._lease
                        or owner.get('epoch') != self._lease['epoch']
                        or state.get('settings_persistent') is not True
                        or state.get('settings_format') != 'hf-device-settings-v1'
                        or ack_values != (exposure, gain, mode)
                        or any(ack.get(key) != value or state['settings'].get(key) != value
                               for key, value in batch.items())):
                    raise ValueError('settings acknowledgment or readback mismatch')
            except (ValueError, TypeError, AttributeError):
                self._settings_queue.clear()
                self._connection = 'disconnected'
                self._status = 'Settings not confirmed - capture held'
                self._error = 'Reconnect to read the authoritative settings. The last edit may or may not have been applied; it will not be retried automatically.'
                self._invalidate_preview()
                self.changed.emit()
                return
            if 'requested_exposure_us' not in self._settings_queue: self._exposure = exposure
            if 'requested_analogue_gain' not in self._settings_queue: self._gain = gain
            if 'sensor_mode' not in self._settings_queue: self._mode = mode
            if self._settings_queue:
                self._dispatch_settings()
            else:
                self._status = 'Settings confirmed by local camera'
                self._error = ''
            self.changed.emit()
            return
        if error is not None:
            code = getattr(error, 'code', 'unavailable')
            if action == 'capture' and code in _DEFINITE_REJECTIONS:
                self._capture = 'failed'
                self._status = 'Capture was rejected · no automatic retry'
            elif action in {'capture', 'poll_capture', 'query', 'cancel'} and self._request:
                self._capture = 'unknown'
                self._status = 'Capture outcome unknown · retain the original request ID'
            else:
                self._status = 'Local camera unavailable'
            if code in {'device_disconnected', 'control_denied', 'control_busy'} or code not in _DEFINITE_REJECTIONS:
                self._connection = 'disconnected'
            if code == 'control_busy':
                self._error = 'Control is held by another session. Release it there, then reconnect.'
            else:
                self._error = ('Native camera could not be started. Build hf-camerad or select the explicit Python reference mode.'
                               if action == 'connect' and self._backend == 'native'
                               else 'Connection or control was lost. Reconnect and query the original request; no photo is retried.')
            self._invalidate_preview()
            self.changed.emit()
            return
        if not isinstance(result, dict):
            self._connection = 'disconnected'
            self._error = 'The authority returned an invalid response. Reconnect to query the original ID.'
            self._invalidate_preview()
            self.changed.emit()
            return
        if 'state' in result:
            state = result['state']
            try:
                exposure, gain, mode = self._state_values(state)
            except ValueError:
                self._connection = 'disconnected'
                self._status = 'Camera state could not be validated'
                self._error = 'Reconnect to query the original request. Capture is held.'
                self._invalidate_preview()
                self.changed.emit()
                return
            self._connection = 'ready' if state['connected'] else 'disconnected'
            if action in {'connect', 'reconnect'}:
                self._status = 'Ready - local synthetic capture' if self._connection == 'ready' else 'Simulated device disconnected'
                self._error = ''
                self._exposure, self._gain, self._mode = exposure, gain, mode
                self._timer.setInterval(max(1, min(self._poll_ms, int(self._lease_seconds * 1000 / 4))))
            elif self._connection == 'disconnected':
                self._status = 'Simulated device disconnected - reconnect to continue'
            elif not self._settings_active and not self._settings_queue:
                self._exposure, self._gain, self._mode = exposure, gain, mode
        if 'record' in result:
            self._apply_record(result['record'], query=action in {'query', 'reconnect'})
        if self._state_fault:
            self._unknown_response('Saved UI request state is invalid - capture held',
                                   'The original UI file is unchanged. Restore it, or explicitly preserve its raw state before allowing a new UUID shot. The previous request ID is unavailable.')
        elif self._capture == 'unknown' and not self._request and self._preserved_state_backups:
            self._unknown_response('Previous request ID is unavailable - outcome unconfirmed',
                                   'Raw UI state is preserved. Explicit acknowledgment is required before a new UUID shot.')
        if self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'}:
            self._invalidate_preview()
        self.changed.emit()

    @pyqtSlot()
    def togglePreview(self):
        if not self._preview_enabled and (self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'}):
            return
        self._preview_enabled = not self._preview_enabled
        if self._preview_enabled:
            self._preview.start()
            self._preview_tick()
        else:
            self._preview.stop()
            self._invalidate_preview()
        self.changed.emit()

    def _invalidate_preview(self):
        self._preview_generation += 1
        self._preview_url = ''

    def _preview_tick(self):
        if (self._closed or not self._preview_enabled or self._preview_pending
                or self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'}):
            return
        settings = {'requested_exposure_us': self._exposure, 'requested_analogue_gain': self._gain,
                    'sensor_mode': self._mode, 'simulation_scene': 'scene'}
        self._preview_pending = True
        generation = self._preview_generation
        future = self._preview_executor.submit(lambda: self._preview.snapshot(settings))
        def complete(done):
            try:
                packet = done.result()
            except Exception:
                packet = None
            if not self._closed:
                self._previewFinished.emit((generation, packet))
        future.add_done_callback(complete)

    @pyqtSlot(object)
    def _on_preview(self, result):
        self._preview_pending = False
        generation, packet = result
        if (self._preview_enabled and packet is not None and generation == self._preview_generation
                and self._connection == 'ready' and self._capture not in _ACTIVE | {'unknown'}):
            self._preview_url = 'data:image/png;base64,' + base64.b64encode(packet['png']).decode('ascii')
            self.changed.emit()

    @pyqtSlot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._preview_timer.stop()
        self._preview.stop()
        self._library_executor.shutdown(wait=True, cancel_futures=True)
        def close_client():
            if self._client is None:
                return
            # Give accepted work a short chance to finish; never submit on close.
            deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
            while self._request and time.monotonic() < deadline:
                try:
                    record = self._client.query_capture(self._request)
                    if record is None or record.get('status') in _TERMINAL:
                        break
                except Exception:
                    break
                time.sleep(.05)
            try:
                if self._lease:
                    self._client.release(self._lease['token'], self._lease['epoch'])
            except Exception:
                pass
            try:
                self._client.close()
            except Exception as exc:
                if hasattr(self._client, 'abort'):
                    self._client.abort()
                elif self._backend == 'reference' and getattr(exc, 'code', None) == 'camera_busy':
                    client = self._client
                    def deferred_close():
                        while True:
                            try:
                                client.close()
                                return
                            except Exception as error:
                                if getattr(error, 'code', None) != 'camera_busy':
                                    return
                                time.sleep(.05)
                    self._deferred_close_thread = threading.Thread(target=deferred_close,
                        name='hf-device-reference-close', daemon=True)
                    self._deferred_close_thread.start()
        self._executor.submit(close_client)
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._preview_executor.shutdown(wait=True, cancel_futures=True)

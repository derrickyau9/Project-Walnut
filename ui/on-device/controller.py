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
import sys
import tempfile
from pathlib import Path
import threading
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, QUrl, Qt, pyqtProperty, pyqtSignal, pyqtSlot

from PyQt5.QtGui import QImage

# This controller is also loaded directly by the existing standalone UI tools.
_UI_DIRECTORY = str(Path(__file__).resolve().parent)
if _UI_DIRECTORY not in sys.path:
    sys.path.insert(0, _UI_DIRECTORY)

from camera_capabilities import capability_model, exposure_steps
from image_pipeline import FrameStore, SyntheticViewfinder, render_retained
from parameter_state import SettingsIntent, validate_patch

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
    previewChanged = pyqtSignal()
    workflowFinished = pyqtSignal(object, object)
    shutdownStarted = pyqtSignal()
    settingsSettled = pyqtSignal(int, 'QVariantMap')
    settingsFailed = pyqtSignal(int, str)
    localControlRequested = pyqtSignal(str)
    _finished = pyqtSignal(str, object, object)
    _previewFinished = pyqtSignal(object)
    _libraryFinished = pyqtSignal(str, object, object)
    _savedThumbnailFinished = pyqtSignal(str, object, object)

    def __init__(self, client_factory, *, backend='native', state_path=None, parent=None, poll_ms=180,
                 frame_store=None, performance=None):
        super().__init__(parent)
        self._factory = client_factory
        self._client = None
        self._lease = None
        self._backend = backend
        self._capabilities = capability_model()
        self._confirmed_settings = {}
        self._workflow_active = False
        self.frames = frame_store or FrameStore()
        self._performance = performance
        self._connection = 'connecting'
        self._status = 'Starting the local camera authority…'
        self._error = ''
        self._exposure = 10000
        self._gain = 1.0
        self._mode = 'sim-small'
        self._settings_queue = {}
        self._settings_active = False
        self._intent = SettingsIntent()
        self._settings_sent = {}
        self._settings_sent_revision = 0
        self._settings_first_edit = None
        self._temporary = None
        self._temporary_recovery = False
        self._settings_error = ''
        self._parameter_action = None
        self._maintenance_token = ''
        self._maintenance_reason = ''
        self._capture = 'idle'
        self._request = ''
        self._acknowledged_unknown_ids = []
        self._preserved_state_backups = []
        self._state_fault = False
        self._state_path = Path(state_path) if state_path is not None else None
        self._temporary_path = self._state_path.with_name('temporary-settings.json') if self._state_path else None
        self._load_temporary_journal()
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
        self._preview = SyntheticViewfinder(width=640, height=424, fps=30)
        self._preview_enabled = False
        self._preview_pending = False
        self._preview_url = ''
        self._preview_generation = 0
        self._preview_frame_id = 0
        self._preview_produced_ns = 0
        self._library_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hf-device-library')
        self._library_pending = False
        self._library_refresh_requested = False
        self._library_items = []
        self._library_next = ''
        self._library_message = 'Load committed captures from the retained journal.'
        self._selected_capture = ''
        self._selected_info = ''
        self._review_url = ''
        self._review_original_url = ''
        self._review_width = self._review_height = 0
        self._review_tile_url = ''
        self._review_tile = (0, 0, 0, 0)
        self._review_tile_next = None
        self._export_message = ''
        self._saved_thumbnail_url = ''
        self._saved_thumbnail_id = ''
        self._saved_thumbnail_next = None
        self._saved_thumbnail_pending = False
        self._libraryFinished.connect(self._on_library_finished)
        self._savedThumbnailFinished.connect(self._on_saved_thumbnail)
        self._finished.connect(self._on_finished)
        self._previewFinished.connect(self._on_preview)
        self._timer = QTimer(self)
        self._poll_ms = poll_ms
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.timeout.connect(self._dispatch_settings)
        self._preview_timer = QTimer(self)
        self._preview_timer.setTimerType(Qt.PreciseTimer)
        self._preview_timer.setInterval(33)
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
    def busy(self): return self._workflow_active or self._capture in _ACTIVE or self._connection == 'connecting'

    @pyqtProperty(bool, notify=changed)
    def canCapture(self):
        return (not self._closed and not self._workflow_active and not self._state_fault and self._connection == 'ready'
                and self._capture not in _ACTIVE | {'unknown'}
                and not self._settings_active and not self._settings_queue and not self._temporary_recovery and not self._maintenance_token)

    @pyqtProperty(bool, notify=changed)
    def canAcknowledgeUnknown(self):
        return (not self._closed and not self._pending and self._connection == 'ready'
                and self._capture == 'unknown'
                and bool(self._request or self._state_fault or self._preserved_state_backups))

    @pyqtProperty(bool, notify=changed)
    def previewActive(self): return self._preview_enabled

    @pyqtProperty(str, notify=previewChanged)
    def previewUrl(self): return self._preview_url

    @pyqtProperty(str, notify=previewChanged)
    def previewStatus(self):
        if not self._preview_url:
            return '合成取景暂停' if self._preview_enabled else '合成取景已关闭'
        return 'HF-Sim · 合成取景 · 帧 ' + str(self._preview_frame_id)

    @pyqtProperty('QVariantMap', notify=changed)
    def capabilities(self): return dict(self._capabilities)

    @pyqtProperty('QVariantList', constant=True)
    def exposureSteps(self): return exposure_steps()

    @pyqtProperty('QVariantMap', notify=changed)
    def confirmedSettings(self): return dict(self._confirmed_settings)

    @pyqtProperty(bool, notify=changed)
    def settingsPending(self): return self._settings_active or bool(self._settings_queue)

    @pyqtProperty('QVariantMap', notify=changed)
    def settingsView(self):
        editable=(not self._closed and self._connection == 'ready' and not self._workflow_active
                  and not self._state_fault and self._capture != 'unknown' and not self._temporary_recovery and not self._maintenance_token)
        phase=('maintenance' if self._maintenance_token else 'recovery' if self._temporary_recovery else 'disconnected' if self._connection != 'ready'
               else 'capture_unknown' if self._capture == 'unknown'
               else 'capture_wait' if self._capture in _ACTIVE and self._settings_queue
               else 'applying' if self._settings_active else 'queued' if self._settings_queue else 'stable')
        temporary_state=('recovery' if self._temporary_recovery else 'restoring' if self._temporary and self._temporary.get('released')
                         else 'held' if self._temporary else 'idle')
        return dict(desired=dict(self._intent.desired),confirmed=dict(self._confirmed_settings),
                    desiredRevision=self._intent.revision,confirmedRevision=self._intent.confirmed_revision,
                    phase=phase,pending=self.settingsPending,canEdit=editable,isTemporary=bool(self._temporary),
                    temporaryState=temporary_state,recoveryRequired=self._temporary_recovery,
                    canRestoreTemporary=bool(self._temporary) and self._connection=='ready' and self._capture not in _ACTIVE | {'unknown','acknowledged'} and not self._workflow_active,
                    canDiscardTemporary=self._connection=='ready' and self._capture not in _ACTIVE | {'unknown'} and not self._workflow_active,
                    error=self._settings_error,
                    maintenanceReason=self._maintenance_reason,
                    reason='' if editable else self._maintenance_reason if self._maintenance_token else self._settings_error if self._temporary_recovery else '请先查询原拍摄请求，结果仍未知。' if self._capture == 'unknown'
                    else '拍摄工作流正在运行。' if self._workflow_active else '相机连接或拍摄状态尚未就绪。')

    @pyqtSlot(str,result=str)
    @pyqtSlot(str,bool,result=str)
    def beginMaintenance(self,reason,allow_unknown=False):
        if (self._closed or self._maintenance_token or self.busy or self.settingsPending
                or not allow_unknown and (self._temporary or self._temporary_recovery or self._capture=='unknown')):return ''
        self.localControlRequested.emit('maintenance')
        self._maintenance_token=str(uuid.uuid4());self._maintenance_reason=str(reason)[:120]
        self.changed.emit();return self._maintenance_token

    @pyqtSlot(str)
    def endMaintenance(self,token):
        if token and token==self._maintenance_token:
            self._maintenance_token='';self._maintenance_reason='';self.changed.emit()

    @pyqtProperty(bool, notify=changed)
    def workflowActive(self): return self._workflow_active

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
    def lastCaptureThumbnail(self): return self._saved_thumbnail_url

    @pyqtProperty(str, notify=changed)
    def reviewOriginalUrl(self): return self._review_original_url

    @pyqtProperty(int, notify=changed)
    def reviewWidth(self): return self._review_width

    @pyqtProperty(int, notify=changed)
    def reviewHeight(self): return self._review_height

    @pyqtProperty(str, notify=changed)
    def reviewTileUrl(self): return self._review_tile_url

    @pyqtProperty(int, notify=changed)
    def reviewTileX(self): return self._review_tile[0]

    @pyqtProperty(int, notify=changed)
    def reviewTileY(self): return self._review_tile[1]

    @pyqtProperty(int, notify=changed)
    def reviewTileWidth(self): return self._review_tile[2]

    @pyqtProperty(int, notify=changed)
    def reviewTileHeight(self): return self._review_tile[3]

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

    def _dispatch_saved_thumbnail(self):
        if self._library_refresh_requested:return
        if self._library_pending or self._saved_thumbnail_pending or not self._saved_thumbnail_next or self._closed:return
        request_id=self._saved_thumbnail_next;self._saved_thumbnail_next=None
        self._saved_thumbnail_pending=True
        future=self._library_executor.submit(lambda:self._client.capture_thumbnail(request_id))
        def complete(done):
            try:raw,error=done.result(),None
            except Exception as exc:raw,error=None,exc
            if not self._closed:self._savedThumbnailFinished.emit(request_id,raw,error)
        future.add_done_callback(complete)

    @pyqtSlot(str,object,object)
    def _on_saved_thumbnail(self,request_id,raw,error):
        self._saved_thumbnail_pending=False
        if self._closed:return
        if request_id == self._saved_thumbnail_id:
            try:
                if error:raise ValueError('thumbnail unavailable')
                self._saved_thumbnail_url=self._review_data_url(raw)
            except (ValueError,TypeError):self._saved_thumbnail_url=''
        self.changed.emit()
        self._dispatch_library_refresh();self._dispatch_saved_thumbnail()

    @pyqtSlot()
    def refreshLibrary(self):
        self._library_refresh_requested=True
        self._dispatch_library_refresh()

    def _dispatch_library_refresh(self):
        if self._library_refresh_requested and self.canBrowseCaptures and not self._library_pending:
            self._library_refresh_requested=False
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
        self._review_original_url = self._review_tile_url = ''
        self._review_width, self._review_height = item['width'], item['height']
        self._review_tile_next = None
        self._export_message = ''
        self._library_message = 'Reading display conversion from the retained DNG...'
        def review():
            return {'request_id': request_id, 'png': self._client.capture_thumbnail(request_id),
                    'original': render_retained(self._client, request_id)}
        self._library_submit('thumbnail', review)

    @pyqtSlot(int, int, int, int)
    def loadReviewTile(self, x, y, width, height):
        if not self.canBrowseCaptures or not self._selected_capture:
            return
        region = (max(0, x), max(0, y), min(1024, max(1, width)), min(768, max(1, height)))
        self._review_tile_next = (self._selected_capture, region)
        self._dispatch_review_tile()

    def _dispatch_review_tile(self):
        if self._library_pending or self._review_tile_next is None or self._closed:
            return
        request_id, region = self._review_tile_next
        self._review_tile_next = None
        if request_id == self._selected_capture:
            self._library_submit('tile', lambda: render_retained(self._client, request_id, region=region))

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
                self._selected_capture = self._selected_info = self._review_url = ''
                self._review_original_url = self._review_tile_url = ''
                self._review_width = self._review_height = 0
                self._review_tile_next = None
                self.frames.clear('review'); self.frames.clear('tile')
                self._library_message = (f'Showing {len(self._library_items)} committed captures. '
                    'Unknown/failed requests remain available through original-ID query.')
            elif action == 'saved_thumbnail':
                if isinstance(result,dict) and result.get('request_id') == self._saved_thumbnail_id:
                    self._saved_thumbnail_url=self._review_data_url(result['png'])
            elif action == 'thumbnail':
                if not isinstance(result, dict) or result.get('request_id') != self._selected_capture:
                    raise ValueError('thumbnail identity changed')
                self._review_url = self._review_data_url(result.get('png'))
                original = result.get('original')
                if original is not None:
                    self._review_original_url = self.frames.publish('review', original['image'])
                    self._review_width, self._review_height = original['width'], original['height']
                self._library_message = 'Retained DNG display conversion - synthetic and uncalibrated; not live preview.'
            elif action == 'tile':
                if result['request_id'] == self._selected_capture:
                    self._review_tile_url = self.frames.publish('tile', result['image'])
                    self._review_tile = (result['x'], result['y'], result['tile_width'], result['tile_height'])
            elif action == 'export':
                if (not isinstance(result, dict) or result.get('request_id') != self._selected_capture
                        or result.get('filename') != self._selected_capture + '.dng'
                        or type(result.get('byte_count')) is not int or result['byte_count'] <= 0
                        or not isinstance(result.get('destination'), str)):
                    raise ValueError('invalid export confirmation')
                self._export_message = f"Exported {result['byte_count']} bytes to {result['destination']}"
        except (ValueError, TypeError, OverflowError):
            if action == 'saved_thumbnail':
                self._saved_thumbnail_url=''
            elif action == 'export':
                self._export_message = 'Export was not completed. The source is unchanged; choose a new local destination.'
            else:
                self._library_message = 'Retained captures could not be read or validated. Capture request state is unchanged.'
                self._review_url = ''
        self.changed.emit()
        self._dispatch_library_refresh()
        self._dispatch_saved_thumbnail()
        self._dispatch_review_tile()

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
        result = {'state': self._client.get_state(), 'capabilities': self._client.capabilities()}
        if self._request:
            result['record'] = self._client.query_capture(self._request)
        return result

    def _read_state(self):
        self._renew()
        return {'state': self._client.get_state()}

    def _query(self, request_id):
        # Query remains safe after lease/device loss and never submits a capture.
        return {'record': self._client.query_capture(request_id),'queried_request_id':request_id}

    def _load_temporary_journal(self):
        if self._temporary_path is None: return
        try:
            path=self._temporary_path
            if path.is_symlink(): raise ValueError('临时参数日志不能是链接。')
            with path.open('rb') as handle: raw=handle.read(16385)
            if len(raw)>16384: raise ValueError('临时参数日志过大。')
            data=json.loads(raw.decode('utf-8'))
            if (not isinstance(data,dict) or set(data) != {'version','backend','baseline','expected','source','token','released'}
                    or type(data['version']) is not int or data['version'] != 1 or data['backend'] != self._backend
                    or type(data['released']) is not bool or not isinstance(data['source'],str) or len(data['source'])>96
                    or not isinstance(data['token'],str) or str(uuid.UUID(data['token'])) != data['token']):
                raise ValueError('临时参数日志格式或相机后端不匹配。')
            validate_patch(data['baseline'],full=True);validate_patch(data['expected'],full=True)
            self._temporary=data
            self._temporary['released']=True
            self._temporary_recovery=True
            self._settings_error='上次临时参数尚未恢复。连接并核实原拍摄后，可恢复原参数或保留当前值。'
        except FileNotFoundError: pass
        except (OSError,ValueError,TypeError,KeyError,UnicodeError):
            self._temporary_recovery=True
            self._settings_error='临时参数恢复日志无法校验；核实拍摄后可选择保留当前参数。'

    def _write_temporary_journal(self, document):
        if not document or self._temporary_path is None: return
        path=self._temporary_path
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name('.temporary-settings-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8') as handle:
                json.dump(document,handle,ensure_ascii=False,separators=(',',':'))
                handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_temporary(self):
        self._temporary=None
        self._temporary_recovery=False
        if self._temporary_path is not None:
            try: self._temporary_path.unlink(missing_ok=True)
            except OSError: self._settings_error='参数已恢复，恢复日志清理失败；重启后会再次核对。'

    def _finish_temporary_if_restored(self):
        if (self._temporary and self._temporary.get('released') and not self._settings_active
                and self._confirmed_settings == self._temporary['baseline'] and not self._settings_queue):
            self._clear_temporary()

    def _sync_intent(self):
        self._exposure=self._intent.desired['requested_exposure_us']
        self._gain=self._intent.desired['requested_analogue_gain']
        self._mode=self._intent.desired['sensor_mode']
        self._settings_queue=self._intent.difference(self._settings_sent if self._settings_active else None)

    def _schedule_settings(self):
        if not self._settings_queue or self._settings_active: return
        now=time.monotonic()
        if self._settings_first_edit is None: self._settings_first_edit=now
        remaining=max(0,100-round((now-self._settings_first_edit)*1000))
        self._settings_timer.start(min(40,remaining))

    def _after_parameter_edit(self, before, origin):
        if self._temporary:
            if not self._temporary.get('released'):
                for key,value in self._intent.desired.items():
                    if before.get(key) != value and key not in self._temporary.get('covered',set()):
                        self._temporary['baseline'][key]=value
                self._temporary['expected']=dict(self._intent.desired)
            else:
                self._temporary['baseline']=dict(self._intent.desired)
        self._sync_intent()
        if self._settings_queue and self._performance is not None:
            if self._parameter_action is None:
                self._parameter_action={'id':self._performance.beginAction('parameters',str(origin)[:80]),'visible':False,'settled':False}
            self._parameter_action['settled']=False
            self._performance.stage(self._parameter_action['id'],'desired:'+str(self._intent.revision))
        self._settings_error=''
        self._status=('参数待确认；当前拍摄不会被后续修改改变' if self._capture in _ACTIVE
                      else '正在合并参数，确认后可拍摄' if self._settings_queue or self._settings_active else '参数已由相机确认')
        self._schedule_settings()
        self._finish_temporary_if_restored()
        self.changed.emit()
        return self._intent.revision

    @pyqtSlot('QVariantMap', result=int)
    @pyqtSlot('QVariantMap', str, result=int)
    def setSettings(self, patch, origin='ui'):
        if not str(origin).startswith('remote'):self.localControlRequested.emit(str(origin)[:80])
        if not self.settingsView['canEdit']:
            self._settings_error=self.settingsView['reason'];self.changed.emit();return -1
        if self._temporary and str(origin).startswith('profile'):
            self._settings_error='请先松开临时参数 Fn，再载入工作台。';self.changed.emit();return -1
        try:
            before=dict(self._intent.desired)
            self._intent.edit(dict(patch))
        except (ValueError,TypeError) as error:
            self._settings_error=str(error);self.changed.emit();return -1
        return self._after_parameter_edit(before,origin)

    @pyqtSlot(str, int, result=int)
    @pyqtSlot(str, int, str, result=int)
    def nudgeSettings(self, key, ticks, origin='dial'):
        if not str(origin).startswith('remote'):self.localControlRequested.emit(str(origin)[:80])
        if not self.settingsView['canEdit']:
            self._settings_error=self.settingsView['reason'];self.changed.emit();return -1
        before=dict(self._intent.desired)
        try: self._intent.nudge(key,ticks)
        except ValueError as error:
            self._settings_error=str(error);self.changed.emit();return -1
        return self._after_parameter_edit(before,origin)

    def _queue_setting(self,name,value): return self.setSettings({name:value},'legacy-ui')

    def _dispatch_settings(self):
        if (self._closed or self._settings_active or not self._settings_queue or self._workflow_active
                or self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'} or self._temporary_recovery):
            return
        self._settings_timer.stop()
        self._settings_first_edit=None
        batch = dict(self._intent.desired)
        self._settings_queue = {}
        self._settings_active = True
        self._settings_sent=dict(batch)
        revision=self._intent.revision
        self._settings_sent_revision=revision
        journal=None
        if self._temporary:
            journal={key:value for key,value in self._temporary.items() if key != 'covered'}
            journal=json.loads(json.dumps(journal))
        def configure():
            self._renew()
            self._write_temporary_journal(journal)
            accepted = self._client.configure(self._lease['token'], self._lease['epoch'], batch)
            return {'settings_batch': batch, 'settings_ack': accepted, 'state': self._client.get_state(),'settings_revision':revision}
        self._submit('configure', configure)

    @pyqtSlot()
    def flushSettings(self):
        self._settings_timer.stop();self._dispatch_settings()

    @pyqtSlot()
    def noteSettingsVisible(self):
        if self._parameter_action and self._performance is not None:
            self._performance.firstVisible(self._parameter_action['id'])
            self._parameter_action['visible']=True
            if self._parameter_action['settled']:
                self._performance.finish(self._parameter_action['id'],'confirmed')
                self._parameter_action=None

    @pyqtSlot('QVariantMap', str, result=str)
    def beginTemporary(self,patch,source):
        self.localControlRequested.emit('temporary:'+str(source)[:64])
        if self._temporary or not self.settingsView['canEdit'] or self._temporary_path is None:
            self._settings_error='请先连接相机、核实拍摄结果或松开已有临时 Fn。';self.changed.emit();return ''
        try: patch=validate_patch(dict(patch))
        except (ValueError,TypeError): return ''
        token=str(uuid.uuid4())
        baseline=dict(self._intent.desired)
        self._temporary=dict(version=1,backend=self._backend,baseline=baseline,
                             expected=dict(baseline,**patch),source=str(source)[:96],token=token,released=False,covered=set(patch))
        self._intent.edit(patch);self._sync_intent();self._schedule_settings();self.changed.emit()
        return token

    @pyqtSlot(str)
    def endTemporary(self,token):
        if not self._temporary or self._temporary['token'] != token or self._temporary.get('released'): return
        self._temporary['released']=True
        self._intent.edit(self._temporary['baseline']);self._sync_intent()
        if self._connection != 'ready' or self._capture == 'unknown':
            self._temporary_recovery=True
            self._settings_error='请先核实原拍摄请求，再恢复原参数；也可以明确保留当前参数。'
        self._schedule_settings();self._finish_temporary_if_restored();self.changed.emit()

    @pyqtSlot(result=bool)
    def reconcileTemporary(self):
        if (not self._temporary or self._connection != 'ready' or self._capture in _ACTIVE | {'unknown','acknowledged'} or self._workflow_active): return False
        self._temporary_recovery=False;self._temporary['released']=True
        self._intent.adopt(self._confirmed_settings);self._intent.edit(self._temporary['baseline'])
        self._sync_intent();self._settings_error='';self._schedule_settings();self._finish_temporary_if_restored();self.changed.emit();return True

    @pyqtSlot(result=bool)
    def discardTemporaryRecovery(self):
        if self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'} or self._workflow_active: return False
        if self._temporary_path is not None:
            try:self._temporary_path.unlink(missing_ok=True)
            except OSError:
                self._settings_error='无法保存“保留当前参数”的选择；恢复日志未清理。';self.changed.emit();return False
        self._clear_temporary();self._intent.adopt(self._confirmed_settings);self._sync_intent();self._settings_error='';self.changed.emit();return True

    @pyqtSlot(int)
    def setExposureUs(self, value):
        if not 100 <= value <= 30000000:
            return
        self._queue_setting('requested_exposure_us', value)

    @pyqtSlot(float)
    def setAnalogueGain(self, value):
        if not math.isfinite(value) or not 1 <= value <= 16:
            return
        self._queue_setting('requested_analogue_gain', round(value, 2))

    @pyqtSlot(str)
    def setSensorMode(self, value):
        if value not in {'sim-small', 'sim-full'}:
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
        self._submit_capture(None,'local')

    @pyqtSlot(str,result=bool)
    @pyqtSlot(str,str,result=bool)
    def captureWithId(self,request_id,origin='remote'):
        if not self._canonical_request_id(request_id):return False
        if request_id == self._request:
            self.queryLast()
            return True
        return self._submit_capture(request_id,origin)

    def _submit_capture(self,request_id=None,origin='local'):
        if not str(origin).startswith('remote'):self.localControlRequested.emit(str(origin)[:80])
        if not self.canCapture:
            if self.settingsPending:
                self.flushSettings()
                self._status='参数确认中，请确认后再按快门；不会延后自动拍摄'
                self.changed.emit()
            return False
        # Persist/display identity before crossing the submission boundary.
        supplied=request_id is not None
        request_id = request_id or str(uuid.uuid4())
        try:
            self._save_request_state(request_id)
        except OSError:
            self._error = 'The request ID could not be saved. Capture was not submitted.'
            self._status = 'Storage unavailable'
            self.changed.emit()
            return False
        self._request = request_id
        self._capture = 'submitting'
        self._cancel_requested = False
        self._invalidate_preview()
        self._path = ''
        self._error = ''
        self._status = 'Submitting capture · keep this request ID'
        frozen=dict(self._confirmed_settings)
        settings = {'sensor_mode': frozen.get('sensor_mode',self._mode), 'requested_exposure_us': frozen.get('requested_exposure_us',self._exposure),
                    'requested_analogue_gain': frozen.get('requested_analogue_gain',self._gain), 'output_format': 'dng',
                    'simulation_scene': 'scene'}
        self.changed.emit()
        def capture():
            self._renew()
            if supplied:
                previous=self._client.query_capture(request_id)
                if previous is not None:return {'record':previous}
            # Full capture settings are carried by the durable request itself.
            request = dict(settings, request_id=request_id, token=self._lease['token'],
                           control_epoch=self._lease['epoch'])
            return {'record': self._client.capture(request)}
        self._submit('capture', capture)
        return True

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
        if self._closed or self._workflow_active or self._pending or self._connection == 'connecting' or self._capture in _ACTIVE:
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
                result = {'state': client.get_state(), 'capabilities': client.capabilities()}
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
        if self._closed or self._workflow_active or self._pending or self._connection != 'ready':
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

    def runExclusiveWorkflow(self, function, *, reconcile_request_id=None):
        """Hand the same authority to one workflow, then reacquire UI control.

        The callable must wait for its runner to stop and release its lease.
        No camera RPC from the UI poll/configure/shutter path can interleave.
        """
        reconcile = (isinstance(reconcile_request_id, str)
                     and self._canonical_request_id(reconcile_request_id)
                     and reconcile_request_id == self._request and self._capture == 'unknown'
                     and not self._closed and not self._state_fault and not self._workflow_active
                     and self._connection == 'ready' and not self._settings_active and not self._settings_queue)
        if self._maintenance_token or self._temporary or self._temporary_recovery or (not self.canCapture and not reconcile) or self._pending or not callable(function):
            return False
        self._workflow_active = True
        self._invalidate_preview()
        self._status = '拍摄流程运行中'
        self.changed.emit()
        def execute():
            lease = self._lease
            self._client.release(lease['token'], lease['epoch'])
            self._lease = None
            value, failure = None, None
            try:
                value = function(self._client)
            except Exception as exc:
                failure = exc
            # Keep capture evidence even if control cannot be reacquired. Losing
            # this result would lose the UUID that must hold the next shutter.
            response = {'workflow_result': value, 'workflow_error': failure}
            try:
                self._accept_lease(self._client.acquire('on-device-ui'))
                response['state'] = self._client.get_state()
                if reconcile:
                    response['reconciled_request_id'] = reconcile_request_id
                    response['reconciled_record'] = self._client.query_capture(reconcile_request_id)
            except Exception as exc:
                response['workflow_control_error'] = exc
            return response
        self._submit('workflow', execute)
        return True

    @staticmethod
    def _positive_finite(value):
        try:
            return type(value) in (int, float) and value > 0 and math.isfinite(value)
        except (OverflowError, ValueError):
            return False

    def _adopt_workflow_unknown(self, workflow):
        if not isinstance(workflow, dict): return
        unresolved = [step for step in workflow.get('steps', [])
                      if step.get('status') in {'unknown','submitting','accepted','capturing'}]
        if not unresolved: return
        identity = unresolved[-1].get('request_id')
        self._capture = 'unknown'
        self._path = ''
        if not self._canonical_request_id(identity):
            self._state_fault = True
            self._error = '流程存在未确认拍摄，原 UUID 无法验证；请检查流程记录'
            return
        self._request = identity
        try:
            self._save_request_state(identity)
        except OSError:
            self._state_fault = True
            self._error = '未确认流程 UUID 无法保存，请保留流程记录并检查存储'

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
            if self._saved_thumbnail_id != self._request:
                self._saved_thumbnail_id=self._request
                self._saved_thumbnail_url=''
                self._saved_thumbnail_next=self._request
                self._dispatch_saved_thumbnail()
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
        if action == 'workflow':
            self._workflow_active = False
            if isinstance(result, dict):
                self._adopt_workflow_unknown(result.get('workflow_result'))
                error = error or result.get('workflow_control_error')
            self.workflowFinished.emit(result.get('workflow_result') if isinstance(result, dict) else None,
                error or (result.get('workflow_error') if isinstance(result, dict) else None))
        if isinstance(result, dict) and 'capabilities' in result:
            self._capabilities = capability_model(result['capabilities'])
        if action == 'configure':
            self._settings_active = False
            revision=(result.get('settings_revision',self._settings_sent_revision) if isinstance(result,dict) else self._settings_sent_revision)
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
                self._settings_timer.stop()
                self._connection = 'disconnected'
                self._settings_error='参数未确认，重新连接后先读取实际参数。'
                if self._temporary:
                    self._temporary['released']=True;self._temporary_recovery=True
                self._status = 'Settings not confirmed - capture held'
                self._error = 'Reconnect to read the authoritative settings. The last edit may or may not have been applied; it will not be retried automatically.'
                self._invalidate_preview()
                self.settingsFailed.emit(revision,self._settings_error)
                if self._parameter_action and self._performance is not None:
                    self._performance.finish(self._parameter_action['id'],'not-confirmed');self._parameter_action=None
                self.changed.emit()
                return
            self._confirmed_settings = dict(requested_exposure_us=exposure,
                requested_analogue_gain=gain, sensor_mode=mode)
            self._intent.acknowledge(self._confirmed_settings,revision)
            self._sync_intent()
            if self._settings_queue:
                self._schedule_settings()
            else:
                self._status = 'Settings confirmed by local camera'
                self._error = ''
            self._finish_temporary_if_restored()
            if self._parameter_action and self._performance is not None:
                self._performance.stage(self._parameter_action['id'],'ack:'+str(revision))
                if not self._settings_queue:
                    self._parameter_action['settled']=True
                    if self._parameter_action['visible']:
                        self._performance.finish(self._parameter_action['id'],'confirmed');self._parameter_action=None
            self.settingsSettled.emit(revision,dict(self._confirmed_settings))
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
            if self._temporary and self._connection != 'ready':
                self._temporary['released']=True;self._temporary_recovery=True
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
            self._confirmed_settings = dict(requested_exposure_us=exposure,
                requested_analogue_gain=gain, sensor_mode=mode)
            if action in {'connect', 'reconnect'}:
                self._status = 'Ready - local synthetic capture' if self._connection == 'ready' else 'Simulated device disconnected'
                self._error = ''
                self._exposure, self._gain, self._mode = exposure, gain, mode
                self._intent.adopt(self._confirmed_settings)
                self._settings_queue.clear()
                self._finish_temporary_if_restored()
                self._timer.setInterval(max(1, min(self._poll_ms, int(self._lease_seconds * 1000 / 4))))
            elif self._connection == 'disconnected':
                self._status = 'Simulated device disconnected - reconnect to continue'
            elif not self._settings_active and not self._settings_queue:
                self._exposure, self._gain, self._mode = exposure, gain, mode
                self._intent.adopt(self._confirmed_settings)
            if action == 'workflow':
                workflow = result.get('workflow_result') or {}
                unresolved = [step for step in workflow.get('steps', [])
                              if step.get('status') in {'unknown', 'submitting', 'accepted', 'capturing'}]
                active = unresolved[-1].get('request_id') if unresolved else None
                if active or state.get('camera_state') != 'idle':
                    self._capture = 'unknown'
                    self._status = '流程结束时仍有未确认拍摄，请查询原请求'
                else:
                    self._status = '拍摄流程已停止，本机控制已恢复'
        if action == 'query' and result.get('queried_request_id',self._request) != self._request:
            self.changed.emit();return
        if 'record' in result:
            self._apply_record(result['record'], query=action in {'query', 'reconnect'})
        if ('reconciled_record' in result and self._capture == 'unknown'
                and result.get('reconciled_request_id') == self._request):
            self._apply_record(result['reconciled_record'], query=True)
        if self._state_fault:
            self._unknown_response('Saved UI request state is invalid - capture held',
                                   'The original UI file is unchanged. Restore it, or explicitly preserve its raw state before allowing a new UUID shot. The previous request ID is unavailable.')
        elif self._capture == 'unknown' and not self._request and self._preserved_state_backups:
            self._unknown_response('Previous request ID is unavailable - outcome unconfirmed',
                                   'Raw UI state is preserved. Explicit acknowledgment is required before a new UUID shot.')
        if self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'}:
            self._invalidate_preview()
        self._finish_temporary_if_restored()
        self._schedule_settings()
        self._dispatch_library_refresh()
        self._dispatch_saved_thumbnail()
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
        self.frames.clear('live')
        self.previewChanged.emit()

    def _preview_tick(self):
        if (self._closed or not self._preview_enabled or self._preview_pending
                or self._workflow_active or self._connection != 'ready' or self._capture in _ACTIVE | {'unknown'}):
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
            self._preview_url = self.frames.publish('live', packet['image'])
            self._preview_frame_id = packet['frame_id']
            self._preview_produced_ns = packet['produced_ns']
            if self._performance is not None:
                self._performance.previewPresented(packet['produced_ns'])
            self.previewChanged.emit()

    @pyqtSlot()
    def shutdown(self):
        if self._closed:
            return
        self.shutdownStarted.emit()
        self._closed = True
        self._timer.stop()
        self._settings_timer.stop()
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

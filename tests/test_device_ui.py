"""Executable Qt Quick interaction tests using local camera authorities only."""
from __future__ import annotations

from contextlib import closing
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
if os.name == 'nt':
    os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'))
try:
    from PyQt5.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt, QTimer, QUrl
    from PyQt5.QtGui import QGuiApplication, QImage
    from PyQt5.QtQml import QQmlApplicationEngine
    from PyQt5.QtQuick import QQuickItem, QQuickWindow
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('PyQt5/Qt Quick optional UI runtime is not installed')

from camera.camerad.service import CameraError, CameraService
from camera.dng.inspect import inspect_dng
from camera.pipeline.preview import PreviewStream

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'ui' / 'on-device'
spec = importlib.util.spec_from_file_location('hf_device_controller', UI / 'controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CameraController = module.CameraController
make_client_factory = module.make_client_factory
from image_pipeline import FrameImageProvider, SyntheticViewfinder


def native_command():
    configured = os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
    if configured:
        return [configured]
    for path in (ROOT / 'build/native/Release/hf-camerad.exe', ROOT / 'build/native/hf-camerad',
                 ROOT / 'build/native/hf-camerad.exe'):
        if path.is_file():
            return [str(path)]
    return None


class QtDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.controller = None
        self.engine = None
        self.window = None
        self._factory_gate = None
        self._qml_loaded_at = None
        self._factory_released_at = None

    def tearDown(self):
        if self._factory_gate is not None:
            self._factory_gate.set()
        if self.controller is not None:
            if getattr(self, 'apps', None) is not None:
                self.apps.close()
            self.controller.shutdown()
        if self.engine is not None:
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window = self.engine = self.controller = None
        self.temporary.cleanup()

    def wait(self, condition, *, timeout=8000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if condition():
                return
            QTest.qWait(20)
        self.fail('Qt condition timed out: ' + (self.controller.statusText if self.controller else 'no controller'))

    def launch(self, *, backend='reference', factory=None, directory=None, expected_connection='ready',
               defer_factory_until_window_ready=False):
        directory = directory or self.root / 'camera'
        factory = factory or make_client_factory(backend, directory, native_command() if backend == 'native' else None)
        if defer_factory_until_window_ready:
            self._factory_gate = threading.Event()
            original_factory = factory
            def window_ready_factory():
                if not self._factory_gate.wait(5):
                    raise RuntimeError('test window did not finish startup before authority acquisition')
                return original_factory()
            factory = window_ready_factory
        self.controller = CameraController(factory, backend=backend,
                                           state_path=directory / 'device-ui.json', poll_ms=60)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider('daosframes', FrameImageProvider(self.controller.frames))
        self.engine.rootContext().setContextProperty('camera', self.controller)
        import sys
        if str(UI) not in sys.path:
            sys.path.insert(0, str(UI))
        from shell_model import ShellController
        from apps_controller import CameraAppsController
        self.shell = ShellController(directory / 'shell-preferences.json')
        self.apps = CameraAppsController(self.controller, directory)
        self.engine.rootContext().setContextProperty('cameraShell', self.shell)
        self.engine.rootContext().setContextProperty('cameraApps', self.apps)
        self.engine.rootContext().setContextProperty('retailTheme', json.loads((UI / 'retail-theme.json').read_text(encoding='utf-8')))
        self.engine.load(QUrl.fromLocalFile(str(UI / 'Console.qml')))
        self.assertEqual(1, len(self.engine.rootObjects()), 'QML did not produce a window')
        self.window = self.engine.rootObjects()[0]
        self.window.requestActivate()
        self._qml_loaded_at = time.monotonic()
        if self._factory_gate is not None:
            # Only the short-lease export test opts in. Nested event processing
            # during cold QQml loading must not spend its lease before the
            # running-event-loop scenario under test has begun.
            def release_factory():
                self._factory_released_at = time.monotonic()
                self._factory_gate.set()
            QTimer.singleShot(80, release_factory)
        self.wait(lambda: self.controller.connectionState != 'connecting')
        self.assertEqual(expected_connection, self.controller.connectionState, self.controller.errorText)
        QTest.qWait(80)
        return self.controller

    def item(self, name):
        item = self.window.findChild(QQuickItem, name)
        if item is None:
            def visual(parent):
                for child in parent.childItems():
                    if child.objectName() == name:
                        return child
                    found = visual(child)
                    if found is not None:
                        return found
                return None
            item = visual(self.window.contentItem())
        self.assertIsNotNone(item, name)
        return item

    def click(self, name, *, fraction=.5):
        item = self.item(name)
        point = item.mapToScene(QPointF(item.width() * fraction, item.height() / 2))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
        QTest.qWait(30)

    def screenshot(self, path):
        capture = self.window.contentItem().grabToImage()
        self.assertIsNotNone(capture)
        ready = []
        capture.ready.connect(lambda: ready.append(True))
        self.wait(lambda: bool(ready))
        self.assertFalse(capture.image().isNull())
        self.assertTrue(capture.image().save(str(path)))
        return capture.image()

    def evidence(self, name):
        destination = os.environ.get('HF_DEVICE_UI_EVIDENCE_DIR')
        if destination:
            folder = Path(destination).resolve()
            folder.mkdir(parents=True, exist_ok=True)
            self.screenshot(folder / name)

    def test_reference_controls_preview_capture_query_and_retained_id(self):
        c = self.launch()
        exposure = self.item('exposureInput')
        exposure.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_Up)
        self.wait(lambda: c.exposureUs == 10100)
        gain = self.item('gainSlider')
        gain.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_Right)
        self.wait(lambda: c.analogueGain > 1)
        mode = self.item('modeCombo')
        mode.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_Down)
        self.wait(lambda: c.sensorMode == 'sim-full')
        QTest.keyClick(self.window, Qt.Key_Up)
        self.wait(lambda: c.sensorMode == 'sim-small')
        self.click('previewButton')
        self.wait(lambda: c.previewUrl.startswith('image://daosframes/live/'))
        # Displayed settings may precede the authority acknowledgment. This
        # fixture must enter the ready state before its single shutter press.
        self.wait(lambda: c.canCapture and self.item('shutterButton').isEnabled())
        self.click('shutterButton')
        request_id = c.lastRequestId
        self.assertTrue(request_id)
        self.assertEqual(request_id, json.loads((self.root / 'camera/device-ui.json').read_text())['request_id'])
        self.wait(lambda: c.captureState == 'complete')
        self.assertTrue(Path(c.dngPath).is_file())
        self.assertEqual(request_id, inspect_dng(c.dngPath)['request_id'])
        self.click('queryButton')
        self.wait(lambda: c._pending == 0)
        self.assertEqual(request_id, c.lastRequestId)
        self.assertEqual(1, len(list((self.root / 'camera/photos').glob('*.dng'))))
        c.shutdown()
        restored = CameraController(make_client_factory('reference', self.root / 'camera'), backend='reference',
                                    state_path=self.root / 'camera/device-ui.json')
        try:
            self.wait(lambda: restored.connectionState == 'ready' and restored.captureState == 'complete')
            self.assertEqual(request_id, restored.lastRequestId)
            self.assertEqual(1, len(list((self.root / 'camera/photos').glob('*.dng'))))
        finally:
            restored.shutdown()

    def test_unknown_submission_disables_shutter_and_queries_original_without_retry(self):
        class LostReply:
            def __init__(self, directory):
                self.service = CameraService(directory)
                self.submissions = 0
            def __getattr__(self, name): return getattr(self.service, name)
            def capture(self, request):
                self.submissions += 1
                self.service.capture(request)
                raise CameraError('outcome_unknown', 'simulated lost response')
        camera = LostReply(self.root / 'camera')
        c = self.launch(factory=lambda: camera)
        self.click('previewButton')
        self.wait(lambda: bool(c.previewUrl))
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'unknown')
        request_id = c.lastRequestId
        self.assertFalse(c.canCapture)
        self.assertFalse(self.item('shutterButton').property('enabled'))
        self.assertTrue(self.window.property('recordDetailsVisible'))
        self.assertFalse(self.item('captureDetailsButton').property('enabled'))
        self.assertEqual('', c.previewUrl)
        self.click('shutterButton')
        self.assertEqual(1, camera.submissions)
        self.wait(lambda: camera.query_capture(request_id)['status'] == 'complete')
        self.click('queryButton')
        self.wait(lambda: c.captureState == 'complete')
        self.assertEqual(request_id, c.lastRequestId)
        self.assertEqual(1, camera.submissions)
        self.click('reconnectButton')
        self.wait(lambda: c.connectionState == 'ready')
        self.assertTrue(c.canCapture)
        self.assertEqual(1, camera.submissions)

    def test_late_preview_cannot_return_after_disconnect(self):
        c = self.launch()
        entered, release = threading.Event(), threading.Event()
        base = SyntheticViewfinder(width=16, height=16, fps=3)
        class SlowPreview:
            def start(self): return base.start()
            def stop(self): return base.stop()
            def snapshot(self, settings):
                entered.set()
                release.wait(timeout=5)
                return base.snapshot(settings)
        c._preview = SlowPreview()
        self.click('previewButton')
        self.wait(entered.is_set)
        c._connection = 'disconnected'
        c._invalidate_preview()
        c.changed.emit()
        release.set()
        self.wait(lambda: not c._preview_pending)
        self.assertEqual('', c.previewUrl)

    def test_malformed_completion_fields_are_held_without_opening_metadata_paths(self):
        service = CameraService(self.root / 'camera')
        class Inconsistent:
            changes = {}
            def __getattr__(self, name): return getattr(service, name)
            def capture(self, request):
                value = {'request_id': request['request_id'], 'capture_id': request['request_id'],
                         'frame_id': request['request_id'], 'status': 'complete',
                         'actual_exposure_us': 10000, 'actual_analogue_gain': 1.0,
                         'file_path': str(self.root / 'untrusted-path.dng')}
                value.update(self.changes)
                return value
        camera = Inconsistent()
        camera.root = self.root
        c = self.launch(factory=lambda: camera)
        cases = [{'file_path': ''}, {'file_path': None}, {'file_path': {'not': 'text'}},
                 {'capture_id': str(uuid.uuid4())}, {'frame_id': None},
                 {'actual_exposure_us': False}, {'actual_exposure_us': 0},
                 {'actual_analogue_gain': float('nan')}, {'actual_analogue_gain': 0},
                 {'request_id': str(uuid.uuid4())}, {'status': []}]
        for changes in cases:
            with self.subTest(fields=tuple(changes)):
                if c.captureState == 'unknown':
                    self.wait(lambda: c.canAcknowledgeUnknown)
                    self.click('acknowledgeUnknownButton')
                camera.changes = changes
                self.click('shutterButton')
                self.wait(lambda: c._pending == 0)
                self.assertEqual('unknown', c.captureState)
                self.assertFalse(c.canCapture)
                self.assertEqual('', c.dngPath)
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        camera.changes = {'status': 'failed', 'error_code': []}
        self.click('shutterButton')
        self.wait(lambda: c._pending == 0)
        self.assertEqual('failed', c.captureState)
        self.assertEqual('', c.dngPath)
        self.assertFalse((self.root / 'untrusted-path.dng').exists())
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM captures').fetchone()[0])

    def test_malformed_authority_state_cannot_overflow_qt_properties(self):
        service = CameraService(self.root / 'camera')
        class Inconsistent:
            reply = []
            def __getattr__(self, name): return getattr(service, name)
            def get_state(self): return self.reply
        camera = Inconsistent()
        c = self.launch(factory=lambda: camera, expected_connection='disconnected')
        for changes in ({'requested_exposure_us': 2 ** 80}, {'sensor_mode': []},
                        {'requested_analogue_gain': float('inf')}):
            reply = service.get_state()
            reply['settings'].update(changes)
            camera.reply = reply
            self.wait(lambda: c._pending == 0)
            c.reconnect()
            self.wait(lambda: c._pending == 0)
            self.assertEqual('disconnected', c.connectionState)
            self.assertFalse(c.canCapture)
            self.assertEqual(10000, c.exposureUs)
            self.assertEqual(1.0, c.analogueGain)

    def test_invalid_lease_metadata_cannot_reach_qt_timer(self):
        service = CameraService(self.root / 'camera')
        class Inconsistent:
            def __getattr__(self, name): return getattr(service, name)
            def acquire(self, owner):
                lease = service.acquire(owner)
                lease['lease_seconds'] = 'NaN'
                return lease
        c = self.launch(factory=lambda: Inconsistent(), expected_connection='disconnected')
        self.assertFalse(c.canCapture)
        self.assertEqual(30.0, c._lease_seconds)

    def test_pending_saved_uuid_without_journal_record_is_not_replayed(self):
        root = self.root / 'camera'
        root.mkdir()
        original_id = str(uuid.uuid4())
        (root / 'device-ui.json').write_text(json.dumps({'schema_version': 1, 'request_id': original_id}))
        c = self.launch()
        self.assertEqual(original_id, c.lastRequestId)
        self.assertEqual('unknown', c.captureState)
        self.assertFalse(c.canCapture)
        with closing(sqlite3.connect(root / 'journal.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM captures').fetchone()[0])
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        self.assertIsNone(c._client.query_capture(original_id))
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        self.assertNotEqual(original_id, c.lastRequestId)
        self.assertIsNone(c._client.query_capture(original_id))

    def test_atomic_state_replace_failure_retains_previous_uuid_and_file(self):
        c = self.launch()
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        original_id = c.lastRequestId
        state_path = self.root / 'camera/device-ui.json'
        before = state_path.read_bytes()
        with patch.object(module.os, 'replace', side_effect=PermissionError('injected replace failure')):
            self.click('shutterButton')
        self.assertEqual(original_id, c.lastRequestId)
        self.assertEqual(before, state_path.read_bytes())
        self.assertIn('not submitted', c.errorText)
        self.assertEqual([], list(state_path.parent.glob('.device-ui-*.tmp')))
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM captures').fetchone()[0])

    def test_corrupt_state_backup_failure_or_oversize_keeps_capture_held(self):
        root = self.root / 'camera'
        root.mkdir()
        state_path = root / 'device-ui.json'
        raw = b'broken UI state'
        state_path.write_bytes(raw)
        c = self.launch()
        self.assertFalse(c.canCapture)
        self.wait(lambda: c.canAcknowledgeUnknown)
        original_open = Path.open
        def cannot_backup(path, *args, **kwargs):
            if path.name.startswith('device-ui-unknown-'):
                raise PermissionError('injected backup failure')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', cannot_backup):
            self.click('acknowledgeUnknownButton')
        self.assertFalse(c.canCapture)
        self.assertEqual(raw, state_path.read_bytes())
        self.assertEqual([], list(root.glob('*.bak')))
        raw = b'x' * 65537
        state_path.write_bytes(raw)
        self.click('acknowledgeUnknownButton')
        self.assertFalse(c.canCapture)
        self.assertEqual(raw, state_path.read_bytes())
        with closing(sqlite3.connect(root / 'journal.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM captures').fetchone()[0])

    def test_busy_reference_shutdown_closes_authority_after_capture_finishes(self):
        service = CameraService(self.root / 'camera')
        gate = threading.Event()
        original_run = service._run
        service._run = lambda request_id, record: (gate.wait(5), original_run(request_id, record))
        try:
            c = self.launch(factory=lambda: service)
            self.click('shutterButton')
            self.wait(lambda: c.captureState == 'accepted')
            started = time.monotonic()
            with patch.object(module, '_SHUTDOWN_GRACE_SECONDS', .1):
                c.shutdown()
            self.assertLess(time.monotonic() - started, 1)
            gate.set()
            self.wait(lambda: not c._deferred_close_thread.is_alive())
            reopened = CameraService(self.root / 'camera')
            reopened.close()
        finally:
            gate.set()
            self.wait(lambda: service.active is None)
            try:
                service.close()
            except sqlite3.ProgrammingError:
                pass

    def test_camera_action_queue_stays_bounded_under_repeated_clicks(self):
        c = self.launch()
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete' and c._pending == 0)
        entered, release = threading.Event(), threading.Event()
        original_query = c._client.query_capture
        calls = []
        def blocked_query(request_id):
            calls.append(request_id)
            entered.set()
            release.wait(3)
            return original_query(request_id)
        c._client.query_capture = blocked_query
        try:
            c.queryLast()
            self.wait(entered.is_set)
            for _ in range(100):
                c.queryLast()
                c.reconnect()
            self.assertEqual(1, len(calls))
            self.assertEqual(1, c._pending)
            c.shutter()
            for _ in range(100):
                c.shutter()
                c.cancelCapture()
            self.assertLessEqual(c._pending, 3)
        finally:
            release.set()
        self.wait(lambda: c._pending == 0)

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_corrupt_state_ack_preserves_backup_and_original_record(self):
        c = self.launch(backend='native')
        original_capture = c._client.capture
        c._client.capture = lambda request: original_capture(dict(request, fault='delay'))
        self.click('shutterButton')
        self.wait(lambda: c.captureState in {'accepted', 'capturing'})
        old_id = c.lastRequestId
        c._client.abort()
        self.wait(lambda: c.captureState == 'unknown')
        c.shutdown()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.engine = self.window = None
        state_path = self.root / 'camera/device-ui.json'
        raw = b'{"schema_version":2,"request_id":' + old_id.encode('ascii') + b',"secret":"LOCAL-STATE-TEST-SECRET"'
        state_path.write_bytes(raw)
        c = self.launch(backend='native')
        self.assertEqual('', c.lastRequestId)
        self.assertEqual('unknown', c.captureState)
        self.assertFalse(c.canCapture)
        self.assertEqual(raw, state_path.read_bytes())
        self.assertNotIn('LOCAL-STATE-TEST-SECRET', c.errorText)
        old_record = c._client.query_capture(old_id)
        self.assertEqual('unknown', old_record['status'])
        self.window.resize(800, 600)
        QTest.qWait(60)
        self.evidence('corrupt-state-held-800.png')
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        self.assertTrue(c.canCapture)
        self.assertEqual('', c.lastRequestId, 'acknowledgment invented a prior request identity')
        saved = json.loads(state_path.read_text())
        self.assertIsNone(saved['request_id'])
        self.assertEqual([], saved['acknowledged_unknown_request_ids'])
        entry = saved['preserved_ui_state_backups'][0]
        backup = state_path.parent / entry['file']
        self.assertEqual(raw, backup.read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
        self.assertEqual(old_record, c._client.query_capture(old_id))
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM captures').fetchone()[0])
        # Restart after acknowledgment but before the new shutter must hold again.
        c.shutdown()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.engine = self.window = None
        c = self.launch(backend='native')
        self.assertEqual('unknown', c.captureState)
        self.assertFalse(c.canCapture)
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        self.assertNotEqual(old_id, c.lastRequestId)
        self.assertEqual(old_record, c._client.query_capture(old_id))
        self.assertEqual(raw, backup.read_bytes())
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual({old_id: 'unknown', c.lastRequestId: 'complete'},
                             dict(db.execute('SELECT request_id,status FROM captures')))

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_http_short_lease_and_stale_client_shutdown_handoff(self):
        from camera.camerad.native_client import NativeClient
        from camera.camerad.http_client import HttpCameraClient
        from camera.camerad.server import BoundedHTTPServer, handler_for
        native = NativeClient(self.root / 'shared-server', command=native_command(), lease_seconds=.6)
        server = thread = second = third = None
        try:
            handler = handler_for(native)
            handler.log_message = lambda *args: None
            server = BoundedHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = 'http://127.0.0.1:' + str(server.server_port)
            factory = make_client_factory('http', None, endpoint=endpoint)
            c = self.launch(backend='http', factory=factory, directory=self.root / 'ui-one')
            first_epoch = c._lease['epoch']
            QTest.qWait(1800)  # Three authority lease durations while idle.
            self.assertEqual('ready', c.connectionState)
            self.assertEqual(first_epoch, native.get_state()['control']['epoch'])
            second = CameraController(factory, backend='http', state_path=self.root / 'ui-two/device-ui.json', poll_ms=60)
            self.wait(lambda: second.connectionState != 'connecting')
            self.assertEqual('disconnected', second.connectionState)
            self.assertIn('held by another session', second.errorText)
            second.shutdown()
            self.assertEqual(first_epoch, native.get_state()['control']['epoch'])
            self.wait(lambda: c._pending == 0)
            c._timer.stop()  # Suspend this UI long enough for explicit handoff.
            QTest.qWait(850)
            self.assertIsNone(native.get_state()['control'])
            third = CameraController(factory, backend='http', state_path=self.root / 'ui-three/device-ui.json', poll_ms=60)
            self.wait(lambda: third.connectionState != 'connecting')
            self.assertEqual('ready', third.connectionState)
            next_epoch = third._lease['epoch']
            self.assertGreater(next_epoch, first_epoch)
            c.shutdown()  # Its old token must not release the new client's lease.
            self.assertIsNone(native.process.poll())
            observer = HttpCameraClient(endpoint)
            try:
                self.assertEqual(next_epoch, observer.get_state()['control']['epoch'])
                third.shutdown()
                self.assertIsNone(observer.get_state()['control'])
                self.assertTrue(observer.get_state()['connected'])
            finally:
                observer.close()
        finally:
            for controller in (self.controller, second, third):
                if controller is not None:
                    controller.shutdown()
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            try:
                native.close()
            except CameraError:
                native.abort()

    def test_retained_reference_review_and_export_preserve_current_capture(self):
        c = self.launch()
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        first_id, first_path = c.lastRequestId, Path(c.dngPath)
        c.setExposureUs(20000)
        self.wait(lambda: c.canCapture)
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete' and c.lastRequestId != first_id)
        current_id = c.lastRequestId
        self.click('libraryButton')
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 2)
        self.assertEqual([current_id, first_id], [item['request_id'] for item in c.libraryItems])
        QTest.qWait(60)
        self.click('capture-' + first_id)
        self.wait(lambda: not c.libraryLoading and bool(c.reviewUrl))
        self.assertEqual(first_id, c.selectedCaptureId)
        self.assertEqual(current_id, c.lastRequestId)
        self.assertEqual('complete', c.captureState)
        raw = base64.b64decode(c.reviewUrl.split(',', 1)[1])
        self.assertEqual(c._client.capture_thumbnail(first_id), raw)
        self.assertNotEqual(c._client.capture_thumbnail(current_id), raw)
        self.assertFalse(QImage.fromData(raw).isNull())
        destination = self.root / 'export-copy.dng'
        self.item('libraryExportPath').setProperty('text', str(destination))
        self.click('libraryExportButton')
        self.wait(lambda: not c.libraryLoading and c.exportMessage.startswith('Exported '))
        expected = hashlib.sha256(first_path.read_bytes()).hexdigest()
        self.assertEqual(expected, hashlib.sha256(destination.read_bytes()).hexdigest())
        self.click('libraryExportButton')
        self.wait(lambda: not c.libraryLoading and 'not completed' in c.exportMessage)
        self.assertEqual(expected, hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertEqual(current_id, c.lastRequestId)
        self.window.resize(800, 600)
        QTest.qWait(80)
        self.evidence('retained-library-reference-800.png')
        c._client.connected = False
        self.wait(lambda: c._pending == 0)
        c._last_state = 0
        c._poll()
        self.wait(lambda: c.connectionState == 'disconnected')
        self.assertTrue(c.canBrowseCaptures)
        c.refreshLibrary()
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 2)

    def test_browsing_does_not_resolve_an_unconfirmed_current_request(self):
        service = CameraService(self.root / 'camera')
        class LostReply:
            uncertain = False
            def __getattr__(self, name): return getattr(service, name)
            def capture(self, request):
                result = service.capture(request)
                if self.uncertain:
                    raise CameraError('outcome_unknown', 'lost response')
                return result
        backend = LostReply()
        c = self.launch(factory=lambda: backend)
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        retained_id = c.lastRequestId
        backend.uncertain = True
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'unknown')
        uncertain_id = c.lastRequestId
        self.wait(lambda: service.query_capture(uncertain_id)['status'] == 'complete')
        self.click('libraryButton')
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 2)
        QTest.qWait(60)
        self.click('capture-' + retained_id)
        self.wait(lambda: not c.libraryLoading and bool(c.reviewUrl))
        self.assertEqual(uncertain_id, c.lastRequestId)
        self.assertEqual('unknown', c.captureState)
        self.assertFalse(c.canCapture)
        self.assertEqual(retained_id, c.selectedCaptureId)

    def test_retained_page_replaces_model_and_slow_export_does_not_starve_lease(self):
        service = CameraService(self.root / 'camera', lease_seconds=.6)
        acquired_at = []
        original_acquire = service.acquire
        def record_acquire(owner):
            acquired_at.append(time.monotonic())
            return original_acquire(owner)
        service.acquire = record_acquire
        c = self.launch(factory=lambda: service, defer_factory_until_window_ready=True)
        self.assertEqual(1,len(acquired_at))
        self.assertGreaterEqual(self._factory_released_at,self._qml_loaded_at)
        self.assertGreaterEqual(acquired_at[0],self._factory_released_at)
        self.assertEqual(.6,service.lease_seconds)
        print('HF_UI_FIXTURE_ACQUIRE_ORDER '+json.dumps({
            'qml_loaded_at':self._qml_loaded_at,'factory_released_at':self._factory_released_at,
            'acquired_at':acquired_at[0],'lease_seconds':service.lease_seconds}),flush=True)
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete')
        c.refreshLibrary()
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 1)
        c.selectCapture(c.libraryItems[0]['request_id'])
        self.wait(lambda: not c.libraryLoading and bool(c.reviewUrl))
        entered, release = threading.Event(), threading.Event()
        original = service.export_capture
        calls = []
        def slow_export(request_id, destination):
            calls.append(request_id)
            entered.set()
            release.wait(3)
            return original(request_id, destination)
        service.export_capture = slow_export
        try:
            c.exportSelected(str(self.root / 'slow-export.dng'))
            self.wait(entered.is_set)
            for _ in range(100):
                c.refreshLibrary()
                c.selectCapture(c.selectedCaptureId)
                c.exportSelected(str(self.root / 'duplicate-export.dng'))
            QTest.qWait(1300)
            self.assertEqual('ready', c.connectionState)
            self.assertIsNotNone(service.get_state()['control'])
            self.assertEqual(1, len(calls))
            self.assertTrue(c.libraryLoading)
        finally:
            release.set()
        self.wait(lambda: not c.libraryLoading and c.exportMessage.startswith('Exported '))
        source = c.libraryItems[0]
        original_list = service.list_captures
        ids = [str(uuid.UUID(int=index + 1)) for index in range(21)]
        def summary(request_id):
            return {**source, 'request_id': request_id, 'capture_id': request_id, 'frame_id': request_id,
                    'filename': request_id + '.dng', 'bit_depth': 14, 'cfa': 'RGGB', 'simulated': True}
        def pages(limit=20, cursor=None):
            values = ids[:20] if cursor is None else ids[20:]
            return {'items': [summary(value) for value in values], 'next_cursor': ids[19] if cursor is None else None,
                    'limit': limit, 'order': 'newest_first'}
        service.list_captures = pages
        c.refreshLibrary()
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 20)
        c.nextLibraryPage()
        self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 1)
        self.assertEqual(ids[20], c.libraryItems[0]['request_id'])
        self.assertEqual('', c.selectedCaptureId)
        self.assertEqual('', c.reviewUrl)
        service.list_captures = original_list

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_http_retained_qml_export_uses_client_destination(self):
        from camera.camerad.native_client import NativeClient
        from camera.camerad.server import BoundedHTTPServer, handler_for
        native = NativeClient(self.root / 'server-photos', command=native_command())
        server = thread = None
        try:
            handler = handler_for(native)
            handler.log_message = lambda *args: None
            server = BoundedHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            factory = make_client_factory('http', None, endpoint='http://127.0.0.1:' + str(server.server_port))
            c = self.launch(backend='http', factory=factory, directory=self.root / 'local-ui')
            self.click('shutterButton')
            self.wait(lambda: c.captureState == 'complete')
            request_id = c.lastRequestId
            source = self.root / 'server-photos/photos' / (request_id + '.dng')
            epoch = native.get_state()['control']['epoch']
            self.click('libraryButton')
            self.wait(lambda: not c.libraryLoading and len(c.libraryItems) == 1)
            QTest.qWait(60)
            self.click('capture-' + request_id)
            self.wait(lambda: not c.libraryLoading and bool(c.reviewUrl))
            destination = self.root / 'client-local-copy.dng'
            self.item('libraryExportPath').setProperty('text', str(destination))
            self.click('libraryExportButton')
            self.wait(lambda: not c.libraryLoading and c.exportMessage.startswith('Exported '))
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertEqual(epoch, native.get_state()['control']['epoch'])
            self.assertEqual(request_id, c.lastRequestId)
            self.evidence('retained-library-native-http-1024.png')
            c.shutdown()
            self.assertIsNone(native.process.poll())
            self.assertIsNone(native.get_state()['control'])
        finally:
            if self.controller is not None: self.controller.shutdown()
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None: thread.join(timeout=5)
            try: native.close()
            except CameraError: native.abort()

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_authority_actual_capture_and_dng(self):
        c = self.launch(backend='native')
        self.assertIn('native C++', c.backendLabel)
        self.click('shutterButton')
        self.wait(lambda: c.captureState == 'complete', timeout=12000)
        self.assertEqual(c.lastRequestId, inspect_dng(c.dngPath)['request_id'])
        self.assertTrue(Path(c.dngPath).is_file())
        self.evidence('native-complete-1024.png')
        self.click('queryButton')
        self.wait(lambda: c._pending == 0)
        self.assertEqual(1, len(list((self.root / 'camera/photos').glob('*.dng'))))

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_unknown_acknowledgment_allows_new_uuid_and_preserves_old_record(self):
        c = self.launch(backend='native')
        original = c._client.capture
        c._client.capture = lambda request: original(dict(request, fault='delay'))
        self.click('shutterButton')
        self.wait(lambda: c.captureState in {'accepted', 'capturing'})
        old_id = c.lastRequestId
        c._client.abort()
        self.wait(lambda: c.captureState == 'unknown')
        self.click('reconnectButton')
        self.wait(lambda: c.connectionState == 'ready' and c.captureState == 'unknown')
        self.wait(lambda: c.canAcknowledgeUnknown)
        old_record = c._client.query_capture(old_id)
        self.assertEqual('unknown', old_record['status'])
        self.assertFalse(c.canCapture)
        self.click('acknowledgeUnknownButton')
        self.assertEqual('acknowledged', c.captureState)
        self.assertTrue(c.canCapture)
        self.assertEqual(old_id, c.lastRequestId)
        state_path = self.root / 'camera/device-ui.json'
        self.assertIn(old_id, json.loads(state_path.read_text())['acknowledged_unknown_request_ids'])
        # A query must not silently reapply the saved acknowledgment.
        self.click('queryButton')
        self.wait(lambda: c.captureState == 'unknown')
        self.assertFalse(c.canCapture)
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        self.assertTrue(c.canCapture)
        # A restart retains the original UUID and trace, and requires a fresh action.
        c.shutdown()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.engine = self.window = None
        c = self.launch(backend='native')
        self.assertEqual(old_id, c.lastRequestId)
        self.assertEqual('unknown', c.captureState)
        self.assertFalse(c.canCapture)
        self.wait(lambda: c.canAcknowledgeUnknown)
        self.click('acknowledgeUnknownButton')
        self.assertTrue(c.canCapture)
        self.window.resize(800, 600)
        QTest.qWait(60)
        self.evidence('native-acknowledged-800.png')
        self.click('shutterButton')
        new_id = c.lastRequestId
        self.assertNotEqual(old_id, new_id)
        self.wait(lambda: c.captureState == 'complete', timeout=12000)
        self.assertEqual(new_id, inspect_dng(c.dngPath)['request_id'])
        self.assertEqual(old_record, c._client.query_capture(old_id))
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            rows = db.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()
        self.assertEqual({old_id: 'unknown', new_id: 'complete'}, dict(rows))
        saved = json.loads(state_path.read_text())
        self.assertEqual(new_id, saved['request_id'])
        self.assertEqual([old_id], saved['acknowledged_unknown_request_ids'])

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_http_attach_native_capture_releases_lease_and_keeps_server_alive(self):
        from camera.camerad.native_client import NativeClient
        from camera.camerad.http_client import HttpCameraClient
        from camera.camerad.server import BoundedHTTPServer, handler_for
        server_root = self.root / 'server-camera'
        state_root = self.root / 'local-ui-state'
        native = NativeClient(server_root, command=native_command())
        server = thread = None
        try:
            handler = handler_for(native)
            handler.log_message = lambda *args: None
            server = BoundedHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = 'http://127.0.0.1:' + str(server.server_port)
            factory = make_client_factory('http', self.root / 'must-not-be-used', endpoint=endpoint)
            c = self.launch(backend='http', factory=factory, directory=state_root)
            self.assertIn('local API', c.backendLabel)
            self.assertFalse((self.root / 'must-not-be-used').exists())
            self.click('shutterButton')
            self.wait(lambda: c.captureState == 'complete', timeout=12000)
            request_id = c.lastRequestId
            self.assertEqual(request_id, inspect_dng(c.dngPath)['request_id'])
            self.assertEqual('complete', native.query_capture(request_id)['status'])
            self.assertFalse((server_root / 'device-ui.json').exists())
            self.assertEqual(request_id, json.loads((state_root / 'device-ui.json').read_text())['request_id'])
            self.evidence('http-attach-complete-1024.png')
            c.shutdown()
            self.assertIsNone(native.process.poll(), 'UI shutdown stopped the shared native server')
            observer = HttpCameraClient(endpoint)
            try:
                state = observer.get_state()
                self.assertTrue(state['connected'])
                self.assertIsNone(state['control'], 'UI did not release its own control lease')
                followup = observer.acquire('another-local-client')
                observer.release(followup['token'], followup['epoch'])
                self.assertEqual('complete', observer.query_capture(request_id)['status'])
            finally:
                observer.close()
            self.assertIsNone(native.process.poll())
        finally:
            if self.controller is not None:
                self.controller.shutdown()
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
            try:
                native.close()
            except CameraError:
                native.abort()

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_cancel_is_confirmed_by_original_request(self):
        c = self.launch(backend='native')
        original = c._client.capture
        c._client.capture = lambda request: original(dict(request, fault='delay'))
        self.click('shutterButton')
        self.wait(lambda: c.captureState in {'accepted', 'capturing'})
        request_id = c.lastRequestId
        self.click('cancelButton')
        self.wait(lambda: c.captureState == 'cancelled')
        self.assertEqual(request_id, c.lastRequestId)
        self.assertEqual('', c.dngPath)
        self.assertEqual([], list((self.root / 'camera/photos').glob('*.dng')))

    def test_request_id_storage_failure_prevents_submission(self):
        c = self.launch()
        blocker = self.root / 'not-a-directory'
        blocker.write_bytes(b'file')
        c._state_path = blocker / 'request.json'
        self.click('shutterButton')
        self.assertEqual('', c.lastRequestId)
        self.assertIn('not submitted', c.errorText)
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM captures').fetchone()[0])

    @unittest.skipUnless(native_command(), 'native hf-camerad executable is not built')
    def test_native_disconnect_unknown_reconnect_does_not_resubmit(self):
        c = self.launch(backend='native')
        original = c._client.capture
        def capture_delayed(request):
            return original(dict(request, fault='delay'))
        c._client.capture = capture_delayed
        self.click('shutterButton')
        self.wait(lambda: c.captureState in {'accepted', 'capturing'})
        request_id = c.lastRequestId
        c._client.abort()
        self.wait(lambda: c.captureState == 'unknown')
        self.assertFalse(c.canCapture)
        self.assertEqual('', c.previewUrl)
        self.click('reconnectButton')
        self.wait(lambda: c.connectionState != 'connecting')
        self.assertEqual('ready', c.connectionState)
        self.assertEqual('unknown', c.captureState)
        self.assertEqual(request_id, c.lastRequestId)
        self.assertFalse(c.canCapture)
        with closing(sqlite3.connect(self.root / 'camera/journal.sqlite3')) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM captures').fetchone()[0])
        self.click('queryButton')
        self.wait(lambda: c._pending == 0)
        self.assertEqual(request_id, c.lastRequestId)
        self.assertEqual('unknown', c.captureState)
        self.window.resize(800, 600)
        QTest.qWait(80)
        self.evidence('native-unknown-800.png')


if __name__ == '__main__':
    unittest.main()

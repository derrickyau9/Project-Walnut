"""Camera-first Qt interactions, independent of the historical console tests."""
from __future__ import annotations

from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
if os.name == 'nt':
    os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
try:
    from PyQt5.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt, QUrl
    from PyQt5.QtGui import QFont, QGuiApplication, QKeyEvent, QWheelEvent
    from PyQt5.QtQml import QQmlApplicationEngine
    from PyQt5.QtQuick import QQuickItem, QQuickWindow
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('optional PyQt5/Qt Quick runtime is not installed')

from camera.dng.inspect import inspect_dng

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT/'ui/on-device'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


camera_module = load_module('hf_camera_first_controller', UI/'controller.py')
shell_module = load_module('hf_camera_first_shell', UI/'shell_model.py')
apps_module = load_module('hf_camera_first_apps', UI/'apps_controller.py')


def native_command():
    value = os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
    if value:
        return [value]
    for candidate in (ROOT/'build/native-r11/Release/hf-camerad.exe', ROOT/'build/native/hf-camerad'):
        if candidate.is_file():
            return [str(candidate)]
    return None


class CameraFirstUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setFont(QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif',10))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)/'camera'
        self.directory.mkdir()
        self.controller = self.shell = self.apps = self.engine = self.window = None
        self.warnings = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        if self.controller is not None:
            self.controller.shutdown()
        if self.engine is not None:
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for model in (self.apps, self.shell, self.controller):
            if model is not None:
                model.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window = self.engine = self.controller = self.shell = self.apps = None
        self.temporary.cleanup()

    def wait(self, predicate, timeout=8000):
        deadline = time.monotonic()+timeout/1000
        while time.monotonic() < deadline:
            if predicate():
                return
            QTest.qWait(20)
        self.fail('camera-first UI timed out: '+('; '.join(self.warnings[-3:]) or
                  (self.controller.statusText+'; '+self.controller.errorText if self.controller else 'no controller')))

    def launch(self, *, backend='reference', saved_request=None, width=800, height=600):
        if saved_request:
            (self.directory/'device-ui.json').write_text(
                json.dumps({'schema_version': 1, 'request_id': saved_request}), encoding='utf-8')
        self.controller = camera_module.CameraController(
            camera_module.make_client_factory(backend, self.directory, native_command() if backend == 'native' else None),
            backend=backend, state_path=self.directory/'device-ui.json', poll_ms=60)
        self.shell = shell_module.ShellController(self.directory/'shell-preferences.json')
        self.assertTrue(self.shell.setPreference('theme','dark'))
        self.apps = apps_module.CameraAppsController(self.controller, self.directory)
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda values:self.warnings.extend(value.toString() for value in values))
        for name, value in (('camera', self.controller), ('cameraShell', self.shell), ('cameraApps', self.apps),
                            ('retailTheme', json.loads((UI/'retail-theme.json').read_text(encoding='utf-8'))),
                            ('runtimeOSLabel', platform.system()+' UI preview / '+platform.machine())):
            self.engine.rootContext().setContextProperty(name, value)
        self.engine.load(QUrl.fromLocalFile(str(UI/'Main.qml')))
        self.assertEqual(1, len(self.engine.rootObjects()), '; '.join(self.warnings))
        self.window = self.engine.rootObjects()[0]
        self.assertEqual('cameraFirstWindow', self.window.objectName(), 'R12 must not silently load Console.qml')
        self.window.resize(width, height)
        self.window.requestActivate()
        self.wait(lambda:self.controller.connectionState != 'connecting')
        self.assertEqual('ready', self.controller.connectionState, self.controller.errorText)
        QTest.qWait(100)
        return self.controller

    def find(self, name):
        if self.window is None:
            return None
        found = self.window.findChild(QQuickItem, name)
        if found is not None:
            return found
        def visual(parent):
            for child in parent.childItems():
                if child.objectName() == name:
                    return child
                result = visual(child)
                if result is not None:
                    return result
            return None
        return visual(self.window.contentItem())

    def item(self, name):
        self.wait(lambda:self.find(name) is not None)
        return self.find(name)

    def popup(self, name):
        result = self.window.findChild(QObject, name)
        self.assertIsNotNone(result, name)
        return result

    def click(self, name):
        control = self.item(name)
        parent = control.parentItem()
        while parent is not None:
            if parent.property('contentY') is not None and parent.property('contentHeight') is not None:
                for _ in range(30):
                    top = control.mapToItem(parent, QPointF(0,0)).y()
                    if top >= 0 and top+control.height() <= parent.height():
                        break
                    point = parent.mapToScene(QPointF(parent.width()/2,parent.height()/2))
                    event = QWheelEvent(point,point,QPoint(),QPoint(0,360 if top < 0 else -360),
                                        Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
                    QCoreApplication.sendEvent(self.window,event)
                    QTest.qWait(35)
                self.wait(lambda:not bool(parent.property('moving')),timeout=2000)
            parent = parent.parentItem()
        self.assertTrue(control.isVisible(),name+' hidden')
        self.assertTrue(control.isEnabled(),name+' disabled')
        point = control.mapToScene(QPointF(control.width()/2,control.height()/2))
        self.assertTrue(0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height(),name)
        QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,QPoint(round(point.x()),round(point.y())))
        QTest.qWait(70)

    def capture_rows(self):
        with closing(sqlite3.connect(self.directory/'journal.sqlite3')) as journal:
            return journal.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()

    def assert_in_view(self,name):
        control = self.item(name)
        self.assertTrue(control.isVisible(),name)
        point = control.mapToScene(QPointF(0,0))
        self.assertGreater(control.width(),0,name)
        self.assertGreater(control.height(),0,name)
        self.assertGreaterEqual(point.x(),-1,name)
        self.assertGreaterEqual(point.y(),-1,name)
        self.assertLessEqual(point.x()+control.width(),self.window.width()+1,name)
        self.assertLessEqual(point.y()+control.height(),self.window.height()+1,name)
        parent = control.parentItem()
        while parent is not None:
            if parent.clip():
                offset = control.mapToItem(parent,QPointF(0,0))
                self.assertGreaterEqual(offset.x(),-1,name)
                self.assertGreaterEqual(offset.y(),-1,name)
                self.assertLessEqual(offset.x()+control.width(),parent.width()+1,name)
                self.assertLessEqual(offset.y()+control.height(),parent.height()+1,name)
            parent = parent.parentItem()

    def visible_text(self):
        def visit(parent):
            rows=[]
            for child in parent.childItems():
                if child.isVisible() and isinstance(child.property('text'),str):
                    rows.append(child.property('text'))
                rows.extend(visit(child))
            return rows
        return '\n'.join(visit(self.window.contentItem()))

    def test_boot_enters_actual_synthetic_preview_without_capture(self):
        c=self.launch()
        self.wait(lambda:c.previewActive and bool(c.previewUrl))
        self.assertEqual('capture',self.window.property('currentScreen'))
        self.assertEqual('',c.lastRequestId)
        self.assertEqual([],self.capture_rows())
        self.assertEqual([],self.warnings)

    def test_800_viewfinder_and_shooting_controls_are_visible_without_desktop_rail(self):
        self.launch()
        self.wait(lambda:bool(self.controller.previewUrl))
        image=self.item('viewfinderImage')
        self.assertGreaterEqual(image.width(),720)
        self.assertGreaterEqual(image.height(),430)
        for name in ('viewfinderImage','shootButton','menuButton','quickButton','playbackButton'):
            self.assert_in_view(name)
        for name in ('DaOSDock','nav-settings','sidebarToggleButton','commandPaletteButton'):
            old=self.find(name)
            self.assertTrue(old is None or not old.isVisible(),name+' leaked console chrome')
        text=self.visible_text()
        self.assertNotIn(str(self.directory),text)
        self.assertNotIn('control_epoch',text)
        self.assertNotIn('request_id',text)
        self.assertEqual([],self.capture_rows())

    def test_quick_control_changes_authoritative_settings_and_closes_to_capture(self):
        c=self.launch()
        self.click('quickButton')
        self.wait(lambda:self.popup('quickControlsDialog').property('visible'))
        before=c.exposureUs
        self.item('qExposureCombo').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Down)
        self.wait(lambda:c.exposureUs != before and c.canCapture)
        self.assertEqual(c.exposureUs,c._client.get_state()['settings']['requested_exposure_us'])
        before=c.analogueGain
        self.item('qGainInput').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Up)
        self.wait(lambda:c.analogueGain > before and c.canCapture)
        self.assertEqual(c.analogueGain,c._client.get_state()['settings']['requested_analogue_gain'])
        keyboard_gain=c.analogueGain
        self.click('qGainIncrease')
        self.wait(lambda:c.analogueGain > keyboard_gain and c.canCapture)
        self.assertEqual(c.analogueGain,c._client.get_state()['settings']['requested_analogue_gain'])
        self.click('qGainDecrease')
        self.wait(lambda:c.analogueGain == keyboard_gain and c.canCapture)
        self.assertEqual(c.analogueGain,c._client.get_state()['settings']['requested_analogue_gain'])
        self.click('qCloseButton')
        self.assertEqual('capture',self.window.property('currentScreen'))
        self.assertEqual([],self.capture_rows())

    def test_pending_quick_setting_holds_shutter_until_authority_acknowledges(self):
        c=self.launch()
        gate=threading.Event();self.gates.append(gate)
        entered=threading.Event()
        configure=c._client.configure
        def delayed(*args,**kwargs):
            entered.set()
            if not gate.wait(5):raise RuntimeError('test settings gate timed out')
            return configure(*args,**kwargs)
        c._client.configure=delayed
        self.click('quickButton')
        self.item('qGainInput').forceActiveFocus();QTest.keyClick(self.window,Qt.Key_Up)
        self.wait(entered.is_set)
        self.assertFalse(c.canCapture)
        self.assertFalse(self.item('shootButton').isEnabled())
        QTest.keyClick(self.window,Qt.Key_Space)
        self.assertEqual([],self.capture_rows())
        gate.set();self.wait(lambda:c.canCapture)
        self.assertEqual(c.analogueGain,c._client.get_state()['settings']['requested_analogue_gain'])

    def test_menu_groups_and_back_return_to_shooting_without_capture(self):
        self.launch()
        self.click('menuButton')
        self.wait(lambda:self.window.property('currentScreen')=='menu')
        for group in ('exposure','image','display','storage','system','tools'):
            self.click('menuCategory-'+group)
            self.assertEqual('menu',self.window.property('currentScreen'))
            self.assertEqual([],self.capture_rows())
        self.click('menuBackButton')
        self.wait(lambda:self.window.property('currentScreen')=='capture')
        self.assertEqual([],self.capture_rows())

    def test_tools_menu_remains_available_without_taking_photographs(self):
        self.launch()
        for tool in ('recipes','devices','diagnostics','system'):
            control=self.find('menuTool-'+tool)
            self.assertTrue(control is None or not control.isVisible())
        self.click('menuButton');self.click('menuCategory-tools')
        for tool in ('recipes','devices','diagnostics','system'):
            self.assert_in_view('menuTool-'+tool)
        self.click('menuTool-diagnostics')
        self.wait(lambda:self.window.property('currentScreen')=='tool')
        self.assertEqual('diagnostics',self.shell.currentApp)
        self.assertEqual([],self.capture_rows())

    def test_menu_exposure_row_opens_quick_control_and_returns_to_menu(self):
        c=self.launch()
        self.click('menuButton');self.click('menuCategory-exposure')
        self.click('menuShutterSetting')
        self.wait(lambda:self.popup('quickControlsDialog').property('visible'))
        before=c.exposureUs
        self.item('qExposureCombo').forceActiveFocus();QTest.keyClick(self.window,Qt.Key_Down)
        self.wait(lambda:c.exposureUs != before and c.canCapture)
        self.click('qCloseButton')
        self.assertEqual('menu',self.window.property('currentScreen'))
        self.assertEqual(c.exposureUs,c._client.get_state()['settings']['requested_exposure_us'])
        self.assertEqual([],self.capture_rows())

    def test_display_preferences_survive_recreation_without_sensor_changes(self):
        c=self.launch()
        before=c._client.get_state()['settings']
        self.click('menuButton');self.click('menuCategory-display')
        self.click('menuGridSwitch')
        self.wait(lambda:self.shell.gridEnabled)
        self.item('menuThemeCombo').forceActiveFocus();QTest.keyClick(self.window,Qt.Key_Down)
        self.wait(lambda:self.shell.theme=='light')
        self.item('menuBrightnessSlider').forceActiveFocus();QTest.keyClick(self.window,Qt.Key_Left)
        self.wait(lambda:self.shell.previewBrightness<100)
        restored=shell_module.ShellController(self.directory/'shell-preferences.json')
        try:
            self.assertTrue(restored.gridEnabled)
            self.assertEqual('light',restored.theme)
            self.assertEqual(self.shell.previewBrightness,restored.previewBrightness)
        finally:
            restored.deleteLater()
        self.assertEqual(before,c._client.get_state()['settings'])
        self.assertEqual([],self.capture_rows())

    def test_menu_and_quick_keyboard_input_never_submits_shutter(self):
        self.launch()
        self.click('menuButton')
        for key in (Qt.Key_Space,Qt.Key_Return):QTest.keyClick(self.window,key)
        self.assertEqual([],self.capture_rows())
        QTest.keyClick(self.window,Qt.Key_Escape)
        self.wait(lambda:self.window.property('currentScreen')=='capture')
        self.click('quickButton');self.item('qGainInput').forceActiveFocus()
        for key in (Qt.Key_Space,Qt.Key_Return):QTest.keyClick(self.window,key)
        self.assertEqual([],self.capture_rows())
        QTest.keyClick(self.window,Qt.Key_Escape)
        self.wait(lambda:not self.popup('quickControlsDialog').property('visible'))
        self.assertEqual([],self.capture_rows())

    def test_unknown_original_request_holds_shutter_across_menu_and_query(self):
        original=str(uuid.uuid4())
        c=self.launch(saved_request=original)
        self.assertEqual('unknown',c.captureState)
        self.assertTrue(self.item('captureRecoveryPanel').isVisible())
        self.assertFalse(self.item('shootButton').isEnabled())
        self.click('queryOriginalButton')
        self.wait(lambda:c._pending==0)
        self.assertEqual(original,c.lastRequestId)
        self.click('menuButton');QTest.keyClick(self.window,Qt.Key_Space)
        self.assertEqual(original,c.lastRequestId)
        QTest.keyClick(self.window,Qt.Key_Escape)
        self.wait(lambda:self.window.property('currentScreen')=='capture')
        QTest.keyClick(self.window,Qt.Key_Space)
        self.assertEqual(original,c.lastRequestId)
        self.assertEqual([],self.capture_rows())

    def test_shutter_keyboard_auto_repeat_cannot_submit_another_capture(self):
        c=self.launch()
        self.wait(lambda:c.canCapture)
        QTest.keyClick(self.window,Qt.Key_Space)
        self.wait(lambda:c.captureState=='complete',timeout=12000)
        original=c.lastRequestId
        for _ in range(3):
            QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyPress,Qt.Key_Space,Qt.NoModifier,' ',True,1))
            QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyRelease,Qt.Key_Space,Qt.NoModifier,' ',True,1))
        QTest.qWait(250)
        self.assertEqual(original,c.lastRequestId)
        self.assertEqual([(original,'complete')],self.capture_rows())

    @unittest.skipUnless(native_command(),'native hf-camerad executable is not built')
    def test_native_one_capture_and_playback_retain_original_dng_identity(self):
        c=self.launch(backend='native')
        state=c._client.get_state()
        self.assertIs(state.get('settings_persistent'),True)
        self.click('shootButton')
        self.wait(lambda:c.captureState=='complete',timeout=12000)
        original=c.lastRequestId
        self.assertEqual(original,inspect_dng(c.dngPath)['request_id'])
        self.assertNotIn(original,self.visible_text())
        self.assertNotIn(str(self.directory),self.visible_text())
        self.click('playbackButton')
        self.wait(lambda:self.window.property('currentScreen')=='playback')
        self.wait(lambda:bool(c.reviewUrl) and c.selectedCaptureId==original)
        self.assert_in_view('playbackReviewImage')
        self.item('playbackReviewImage').forceActiveFocus()
        for key in (Qt.Key_Space,Qt.Key_Return):QTest.keyClick(self.window,key)
        self.assertEqual('playback',self.window.property('currentScreen'))
        self.assertEqual(original,c.lastRequestId)
        self.assertEqual([(original,'complete')],self.capture_rows())
        self.click('playbackInfoButton')
        self.wait(lambda:self.popup('playbackInfoPopup').property('visible'))
        self.assertIn(original,self.item('playbackInfoText').property('text'))
        QTest.keyClick(self.window,Qt.Key_Escape)
        self.wait(lambda:not self.popup('playbackInfoPopup').property('visible'))
        self.assertEqual('playback',self.window.property('currentScreen'))
        QTest.keyClick(self.window,Qt.Key_Escape)
        self.wait(lambda:self.window.property('currentScreen')=='capture')
        self.assertEqual([],self.warnings)


if __name__=='__main__':
    unittest.main()

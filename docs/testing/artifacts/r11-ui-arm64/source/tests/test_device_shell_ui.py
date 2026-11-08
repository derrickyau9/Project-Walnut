"""Interaction tests for the native DaOS app shell, with no shutter actions."""
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
import time
import unittest
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
if os.name == 'nt':
    os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
try:
    from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QUrl
    from PyQt5.QtGui import QFont, QGuiApplication, QKeyEvent, QWheelEvent
    from PyQt5.QtQml import QQmlApplicationEngine
    from PyQt5.QtQuick import QQuickItem, QQuickWindow
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('optional PyQt5/Qt Quick runtime is not installed')

from daos.capability_runtime.recipes import load_recipe

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT/'ui/on-device'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


camera_module = load_module('hf_shell_test_camera', UI/'controller.py')
shell_module = load_module('hf_shell_test_state', UI/'shell_model.py')
apps_module = load_module('hf_shell_test_apps', UI/'apps_controller.py')


def native_command():
    configured=os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
    if configured:return [configured]
    for candidate in (ROOT/'build/native/Release/hf-camerad.exe',ROOT/'build/native/hf-camerad',
                       ROOT/'build/native/hf-camerad.exe'):
        if candidate.is_file():return [str(candidate)]
    return None


class DeviceShellUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setFont(QFont('Segoe UI' if os.name=='nt' else 'Sans Serif'))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.controller = self.shell = self.apps = self.engine = self.window = None
        self.qml_warnings = []

    def tearDown(self):
        if self.controller is not None:
            self.controller.shutdown()
        if self.engine is not None:
            self.engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for model in (self.apps, self.shell, self.controller):
            if model is not None:
                model.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window = self.engine = self.apps = self.shell = self.controller = None
        self.temporary.cleanup()

    def wait(self, predicate, timeout=8000):
        until = time.monotonic() + timeout/1000
        while time.monotonic() < until:
            if predicate(): return
            QTest.qWait(20)
        self.fail('shell Qt condition timed out: ' + ('; '.join(self.qml_warnings[-3:]) or
                  (self.controller.statusText if self.controller else 'no controller')))

    def launch(self, *, saved_request=None, width=1280, height=800, available_apps=None, backend='reference'):
        self.directory = self.root/'camera'
        self.directory.mkdir()
        if saved_request:
            (self.directory/'device-ui.json').write_text(
                json.dumps({'schema_version':1, 'request_id':saved_request}), encoding='utf-8')
        self.controller = camera_module.CameraController(
            camera_module.make_client_factory(backend,self.directory,native_command() if backend=='native' else None),
            backend=backend,
            state_path=self.directory/'device-ui.json', poll_ms=60)
        self.shell = shell_module.ShellController(self.directory/'shell-preferences.json', available_apps=available_apps)
        self.apps = apps_module.CameraAppsController(self.controller, self.directory)
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda values:self.qml_warnings.extend(item.toString() for item in values))
        context = self.engine.rootContext()
        for name, value in (('camera',self.controller),('cameraShell',self.shell),('cameraApps',self.apps),
                            ('retailTheme',json.loads((UI/'retail-theme.json').read_text(encoding='utf-8'))),
                            ('runtimeOSLabel',platform.system()+' UI preview / '+platform.machine())):
            context.setContextProperty(name,value)
        self.engine.load(QUrl.fromLocalFile(str(UI/'Main.qml')))
        self.assertEqual(1,len(self.engine.rootObjects()), 'new shell QML did not load: '+'; '.join(self.qml_warnings))
        self.window = self.engine.rootObjects()[0]
        self.window.resize(width,height)
        self.window.requestActivate()
        self.wait(lambda:self.controller.connectionState != 'connecting')
        self.assertEqual('ready',self.controller.connectionState,self.controller.errorText)
        self.wait(lambda:self.find('nav-settings') is not None)
        QTest.qWait(80)
        return self.controller

    def find(self,name):
        if self.window is None:return None
        found=self.window.findChild(QQuickItem,name)
        if found is not None:return found
        def visual(parent):
            for child in parent.childItems():
                if child.objectName()==name:return child
                value=visual(child)
                if value is not None:return value
            return None
        return visual(self.window.contentItem())

    def item(self,name):
        self.wait(lambda:self.find(name) is not None)
        return self.find(name)

    def click(self,name):
        item=self.item(name)
        self.scroll_into_view(item)
        self.assertTrue(item.isVisible(), name+' is hidden')
        self.assertTrue(item.isEnabled(), name+' is disabled')
        point=item.mapToScene(QPointF(item.width()/2,item.height()/2))
        self.assertTrue(0<=point.x()<self.window.width() and 0<=point.y()<self.window.height(),
                        name+' is outside the clickable viewport')
        QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,QPoint(round(point.x()),round(point.y())))
        QTest.qWait(60)

    def scroll_into_view(self,item):
        ancestors=[]
        ancestor=item.parentItem()
        while ancestor is not None:
            if ancestor.property('contentY') is not None and ancestor.property('contentHeight') is not None:
                ancestors.append(ancestor)
            ancestor=ancestor.parentItem()
        for flickable in ancestors:
            for _ in range(35):
                top=item.mapToItem(flickable,QPointF(0,0)).y()
                if top>=0 and top+item.height()<=flickable.height():break
                point=flickable.mapToScene(QPointF(flickable.width()/2,flickable.height()/2))
                event=QWheelEvent(point,point,QPoint(),QPoint(0,360 if top<0 else -360),
                                  Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
                QCoreApplication.sendEvent(self.window,event)
                QTest.qWait(40)
            self.wait(lambda:not bool(flickable.property('moving')),timeout=2000)

    def text(self,name,value):
        item=self.item(name)
        item.forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_A,Qt.ControlModifier)
        for character in value:
            QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,character))
            QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,character))
        QTest.qWait(30)
        self.assertEqual(value,item.property('text'))

    def capture_count(self):
        with closing(sqlite3.connect(self.directory/'journal.sqlite3')) as db:
            return db.execute('SELECT count(*) FROM captures').fetchone()[0]

    def assert_visible_in_viewport(self,name):
        item=self.item(name)
        self.assertTrue(item.isVisible(),name)
        self.assertGreater(item.width(),0,name)
        self.assertGreater(item.height(),0,name)
        point=item.mapToScene(QPointF(0,0))
        self.assertGreaterEqual(point.x(),-1,name)
        self.assertGreaterEqual(point.y(),-1,name)
        self.assertLessEqual(point.x()+item.width(),self.window.width()+1,name)
        self.assertLessEqual(point.y()+item.height(),self.window.height()+1,name)
        ancestor=item.parentItem()
        while ancestor is not None:
            if ancestor.clip():
                position=item.mapToItem(ancestor,QPointF(0,0))
                self.assertGreaterEqual(position.x(),-1,name+' clipped on left')
                self.assertGreaterEqual(position.y(),-1,name+' clipped above')
                self.assertLessEqual(position.x()+item.width(),ancestor.width()+1,name+' clipped on right')
                self.assertLessEqual(position.y()+item.height(),ancestor.height()+1,name+' clipped below')
            ancestor=ancestor.parentItem()

    def test_all_seven_app_routes_preserve_original_unknown_request_without_capture(self):
        original=str(uuid.uuid4())
        c=self.launch(saved_request=original)
        self.assertEqual('unknown',c.captureState)
        for app_id in ('settings','devices','diagnostics','system','recipes','photos','camera'):
            self.click('nav-'+app_id)
            self.wait(lambda:self.shell.currentApp==app_id)
            self.assertEqual(original,c.lastRequestId)
            self.assertEqual('unknown',c.captureState)
            self.assertEqual(0,self.capture_count())
        self.assertEqual([],self.qml_warnings)

    def test_settings_widgets_change_authoritative_persistent_settings(self):
        c=self.launch()
        self.click('nav-settings');self.click('appSettingsGroup-exposure')
        initial=c.exposureUs
        self.item('appSettingsExposureInput').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Up)
        self.wait(lambda:c.exposureUs>initial and c.canCapture)
        self.assertEqual(c.exposureUs,c._client.get_state()['settings']['requested_exposure_us'])
        initial=c.analogueGain
        self.item('appSettingsGainInput').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Up)
        self.wait(lambda:c.analogueGain>initial and c.canCapture)
        self.assertEqual(c.analogueGain,c._client.get_state()['settings']['requested_analogue_gain'])
        self.click('appSettingsGroup-image')
        self.item('appSettingsModeCombo').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Down)
        self.wait(lambda:c.sensorMode=='sim-full' and c.canCapture)
        self.assertEqual('sim-full',c._client.get_state()['settings']['sensor_mode'])
        self.assertEqual(0,self.capture_count())

    @unittest.skipUnless(native_command(),'native hf-camerad executable is not built')
    def test_native_settings_control_confirms_persistent_contract_without_capture(self):
        c=self.launch(backend='native')
        state=c._client.get_state()
        self.assertIs(state.get('settings_persistent'),True)
        self.assertEqual('hf-device-settings-v1',state.get('settings_format'))
        self.click('nav-settings');self.click('appSettingsGroup-exposure')
        initial=c.exposureUs
        self.item('appSettingsExposureInput').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Up)
        self.wait(lambda:c.exposureUs>initial and c.canCapture)
        self.assertEqual('ready',c.connectionState)
        self.assertEqual(c.exposureUs,c._client.get_state()['settings']['requested_exposure_us'])
        self.assertEqual('',c.lastRequestId)
        self.assertEqual(0,self.capture_count())
        self.assertEqual([],self.qml_warnings)

    def test_display_controls_persist_without_altering_sensor_settings(self):
        c=self.launch()
        before=c._client.get_state()['settings']
        self.click('nav-settings');self.click('appSettingsGroup-display')
        self.click('appThemeDarkButton')
        self.wait(lambda:self.shell.theme=='dark')
        self.click('appGridSwitch')
        self.wait(lambda:self.shell.gridEnabled)
        self.item('appBrightnessSlider').forceActiveFocus()
        QTest.keyClick(self.window,Qt.Key_Left)
        self.wait(lambda:self.shell.previewBrightness<100)
        restored=shell_module.ShellController(self.directory/'shell-preferences.json')
        try:
            self.assertEqual('dark',restored.theme)
            self.assertTrue(restored.gridEnabled)
            self.assertEqual(self.shell.previewBrightness,restored.previewBrightness)
        finally:restored.deleteLater()
        self.assertEqual(before,c._client.get_state()['settings'])
        self.assertEqual(0,self.capture_count())

    def test_recipe_app_saves_and_exports_actual_portable_json_without_execution(self):
        self.launch()
        self.click('nav-recipes')
        self.text('appRecipeNameInput','Review bracket')
        self.text('appRecipeExposureInput','5000,10000,20000')
        self.click('appRecipeSaveButton')
        self.wait(lambda:len(self.apps.recipes)==1)
        self.assertEqual('',self.apps.errorText)
        exports=Path(self.apps.exportDirectory)
        before=set(exports.glob('*.json'))
        self.click('appRecipeExportButton')
        self.wait(lambda:len(set(exports.glob('*.json'))-before)==1)
        exported=next(iter(set(exports.glob('*.json'))-before))
        self.assertEqual([5000,10000,20000],load_recipe(exported)['plan']['exposures_us'])
        self.assertEqual('Review bracket',load_recipe(exported)['name'])
        self.assertEqual(0,self.capture_count())

    def test_recipe_notice_does_not_leak_into_diagnostics_app(self):
        self.launch()
        self.click('nav-recipes');self.click('appRecipeSaveButton')
        self.wait(lambda:len(self.apps.recipes)==1)
        self.click('appRecipeExportButton')
        self.wait(lambda:'Portable recipe exported' in self.apps.message)
        recipe_message=self.apps.message
        self.assertEqual('recipes',self.apps.messageScope)
        self.click('nav-diagnostics')
        self.assertEqual('recipes',self.apps.messageScope)
        def visible_texts(parent):
            values=[]
            for child in parent.childItems():
                if child.isVisible() and child.property('text') is not None:
                    values.append(child.property('text'))
                values.extend(visible_texts(child))
            return values
        self.assertNotIn(recipe_message,visible_texts(self.window.contentItem()))
        self.click('appDiagnosticExportButton')
        self.wait(lambda:self.apps.messageScope=='diagnostics' and 'diagnostic snapshot exported' in self.apps.message)
        self.assertIn(self.apps.message,visible_texts(self.window.contentItem()))
        self.assertEqual(0,self.capture_count())
        self.assertEqual([],self.qml_warnings)

    def test_diagnostics_app_exports_requested_state_preserving_original_request(self):
        original=str(uuid.uuid4())
        self.launch(saved_request=original)
        settings_before=self.controller._client.get_state()['settings']
        self.click('nav-diagnostics')
        exports=Path(self.apps.exportDirectory)
        self.click('appDiagnosticExportButton')
        self.wait(lambda:len(list(exports.glob('*.json')))==1)
        value=json.loads(next(exports.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(original,value['camera']['request_id'])
        self.assertEqual('unknown',value['camera']['capture_status'])
        self.assertTrue(value['synthetic'])
        self.assertFalse(value['physical_hardware_validated'])
        self.assertNotIn('actual_exposure_us',value['camera'])
        self.assertEqual(original,self.controller.lastRequestId)
        self.assertEqual(settings_before,self.controller._client.get_state()['settings'])
        self.assertEqual(0,self.capture_count())

    def test_compact_viewport_contains_navigation_camera_and_settings_controls(self):
        self.launch(width=800,height=600)
        for app_id in ('camera','photos','recipes','settings','devices','diagnostics','system'):
            self.assert_visible_in_viewport('nav-'+app_id)
        self.assert_visible_in_viewport('shutterButton')
        self.assert_visible_in_viewport('previewButton')
        self.click('nav-settings');self.click('appSettingsGroup-exposure')
        for name in ('appSettingsExposureInput','appSettingsGainInput','appSettingsGroup-display'):
            self.assert_visible_in_viewport(name)
        self.assertEqual([],self.qml_warnings)

    def test_command_palette_keyboard_search_navigates_without_capture(self):
        self.launch()
        QTest.keyClick(self.window,Qt.Key_K,Qt.ControlModifier)
        self.wait(lambda:self.item('commandSearchInput').isVisible())
        self.text('commandSearchInput','diagnostics')
        self.wait(lambda:self.find('command-result-diagnostics') is not None)
        camera_result=self.find('command-result-camera')
        self.assertTrue(camera_result is None or not camera_result.isVisible())
        self.click('command-result-diagnostics')
        self.wait(lambda:self.shell.currentApp=='diagnostics')
        self.assertEqual(0,self.capture_count())

    def test_launcher_has_real_app_routes_and_hides_unavailable_commands(self):
        self.launch(available_apps={'camera','settings'})
        self.click('appLauncherButton')
        self.click('appLaunch-settings')
        self.wait(lambda:self.shell.currentApp=='settings')
        QTest.keyClick(self.window,Qt.Key_K,Qt.ControlModifier)
        self.wait(lambda:self.item('commandSearchInput').isVisible())
        self.text('commandSearchInput','diagnostics')
        QTest.qWait(60)
        result=self.find('command-result-diagnostics')
        self.assertTrue(result is None or not result.isVisible() or not result.isEnabled())
        QTest.keyClick(self.window,Qt.Key_Return)
        self.assertEqual('settings',self.shell.currentApp)
        self.assertEqual(0,self.capture_count())

    def test_command_palette_exposure_search_opens_validated_settings_group_without_capture(self):
        original=str(uuid.uuid4())
        self.launch(saved_request=original)
        QTest.keyClick(self.window,Qt.Key_K,Qt.ControlModifier)
        self.wait(lambda:self.item('commandSearchInput').isVisible())
        self.text('commandSearchInput','exposure')
        self.wait(lambda:self.find('command-result-settings-exposure') is not None)
        self.assertTrue(self.item('command-result-settings-exposure').isVisible())
        QTest.keyClick(self.window,Qt.Key_Return)
        self.wait(lambda:self.shell.currentApp=='settings' and self.shell.currentGroup=='exposure')
        self.assertEqual(original,self.controller.lastRequestId)
        self.assertEqual('unknown',self.controller.captureState)
        self.assertEqual(0,self.capture_count())
        self.assertEqual([],self.qml_warnings)

    def test_compact_sidebar_menu_opens_launcher_and_routes_without_capture(self):
        original=str(uuid.uuid4())
        self.launch(saved_request=original,width=800,height=600)
        self.click('sidebarToggleButton')
        self.wait(lambda:self.item('appLaunch-settings').isVisible())
        self.click('appLaunch-settings')
        self.wait(lambda:self.shell.currentApp=='settings')
        self.assertEqual(original,self.controller.lastRequestId)
        self.assertEqual('unknown',self.controller.captureState)
        self.assertEqual(0,self.capture_count())
        self.assertEqual([],self.qml_warnings)


if __name__=='__main__':
    unittest.main()

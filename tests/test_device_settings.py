"""Qt controller tests for durable authority settings (no physical camera)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('PyQt5 optional UI runtime is not installed')

from camera.camerad.service import CameraError, CameraService

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hf_settings_controller', ROOT / 'ui/on-device/controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeviceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.controllers = []

    def tearDown(self):
        for controller in reversed(self.controllers):
            controller.shutdown()
        self.temp.cleanup()

    def wait(self, predicate, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate(): return
            QTest.qWait(20)
        self.fail('timed out waiting for UI state')

    def start(self, factory, backend='reference'):
        controller = module.CameraController(factory, backend=backend, state_path=self.root/'ui.json', poll_ms=50)
        self.controllers.append(controller)
        self.wait(lambda: controller.connectionState != 'connecting')
        self.assertEqual('ready', controller.connectionState, controller.errorText)
        return controller

    def test_edit_persists_and_restart_reads_authority_without_capture(self):
        directory = self.root/'camera'
        factory = lambda: CameraService(directory)
        c = self.start(factory)
        c.setExposureUs(12300); c.setAnalogueGain(2.5); c.setSensorMode('sim-full')
        self.assertFalse(c.canCapture)
        self.wait(lambda: c.canCapture and c.statusText == 'Settings confirmed by local camera')
        self.assertEqual((12300, 2.5, 'sim-full'), (c.exposureUs, c.analogueGain, c.sensorMode))
        self.assertEqual([], list((directory/'photos').glob('*.dng')))
        c.shutdown(); self.controllers.remove(c)
        restored = self.start(factory)
        self.assertEqual((12300, 2.5, 'sim-full'), (restored.exposureUs, restored.analogueGain, restored.sensorMode))
        self.assertEqual('', restored.lastRequestId)

    def test_latest_edits_coalesce_while_worker_waits_and_preserve_other_fields(self):
        service = CameraService(self.root/'camera')
        initial = service.acquire('prior-client')
        service.configure(initial['token'], initial['epoch'], {'simulation_scene': 'flat'})
        service.release(initial['token'], initial['epoch'])
        entered, proceed = threading.Event(), threading.Event()
        class Delayed:
            calls = []
            def __getattr__(self, name): return getattr(service, name)
            def configure(self, token, epoch, settings):
                self.calls.append(dict(settings))
                if len(self.calls) == 1:
                    entered.set(); proceed.wait(5)
                return service.configure(token, epoch, settings)
        wrapped = Delayed(); c = self.start(lambda: wrapped)
        try:
            c.setExposureUs(11000)
            self.wait(entered.is_set)
            c.setExposureUs(12000); c.setExposureUs(13000); c.setAnalogueGain(2)
            self.assertFalse(c.canCapture)
            self.assertTrue(c.settingsPending)
            self.assertEqual('applying', c.settingsView['phase'])
            self.assertEqual(13000,c.settingsView['desired']['requested_exposure_us'])
            self.assertEqual(2,c.settingsView['desired']['requested_analogue_gain'])
            self.assertEqual(10000, service.get_state()['settings']['requested_exposure_us'])
        finally:
            proceed.set()
        self.wait(lambda: c.canCapture and c.settingsView['phase'] == 'stable')
        # SettingsIntent now submits complete editable snapshots. The queued
        # edits still coalesce into exactly one successor configuration.
        self.assertEqual([{'requested_exposure_us': 11000,'requested_analogue_gain':1.0,'sensor_mode':'sim-small'},
                          {'requested_exposure_us': 13000,'requested_analogue_gain':2.0,'sensor_mode':'sim-small'}], wrapped.calls)
        settings = service.get_state()['settings']
        self.assertEqual((13000, 2, 'flat'), (settings['requested_exposure_us'], settings['requested_analogue_gain'], settings['simulation_scene']))

    def test_lost_configure_reply_holds_capture_and_reconnect_reads_actual_without_retry(self):
        service = CameraService(self.root/'camera')
        class LostAck:
            calls = 0
            def __getattr__(self, name): return getattr(service, name)
            def configure(self, token, epoch, settings):
                self.calls += 1
                service.configure(token, epoch, settings)
                raise CameraError('outcome_unknown', 'response lost')
        wrapped = LostAck(); c = self.start(lambda: wrapped)
        c.setExposureUs(15000)
        self.wait(lambda: c.connectionState == 'disconnected')
        self.assertFalse(c.canCapture)
        self.assertIn('not confirmed', c.statusText)
        self.assertEqual('', c.lastRequestId)
        self.assertEqual(1, wrapped.calls)
        c.shutter(); self.assertEqual('', c.lastRequestId)
        c.reconnect(); self.wait(lambda: c.connectionState == 'ready')
        self.assertEqual(15000, c.exposureUs)
        self.assertEqual(1, wrapped.calls)
        self.assertEqual([], list((self.root/'camera/photos').glob('*.dng')))

    def test_unknown_capture_does_not_allow_setting_write(self):
        service = CameraService(self.root/'camera'); c = self.start(lambda: service)
        c._capture = 'unknown'
        c.setExposureUs(18000)
        QTest.qWait(100)
        self.assertEqual(10000, service.get_state()['settings']['requested_exposure_us'])
        self.assertFalse(c.canCapture)
        self.assertEqual('unknown', c.captureState)

    def test_missing_persistence_marker_cannot_confirm_setting(self):
        service = CameraService(self.root/'camera')
        class LegacyState:
            def __getattr__(self, name): return getattr(service, name)
            def get_state(self):
                state = service.get_state()
                state.pop('settings_persistent', None); state.pop('settings_format', None)
                return state
        c = self.start(lambda: LegacyState())
        c.setExposureUs(14000)
        self.wait(lambda: c.connectionState == 'disconnected')
        self.assertFalse(c.canCapture)
        self.assertIn('not confirmed', c.statusText)
        self.assertEqual('', c.lastRequestId)

    def test_native_authority_restart_when_explicitly_available(self):
        executable = (os.environ.get('HF_R9_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_DAEMON')
                      or os.environ.get('HF_NATIVE_CAMERAD'))
        explicit = bool(executable)
        if not executable:
            filename = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
            executable = next((str(path) for path in (ROOT/'build/native/Release'/filename,
                              ROOT/'build/native'/filename) if path.is_file()), None)
        if not executable:
            self.skipTest('native authority not built; build it or set HF_NATIVE_DAEMON')
        if not Path(executable).is_file():
            self.fail('explicit native authority does not exist: ' + executable)
        factory = module.make_client_factory('native', self.root/'native', [executable])
        c = self.start(factory, backend='native')
        state = c._client.get_state()
        if (state.get('settings_persistent') is not True
                or state.get('settings_format') != 'hf-device-settings-v1'):
            if explicit:
                self.fail('configured native authority does not support persistent settings API 1.1')
            self.skipTest('conventional native build predates persistent settings API 1.1')
        c.setExposureUs(13200); c.setAnalogueGain(1.5)
        self.wait(lambda: c.canCapture and c.statusText == 'Settings confirmed by local camera')
        c.shutdown(); self.controllers.remove(c)
        restored = self.start(factory, backend='native')
        self.assertEqual((13200, 1.5), (restored.exposureUs, restored.analogueGain))


if __name__ == '__main__': unittest.main()

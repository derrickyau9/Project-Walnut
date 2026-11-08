"""004 interaction regressions: delayed authority, relative input and temporary restoration."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtCore import QCoreApplication,QEvent
from PyQt5.QtTest import QTest
from camera.camerad.service import CameraService
from controller import CameraController
from shooting_workspace import ShootingWorkspace, WorkspaceState
from parameter_state import SettingsIntent
from camera_capabilities import exposure_steps
from input_router import CameraInputRouter

APP=QGuiApplication.instance() or QGuiApplication([])

def wait_for(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('Timed out waiting for controller state')

class RecordingService:
    def __init__(self,path):
        self.service=CameraService(path)
        self.configurations=[]
        self.captures=[]
        self.delay=.12
        self.capture_gate=None
        self.active=0
        self.maximum=0
        self.fail_configure=False
        self.fail_capture=False
        self.thumbnail_delay=0
        self.list_calls=0
    def __getattr__(self,name):return getattr(self.service,name)
    def configure(self,*args):
        self.active+=1;self.maximum=max(self.maximum,self.active)
        try:
            self.configurations.append(dict(args[-1]))
            time.sleep(self.delay)
            value=self.service.configure(*args)
            if self.fail_configure:
                self.fail_configure=False;raise OSError('injected lost acknowledgement')
            return value
        finally:self.active-=1
    def capture(self,request):
        self.captures.append(dict(request))
        if self.capture_gate:self.capture_gate.wait(2)
        value=self.service.capture(request)
        if self.fail_capture:
            self.fail_capture=False;raise OSError('injected lost capture reply')
        return value
    def capture_thumbnail(self,*args):
        if self.thumbnail_delay:time.sleep(self.thumbnail_delay)
        return self.service.capture_thumbnail(*args)
    def list_captures(self,*args,**kwargs):
        self.list_calls+=1
        return self.service.list_captures(*args,**kwargs)

class Controls004(unittest.TestCase):
    def setUp(self):
        temporary_root=ROOT/'build/DaOS_Camera_004'
        temporary_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=str(temporary_root))
        self.root=Path(self.temp.name)
        self.service=RecordingService(self.root/'camera')
        self.camera=CameraController(lambda:self.service,backend='reference',state_path=self.root/'ui/device-ui.json',poll_ms=20)
        wait_for(lambda:self.camera.connectionState=='ready')
    def tearDown(self):
        if self.service.capture_gate:self.service.capture_gate.set()
        self.camera.shutdown()
        deferred=getattr(self.camera,'_deferred_close_thread',None)
        if deferred:deferred.join(3)
        APP.processEvents()
        self.assertTrue(self.root.resolve().is_relative_to((ROOT/'build/DaOS_Camera_004').resolve()))
        self.temp.cleanup()
    def stable(self):wait_for(lambda:not self.camera.settingsPending and self.camera.settingsView['phase']=='stable')
    def test_ticks_during_delayed_ack_are_accumulated(self):
        self.camera.setSettings({'requested_exposure_us':30000000},'test')
        self.stable();self.service.configurations.clear()
        self.camera.nudgeSettings('exposure',20,'dial')
        wait_for(lambda:self.camera._settings_active)
        for _ in range(30):self.camera.nudgeSettings('exposure',1,'dial')
        self.stable()
        self.assertEqual(self.camera.exposureUs,exposure_steps()[50]['us'])
        self.assertEqual(self.camera.confirmedSettings['requested_exposure_us'],exposure_steps()[50]['us'])
        self.assertEqual(self.service.maximum,1)
        self.assertLessEqual(len(self.service.configurations),2)
    def test_router_preserves_aggregated_ticks(self):
        router=CameraInputRouter(APP)
        events=[];router.dialRequested.connect(events.append)
        router.dispatch('dial',True,17);router.dispatch('dial',True,-9)
        self.assertEqual(events,[17,-9])
        router.deleteLater()
    def test_capture_uses_frozen_confirmed_parameters(self):
        original=dict(self.camera.confirmedSettings)
        self.service.capture_gate=threading.Event()
        self.camera.shutter()
        wait_for(lambda:bool(self.service.captures))
        request_id=self.camera.lastRequestId
        self.camera.nudgeSettings('exposure',5,'dial')
        self.assertEqual(self.service.captures[0]['requested_exposure_us'],original['requested_exposure_us'])
        self.service.capture_gate.set()
        wait_for(lambda:self.camera.captureState=='complete')
        self.stable()
        self.assertNotEqual(self.camera.confirmedSettings['requested_exposure_us'],original['requested_exposure_us'])
        self.assertEqual(self.camera.lastRequestId,request_id)
        self.assertEqual(len(self.service.captures),1)
    def test_fn_release_during_ack_restores(self):
        baseline=dict(self.camera.confirmedSettings)
        token=self.camera.beginTemporary({'requested_exposure_us':2000000},'fn1')
        self.assertTrue(token)
        wait_for(lambda:self.camera._settings_active)
        self.camera.endTemporary(token)
        wait_for(lambda:not self.camera.settingsView['isTemporary'] and not self.camera.settingsPending)
        self.assertEqual(self.camera.confirmedSettings,baseline)
        self.assertFalse((self.root/'ui/temporary-settings.json').exists())
        self.assertEqual(len(self.service.configurations),2)
    def test_fn_release_during_capture_does_not_mutate_current_shot(self):
        baseline=dict(self.camera.confirmedSettings)
        token=self.camera.beginTemporary({'requested_exposure_us':200000},'fn2')
        self.stable()
        self.service.capture_gate=threading.Event();self.camera.shutter()
        wait_for(lambda:bool(self.service.captures))
        self.camera.endTemporary(token)
        self.assertTrue(self.camera.settingsPending)
        self.assertEqual(self.service.captures[0]['requested_exposure_us'],200000)
        self.service.capture_gate.set()
        wait_for(lambda:self.camera.captureState=='complete')
        wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.confirmedSettings,baseline)
    def test_profile_v2_real_settings_and_import_is_passive(self):
        workspace=ShootingWorkspace(self.root/'workspace',APP)
        workspace.attachCamera(self.camera);workspace.setCapabilities(self.camera.capabilities)
        workspace.setCurrentSettings({'grid_enabled':True,'theme':'dark'})
        original=dict(self.camera.confirmedSettings)
        self.assertTrue(workspace.saveProfile('C1'))
        self.camera.nudgeSettings('gain',3,'test');self.stable()
        self.assertTrue(workspace.loadProfile('C1'));self.stable()
        self.assertEqual(self.camera.confirmedSettings,original)
        path=self.root/'share.json';self.assertTrue(workspace.exportWorkspace(str(path)))
        self.camera.nudgeSettings('gain',2,'test');self.stable()
        before=dict(self.camera.confirmedSettings);count=len(self.service.configurations)
        self.assertTrue(workspace.importWorkspace(str(path)))
        QTest.qWait(160)
        self.assertEqual(self.camera.confirmedSettings,before)
        self.assertEqual(len(self.service.configurations),count)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['version'],2)
    def test_v1_profiles_migrate_without_inventing_camera_parameters(self):
        from shooting_workspace import default_layout
        path=self.root/'legacy';path.mkdir()
        (path/'shooting-workspace.json').write_text(json.dumps({'version':1,'layout':default_layout(),'profiles':{'C1':default_layout()},'active_profile':'C1'}),encoding='utf-8')
        state=WorkspaceState(path)
        self.assertEqual(state.error,'')
        self.assertIsNone(state.profiles['C1']['camera'])
    def test_fn_lost_ack_reconciles_explicitly_without_capture(self):
        baseline=dict(self.camera.confirmedSettings)
        self.service.fail_configure=True
        token=self.camera.beginTemporary({'requested_exposure_us':2000000},'fn1')
        wait_for(lambda:self.camera.connectionState=='disconnected')
        self.camera.endTemporary(token)
        self.assertTrue(self.camera.settingsView['recoveryRequired'])
        self.camera.reconnect();wait_for(lambda:self.camera.connectionState=='ready')
        self.assertFalse(self.camera.canCapture)
        self.assertTrue(self.camera.reconcileTemporary())
        wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.confirmedSettings,baseline)
        self.assertEqual(self.service.captures,[])
    def test_fn_unknown_capture_keeps_uuid_and_never_retries(self):
        baseline=dict(self.camera.confirmedSettings)
        token=self.camera.beginTemporary({'requested_exposure_us':200000},'fn1');self.stable()
        self.service.fail_capture=True;self.camera.shutter()
        wait_for(lambda:self.camera.captureState=='unknown')
        request_id=self.camera.lastRequestId;self.camera.endTemporary(token)
        QTest.qWait(150)
        self.camera.reconnect();wait_for(lambda:self.camera.connectionState=='ready')
        wait_for(lambda:self.camera.captureState=='complete')
        self.assertTrue(self.camera.reconcileTemporary())
        wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.lastRequestId,request_id)
        self.assertEqual(len(self.service.captures),1)
        self.assertEqual(self.camera.confirmedSettings,baseline)
    def test_restart_retains_fn_baseline_recovery_log(self):
        baseline=dict(self.camera.confirmedSettings)
        self.camera.beginTemporary({'requested_exposure_us':2000000},'fn1');self.stable()
        self.assertTrue((self.root/'ui/temporary-settings.json').exists())
        self.camera.shutdown()
        self.service=RecordingService(self.root/'camera')
        self.camera=CameraController(lambda:self.service,backend='reference',state_path=self.root/'ui/device-ui.json',poll_ms=20)
        wait_for(lambda:self.camera.connectionState=='ready')
        self.assertTrue(self.camera.settingsView['recoveryRequired'])
        self.assertFalse(self.camera.canCapture)
        self.assertTrue(self.camera.reconcileTemporary())
        wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.confirmedSettings,baseline)
    def test_fn_application_deactivation_restores_profile(self):
        workspace=ShootingWorkspace(self.root/'workspace',APP);workspace.attachCamera(self.camera);workspace.setCapabilities(self.camera.capabilities)
        self.camera.setSettings({'requested_exposure_us':200000},'test');self.stable()
        self.assertTrue(workspace.saveProfile('C1'))
        self.camera.setSettings({'requested_exposure_us':10000},'test');self.stable()
        baseline=dict(self.camera.confirmedSettings)
        workspace.setInput('fn1','hold_c1');workspace.beginInput('fn1');self.stable()
        self.assertTrue(self.camera.settingsView['isTemporary'])
        QCoreApplication.sendEvent(APP,QEvent(QEvent.ApplicationDeactivate))
        wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.confirmedSettings,baseline)
    def test_remote_given_uuid_is_durable_and_duplicate_only_queries(self):
        import uuid
        first=str(uuid.uuid4());second=str(uuid.uuid4())
        self.assertTrue(self.camera.captureWithId(first,'remote:test'))
        state=json.loads((self.root/'ui/device-ui.json').read_text(encoding='utf-8'))
        self.assertEqual(state['request_id'],first)
        self.assertTrue(self.camera.captureWithId(first,'remote:test'))
        wait_for(lambda:self.camera.captureState=='complete')
        self.assertEqual(len(self.service.captures),1)
        self.assertTrue(self.camera.captureWithId(second,'remote:test'))
        wait_for(lambda:self.camera.captureState=='complete' and self.camera.lastRequestId==second)
        self.assertTrue(self.camera.captureWithId(first,'remote:test'))
        wait_for(lambda:self.camera.captureState=='complete' and self.camera.lastRequestId==first)
        self.assertEqual(len(self.service.captures),2)
    def test_remote_cannot_replace_unknown_uuid_and_local_control_signals(self):
        import uuid
        events=[];self.camera.localControlRequested.connect(events.append)
        self.camera.nudgeSettings('gain',2,'remote:test');self.stable()
        self.assertEqual(events,[])
        self.camera.nudgeSettings('gain',1,'dial');self.stable()
        self.assertEqual(events,['dial'])
        self.service.fail_capture=True;self.camera.shutter();wait_for(lambda:self.camera.captureState=='unknown')
        original=self.camera.lastRequestId
        self.assertFalse(self.camera.captureWithId(str(uuid.uuid4()),'remote:test'))
        self.assertEqual(self.camera.lastRequestId,original)
    def test_fn_started_during_base_ack_preserves_uncovered_relative_edits(self):
        self.camera.setSettings({'requested_exposure_us':250000},'test')
        wait_for(lambda:self.camera._settings_active)
        token=self.camera.beginTemporary({'requested_analogue_gain':4},'fn1')
        self.assertTrue(token)
        self.camera.nudgeSettings('exposure',3,'dial')
        self.camera.endTemporary(token)
        self.stable();wait_for(lambda:not self.camera.settingsView['isTemporary'])
        self.assertEqual(self.camera.confirmedSettings['requested_exposure_us'],125000)
        self.assertEqual(self.camera.confirmedSettings['requested_analogue_gain'],1)
        self.assertTrue(all(item['requested_analogue_gain']==1 for item in self.service.configurations))
    def test_fn_pressed_and_released_during_capture_never_changes_exposure(self):
        baseline=dict(self.camera.confirmedSettings)
        self.service.capture_gate=threading.Event();self.camera.shutter();wait_for(lambda:bool(self.service.captures))
        token=self.camera.beginTemporary({'requested_analogue_gain':4},'fn1')
        self.assertTrue(token);self.camera.endTemporary(token)
        self.service.capture_gate.set();wait_for(lambda:self.camera.captureState=='complete');self.stable()
        self.assertEqual(self.camera.confirmedSettings,baseline)
        self.assertEqual(self.service.configurations,[])
    def test_off_grid_gain_moves_to_adjacent_stop_without_skipping(self):
        self.camera.setSettings({'requested_analogue_gain':1.5},'test');self.stable()
        self.camera.nudgeSettings('gain',1,'dial');self.stable()
        self.assertEqual(self.camera.analogueGain,1.59)
        self.camera.setSettings({'requested_analogue_gain':1.5},'test');self.stable()
        self.camera.nudgeSettings('gain',-1,'dial');self.stable()
        self.assertEqual(self.camera.analogueGain,1.26)
    def test_profile_load_interrupted_by_dial_does_not_switch_mapping(self):
        workspace=ShootingWorkspace(self.root/'workspace',APP);workspace.attachCamera(self.camera);workspace.setCapabilities(self.camera.capabilities)
        workspace.setInput('dial','gain')
        self.camera.setSettings({'requested_exposure_us':2000000},'test');self.stable();self.assertTrue(workspace.saveProfile('C1'))
        workspace.setInput('dial','exposure')
        self.camera.setSettings({'requested_exposure_us':10000},'test');self.stable()
        self.assertTrue(workspace.loadProfile('C1'))
        self.camera.nudgeSettings('exposure',2,'dial');self.stable()
        self.assertEqual(workspace.inputBindings['dial'],'exposure')
        self.assertEqual(self.camera.exposureUs,1300000)
    def test_refresh_requested_during_thumbnail_is_coalesced_not_lost(self):
        self.service.thumbnail_delay=.2
        self.camera.shutter()
        wait_for(lambda:self.camera.captureState=='complete' and self.camera._saved_thumbnail_pending)
        for _ in range(100):self.camera.refreshLibrary()
        wait_for(lambda:len(self.camera.libraryItems)==1 and not self.camera._library_pending)
        self.assertLessEqual(self.service.list_calls,2)

if __name__=='__main__':
    (ROOT/'build/DaOS_Camera_004').mkdir(parents=True,exist_ok=True)
    unittest.main()

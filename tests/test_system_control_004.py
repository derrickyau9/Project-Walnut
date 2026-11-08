"""004 portable configuration transactions and confirmed system maintenance."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'ui/on-device'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtCore import QEvent
from PyQt5.QtTest import QTest
import system_archive as archive
import system_control as system
from media_catalog import MediaCatalog,DEFAULT_EDITS
from shooting_workspace import default_layout,ShootingWorkspace
from shell_model import DEFAULT_PREFERENCES,ShellController

APP=QGuiApplication.instance() or QGuiApplication([])
BUILD=ROOT/'build/DaOS_Camera_004'
BUILD.mkdir(parents=True,exist_ok=True)

def wait_for(predicate,seconds=5):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('timed out waiting for system operation')

def state_document():
    layout=default_layout()
    return dict(version=2,layout=layout,profiles={'C1':dict(ui=copy.deepcopy(layout),camera=dict(requested_exposure_us=12500,requested_analogue_gain=2.,sensor_mode='sim-full'),compatibility=dict(device='reference',settings_schema='hf-device-settings-v1'))},active_profile='C1')

def style_document():
    return dict(format='daos.photo-styles.v1',styles=[dict(id=str(uuid.uuid4()),name='阴天街拍',params=copy.deepcopy(DEFAULT_EDITS))])

def portable(rating=4):
    return dict(format='daos.portable-photo-metadata.v1',items=[dict(sha256='a'*64,rating=rating,protected=False,trashed=False,edits=copy.deepcopy(DEFAULT_EDITS))],unavailable_fingerprints=0)

def config_documents():
    return {'shell-preferences.json':archive.encode(dict(schema_version=1,preferences=dict(DEFAULT_PREFERENCES,grid_enabled=True,theme='dark'))),
            'shooting-workspace.json':archive.encode(state_document()),
            'system-preferences.json':archive.encode(dict(system.PREFERENCES,timezone='UTC',developer_mode=True)),
            'apps/photo-styles.json':archive.encode(style_document())}

def write_documents(root,documents):
    for name,raw in documents.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

def tar_write(path,members):
    with tarfile.open(path,'w:gz') as target:
        for name,raw,mode in members:
            member=tarfile.TarInfo(name);member.size=len(raw);member.mode=mode
            target.addfile(member,io.BytesIO(raw))

def backup_tar(path,documents,mutate=None):
    manifest=dict(format=archive.FORMAT,created_utc='2026-11-06T00:00:00+00:00',files=[dict(path=name,bytes=len(raw),sha256=archive.digest(raw)) for name,raw in documents.items()])
    if mutate:mutate(manifest)
    tar_write(path,[('manifest.json',archive.encode(manifest),0o600)]+[(name,raw,0o600) for name,raw in documents.items()])

def update_tar(path,version='004',native=True,osabi=9,mode=0o755):
    documents={'opt/hasslefree/ui/on-device/main.py':b'# entry\n','opt/hasslefree/ui/on-device/Main.qml':b'import QtQuick 2.15\nItem {}'}
    if native:
        header=bytearray(64);header[:7]=b'\x7fELF\x02\x01\x01';header[7]=osabi;header[18:20]=(183).to_bytes(2,'little')
        documents.update({name:bytes(header) for name in ('opt/hasslefree/bin/hf-camerad','opt/hasslefree/bin/hf-imager')})
    modes={name:(mode if '/bin/'in name else 0o644) for name in documents}
    manifest=dict(format='DaOS-camera-app-v1',version='camera-experience-'+version,files=[dict(path=name,bytes=len(raw),sha256=archive.digest(raw),mode=modes[name]) for name,raw in documents.items()])
    tar_write(path,[('manifest.json',archive.encode(manifest),0o644)]+[('payload/'+name,raw,modes[name]) for name,raw in documents.items()])
    return archive.digest(path.read_bytes())

class Crash(BaseException):pass

class CameraGuard:
    busy=False
    settingsPending=False
    def __init__(self):self.allowed=True;self.active='';self.calls=[]
    def beginMaintenance(self,reason,allow_unknown=False):
        self.calls.append(('begin',reason,allow_unknown))
        if not self.allowed:return ''
        self.active=uuid.uuid4().hex;return self.active
    def endMaintenance(self,token):
        if token!=self.active:raise ValueError('maintenance token mismatch')
        self.calls.append(('end',token));self.active=''

class TaskGuard:
    def __init__(self,ready=True):self.ready=ready;self.blocked=False;self.calls=[]
    def setAdmissionBlocked(self,value,reason=''):self.blocked=value;self.calls.append(value)
    def quiescent(self):return self.ready

class SystemArchive004(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=BUILD);self.root=Path(self.temp.name)
    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BUILD.resolve()));self.temp.cleanup()
    def catalog(self):
        catalog=MediaCatalog(self.root);photo=str(uuid.uuid4());library=str(uuid.uuid4())
        catalog.upsert_asset(library,dict(photo_uuid=photo,original_request_uuid=photo,change_seq=1,state='committed',sha256='a'*64))
        return catalog,library,photo
    def test_backup_includes_styles_and_passive_camera_bank_excludes_runtime_and_secrets(self):
        documents=config_documents();write_documents(self.root,documents)
        secret=b'{"lease":"do-not-copy","request_id":"do-not-replay","psk":"secret"}'
        write_documents(self.root,{'device-ui.json':secret,'temporary-settings.json':secret,'wifi.json':secret,'apps/task-journal.json':secret})
        destination=self.root/'backup.tar.gz';archive.create_backup(self.root,destination)
        _,found=archive.inspect_backup(destination)
        self.assertEqual(set(found),set(documents));self.assertEqual(json.loads(found['shooting-workspace.json'])['profiles']['C1']['camera']['requested_exposure_us'],12500)
        with self.assertRaises(ValueError):archive.create_backup(self.root,destination)
        self.assertEqual(secret,(self.root/'device-ui.json').read_bytes())
    def test_rejects_unknown_workspace_authority_and_duplicate_json(self):
        value=state_document();value['lease']='forbidden'
        write_documents(self.root,{'shooting-workspace.json':archive.encode(value)})
        with self.assertRaises(ValueError):archive.create_backup(self.root,self.root/'bad.tar.gz')
        with self.assertRaises(ValueError):archive.strict_json(b'{"version":1,"version":2}')
        with self.assertRaises(ValueError):archive.strict_json(b'{"x":NaN}')
    def test_backup_rejects_traversal_duplicates_unlisted_and_digest_mismatch(self):
        documents={'system-preferences.json':archive.encode(system.PREFERENCES)};target=self.root/'bad.tar.gz'
        for extra in ('../system-preferences.json','/system-preferences.json','a\\b'):
            tar_write(target,[(extra,b'{}',0o600)])
            with self.assertRaises(ValueError,msg=extra):archive.inspect_backup(target)
        backup_tar(target,documents,lambda m:m['files'][0].update(sha256='0'*64))
        with self.assertRaises(ValueError):archive.inspect_backup(target)
        backup_tar(target,documents,lambda m:m.update(files=[]))
        with self.assertRaises(ValueError):archive.inspect_backup(target)
        raw=archive.encode(system.PREFERENCES);tar_write(target,[('system-preferences.json',raw,0o600)]*2)
        with self.assertRaises(ValueError):archive.inspect_backup(target)
    def test_update_requires_full_manifest_hash_modes_and_matching_arm64_native_pair(self):
        target=self.root/'update.tar.gz';digest=update_tar(target)
        result=archive.inspect_update(target,digest.upper());self.assertEqual(result['files'],4)
        with self.assertRaises(ValueError):archive.inspect_update(target,'0'*64)
        for kwargs in ({'native':False},{'osabi':0},{'mode':0o644}):
            digest=update_tar(target,**kwargs)
            with self.assertRaises(ValueError,msg=str(kwargs)):archive.inspect_update(target,digest)
        digest=update_tar(target,version='003',native=False);self.assertEqual(archive.inspect_update(target,digest)['files'],2)
    def test_restore_preserves_local_developer_mode_and_capture_identity(self):
        capture=b'{"last_request_id":"kept","lease":"kept"}'
        write_documents(self.root,{'device-ui.json':capture})
        archive.restore_documents(self.root,config_documents(),developer_mode=False)
        self.assertFalse(json.loads((self.root/'system-preferences.json').read_bytes())['developer_mode'])
        self.assertEqual((self.root/'device-ui.json').read_bytes(),capture)
        self.assertEqual(json.loads((self.root/'shooting-workspace.json').read_bytes())['version'],2)
        self.assertEqual(archive.recover_restore_transactions(self.root),[])
    def test_v1_restore_migrates_layout_without_inventing_parameters(self):
        layout=default_layout();old=dict(version=1,layout=layout,profiles={'C1':layout},active_profile='C1')
        archive.restore_documents(self.root,{'shooting-workspace.json':archive.encode(old)})
        restored=json.loads((self.root/'shooting-workspace.json').read_bytes())
        self.assertEqual(restored['version'],2);self.assertIsNone(restored['profiles']['C1']['camera'])
    def test_failure_rolls_back_all_config_files(self):
        original={'system-preferences.json':archive.encode(system.PREFERENCES)};write_documents(self.root,original)
        def fail(stage):
            if stage=='files-applied':raise OSError('injected publication failure')
        with self.assertRaises(OSError):archive.restore_documents(self.root,config_documents(),fault=fail)
        self.assertEqual((self.root/'system-preferences.json').read_bytes(),original['system-preferences.json'])
        self.assertFalse((self.root/'shooting-workspace.json').exists())
        self.assertEqual(archive.recover_restore_transactions(self.root),[])
    def test_kill_before_commit_rolls_back_on_restart(self):
        for phase in ('prepared','files-applied'):
            original=archive.encode(dict(system.PREFERENCES,timezone='Asia/Tokyo'));(self.root/'system-preferences.json').write_bytes(original)
            def die(stage):
                if stage==phase:raise Crash()
            with self.assertRaises(Crash):archive.restore_documents(self.root,config_documents(),fault=die)
            recovered=archive.recover_restore_transactions(self.root)
            self.assertEqual(recovered[0]['result'],'rolled_back');self.assertEqual((self.root/'system-preferences.json').read_bytes(),original)
            self.assertFalse((self.root/'shooting-workspace.json').exists())
    def test_kill_after_metadata_commit_rolls_forward_using_database_ledger(self):
        catalog,library,photo=self.catalog();documents=config_documents();documents['portable-media.json']=archive.encode(portable())
        def die(stage):
            if stage=='metadata-applied':raise Crash()
        with self.assertRaises(Crash):archive.restore_documents(self.root,documents,catalog,fault=die)
        self.assertEqual(catalog.photo(library,photo)['rating'],4)
        # Recovery runs before QObject/catalog construction and reads the durable DB ledger.
        result=archive.recover_restore_transactions(self.root)
        self.assertEqual(result[0]['result'],'completed');self.assertEqual(json.loads((self.root/'system-preferences.json').read_bytes())['timezone'],'UTC')
        self.assertEqual(catalog.photo(library,photo)['metadata_revision'],1)
    def test_conflict_preflight_changes_neither_config_nor_metadata(self):
        catalog,library,photo=self.catalog();catalog.restorePortableMetadata(portable(2))
        original=archive.encode(system.PREFERENCES);(self.root/'system-preferences.json').write_bytes(original)
        documents=config_documents();documents['portable-media.json']=archive.encode(portable(5))
        with self.assertRaises(ValueError):archive.restore_documents(self.root,documents,catalog)
        self.assertEqual(catalog.photo(library,photo)['rating'],2);self.assertEqual((self.root/'system-preferences.json').read_bytes(),original)
        self.assertFalse((self.root/'shooting-workspace.json').exists())
    def test_recovery_refuses_external_edits_instead_of_overwriting(self):
        def die(stage):
            if stage=='files-applied':raise Crash()
        with self.assertRaises(Crash):archive.restore_documents(self.root,config_documents(),fault=die)
        external=archive.encode(dict(system.PREFERENCES,timezone='Asia/Shanghai'));(self.root/'system-preferences.json').write_bytes(external)
        with self.assertRaises(ValueError):archive.recover_restore_transactions(self.root)
        self.assertEqual((self.root/'system-preferences.json').read_bytes(),external)

class SystemController004(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=BUILD);self.root=Path(self.temp.name);self.camera=CameraGuard()
        self.controller=system.SystemController(self.root,self.camera);self.power=[];self.controller.powerScheduled.connect(lambda:self.power.append(True))
    def tearDown(self):
        self.controller.close();APP.processEvents();self.assertTrue(self.root.resolve().is_relative_to(BUILD.resolve()));self.temp.cleanup()
    def settle(self):wait_for(lambda:not self.controller.busy)
    def test_offline_native_service_never_claims_power_or_preference_success(self):
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request',side_effect=OSError('offline')):
            self.controller.refresh();self.settle();self.assertFalse(self.controller.nativeReady)
            self.controller.systemAction('reboot');self.settle();self.assertFalse(self.power)
            self.controller.setPreference('developer_mode',False);self.settle()
        self.assertTrue(self.controller.preferences['developer_mode']);self.assertFalse((self.root/'system-preferences.json').exists());self.assertFalse(self.camera.active)
    def test_power_requires_explicit_scheduled_readback_and_releases_guard(self):
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request',return_value={'scheduled':False}):
            self.controller.systemAction('shutdown');self.settle();self.assertFalse(self.power);self.assertIn('未确认',self.controller.message)
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request',return_value={'scheduled':True}):
            self.controller.systemAction('shutdown');self.settle()
        self.assertEqual(len(self.power),1);self.assertFalse(self.camera.active);self.assertTrue(self.camera.calls[0][2])
    def test_native_access_flag_stays_authoritative_if_ui_file_cannot_be_saved(self):
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request',return_value={'developer_mode':False}),patch.object(system,'_atomic',side_effect=OSError('disk unavailable')):
            self.controller.setPreference('developer_mode',False);self.settle()
        self.assertFalse(self.controller.preferences['developer_mode']);self.assertIn('未保存',self.controller.message)
        self.controller._preferences['developer_mode']=True
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request',return_value={'native':True,'developer_mode':False}):
            self.controller.refresh();self.settle()
        self.assertTrue(self.controller.nativeReady);self.assertFalse(self.controller.preferences['developer_mode'])
    def test_manual_utc_is_strict_and_must_read_back(self):
        with patch.object(system.platform,'system',return_value='FreeBSD'),patch.object(system,'service_request') as request:
            self.controller.setUtcTime('2026-11-06 01:02:03');self.assertFalse(self.controller.busy);request.assert_not_called()
            request.return_value={'clock_utc':'2026-11-06T01:02:03Z'}
            self.controller.setUtcTime('2026-11-06T01:02:03Z');self.settle();self.assertEqual(self.controller.state['clock_utc'],'2026-11-06T01:02:03Z')
            request.return_value={'clock_utc':'2026-11-07T01:02:03Z'}
            self.controller.setUtcTime('2026-11-06T01:02:03Z');self.settle();self.assertIn('不一致',self.controller.message)
        self.assertFalse(self.camera.active)
    def test_running_tasks_reject_maintenance_before_file_write(self):
        tasks=TaskGuard(False);self.controller.attachTasks(tasks);self.controller._backup=({'restorable':True},config_documents())
        self.controller.restoreBackup();self.assertFalse(self.controller.busy);self.assertEqual(tasks.calls,[True,False]);self.assertFalse(self.camera.calls)
        self.assertFalse((self.root/'system-preferences.json').exists())
    def test_maintenance_holds_admission_until_async_result_and_releases_on_error(self):
        tasks=TaskGuard();self.controller.attachTasks(tasks);gate=threading.Event()
        def action():gate.wait(2);raise OSError('injected')
        self.controller._guarded_submit('system',action,'test')
        self.assertTrue(tasks.blocked);self.assertTrue(self.camera.active)
        gate.set();self.settle();self.assertFalse(tasks.blocked);self.assertFalse(self.camera.active)
    def test_real_camera_authority_rejects_capture_and_parameters_during_maintenance(self):
        from camera.camerad.service import CameraService
        from controller import CameraController
        service=CameraService(self.root/'camera')
        camera=CameraController(lambda:service,backend='reference',state_path=self.root/'ui/device-ui.json',poll_ms=20)
        gate=threading.Event()
        try:
            wait_for(lambda:camera.connectionState=='ready');self.controller.camera=camera
            confirmed=dict(camera.confirmedSettings)
            def action():gate.wait(2);return {}
            self.assertTrue(self.controller._guarded_submit('system',action,'test maintenance'))
            self.assertEqual(camera.settingsView['phase'],'maintenance');self.assertFalse(camera.canCapture)
            self.assertEqual(camera.nudgeSettings('exposure',5),-1)
            self.assertFalse(camera.captureWithId(str(uuid.uuid4())))
            self.assertEqual(camera.beginTemporary({'requested_analogue_gain':2.},'fn1'),'')
            self.assertEqual(camera.confirmedSettings,confirmed);self.assertEqual(camera.lastRequestId,'')
            gate.set();self.settle();self.assertTrue(camera.settingsView['canEdit'])
        finally:
            gate.set();self.controller._end_guard();self.controller.camera=self.camera;camera.shutdown()
            deferred=getattr(camera,'_deferred_close_thread',None)
            if deferred:deferred.join(3)
    def test_changed_inspection_path_cannot_publish_stale_cached_result(self):
        target=self.root/'update.tar.gz';digest=update_tar(target);original=system.inspect_update;gate=threading.Event()
        def blocked(*args):gate.wait(2);return original(*args)
        with patch.object(system,'inspect_update',side_effect=blocked):
            self.controller.inspectUpdate(str(target),digest);self.controller.clearInspection('update');gate.set();self.settle()
        self.assertFalse(self.controller.updateInfo)
        self.controller.inspectUpdate(str(target),digest);self.settle();self.assertTrue(self.controller.updateInfo)
        self.controller.inspectUpdate(str(target),'0'*64);self.settle();self.assertFalse(self.controller.updateInfo)
    def test_restore_notifies_real_reload_and_does_not_apply_saved_camera_bank(self):
        shell=ShellController(self.root/'shell-preferences.json');workspace=ShootingWorkspace(self.root)
        applied=[];workspace.profileApplied.connect(lambda value:applied.append(value))
        self.controller._backup=({'restorable':True},config_documents());self.controller._preferences['developer_mode']=False
        def reload():
            self.assertTrue(shell.reloadPreferences());self.assertTrue(workspace.reloadFromDisk());self.controller.markReloaded()
        self.controller.restored.connect(reload);self.controller.restoreBackup();self.settle()
        self.assertFalse(self.controller.restartRequired);self.assertFalse(applied)
        self.assertTrue(shell.gridEnabled);self.assertEqual(shell.theme,'dark');self.assertEqual(workspace._state.profiles['C1']['camera']['requested_exposure_us'],12500)
        self.assertFalse(self.controller.preferences['developer_mode']);self.assertFalse(self.camera.active)
    def test_restore_without_reload_handler_is_explicit_about_pending_ui_refresh(self):
        self.controller._backup=({'restorable':True},config_documents());self.controller.restoreBackup();self.settle()
        self.assertTrue(self.controller.restartRequired);self.assertTrue(self.controller.markReloaded());self.assertFalse(self.controller.restartRequired)
    def test_idle_first_wake_input_is_consumed_and_no_sleep_during_maintenance(self):
        self.controller._preferences['idle_seconds']=30;self.controller._last_input=time.monotonic()-40
        self.controller._busy=True;self.controller._idle();self.assertFalse(self.controller.screenAsleep)
        self.controller._busy=False;self.controller._idle();self.assertTrue(self.controller.screenAsleep)
        self.assertTrue(self.controller.eventFilter(None,QEvent(QEvent.TouchBegin)));self.assertFalse(self.controller.screenAsleep)
        self.assertFalse(self.controller.eventFilter(None,QEvent(QEvent.TouchBegin)))

if __name__=='__main__':unittest.main()

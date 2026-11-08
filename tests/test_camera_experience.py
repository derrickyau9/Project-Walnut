"""Critical integration checks for frame transport, real pixels and app rollback."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
try:
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtGui import QGuiApplication, QImage, QColor
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('PyQt5 is required for the camera experience checks')

from camera.camerad.service import CameraService
from camera.dng.inspect import inspect_dng
from controller import CameraController
from image_pipeline import FrameStore, render_retained, export_review_jpeg
from camera_capabilities import capability_model
from tools.camera_experience_bundle import build

spec=importlib.util.spec_from_file_location('camera_app_installer',ROOT/'system/freebsd/raspberrypi/deploy_camera_app.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class CameraExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.controllers=[];self.services=[]

    def tearDown(self):
        for controller in self.controllers:controller.shutdown()
        for service in self.services:
            try:service.close()
            except Exception:pass
        self.temp.cleanup()

    def wait(self,condition,seconds=8):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if condition():return
            QTest.qWait(10)
        self.fail('operation did not reach its expected state')

    def controller(self):
        service=CameraService(self.root/'camera')
        controller=CameraController(lambda:service,backend='reference',state_path=self.root/'camera/device-ui.json')
        self.controllers.append(controller)
        self.wait(lambda:controller.connectionState=='ready' and controller._pending==0)
        return controller

    def photograph(self):
        service=CameraService(self.root/'source');self.services.append(service)
        lease=service.acquire('pixel-check');identity=str(uuid.uuid4())
        service.capture(dict(service.get_state()['settings'],request_id=identity,
                             token=lease['token'],control_epoch=lease['epoch']))
        self.wait(lambda:service.query_capture(identity)['status']=='complete' and service.get_state()['camera_state']=='idle')
        service.release(lease['token'],lease['epoch'])
        return service,identity

    def test_latest_frame_store_is_bounded_and_cleared(self):
        store=FrameStore()
        for number in range(120):
            image=QImage(64,48,QImage.Format_RGB32);image.fill(QColor(number%255,0,0))
            self.assertTrue(store.publish('live',image).startswith('image://daosframes/live/'))
        self.assertLessEqual(store.retained_bytes,64*48*4)
        self.assertEqual(119,store.image('live').pixelColor(0,0).red())
        store.clear('live');self.assertTrue(store.image('live').isNull())

    def test_original_review_reads_real_source_coordinates(self):
        service,identity=self.photograph()
        tile=render_retained(service,identity,region=(30,20,17,13))
        self.assertEqual((17,13),(tile['image'].width(),tile['image'].height()))
        with service.open_capture(identity) as stream:
            info=inspect_dng(stream);stream.seek(info['strip_offsets'][0]+(20*128+30)*2)
            red=struct.unpack('<H',stream.read(2))[0]
        expected=round((max(0,red-64)/16319)**(1/2.2)*255)
        self.assertEqual(expected,tile['image'].pixelColor(0,0).red())
        self.assertEqual((30,20),(tile['x'],tile['y']))

    def test_jpeg_proof_does_not_change_or_overwrite_raw(self):
        service,identity=self.photograph()
        path=service.root/'photos'/(identity+'.dng');before=hashlib.sha256(path.read_bytes()).hexdigest()
        destination=self.root/'proof.jpg'
        receipt=export_review_jpeg(service,identity,destination,{'exposure':.5,'rotation':90})
        self.assertFalse(receipt['calibrated']);self.assertTrue(destination.read_bytes().startswith(b'\xff\xd8'))
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(Exception):export_review_jpeg(service,identity,destination)

    def test_workflow_exclusive_lease_restores_and_unknown_holds_same_id(self):
        controller=self.controller();identity=str(uuid.uuid4())
        observed=[]
        def job(client):
            lease=client.acquire('workflow-check')
            observed.append(client.get_state()['control']['owner'])
            time.sleep(.04)
            client.release(lease['token'],lease['epoch'])
            return {'status':'unknown','steps':[{'request_id':identity,'status':'unknown'}]}
        self.assertTrue(controller.runExclusiveWorkflow(job))
        self.assertFalse(controller.canCapture)
        self.wait(lambda:not controller.workflowActive and controller._pending==0)
        self.assertEqual(['workflow-check'],observed)
        self.assertEqual('on-device-ui',controller._client.get_state()['control']['owner'])
        self.assertEqual('unknown',controller.captureState)
        self.assertEqual(identity,controller.lastRequestId)
        self.assertEqual(identity,json.loads((self.root/'camera/device-ui.json').read_text())['request_id'])
        self.assertFalse(controller.canCapture)

    def test_stale_preview_worker_cannot_reappear(self):
        controller=self.controller();controller.togglePreview()
        self.wait(lambda:bool(controller.previewUrl))
        controller._connection='disconnected';controller._invalidate_preview()
        QTest.qWait(90)
        self.assertEqual('',controller.previewUrl)
        self.assertTrue(controller.frames.image('live').isNull())

    def test_workflow_uuid_survives_control_reacquisition_failure(self):
        from camera.camerad.service import CameraError
        service=CameraService(self.root/'camera')
        class FailReacquire:
            attempts=0
            def __getattr__(self,name):return getattr(service,name)
            def acquire(self,owner):
                if owner=='on-device-ui':
                    self.attempts+=1
                    if self.attempts==2:raise CameraError('control_busy','another owner')
                return service.acquire(owner)
        client=FailReacquire()
        controller=CameraController(lambda:client,backend='reference',state_path=self.root/'camera/device-ui.json')
        self.controllers.append(controller)
        self.wait(lambda:controller.connectionState=='ready' and controller._pending==0)
        identity=str(uuid.uuid4())
        self.assertTrue(controller.runExclusiveWorkflow(lambda ignored:{'status':'unknown',
            'steps':[{'request_id':identity,'status':'unknown'}]}))
        self.wait(lambda:not controller.workflowActive)
        self.assertEqual(identity,controller.lastRequestId)
        self.assertEqual('unknown',controller.captureState)
        self.assertEqual('disconnected',controller.connectionState)
        controller.reconnect()
        self.wait(lambda:controller.connectionState=='ready' and controller._pending==0)
        self.assertEqual(identity,controller.lastRequestId)
        self.assertFalse(controller.canCapture)

    def test_missing_hardware_does_not_enable_video_or_af(self):
        capabilities=capability_model(CameraService.capabilities())
        self.assertTrue(capabilities['simulated'])
        for name in ('video','focus','autofocus','iso','level','burst'):
            self.assertFalse(capabilities[name]['available'])
            self.assertTrue(capabilities[name]['reason'])

    def test_application_install_and_rollback_are_hash_bound(self):
        archive=self.root/'application.tar.gz';receipt=build(archive,'camera-experience-001')
        stage=self.root/'stage';stage.mkdir()
        old=stage/'opt/hasslefree/ui/on-device/main.py';old.parent.mkdir(parents=True);old.write_bytes(b'original application')
        work=self.root/'update';work.mkdir()
        incoming,manifest=installer.unpack(archive,receipt['archive_sha256'],work)
        self.assertFalse(any(entry['path'].startswith('boot/') for entry in manifest['files']))
        self.assertTrue(any(entry['path'].endswith('LICENSE.Apache-2.0') for entry in manifest['files']))
        installer.install_files(stage,incoming,manifest,work)
        self.assertNotEqual(b'original application',old.read_bytes())
        installer.rollback_files(stage,work)
        self.assertEqual(b'original application',old.read_bytes())
        self.assertFalse((stage/'opt/hasslefree/ui/on-device/image_pipeline.py').exists())
        self.assertFalse(installer.allowed('opt/hasslefree/ui/on-device/../../boot/loader.conf'))
        self.assertFalse(installer.allowed('boot/kernel/kernel'))

    def test_interrupted_preparation_leaves_previous_entry_usable(self):
        archive=self.root/'application.tar.gz';receipt=build(archive,'camera-experience-001')
        stage=self.root/'stage';stage.mkdir()
        old=stage/'opt/hasslefree/ui/on-device/main.py';old.parent.mkdir(parents=True)
        old.write_bytes(b'previous complete application')
        work=self.root/'update';work.mkdir()
        incoming,manifest=installer.unpack(archive,receipt['archive_sha256'],work)
        original_write=installer.write_file
        def interrupt(path,source,mode=0o644):
            if path.name=='performance.py':raise OSError('interrupted preparation')
            return original_write(path,source,mode)
        with patch.object(installer,'write_file',side_effect=interrupt):
            with self.assertRaises(OSError):installer.install_files(stage,incoming,manifest,work)
        self.assertEqual(b'previous complete application',old.read_bytes())
        self.assertFalse((stage/installer.ACTIVE_PATH).exists())
        self.assertFalse((stage/'opt/hasslefree/releases/camera-experience-001/release-ready.json').exists())
        installer.rollback_files(stage,work)
        self.assertEqual(b'previous complete application',old.read_bytes())


if __name__=='__main__':unittest.main()

"""Shared handoff admission, recovery identity preservation and actual QML gates."""
from pathlib import Path
import copy
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
ROOT=Path(__file__).resolve().parents[1];UI=ROOT/'ui/on-device';sys.path.insert(0,str(UI))
from PyQt5.QtCore import QObject,QUrl,pyqtSignal,pyqtProperty
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtQuick import QQuickItem
from PyQt5.QtTest import QTest,QSignalSpy
from camera.camerad.service import CameraService
from controller import CameraController
from workflow_helpers import CameraWorkflow


def wait(predicate):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if predicate():return
        QTest.qWait(5)
    raise AssertionError('Workflow readiness did not settle')


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance()or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=CameraService(self.root/'camera')
        self.camera=CameraController(lambda:self.service,backend='reference',state_path=self.root/'ui.json')
        wait(lambda:self.camera.connectionState=='ready' and not self.camera._pending)
        self.camera._timer.stop()
    def tearDown(self):self.camera.shutdown();self.temp.cleanup()

    def test_query_is_pure_and_keeps_every_original_handoff_gate(self):
        camera=self.camera;self.assertTrue(camera.canRunExclusiveWorkflow())
        changed=QSignalSpy(camera.changed)
        guards={'_pending':1,'_maintenance_token':'maintenance','_temporary':{'held':True},'_temporary_recovery':True,
                '_state_fault':True,'_workflow_active':True,'_closed':True,'_connection':'disconnected',
                '_settings_active':True,'_settings_queue':{'requested_exposure_us':11000},'_capture':'capturing'}
        for field,value in guards.items():
            old=getattr(camera,field)
            try:
                setattr(camera,field,value)
                with self.subTest(gate=field),patch.object(camera,'_submit')as submit:
                    self.assertFalse(camera.canRunExclusiveWorkflow())
                    self.assertFalse(camera.runExclusiveWorkflow(lambda ignored:None))
                    submit.assert_not_called();self.assertEqual(getattr(camera,field),value)
            finally:setattr(camera,field,old)
        self.assertEqual(len(changed),0)
        self.assertTrue(camera.canRunExclusiveWorkflow())
        self.assertFalse(camera.runExclusiveWorkflow(None))

    def test_original_unknown_uuid_is_admitted_but_pending_or_foreign_uuid_is_not(self):
        camera=self.camera;original=str(uuid.uuid4());old=camera._capture,camera._request
        try:
            camera._capture='unknown';camera._request=original
            self.assertFalse(camera.canCapture)
            self.assertFalse(camera.canRunExclusiveWorkflow())
            self.assertTrue(camera.canRunExclusiveWorkflow(original))
            self.assertFalse(camera.canRunExclusiveWorkflow(str(uuid.uuid4())))
            camera._pending=1
            self.assertFalse(camera.canRunExclusiveWorkflow(original))
            self.assertFalse(camera.runExclusiveWorkflow(lambda ignored:None,reconcile_request_id=original))
            self.assertEqual(camera.lastRequestId,original)
        finally:camera._pending=0;camera._capture,camera._request=old


class GateCamera(QObject):
    changed=pyqtSignal()
    def __init__(self):super().__init__();self.pending=False;self.unknown='';self.running=False;self.accept=True;self.calls=[]
    @pyqtProperty(bool,notify=changed)
    def workflowActive(self):return self.running
    def canRunExclusiveWorkflow(self,reconcile_request_id=None):
        return not self.pending and not self.running and (not self.unknown or reconcile_request_id==self.unknown)
    def runExclusiveWorkflow(self,function,**kwargs):
        self.calls.append(kwargs);return self.accept


class HelperReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance()or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.camera=GateCamera()
        self.workflow=CameraWorkflow(self.camera,self.temp.name)
        wait(lambda:self.workflow.available and not self.workflow._pending);self.workflow._timer.stop()
        self.identity=str(uuid.uuid4());self.workflow._job='existing-job'
        self.workflow._record={'job_id':'existing-job','status':'paused','plan':{'exposures_us':[10000]},
                               'steps':[{'step':0,'request_id':self.identity,'status':'unknown'}]}
    def tearDown(self):self.workflow.close();self.temp.cleanup()

    def test_busy_resume_keeps_record_uuid_and_intent_and_never_auto_retries(self):
        workflow=self.workflow;before=copy.deepcopy(workflow._record);cancel=workflow._cancel;desired=workflow.desired
        self.camera.pending=True;self.camera.changed.emit()
        self.assertFalse(workflow.canStart);self.assertFalse(workflow.canResume);self.assertFalse(workflow.canCancel)
        self.assertFalse(workflow.resume());self.assertFalse(workflow.cancel())
        self.assertEqual(workflow._record,before);self.assertEqual(workflow.jobId,'existing-job')
        self.assertIs(workflow._cancel,cancel);self.assertEqual(workflow.desired,desired);self.assertEqual(self.camera.calls,[])
        signals=QSignalSpy(workflow.changed)
        self.camera.pending=False;self.camera.changed.emit()
        self.assertTrue(workflow.canResume);self.assertGreater(len(signals),0)
        QTest.qWait(30);self.assertEqual(self.camera.calls,[])
        self.assertTrue(workflow.resume());self.assertEqual(self.camera.calls,[{'reconcile_request_id':self.identity}])

    def test_backend_refusal_after_query_preserves_paused_unknown_and_new_start_record(self):
        self.camera.accept=False
        for status in ('paused','unknown'):
            with self.subTest(status=status):
                self.workflow._record['status']=status;before=copy.deepcopy(self.workflow._record)
                cancel=self.workflow._cancel;desired=self.workflow.desired
                self.assertTrue(self.workflow.canResume);self.assertFalse(self.workflow.resume())
                self.assertEqual(self.workflow._record,before);self.assertIs(self.workflow._cancel,cancel)
                self.assertEqual(self.workflow.desired,desired)
                self.assertFalse(self.workflow.start({'exposures_us':[20000]}))
                self.assertEqual(self.workflow._record,before);self.assertEqual(self.workflow.jobId,'existing-job')

    def test_qml_disables_busy_resume_and_allows_only_original_unknown_reconciliation(self):
        workflow=self.workflow;workflow._record['status']='unknown'
        self.camera.unknown=self.identity;self.camera.pending=True
        class Apps(QObject):
            @pyqtProperty(QObject,constant=True)
            def workflow(self):return workflow
            @pyqtProperty('QVariantList',constant=True)
            def recipes(self):return []
            @pyqtProperty(str,constant=True)
            def selectedRecipeId(self):return 'selected'
            @pyqtProperty(QObject,constant=True)
            def media(self):return None
        apps=Apps();engine=QQmlApplicationEngine();warnings=[]
        engine.warnings.connect(lambda values:warnings.extend(v.toString()for v in values))
        engine.rootContext().setContextProperty('appsFixture',apps)
        engine.loadData(b'import QtQuick 2.15\nimport QtQuick.Window 2.15\nimport "."\nWindow {visible:true;width:800;height:600;RecipeWorkspace {anchors.fill:parent;apps:appsFixture}}',QUrl.fromLocalFile(str(UI/'workflow-gate-test.qml')))
        self.assertTrue(engine.rootObjects());window=engine.rootObjects()[0]
        def enabled(name):return window.findChild(QObject,name).property('enabled')
        try:
            QTest.qWait(20)
            self.assertFalse(enabled('workflowResumeButton'));self.assertFalse(enabled('workflowRunButton'))
            self.camera.pending=False;self.camera.changed.emit();QTest.qWait(20)
            self.assertTrue(enabled('workflowResumeButton'));self.assertTrue(enabled('workflowCancelButton'))
            self.assertFalse(enabled('workflowRunButton'),'Unknown capture must not allow a fresh plan')
            self.camera.unknown=str(uuid.uuid4());self.camera.changed.emit();QTest.qWait(20)
            self.assertFalse(enabled('workflowResumeButton'));self.assertFalse(enabled('workflowCancelButton'))
            self.assertEqual(workflow.steps[0]['requestId'],self.identity);self.assertEqual(self.camera.calls,[])
            self.assertEqual(warnings,[])
        finally:window.close();engine.deleteLater();QTest.qWait(10)


if __name__=='__main__':unittest.main()

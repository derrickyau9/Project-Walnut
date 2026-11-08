import os,sys,tempfile,time,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ui/on-device'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
from PyQt5.QtCore import QObject,pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtTest import QTest
from task_hub import TaskHub
from tests.test_camera_link_004 import Camera,Service
from camera_link import CameraLink

class Media(QObject):
    changed=pyqtSignal();jobs=[{'job_id':str(uuid.uuid4()),'kind':'export_jpeg','status':'paused','created':1,'done':0,'total':1,'progress':0}]
    message='';detailText='';activeJobId=''
    def refresh(self):pass
class Workflow(QObject):
    changed=pyqtSignal();active=False;jobId='camera-original';progress=0.;completed=0;total=2;errorText='';steps=[]
    jobs=[{'job_id':'camera-original','status':'unknown','error':'核实原UUID'}]
    def __init__(self):super().__init__();self.calls=[];self._record={'job_id':self.jobId,'plan':{'exposures_us':[100,100]}}
    def refresh(self):pass
    def selectJob(self,identity):self.calls.append(('select',identity));self.changed.emit()
    def resume(self):self.calls.append(('resume',self.jobId))

class HubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def test_three_sources_and_resume_routes_to_original_workflow_or_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            service=Service();link=CameraLink(Camera(),directory,client_getter=lambda:service);media=Media();workflow=Workflow();hub=TaskHub(media,workflow,link)
            transfer=link.create_transfer(str(uuid.uuid4()));hub.refresh()
            deadline=time.monotonic()+2
            while len(hub.jobs)<3 and time.monotonic()<deadline:QTest.qWait(10)
            try:
                self.assertEqual({'export_jpeg','workflow','transfer'},{row['kind'] for row in hub.jobs})
                hub.resume('workflow:camera-original');self.assertIn(('resume','camera-original'),workflow.calls)
                identity='transfer:'+transfer['transfer_id'];hub.pause(identity)
                deadline=time.monotonic()+2
                while link.transfer_status(transfer['transfer_id'])['status']!='paused' and time.monotonic()<deadline:QTest.qWait(10)
                self.assertEqual('paused',link.transfer_status(transfer['transfer_id'])['status']);self.assertEqual(0,service.opened)
                self.assertEqual(identity,hub.inspect_snapshot(identity)['job_id'])
            finally:hub.close();link.close()
if __name__=='__main__':unittest.main()

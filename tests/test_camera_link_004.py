"""No exposure retry, owner takeover and persisted verified binary transfers."""
import concurrent.futures
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtCore import QCoreApplication,QObject,pyqtSignal
from camera_link import CameraLink,CHUNK_SIZE
from daos.camera_link import CameraClient,LinkError

class Camera(QObject):
    connectionState='ready';captureState='idle';lastRequestId=''
    confirmedSettings={'requested_exposure_us':10000};settingsView={'canCapture':True}
    capabilities={'simulated':True}
    def __init__(self):super().__init__();self.requests=[]
    def captureWithId(self,identity,origin):self.requests.append(identity);self.lastRequestId=identity;return True
    def setSettings(self,patch,origin):return 1

class Service:
    def __init__(self):self.data=os.urandom(CHUNK_SIZE*2+19000);self.opened=0
    def get_library_asset(self,photo):return {'photo_uuid':photo,'library_id':'test','extension':'.dng'}
    def open_library_asset(self,photo):
        service=self
        class Stream(io.BytesIO):
            def close(self):
                if not self.closed:service.opened-=1
                super().close()
        self.opened+=1;return Stream(self.data)
    def query_capture(self,identity):return {'request_id':identity,'status':'unknown'}

class LocalTransport:
    def __init__(self,broker):self.broker=broker;self.break_after=None;self.reads=0
    def call(self,request):
        if request['method']=='transfer.getChunk':
            self.reads+=1
            if self.break_after is not None and self.reads>self.break_after:raise OSError('injected disconnected USB')
            return self.broker.read_chunk(request['params']['transfer_id'],request['params']['offset'])
        future=concurrent.futures.Future();self.broker._dispatch(request,future)
        return future.result(timeout=5)
    def close(self):pass

class LinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.camera=Camera();self.service=Service()
        self.broker=CameraLink(self.camera,self.root,client_getter=lambda:self.service)
        self.transport=LocalTransport(self.broker);self.client=CameraClient(self.transport)
    def tearDown(self):self.broker.close();self.temp.cleanup()
    def test_local_takeover_prevents_remote_exposure_and_query_does_not_reshoot(self):
        with self.assertRaises(ValueError):self.client.acquire()
        self.broker.setEnabled(True);self.client.acquire();original=str(uuid.uuid4())
        self.client.capture(original);self.broker.takeLocalControl()
        with self.assertRaises(ValueError):self.client.capture(str(uuid.uuid4()))
        self.assertEqual(original,self.client.query(original)['request_id'])
        self.assertEqual([original],self.camera.requests)
        old=self.client.session;self.client.acquire();self.assertNotEqual(old,self.client.session)
    def test_disconnect_resume_restart_sha_commit_and_no_overwrite(self):
        photo=str(uuid.uuid4());target=self.root/'export.dng';self.transport.break_after=1
        with self.assertRaises(OSError):self.client.download(photo,target)
        self.assertFalse(target.exists());self.assertEqual(CHUNK_SIZE,target.with_name('export.dng.part').stat().st_size)
        checkpoint=target.with_name('export.dng.transfer.json');identity=__import__('json').loads(checkpoint.read_text())['transfer_id']
        self.assertEqual(1,self.service.opened)
        self.broker.close();self.assertEqual(0,self.service.opened)
        self.broker=CameraLink(self.camera,self.root,client_getter=lambda:self.service)
        self.client=CameraClient(LocalTransport(self.broker))
        result=self.client.download(photo,target)
        self.assertEqual(self.service.data,target.read_bytes());self.assertEqual(hashlib.sha256(self.service.data).hexdigest(),result['sha256'])
        self.assertEqual('complete',self.broker.transfer_status(identity)['status']);self.assertEqual(0,self.service.opened)
        self.assertFalse(checkpoint.exists())
        with self.assertRaises(LinkError):self.client.download(photo,target)
    def test_source_changed_after_interruption_and_cancel_release_pin(self):
        photo=str(uuid.uuid4());transfer=self.broker.create_transfer(photo);identity=transfer['transfer_id']
        self.broker.read_chunk(identity,0)
        with self.assertRaises(ValueError):self.broker.commit_transfer(identity,'0'*64)
        self.assertEqual(1,self.service.opened)
        self.client.call('transfer.cancel',{'transfer_id':identity});self.assertEqual(0,self.service.opened)
        with self.assertRaises(ValueError):self.broker.read_chunk(identity,0)
        transfer=self.broker.create_transfer(photo);identity=transfer['transfer_id'];self.broker._close_stream(identity)
        self.service.data=b'x'*len(self.service.data)
        with self.assertRaises(ValueError):self.broker.read_chunk(identity,0)
        self.assertEqual(0,self.service.opened)
    def test_body_pause_keeps_checkpoint_and_explicit_sdk_resume_finishes(self):
        photo=str(uuid.uuid4());target=self.root/'paused.dng';self.transport.break_after=1
        with self.assertRaises(OSError):self.client.download(photo,target)
        import json
        identity=json.loads(target.with_name(target.name+'.transfer.json').read_text())['transfer_id']
        self.broker.control_transfer(identity,'pause');self.assertEqual(0,self.service.opened)
        with self.assertRaises(ValueError):self.broker.read_chunk(identity,CHUNK_SIZE)
        self.transport.break_after=None;self.client.download(photo,target)
        self.assertEqual(self.service.data,target.read_bytes());self.assertEqual('complete',self.broker.transfer_status(identity)['status'])

    def test_expired_pairing_code_notifies_ui_and_clears_display(self):
        from PyQt5.QtTest import QSignalSpy
        import time
        self.broker._pin='123456';self.broker._pin_expires=time.monotonic()-1;spy=QSignalSpy(self.broker.changed)
        self.broker._expire();self.assertEqual('',self.broker.pairingCode);self.assertEqual('',self.broker._pin);self.assertGreaterEqual(len(spy),1)

    def test_lost_commit_reply_resumes_published_target_without_redownload(self):
        broker=self.broker
        class LostCommit(LocalTransport):
            def __init__(self):super().__init__(broker);self.once=True;self.chunks=0
            def call(self,request):
                if request['method']=='transfer.getChunk':self.chunks+=1
                result=super().call(request)
                if request['method']=='transfer.commit' and self.once:
                    self.once=False;raise OSError('Commit succeeded but its reply was lost')
                return result
        transport=LostCommit();client=CameraClient(transport);photo=str(uuid.uuid4());target=self.root/'commit-boundary.dng'
        with self.assertRaises(OSError):client.download(photo,target)
        self.assertEqual(self.service.data,target.read_bytes());checkpoint=target.with_name(target.name+'.transfer.json');self.assertTrue(checkpoint.exists())
        chunks=transport.chunks;result=client.download(photo,target)
        self.assertEqual(chunks,transport.chunks);self.assertFalse(checkpoint.exists());self.assertEqual(result['bytes'],len(self.service.data));self.assertEqual(0,self.service.opened)

if __name__=='__main__':unittest.main()

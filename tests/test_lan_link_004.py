"""Actual paired HTTPS on loopback; no appliance, USB or LAN listener is used.

Qt dispatch remains on the main thread while every blocking SDK/HTTP request
runs in a Future. Asset bytes exercise transport integrity, not RAW decoding.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import inspect
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtGui import QColor,QGuiApplication,QImage,QPainter
from PyQt5.QtTest import QTest
from camera_link import CameraLink
from image_pipeline import FrameStore
from daos.camera_link import CameraClient,HTTPTransport,LinkError
from tests.test_camera_link_004 import Camera,Service


class PreviewCamera(Camera):
    def __init__(self):
        super().__init__();self.frames=FrameStore();self.patches=[];self.dispatch_threads=[]
        self.confirmedSettings={'requested_exposure_us':10000,'requested_analogue_gain':1.}
        self.settingsView={'canCapture':True,'canEdit':True}
        image=QImage(64,48,QImage.Format_RGB32);image.fill(QColor(32,96,160))
        painter=QPainter(image);painter.fillRect(32,0,32,48,QColor(208,144,64));painter.end()
        self.frames.publish('live',image)
    def setSettings(self,patch,origin):
        self.dispatch_threads.append(threading.get_ident());self.patches.append((dict(patch),origin))
        self.confirmedSettings.update(patch);return len(self.patches)
    def captureWithId(self,identity,origin):
        self.dispatch_threads.append(threading.get_ident());return super().captureWithId(identity,origin)


class LANLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QGuiApplication.instance()or QGuiApplication([])
        cls.artifacts=ROOT/'build/DaOS_Camera_004';cls.artifacts.mkdir(parents=True,exist_ok=True)

    def setUp(self):
        # Fail closed if the injectable listener contract ever disappears.
        signature=inspect.signature(CameraLink)
        self.assertIn('web_host',signature.parameters);self.assertIn('web_port',signature.parameters)
        self.temp=tempfile.TemporaryDirectory(prefix='lan-qa-',dir=self.artifacts)
        self.root=Path(self.temp.name);self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='lan-test-sdk')
        # Verify production discovery works without a test-only config repair.
        if os.name=='nt':
            environment=patch.dict(os.environ);environment.start();self.addCleanup(environment.stop)
            os.environ.pop('OPENSSL_CONF',None)
        self.camera=PreviewCamera();self.service=Service();self.clients=[];self.main_thread=threading.get_ident()
        self.broker=CameraLink(self.camera,self.root,client_getter=lambda:self.service,web_host='127.0.0.1',web_port=0)
        self.addCleanup(self.cleanup)
        self.broker.setEnabled(True);self.broker.startWeb()
        self.wait(lambda:not self.broker._web_starting,timeout=25)
        self.assertTrue(self.broker.webEnabled,self.broker.message);self.assertTrue(self.broker.pairingCode)
        self.assertEqual('127.0.0.1',self.broker._web.server_address[0])
        self.port=self.broker._web.server_address[1];self.assertGreater(self.port,0)
        self.origin='https://127.0.0.1:'+str(self.port);self.assertEqual(self.origin+'/',self.broker.webUrl)
        self.certificate=self.broker.root/'lan-cert.pem';self.key=self.broker.root/'lan-key.pem'
        self.context=ssl.create_default_context(cafile=str(self.certificate));self.context.check_hostname=False
        self.assertEqual(ssl.CERT_REQUIRED,self.context.verify_mode)

    def cleanup(self):
        for client in self.clients:client.close()
        self.broker.close();self.pool.shutdown(wait=True,cancel_futures=True)
        self.camera.deleteLater();QCoreApplication.processEvents();self.temp.cleanup()

    def wait(self,predicate,timeout=10):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            QCoreApplication.processEvents()
            if predicate():return
            QTest.qWait(5)
        self.fail('HTTPS/Qt request timed out: '+self.broker.message)

    def background(self,function,*args,**kwargs):
        future=self.pool.submit(function,*args,**kwargs);self.wait(future.done,timeout=20)
        return future.result()

    def request(self,method,path,body=None,headers=None,context=None):
        def send():
            connection=http.client.HTTPSConnection('127.0.0.1',self.port,context=context or self.context,timeout=8)
            try:
                connection.connect();tls=connection.sock.version();certificate=connection.sock.getpeercert(binary_form=True)
                supplied=dict(headers or {})
                if body is not None:supplied.setdefault('Content-Type','application/json')
                connection.request(method,path,body=json.dumps(body).encode()if body is not None else None,headers=supplied)
                response=connection.getresponse()
                return response.status,dict(response.getheaders()),response.read(),tls,hashlib.sha256(certificate).hexdigest()
            finally:connection.close()
        return self.background(send)

    def sdk(self):
        transport=self.background(lambda:HTTPTransport(self.origin,self.broker.pairingCode,cafile=self.certificate))
        client=CameraClient(transport);self.clients.append(client);return client

    def test_real_certificate_wrong_pin_pairing_cookie_csrf_and_origin(self):
        self.assertIn('BEGIN CERTIFICATE',self.certificate.read_text());self.assertTrue(self.key.stat().st_size>0)
        status,headers,data,tls,peer_sha=self.request('GET','/')
        self.assertEqual(200,status);self.assertIn(tls,{'TLSv1.2','TLSv1.3'});self.assertIn(b'DaOS',data)
        self.assertEqual('nosniff',headers['X-Content-Type-Options']);self.assertEqual('no-store',headers['Cache-Control'])
        der=ssl.PEM_cert_to_DER_cert(self.certificate.read_text());self.assertEqual(hashlib.sha256(der).hexdigest(),peer_sha)
        with self.assertRaises(ssl.SSLCertVerificationError):self.request('GET','/',context=ssl.create_default_context())
        status,_,_,_,_=self.request('GET','/preview.jpg');self.assertEqual(403,status)
        wrong='000000'if self.broker.pairingCode!='000000'else'111111'
        status,headers,data,_,_=self.request('POST','/pair',{'pin':wrong})
        self.assertEqual(400,status);self.assertIn('error',json.loads(data));self.assertNotIn('Set-Cookie',headers)
        status,headers,data,_,_=self.request('POST','/pair',{'pin':self.broker.pairingCode})
        self.assertEqual(200,status);paired=json.loads(data);self.assertTrue(paired['paired']);csrf=paired['csrf']
        for flag in ('HttpOnly','Secure','SameSite=Strict','Path=/'):self.assertIn(flag,headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';',1)[0];rpc={'method':'getState','params':{}}
        for request_headers in ({},{'Cookie':cookie},{'Cookie':cookie,'X-DaOS-CSRF':'wrong'},
                                {'Cookie':cookie,'X-DaOS-CSRF':csrf,'Origin':'https://untrusted.example'}):
            status,_,data,_,_=self.request('POST','/rpc',rpc,request_headers)
            self.assertEqual(400,status);self.assertIn('error',json.loads(data))
        status,_,data,_,_=self.request('POST','/rpc',rpc,{'Cookie':cookie,'X-DaOS-CSRF':csrf,'Origin':self.origin})
        self.assertEqual(200,status);self.assertTrue(json.loads(data)['result']['simulated'])
        (self.artifacts/'lan-https-report.json').write_text(json.dumps({'listener':'127.0.0.1','port':self.port,'tls_version':tls,'certificate_der_sha256':peer_sha,
            'system_trust_rejected_self_signed':True,'wrong_pin_rejected':True,'paired_cookie_flags_verified':True,'missing_wrong_csrf_rejected':True,'foreign_origin_rejected':True},indent=2))

    def test_https_sdk_state_configuration_original_uuid_and_local_takeover(self):
        client=self.sdk();state=self.background(client.state)
        self.assertEqual('ready',state['connection']);self.assertTrue(state['simulated']);self.assertFalse(state['remote_owner'])
        view_requests=[];self.broker.requestCaptureView.connect(lambda:view_requests.append(True))
        acquired=self.background(client.acquire);self.assertEqual(30,acquired['lease_seconds']);self.assertTrue(acquired['session']);self.assertEqual([True],view_requests)
        result=self.background(client.configure,requested_exposure_us=20000,requested_analogue_gain=2.)
        self.assertEqual(1,result['revision']);self.assertEqual('remote',self.camera.patches[-1][1])
        self.assertEqual(20000,self.background(client.state)['settings']['requested_exposure_us'])
        original=str(uuid.uuid4());(self.root/'persisted-capture-uuid.json').write_text(json.dumps({'request_id':original}))
        accepted=self.background(client.capture,original);self.assertTrue(accepted['accepted']);self.assertEqual(original,accepted['request_id'])
        self.assertEqual(original,self.background(client.query,original)['request_id']);self.assertEqual([original],self.camera.requests)
        self.assertTrue(all(identity==self.main_thread for identity in self.camera.dispatch_threads))
        previous_session=client.session;self.broker.takeLocalControl()
        self.assertFalse(self.background(client.state)['remote_owner'])
        with self.assertRaises(urllib.error.HTTPError)as refusal:self.background(client.capture,str(uuid.uuid4()))
        self.assertEqual(400,refusal.exception.code);refusal.exception.close()
        with self.assertRaises(urllib.error.HTTPError)as refusal:self.background(client.heartbeat)
        self.assertEqual(400,refusal.exception.code);refusal.exception.close()
        self.assertEqual(original,self.background(client.query,original)['request_id']);self.assertEqual([original],self.camera.requests)
        self.background(client.acquire);self.assertNotEqual(previous_session,client.session)
        self.assertTrue(self.background(client.release)['released']);self.assertEqual('',client.session)

    def test_https_preview_and_sdk_binary_download_hash_and_no_overwrite(self):
        client=self.sdk()
        def preview():
            with client.transport.opener.open(self.origin+'/preview.jpg',timeout=8)as response:
                return response.headers.get_content_type(),response.read(1024*1024)
        kind,data=self.background(preview);self.assertEqual('image/jpeg',kind);self.assertLess(len(data),65536)
        image=QImage.fromData(data,'JPEG');self.assertFalse(image.isNull());self.assertEqual((64,48),(image.width(),image.height()))
        for x,expected in ((10,(32,96,160)),(53,(208,144,64))):
            color=image.pixelColor(x,24)
            for actual,wanted in zip((color.red(),color.green(),color.blue()),expected):self.assertLessEqual(abs(actual-wanted),5)
        self.assertTrue(image.save(str(self.artifacts/'lan-live-preview-fixture.png')))
        photo=str(uuid.uuid4());destination=self.root/'downloaded-asset.dng';progress=[]
        result=self.background(client.download,photo,destination,progress=lambda done,total:progress.append((done,total)))
        digest=hashlib.sha256(self.service.data).hexdigest();self.assertEqual(digest,result['sha256']);self.assertEqual(len(self.service.data),result['bytes'])
        self.assertEqual(self.service.data,destination.read_bytes());self.assertEqual(0,self.service.opened)
        self.assertGreaterEqual(len(progress),3);self.assertEqual((len(self.service.data),len(self.service.data)),progress[-1])
        self.assertFalse(destination.with_name(destination.name+'.part').exists());self.assertFalse(destination.with_name(destination.name+'.transfer.json').exists())
        with self.broker._db()as db:row=db.execute('SELECT * FROM transfers WHERE photo=?',(photo,)).fetchone()
        status=self.background(client.call,'transfer.status',{'transfer_id':row['id']})
        self.assertEqual('complete',status['status']);self.assertEqual(digest,status['sha256'])
        with self.assertRaises(LinkError):self.background(client.download,photo,destination)
        self.assertEqual(digest,hashlib.sha256(destination.read_bytes()).hexdigest());self.assertEqual([],self.camera.requests)


if __name__=='__main__':unittest.main()

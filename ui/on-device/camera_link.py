"""Controlled camera broker shared by USB stdio, the SDK and paired LAN clients.

The broker proxies the existing UI controller. It never starts camerad or owns a
second native lease. Binary preview/assets use separate length-bounded routes.
"""
from __future__ import annotations
from concurrent.futures import Future,ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import base64
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
import uuid

from PyQt5.QtCore import QObject,QEvent,QTimer,QBuffer,QIODevice,pyqtProperty,pyqtSignal,pyqtSlot

MAX_CONTROL=65536
MAX_RESPONSE=2*1024*1024
CHUNK_SIZE=256*1024


def canonical(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError('需要规范 UUID')
    return value

def peer_uid(connection):
    if hasattr(socket,'SO_PEERCRED'):
        import struct
        return struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
    if hasattr(connection,'getpeereid'):return connection.getpeereid()[0]
    if sys.platform.startswith(('freebsd','netbsd','openbsd')):
        import ctypes
        uid=ctypes.c_uint32();gid=ctypes.c_uint32()
        function=ctypes.CDLL(None,use_errno=True).getpeereid
        function.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32)]
        function.restype=ctypes.c_int
        if function(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid))!=0:raise OSError(ctypes.get_errno(),'getpeereid failed')
        return uid.value
    raise OSError('The platform cannot authenticate a local camera-link peer')


class CameraLink(QObject):
    changed=pyqtSignal()
    _request=pyqtSignal(object,object)
    requestCaptureView=pyqtSignal()
    _webDone=pyqtSignal(object,object)

    def __init__(self,camera,data_root,*,catalog=None,tasks=None,client_getter=None,parent=None,web_host='0.0.0.0',web_port=8443):
        super().__init__(parent)
        self.camera=camera;self.root=Path(data_root)/'link';self.root.mkdir(parents=True,exist_ok=True)
        self.catalog=catalog;self.tasks=tasks;self.client_getter=client_getter
        if web_host not in ('0.0.0.0','127.0.0.1') or type(web_port) is not int or not 0<=web_port<=65535:raise ValueError('联机监听配置无效')
        self._web_host=web_host;self._web_port=web_port
        self._enabled=False;self._owner='';self._expires=0.;self._message='联机控制未启用'
        self._pin='';self._pin_expires=0.;self._tokens={};self._failures={}
        self._web=None;self._web_thread=None;self._socket=None;self._socket_thread=None
        self._web_url='';self._closed=False;self._lock=threading.RLock();self._auth_lock=threading.RLock();self._reap_pending=False
        self._executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='daos-link')
        self._streams={};self._web_starting=False
        self._work_gate=threading.BoundedSemaphore(32)
        self._request.connect(self._dispatch)
        self._webDone.connect(self._web_done)
        self.db_path=self.root/'transfers.sqlite3'
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS transfers(id TEXT PRIMARY KEY,photo TEXT,library TEXT,bytes INTEGER,sha256 TEXT,offset INTEGER,status TEXT,created REAL)')
        self._timer=QTimer(self);self._timer.setInterval(1000);self._timer.timeout.connect(self._expire);self._timer.start()

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.db_path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @pyqtProperty(bool,notify=changed)
    def enabled(self):return self._enabled
    @pyqtProperty(bool,notify=changed)
    def webEnabled(self):return self._web is not None
    @pyqtProperty(str,notify=changed)
    def owner(self):return '远程客户端' if self._owner else ''
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message
    @pyqtProperty(str,notify=changed)
    def pairingCode(self):return self._pin if time.monotonic()<self._pin_expires else ''
    @pyqtProperty(str,notify=changed)
    def webUrl(self):return self._web_url
    @pyqtProperty(str,notify=changed)
    def socketPath(self):
        if os.name=='nt':return str(Path('F:/tmp')/('daos-link-'+hashlib.sha256(str(self.root).encode()).hexdigest()[:12]+'.sock'))
        return str(self.root.parent/'camera-link.sock')

    def eventFilter(self,watched,event):
        if self._owner and event.type() in (QEvent.MouseButtonPress,QEvent.TouchBegin,QEvent.KeyPress):
            self.takeLocalControl()
        return False

    @pyqtSlot(bool)
    def setEnabled(self,enabled):
        self._enabled=bool(enabled)
        if not enabled:self.takeLocalControl();self.stopWeb()
        self._message='联机控制已启用，本机操作随时接管' if enabled else '联机控制已关闭'
        self.changed.emit()

    @pyqtSlot()
    def takeLocalControl(self):
        if self._owner:
            self._owner='';self._expires=0.;self._message='本机已接管，原拍摄只查询不重拍';self.changed.emit()

    def _expire(self):
        if self._owner and time.monotonic()>self._expires:self.takeLocalControl()
        with self._auth_lock:
            now=time.monotonic()
            self._tokens={key:value for key,value in self._tokens.items() if value>now}
            if self._pin and now>self._pin_expires:self._pin='';self.changed.emit()
        if not self._closed and not self._reap_pending:
            self._reap_pending=True
            def reap():
                try:
                    with self._lock:
                        for identity,entry in list(self._streams.items()):
                            if time.monotonic()-entry['access']>120:self._close_stream(identity)
                finally:self._reap_pending=False
            self._executor.submit(reap)

    def call(self,request,*,trusted_usb=False,timeout=12):
        if not isinstance(request,dict) or set(request)-{'id','method','params','session'}:raise ValueError('请求字段无效')
        if len(json.dumps(request).encode())>MAX_CONTROL:raise ValueError('控制消息过大')
        future=Future();self._request.emit(dict(request,trusted_usb=trusted_usb),future)
        return future.result(timeout=timeout)

    def _snapshot(self):
        return {'connection':self.camera.connectionState,'capture_status':self.camera.captureState,
                'request_id':self.camera.lastRequestId,'settings':dict(self.camera.confirmedSettings),
                'settings_view':dict(getattr(self.camera,'settingsView',{})),
                'capabilities':dict(self.camera.capabilities),'remote_owner':bool(self._owner),
                'simulated':bool(self.camera.capabilities.get('simulated')),'software_version':'camera-experience-004'}

    @pyqtSlot(object,object)
    def _dispatch(self,request,future):
        try:
            method=request.get('method');params=request.get('params',{})
            if not isinstance(params,dict):raise ValueError('参数必须为对象')
            if method in ('getState','capabilities'):
                value=self._snapshot() if method=='getState' else dict(self.camera.capabilities)
            elif method=='session.acquire':
                if not self._enabled:raise ValueError('请先在机身启用联机控制')
                if self._owner and request.get('session')!=self._owner:raise ValueError('另一个客户端正在控制相机')
                self._owner=self._owner or secrets.token_urlsafe(24)
                self._expires=time.monotonic()+30;value={'session':self._owner,'lease_seconds':30}
                self.requestCaptureView.emit();self._message='远程控制中，本机触摸可接管';self.changed.emit()
            elif method=='session.release':
                self._authorize(request);self.takeLocalControl();value={'released':True}
            elif method=='session.heartbeat':
                self._authorize(request);self._expires=time.monotonic()+30;value={'lease_seconds':30}
            elif method=='configure':
                self._authorize(request)
                value={'revision':self.camera.setSettings(params,'remote'),'state':dict(self.camera.settingsView)}
            elif method=='capture':
                self._authorize(request);identity=canonical(params.get('request_id'))
                if not hasattr(self.camera,'captureWithId'):raise ValueError('相机尚未提供受控拍摄接口')
                accepted=self.camera.captureWithId(identity,'remote')
                value={'request_id':identity,'accepted':bool(accepted),'state':self._snapshot()}
            elif method=='query':
                identity=canonical(params.get('request_id'))
                self._background(future,lambda:self.client_getter().query_capture(identity));return
            elif method=='listPhotos':
                if self.catalog is None:raise ValueError('照片库尚未就绪')
                self._background(future,lambda:self.catalog.page(self.catalog.active_library,params.get('query',{}),limit=min(60,max(1,int(params.get('limit',48)))),cursor=params.get('cursor') or ''));return
            elif method=='transfer.create':
                self._background(future,lambda:self.create_transfer(canonical(params.get('photo_uuid'))));return
            elif method=='transfer.status':
                self._background(future,lambda:self.transfer_status(canonical(params.get('transfer_id'))));return
            elif method=='transfer.cancel':
                identity=canonical(params.get('transfer_id'))
                self._background(future,lambda:self.control_transfer(identity,'cancel'));return
            elif method in ('transfer.pause','transfer.resume'):
                identity=canonical(params.get('transfer_id'))
                self._background(future,lambda:self.control_transfer(identity,method.split('.')[1]));return
            elif method=='transfer.commit':
                self._background(future,lambda:self.commit_transfer(canonical(params.get('transfer_id')),params.get('sha256')));return
            elif method=='tasks':value=list(self.tasks.jobs) if self.tasks else []
            elif method=='task.inspect':
                if self.tasks is None:raise ValueError('任务服务尚未就绪')
                if str(params.get('job_id','')).startswith(('workflow:','transfer:')) and hasattr(self.tasks,'inspect_snapshot'):
                    value=self.tasks.inspect_snapshot(params['job_id']);future.set_result(value);return
                identity=canonical(params.get('job_id'))
                def inspect():
                    with self.catalog.db() as db:
                        row=db.execute('SELECT job_id,kind,status,stage,error,done,total,progress FROM jobs WHERE job_id=?',(identity,)).fetchone()
                        items=db.execute('SELECT ordinal,photo_uuid,status,error FROM job_items WHERE job_id=? AND ordinal>? ORDER BY ordinal LIMIT 50',(identity,int(params.get('after',-1)))).fetchall()
                    if row is None:raise ValueError('任务不存在')
                    return dict(row,items=[dict(item) for item in items],next_ordinal=items[-1]['ordinal'] if len(items)==50 else None)
                self._background(future,inspect);return
            elif method in ('task.pause','task.resume','task.cancel'):
                self._authorize(request)
                if self.tasks is None:raise ValueError('任务服务尚未就绪')
                identity=params.get('job_id')
                if isinstance(identity,str) and identity.startswith(('workflow:','transfer:')) and hasattr(self.tasks,'inspect_snapshot'):self.tasks.inspect_snapshot(identity)
                else:identity=canonical(identity)
                getattr(self.tasks,method.split('.')[1])(identity)
                value={'job_id':identity,'control_requested':method.split('.')[1]}
            else:raise ValueError('不支持的联机操作')
            future.set_result(value)
        except Exception as error:future.set_exception(error)

    def _authorize(self,request):
        if not self._enabled or not self._owner or not hmac.compare_digest(request.get('session',''),self._owner) or time.monotonic()>self._expires:
            raise ValueError('远程控制权无效或已由本机接管')
        self._expires=time.monotonic()+30

    def _background(self,future,function):
        if not self._work_gate.acquire(blocking=False):raise ValueError('后台联机队列已满，请稍后重试')
        task=self._executor.submit(function)
        def done(value):
            try:
                try:future.set_result(value.result())
                except Exception as error:future.set_exception(error)
            finally:self._work_gate.release()
        task.add_done_callback(done)

    def create_transfer(self,photo):
        if self.client_getter is None:raise ValueError('资产服务未连接')
        client=self.client_getter();asset=client.get_library_asset(photo)
        stream=client.open_library_asset(photo)
        try:
            value=hashlib.sha256();count=0
            while True:
                block=stream.read(CHUNK_SIZE)
                if not block:break
                value.update(block);count+=len(block)
            stream.seek(0)
        except BaseException:
            stream.close();raise
        identity=str(uuid.uuid4());digest=value.hexdigest()
        with self._lock:
            if len(self._streams)>=8:
                stream.close();raise ValueError('同时传输最多8项，请完成或取消已有传输')
            self._streams[identity]={'stream':stream,'access':time.monotonic()}
            try:
                with self._db() as db:
                    db.execute('INSERT INTO transfers VALUES(?,?,?,?,?,?,?,?)',
                               (identity,photo,asset.get('library_id',''),count,digest,0,'ready',time.time()))
            except BaseException:self._close_stream(identity);raise
        return {'transfer_id':identity,'photo_uuid':photo,'bytes':count,'sha256':digest,'chunk_bytes':CHUNK_SIZE,'extension':asset.get('extension','.dng')}

    def _close_stream(self,identity):
        entry=self._streams.pop(identity,None)
        if entry:entry['stream'].close()

    def commit_transfer(self,identity,digest):
        with self._lock:
            row=self.transfer_status(identity)
            if row['status'] not in ('awaiting_commit','complete') or not isinstance(digest,str) or not hmac.compare_digest(digest,row['sha256']):
                raise ValueError('请先接收全部字节并核对SHA256')
            self._close_stream(identity)
            with self._db() as db:db.execute("UPDATE transfers SET status='complete' WHERE id=?",(identity,))
        return {'transfer_id':identity,'complete':True,'sha256':digest}

    def transfer_status(self,identity):
        with self._db() as db:row=db.execute('SELECT * FROM transfers WHERE id=?',(identity,)).fetchone()
        if row is None:raise ValueError('传输记录不存在')
        return dict(row)

    def control_transfer(self,identity,action):
        canonical(identity)
        if action not in ('pause','resume','cancel'):raise ValueError('传输控制无效')
        state={'pause':'paused','resume':'ready','cancel':'cancelled'}[action]
        with self._lock:
            row=self.transfer_status(identity)
            if row['status'] in ('complete','cancelled'):raise ValueError('传输已结束')
            if action in ('pause','cancel'):self._close_stream(identity)
            with self._db() as db:db.execute('UPDATE transfers SET status=? WHERE id=?',(state,identity))
        return {'transfer_id':identity,'status':state}

    def read_chunk(self,identity,offset):
        canonical(identity)
        with self._lock:
            row=self.transfer_status(identity)
            if row['status'] in ('cancelled','complete','paused'):raise ValueError('传输已结束或由机身暂停')
            if type(offset) is not int or not 0<=offset<=row['bytes']:raise ValueError('传输偏移无效')
            entry=self._streams.get(identity)
            if entry is None:
                if len(self._streams)>=8:raise ValueError('同时传输最多8项')
                stream=self.client_getter().open_library_asset(row['photo'])
                actual=hashlib.file_digest(stream,'sha256').hexdigest()
                if actual!=row['sha256']:
                    stream.close();raise ValueError('源资产已变化，不能继续原传输')
                entry={'stream':stream,'access':time.monotonic()};self._streams[identity]=entry
            stream=entry['stream'];entry['access']=time.monotonic()
            stream.seek(offset);data=stream.read(min(CHUNK_SIZE,row['bytes']-offset))
            if len(data)!=min(CHUNK_SIZE,row['bytes']-offset):raise ValueError('源文件读取不完整')
            with self._db() as db:
                end=offset+len(data)
                db.execute('UPDATE transfers SET offset=max(offset,?),status=? WHERE id=?',(end,'awaiting_commit' if end==row['bytes'] else 'transferring',identity))
        return data

    def preview_jpeg(self):
        image=self.camera.frames.image('live')
        if image.isNull():raise ValueError('取景已暂停')
        image=image.scaledToWidth(min(640,image.width()))
        buffer=QBuffer();buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer,'JPEG',80):raise ValueError('预览编码失败')
        return bytes(buffer.data())

    def startSocket(self):
        if self._socket is not None:return
        if os.name=='nt' or not hasattr(socket,'AF_UNIX'):raise OSError('此桌面没有已认证Unix端点；设备USB入口由FreeBSD启用')
        endpoint=Path(self.socketPath);endpoint.parent.mkdir(parents=True,exist_ok=True)
        if endpoint.exists() or endpoint.is_symlink():
            previous=endpoint.lstat()
            if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid!=os.getuid():raise ValueError('联机套接字身份不符')
            try:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:probe.settimeout(.2);probe.connect(str(endpoint))
            except OSError as error:
                if error.errno not in (errno.ECONNREFUSED,errno.ENOENT):raise ValueError('旧联机端点仍不可核实') from error
                current=endpoint.lstat()
                if (current.st_dev,current.st_ino)!=(previous.st_dev,previous.st_ino):raise ValueError('联机端点在核对期间已变化')
                endpoint.unlink()
            else:raise ValueError('已有联机实例正在运行')
        broker=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(15)
                # A private 0600 Unix socket authenticates the appliance UID;
                # FreeBSD additionally verifies peer credentials.
                try:
                    if peer_uid(self.request) not in (0,os.getuid()):return
                except OSError:return
                while True:
                    raw=self.rfile.readline(MAX_CONTROL+1)
                    if not raw:return
                    if len(raw)>MAX_CONTROL:raise ValueError('控制消息过大')
                    try:
                        value=json.loads(raw)
                        if value.get('method')=='transfer.getChunk':
                            params=value.get('params',{});data=broker.read_chunk(params.get('transfer_id'),params.get('offset',0))
                            self.wfile.write(json.dumps({'binary_bytes':len(data)}).encode()+b'\n'+data);self.wfile.flush();continue
                        result={'result':broker.call(value,trusted_usb=True)}
                    except Exception as error:result={'error':str(error)}
                    raw=json.dumps(result,ensure_ascii=False).encode()
                    if len(raw)>MAX_RESPONSE:raw=json.dumps({'error':'响应过大，请缩小查询页'}).encode()
                    self.wfile.write(raw+b'\n');self.wfile.flush()
        class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
            address_family=socket.AF_UNIX
            daemon_threads=True
        self._socket=Server(str(endpoint),Handler)
        if os.name!='nt':os.chmod(endpoint,0o600)
        self._socket_thread=threading.Thread(target=self._socket.serve_forever,daemon=True,name='daos-usb-broker');self._socket_thread.start()

    @pyqtSlot()
    def startWeb(self):
        if self._web is not None or self._web_starting:return
        if not self._enabled:self._message='请先启用联机控制';self.changed.emit();return
        self._web_starting=True;self._message='正在准备局域网加密连接';self.changed.emit()
        task=self._executor.submit(self._start_web)
        def done(value):
            try:self._webDone.emit(value.result(),None)
            except Exception as error:self._webDone.emit(None,str(error))
        task.add_done_callback(done)

    def _start_web(self):
        try:
            certificate=self.root/'lan-cert.pem';key=self.root/'lan-key.pem'
            if not certificate.exists() or not key.exists():
                import shutil
                binary=shutil.which('openssl') or ('C:/ProgramData/anaconda3/Library/bin/openssl.exe' if os.name=='nt' else '/usr/bin/openssl')
                environment=os.environ.copy()
                if os.name=='nt' and not environment.get('OPENSSL_CONF'):
                    candidates=(Path(binary).parent.parent/'ssl/openssl.cnf',Path('C:/ProgramData/anaconda3/Library/ssl/openssl.cnf'))
                    configuration=next((path for path in candidates if path.is_file()),None)
                    if configuration is not None:environment['OPENSSL_CONF']=str(configuration)
                subprocess.run([binary,'req','-x509','-newkey','rsa:2048','-nodes','-days','365','-subj','/CN=DaOS Camera',
                                '-keyout',str(key),'-out',str(certificate)],check=True,capture_output=True,timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,env=environment)
                if os.name!='nt':os.chmod(key,0o600)
            broker=self
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def send(self,status,data,kind='application/json',cookie=None):
                    self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
                    self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                    if cookie:self.send_header('Set-Cookie',cookie)
                    self.end_headers();self.wfile.write(data)
                def token(self):
                    cookies=dict(part.strip().split('=',1) for part in self.headers.get('Cookie','').split(';') if '=' in part)
                    value=cookies.get('daos','')
                    with broker._auth_lock:
                        if broker._tokens.get(value,0)<time.monotonic():raise ValueError('请先配对')
                    return value
                def do_GET(self):
                    try:
                        if self.path=='/':return self.send(200,Path(__file__).with_name('remote.html').read_bytes(),'text/html; charset=utf-8')
                        self.token()
                        if self.path=='/preview.jpg':return self.send(200,broker.preview_jpeg(),'image/jpeg')
                        matched=re.fullmatch('/transfer/([0-9a-f-]{36})/([0-9]+)',self.path)
                        if matched:return self.send(200,broker.read_chunk(matched[1],int(matched[2])),'application/octet-stream')
                        self.send(404,b'{}')
                    except Exception as error:self.send(403,json.dumps({'error':str(error)},ensure_ascii=False).encode())
                def do_POST(self):
                    try:
                        origin=self.headers.get('Origin')
                        if origin and origin!='https://'+self.headers.get('Host',''):raise ValueError('来源不匹配')
                        size=int(self.headers.get('Content-Length','0'))
                        if not 0<size<=MAX_CONTROL:raise ValueError('请求过大')
                        value=json.loads(self.rfile.read(size))
                        if self.path=='/pair':
                            address=self.client_address[0];now=time.monotonic()
                            with broker._auth_lock:
                                attempts,until=broker._failures.get(address,(0,now+60))
                                if now>until:attempts,until=0,now+60
                                if attempts>=5:raise ValueError('尝试过多，请稍后再试')
                                if now>broker._pin_expires or not hmac.compare_digest(str(value.get('pin','')),broker._pin):
                                    broker._failures[address]=(attempts+1,until);raise ValueError('配对码无效')
                                token=secrets.token_urlsafe(32);broker._tokens[token]=now+3600
                            return self.send(200,json.dumps({'paired':True,'csrf':token}).encode(),cookie='daos='+token+'; HttpOnly; Secure; SameSite=Strict; Path=/')
                        token=self.token()
                        if not hmac.compare_digest(self.headers.get('X-DaOS-CSRF',''),token):raise ValueError('请求验证失败')
                        result=broker.call(value)
                        self.send(200,json.dumps({'result':result},ensure_ascii=False).encode())
                    except Exception as error:self.send(400,json.dumps({'error':str(error)},ensure_ascii=False).encode())
            self._web=ThreadingHTTPServer((self._web_host,self._web_port),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certificate,key);self._web.socket=context.wrap_socket(self._web.socket,server_side=True)
            self._pin=str(secrets.randbelow(900000)+100000);self._pin_expires=time.monotonic()+300
            try:
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
                    probe.connect(('192.0.2.1',9));address=probe.getsockname()[0]
            except OSError:address=socket.gethostbyname(socket.gethostname())
            if self._web_host=='127.0.0.1':address='127.0.0.1'
            self._web_url='https://'+address+':'+str(self._web.server_address[1])+'/'
            self._web_thread=threading.Thread(target=self._web.serve_forever,daemon=True,name='daos-lan-camera');self._web_thread.start()
            self._message='局域网已开启，配对码有效5分钟；设备证书指纹 '+hashlib.sha256(certificate.read_bytes()).hexdigest()[:24]
        except Exception:
            if self._web:self._web.server_close();self._web=None
            raise
        return True

    @pyqtSlot(object,object)
    def _web_done(self,result,error):
        self._web_starting=False
        if error:self._message='局域网未开启：'+error
        if not self._enabled or self._closed:self.stopWeb()
        self.changed.emit()

    @pyqtSlot()
    def stopWeb(self):
        server=self._web;self._web=None
        if server:
            def stop():server.shutdown();server.server_close()
            if self._closed:stop()
            else:self._executor.submit(stop)
        with self._auth_lock:self._tokens.clear()
        self._pin='';self._web_url='';self.changed.emit()

    def close(self):
        if self._closed:return
        self._closed=True;self._timer.stop();self.stopWeb()
        if self._socket:
            self._socket.shutdown();self._socket.server_close();self._socket=None
            Path(self.socketPath).unlink(missing_ok=True)
        self._executor.shutdown(wait=True,cancel_futures=True)
        if self._web:self.stopWeb()
        with self._lock:
            for identity in list(self._streams):self._close_stream(identity)

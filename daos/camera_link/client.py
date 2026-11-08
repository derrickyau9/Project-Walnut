"""Standard-library USB/TLS client with verified resumable asset downloads."""
from __future__ import annotations
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import urllib.request
import uuid

MAX_CONTROL=65536
MAX_RESPONSE=2*1024*1024
BRIDGE='/usr/local/bin/python3.12 -B /usr/local/libexec/DaOS-camera-link.py'

class LinkError(RuntimeError):pass

def _read_exact(stream,count):
    result=bytearray()
    while len(result)<count:
        block=stream.read(count-len(result))
        if not block:raise LinkError('Connection ended; preserve the original capture UUID or transfer checkpoint')
        result.extend(block)
    return bytes(result)

class StreamTransport:
    def __init__(self,reader,writer):self.reader=reader;self.writer=writer;self.lock=threading.Lock()
    def call(self,request):
        raw=json.dumps(request,separators=(',',':')).encode()+b'\n'
        if len(raw)>MAX_CONTROL:raise LinkError('Control message exceeds 64 KiB')
        with self.lock:
            self.writer.write(raw);self.writer.flush()
            line=self.reader.readline(MAX_RESPONSE+1)
            if not line or len(line)>MAX_RESPONSE:raise LinkError('Invalid or interrupted response')
            response=json.loads(line)
            if 'error' in response:raise LinkError(response['error'])
            if 'binary_bytes' in response:
                count=response['binary_bytes']
                if type(count) is not int or not 0<=count<=256*1024:raise LinkError('Invalid binary chunk length')
                return _read_exact(self.reader,count)
            return response['result']
    def close(self):self.reader.close();self.writer.close()

class SocketTransport(StreamTransport):
    """Local test/appliance transport. The socket must be privately owned."""
    def __init__(self,path):
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.socket.settimeout(30)
        self.socket.connect(str(path));super().__init__(self.socket.makefile('rb'),self.socket.makefile('wb'))
    def close(self):
        try:super().close()
        finally:self.socket.close()

class USBTransport(StreamTransport):
    """Uses an existing dedicated ADB server and an explicitly selected DaOS serial.

    Start/authorize it with system/adb/connect_usb.py. No server is killed and no
    device is selected implicitly. The command is a fixed broker, never a shell.
    """
    def __init__(self,adb,serial='DaOS-USB-ADB-DEVELOPMENT',port=5049):
        path=Path(adb).resolve(strict=True)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',serial) or not 1024<=int(port)<=65535 or int(port)==5037:
            raise LinkError('Use an explicit DaOS serial and a dedicated ADB server port')
        command=[str(path),'-P',str(port),'-s',serial,'exec-out',BRIDGE]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        super().__init__(self.process.stdout,self.process.stdin)
    def close(self):
        super().close()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=3)

class HTTPTransport:
    """Paired HTTPS; pass the exported device certificate as cafile.

    The certificate must be obtained/checked on the camera. Never disables TLS
    verification. A network failure is surfaced to the caller, not auto-retried.
    """
    def __init__(self,origin,pin,*,cafile):
        if not re.fullmatch(r'https://[A-Za-z0-9.:-]+',origin):raise LinkError('Expected an HTTPS origin')
        self.origin=origin;self.csrf=''
        context=ssl.create_default_context(cafile=str(cafile));context.check_hostname=False
        # The pinned self-signed certificate authenticates the body. IP address
        # hostnames are expected, so certificate name checking is intentionally
        # replaced by this explicitly supplied single camera certificate.
        self.opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        value=self._json('/pair',{'pin':str(pin)});self.csrf=value['csrf']
    def _json(self,path,value):
        req=urllib.request.Request(self.origin+path,json.dumps(value).encode(),{'Content-Type':'application/json','X-DaOS-CSRF':self.csrf})
        with self.opener.open(req,timeout=30) as response:result=json.load(response)
        if 'error' in result:raise LinkError(result['error'])
        return result
    def call(self,request):
        if request['method']=='transfer.getChunk':
            p=request['params'];path='/transfer/'+p['transfer_id']+'/'+str(p['offset'])
            with self.opener.open(self.origin+path,timeout=30) as response:data=response.read(256*1024+1)
            if len(data)>256*1024:raise LinkError('Invalid binary chunk length')
            return data
        return self._json('/rpc',request)['result']
    def close(self):pass

class CameraClient:
    def __init__(self,transport):self.transport=transport;self.session=''
    def call(self,method,params=None):return self.transport.call({'method':method,'params':params or {},'session':self.session})
    def state(self):return self.call('getState')
    def acquire(self):
        value=self.call('session.acquire');self.session=value['session'];return value
    def release(self):
        value=self.call('session.release');self.session='';return value
    def heartbeat(self):return self.call('session.heartbeat')
    def configure(self,**settings):return self.call('configure',settings)
    def capture(self,request_id):
        """Caller MUST persist this UUID before sending; on error call query(id)."""
        if str(uuid.UUID(request_id))!=request_id:raise LinkError('Persist a canonical capture UUID before sending')
        return self.call('capture',{'request_id':request_id})
    def query(self,request_id):return self.call('query',{'request_id':request_id})
    def photos(self,query=None,cursor='',limit=48):return self.call('listPhotos',{'query':query or {},'cursor':cursor,'limit':limit})
    def iter_photos(self,query=None):
        cursor=''
        while True:
            page=self.photos(query,cursor)
            yield from page['items']
            cursor=page['next_cursor']
            if not cursor:return
    def tasks(self):return self.call('tasks')
    def inspect_task(self,job_id):return self.call('task.inspect',{'job_id':job_id})
    def control_task(self,job_id,action):
        if action not in ('pause','resume','cancel'):raise LinkError('Expected pause/resume/cancel')
        return self.call('task.'+action,{'job_id':job_id})
    def download(self,photo_uuid,destination,*,progress=None):
        """Exclusive publication, .part checkpoint resume and full SHA256 verify.

        Reuse the same destination after interruption. A changed source or
        checkpoint mismatch fails explicitly; existing files are never replaced.
        """
        target=Path(destination).absolute();partial=target.with_name(target.name+'.part');checkpoint=target.with_name(target.name+'.transfer.json')
        if any(p.is_symlink() for p in (target,partial,checkpoint,*target.parents)):raise LinkError('Choose a new ordinary destination')
        if target.exists():
            if not checkpoint.exists():raise LinkError('Destination already exists')
            transfer=json.loads(checkpoint.read_text())
            with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if transfer.get('photo_uuid')!=photo_uuid or digest!=transfer.get('sha256'):raise LinkError('Existing destination does not match the checkpoint')
            self.call('transfer.commit',{'transfer_id':transfer['transfer_id'],'sha256':digest})
            partial.unlink(missing_ok=True);checkpoint.unlink()
            return {'path':str(target),'sha256':digest,'bytes':target.stat().st_size}
        target.parent.mkdir(parents=True,exist_ok=True)
        if checkpoint.exists():
            if checkpoint.stat().st_size>4096:raise LinkError('Invalid checkpoint')
            transfer=json.loads(checkpoint.read_text())
            if transfer.get('photo_uuid')!=photo_uuid:raise LinkError('Checkpoint belongs to another asset')
            status=self.call('transfer.status',{'transfer_id':transfer['transfer_id']})
            if status['sha256']!=transfer['sha256'] or status['bytes']!=transfer['bytes']:raise LinkError('Transfer identity changed')
            if status['status']=='paused':self.call('transfer.resume',{'transfer_id':transfer['transfer_id']})
        else:
            if partial.exists():raise LinkError('Orphan partial file; inspect it before choosing another destination')
            transfer=self.call('transfer.create',{'photo_uuid':photo_uuid})
            with checkpoint.open('x',encoding='utf-8') as stream:json.dump(transfer,stream);stream.flush();os.fsync(stream.fileno())
        offset=partial.stat().st_size if partial.exists() else 0
        if not 0<=offset<=transfer['bytes']:raise LinkError('Invalid partial file size')
        with partial.open('ab') as stream:
            while offset<transfer['bytes']:
                data=self.call('transfer.getChunk',{'transfer_id':transfer['transfer_id'],'offset':offset})
                if not data or len(data)>transfer['bytes']-offset:raise LinkError('Invalid chunk')
                stream.write(data);stream.flush();os.fsync(stream.fileno());offset+=len(data)
                if progress:progress(offset,transfer['bytes'])
        with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=transfer['sha256']:raise LinkError('SHA256 mismatch; partial file retained for inspection')
        # Hard-link publication is create-only and atomic on the target volume.
        os.link(partial,target)
        partial.unlink()
        self.call('transfer.commit',{'transfer_id':transfer['transfer_id'],'sha256':digest})
        checkpoint.unlink()
        return {'path':str(target),'sha256':digest,'bytes':offset}
    def close(self):self.transport.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

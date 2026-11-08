"""Borrow an existing loopback ADB server without any server lifecycle command.

Uses the documented smart-socket host/transport services, shell_v2 and sync v1.
No adb executable is run, so version negotiation cannot restart another server.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time


def exact(sock,count):
    data=bytearray()
    while len(data)<count:
        piece=sock.recv(count-len(data))
        if not piece:raise ConnectionError('Existing ADB server/device disconnected')
        data.extend(piece)
    return bytes(data)


def request(sock,service):
    data=service.encode('utf-8')
    if len(data)>65535:raise ValueError('ADB service request exceeds smart-socket limit')
    sock.sendall(('%04x'%len(data)).encode()+data)
    status=exact(sock,4)
    if status==b'OKAY':return
    if status==b'FAIL':
        count=int(exact(sock,4),16)
        raise ConnectionError(exact(sock,count).decode(errors='replace'))
    raise ConnectionError('Invalid response from existing ADB smart socket')


class Client:
    def __init__(self,port,serial):
        if type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid existing local ADB port')
        self.port=port;self.serial=serial;self.transport=None

    def connect(self,timeout=10):return socket.create_connection(('127.0.0.1',self.port),timeout=timeout)

    def devices(self):
        with self.connect() as sock:
            request(sock,'host:devices-l');count=int(exact(sock,4),16)
            if count>1024**2:raise ValueError('Oversized ADB device inventory')
            return exact(sock,count).decode('utf-8')

    def bind(self,selected):
        if selected.get('serial')!=self.serial or selected.get('state')!='device' or not selected.get('transport_id','').isdecimal():
            raise ValueError('Existing server requires exact authenticated USB serial and transport ID')
        if ':' in self.serial:raise ValueError('TCP devices cannot be borrowed')
        self.transport=selected['transport_id']

    def open_service(self,service,timeout=120):
        if self.transport is None:raise ValueError('No pinned authenticated ADB transport')
        sock=self.connect(timeout)
        try:
            request(sock,'host:transport-id:'+self.transport);request(sock,service);return sock
        except BaseException:sock.close();raise

    def shell(self,command,timeout=120):
        stdout=bytearray();stderr=bytearray();code=1
        try:
            with self.open_service('shell,v2,raw:'+command,timeout) as sock:
                sock.sendall(struct.pack('<BI',4,0))
                while True:
                    kind,count=struct.unpack('<BI',exact(sock,5))
                    if count>16*1024**2 or len(stdout)+len(stderr)+count>64*1024**2:raise ValueError('Shell response exceeds bounds')
                    data=exact(sock,count)
                    if kind==1:stdout.extend(data)
                    elif kind==2:stderr.extend(data)
                    elif kind==3:
                        if not 1<=count<=4:raise ValueError('Invalid shell exit packet')
                        code=int.from_bytes(data,'little');break
        except (OSError,ConnectionError) as error:
            # Preserve a flushed reboot/selector receipt even when USB vanishes
            # before the final shell exit packet. Never retry the command.
            stderr.extend(('\n'+str(error)).encode())
        return subprocess.CompletedProcess(command,code,stdout.decode(errors='replace'),stderr.decode(errors='replace'))

    def push(self,source,target,timeout=180):
        encoded=(target+','+str(0o100600)).encode()
        if len(encoded)>4096:raise ValueError('Sync target exceeds bounds')
        with self.open_service('sync:',timeout) as sock:
            sock.sendall(b'SEND'+struct.pack('<I',len(encoded))+encoded)
            with Path(source).open('rb') as stream:
                while data:=stream.read(65536):sock.sendall(b'DATA'+struct.pack('<I',len(data))+data)
            sock.sendall(b'DONE'+struct.pack('<I',int(time.time())))
            tag,count=struct.unpack('<4sI',exact(sock,8))
            if count>4096:raise ValueError('Oversized sync response')
            message=exact(sock,count)
            if tag!=b'OKAY':raise ConnectionError('USB sync push failed: '+message.decode(errors='replace'))
            sock.sendall(b'QUIT'+struct.pack('<I',0))
        return subprocess.CompletedProcess('push',0,'','')

    def interactive(self):
        with self.open_service('shell,v2,pty:',timeout=600) as sock:
            def incoming():
                try:
                    while data:=sys.stdin.buffer.read1(1024):sock.sendall(struct.pack('<BI',0,len(data))+data)
                    sock.sendall(struct.pack('<BI',4,0))
                except (OSError,ValueError):pass
            threading.Thread(target=incoming,daemon=True).start()
            while True:
                kind,count=struct.unpack('<BI',exact(sock,5))
                if count>16*1024**2:raise ValueError('Oversized interactive shell packet')
                data=exact(sock,count)
                if kind in (1,2):
                    out=sys.stdout.buffer if kind==1 else sys.stderr.buffer;out.write(data);out.flush()
                elif kind==3:return int.from_bytes(data,'little')


class Connector:
    """Minimal adapter so existing USB methods preserve receipt/error handling."""
    def __init__(self,base,client):self.base=base;self.client=client
    def __getattr__(self,name):return getattr(self.base,name)
    def adb_call(self,adb,port,environment,*arguments,timeout=120):
        args=list(arguments)
        if args==['devices','-l']:return subprocess.CompletedProcess(args,0,self.client.devices(),'')
        if args[:1]==['-t']:
            if args[1]!=self.client.transport:raise ValueError('Borrowed transport ID changed')
            args=args[2:]
        if args==['get-state']:
            inventory=self.base.daos_devices(self.base.parse_devices(self.client.devices()),self.client.serial)
            valid=[d for d in inventory if d.get('state')=='device' and d.get('transport_id')==self.client.transport]
            if len(valid)!=1:raise ConnectionError('Pinned authenticated USB transport is no longer present')
            return subprocess.CompletedProcess(args,0,'device\n','')
        if args[:2]==['shell','-T'] and len(args)==3:return self.client.shell(args[2],timeout)
        if args[:1]==['push'] and len(args)==3:return self.client.push(args[1],args[2],timeout)
        raise ValueError('Unsupported borrowed-server operation; no lifecycle commands are allowed')

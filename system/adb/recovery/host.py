#!/usr/bin/env python3
"""DaOS USB rescue client. Write is explicit, chunked and identity-bound.

Reuses an authenticated matching USB transport on an existing loopback ADB
server, or starts the dedicated project server with its provisioned key when
none exists. Existing servers and their keys are never changed.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
REMOTE='/usr/local/libexec/DaOS-recovery/flash.py'
BOOT_RESCUE='/usr/local/libexec/DaOS-recovery/boot-rescue.py'
RETURN_NORMAL=HERE/'return-normal.py'
PYTHON='/usr/local/bin/python3.12'
RAM='/DaOS-recovery-ram/flash'
CHUNK=16*1024**2
SERIAL='DaOS-USB-ADB-DEVELOPMENT'
PORT=5053


def load_connector():
    spec=importlib.util.spec_from_file_location('daos_recovery_connector',HERE.parent/'connect_usb.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def load_socket_client():
    spec=importlib.util.spec_from_file_location('daos_recovery_adb_socket',HERE/'adb_socket.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def discover_existing_server(base,sockets,tools,serial,canonical_port=PORT):
    ports=[canonical_port]
    for record_path in (tools/'state').glob('server-*.json'):
        try:
            record=json.loads(record_path.read_text(encoding='utf-8'))
            port=record['port']
            if record.get('format')!='DaOS-project-adb-server-v1' or record.get('serial_filter')!=serial or type(port) is not int or not 1024<=port<=65535:continue
            ports.append(port)
        except (OSError,ValueError,KeyError):continue
    ports.append(5037)
    pending=[]
    for port in dict.fromkeys(ports):
        client=sockets.Client(port,serial)
        try:inventory=client.devices()
        except (OSError,ConnectionError,ValueError):continue
        ready=base.daos_devices(base.parse_devices(inventory),serial)
        if any(d.get('state')=='device' and d.get('transport_id','').isdecimal() for d in ready):return client
        # During reboot the existing owner may temporarily have no ready DaOS
        # transport. Wait for authentication on that running server rather
        # than start a competing USB owner. Binding/actions still require the
        # exact serial, DaOS metadata, state=device and a transport ID below.
        pending.append((bool(ready),client))
    for exact_serial,client in pending:
        if exact_serial:return client
    for _,client in pending:
        if client.port==5037:return client
    return pending[0][1] if pending else None


def sha(data):return hashlib.sha256(data).hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def write_new(path,data):
    with path.open('xb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())


class Journal:
    def __init__(self,path,manifest,*,resume=False):
        self.path=Path(path).absolute()
        if any(p.is_symlink() for p in (self.path,*self.path.parents)):
            raise ValueError('Redirected host journal path')
        if resume:
            stored=json.loads((self.path/'manifest.json').read_text(encoding='utf-8'))
            for key in ('format','session','role','image_bytes','image_sha256','chunk_bytes','chunks','target_identity'):
                if stored.get(key)!=manifest.get(key):raise ValueError('Resume image/card/role differs from the original session')
        else:
            self.path.mkdir(mode=0o700,parents=True,exist_ok=False)
            write_new(self.path/'manifest.json',canonical(manifest)+b'\n')
            (self.path/'events').mkdir(mode=0o700)
            (self.path/'uploads').mkdir(mode=0o700)
        events=list((self.path/'events').glob('*.json'))
        self.sequence=max([int(p.stem) for p in events]+[0])

    def event(self,event,**details):
        self.sequence+=1
        value={'sequence':self.sequence,'time':time.time(),'event':event,**details}
        write_new(self.path/'events'/('%08d.json'%self.sequence),canonical(value)+b'\n')

    def owned_uploads(self):
        operations=set()
        for path in (self.path/'events').glob('*.json'):
            event=json.loads(path.read_text(encoding='utf-8'))
            operation=event.get('operation','')
            if event.get('event')=='applying' and isinstance(operation,str) and re.fullmatch('[a-f0-9]{32}',operation):operations.add(operation)
        return [p for op in sorted(operations) for p in (RAM+'/incoming/'+op+'.bin',RAM+'/'+op+'.request.json')]


class USB:
    def __init__(self,tools,serial=SERIAL,port=PORT,timeout=90,existing_server_port=None):
        if not re.fullmatch(r'(?i:daos)[A-Za-z0-9._-]{1,60}',serial):raise ValueError('Expected an exact DaOS USB serial')
        if not 0<timeout<=600:raise ValueError('Connection timeout must be 1..600 seconds')
        self.connector=load_connector();base_connector=self.connector;self.tools=Path(tools).resolve(strict=False)
        self.adb=self.tools/'platform-tools'/('adb.exe' if os.name=='nt' else 'adb')
        sockets=load_socket_client();self.borrowed=None
        if existing_server_port is not None:self.borrowed=sockets.Client(existing_server_port,serial)
        else:self.borrowed=discover_existing_server(self.connector,sockets,self.tools,serial,port)
        if self.borrowed is not None:
            port=self.borrowed.port
            self.connector=sockets.Connector(self.connector,self.borrowed);self.environment={}
        else:
            if not self.adb.is_file():raise ValueError('Bundled official adb is missing')
            _,self.environment=self.connector.ensure_server(self.tools,self.adb,port,serial)
        self.port=port;self.serial=serial
        deadline=time.monotonic()+timeout
        while True:
            result=self.connector.adb_call(self.adb,port,self.environment,'devices','-l')
            if result.returncode:raise RuntimeError(result.stderr.strip())
            devices=self.connector.daos_devices(self.connector.parse_devices(result.stdout),serial)
            selected=self.connector.select_device(devices,interactive=False)
            if selected is not None:break
            if existing_server_port is None:
                discovered=discover_existing_server(base_connector,sockets,self.tools,serial,port)
                if discovered is not None and (self.borrowed is None or discovered.port!=port):
                    self.borrowed=discovered;port=discovered.port;self.port=port
                    self.connector=sockets.Connector(base_connector,discovered);self.environment={}
            if time.monotonic()>=deadline:
                raise TimeoutError('No authenticated matching DaOS USB transport; connect USB-C and boot normal/recovery mode')
            time.sleep(.4)
        if self.borrowed is not None:self.borrowed.bind(selected)
        self.selector=['-t',selected['transport_id']] if selected.get('transport_id','').isdecimal() else ['-s',serial]
        self.selected=selected
        self.check()

    def check(self):
        result=self.connector.adb_call(self.adb,self.port,self.environment,*self.selector,'get-state')
        if result.returncode or result.stdout.strip()!='device':raise ConnectionError('Selected authenticated USB transport disconnected')

    def run(self,*arguments,timeout=120):
        self.check()
        result=self.connector.adb_call(self.adb,self.port,self.environment,*self.selector,*arguments,timeout=timeout)
        if result.returncode:raise RuntimeError((result.stderr or result.stdout).strip() or 'ADB command failed')
        return result.stdout

    def remote(self,arguments,timeout=120):
        return self.run('shell','-T',' '.join(shlex.quote(str(v)) for v in arguments),timeout=timeout)

    def json(self,arguments,timeout=120):
        output=self.remote(arguments,timeout)
        try:value=json.loads(output)
        except json.JSONDecodeError as error:raise RuntimeError('Recovery returned invalid JSON: '+output[:500]) from error
        if not isinstance(value,dict):raise ValueError('Recovery response is not an object')
        return value

    def probe(self,role,offset,count):
        return self.json([PYTHON,'-B',REMOTE,'probe','--role',role,'--offset',offset,'--bytes',count])

    def prepare(self):
        self.remote(['/bin/mkdir','-p',RAM+'/incoming'])
        self.remote(['/bin/chmod','700',RAM,RAM+'/incoming'])

    def upload(self,source,target):
        self.run('push',str(source),target,timeout=180)
        # Windows source mode bits are not a reliable FreeBSD file policy.
        self.remote(['/bin/chmod','600',target])

    def apply(self,request_path):
        return self.json([PYTHON,'-B',REMOTE,'apply','--request',request_path],timeout=180)

    def cleanup(self,paths):self.remote(['/bin/rm','-f',*paths])

    def shell(self):
        self.check()
        if getattr(self,'borrowed',None) is not None:return self.borrowed.interactive()
        return subprocess.call([str(self.adb),'-P',str(self.port),*self.selector,'shell'],env=self.environment)

    def status(self):
        code="import json,os,subprocess;from pathlib import Path;m=Path('/run/daos-recovery/ram-root.json');i=Path('/etc/daos/recovery-identity.json');print(json.dumps({'system':os.uname().sysname,'machine':os.uname().machine,'bootfile':subprocess.check_output(['/sbin/sysctl','-n','kern.bootfile'],text=True).strip(),'ram_rescue':json.loads(m.read_text()) if m.is_file() else None,'flash_identity_present':i.is_file(),'flash_tool_present':Path('"+REMOTE+"').is_file()}))"
        result=self.json([PYTHON,'-B','-c',code])
        result['host_transport']={'server_port':self.port,'serial':self.serial,'transport_id':self.selected.get('transport_id'),
                                  'borrowed_existing_server':getattr(self,'borrowed',None) is not None}
        return result

    def rescue(self):
        self.check()
        arguments=[PYTHON,'-I','-B',BOOT_RESCUE,'reboot']
        command=' '.join(shlex.quote(v) for v in arguments)
        # Reboot can close USB before adb receives the shell exit packet. Do
        # not reissue it: require the flushed arming receipt, then hand over to
        # the next connection instead of calling run() and losing stdout.
        result=self.connector.adb_call(self.adb,self.port,self.environment,*self.selector,'shell','-T',command,timeout=120)
        receipt=None
        for line in result.stdout.splitlines():
            try:value=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(value,dict) and value.get('status')=='RAM-rescue-nextboot-armed' and value.get('reboot_requested') is True:receipt=value
        if receipt is None:
            raise ConnectionError('No RAM-rescue arming receipt received; command was not retried. Inspect status before another request. '+result.stderr.strip())
        return {'status':'RAM-rescue-nextboot-armed','reboot_requested':True,'reboot_confirmed':False,
                'next_step':'Keep USB connected; wait for re-enumeration, then run --timeout 300 status to verify RAM rescue before flashing',
                'adb_exit_code':result.returncode,'adb_message':result.stderr.strip(),'request_retried':False}

    def normal(self):
        validate_probe(self.probe('root',0,4096),'root',0,4096)
        self.prepare()
        remote=RAM+'/incoming/return-normal.py'
        self.upload(RETURN_NORMAL,remote)
        self.check()
        command=' '.join(shlex.quote(v) for v in (PYTHON,'-I','-B',remote))
        result=self.connector.adb_call(self.adb,self.port,self.environment,*self.selector,'shell','-T',command,timeout=120)
        receipt=None
        for line in result.stdout.splitlines():
            try:value=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(value,dict) and value.get('status')=='normal-boot-selected' and value.get('reboot_requested') is True and (type(value.get('marker_bytes_written')) is int and (value['marker_bytes_written']==1 or value['marker_bytes_written']==0 and value.get('marker_absent') is True)):receipt=value
        if receipt is None:raise ConnectionError('No normal-boot selector receipt received; command was not retried. '+result.stderr.strip())
        return {**receipt,'reboot_confirmed':False,'request_retried':False,'adb_exit_code':result.returncode,
                'next_step':'Keep USB connected; wait for re-enumeration, then run --timeout 300 status to verify normal boot'}


def validate_probe(value,role,offset,count):
    if value.get('format')!='DaOS-recovery-probe-v1' or value.get('role')!=role or value.get('target_offset')!=offset or value.get('bytes')!=count:
        raise ValueError('Probe does not match requested role/range')
    if type(value.get('partition_bytes')) is not int or value['partition_bytes']<=0 or value.get('sector_size') not in (512,4096):
        raise ValueError('Invalid probed partition geometry')
    if not re.fullmatch('[a-f0-9]{64}',str(value.get('before_sha256',''))) or not isinstance(value.get('target_identity'),dict):
        raise ValueError('Probe lacks a bound partition identity/preimage')
    identity=value['target_identity']
    if not all(k in identity for k in ('disk_uuid','uuid','start_lba','sectors','index')):
        raise ValueError('Probe partition identity is incomplete')
    if identity['sectors']*value['sector_size']!=value['partition_bytes']:
        raise ValueError('Probe identity and geometry disagree')
    return value


def hash_image(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Full partition image must be an ordinary file')
    digest=hashlib.sha256();chunks=[];offset=0
    with path.open('rb') as f:
        before=os.fstat(f.fileno())
        while data:=f.read(CHUNK):
            digest.update(data);chunks.append({'offset':offset,'bytes':len(data),'sha256':sha(data)});offset+=len(data)
        after=os.fstat(f.fileno())
    if before.st_size!=offset or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Source image changed while hashing')
    return offset,digest.hexdigest(),chunks


def write_image(usb,role,image,journal_path,*,resume=False,progress=print):
    if role not in ('root','state'):raise ValueError('Only complete root/state partition images are accepted')
    image=Path(image).resolve(strict=True)
    image_bytes,image_sha,chunks=hash_image(image)
    initial=validate_probe(usb.probe(role,0,4096),role,0,4096)
    if image_bytes!=initial['partition_bytes']:
        raise ValueError('Image must exactly match the full '+role+' partition: expected '+str(initial['partition_bytes'])+' bytes')
    if any(c['bytes']%initial['sector_size'] for c in chunks):raise ValueError('Source is not sector aligned')
    if resume:
        stored=json.loads((Path(journal_path)/'manifest.json').read_text(encoding='utf-8'))
        session=stored.get('session')
        if not isinstance(session,str) or not re.fullmatch('[a-f0-9]{32}',session):raise ValueError('Invalid resume session')
    else:session=uuid.uuid4().hex
    manifest={'format':'DaOS-USB-recovery-host-session-v1','session':session,'role':role,'image':str(image),
              'image_bytes':image_bytes,'image_sha256':image_sha,'chunk_bytes':CHUNK,'chunks':chunks,
              'target_identity':initial['target_identity']}
    journal=Journal(journal_path,manifest,resume=resume)
    journal.event('resume' if resume else 'start',image_sha256=image_sha)
    progress('Journal: '+str(journal.path))
    completed=0
    try:
        usb.prepare()
        if resume:
            stale=journal.owned_uploads()
            if stale:usb.cleanup(stale)
        with image.open('rb') as source:
            for chunk in chunks:
                if os.fstat(source.fileno()).st_size!=image_bytes:raise ValueError('Source image length changed after its immutable manifest')
                source.seek(chunk['offset']);data=source.read(chunk['bytes'])
                if len(data)!=chunk['bytes'] or sha(data)!=chunk['sha256']:raise ValueError('Source chunk changed since its immutable manifest')
                observed=validate_probe(usb.probe(role,chunk['offset'],chunk['bytes']),role,chunk['offset'],chunk['bytes'])
                if observed['target_identity']!=manifest['target_identity'] or observed['partition_bytes']!=image_bytes:
                    raise ValueError('Target identity changed during the recovery session')
                if observed['before_sha256']==chunk['sha256']:
                    journal.event('verified-already-present',offset=chunk['offset'],bytes=chunk['bytes'],sha256=chunk['sha256'])
                    completed+=chunk['bytes'];progress(str(completed)+' / '+str(image_bytes)+' bytes verified');continue
                # A fresh preimage produces a fresh plan on explicit --resume.
                # A lost reply never triggers an automatic uncertain rewrite.
                operation=uuid.uuid5(uuid.UUID(hex=session),str(chunk['offset'])+':'+observed['before_sha256']+':'+chunk['sha256']).hex
                name=operation+'.bin';remote_image=RAM+'/incoming/'+name;remote_request=RAM+'/'+operation+'.request.json'
                request={'format':'DaOS-recovery-write-v1','operation':operation,'role':role,'image':name,
                         'image_bytes':chunk['bytes'],'image_sha256':chunk['sha256'],
                         'extents':[{'target_offset':chunk['offset'],'image_offset':0,'bytes':chunk['bytes'],
                                     'before_sha256':observed['before_sha256'],'sha256':chunk['sha256']}]}
                local_chunk=journal.path/'uploads'/name;local_request=journal.path/'uploads'/(operation+'.request.json')
                if local_chunk.exists():local_chunk.unlink()
                if local_request.exists():local_request.unlink()
                write_new(local_chunk,data);write_new(local_request,canonical(request)+b'\n')
                journal.event('applying',operation=operation,offset=chunk['offset'],bytes=chunk['bytes'],request=request)
                usb.upload(local_chunk,remote_image);usb.upload(local_request,remote_request)
                receipt=usb.apply(remote_request)
                if receipt.get('format')!='DaOS-recovery-write-v1' or receipt.get('operation')!=operation or receipt.get('status')!='complete' or receipt.get('readback_verified') is not True or receipt.get('request_sha256')!=sha(canonical(request)) or receipt.get('target_identity')!=manifest['target_identity']:
                    raise ValueError('Device did not return the exact completed verified request receipt')
                journal.event('applied',offset=chunk['offset'],bytes=chunk['bytes'],receipt=receipt)
                usb.cleanup([remote_image,remote_request]);local_chunk.unlink();local_request.unlink()
                completed+=chunk['bytes'];progress(str(completed)+' / '+str(image_bytes)+' bytes verified')
        journal.event('complete',verified_bytes=completed)
        return {'status':'complete','role':role,'image_sha256':image_sha,'verified_bytes':completed,'journal':str(journal.path)}
    except BaseException as error:
        journal.event('interrupted',verified_bytes=completed,error=str(error))
        raise


def main():
    connector=load_connector()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools',type=Path,default=connector.DEFAULT_TOOLS)
    p.add_argument('--server-port',type=int,default=PORT)
    p.add_argument('--existing-server-port',type=int,help='Borrow an already-running loopback ADB server without lifecycle commands')
    p.add_argument('--serial',default=SERIAL)
    p.add_argument('--timeout',type=float,default=90)
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('status');sub.add_parser('shell');sub.add_parser('rescue');sub.add_parser('normal')
    probe=sub.add_parser('probe');probe.add_argument('--role',choices=('root','state'),required=True);probe.add_argument('--offset',type=int,default=0);probe.add_argument('--bytes',type=int,required=True)
    write=sub.add_parser('write');write.add_argument('--role',choices=('root','state'),required=True);write.add_argument('--image',type=Path,required=True);write.add_argument('--journal',type=Path);write.add_argument('--resume',type=Path)
    args=p.parse_args()
    if args.action=='write' and args.journal is not None and args.resume is not None:p.error('Use --journal for a fresh run or --resume for an existing journal')
    usb=USB(args.tools,args.serial,args.server_port,args.timeout,existing_server_port=args.existing_server_port)
    if args.action=='shell':raise SystemExit(usb.shell())
    if args.action=='status':result=usb.status()
    elif args.action=='rescue':result=usb.rescue()
    elif args.action=='normal':result=usb.normal()
    elif args.action=='probe':result=validate_probe(usb.probe(args.role,args.offset,args.bytes),args.role,args.offset,args.bytes)
    else:
        directory=args.resume or args.journal or PROJECT/'build/DaOS_USB_Recovery/host-journals'/uuid.uuid4().hex
        result=write_image(usb,args.role,args.image,directory,resume=args.resume is not None)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print('DaOS USB recovery: '+str(error),file=sys.stderr);raise SystemExit(1)

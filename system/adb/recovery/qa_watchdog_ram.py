#!/usr/local/bin/python3.12
"""Explicit RAM-only hardware watchdog QA; never run automatically.

plan performs read-only hardware/media inspection. disarm proves clean stop.
fault requires that same-boot disarm receipt and deliberately stops the feeder.
Only RAM files, one temporary kernel environment value and the watchdog are
changed. The production watchdog binary and SD enable policy are not modified.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid

RAM=Path('/DaOS-recovery-ram')
WORK=RAM/'watchdog-qa'
POLICY=Path('/etc/daos/recovery-watchdog.enabled')
POLICY_BYTES=b'DaOS-recovery-watchdog-v1\n'
BINARY_SHA='b06cdf2b16b375c6d287a48e3c7b7d8196b0a9cc9bcc6b12f1a3dd2db1fdcdac'
EFI_GUID='c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
FORMAT='DaOS-RAM-watchdog-QA-v1'
SBT_1S=1<<32
# Pinned 95719d8... sys/sys/watchdog.h: _IOR('W',54,sbintime_t).
# sys/sys/ioccom.h: IOC_OUT | ((sizeof(sbintime_t)&8191)<<16) | ('W'<<8) |54.
WDIOC_GETTIMEOUT=0x40000000|(8<<16)|(ord('W')<<8)|54


def sha(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def command(args):
    result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=10)
    if result.returncode:raise RuntimeError(str(args)+': '+result.stderr[-1000:])
    return result.stdout.strip()


def query_timeout():
    """The controller issues only this read-only watchdog ioctl."""
    fd=os.open('/dev/fido',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        data=bytearray(8);fcntl.ioctl(fd,WDIOC_GETTIMEOUT,data,True)
        value=struct.unpack('=q',data)[0]
        if value<0:raise ValueError('Invalid watchdog timeout result')
        return value
    finally:os.close(fd)


def safety_mounts(text,md_description,swap):
    rows=[line.split() for line in text.splitlines() if line.strip()]
    if any(len(row)!=6 for row in rows):raise ValueError('Invalid mount table')
    roots=[row for row in rows if row[1]=='/']
    if len(roots)!=1 or roots[0][0]!='/dev/md0' or roots[0][2]!='ufs':raise ValueError('Requires exact resident /dev/md0 UFS root')
    if 'rw'not in roots[0][3].split(',') or 'ro'in roots[0][3].split(','):raise ValueError('Resident RAM root must already be writable')
    md=md_description.split()
    if len(md)<2 or md[:2]not in (['md0','preload'],['md0','malloc']):raise ValueError('md0 is not resident RAM')
    if swap.strip()!='0':raise ValueError('Active swap prevents watchdog reset QA')
    for row in rows:
        if row[1]=='/':continue
        if row[2]not in ('tmpfs','devfs','procfs','fdescfs'):
            raise ValueError('Physical/aliased filesystem remains mounted: '+row[1])
    private=[row for row in rows if row[1]==str(RAM)]
    if len(private)!=1 or private[0][2]!='tmpfs' or 'rw'not in private[0][3].split(','):
        raise ValueError('Dedicated writable recovery tmpfs is required')
    return rows


def save(path,record):
    temporary=path.with_suffix('.tmp')
    with temporary.open('x',encoding='utf-8') as file:
        json.dump(record,file,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
    os.chmod(temporary,0o600);os.replace(temporary,path)


def emit(path,record,status,**fields):
    record.update(fields,status=status,updated_monotonic=time.monotonic())
    save(path,record);print(json.dumps(record,sort_keys=True),flush=True)


@contextmanager
def ram_policy(run=command,path=POLICY):
    """Grant only this launch, then restore the rescue guard immediately."""
    if run(['/rescue/kenv','-q','daos_recovery'])!='YES':raise ValueError('Original rescue guard must be YES')
    if path.exists() or path.is_symlink():raise ValueError('Unexpected existing watchdog enable policy')
    created=False
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
        with os.fdopen(fd,'wb') as file:file.write(POLICY_BYTES);file.flush();os.fsync(file.fileno())
        os.chmod(path,0o444)
        run(['/rescue/kenv','-u','daos_recovery'])
        yield
    finally:
        try:
            if created:run(['/rescue/kenv','daos_recovery=YES'])
        finally:
            if created:path.unlink(missing_ok=True)


class Runtime:
    clock=staticmethod(time.monotonic)
    sleep=staticmethod(time.sleep)
    query=staticmethod(query_timeout)
    def __init__(self,binary,selector_sha):
        spec=importlib.util.spec_from_file_location('watchdog_qa_flash_gate','/usr/local/libexec/DaOS-recovery/flash.py')
        self.flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.flash)
        self.binary=Path(binary);self.selector_sha=selector_sha;self.child=None;self.selector=None
    def boot(self):return command(['/rescue/sysctl','-n','kern.boottime'])
    def verify(self):
        if not sys.platform.startswith('freebsd') or os.geteuid()!=0 or sys.byteorder!='little':raise ValueError('Native little-endian FreeBSD root required')
        self.flash.NativeBackend().prove('root')
        rows=safety_mounts(command(['/rescue/mount','-p']),command(['/rescue/mdconfig','-l','-v','-u','0']),command(['/rescue/sysctl','-n','vm.swap_total']))
        self.flash.secure_path(WORK)
        if self.binary.parent!=WORK or self.binary.name!='kernel-watchdog':raise ValueError('Binary must be the private tmpfs QA copy')
        self.flash.secure_path(self.binary)
        info=self.binary.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not info.st_mode&0o111 or sha(self.binary)!=BINARY_SHA:
            raise ValueError('The exact bound executable watchdog is required')
        driver=command(['/rescue/sysctl','-n','dev.bcmwd.0.%driver'])
        if driver!='bcmwd':raise ValueError('The BCM hardware watchdog must be attached')
        if command(['/rescue/kenv','-q','daos_recovery'])!='YES':raise ValueError('Rescue boot guard changed')
        if POLICY.exists() or POLICY.is_symlink():raise ValueError('RAM watchdog policy must initially be absent')
        return {'mounts':rows,'driver':driver,'binary_sha256':BINARY_SHA}
    def marker(self):
        identity=json.loads(self.flash.secure_path(self.flash.IDENTITY).read_text());disk=identity['disk']
        fd=os.open(disk,os.O_RDONLY|os.O_NOFOLLOW)
        try:disk_uuid,parts=self.flash.parse_gpt(fd,identity['disk_bytes'],identity['sector_size'])
        finally:os.close(fd)
        first=parts.get(1,{})
        if disk_uuid!=identity['disk_uuid'] or first.get('type')!=EFI_GUID or first.get('label')!='DaOS-efi':raise ValueError('Unexpected fixed EFI partition identity')
        mount=WORK/('efi-read-'+uuid.uuid4().hex);mount.mkdir(mode=0o700);mounted=False
        try:
            command(['/rescue/rescue','mount_msdosfs','-o','ro',disk+'p1',mount]);mounted=True
            rows=[line.split()for line in command(['/rescue/mount','-p']).splitlines()]
            matching=[r for r in rows if len(r)==6 and r[1]==str(mount)]
            if len(matching)!=1 or matching[0][0]!=disk+'p1' or matching[0][2]!='msdosfs' or 'ro'not in matching[0][3].split(',') or 'rw'in matching[0][3].split(','):raise ValueError('EFI inspection must be the exact read-only provider')
            marker=mount/'DaOS-recovery/normal.running';selector=mount/'boot.scr'
            if any(p.is_symlink()for p in (marker,marker.parent,selector)):raise ValueError('Redirected FAT boot state')
            info=marker.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size!=1 or info.st_nlink!=1 or marker.read_bytes()!=b'\1':raise ValueError('Sticky FAT normal.running must already equal one')
            if not 0<selector.stat().st_size<=65536:raise ValueError('Unexpected FAT boot selector size')
            if not re.fullmatch('[a-f0-9]{64}',self.selector_sha) or sha(selector)!=self.selector_sha:raise ValueError('Armed FAT selector SHA256 differs')
            self.selector={'disk_uuid':disk_uuid,'efi_uuid':first['uuid'],'marker':1,'selector_sha256':self.selector_sha}
        finally:
            if mounted:command(['/rescue/rescue','umount',mount])
            mount.rmdir()
        self.verify();return self.selector
    def arm(self):
        # Caller has proved all media protected BEFORE the first SETTIMEOUT.
        with ram_policy():
            self.child=subprocess.Popen([str(self.binary)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,start_new_session=True,close_fds=True,
                env={'PATH':'/rescue','HOME':'/','LC_ALL':'C'})
            pending=bytearray();deadline=self.clock()+4
            selector=selectors.DefaultSelector();selector.register(self.child.stderr,selectors.EVENT_READ)
            try:
                while self.clock()<deadline:
                    if selector.select(.1):
                        data=os.read(self.child.stderr.fileno(),4096)
                        if not data:break
                        pending.extend(data)
                        expected=('ARMED pid='+str(self.child.pid)+' hardware_timeout_seconds=8 feed_seconds=1\n').encode()
                        if expected in pending and self.child.poll()is None:return {'pid':self.child.pid,'arm_output':pending.decode(errors='replace')[-1800:]}
                    if self.child.poll()is not None:break
            finally:selector.close()
            raise RuntimeError('Matching watchdog ARMED acknowledgement missing: '+pending.decode(errors='replace')[-1800:])
    def disarm(self):
        if self.child is None:return
        if self.child.poll()is None:
            self.child.send_signal(signal.SIGCONT);self.child.terminate()
        output=self.child.communicate(timeout=3)[1]
        if self.child.returncode!=0 or b'DISARMED:'not in output:raise RuntimeError('Watchdog clean disarm did not complete')
        if self.query()!=0:raise ValueError('Watchdog remains logically armed')
    def stop(self):
        if self.child is None or self.child.poll()is not None:raise ValueError('Owned watchdog child is not alive')
        self.child.send_signal(signal.SIGSTOP)


def observe_disarmed(runtime,boot_id,seconds=12):
    started=runtime.clock()
    while runtime.clock()-started<seconds:
        if runtime.boot()!=boot_id or runtime.query()!=0:raise ValueError('Reset or re-arming occurred during disarm observation')
        runtime.sleep(.5)
    return runtime.clock()-started


def validate_disarm_receipt(prior,runtime,boot_id):
    if prior.get('format')!=FORMAT or prior.get('status')!='disarm-observation-passed' or prior.get('boot_id')!=boot_id or prior.get('binary_sha256')!=BINARY_SHA:
        raise ValueError('Fresh successful same-boot disarm receipt required')
    if prior.get('selector')!=runtime.selector or not 0<=runtime.clock()-prior.get('completed_monotonic',-1)<=900:
        raise ValueError('Disarm receipt selector differs or is stale')


def run_phase(action,runtime,path,prior=None):
    if action not in ('plan','disarm','fault'):raise ValueError('Unknown explicit QA phase')
    runtime.verify();selector=runtime.marker();boot_id=runtime.boot()
    if runtime.query()!=0:raise ValueError('An existing watchdog is already armed')
    record={'format':FORMAT,'action':action,'receipt':str(path),'boot_id':boot_id,'binary_sha256':BINARY_SHA,'selector':selector,
            'query_ioctl':hex(WDIOC_GETTIMEOUT),'physical_filesystems_unmounted':True,'physical_partition_bytes_written':0}
    if action=='plan':emit(path,record,'planned-no-watchdog-arm');return record
    if action=='fault':validate_disarm_receipt(prior or {},runtime,boot_id)
    emit(path,record,'preparing-RAM-only-arm')
    try:
        armed=runtime.arm();runtime.verify()
        if runtime.query()!=8*SBT_1S:raise ValueError('Queried watchdog timeout is not eight seconds')
        emit(path,record,'armed-guard-restored',**armed)
        if action=='disarm':
            runtime.sleep(2);runtime.disarm()
            observed=observe_disarmed(runtime,boot_id)
            runtime.verify()
            emit(path,record,'disarm-observation-passed',observed_seconds=observed,completed_monotonic=runtime.clock(),timeout_sbt=0)
            return record
        runtime.verify()
        nonce=uuid.uuid4().hex;nonce_path=path.with_suffix('.nonce');nonce_path.write_text(nonce+'\n');os.chmod(nonce_path,0o600)
        emit(path,record,'fault-stop-requested',reset_nonce={'path':str(nonce_path),'value':nonce},
             expected_reset_seconds=8,failure_deadline_seconds=15,host_must_verify_new_transport=True,
             reset_proof='Fresh RAM rescue plus nonce absent and new USB transport; boot time may repeat without RTC')
        runtime.stop();started=runtime.clock()
        emit(path,record,'feeder-stopped-awaiting-reset',stopped_monotonic=started)
        # A successful hardware reset terminates this RAM process. Surviving
        # this deadline is a failed test, never a success inferred from USB loss.
        while runtime.clock()-started<15:runtime.sleep(.25)
        runtime.disarm();observed=observe_disarmed(runtime,boot_id)
        emit(path,record,'fault-no-reset-failed',cleanup_disarm_observed_seconds=observed)
        return record
    except BaseException as error:
        try:runtime.disarm()
        except BaseException as cleanup:record['cleanup_error']=str(cleanup)
        emit(path,record,'failed',error=str(error));raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','disarm','fault'))
    p.add_argument('--binary',type=Path,default=WORK/'kernel-watchdog')
    p.add_argument('--selector-sha256',required=True)
    p.add_argument('--disarm-receipt',type=Path)
    args=p.parse_args()
    runtime=Runtime(args.binary,args.selector_sha256)
    # The caller creates/uploads only inside this already validated tmpfs.
    runtime.verify();runtime.flash.NativeBackend().prove('root')
    global_work=runtime.flash.WORK;global_work.mkdir(mode=0o700,parents=True,exist_ok=True);runtime.flash.secure_path(global_work)
    previous=None
    if args.action=='fault':
        if args.disarm_receipt is None:p.error('fault requires --disarm-receipt from this same boot')
        if args.disarm_receipt.parent!=WORK or not re.fullmatch(r'disarm-[a-f0-9]{32}\.json',args.disarm_receipt.name):raise ValueError('Use the private same-boot disarm receipt path')
        previous=json.loads(runtime.flash.secure_path(args.disarm_receipt).read_text())
    path=WORK/(args.action+'-'+uuid.uuid4().hex+'.json')
    for number in (signal.SIGINT,signal.SIGTERM):signal.signal(number,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt('QA controller interrupted')))
    with runtime.flash.lock(global_work):record=run_phase(args.action,runtime,path,previous)
    print(json.dumps({'receipt':str(path),'status':record['status']}),flush=True)
    return 1 if record['status']=='fault-no-reset-failed' else 0


if __name__=='__main__':raise SystemExit(main())

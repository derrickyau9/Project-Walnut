#!/usr/local/bin/python3.12
"""Stage one fixed no-radio 005+RAM boot; plan is the default, never reboot.

The adjacent, exact-hash watchdog enabler supplies the already reviewed card,
normal-root and independent FAT-v3 pins. Its enable operation is never called.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import sys
import uuid

FORMAT='DaOS-wifi005-RAM-one-shot-v1'
GATE_SHA='c306bb42b4fc71ad5c527bc0a979f962d4e0dc620cee31482f809ee8cedb8f5e'
KERNEL='kernel.daos-wifi-005-ramprobe'
DIRECTORY='boot/'+KERNEL
SOURCE=Path('/DaOS-recovery-ram/flash/incoming/wifi005-ramprobe')
RAM_FILE='boot/DaOS-recovery/root.ufs.gz'
RAM_SHA='e6af2f549a0c6935c83c17b0e1739bd6add522bcedff813e42b9be5d14ed8e2c'
DTBO='boot/dtb/overlays/daos-wifi-layout-002.dtbo'
DTBO_SHA='d158595ff3fa7ebc2b800667a22ca7598f4378afeef22a70e08dd1b1d9e4b347'
ROLLBACK_MARGIN=6*1024**2
INPUTS={
    'kernel':dict(bytes=19595896,sha256='f966d6d2211cd6df06d1f9a0a50c520450f812c53662cddf7f91da0488e2e012',elf_type=2),
    'usb_template.ko':dict(bytes=76856,sha256='c385973ab0fd4483ad8238bfc760f4cdffc5a1b41213a7dca6ad4ce6be6f4fbc',elf_type=3),
    'daos_adb_template.ko':dict(bytes=10184,sha256='dfbfa4346cad57d2dc581f1b2b28f5389f221d386b6f58ca36a3364b76e19e67',elf_type=3)}


def sha(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def syncdir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def nonce(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Explicit 32-hex trial nonce required')
    return value


def selector(trial):
    nonce(trial);base='/boot/'+KERNEL
    settings={
        'kernel':KERNEL,'bootfile':base+'/kernel','kernel_path':base,'module_path':base,
        'kernels_autodetect':'NO',
        'fdt_overlays':'/'+DTBO,
        'mfsroot_load':'YES','mfsroot_type':'md_image','mfsroot_name':'/boot/DaOS-recovery/root.ufs',
        'vfs.root.mountfrom':'ufs:/dev/md0','vfs.root.mountfrom.options':'rw',
        'daos_recovery':'YES','daos_wifi_trial':'005','daos_wifi005_no_rf':'YES',
        'daos_wifi005_trial_nonce':trial,'daos_wifi005_kernel_sha256':INPUTS['kernel']['sha256'],
        'daos_wifi005_ram_gzip_sha256':RAM_SHA,'hw.bcm2835.sdhci.pio_mode':'1',
        'hw.usb.template':'-1','usb_template_load':'YES','usb_template_name':base+'/usb_template.ko',
        'daos_adb_template_load':'YES','daos_adb_template_name':base+'/daos_adb_template.ko',
        'rpi_ft5406_load':'NO','sdio_load':'NO','if_brcmfmac_load':'NO',
        'boot_single':'NO','boot_mute':'NO','boot_verbose':'YES',
        'init_script':'/usr/local/libexec/DaOS-recovery/early-init.sh'}
    return ('nextboot_enable="YES"\n'+''.join(key+'="'+value+'"\n' for key,value in settings.items())).encode()


def cleared(data):
    lines=data.splitlines()
    return bool(lines and lines[0].strip()==b'nextboot_enable="NO"' and sum(bool(re.match(rb'\s*nextboot_enable\s*=',line)) for line in lines)==1)


def ours(data,expected):
    first,_,body=data.partition(b'\n');_,_,wanted=expected.partition(b'\n')
    return data==expected or (first.strip()==b'nextboot_enable="NO"' and body==wanted)


def validate_sources(source,pins=INPUTS,secure=lambda path:Path(path)):
    secure(source)
    if set(path.name for path in source.iterdir())!=set(pins):raise ValueError('Only the three fixed no-radio inputs are accepted')
    for name,entry in pins.items():
        path=secure(source/name);info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size!=entry['bytes'] or sha(path)!=entry['sha256']:raise ValueError('Candidate input differs: '+name)
        with path.open('rb') as file:header=file.read(20)
        if len(header)!=20 or header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<HH',header,16)!=(entry['elf_type'],183):raise ValueError('Candidate must be the bound ARM64 ELF: '+name)
    return pins


def atomic(path,data,mode=0o644,expected=None):
    temporary=path.with_name('.'+path.name+'.trial-'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as file:file.write(data);os.fchmod(file.fileno(),mode);file.flush();os.fsync(file.fileno())
        if expected is not None and path.read_bytes()!=expected:raise ValueError('Atomic destination changed before replacement')
        os.replace(temporary,path);syncdir(path.parent)
        if path.read_bytes()!=data:raise ValueError('Atomic write readback failed: '+str(path))
    finally:temporary.unlink(missing_ok=True)


def space_report(available,free,stage_bytes):
    required=stage_bytes+ROLLBACK_MARGIN
    if available<required or free<available:raise ValueError('Insufficient ordinary available root space for candidates plus fixed six-MiB rollback margin')
    return {'available_bytes':available,'free_bytes':free,'stage_bytes':stage_bytes,
            'rollback_margin_bytes':ROLLBACK_MARGIN,'required_available_bytes':required}


def copy_candidate(source,path,entry):
    if path.exists() or path.is_symlink():raise ValueError('Candidate destination already exists')
    temporary=path.with_name('.'+path.name+'.trial-'+uuid.uuid4().hex)
    try:
        with source.open('rb') as incoming,temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming,outgoing,1024*1024);os.fchmod(outgoing.fileno(),0o644);outgoing.flush();os.fsync(outgoing.fileno())
        if temporary.stat().st_size!=entry['bytes'] or sha(temporary)!=entry['sha256']:raise ValueError('Candidate copy readback differs')
        os.link(temporary,path);temporary.unlink();syncdir(path.parent)
        if sha(path)!=entry['sha256']:raise ValueError('Published candidate differs')
    finally:temporary.unlink(missing_ok=True)


class Native:
    root=Path('/')
    inputs=INPUTS
    def __init__(self,source,trial):
        if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root required')
        path=Path(__file__).with_name('enable_recovery_watchdog.py')
        if path.is_symlink() or sha(path)!=GATE_SHA:raise ValueError('Adjacent reviewed watchdog gate SHA differs')
        spec=importlib.util.spec_from_file_location('wifi005_stage_gate',path);self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.module.owned(path);self.gate=self.module.Native();self.source=source;self.trial=nonce(trial)
        self.work=Path('/var/DaOS/usb-recovery/wifi005-ramtrial-'+self.trial)
    def verify(self):
        m=self.module;g=self.gate
        if Path('/run/daos-recovery/ram-root.json').exists() or Path('/run/daos-recovery/ram-root.json').is_symlink():raise ValueError('Stage only from the stable normal root')
        g.mounts();m.owned(m.RAM)
        if stat.S_IMODE(m.RAM.stat().st_mode)!=0o700:raise ValueError('Private recovery tmpfs required')
        m.normal_environment()
        if g.run(['/sbin/sysctl','-n','kern.bootfile'])!='/boot/kernel/kernel':raise ValueError('Stable normal kernel is not running')
        for name,digest in m.ROOT_PINS.items():
            if sha(m.owned(name))!=digest:raise ValueError('Normal production pin differs: '+name)
        policy=m.owned(m.POLICY);info=policy.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o444 or policy.read_bytes()!=m.DATA:raise ValueError('Current fixed production watchdog policy required')
        if g.run(['/sbin/sysctl','-n','dev.bcmwd.0.%driver'])!='bcmwd' or g.query()!=8*(1<<32):raise ValueError('Production BCM watchdog must already be armed at eight seconds')
        info=g.run(['/usr/sbin/diskinfo',m.DISK]).split()
        if int(info[1])!=m.SECTOR or int(info[2])!=m.DISK_BYTES:raise ValueError('Card geometry changed')
        fd=os.open(m.DISK,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Bound card must be a direct character provider')
            disk_uuid,parts=g.flash.parse_gpt(fd,m.DISK_BYTES,m.SECTOR)
        finally:os.close(fd)
        m.check_gpt(disk_uuid,parts)
        health=json.loads(g.run(['/usr/local/bin/python3.12','-I','/usr/local/libexec/DaOS-recovery/early_adb.py','health']))
        if any(health.get(key)is not True for key in ('ready','supervisor_alive','host_configured','adb_authenticated')):raise ValueError('Authenticated normal early USB is required')
        return {'normal_root_read_only':True,'watchdog_timeout_sbt':8*(1<<32),'disk_uuid':disk_uuid,'root_pins':m.ROOT_PINS}
    def inspect_efi(self):return self.gate.inspect_efi()
    def current_payload(self):
        for name,size,digest in ((RAM_FILE,28565704,RAM_SHA),(DTBO,964,DTBO_SHA)):
            path=self.module.owned(self.root/name)
            if not path.is_file() or path.stat().st_size!=size or sha(path)!=digest:raise ValueError('Current pinned RAM/DTBO differs: '+name)
        shadow=self.root/'boot/DaOS-recovery/root.ufs'
        if shadow.exists() or shadow.is_symlink():raise ValueError('Raw root.ufs would shadow the pinned implicit gzip image')
    def validate_inputs(self):
        self.gate.mounts()
        rows=[line.split() for line in self.gate.run(['/sbin/mount','-p']).splitlines()]
        name=str(self.source);parents=[row for row in rows if len(row)==6 and (name==row[1] or name.startswith(row[1].rstrip('/')+'/'))]
        if not name.startswith('/DaOS-recovery-ram/') or max(parents,key=lambda row:len(row[1]))[2]!='tmpfs':raise ValueError('Inputs must be owned files on the dedicated tmpfs')
        return validate_sources(self.source,self.inputs,self.module.owned)
    def selector_state(self):
        path=self.module.owned(self.root/'boot/nextboot.conf');info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>16384 or getattr(info,'st_flags',0)!=0:raise ValueError('Unsupported nextboot file type/flags/size')
        data=path.read_bytes();after=path.lstat()
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_mode)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_mode):raise ValueError('Nextboot changed during read')
        return data,stat.S_IMODE(info.st_mode)
    def state_location(self):self.gate.mounts(receipt_path=self.work)
    def prepare_work(self):
        self.state_location();parent=self.work.parent
        if not parent.exists():self.module.owned(parent.parent);parent.mkdir(mode=0o700)
        self.module.owned(parent);self.work.mkdir(mode=0o700);self.module.owned(self.work)
    def load_work(self):self.state_location();self.module.owned(self.work);return json.loads(self.module.owned(self.work/'receipt.json').read_text())
    def remount(self,writable):self.gate.remount(writable)
    def assert_root(self,writable):self.gate.assert_root(writable)
    def check_space(self):
        info=os.statvfs(self.root/'boot')
        return space_report(info.f_bavail*info.f_frsize,info.f_bfree*info.f_frsize,sum(entry['bytes'] for entry in self.inputs.values()))
    def secure(self,path):return self.module.owned(path)


def save(runtime,record):atomic(runtime.work/'receipt.json',(json.dumps(record,indent=2)+'\n').encode(),0o600)


def plan(runtime):
    normal=runtime.verify();efi=runtime.inspect_efi();runtime.current_payload();runtime.validate_inputs();runtime.verify()
    before,mode=runtime.selector_state()
    if not cleared(before):raise ValueError('A cleared nextboot selector is required')
    directory=runtime.root/DIRECTORY
    if directory.exists() or directory.is_symlink():raise ValueError('Dedicated candidate directory must be new')
    if runtime.work.exists() or runtime.work.is_symlink():raise ValueError('Trial receipt directory must be new')
    space=runtime.check_space();runtime.state_location();data=selector(runtime.trial)
    return {'format':FORMAT,'status':'planned','trial_nonce':runtime.trial,'normal':normal,'efi':efi,
            'directory':'/'+DIRECTORY,'inputs':runtime.inputs,'source_dir':str(runtime.source),'total_stage_bytes':sum(item['bytes'] for item in runtime.inputs.values()),
            'space':space,
            'nextboot_before_sha256':hashlib.sha256(before).hexdigest(),'nextboot_before_mode':mode,
            'selector_hex':data.hex(),'selector_sha256':hashlib.sha256(data).hexdigest(),'reboot_performed':False,
            'fat_modified':False,'default_kernel_modified':False,'rc_modified':False,'rf_attach_requested':False}


def restore_selector(runtime,record,before):
    current,mode=runtime.selector_state();expected=bytes.fromhex(record['selector_hex'])
    if current==before:return
    if not ours(current,expected):raise ValueError('Nextboot changed externally; refusing rollback')
    atomic(runtime.root/'boot/nextboot.conf',before,record['nextboot_before_mode'],expected=current)


def cleanup_candidates(runtime):
    directory=runtime.root/DIRECTORY
    if not directory.exists():return
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Candidate directory changed externally')
    allowed=set(runtime.inputs)
    for path in directory.iterdir():
        if path.name.startswith('.') and '.trial-' in path.name:raise ValueError('Incomplete candidate needs explicit inspection')
        if path.name not in allowed or path.is_symlink() or sha(path)!=runtime.inputs[path.name]['sha256']:raise ValueError('Candidate file changed externally; refusing removal')
    for path in directory.iterdir():path.unlink()
    directory.rmdir();syncdir(directory.parent)


def apply(runtime):
    record=plan(runtime);before,mode=runtime.selector_state()
    if not cleared(before) or hashlib.sha256(before).hexdigest()!=record['nextboot_before_sha256'] or mode!=record['nextboot_before_mode']:raise ValueError('Cleared nextboot bytes/mode changed after planning')
    runtime.prepare_work()
    atomic(runtime.work/'nextboot.before',before,0o600);record['status']='prepared-selector-still-clear';save(runtime,record)
    owned_directory=False;errors=[]
    try:
        runtime.verify();runtime.current_payload();runtime.validate_inputs()
        runtime.remount(True);runtime.assert_root(True)
        directory=runtime.root/DIRECTORY;directory.mkdir(mode=0o755);owned_directory=True
        for name,entry in runtime.inputs.items():copy_candidate(runtime.source/name,directory/name,entry)
        syncdir(directory);syncdir(directory.parent)
        if runtime.selector_state()!=(before,mode):raise ValueError('Nextboot changed before final publication')
        atomic(runtime.root/'boot/nextboot.conf',bytes.fromhex(record['selector_hex']),mode,expected=before)
        record['status']='selector-published';save(runtime,record)
    except BaseException as error:errors.append(error)
    finally:
        try:runtime.remount(False);runtime.assert_root(False)
        except BaseException as error:errors.append(error)
    if not errors:
        try:
            runtime.verify();runtime.current_payload();runtime.inspect_efi()
            record['status']='armed-no-reboot';record['root_read_only']=True;save(runtime,record)
            return record
        except BaseException as error:errors.append(error)
    root_ro=False
    try:
        if owned_directory:
            runtime.remount(True);runtime.assert_root(True)
            restore_selector(runtime,record,before);record['selector_restored']=True
            cleanup_candidates(runtime)
    except BaseException as error:errors.append(error)
    finally:
        try:runtime.remount(False);runtime.assert_root(False);root_ro=True
        except BaseException as error:errors.append(error)
    try:complete=root_ro and runtime.selector_state()[0]==before and not (runtime.root/DIRECTORY).exists()
    except BaseException as error:errors.append(error);complete=False
    record.update(status='failed-rollback-complete' if complete else 'failed-rollback-incomplete',rollback_complete=complete,root_read_only=root_ro,errors=list(map(str,errors)))
    try:save(runtime,record)
    except BaseException as error:errors.append(error)
    raise RuntimeError('Trial staging failed; selector rollback and root RO attempted: '+'; '.join(map(str,errors)))


def clear_nextboot(runtime):
    runtime.verify();record=runtime.load_work()
    if record.get('format')!=FORMAT or record.get('trial_nonce')!=runtime.trial or record.get('directory')!='/'+DIRECTORY or record.get('inputs')!=runtime.inputs or record.get('selector_hex')!=selector(runtime.trial).hex():raise ValueError('Stored trial receipt differs')
    before=runtime.secure(runtime.work/'nextboot.before').read_bytes()
    if not cleared(before) or hashlib.sha256(before).hexdigest()!=record.get('nextboot_before_sha256') or record.get('nextboot_before_mode') not in (0o400,0o440,0o444,0o600,0o640,0o644):raise ValueError('Cleared selector backup differs')
    current,_=runtime.selector_state()
    if current!=before and not ours(current,bytes.fromhex(record['selector_hex'])):raise ValueError('External nextboot must not be overwritten')
    errors=[]
    try:
        runtime.remount(True);runtime.assert_root(True);restore_selector(runtime,record,before)
    except BaseException as error:errors.append(error)
    finally:
        try:runtime.remount(False);runtime.assert_root(False)
        except BaseException as error:errors.append(error)
    if errors:raise RuntimeError('Clear failed; root RO attempted: '+'; '.join(map(str,errors)))
    runtime.verify();record.update(status='cleared-no-reboot',root_read_only=True);save(runtime,record);return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',nargs='?',default='plan',choices=('plan','apply','clear-nextboot'))
    parser.add_argument('--source-dir',type=Path,default=SOURCE);parser.add_argument('--trial-nonce');args=parser.parse_args()
    if args.action!='plan' and args.trial_nonce is None:parser.error('apply/clear-nextboot require the reviewed plan --trial-nonce')
    runtime=Native(args.source_dir,args.trial_nonce or uuid.uuid4().hex)
    for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(number,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt('Trial staging interrupted')))
    with runtime.gate.flash.lock(Path('/DaOS-recovery-ram/flash')):
        result={'plan':plan,'apply':apply,'clear-nextboot':clear_nextboot}[args.action](runtime)
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())

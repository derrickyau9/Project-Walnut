#!/usr/local/bin/python3.12
"""Enable only the fixed watchdog policy after the pinned normal/rescue checks.

No watchdog is armed, process started, executable replaced, or reboot requested.
The parent must independently approve the physical RAM-only reset proof first.
"""
from __future__ import annotations
import argparse
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import uuid

RAM=Path('/DaOS-recovery-ram')
POLICY=Path('/etc/daos/recovery-watchdog.enabled')
DATA=b'DaOS-recovery-watchdog-v1\n'
FORMAT='DaOS-recovery-watchdog-enable-v1'
DISK='/dev/mmcsd0'
DISK_UUID='d0a0ead0-d9ab-11f1-2887-fbd68f5c160e'
DISK_BYTES=63864569856
SECTOR=512
WDIOC_GETTIMEOUT=0x40085736
# Pinned FreeBSD 95719d8... sys/sys/kenv.h and include/kenv.h.
# KENV_GET is read-only; sys_kenv returns -1/ENOENT for an absent name.
KENV_GET=0
KENV_VALUE_BYTES=129
PARTITIONS={
    1:dict(uuid='d0a0e3aa-d9ab-11f1-067b-ff9d86f8dced',start_lba=40,sectors=262144,type='c12a7328-f81f-11d2-ba4b-00a0c93ec93b',label='DaOS-efi'),
    2:dict(uuid='d0a0e3be-d9ab-11f1-1d08-61407e6af4da',start_lba=262184,sectors=5242880,type='516e7cb6-6ecf-11d6-8ff8-00022d09712b',label='DaOS-root'),
    3:dict(uuid='d0a0e3be-d9ab-11f1-17a5-d14fff7c5968',start_lba=5505064,sectors=262144,type='516e7cb6-6ecf-11d6-8ff8-00022d09712b',label='DaOS-state'),
    4:dict(uuid='d0a0e3be-d9ab-11f1-0432-f1d3b03b0818',start_lba=5767208,sectors=65536,type='516e7cb6-6ecf-11d6-8ff8-00022d09712b',label='DaOS-calibration'),
    5:dict(uuid='d0a0e3be-d9ab-11f1-3fe7-9181628e1ee9',start_lba=5832744,sectors=118900696,type='516e7cb6-6ecf-11d6-8ff8-00022d09712b',label='DaOS-photos')}
ROOT_PINS={
    '/boot/kernel/kernel':'5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec',
    '/sbin/init':'7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427',
    '/usr/local/libexec/DaOS-recovery/early-init.sh':'f43adcab9d87d041cfa9df807b3826916749bd3ff2332dfb53ff240cfbcdb95f',
    '/usr/local/libexec/DaOS-recovery/kernel-watchdog':'b06cdf2b16b375c6d287a48e3c7b7d8196b0a9cc9bcc6b12f1a3dd2db1fdcdac',
    '/usr/local/libexec/daos-usb-adb':'d6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa',
    '/etc/rc.d/DaOS-rescue-clean':'bdda4eb930dbbca728a4fb443b026ce81acd950ae24d6f11cf063ff231ee75ec',
    '/boot/kernel/usb_template.ko':'c385973ab0fd4483ad8238bfc760f4cdffc5a1b41213a7dca6ad4ce6be6f4fbc',
    '/boot/modules/daos_adb_template.ko':'dfbfa4346cad57d2dc581f1b2b28f5389f221d386b6f58ca36a3364b76e19e67'}
EFI_PINS={
    'config.txt':'3cca8c524fcd65b4a8b3fd659106e0c67a49c314f4cb3dab145e70a74b633f0b',
    'u-boot-daos-rescue.bin':'592cd960b7d41a12a87de55d78492b467a174e0c381b6aa637f0a916d0bf8312',
    'u-boot.bin':'9ca13c64711d947be830382d50a8459b945e01626eaa4c497843d72448818b53',
    'EFI/BOOT/BOOTAA64.EFI':'7374ad7c46452c29e24f090e9ee2c9cc4ee5013a82c28f836b71113691fe0472',
    'boot.scr':'05e286e0bc3db1a6fdfdb80867d2c672bfc4f9f95ccc206badc023220e6fb447',
    'DaOS-recovery/root.ufs.gz':'e6af2f549a0c6935c83c17b0e1739bd6add522bcedff813e42b9be5d14ed8e2c',
    'DaOS-recovery/loader.lua':'2bd2b7bc031ff260590200b82bcfc960c837052db4ff0f9069eb2738f227f159',
    'DaOS-recovery/loader.efi':'8567870cea56264ebee45c573196a04b4fadf5d7d36a52c303ba5336248b30f4',
    'DaOS-recovery/kernel/kernel':ROOT_PINS['/boot/kernel/kernel'],
    'DaOS-recovery/kernel/usb_template.ko':ROOT_PINS['/boot/kernel/usb_template.ko'],
    'DaOS-recovery/kernel/daos_adb_template.ko':ROOT_PINS['/boot/modules/daos_adb_template.ko']}


def sha(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def kenv_get(name,function=None):
    if function is None:
        function=ctypes.CDLL(None,use_errno=True).kenv
        function.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.POINTER(ctypes.c_char),ctypes.c_int]
        function.restype=ctypes.c_int
    buffer=ctypes.create_string_buffer(KENV_VALUE_BYTES);ctypes.set_errno(0)
    count=function(KENV_GET,name.encode('ascii'),buffer,len(buffer))
    if count==-1:
        error=ctypes.get_errno()
        if error==errno.ENOENT:return None
        raise OSError(error or errno.EIO,'Read-only KENV_GET failed for '+name)
    if not 1<=count<=len(buffer) or buffer.raw[count-1]!=0 or b'\0' in buffer.raw[:count-1]:
        raise ValueError('Invalid or truncated KENV_GET result for '+name)
    return buffer.raw[:count-1].decode('ascii')


def normal_environment(getter=kenv_get):
    recovery=getter('daos_recovery')
    if recovery is not None and recovery.strip()=='YES':raise ValueError('Rescue kernel environment remains active')
    if getter('init_script')!='/usr/local/libexec/DaOS-recovery/early-init.sh':raise ValueError('Early init hook is not active')


def owned(path):
    path=Path(path)
    for parent in (path,*path.parents):
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('Untrusted or redirected path: '+str(parent))
    return path


def syncdir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def check_gpt(disk_uuid,parts):
    if disk_uuid!=DISK_UUID or parts!=PARTITIONS:raise ValueError('Exact five-partition dual GPT identity differs')


def check_efi_files(folder,pins=EFI_PINS):
    for name,digest in pins.items():
        path=folder/name
        if any(p.is_symlink()for p in (path,*path.parents)) or not path.is_file() or sha(path)!=digest:raise ValueError('Pinned FAT v3 file differs: '+name)
    marker=folder/'DaOS-recovery/normal.running'
    if marker.is_symlink() or not marker.is_file() or marker.read_bytes()!=b'\1':raise ValueError('Sticky normal.running must equal one')
    return {'marker':1,'files_sha256':pins}


def cleanup_efi(folder,run):
    """A mount can finish before its invoking process reports an error."""
    def mounted():
        rows=[line.split() for line in run(['/sbin/mount','-p']).splitlines() if line.strip()]
        if any(len(row)!=6 for row in rows):raise ValueError('Malformed cleanup mount table')
        return [row for row in rows if row[1]==str(folder)]
    try:
        rows=mounted()
        if rows:
            if len(rows)!=1 or rows[0][0]!=DISK+'p1' or rows[0][2]!='msdosfs':raise ValueError('Refusing cleanup of unexpected mount at owned EFI directory')
            run(['/sbin/umount',folder])
    finally:
        if not mounted():folder.rmdir()


def remove_own_policy(path,identity):
    info=path.lstat()
    if (info.st_dev,info.st_ino)!=identity:raise ValueError('Policy changed externally; refusing to remove it')
    path.unlink();syncdir(path.parent)


def publish_policy(path,ownership=None):
    """An exclusive hard-link publishes the fully synced fixed file atomically."""
    temporary=path.with_name('.watchdog-policy-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    identity=None;published=False
    if ownership is None:ownership={}
    try:
        with os.fdopen(fd,'wb') as file:
            file.write(DATA);os.fchmod(file.fileno(),0o444);os.fsync(file.fileno())
            info=os.fstat(file.fileno());identity=(info.st_dev,info.st_ino);ownership['identity']=identity
        os.link(temporary,path);published=True;ownership['published']=True
        temporary.unlink();syncdir(path.parent)
        info=path.lstat()
        if (info.st_dev,info.st_ino)!=identity or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o444 or path.read_bytes()!=DATA:
            raise ValueError('Fixed watchdog policy readback failed')
        return identity
    except BaseException:
        # Ownership is recorded before publication, including interruption
        # immediately after link(2) but before the Python assignment above.
        if identity is not None and path.exists():
            info=path.lstat()
            if (info.st_dev,info.st_ino)==identity:remove_own_policy(path,identity)
            elif published:raise ValueError('Policy changed externally; refusing to remove it')
        raise
    finally:
        if temporary.exists():temporary.unlink()


def mount_guard(labels,text,*,root_rw=False,receipt_path=None):
    aliases={}
    for line in labels.splitlines():
        fields=line.split()
        if len(fields)!=3 or '/dev/'+fields[0] in aliases:raise ValueError('Malformed GEOM aliases')
        aliases['/dev/'+fields[0]]='/dev/'+fields[2]
    def resolve(source):
        seen=set()
        while source in aliases:
            if source in seen:raise ValueError('GEOM alias cycle')
            seen.add(source);source=aliases[source]
        return source
    rows=[line.split() for line in text.splitlines() if line.strip()]
    if any(len(row)!=6 for row in rows):raise ValueError('Malformed mount table')
    roots=[row for row in rows if row[1]=='/']
    mode='rw' if root_rw else 'ro'
    if len(roots)!=1 or resolve(roots[0][0])!=DISK+'p2' or roots[0][2]!='ufs' or mode not in roots[0][3].split(',') or ('ro' if root_rw else 'rw') in roots[0][3].split(','):
        raise ValueError('Exact normal SD UFS root must be '+mode)
    if any(resolve(row[0]) in (DISK,DISK+'p1') for row in rows):raise ValueError('EFI is already mounted')
    private=[row for row in rows if row[1]==str(RAM)]
    if len(private)!=1 or private[0][2]!='tmpfs' or 'rw' not in private[0][3].split(','):raise ValueError('Dedicated recovery tmpfs required')
    if receipt_path is not None:
        candidates=[row for row in rows if str(receipt_path)==row[1] or str(receipt_path).startswith(row[1].rstrip('/')+'/')]
        row=max(candidates,key=lambda row:len(row[1]))
        if resolve(row[0])!=DISK+'p3' or row[2]!='ufs' or 'rw' not in row[3].split(','):raise ValueError('Receipt must reside directly on bound writable state')
    return rows


class Native:
    policy=POLICY
    def __init__(self):
        spec=importlib.util.spec_from_file_location('enable_watchdog_gpt','/usr/local/libexec/DaOS-recovery/flash.py')
        self.flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.flash)
    def run(self,args):
        result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError(str(args)+': '+result.stderr[-800:])
        return result.stdout.strip()
    def mounts(self,root_rw=False,receipt_path=None):
        return mount_guard(self.run(['/sbin/geom','label','status','-s']),self.run(['/sbin/mount','-p']),root_rw=root_rw,receipt_path=receipt_path)
    def query(self):
        fd=os.open('/dev/fido',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Watchdog device is not a character device')
            data=bytearray(8);fcntl.ioctl(fd,WDIOC_GETTIMEOUT,data,True)
            return struct.unpack('=q',data)[0]
        finally:os.close(fd)
    def verify(self):
        if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root required')
        if Path('/run/daos-recovery/ram-root.json').exists() or Path('/run/daos-recovery/ram-root.json').is_symlink():raise ValueError('RAM rescue cannot enable normal watchdog policy')
        if self.policy.exists() or self.policy.is_symlink():raise ValueError('Watchdog policy must be absent')
        owned(self.policy.parent);owned(RAM)
        if stat.S_IMODE(RAM.stat().st_mode)!=0o700:raise ValueError('Recovery tmpfs permissions differ')
        self.mounts()
        normal_environment()
        if self.run(['/sbin/sysctl','-n','kern.bootfile'])!='/boot/kernel/kernel':raise ValueError('Stable normal kernel is required')
        for name,digest in ROOT_PINS.items():
            file=owned(name)
            if not file.is_file() or sha(file)!=digest:raise ValueError('Pinned normal file differs: '+name)
        fd=os.open(DISK,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Bound card is not a character device')
            if int(self.run(['/usr/sbin/diskinfo',DISK]).split()[2])!=DISK_BYTES:raise ValueError('Card geometry changed')
            disk_uuid,parts=self.flash.parse_gpt(fd,DISK_BYTES,SECTOR)
        finally:os.close(fd)
        check_gpt(disk_uuid,parts)
        health=json.loads(self.run(['/usr/local/bin/python3.12','-I','/usr/local/libexec/DaOS-recovery/early_adb.py','health']))
        if health.get('format')!='DaOS-early-recovery-adb-health-v1' or any(health.get(key)is not True for key in ('ready','supervisor_alive','host_configured','adb_authenticated')):
            raise ValueError('Normal early USB must currently be authenticated')
        if self.run(['/sbin/sysctl','-n','dev.bcmwd.0.%driver'])!='bcmwd' or self.query()!=0:raise ValueError('BCM watchdog must be attached and unarmed')
        return {'disk_uuid':disk_uuid,'partitions_checked':5,'early_usb_authenticated':True,'watchdog_timeout_sbt':0,
                'root_files_sha256':ROOT_PINS,'boot_id':self.run(['/sbin/sysctl','-n','kern.boottime'])}
    def inspect_efi(self):
        self.mounts();folder=RAM/('watchdog-efi-'+uuid.uuid4().hex);folder.mkdir(mode=0o700)
        try:
            self.run(['/sbin/mount','-t','msdosfs','-o','ro',DISK+'p1',folder])
            rows=[line.split() for line in self.run(['/sbin/mount','-p']).splitlines()]
            found=[row for row in rows if len(row)==6 and row[1]==str(folder)]
            if len(found)!=1 or found[0][0]!=DISK+'p1' or found[0][2]!='msdosfs' or 'ro'not in found[0][3].split(',') or 'rw'in found[0][3].split(','):raise ValueError('Exact private read-only EFI mount required')
            return check_efi_files(folder)
        finally:
            cleanup_efi(folder,self.run)
    def remount(self,writable):self.run(['/sbin/mount','-u','-w' if writable else '-r','/'])
    def assert_root(self,writable):self.mounts(root_rw=writable)
    def receipt(self,record):
        directory=Path('/var/DaOS/usb-recovery/watchdog-enable');self.mounts(receipt_path=directory)
        missing=[];parent=directory
        while not parent.exists():missing.append(parent);parent=parent.parent
        owned(parent)
        for parent in reversed(missing):parent.mkdir(mode=0o700)
        owned(directory);path=directory/('enable-'+uuid.uuid4().hex+'.json')
        record['receipt']=str(path)
        with path.open('x') as file:json.dump(record,file,indent=2);file.write('\n');file.flush();os.fchmod(file.fileno(),0o600);os.fsync(file.fileno())
        syncdir(directory);return str(path)


def execute(action,runtime):
    if action not in ('plan','enable'):raise ValueError('Unknown action')
    normal=runtime.verify();efi=runtime.inspect_efi();runtime.verify()
    record={'format':FORMAT,'action':action,'status':'planned','normal':normal,'efi':efi,'watchdog_started':False,'reboot_performed':False,'policy_sha256':hashlib.sha256(DATA).hexdigest()}
    if action=='plan':return record
    ownership={};errors=[]
    try:
        runtime.remount(True);runtime.assert_root(True)
        publish_policy(runtime.policy,ownership)
    except BaseException as error:errors.append(error)
    finally:
        try:runtime.remount(False);runtime.assert_root(False)
        except BaseException as error:errors.append(error)
    if errors:
        identity=ownership.get('identity');remove=False
        if identity is not None and runtime.policy.exists():
            info=runtime.policy.lstat();remove=(info.st_dev,info.st_ino)==identity
            if not remove and ownership.get('published'):errors.append(ValueError('Policy changed externally; refusing to remove it'))
        if remove:
            try:
                runtime.remount(True);runtime.assert_root(True);remove_own_policy(runtime.policy,identity)
            except BaseException as error:errors.append(error)
        # Retry restoring RO even when publication failed before there was a
        # policy to remove, or an external replacement prevented rollback.
        try:runtime.remount(False);runtime.assert_root(False)
        except BaseException as error:errors.append(error)
        raise RuntimeError('Enable failed; own policy rollback and root RO restoration attempted: '+'; '.join(map(str,errors)))
    record.update(status='enabled-policy-only',root_read_only=True,policy_bytes=len(DATA),policy_mode='0444')
    try:record['receipt']=runtime.receipt(record)
    except Exception as error:record.update(receipt=None,receipt_error=str(error))
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('plan','enable'));args=parser.parse_args()
    runtime=Native();runtime.verify()
    for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(number,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt('Watchdog policy enable interrupted')))
    with runtime.flash.lock(RAM/'flash'):result=execute(args.action,runtime)
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())

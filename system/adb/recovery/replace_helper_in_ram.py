#!/usr/local/bin/python3.12
"""Replace only the normal-root USB helper from authenticated RAM rescue.

The physical normal root is unmounted at entry/exit and never executed here.
Candidate must already be a secure ordinary file in the recovery incoming RAM.
No reboot, authentication, module, policy, GPT or other-partition writes.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import uuid

HELPER='usr/local/libexec/daos-usb-adb'
KERNEL='5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec'
INIT='7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427'


def sha(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def elf(data):
    if len(data)<64 or data[:8]!=b'\x7fELF\x02\x01\x01\x09' or struct.unpack_from('<HHI',data,16) not in ((2,183,1),(3,183,1)):
        raise ValueError('FreeBSD ARM64 executable ELF required')
    offset=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
    if offset<64 or size!=56 or not 1<=count<=64 or offset+size*count>len(data):raise ValueError('Invalid ELF program table')
    if not any(struct.unpack_from('<I',data,offset+size*i)[0]==1 for i in range(count)):raise ValueError('ELF has no loadable segment')


def unredirected(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Redirected maintenance path')
    if path.exists():
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022:raise ValueError('Untrusted ordinary maintenance file')
    return path


def atomic(path,data,mode,token):
    unredirected(path);tmp=path.with_name(path.name+'.'+token+'.tmp')
    try:
        with tmp.open('xb') as file:
            file.write(data);os.chmod(tmp,mode);file.flush();os.fsync(file.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)


def command(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=30).strip()


class Runtime:
    def __init__(self,flash,backend):self.flash=flash;self.backend=backend;self.mounted=False
    def prove(self):
        target=self.backend.prove('root')
        self.check_mounts()
        return target
    def check_mounts(self,owned=None,mode=None):
        rows=[line.split() for line in command(['/rescue/mount','-p']).splitlines()]
        if any(len(row)!=6 for row in rows):raise ValueError('Malformed RAM mount table')
        roots=[row for row in rows if row[1]=='/']
        if len(roots)!=1:raise ValueError('Unique verified RAM root required')
        matches=[]
        for row in rows:
            if owned is not None and row[1]==str(owned[0]):
                matches.append(row)
                if row[0]!=str(owned[1]) or row[2]!='ufs' or mode not in row[3].split(','):raise ValueError('Owned normal-root mount changed')
            elif row[1]=='/':continue  # NativeBackend.prove already proved RAM.
            elif row[2] not in ('tmpfs','devfs','procfs','fdescfs'):
                raise ValueError('All physical filesystems must remain unmounted')
        if owned is not None and len(matches)!=1:raise ValueError('Owned normal-root mount missing')
    def mount(self,provider,path):
        try:
            command(['/rescue/mount','-t','ufs','-o','ro',str(provider),str(path)])
            self.mounted=True
            self.check_mounts((path,provider),'ro')
        except BaseException:
            # A mount can succeed before a verification/transport error.
            # This mountpoint is newly owned and had no filesystem at entry.
            if not self.mounted:
                try:
                    rows=[line.split() for line in command(['/rescue/mount','-p']).splitlines()]
                    self.mounted=any(len(row)==6 and row[1]==str(path) for row in rows)
                except BaseException:self.mounted=True  # Unknown: keep cleanup ownership.
            try:self.unmount(path)
            except BaseException:pass
            raise
    def writable(self,provider,path):
        command(['/rescue/mount','-u','-w',str(path)])
        self.check_mounts((path,provider),'rw')
    def readonly(self,provider,path):
        command(['/rescue/mount','-u','-r',str(path)])
        self.check_mounts((path,provider),'ro')
    def unmount(self,path):
        command(['/rescue/umount',str(path)]);self.mounted=False


def maintain(candidate,before,after,runtime):
    flash=runtime.flash
    if any(not re.fullmatch('[a-f0-9]{64}',v or '') for v in (before,after)) or before==after:raise ValueError('Distinct exact helper hashes required')
    # Full NativeBackend proof and all-media isolation before physical mounting.
    runtime.prove()
    candidate=flash.secure_path(candidate)
    if candidate.parent!=flash.WORK/'incoming' or not candidate.is_file():raise ValueError('Candidate must be an ordinary recovery incoming RAM file')
    data=candidate.read_bytes()
    if not 64<=len(data)<=2*1024**2 or hashlib.sha256(data).hexdigest()!=after:raise ValueError('Candidate SHA256/size mismatch')
    elf(data)
    with flash.lock(flash.WORK):
        target=runtime.prove();provider=target['path']
        token=uuid.uuid4().hex;work=flash.WORK/('helper-maintenance-'+token);work.mkdir(mode=0o700)
        mount=work/'root';mount.mkdir(mode=0o700)
        record={'format':'DaOS-offline-helper-maintenance-v1','status':'prepared','before_sha256':before,'after_sha256':after,
                'target_identity':target['identity'],'physical_raw_writes':False,'root_provider':str(provider),
                'root_unmounted':False,'root_read_only':False,'reboot_requested':False,'helper_restarted':False}
        receipt=work/'receipt.json'
        def save():atomic(receipt,(json.dumps(record,indent=2)+'\n').encode(),0o600,token)
        save();mounted=False;attempted_rw=False;original=None;path=None;root_backup=None
        def restore():
            if path is not None and original is not None and sha(unredirected(path))!=before:
                runtime.writable(provider,mount)
                atomic(path,original,0o755,token)
                if sha(unredirected(path))!=before:raise ValueError('Offline original helper rollback mismatch')
                record['status']='failed-rolled-back'
        try:
            runtime.mount(provider,mount);mounted=True
            path=unredirected(mount/HELPER)
            if sha(path)!=before or sha(unredirected(mount/'boot/kernel/kernel'))!=KERNEL or sha(unredirected(mount/'sbin/init'))!=INIT:
                raise ValueError('Offline helper/kernel/init identity differs')
            if stat.S_IMODE(path.stat().st_mode)!=0o755:raise ValueError('Original helper mode differs')
            original=path.read_bytes()
            ram_backup=work/'daos-usb-adb.original';atomic(ram_backup,original,0o700,token)
            if sha(ram_backup)!=before:raise ValueError('RAM backup readback mismatch')
            record['ram_backup']=str(ram_backup)
            root_backup=path.with_name('daos-usb-adb.recovery-backup-'+token)
            if root_backup.exists() or root_backup.is_symlink():raise ValueError('Owned root backup name already exists')
            record['root_backup_relative']=root_backup.relative_to(mount).as_posix();save()
            attempted_rw=True
            runtime.writable(provider,mount)
            atomic(root_backup,original,0o700,token)
            if sha(root_backup)!=before:raise ValueError('Owned root backup readback mismatch')
            if sha(unredirected(path))!=before:raise ValueError('Offline target changed before publication')
            atomic(path,data,0o755,token)
            if sha(unredirected(path))!=after or stat.S_IMODE(path.stat().st_mode)!=0o755:raise ValueError('Offline helper readback mismatch')
            record['status']='helper-replaced-offline';save()
        except BaseException as error:
            record['status']='failed';record['error']=str(error)
            if mounted and attempted_rw:
                try:restore()
                except BaseException as rollback_error:record['rollback_error']=str(rollback_error);record['status']='failed-rollback-refused'
        finally:
            mounted=mounted or runtime.mounted
            if mounted:
                try:runtime.readonly(provider,mount);record['root_read_only']=True
                except BaseException as error:
                    record['root_restore_error']=str(error);record['status']='failed-root-readonly-restore'
                    try:
                        if attempted_rw:restore()
                        runtime.readonly(provider,mount);record['root_read_only']=True
                    except BaseException as restore_error:record['root_restore_error']=str(restore_error)
                try:runtime.unmount(mount);mounted=False;record['root_unmounted']=True
                except BaseException as error:record['unmount_error']=str(error);record['status']='failed-root-unmount'
            if not mounted:
                try:
                    post=runtime.prove()
                    if post['identity']!=target['identity']:raise ValueError('Bound identity changed after maintenance')
                    record['post_native_proof']=True
                except BaseException as error:record['post_proof_error']=str(error);record['status']='failed-post-proof'
            try:save()
            except BaseException as error:record['receipt_error']=str(error);record['status']='failed-receipt'
        return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True);parser.add_argument('--beforeSHA',required=True);parser.add_argument('--afterSHA',required=True)
    args=parser.parse_args()
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD RAM rescue uid0 required')
    path=Path('/usr/local/libexec/DaOS-recovery/flash.py')
    spec=importlib.util.spec_from_file_location('installed_recovery_flash',path)
    flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(flash)
    record=maintain(args.candidate,args.beforeSHA,args.afterSHA,Runtime(flash,flash.NativeBackend()))
    print(json.dumps(record,separators=(',',':')),flush=True)
    sys.stdout.flush();os._exit(0 if record['status']=='helper-replaced-offline' and record.get('post_native_proof') and record.get('root_unmounted') else 1)


if __name__=='__main__':main()

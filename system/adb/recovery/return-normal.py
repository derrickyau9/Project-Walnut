"""Clear the optional FAT crash marker from authenticated RAM rescue, then reboot.

Uploaded as a small utility by host.py; the frozen rescue image need not contain
it. This tool writes exactly the existing one-byte normal.running file. It
cannot format EFI, rewrite GPT, or alter partition images.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

FLASH=Path('/usr/local/libexec/DaOS-recovery/flash.py')
MOUNT=Path('/DaOS-recovery-ram/return-normal-efi')
EFI_GUID='c12a7328-f81f-11d2-ba4b-00a0c93ec93b'


def load_flash():
    spec=importlib.util.spec_from_file_location('daos_installed_flash_gate',FLASH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def command(flash,args):
    try:return flash.command(args)
    except subprocess.CalledProcessError as error:
        detail=error.output or ''
        if isinstance(detail,bytes):detail=detail.decode(errors='replace')
        raise RuntimeError('Recovery command failed: '+str(args)+': '+detail.strip()) from error


def aliases(flash):
    result={}
    for line in command(flash,['/sbin/geom','label','status','-s']).splitlines():
        fields=line.split()
        if len(fields)!=3:raise ValueError('Unexpected GEOM alias table')
        result['/dev/'+fields[0]]='/dev/'+fields[2]
    return result


def resolved(source,labels):
    seen=set()
    while source in labels:
        if source in seen:raise ValueError('GEOM alias cycle')
        seen.add(source);source=labels[source]
    return source


def rows(flash):
    result=[line.split() for line in command(flash,['/sbin/mount','-p']).splitlines() if line.strip()]
    if any(len(row)!=6 for row in result):raise ValueError('Unexpected mount table')
    return result


def select_normal(flash):
    backend=flash.NativeBackend();backend.prove('root')
    backend.work.mkdir(mode=0o700,parents=True,exist_ok=True);flash.secure_path(backend.work)
    with flash.lock(backend.work):return select_normal_locked(flash)


def select_normal_locked(flash):
    identity=json.loads(flash.secure_path(flash.IDENTITY).read_text())
    disk=identity['disk']
    fd=os.open(disk,os.O_RDONLY|os.O_NOFOLLOW)
    try:disk_uuid,parts=flash.parse_gpt(fd,identity['disk_bytes'],identity['sector_size'])
    finally:os.close(fd)
    if disk_uuid!=identity['disk_uuid']:raise ValueError('Card identity changed')
    part=parts.get(1,{})
    if part.get('type')!=EFI_GUID or part.get('label')!='DaOS-efi':
        raise ValueError('Fixed first DaOS EFI partition is unavailable')
    provider=disk+'p1';labels=aliases(flash)
    before=rows(flash)
    if any(resolved(row[0],labels)==provider or row[1]==str(MOUNT) for row in before):
        raise ValueError('EFI provider or return-normal mountpoint is already mounted')
    if MOUNT.exists() or MOUNT.is_symlink():raise ValueError('Fresh private return-normal mountpoint required')
    MOUNT.mkdir(mode=0o700)
    mounted=False;marker_bytes_written=0;marker_absent=False
    try:
        command(flash,['/rescue/rescue','mount_msdosfs','-o','ro',provider,str(MOUNT)]);mounted=True
        current=[row for row in rows(flash) if row[1]==str(MOUNT)]
        if len(current)!=1 or current[0][2]!='msdosfs' or resolved(current[0][0],labels)!=provider:
            raise ValueError('Mounted EFI identity differs from the bound provider')
        marker=MOUNT/'DaOS-recovery/normal.running'
        if any(p.is_symlink() for p in (marker,marker.parent)):raise ValueError('Redirected FAT marker')
        if not marker.exists():
            # Ordinary one-shot rescue never installs the automatic crash
            # marker. Absence needs no FAT write and is not a failed repair.
            marker_absent=True
        else:
            fd=os.open(marker,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                info=os.fstat(fd);value=os.read(fd,2)
                if not stat.S_ISREG(info.st_mode) or info.st_size!=1 or info.st_nlink!=1 or value not in (b'\0',b'\1'):
                    raise ValueError('Crash marker must be an existing exact one-byte 0/1 file')
            finally:os.close(fd)
            command(flash,['/rescue/rescue','umount',str(MOUNT)]);mounted=False
            flash.NativeBackend().prove('root')
            command(flash,['/rescue/rescue','mount_msdosfs','-o','rw',provider,str(MOUNT)]);mounted=True
            current=[row for row in rows(flash) if row[1]==str(MOUNT)]
            if len(current)!=1 or current[0][2]!='msdosfs' or resolved(current[0][0],labels)!=provider:
                raise ValueError('Writable EFI identity differs from the bound provider')
            if any(p.is_symlink() for p in (marker,marker.parent)):raise ValueError('Redirected writable FAT marker')
            fd=os.open(marker,os.O_RDWR|os.O_NOFOLLOW)
            try:
                info=os.fstat(fd);value=os.read(fd,2)
                if not stat.S_ISREG(info.st_mode) or info.st_size!=1 or info.st_nlink!=1 or value not in (b'\0',b'\1'):
                    raise ValueError('Writable crash marker changed')
                flash.NativeBackend().prove('root')
                os.lseek(fd,0,os.SEEK_SET)
                if os.write(fd,b'\0')!=1:raise OSError('Short crash marker write')
                marker_bytes_written=1
                os.fsync(fd);os.lseek(fd,0,os.SEEK_SET)
                if os.read(fd,2)!=b'\0':raise OSError('Crash marker readback failed')
            finally:os.close(fd)
            os.sync()
        command(flash,['/rescue/rescue','umount',str(MOUNT)]);mounted=False
        if any(row[1]==str(MOUNT) for row in rows(flash)):raise ValueError('EFI unmount was not confirmed')
    finally:
        if mounted:command(flash,['/rescue/rescue','umount',str(MOUNT)])
        if not any(row[1]==str(MOUNT) for row in rows(flash)):MOUNT.rmdir()
    return {'status':'normal-boot-selected','marker_bytes_written':marker_bytes_written,
            'marker_absent':marker_absent,'reboot_requested':True,
            'disk_uuid':disk_uuid,'efi_uuid':part['uuid']}


def check_quick_reboot(flash):
    """Skip normal shutdown only with a proven RAM root and protected media."""
    flash.NativeBackend().prove('root')
    for row in rows(flash):
        source,target,kind,options,_,_=row
        modes=options.split(',')
        if ('ro' in modes)==('rw' in modes):raise ValueError('Unclear mount mode before quick reboot')
        if target=='/' or kind in ('tmpfs','devfs','procfs','fdescfs','nullfs'):continue
        if 'rw' in modes:raise ValueError('Writable physical filesystem prevents quick reboot: '+target)
    if command(flash,['/rescue/rescue','sysctl','-n','vm.swap_total']).strip()!='0':
        raise ValueError('Active swap prevents quick reboot')


def main():
    flash=load_flash();receipt=select_normal(flash)
    check_quick_reboot(flash)
    # The ordinary reboot utility SIGTERMs adbd and then sleeps. adbd's shell
    # cleanup can SIGKILL that same process group before reboot reaches the
    # kernel. Detach a static RAM-only delayed helper and return the receipt
    # through the normal shell exit packet before the quick reboot syscall.
    child=subprocess.Popen(['/rescue/sh','-c','/rescue/sleep 1; exec /rescue/reboot -qn'],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        start_new_session=True,close_fds=True,
        env={'PATH':'/rescue','HOME':'/','LC_ALL':'C'})
    receipt.update(reboot_mode='detached-quick-protected-RAM',reboot_helper_pid=child.pid)
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()

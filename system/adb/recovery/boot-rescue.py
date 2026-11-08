"""Select a one-shot RAM rescue boot from a still-working authenticated shell."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

BASE=Path('/boot/DaOS-recovery')
NEXT=Path('/boot/nextboot.conf')
RAM=Path('/DaOS-recovery-ram')
SETTINGS={'kernel':'DaOS-recovery/kernel','module_path':'/boot/DaOS-recovery/kernel',
          'mfsroot_load':'YES','mfsroot_type':'md_image','mfsroot_name':'/boot/DaOS-recovery/root.ufs',
          'vfs.root.mountfrom':'ufs:/dev/md0','vfs.root.mountfrom.options':'rw',
          'daos_recovery':'YES','usb_template_load':'YES','daos_adb_template_load':'YES',
          'rpi_ft5406_load':'NO','sdio_load':'NO','if_brcmfmac_load':'NO','daos_wifi_trial':'',
          'hw.usb.template':'-1','boot_single':'NO','boot_mute':'NO',
          'init_script':'/usr/local/libexec/DaOS-recovery/early-init.sh'}


def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def validate(base=BASE):
    if any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('Redirected rescue directory')
    manifest=json.loads((base/'payload.json').read_text())
    if manifest.get('format')!='DaOS-RAM-rescue-payload-v1':raise ValueError('Unknown rescue payload')
    if set(manifest['files'])!={'kernel/kernel','kernel/usb_template.ko','kernel/daos_adb_template.ko','root.ufs.gz'}:raise ValueError('Incomplete rescue payload')
    for name,entry in manifest['files'].items():
        p=base/name
        if any(v.is_symlink() for v in (p,*p.parents)) or p.stat().st_size!=entry['bytes'] or sha(p)!=entry['sha256']:raise ValueError('Rescue payload changed: '+name)
    return manifest


def selector():return ('nextboot_enable="YES"\n'+''.join(k+'="'+v+'"\n' for k,v in SETTINGS.items())).encode()


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','arm','reboot']);a=p.parse_args()
    payload=validate()
    if a.action=='plan':print(json.dumps({'payload_verified':True,'requires_primary_root_readable':True,'nextboot':SETTINGS}));return
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root required')
    if NEXT.is_symlink() or not NEXT.read_bytes().startswith(b'nextboot_enable="NO"'):raise ValueError('Another one-shot boot is pending')
    ram=RAM
    rows=subprocess.check_output(['/sbin/mount','-p'],text=True).splitlines()
    if not any(len(f:=line.split())==6 and f[1]==str(ram) and f[2]=='tmpfs' and 'rw' in f[3].split(',') for line in rows):raise ValueError('Dedicated recovery RAM is unavailable')
    backup=ram/'usb-rescue-request';backup.mkdir(mode=0o700,parents=True,exist_ok=True)
    if any(v.is_symlink() for v in (backup,*backup.parents)):raise ValueError('Redirected backup')
    old=NEXT.read_bytes();(backup/'nextboot.before').write_bytes(old)
    data=selector();tmp=NEXT.with_name('nextboot.conf.rescue.tmp')
    subprocess.run(['/sbin/mount','-u','-w','/'],check=True)
    try:
        with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644);os.replace(tmp,NEXT)
        if NEXT.read_bytes()!=data:raise ValueError('Selector readback failed')
    finally:subprocess.run(['/sbin/mount','-u','-r','/'],check=True)
    print(json.dumps({'status':'RAM-rescue-nextboot-armed','reboot_requested':a.action=='reboot'}),flush=True)
    if a.action=='reboot':subprocess.run(['/sbin/shutdown','-r','now'],check=True)


if __name__=='__main__':main()

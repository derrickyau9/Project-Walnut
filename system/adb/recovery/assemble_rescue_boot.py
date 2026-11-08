"""Native FAT/GPT rescue-media assembly; writes only new ordinary output files."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def run(args):
    r=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=120)
    if r.returncode:raise RuntimeError(str(args)+'\n'+r.stdout+r.stderr)
    return r.stdout


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main(a):
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('FreeBSD root build environment required')
    out=a.output.absolute()
    if out.exists() or out.is_symlink():raise ValueError('New output required')
    out.mkdir(mode=0o700);old=out/'old';old.mkdir();fat=out/'fat';fat.mkdir()
    md=run(['/sbin/mdconfig','-a','-t','vnode','-f',a.efi_source,'-o','readonly']).strip();mounted=False
    if not re.fullmatch('md[0-9]+',md):raise ValueError('Unexpected owned md')
    try:
        run(['/sbin/mount','-t','msdosfs','-o','ro','/dev/'+md,old]);mounted=True
        shutil.copytree(old,fat,dirs_exist_ok=True)
    finally:
        if mounted:run(['/sbin/umount',old])
        run(['/sbin/mdconfig','-d','-u',md[2:]])
    recovery=fat/'DaOS-recovery';recovery.mkdir()
    kernel=recovery/'kernel';kernel.mkdir()
    root=a.ram_build/'root'
    for name,src in [('kernel',root/'boot/kernel/kernel'),('usb_template.ko',root/'boot/kernel/usb_template.ko'),('daos_adb_template.ko',root/'boot/modules/daos_adb_template.ko')]:shutil.copyfile(src,kernel/name)
    shutil.copyfile(a.loader,recovery/'loader.efi')
    shutil.copyfile(a.ram_build/'DaOS-rescue.ufs.gz',recovery/'root.ufs.gz')
    for name in ('loader.lua','boot.scr','boot.cmd'):shutil.copyfile(a.boot_files/'DaOS-recovery'/name,recovery/name)
    # This dedicated rescue-only medium has no primary UFS to depend on.
    # Never copy this global LoaderEnv to the normal card's ESP.
    boot=fat/'EFI/BOOT';boot.mkdir(parents=True,exist_ok=True);shutil.copyfile(a.loader,boot/'BOOTAA64.EFI')
    env=fat/'EFI/freebsd/loader.env';env.parent.mkdir(parents=True,exist_ok=True)
    env.write_text('daos_recovery=YES\nloader_lua=/DaOS-recovery/loader.lua\nconsole=efi\n')
    for n in ('boot.scr','boot.cmd'):
        (fat/n).unlink(missing_ok=True)
    fs=out/'rescue-fat.img'
    run(['/usr/sbin/makefs','-t','msdos','-s','128m','-o','fat_type=32,sectors_per_cluster=1,volume_label=DAOSRESCUE',fs,fat])
    check=run(['/sbin/fsck_msdosfs','-n',fs]);(out/'fsck-fat.txt').write_text(check)
    image=out/'DaOS-Rescue-USB-arm64.img'
    run(['/usr/bin/mkimg','-s','gpt','-f','raw','-a','1','-p','efi/DaOS-rescue-efi:='+str(fs),'-o',image])
    report={'format':'DaOS-independent-USB-rescue-media-v1','image':str(image),'bytes':image.stat().st_size,'sha256':sha(image),'ram_image_sha256':sha(a.ram_build/'DaOS-rescue.ufs'),'efi_loader_sha256':sha(a.loader),'physical_pi_boot_verified':False,'qemu_boot_verified':False,'source_primary_efi_untouched':True}
    (out/'build.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--efi-source',type=Path,required=True);p.add_argument('--ram-build',type=Path,required=True);p.add_argument('--loader',type=Path,required=True);p.add_argument('--boot-files',type=Path,required=True);main(p.parse_args())

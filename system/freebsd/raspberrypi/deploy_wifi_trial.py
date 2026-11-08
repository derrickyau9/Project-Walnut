#!/usr/local/bin/python3.12
"""Stage the repaired SDIO kernel with an observed working one-shot fallback."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

KERNEL='kernel.daos-wifi-002'
KERNEL_SHA='380b819ff2a2b0a8eb8b54f750ce78b8c22a54a97fa112a68322ceab38209a43'
PAYLOAD_SHA='302b4ccc968b33f5f03fb7c3e6cc47981b6d5e8bfc18530528adbe998816794f'
WORK=Path('/var/DaOS/updates/wifi-002')
BOOT={'kernel':KERNEL,'module_path':'/boot/'+KERNEL+';/boot/modules;/boot/dtb;/boot/dtb/overlays',
      'fdt_overlays':'/boot/dtb/overlays/daos-wifi-layout-002.dtbo','sdio_load':'YES','daos_wifi_trial':'002'}

def run(args,**kw):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=45,**kw)
def atomic(path,data,mode=0o644):
    tmp=path.with_name(path.name+'.wifi-002.tmp')
    with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.chmod(tmp,mode);os.replace(tmp,path)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library');parser.add_argument('archive',nargs='?')
    parser.add_argument('--commit',action='store_true');parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or os.uname().machine!='arm64':raise ValueError('Pi4 ARM64 root required')
    library=Path(args.library)
    if library.is_symlink() or library.stat().st_uid!=0:raise ValueError('Untrusted installer library')
    spec=importlib.util.spec_from_file_location('wifi_library',library);u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
    u.KERNEL=KERNEL
    u.PREFIXES=('root/boot/'+KERNEL+'/','root/boot/firmware/brcmfmac43455-sdio.','root/usr/share/DaOS/wifi/')
    u.EXACT=u.EXACT|{'root/boot/dtb/overlays/daos-wifi-layout-002.dtbo'}
    boot=run(['/sbin/sysctl','-n','kern.bootfile']).stdout.strip()
    receipt_path=WORK/'deployment.json'
    if args.commit:
        receipt=json.loads(receipt_path.read_text())
        if boot!='/boot/'+KERNEL+'/kernel' or u.sha(Path(boot))!=KERNEL_SHA:raise ValueError('Repaired kernel is not running')
        if not run(['/sbin/sysctl','-n','net.wlan.devices']).stdout.strip():raise ValueError('Wireless driver has not exposed an interface')
        if not Path('/boot/nextboot.conf').read_text().startswith('nextboot_enable="NO"'):raise ValueError('One-shot flag did not clear')
        run(['/sbin/mount','-u','-w','/'])
        try:
            path=Path('/boot/loader.conf.local');atomic(path,u.conf(path.read_text(),BOOT))
        finally:run(['/sbin/mount','-u','-r','/'])
        receipt['status']='repaired-kernel-committed';receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({'status':receipt['status'],'kernel':boot}));return
    if args.rollback:
        receipt=json.loads(receipt_path.read_text())
        run(['/usr/sbin/service','DaOS-camera','stop']);run(['/usr/sbin/service','DaOS-system','stop'])
        run(['/sbin/mount','-u','-w','/'])
        try:
            for rel,entry in receipt['backups'].items():
                dst=Path('/')/rel
                if dst.is_symlink():raise ValueError('Rollback target was redirected')
                if entry is None:dst.unlink(missing_ok=True)
                else:shutil.copy2(WORK/'backup'/rel,dst)
            resolver=Path('/etc/resolv.conf')
            if resolver.is_symlink():
                if os.readlink(resolver)!='/var/run/resolv.conf':raise ValueError('Resolver changed')
                resolver.unlink()
            saved=receipt['resolver_backup']
            if saved and saved['kind']=='file':shutil.copy2(WORK/'backup/etc/resolv.conf',resolver)
            elif saved:os.symlink(saved['target'],resolver)
        finally:
            run(['/sbin/mount','-u','-r','/']);run(['/usr/sbin/service','DaOS-system','start']);run(['/usr/sbin/service','DaOS-camera','start'])
        receipt['status']='previous-configuration-restored';receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({'status':receipt['status'],'requires_reboot':True}));return
    if boot!='/boot/kernel/kernel':raise ValueError('Stage from the restored original kernel')
    nextboot=Path('/boot/nextboot.conf')
    if not nextboot.read_text().startswith('nextboot_enable="NO"'):raise ValueError('One-shot fallback must first be verified on this board')
    if shutil.disk_usage('/').free<160*1024**2 or shutil.disk_usage('/tmp').free<170*1024**2:raise ValueError('Insufficient staging space')
    WORK.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix='DaOS-wifi-002-',dir='/tmp') as scratch:
        incoming,manifest=u.unpack(Path(args.archive),PAYLOAD_SHA,Path(scratch))
        if any(not e['path'].startswith('root/') for e in manifest['files']):raise ValueError('One-shot update must not change firmware partition')
        paths=[e['path'][5:] for e in manifest['files'] if not e['path'].startswith(('root/boot/'+KERNEL+'/','root/usr/share/DaOS/wifi/','root/boot/firmware/'))]
        paths+=['boot/loader.conf.local','boot/nextboot.conf','etc/rc.conf','etc/resolvconf.conf']
        backups={}
        for rel in paths:
            src=Path('/')/rel
            if src.is_symlink():raise ValueError('Redirected update target')
            backups[rel]=u.sha(src) if src.is_file() else None
            if src.is_file():
                dst=WORK/'backup'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        resolver=Path('/etc/resolv.conf');saved=None
        if resolver.is_symlink():saved={'kind':'link','target':os.readlink(resolver)}
        elif resolver.is_file():
            dst=WORK/'backup/etc/resolv.conf';dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(resolver,dst);saved={'kind':'file','sha256':u.sha(resolver)}
        receipt={'format':'DaOS-pi4-wifi-trial-v2','status':'backed-up','payload_sha256':PAYLOAD_SHA,'backups':backups,
                 'resolver_backup':saved,'original_kernel_retained':True,'firmware_partition_changed':False,'one_shot_flag_previously_observed_clearing':True}
        receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
        run(['/usr/sbin/service','DaOS-camera','stop']);run(['/usr/sbin/service','DaOS-system','stop'])
        run(['/sbin/mount','-u','-w','/'])
        try:
            receipt['changes']=u.apply(Path('/'),Path('/boot/efi'),incoming,manifest,set_boot=False)
            if u.sha(Path('/boot')/KERNEL/'kernel')!=KERNEL_SHA:raise ValueError('Repaired kernel identity mismatch')
            for e in manifest['files']:
                if u.sha(Path('/')/e['path'][5:])!=e['sha256']:raise ValueError('Installed payload mismatch')
            atomic(nextboot,u.conf('nextboot_enable="YES"\n',dict(BOOT,boot_mute='NO')))
            # The Lua loader only recognizes NO if it is the very first line.
            if not nextboot.read_text().startswith('nextboot_enable="YES"'):raise ValueError('Bad one-shot header')
            receipt['status']='staged-for-one-shot-boot'
        except BaseException:
            receipt['status']='failed-backups-retained';raise
        finally:
            receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
            run(['/sbin/mount','-u','-r','/']);run(['/usr/sbin/service','DaOS-system','start']);run(['/usr/sbin/service','DaOS-camera','start'])
    print(json.dumps({'status':receipt['status'],'requires_reboot':True,'default_kernel':'/boot/kernel/kernel'}))

if __name__=='__main__':main()

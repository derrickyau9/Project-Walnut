#!/usr/local/bin/python3.12
"""Install the hash-bound Pi4 Wi-Fi payload; retain the previous boot selection.

An offline image assembler may call unpack/apply. Live installation stages the
kernel for the next normal reboot; it never tries to kldload MMCCAM on the old
kernel and never rewrites a physical disk.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

KERNEL='kernel.daos-wifi-001'
PREFIXES=('root/boot/'+KERNEL+'/', 'root/boot/firmware/brcmfmac43455-sdio.',
          'root/usr/share/DaOS/wifi/')
EXACT={'root/opt/hasslefree/ui/on-device/WifiPage.qml','root/usr/local/libexec/DaOS-system-service.py',
       'root/opt/hasslefree/ui/on-device/system_settings.py','root/usr/local/libexec/DaOS-wifi-load.py',
       'root/usr/local/libexec/DaOS-usb-adb-session.py',
       'root/usr/local/etc/rc.d/DaOS-wifi-driver','efi/overlays/daos-wlan-pwrseq.dtbo'}
EXACT.add('root/usr/local/etc/rc.d/DaOS-system')
CONFIGS=('root/boot/loader.conf.local','root/etc/rc.conf','root/etc/resolvconf.conf','efi/config.txt')

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def unpack(archive,expected,work):
    if expected in {'ae52f68a0367c5addcc97fafe9e08a01d66a7a31a1ca594220ec9405d733f18b',
                    '302b4ccc968b33f5f03fb7c3e6cc47981b6d5e8bfc18530528adbe998816794f'}:
        raise ValueError('Withdrawn Pi4 Wi-Fi candidate: physical boot/storage failure. Use a corrected release.')
    if sha(archive)!=expected:raise ValueError('Wi-Fi archive hash mismatch')
    incoming=work/'incoming';incoming.mkdir()
    with tarfile.open(archive) as t:
        members=t.getmembers()
        if len(members)>1024 or sum(m.size for m in members)>160*1024**2:raise ValueError('Payload exceeds bounds')
        names=set()
        for m in members:
            p=PurePosixPath(m.name)
            if not m.isfile() or p.is_absolute() or '..' in p.parts or str(p)!=m.name or m.name in names:raise ValueError('Unsafe or duplicate archive path')
            names.add(m.name)
        t.extractall(incoming,filter='data')
    manifest=json.loads((incoming/'manifest.json').read_text())
    if manifest.get('format')!='DaOS-pi4-wifi-update-v1' or manifest.get('kernel_directory')!=KERNEL:raise ValueError('Unknown payload format')
    expected_names={'manifest.json'}
    for entry in manifest['files']:
        rel=entry['path'];p=incoming/rel
        if rel not in EXACT and not rel.startswith(PREFIXES):raise ValueError('Unexpected install path')
        if rel in expected_names or entry['mode'] not in (0o644,0o755) or p.is_symlink() or sha(p)!=entry['sha256'] or p.stat().st_size!=entry['bytes']:raise ValueError('Payload file mismatch')
        expected_names.add(rel)
    if names!=expected_names:raise ValueError('Unmanifested payload file')
    return incoming,manifest

def conf(data,values):
    lines=[line for line in data.splitlines() if not any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in values)]
    return ('\n'.join(lines)+'\n# DaOS Pi4 SDIO Wi-Fi\n'+''.join(k+'="'+v+'"\n' for k,v in values.items())).encode()

def destination(root,efi,rel):
    tree,name=rel.split('/',1)
    target=(root if tree=='root' else efi)/name
    if any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('Redirected system path')
    return target

def apply(root,efi,incoming,manifest,*,allow_existing_kernel=False,set_boot=True):
    # Stage a separate boot directory, leaving the working kernel and its
    # modules available for immediate rollback from the loader.
    kerneldir=root/'boot'/KERNEL
    if kerneldir.exists():
        if not allow_existing_kernel:raise ValueError('Wi-Fi kernel directory already exists')
        expected={e['path'].split('/',3)[3]:e['sha256'] for e in manifest['files'] if e['path'].startswith('root/boot/'+KERNEL+'/')}
        actual={p.name:sha(p) for p in kerneldir.iterdir() if p.is_file() and not p.is_symlink()}
        if actual!=expected:raise ValueError('Existing image kernel differs from compiled payload')
    changes={}
    def put(rel,data,mode):
        dst=destination(root,efi,rel)
        old=sha(dst) if dst.is_file() else None
        if old==hashlib.sha256(data).hexdigest():
            changes[rel]={'before_sha256':old,'sha256':old}
            return
        dst.parent.mkdir(parents=True,exist_ok=True)
        # The installer inherits a private umask; installed public directories
        # must remain traversable by the camera UI user.
        current=dst.parent
        anchor=root if rel.startswith('root/') else efi
        while current!=anchor:
            if current.stat().st_uid!=0:raise ValueError('Unowned payload parent')
            os.chmod(current,0o755);current=current.parent
        temp=dst.with_name(dst.name+'.wifi-001.tmp')
        with temp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(temp,mode);os.chown(temp,0,0);os.replace(temp,dst)
        changes[rel]={'before_sha256':old,'sha256':sha(dst)}
    for entry in manifest['files']:put(entry['path'],(incoming/entry['path']).read_bytes(),entry['mode'])
    local=root/'boot/loader.conf.local'
    if set_boot:
        put('root/boot/loader.conf.local',conf(local.read_text() if local.exists() else '',
            {'kernel':KERNEL,'module_path':'/boot/'+KERNEL+';/boot/modules','sdio_load':'YES'}),0o644)
    rc=root/'etc/rc.conf'
    put('root/etc/rc.conf',conf(rc.read_text(),{'DaOS_wifi_driver_enable':'YES','DaOS_system_enable':'YES'}),0o644)
    resolver_conf=root/'etc/resolvconf.conf'
    put('root/etc/resolvconf.conf',conf(resolver_conf.read_text() if resolver_conf.exists() else '',
        {'resolv_conf':'/var/run/resolv.conf'}),0o644)
    resolver=root/'etc/resolv.conf'
    if resolver.parent.is_symlink():raise ValueError('Redirected resolver directory')
    if resolver.is_symlink() and os.readlink(resolver)!='/var/run/resolv.conf':raise ValueError('Unrecognized resolver link')
    if not resolver.is_symlink():
        before=sha(resolver) if resolver.is_file() else None
        temp=resolver.with_name('resolv.conf.wifi-001.tmp')
        os.symlink('/var/run/resolv.conf',temp);os.replace(temp,resolver)
        changes['root/etc/resolv.conf']={'before_sha256':before,'symlink':'/var/run/resolv.conf'}
    if not set_boot:return changes
    configuration=(efi/'config.txt').read_text()
    # The old mmc overlay routes the Wi-Fi SDHCI controller to SD-card pins.
    # MMCCAM supports the dedicated Pi4 eMMC2 SD slot and the Wi-Fi controller.
    lines=[line for line in configuration.splitlines() if not re.match(r'\s*dtoverlay\s*=\s*mmc\s*(?:,|$)',line)]
    if not any(line.strip()=='dtoverlay=daos-wlan-pwrseq' for line in lines):lines+=['','[pi4]','dtoverlay=daos-wlan-pwrseq','[all]']
    put('efi/config.txt',('\n'.join(lines)+'\n').encode(),0o644)
    return changes

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive');parser.add_argument('sha256');parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args()
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0 or os.uname().machine!='arm64':raise ValueError('Native Pi4 ARM64 root required')
    if not Path('/dev/gpt/DaOS-efi').exists():raise ValueError('DaOS EFI partition not found')
    work=Path('/var/DaOS/updates/wifi-001')
    if args.rollback:
        receipt=json.loads((work/'deployment.json').read_text())
        subprocess.run(['/sbin/mount','-u','-w','/'],check=True)
        mountpoint=work/'efi'
        subprocess.run(['/sbin/mount','-t','msdosfs','/dev/gpt/DaOS-efi',str(mountpoint)],check=True)
        try:
            for rel,entry in receipt['backups'].items():
                dst=destination(Path('/'),mountpoint,rel)
                if entry is None:dst.unlink(missing_ok=True)
                else:shutil.copy2(work/'backup'/rel,dst)
            resolver=Path('/etc/resolv.conf')
            saved=receipt.get('resolver_backup')
            if resolver.is_symlink() and os.readlink(resolver)!='/var/run/resolv.conf':raise ValueError('Resolver changed since update')
            if resolver.is_symlink():resolver.unlink()
            if saved and saved['kind']=='file':shutil.copy2(work/'backup/root/etc/resolv.conf',resolver)
            elif saved and saved['kind']=='link':os.symlink(saved['target'],resolver)
            subprocess.run(['/bin/sync'],check=True)
        finally:
            subprocess.run(['/sbin/umount',str(mountpoint)],check=True)
            subprocess.run(['/sbin/mount','-u','-r','/'],check=True)
        print('Previous boot selection and configuration restored; reboot normally to use it.')
        return
    if shutil.disk_usage('/').free<180*1024**2:raise ValueError('180 MiB free root space required for separate kernel staging')
    work.mkdir(mode=0o700)
    # /var is a small persistent appliance partition. Extract the 90 MiB
    # kernel payload in private tmpfs scratch, rather than filling /var.
    if shutil.disk_usage('/tmp').free<170*1024**2:raise ValueError('170 MiB temporary space required')
    with tempfile.TemporaryDirectory(prefix='DaOS-wifi-',dir='/tmp') as scratch:
        incoming,manifest=unpack(Path(args.archive),args.sha256,Path(scratch))
        install_live(args,work,incoming,manifest)

def install_live(args,work,incoming,manifest):
    mountpoint=work/'efi';mountpoint.mkdir()
    subprocess.run(['/sbin/mount','-t','msdosfs','/dev/gpt/DaOS-efi',str(mountpoint)],check=True)
    receipt={'format':'DaOS-pi4-wifi-deployment-v1','status':'backed-up','payload_sha256':args.sha256,'backups':{},'requires_reboot':True}
    record=work/'deployment.json'
    try:
        resolver=Path('/etc/resolv.conf')
        receipt['resolver_backup']=None
        if resolver.is_symlink():
            if os.readlink(resolver)!='/var/run/resolv.conf':raise ValueError('Unrecognized resolver link')
            receipt['resolver_backup']={'kind':'link','target':os.readlink(resolver)}
        elif resolver.is_file():
            saved=work/'backup/root/etc/resolv.conf';saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(resolver,saved)
            receipt['resolver_backup']={'kind':'file','sha256':sha(resolver)}
        for rel in sorted(EXACT|set(CONFIGS)):
            src=destination(Path('/'),mountpoint,rel)
            receipt['backups'][rel]=sha(src) if src.is_file() else None
            if src.is_file():
                dest=work/'backup'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
        record.write_text(json.dumps(receipt,indent=2)+'\n')
        subprocess.run(['/usr/sbin/service','DaOS-camera','stop'],check=True,capture_output=True,timeout=45)
        subprocess.run(['/usr/sbin/service','DaOS-system','stop'],check=True,capture_output=True,timeout=20)
        subprocess.run(['/sbin/mount','-u','-w','/'],check=True)
        receipt['changes']=apply(Path('/'),mountpoint,incoming,manifest)
        subprocess.run(['/bin/sync'],check=True)
        receipt['status']='installed-reboot-required'
    except BaseException:
        receipt['status']='failed-backups-retained';raise
    finally:
        record.write_text(json.dumps(receipt,indent=2)+'\n')
        subprocess.run(['/sbin/umount',str(mountpoint)],check=True)
        subprocess.run(['/sbin/mount','-u','-r','/'],check=True)
        subprocess.run(['/usr/sbin/service','DaOS-system','start'],capture_output=True,timeout=20)
        subprocess.run(['/usr/sbin/service','DaOS-camera','start'],capture_output=True,timeout=20)
    print(json.dumps({'status':receipt['status'],'receipt':str(record),'physical_wifi_validated':False}))

if __name__=='__main__':main()

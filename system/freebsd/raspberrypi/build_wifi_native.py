#!/usr/bin/env python3
"""Build the Pi4 SDIO kernel and native brcmfmac against the pinned DaOS source.

Run only in the private native FreeBSD builder. The original source and existing
factory image stay unchanged. Scratch is an explicitly owned tmpfs on F-backed WSL.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import re

BASE = Path('/var/daos-builds/daos-base-arm64-p6-001/source/usr/src')
SDK = Path('/var/daos-builds/daos-base-arm64-pi-splash-001/stage')
REVISION=os.environ.get('DAOS_WIFI_REVISION','001')
if not re.fullmatch('[0-9]{3}',REVISION):raise ValueError('Invalid native build revision')
WORK_ROOT=Path(os.environ.get('DAOS_WIFI_WORK_ROOT','/var/DaOS-wifi-build-memory'))
WORK = WORK_ROOT/('native-'+REVISION)
ARCHIVE = Path('/var/tmp/DaOS-wifi-source-001.tar.gz')
ARCHIVE_SHA = '55079d558059954875895ae069a60987d7c11ad701c696f180a91f436ba6dafb'
SOURCE = WORK/'source'
DRIVER = WORK/'driver'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024**2),b''):h.update(data)
    return h.hexdigest()

def run(args, name, **kw):
    start=time.monotonic()
    logfile=WORK/(name+'.log')
    for n in range(1,100):
        if not logfile.exists():break
        logfile=WORK/(name+'-'+str(n)+'.log')
    with logfile.open('x') as log:
        p=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=1800,**kw)
    print(json.dumps({'step':name,'returncode':p.returncode,'seconds':round(time.monotonic()-start,2)}),flush=True)
    if p.returncode:
        print(logfile.read_text(errors='replace')[-10000:],flush=True)
        raise SystemExit(p.returncode)

def main():
    if os.uname().sysname!='FreeBSD' or os.geteuid()!=0:raise ValueError('Private native builder required')
    if sha(ARCHIVE)!=ARCHIVE_SHA:raise ValueError('Driver input changed')
    parent=WORK.parent
    if not parent.exists():parent.mkdir(mode=0o700)
    mounts=subprocess.check_output(['/sbin/mount','-p'],text=True)
    if parent==Path('/var/DaOS004-work/wifi'):
        owner=json.loads(Path('/var/DaOS004-work/ownership.json').read_text())
        if owner.get('serial')!='DAOS004-WORK' or not any(x.split()[0:2]==['/dev/gpt/DaOS004Work','/var/DaOS004-work'] for x in mounts.splitlines()):
            raise ValueError('Explicitly owned work disk required')
    else:
        if not any(x.split()[1:3]==[str(parent),'tmpfs'] for x in mounts.splitlines()):
            if parent.is_symlink() or parent.stat().st_uid!=0 or any(parent.iterdir()):raise ValueError('Nonempty or unowned scratch mountpoint')
            subprocess.run(['/sbin/mount','-t','tmpfs','-o','size=12g','tmpfs',str(parent)],check=True,timeout=30)
        mounts=subprocess.check_output(['/sbin/mount','-p'],text=True)
        if not any(x.split()[1:3]==[str(parent),'tmpfs'] for x in mounts.splitlines()):raise ValueError('Owned tmpfs required')
    resume='--resume' in sys.argv
    WORK.mkdir(mode=0o700,exist_ok=resume)
    if not resume:shutil.copytree(BASE,SOURCE,symlinks=True)
    with tarfile.open(ARCHIVE) as archive:
        members=archive.getmembers()
        prefix=members[0].name.split('/')[0]+'/'
        DRIVER.mkdir(exist_ok=resume)
        for entry in members:
            rel=entry.name.removeprefix(prefix)
            if not rel or entry.isdir():continue
            if not entry.isfile() or rel.startswith('/') or '..' in Path(rel).parts:raise ValueError('Unexpected source archive entry')
            dest=DRIVER/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            if not resume:
                with archive.extractfile(entry) as a,dest.open('xb') as b:shutil.copyfileobj(a,b)
    patches=sorted((DRIVER/'patches').glob('*.patch'))
    if REVISION!='001':patches.append(Path('/var/tmp/DaOS-mmccam-async-ios-002.patch'))
    if int(REVISION)>=4:patches.append(Path('/var/tmp/DaOS-sdio-f0-timeout.patch'))
    changed={}
    for patch in patches:
        for line in patch.read_text().splitlines():
            if line.startswith('+++ b/'):
                rel=line[6:].split()[0]
                changed.setdefault(rel,sha(BASE/rel))
        if not resume:run(['/usr/bin/patch','--batch','--forward','-p1','-i',str(patch)],'patch-'+patch.name,cwd=SOURCE)
        else:
            subprocess.run(['/usr/bin/patch','--batch','-R','--dry-run','-p1','-i',str(patch)],cwd=SOURCE,check=True,stdout=subprocess.DEVNULL,timeout=30)
    config=WORK/'config';config.mkdir(exist_ok=resume)
    (config/'DaOS').write_text('include GENERIC\nident DaOS\nmakeoptions DEBUG=""\noptions MMCCAM\ndevice splash\ndevice wlan\ndevice wlan_ccmp\n')
    (config/'src.conf').write_text('WITHOUT_DEBUG_FILES=yes\nWITHOUT_KERNEL_SYMBOLS=yes\n')
    env=dict(os.environ,MAKEOBJDIRPREFIX=str(WORK/'obj'))
    common=['make','-C',str(SOURCE),'TARGET=arm64','TARGET_ARCH=aarch64',
            'SRCCONF='+str(config/'src.conf'),'SRC_ENV_CONF=/dev/null','__MAKE_CONF=/dev/null',
            'KERNCONF=DaOS','KERNCONFDIR='+str(config)]
    run(common+['-j12','kernel-toolchain'],'kernel-toolchain',env=env)
    run(common+['-j12','buildkernel'],'buildkernel',env=env)
    kobj=WORK/'obj'/str(SOURCE).lstrip('/')/'arm64.aarch64/sys/DaOS'
    # Build every external module used by the appliance against this MMCCAM
    # kernel, including USB ADB and the DSI touch driver; avoid ABI mixtures.
    script=WORK/'modules.sh'
    script.write_text('set -eu\nmake -C '+str(DRIVER)+' SYSDIR='+str(SOURCE/'sys')+' KERNBUILDDIR='+str(kobj)+' all\n')
    run(common+['WORLDTMP='+str(SDK),'buildenv','BUILDENV_SHELL=/usr/bin/env PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh '+str(script)],'brcmfmac',env=env)
    overlay=WORK/'wlan-pwrseq.dtbo'
    run(['/usr/bin/dtc','-@','-I','dts','-O','dtb','-o',str(overlay),str(DRIVER/'patches/wlan-pwrseq.dts')],'overlay')
    result={'format':'DaOS-pi4-wifi-native-v1','status':'compiled','upstream_commit':'9adbe774d3be91827d628fbd811daf63512acb6d',
            'upstream_archive_sha256':ARCHIVE_SHA,'freebsd_commit':'95719d8a895bf8170851e9844d9b4092b1d2b6fe',
            'kernel_object':str(kobj),'kernel_config':(config/'DaOS').read_text(),
            'original_source_unchanged':all(sha(BASE/p)==h for p,h in changed.items()),
            'patches':{p.name:sha(p) for p in patches},'artifacts':{},'physical_wifi_validated':False}
    for name,p in [('kernel',kobj/'kernel'),('if_brcmfmac.ko',DRIVER/'if_brcmfmac.ko'),('wlan-pwrseq.dtbo',overlay)]:
        data=p.read_bytes()
        if name!='wlan-pwrseq.dtbo' and (data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183):raise ValueError('Not AArch64: '+name)
        result['artifacts'][name]={'path':str(p),'bytes':len(data),'sha256':sha(p)}
    (WORK/'native-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'compiled','kernel':result['artifacts']['kernel']}),flush=True)

if __name__=='__main__':main()

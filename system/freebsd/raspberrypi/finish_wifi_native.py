#!/usr/bin/env python3
"""Finish the owned SDIO build: matching touch/ADB modules and install tree."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

WORK=Path(os.environ.get('DAOS_WIFI_WORK_ROOT','/var/DaOS-wifi-build-memory'))/('native-'+os.environ.get('DAOS_WIFI_REVISION','001'))
SRC=WORK/'source'
SDK=Path('/var/daos-builds/daos-base-arm64-pi-splash-001/stage')

def sha(p):return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()

def main():
    record=json.loads((WORK/'native-evidence.json').read_text())
    kobj=Path(record['kernel_object'])
    if sha(kobj/'kernel')!=record['artifacts']['kernel']['sha256']:raise ValueError('Kernel changed')
    usb=WORK/'usb-input';usb.mkdir()
    with tarfile.open('/var/tmp/DaOS-wifi-usb-module-001.tar.gz') as t:t.extractall(usb,filter='data')
    touch=WORK/'touch';touch.mkdir()
    shutil.copyfile(SRC/'sys/modules/rpi_ft5406/Makefile',touch/'Makefile')
    script=WORK/'external.sh'
    usbmod=usb/'system/adb/freebsd_usb/kmod'
    script.write_text('set -eu\n'+''.join('make -C '+str(p)+' SYSDIR='+str(SRC/'sys')+' KERNBUILDDIR='+str(kobj)+' all\n' for p in (touch,usbmod)))
    common=['make','-C',str(SRC),'TARGET=arm64','TARGET_ARCH=aarch64','SRCCONF='+str(WORK/'config/src.conf'),
            'SRC_ENV_CONF=/dev/null','__MAKE_CONF=/dev/null','KERNCONF=DaOS','KERNCONFDIR='+str(WORK/'config')]
    env=dict(os.environ,MAKEOBJDIRPREFIX=str(WORK/'obj'))
    with (WORK/'external-build.log').open('x') as log:
        subprocess.run(common+['WORLDTMP='+str(SDK),'buildenv','BUILDENV_SHELL=/usr/bin/env PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh '+str(script)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    stage=WORK/'stage';stage.mkdir()
    with (WORK/'installkernel.log').open('x') as log:
        subprocess.run(common+['DESTDIR='+str(stage),'NO_ROOT=yes','installkernel'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    for name,p in [('rpi_ft5406.ko',touch/'rpi_ft5406.ko'),('daos_adb_template.ko',usbmod/'daos_adb_template.ko')]:
        data=p.read_bytes()
        if data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183:raise ValueError('Wrong external module architecture')
        record['artifacts'][name]={'path':str(p),'bytes':len(data),'sha256':sha(p)}
    files=list((stage/'boot/kernel').glob('*'))
    record['installed_kernel_directory']=str(stage/'boot/kernel')
    record['installed_kernel_files']={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in files if p.is_file() and not p.is_symlink()}
    (WORK/'native-complete.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'compiled','kernel_files':len(record['installed_kernel_files']),
                      'kernel_bytes':sum(p.stat().st_size for p in files if p.is_file()),'artifacts':list(record['artifacts'])}))

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Package the compiled MMCCAM kernel, appliance modules and host inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import re
import os
import tarfile

NATIVE_REVISION=os.environ.get('DAOS_WIFI_REVISION','001')
WORK=Path(os.environ.get('DAOS_WIFI_WORK_ROOT','/var/DaOS-wifi-build-memory'))/('native-'+NATIVE_REVISION)

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    record=json.loads((WORK/'native-final.json').read_text())
    revision=sys.argv[2] if len(sys.argv)>2 else '001'
    if not re.fullmatch('[0-9]{3}',revision):raise ValueError('Invalid package revision')
    folder=WORK/('bundle-'+revision);folder.mkdir()
    with tarfile.open(sys.argv[1] if len(sys.argv)>1 else '/var/tmp/DaOS-wifi-host-inputs-001.tar.gz') as t:t.extractall(folder,filter='data')
    kernel_name='kernel.daos-wifi-'+NATIVE_REVISION
    kernel=folder/'root/boot'/kernel_name
    kernel.parent.mkdir(parents=True,exist_ok=True)
    if not kernel.exists():shutil.copytree(record['installed_kernel_directory'],kernel,symlinks=True)
    for p in kernel.iterdir():
        if p.is_symlink():
            resolved=p.resolve(strict=True)
            if resolved.parent!=kernel:raise ValueError('Kernel alias escaped build tree')
            data=resolved.read_bytes();p.unlink();p.write_bytes(data)
    for name in ('if_brcmfmac.ko','rpi_ft5406.ko','daos_adb_template.ko'):
        entry=record['artifacts'][name];source=Path(entry['path'])
        if sha(source)!=entry['sha256']:raise ValueError('Native artifact changed')
        shutil.copyfile(source,kernel/name)
    subprocess.run(['/usr/sbin/kldxref',str(kernel)],check=True,timeout=30)
    if NATIVE_REVISION=='001':
        firmware=folder/'efi/overlays';firmware.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(WORK/'wlan-pwrseq.dtbo',firmware/'daos-wlan-pwrseq.dtbo')
    else:
        overlay=folder/'root/boot/dtb/overlays/daos-wifi-layout-002.dtbo';overlay.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['/usr/bin/dtc','-@','-I','dts','-O','dtb','-o',str(overlay),'/var/tmp/DaOS-wifi-layout-002.dts'],check=True,timeout=30)
    meta=folder/'root/usr/share/DaOS/wifi'
    shutil.copyfile(WORK/'native-final.json',meta/'native-evidence.json')
    shutil.copyfile(WORK/'DaOS-pi4-board-mac.patch',meta/'source/DaOS-pi4-board-mac.patch')
    for patch in (WORK/'driver/patches').glob('*.patch'):shutil.copyfile(patch,meta/'source'/patch.name)
    if NATIVE_REVISION!='001':shutil.copyfile('/var/tmp/DaOS-mmccam-async-ios-002.patch',meta/'source/mmccam-async-ios.patch')
    if int(NATIVE_REVISION)>=4:shutil.copyfile('/var/tmp/DaOS-sdio-f0-timeout.patch',meta/'source/sdio-f0-timeout.patch')
    shutil.copyfile(WORK/'driver/LICENSE',meta/'licenses/brcmfmac-ISC.txt')
    files=[]
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():raise ValueError('Unexpected payload link: '+str(p))
        if not p.is_file():continue
        rel=p.relative_to(folder).as_posix()
        mode=0o755 if rel.startswith(('root/usr/local/libexec/','root/usr/local/etc/rc.d/')) else 0o644
        files.append({'path':rel,'mode':mode,'bytes':p.stat().st_size,'sha256':sha(p)})
    manifest={'format':'DaOS-pi4-wifi-update-v1','update_id':'wifi-'+NATIVE_REVISION,'kernel_directory':kernel_name,
              'files':files,'requires_reboot':True,'physical_wifi_validated':False,
              'limits':'Experimental brcmfmac; 5GHz WPA2 upstream working; 2.4GHz upstream unresolved'}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    output=WORK/('wifi-update-'+revision+'.tar.gz')
    with tarfile.open(output,'w:gz',compresslevel=6) as t:
        for p in sorted(folder.rglob('*')):
            if p.is_file():t.add(p,arcname=p.relative_to(folder).as_posix(),recursive=False)
    print(json.dumps({'status':'packaged','path':str(output),'sha256':sha(output),'bytes':output.stat().st_size,'files':len(files)}))

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Derive the Wi-Fi factory image from the released DSI43 system image."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import re

BASE=Path('/var/DaOS-system-image-003/DaOS-BSD-RPi4-DSI43-System-ARM64.img')
BASE_SHA='1788ed68190e78bfe76b1f2ca56d6904883dfada800064312101dae386e160db'
WORK=Path('/var/DaOS-wifi-build-memory/image-001')

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def run(args):return subprocess.run(args,check=True,timeout=1800)

def main():
    global WORK
    if len(sys.argv)>4:
        if not re.fullmatch('[0-9]{3}',sys.argv[4]):raise ValueError('Invalid candidate revision')
        WORK=WORK.parent/('image-'+sys.argv[4])
    if os.uname().sysname!='FreeBSD' or os.geteuid()!=0:raise ValueError('Native root builder required')
    if BASE.is_symlink() or sha(BASE)!=BASE_SHA:raise ValueError('System base image changed')
    if shutil.disk_usage(WORK.parent).free<BASE.stat().st_size+200*1024**2:raise ValueError('Insufficient owned tmpfs scratch')
    WORK.mkdir(mode=0o700)
    spec=importlib.util.spec_from_file_location('wifi_installer',sys.argv[3]);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    incoming,manifest=installer.unpack(Path(sys.argv[1]),sys.argv[2],WORK)
    image=WORK/'DaOS-BSD-RPi4-DSI43-WiFi-ARM64.img'
    with BASE.open('rb') as a,image.open('xb') as b:shutil.copyfileobj(a,b,1024**2)
    md=None;mounted=[]
    root=WORK/'root';root.mkdir();efi=WORK/'efi';efi.mkdir()
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected owned memory disk')
        run(['/sbin/mount','-t','msdosfs','/dev/'+md+'p1',str(efi)]);mounted.append(efi)
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted.append(root)
        changes=installer.apply(root,efi,incoming,manifest)
        payload_path=root/'usr/share/DaOS/payload.json';payload=json.loads(payload_path.read_text())
        payload['system_ui']['wifi']='BCM43455 SDIO / experimental native brcmfmac / WPA2 host supplicant / DHCP'
        payload['wifi']={'kernel_directory':installer.KERNEL,'upstream_commit':'9adbe774d3be91827d628fbd811daf63512acb6d',
                         'firmware':'7.45.265','board_mac':'firmware-populated FDT; distributed example replaced',
                         'physical_wifi_validated':False,'limitations':'2.4GHz authentication unresolved upstream; prefer 5GHz WPA2'}
        for entry in manifest['files']:
            prefix='root/opt/hasslefree/'
            if entry['path'].startswith(prefix):payload['project_source_sha256'][entry['path'][len(prefix):]]=entry['sha256']
        payload_path.write_text(json.dumps(payload,indent=2)+'\n')
        (root/'usr/share/DaOS/wifi/update-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        # Content/ABI guards are assembly checks, not a hardware boot claim.
        for entry in manifest['files']:
            dst=installer.destination(root,efi,entry['path'])
            if sha(dst)!=entry['sha256']:raise ValueError('Installed payload changed')
        settings={'loader':(root/'boot/loader.conf.local').read_text(),'firmware':(efi/'config.txt').read_text()}
        if 'dtoverlay=mmc\n' in settings['firmware']:raise ValueError('Legacy SDIO pin-routing conflict remains')
        if 'dtoverlay=daos-ft5406' not in settings['firmware'] or 'device_tree=daos-rpi4-usb-device.dtb' not in settings['firmware']:raise ValueError('DSI/USB setup lost')
        free=shutil.disk_usage(root).free
    finally:
        for path in reversed(mounted):run(['/sbin/umount',str(path)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    record={'format':'DaOS-freebsd-image-build-v1','derivation_format':'DaOS-pi4-dsi43-wifi-image-v1',
            'status':'assembled','assembled':True,'target':{'architecture':'arm64','elf_machine':183,'efi_machine':43620},
            'base_image_sha256':BASE_SHA,'base_image_unchanged':sha(BASE)==BASE_SHA,
            'payload_sha256':sys.argv[2],'payload_files':len(manifest['files']),'changes':changes,'boot_configuration':settings,
            'root_free_bytes':free,'boot_validated':False,'gui_validated':False,'physical_board_boot_validated':False,
            'physical_wifi_validated':False,'live_device_updated':False,'country_selection_required_before_radio_attach':True,
            'scope':'Compiled and assembled; current physical USB transport offline; 2.4GHz remains upstream experimental',
            'image':{'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)}}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    run(['/usr/bin/xz','-T4','-k',str(image)])
    compressed=Path(str(image)+'.xz')
    record['compressed_image']={'path':str(compressed),'bytes':compressed.stat().st_size,'sha256':sha(compressed),
                                'raw_sha256':record['image']['sha256'],'raw_bytes':record['image']['bytes'],'format':'xz'}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'assembled','image':record['image'],'compressed':record['compressed_image']}),flush=True)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Create a fresh factory image using the same system UI installer payload."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BASE=Path('/var/DaOS-pi4-usb-build-memory/DaOS-dsi43-001/DaOS-BSD-RPi4-DSI43-USB-ADB-arm64.img')
BASE_SHA='fe55c5c2a3f4c3423aeab2d7f09a10021412b18b923d4f3f088cb7df23528ee5'
WORK=Path('/var/DaOS-system-image-001')

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def run(args):
    print(json.dumps(args),flush=True)
    return subprocess.run(args,check=True,timeout=1800)

def main():
    if os.geteuid()!=0 or not sys.platform.startswith('freebsd'):raise ValueError('Native root builder required')
    if sha(BASE)!=BASE_SHA:raise ValueError('DSI base changed')
    # Reclaim only the prior raw build scratch after confirming the completed
    # compressed copy, whose hash was independently retained on F:.
    prior=Path('/var/DaOS-soft-keyboard-image-001/DaOS-BSD-RPi4-DSI43-USB-ADB-Keyboard-arm64.img')
    if prior.exists():
        compressed=Path(str(prior)+'.xz')
        if prior.is_symlink() or sha(prior)!='82688a4509d09a0ba5146fb85ca5551daec101e9d3e8927b72c850a4bf0b74de':raise ValueError('Prior scratch identity changed')
        if sha(compressed)!='39f8a8aea47db42f30e55c5b19d316dbec7011f744d370c7421b41c43fef2df2':raise ValueError('Prior archive changed')
        prior.unlink()
    if shutil.disk_usage('/var').free<BASE.stat().st_size+700*1024**2:raise ValueError('Insufficient native scratch')
    WORK.mkdir(mode=0o700)
    spec=importlib.util.spec_from_file_location('installer',sys.argv[3]);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    incoming,manifest=installer.unpack(Path(sys.argv[1]),sys.argv[2],WORK)
    image=WORK/'DaOS-BSD-RPi4-DSI43-System-ARM64.img'
    with BASE.open('rb') as a,image.open('xb') as b:shutil.copyfileobj(a,b,1024**2)
    root=WORK/'root';root.mkdir();md=None;mounted=False
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected owned md device')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        changes=installer.apply(root,incoming,manifest,'system-ui-image-001')
        payload_path=root/'usr/share/DaOS/payload.json';payload=json.loads(payload_path.read_text())
        for entry in manifest['files']:
            if entry['path'].startswith('opt/hasslefree/'):
                payload['project_source_sha256'][entry['path'][len('opt/hasslefree/'):]]=entry['sha256']
        payload['system_ui']={'ascii_brand':'DaOS','boot_handoff':'native framebuffer -> Qt splash -> ready UI',
                              'wifi':'Local net80211/wpa_supplicant service; adapter required',
                              'terminal':'PTY /bin/sh as camera user; touch keyboard'}
        payload_path.write_text(json.dumps(payload,indent=2)+'\n')
        (root/'usr/share/DaOS/system-ui-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    finally:
        if mounted:run(['/sbin/umount',str(root)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    record={'format':'DaOS-freebsd-image-build-v1','derivation_format':'DaOS-DSI43-system-ui-image-v1',
            'status':'assembled','assembled':True,'target':{'architecture':'arm64','elf_machine':183,'efi_machine':43620},
            'base_image_sha256':BASE_SHA,'base_image_unchanged':sha(BASE)==BASE_SHA,'payload_sha256':sys.argv[2],
            'boot_validated':False,'gui_validated':False,'physical_board_boot_validated':False,
            'changes':changes,'image':{'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)},
            'scope':'Factory image matching the live system UI payload; not separately booted. Current board has no detected wireless adapter.'}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    run(['/usr/bin/xz','-T4','-k',str(image)])
    compressed=Path(str(image)+'.xz')
    record['compressed_image']={'path':str(compressed),'bytes':compressed.stat().st_size,'sha256':sha(compressed),
                                'format':'xz','raw_sha256':record['image']['sha256'],'raw_bytes':record['image']['bytes']}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'assembled','image_sha256':record['image']['sha256']}),flush=True)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Create a fresh keyboard image from the frozen DSI43 image using file-backed md."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

BASE=Path('/var/DaOS-pi4-usb-build-memory/DaOS-dsi43-001/DaOS-BSD-RPi4-DSI43-USB-ADB-arm64.img')
BASE_SHA='fe55c5c2a3f4c3423aeab2d7f09a10021412b18b923d4f3f088cb7df23528ee5'
WORK=Path('/var/DaOS-soft-keyboard-image-001')
FILES=('main.py','Main.qml','Console.qml','SoftKeyboard.qml','soft_keyboard.py')

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()

def run(argv):
    print(json.dumps(argv),flush=True)
    return subprocess.run(argv,check=True,timeout=1800)

def main():
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise RuntimeError('Native root builder required')
    if sha(BASE)!=BASE_SHA:raise RuntimeError('DSI43 base changed')
    if shutil.disk_usage('/var').free<BASE.stat().st_size+700*1024**2:raise RuntimeError('Insufficient private image scratch space')
    archive=Path(sys.argv[1])
    if sha(archive)!=sys.argv[2]:raise RuntimeError('Keyboard archive changed')
    WORK.mkdir(mode=0o700)
    incoming=WORK/'incoming';incoming.mkdir()
    with tarfile.open(archive) as t:
        members=t.getmembers()
        if {m.name for m in members}!=set(FILES) or any(not m.isfile() or m.size>256*1024 for m in members):raise RuntimeError('Invalid keyboard payload')
        t.extractall(incoming,filter='data')
    image=WORK/'DaOS-BSD-RPi4-DSI43-USB-ADB-Keyboard-arm64.img'
    with BASE.open('rb') as a,image.open('xb') as b:shutil.copyfileobj(a,b,1024**2)
    md=None;mounted=False
    root=WORK/'root';root.mkdir()
    changes={}
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise RuntimeError('Unexpected owned md device')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        for name in FILES:
            dst=root/'opt/hasslefree/ui/on-device'/name
            if dst.is_symlink():raise RuntimeError('Redirected app input')
            changes[name]={'before':sha(dst) if dst.exists() else None,'after':sha(incoming/name)}
            shutil.copyfile(incoming/name,dst);os.chmod(dst,0o644);os.chown(dst,0,0)
        feature={'format':'DaOS-touch-keyboard-v1','layouts':['latin','numbers','symbols'],
                 'features':['focus-triggered panel','editor preview','one-shot shift and caps','repeat backspace','cursor arrows','select all','hide','done'],
                 'chinese_IME':False,'files':changes}
        (root/'usr/share/DaOS/soft-keyboard.json').write_text(json.dumps(feature,indent=2)+'\n')
        payload_path=root/'usr/share/DaOS/payload.json'
        payload=json.loads(payload_path.read_text());payload['soft_keyboard']=feature
        for name in FILES:payload['project_source_sha256']['ui/on-device/'+name]=sha(incoming/name)
        payload_path.write_text(json.dumps(payload,indent=2)+'\n')
    finally:
        if mounted:run(['/sbin/umount',str(root)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    record={'format':'DaOS-freebsd-image-build-v1','derivation_format':'DaOS-DSI43-soft-keyboard-image-v1',
            'assembled':True,'status':'assembled','target':{'architecture':'arm64','elf_machine':183,'efi_machine':43620},
            'base_image_sha256':BASE_SHA,'base_image_unchanged':sha(BASE)==BASE_SHA,
            'keyboard_archive_sha256':sha(archive),'soft_keyboard':feature,
            'boot_validated':False,'gui_validated':False,'physical_board_boot_validated':False,
            'scope':'Factory image updated with the same keyboard files hot-deployed to the attached Pi4; this new image has not been separately booted.',
            'image':{'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)}}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    run(['/usr/bin/xz','-T4','-k',str(image)])
    xz=Path(str(image)+'.xz')
    record['compressed_image']={'path':str(xz),'bytes':xz.stat().st_size,'sha256':sha(xz),'format':'xz',
                                'raw_sha256':record['image']['sha256'],'raw_bytes':record['image']['bytes']}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'assembled','image_sha256':record['image']['sha256']}),flush=True)

if __name__=='__main__':main()

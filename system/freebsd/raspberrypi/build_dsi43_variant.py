#!/usr/bin/env python3
"""Derive an unboot-tested DSI43 candidate from the completed USB image.

Runs inside the isolated native FreeBSD builder. Only a newly created file-backed
md device is mounted. No physical disk or source world is changed.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

BASE=Path('/var/DaOS-pi4-usb-build-memory/DaOS-image-rpi4-usb-adb-002/DaOS-BSD-RPi4-USB-ADB-arm64.img')
BASE_SHA='ed84cce5eb14da514732f88909d0baee8f22266a3f837569185b30f855e1394e'
SRC=Path('/var/daos-builds/daos-base-arm64-p6-001/source/usr/src')
STATE=Path('/var/daos-builds/daos-base-arm64-pi-splash-001/build-state.json')
KOBJ=Path('/var/daos-builds/daos-base-arm64-pi-splash-001/obj/var/daos-builds/daos-base-arm64-p6-001/source/usr/src/arm64.aarch64/sys/DaOS')
WORK=Path('/var/DaOS-pi4-usb-build-memory/DaOS-dsi43-001')
INPUT=Path('/var/DaOS-dsi43-input-001')
CONFIG='''
# DaOS Waveshare 4.3inch DSI LCD / legacy firmware framebuffer.
[all]
hdmi_safe=0
hdmi_force_hotplug=0
display_auto_detect=0
ignore_lcd=0
display_default_lcd=1
disable_touchscreen=0
disable_overscan=1
disable_splash=1
framebuffer_width=800
framebuffer_height=480
max_framebuffer_width=800
max_framebuffer_height=480
lcd_rotate=0
lcd_framerate=60
dtoverlay=daos-ft5406
'''
DTS='''/dts-v1/;
/plugin/;
/ {
    compatible = "brcm,bcm2835";
    fragment@0 {
        target-path = "/soc/firmware";
        __overlay__ {
            touchscreen {
                compatible = "raspberrypi,firmware-ts";
                touchscreen-size-x = <800>;
                touchscreen-size-y = <480>;
                status = "okay";
            };
        };
    };
};
'''

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def run(argv, **kw):
    print(json.dumps(argv),flush=True)
    return subprocess.run(argv,check=True,**kw)

def main():
    if platform.system()!='FreeBSD' or os.geteuid()!=0:raise ValueError('Native root builder required')
    if BASE.stat().st_size!=3254820864 or sha(BASE)!=BASE_SHA:raise ValueError('Base identity changed')
    if sha(STATE)!='f8c0dd35aedc768f9127400c14173d0a2e6751fbf9cd7a2cfe347066e0abb914':raise ValueError('Base receipt changed')
    if shutil.disk_usage(WORK.parent).free<BASE.stat().st_size+600*1024**2:raise ValueError('Insufficient scratch space')
    WORK.mkdir(mode=0o700)
    native=WORK/'native';native.mkdir()
    shutil.copyfile(SRC/'sys/modules/rpi_ft5406/Makefile',native/'Makefile')
    shell=WORK/'build-touch.sh'
    shell.write_text('set -eu\nmake -C '+str(native)+' SYSDIR='+str(SRC/'sys')+' KERNBUILDDIR='+str(KOBJ)+' all\n')
    state=json.loads(STATE.read_text())
    env=dict(os.environ,MAKEOBJDIRPREFIX=str(WORK/'obj'))
    argv=['make','-C',str(SRC),'TARGET=arm64','TARGET_ARCH=aarch64',
          'SRCCONF=/var/daos-builds/daos-base-arm64-pi-splash-001/config/src.conf',
          'SRC_ENV_CONF=/dev/null','__MAKE_CONF=/dev/null','WORLDTMP='+state['stage_directory'],
          'buildenv','BUILDENV_SHELL=/usr/bin/env PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh '+str(shell)]
    with (WORK/'touch-build.log').open('x') as log:run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    module=native/'rpi_ft5406.ko'
    header=module.read_bytes()[:64]
    if header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',header,18)[0]!=183:raise ValueError('Not AArch64 module')
    (WORK/'daos-ft5406.dts').write_text(DTS)
    run(['/usr/bin/dtc','-I','dts','-O','dtb','-o',str(WORK/'daos-ft5406.dtbo'),str(WORK/'daos-ft5406.dts')])
    image=WORK/'DaOS-BSD-RPi4-DSI43-USB-ADB-arm64.img'
    with BASE.open('rb') as a,image.open('xb') as b:shutil.copyfileobj(a,b,1024*1024)
    md=None;mounted=[]
    root=WORK/'root';root.mkdir();efi=WORK/'efi';efi.mkdir()
    changes={}
    def install(source,destination):
        old=sha(destination) if destination.is_file() else None
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
        os.chmod(destination,0o644);os.chown(destination,0,0)
        changes[str(destination.relative_to(WORK))]={'before_sha256':old,'sha256':sha(destination)}
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected owned md device')
        run(['/sbin/mount','-t','msdosfs','/dev/'+md+'p1',str(efi)]);mounted.append(efi)
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted.append(root)
        for name in ['main.py','Main.qml']:
            install(INPUT/name,root/'opt/hasslefree/ui/on-device'/name)
        install(module,root/'boot/modules/rpi_ft5406.ko')
        install(INPUT/'DaOS-logo-480.png',root/'boot/images/DaOS-logo.png')
        install(WORK/'daos-ft5406.dtbo',efi/'overlays/daos-ft5406.dtbo')
        conf=efi/'config.txt'
        old=sha(conf)
        # Remove the prior HDMI force-mode block. Keep firmware and USB DWC setup.
        lines=[line for line in conf.read_text().splitlines() if not line.lstrip().startswith(('hdmi_mode=','hdmi_group=','framebuffer_','max_framebuffer_','hdmi_force_hotplug=','hdmi_safe='))]
        conf.write_text('\n'.join(lines)+'\n'+CONFIG)
        changes['efi/config.txt']={'before_sha256':old,'sha256':sha(conf)}
        loader=root/'boot/loader.conf'
        old=sha(loader)
        loader.write_text(loader.read_text()+'\n# Firmware-polling FT5406 evdev touch path\nrpi_ft5406_load="YES"\n')
        changes['root/boot/loader.conf']={'before_sha256':old,'sha256':sha(loader)}
        touch=root/'usr/local/etc/X11/xorg.conf.d/30-DaOS-touch.conf'
        touch.write_text('Section "InputClass"\n Identifier "DaOS DSI touchscreen"\n MatchIsTouchscreen "on"\n Driver "libinput"\n Option "TransformationMatrix" "1 0 0 0 1 0 0 0 1"\nEndSection\n')
        os.chmod(touch,0o644);os.chown(touch,0,0)
        changes[str(touch.relative_to(WORK))]={'sha256':sha(touch)}
        receipt={'format':'DaOS-freebsd-image-build-v1','derivation_format':'DaOS-DSI43-derived-image-v1','status':'assembled','assembled':True,
                 'target':{'architecture':'arm64','elf_machine':183,'efi_machine':43620},
                 'boot_validated':False,'gui_validated':False,'physical_board_boot_validated':False,'base_image_sha256':BASE_SHA,
                 'display':{'model':'Waveshare 4.3inch DSI LCD','width':800,'height':480,
                            'path':'Legacy VideoCore firmware -> U-Boot EFI framebuffer -> SCFB',
                            'touch_path':'VideoCore FT5406 touch buffer -> source-built rpi_ft5406 -> evdev -> libinput',
                            'physical_display_verified':False,'physical_touch_verified':False},
                 'source_commit':state['source']['commit'],'touch_build_argv':argv,'touch_module_sha256':sha(module),
                 'touch_source_sha256':sha(SRC/'sys/arm/broadcom/bcm2835/bcm2835_ft5406.c'),
                 'logo_provenance':json.loads((INPUT/'logo-provenance.json').read_text()),
                 'changes':changes,'vm_tests_run':False,
                 'scope':'Hardware candidate using firmware LCD detection; no native VC4 KMS driver claim. Firmware must identify the attached panel and provide framebuffer and touch buffer.'}
        (root/'usr/share/DaOS/dsi43-derivation.json').write_text(json.dumps(receipt,indent=2)+'\n')
        board_path=root/'usr/share/DaOS/raspberrypi-board.json'
        board=json.loads(board_path.read_text())
        board['display']=receipt['display']
        board['boot_logo'].update(width=receipt['logo_provenance']['size'][0],height=receipt['logo_provenance']['size'][1],sha256=sha(root/'boot/images/DaOS-logo.png'),bytes=(root/'boot/images/DaOS-logo.png').stat().st_size,resized_by_assembler=True)
        for item in board['boot_payload']:
            if item['path']=='config.txt':item.update(bytes=conf.stat().st_size,sha256=sha(conf))
        board['boot_payload'].append({'path':'overlays/daos-ft5406.dtbo','bytes':(WORK/'daos-ft5406.dtbo').stat().st_size,'sha256':sha(WORK/'daos-ft5406.dtbo')})
        board['dsi43_derivation']='/usr/share/DaOS/dsi43-derivation.json'
        board_path.write_text(json.dumps(board,indent=2)+'\n')
        payload_path=root/'usr/share/DaOS/payload.json'
        payload=json.loads(payload_path.read_text())
        payload['board']=board
        payload['dsi43_derivation']='/usr/share/DaOS/dsi43-derivation.json'
        for name in ['main.py','Main.qml']:
            key='ui/on-device/'+name
            if key in payload['project_source_sha256']:payload['project_source_sha256'][key]=sha(INPUT/name)
        payload_path.write_text(json.dumps(payload,indent=2)+'\n')
    finally:
        for mount in reversed(mounted):run(['/sbin/umount',str(mount)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    receipt['image']={'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)}
    receipt['source_image_unchanged']=sha(BASE)==BASE_SHA
    (WORK/'image-evidence.json').write_text(json.dumps(receipt,indent=2)+'\n')
    run(['/usr/bin/xz','-T4','-k',str(image)],timeout=1800)
    xz=Path(str(image)+'.xz')
    receipt['compressed_image']={'path':str(xz),'bytes':xz.stat().st_size,'sha256':sha(xz),'format':'xz','raw_sha256':receipt['image']['sha256'],'raw_bytes':receipt['image']['bytes']}
    (WORK/'image-evidence.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2),flush=True)

if __name__=='__main__':main()

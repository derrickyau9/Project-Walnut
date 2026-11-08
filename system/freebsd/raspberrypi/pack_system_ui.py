"""Create a public-only, explicitly mapped system UI update payload on F:."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tarfile

ROOT=Path(__file__).resolve().parents[3]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--native',type=Path,required=True)
    p.add_argument('--startup',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise ValueError('Choose a new update archive')
    header=args.native.read_bytes()[:64]
    if header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',header,18)[0]!=183:raise ValueError('AArch64 native splash required')
    if args.startup.stat().st_size!=800*480*3:raise ValueError('800x480 RGB asset required')
    names=['main.py','CameraMenu.qml','Console.qml','Main.qml','shell_model.py','soft_keyboard.py','SoftKeyboard.qml','TerminalPage.qml','WifiPage.qml','terminal_surface.py','terminal_exec.py','system_settings.py','BootSplash.qml','boot_status.py']
    sources={'opt/hasslefree/ui/on-device/'+n:ROOT/'ui/on-device'/n for n in names}
    vendor=ROOT/'ui/on-device/vendor'
    for file in vendor.rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts:sources['opt/hasslefree/ui/on-device/vendor/'+file.relative_to(vendor).as_posix()]=file
    for name in ['usr/local/libexec/DaOS-camera-session.py','usr/local/libexec/DaOS-system-service.py','usr/local/etc/rc.d/DaOS-system','etc/rc.d/DaOS-boot','boot/lua/gfx-daos.lua']:
        sources[name]=ROOT/'system/freebsd/image/overlay'/name
    sources['usr/local/libexec/DaOS-boot']=args.native
    sources['usr/share/DaOS/boot/startup.rgb']=args.startup
    records=[]
    for rel,file in sorted(sources.items()):
        if file.is_symlink():raise ValueError('Regular public input required')
        mode=0o755 if rel.startswith(('usr/local/etc/rc.d/','etc/rc.d/')) or rel=='usr/local/libexec/DaOS-boot' else 0o644
        records.append({'path':rel,'mode':mode,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    manifest=args.output.with_suffix(args.output.suffix+'.json')
    removals=[{'path':'usr/local/etc/rc.d/DaOS-boot','sha256':'8d206270d913b0edb585e06165626c83ee6eb1c5dc0f62242f4b307db3dd022b'}]
    with manifest.open('x') as f:json.dump({'format':'DaOS-system-ui-payload-v1','files':records,'removals':removals},f,indent=2);f.write('\n')
    with tarfile.open(args.output,'x:gz') as t:
        t.add(manifest,arcname='manifest.json')
        for rel,file in sorted(sources.items()):t.add(file,arcname='payload/'+rel)
    print(hashlib.sha256(args.output.read_bytes()).hexdigest())

if __name__=='__main__':main()

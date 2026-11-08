"""Install a prepared app release through the project's exact-serial USB link."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir',type=Path,required=True)
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--rollback',action='store_true')
    parser.add_argument('--server-port',type=int,default=5059)
    args=parser.parse_args()
    release=args.release_dir.resolve(strict=True)
    record=json.loads((release/'release.json').read_text(encoding='utf-8'))
    version=record['version']
    if not re.fullmatch('camera-experience-[0-9]{3}',version):raise ValueError('invalid release identity')
    bundle=release/(version+'.tar.gz');installer=release/'install_camera_app.py'
    if (bundle.is_symlink() or installer.is_symlink() or digest(bundle)!=record['bundle_sha256']
            or digest(installer)!=record['installer_sha256']):
        raise ValueError('release files changed; no device action performed')
    if args.check_only:
        print(json.dumps({'version':version,'bundle_verified':True,'installer_verified':True,
                          'device_changed':False}));return
    if not 1024<=args.server_port<=65535:raise ValueError('invalid project USB server port')
    from system.adb import connect_usb as usb
    serial='DaOS-USB-ADB-DEVELOPMENT'
    tools=usb.DEFAULT_TOOLS.resolve(strict=True)
    adb=tools/'platform-tools/adb.exe'
    _,environment=usb.ensure_server(tools,adb,args.server_port,serial)
    def call(*parts,timeout=15):
        result=usb.adb_call(adb,args.server_port,environment,*parts,timeout=timeout)
        if result.returncode:raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'USB command failed')
        return result.stdout.strip()
    candidates=usb.daos_devices(usb.parse_devices(call('devices','-l')),serial)
    selected=usb.select_device(candidates,interactive=False)
    if selected is None:raise RuntimeError('DaOS Pi is not connected; no files were sent')
    selector=['-t',selected['transport_id']] if selected.get('transport_id','').isdecimal() else ['-s',serial]
    def shell(command,timeout=15):return call(*selector,'shell',command,timeout=timeout)
    if shell('uname -s')!='FreeBSD' or shell('uname -m') not in ('arm64','aarch64') or shell('id -u')!='0':
        raise ValueError('expected the authorized native FreeBSD ARM64 development shell')
    incoming='/var/DaOS/updates/incoming'
    shell('mkdir -p '+shlex.quote(incoming))
    remote_installer=incoming+'/install_camera_app.py'
    print('Sending the verified application installer…',flush=True)
    call(*selector,'push',str(installer),remote_installer,timeout=60)
    if shell('sha256 -q '+shlex.quote(remote_installer))!=record['installer_sha256']:
        raise ValueError('USB installer readback mismatch')
    command=['/usr/local/bin/python3.12',remote_installer]
    if args.rollback:command+=['--rollback','--tag',version]
    else:
        remote_bundle=incoming+'/'+bundle.name
        print('Sending the application bundle…',flush=True)
        call(*selector,'push',str(bundle),remote_bundle,timeout=120)
        command += [remote_bundle,'--sha256',record['bundle_sha256'],'--tag',version]
    print('Switching camera application; the kernel is unchanged…',flush=True)
    output=shell(' '.join(shlex.quote(part) for part in command),timeout=150)
    print(output)
    receipt={'version':version,'usb_serial':serial,'operation':'rollback' if args.rollback else 'install',
             'completed_at_unix':time.time(),'output':output}
    (release/'last-device-install.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()

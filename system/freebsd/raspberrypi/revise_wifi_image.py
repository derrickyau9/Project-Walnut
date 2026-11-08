#!/usr/bin/env python3
"""Revise an owned candidate, retaining its reconstructable compressed copy."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import re

BASE=Path('/var/DaOS-wifi-build-memory/image-002')
WORK=Path('/var/DaOS-wifi-build-memory/image-003')

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def run(args):return subprocess.run(args,check=True,timeout=1800)

def main():
    global BASE,WORK
    if len(sys.argv)>5:
        if not all(re.fullmatch('[0-9]{3}',r) for r in sys.argv[4:6]):raise ValueError('Invalid candidate revision')
        BASE=BASE.parent/('image-'+sys.argv[4]);WORK=WORK.parent/('image-'+sys.argv[5])
    if os.uname().sysname!='FreeBSD' or os.geteuid()!=0:raise ValueError('Native root builder required')
    prior=json.loads((BASE/'image-evidence.json').read_text())
    source=BASE/'DaOS-BSD-RPi4-DSI43-WiFi-ARM64.img'
    if source.is_symlink() or sha(source)!=prior['image']['sha256'] or sha(Path(str(source)+'.xz'))!=prior['compressed_image']['sha256']:raise ValueError('Candidate identity changed')
    WORK.mkdir(mode=0o700)
    spec=importlib.util.spec_from_file_location('wifi_installer',sys.argv[3]);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    incoming,manifest=installer.unpack(Path(sys.argv[1]),sys.argv[2],WORK)
    image=WORK/source.name;os.rename(source,image)
    md=None;mounted=[];root=WORK/'root';root.mkdir();efi=WORK/'efi';efi.mkdir()
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected owned memory disk')
        run(['/sbin/mount','-t','msdosfs','/dev/'+md+'p1',str(efi)]);mounted.append(efi)
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted.append(root)
        changes=installer.apply(root,efi,incoming,manifest,allow_existing_kernel=True)
        if not (root/'sbin/resolvconf').is_file():raise ValueError('DHCP resolver manager missing')
        if os.readlink(root/'etc/resolv.conf')!='/var/run/resolv.conf':raise ValueError('Read-only-root resolver handoff missing')
        path=root/'usr/share/DaOS/payload.json';payload=json.loads(path.read_text())
        payload['wifi']['limitations']='Pi4 onboard: 5GHz only; 2.4GHz excluded because authentication remains unresolved upstream'
        for entry in manifest['files']:
            prefix='root/opt/hasslefree/'
            if entry['path'].startswith(prefix):payload['project_source_sha256'][entry['path'][len(prefix):]]=entry['sha256']
            if sha(installer.destination(root,efi,entry['path']))!=entry['sha256']:raise ValueError('Installed payload mismatch')
        path.write_text(json.dumps(payload,indent=2)+'\n')
        (root/'usr/share/DaOS/wifi/update-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        settings={'loader':(root/'boot/loader.conf.local').read_text(),'firmware':(efi/'config.txt').read_text(),
                  'resolver':(root/'etc/resolvconf.conf').read_text(),'resolver_link':os.readlink(root/'etc/resolv.conf')}
    finally:
        for p in reversed(mounted):run(['/sbin/umount',str(p)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    record=dict(prior);record.pop('compressed_image',None)
    record.update(parent_candidate_raw_sha256=prior['image']['sha256'],payload_sha256=sys.argv[2],payload_files=len(manifest['files']),
                  changes=changes,boot_configuration=settings,onboard_wifi_band='5GHz only',
                  scope='Compiled and assembled; no physical board boot or wireless association observed for this candidate',
                  image={'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)})
    run(['/usr/bin/xz','-T4','-k',str(image)])
    compressed=Path(str(image)+'.xz')
    record['compressed_image']={'path':str(compressed),'bytes':compressed.stat().st_size,'sha256':sha(compressed),
                                'raw_sha256':record['image']['sha256'],'raw_bytes':record['image']['bytes'],'format':'xz'}
    (WORK/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'assembled','image':record['image'],'compressed':record['compressed_image']}),flush=True)

if __name__=='__main__':main()

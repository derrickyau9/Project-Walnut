#!/usr/bin/env python3
"""Preserve a completed candidate's XZ, revise its private raw in a new work tree."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()

def run(args):
    print(json.dumps(args),flush=True)
    return subprocess.run(args,check=True,timeout=1800)

def main():
    old=Path(sys.argv[4]) if len(sys.argv)>4 else Path('/var/DaOS-system-image-001')
    work=Path(sys.argv[5]) if len(sys.argv)>5 else Path('/var/DaOS-system-image-002')
    if old.parent!=Path('/var') or work.parent!=Path('/var') or not old.name.startswith('DaOS-system-image-') or not work.name.startswith('DaOS-system-image-'):raise ValueError('Private image work paths required')
    prior=json.loads((old/'image-evidence.json').read_text())
    source=old/'DaOS-BSD-RPi4-DSI43-System-ARM64.img'
    archive=Path(str(source)+'.xz')
    if source.is_symlink() or source.stat().st_size!=3254820864 or sha(source)!=prior['image']['sha256']:raise ValueError('Prior raw identity changed')
    if sha(archive)!=prior['compressed_image']['sha256']:raise ValueError('Prior reconstructable archive changed')
    work.mkdir(mode=0o700)
    spec=importlib.util.spec_from_file_location('installer',sys.argv[3]);installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    incoming,manifest=installer.unpack(Path(sys.argv[1]),sys.argv[2],work)
    image=work/source.name
    os.rename(source,image)
    root=work/'root';root.mkdir();md=None;mounted=False
    try:
        md=subprocess.check_output(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)],text=True).strip()
        if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected owned md device')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        changes=installer.apply(root,incoming,manifest,'system-ui-image-002')
        payload_path=root/'usr/share/DaOS/payload.json';payload=json.loads(payload_path.read_text())
        for entry in manifest['files']:
            if entry['path'].startswith('opt/hasslefree/'):
                payload['project_source_sha256'][entry['path'][len('opt/hasslefree/'):]]=entry['sha256']
        payload_path.write_text(json.dumps(payload,indent=2)+'\n')
        (root/'usr/share/DaOS/system-ui-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    finally:
        if mounted:run(['/sbin/umount',str(root)])
        if md:run(['/sbin/mdconfig','-d','-u',md[2:]])
    record=dict(prior)
    record.pop('compressed_image',None)
    record.update(parent_candidate_raw_sha256=prior['image']['sha256'],parent_candidate_receipt_sha256=sha(old/'image-evidence.json'),
                  payload_sha256=sys.argv[2],changes=changes,
                  correction='Final UI compatibility and nonintrusive Wi-Fi status polling; prior compressed candidate retained, not delivered',
                  image={'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)})
    (work/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    run(['/usr/bin/xz','-T4','-k',str(image)])
    compressed=Path(str(image)+'.xz')
    record['compressed_image']={'path':str(compressed),'bytes':compressed.stat().st_size,'sha256':sha(compressed),'format':'xz',
                                'raw_sha256':record['image']['sha256'],'raw_bytes':record['image']['bytes']}
    (work/'image-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':'assembled','image_sha256':record['image']['sha256']}),flush=True)

if __name__=='__main__':main()

"""Package only the camera application, without kernel/boot or user data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import io

ROOT = Path(__file__).resolve().parents[1]


def source_files():
    result = {}
    for relative in ('camera', 'simulation', 'daos/capability_runtime', 'daos/camera_link', 'ui/on-device'):
        base = ROOT / relative
        for path in sorted(base.rglob('*')):
            local = path.relative_to(base)
            if any(part.startswith(('evidence', '__pycache__', '.')) for part in local.parts):
                continue
            if not path.is_file() or path.is_symlink(): continue
            allowed = path.suffix in {'.py','.qml','.qm','.json','.sqlite3','.db','.txt','.md','.tsv','.html'}
            if relative == 'ui/on-device' and any(part in ('ime_data','vendor','fonts') for part in local.parts):
                allowed = not path.name.endswith(('.tmp','.new','.pyc'))
            if relative != 'ui/on-device': allowed = path.suffix == '.py'
            if not allowed or 'native' in local.parts: continue
            result['opt/hasslefree/' + path.relative_to(ROOT).as_posix()] = path
    if (ROOT/'daos/__init__.py').is_file():
        result['opt/hasslefree/daos/__init__.py'] = ROOT/'daos/__init__.py'
    result['usr/share/DaOS/camera-app-installer.py'] = ROOT/'system/freebsd/raspberrypi/deploy_camera_app.py'
    processing=ROOT/'camera/processing'
    for path in sorted(processing.rglob('*')):
        local=path.relative_to(processing)
        if path.is_file() and not path.is_symlink() and (local.parts[0]=='licenses' or path.name in ('SOURCES.json','README.md')):
            result['opt/hasslefree/camera/processing/'+local.as_posix()]=path
    return result


def build(output, version, native_dir=None):
    if output.exists(): raise ValueError('choose a new bundle path')
    files = source_files()
    if version >= 'camera-experience-004':
        native_dir=Path(native_dir or ROOT/'build/DaOS_Camera_004/arm64')
        for name in ('hf-camerad','hf-imager'):
            path=native_dir/name
            raw=path.read_bytes()[:64]
            if raw[:7]!=b'\x7fELF\x02\x01\x01' or int.from_bytes(raw[18:20],'little')!=183 or raw[7]!=9:
                raise ValueError('004 requires matching FreeBSD ARM64 native binaries: '+name)
            files['opt/hasslefree/bin/'+name]=path
        overlay=ROOT/'system/freebsd/image/overlay/usr/local/libexec'
        for name in ('DaOS-system-service.py','DaOS-system-ops.py','DaOS-camera-link.py'):
            files['opt/hasslefree/support/'+name]=overlay/name
        files['opt/hasslefree/support/DaOS-usb-adb-session.py']=ROOT/'system/adb/integration/files/usr/local/libexec/DaOS-usb-adb-session.py'
        for path in sorted((ROOT/'system/adb/daemon').glob('*.py')):
            if path.name.startswith(('test_','guest_','integration_')):continue
            files['opt/hasslefree/support/daos-adb/'+path.name]=path
    entries = []
    payloads = {}
    for name,path in sorted(files.items()):
        raw = path.read_bytes()
        payloads[name] = raw
        mode=0o755 if name.startswith('opt/hasslefree/bin/') else 0o644
        entries.append(dict(path=name, bytes=len(raw), mode=mode, sha256=hashlib.sha256(raw).hexdigest()))
    manifest = {'format':'DaOS-camera-app-v1','version':version,
                'target':'FreeBSD ARM64 / Qt 5.15 / Pi4 800x480',
                'camera_authority_api':'1.1','files':entries,
                'scope':'versioned application/native workers/controlled system helpers; synthetic camera; no boot or kernel writes; user media retained'}
    raw_manifest = (json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'x:gz') as archive:
        info = tarfile.TarInfo('manifest.json');info.size=len(raw_manifest);info.mode=0o644
        archive.addfile(info,io.BytesIO(raw_manifest))
        for entry in entries:
            info = tarfile.TarInfo('payload/'+entry['path']);info.size=entry['bytes'];info.mode=entry['mode']
            archive.addfile(info,io.BytesIO(payloads[entry['path']]))
    receipt = dict(manifest,archive_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                   archive_bytes=output.stat().st_size)
    output.with_suffix(output.suffix+'.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version',default='camera-experience-001')
    parser.add_argument('--native-dir',type=Path)
    args=parser.parse_args()
    receipt=build(args.output,args.version,args.native_dir)
    print(json.dumps({key:receipt[key] for key in ('version','archive_sha256','archive_bytes')}))


if __name__=='__main__':main()

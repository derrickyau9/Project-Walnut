#!/usr/local/bin/python3.12
"""Hash-bound application updates with rollback; no kernel or boot writes.

--stage-root installs into an ordinary scratch image tree without services.
Native live installation requires FreeBSD root and checks a newly ready GUI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time

FORMAT = 'DaOS-camera-app-v1'
MAX_TOTAL = 96*1024*1024
ACTIVE_PATH = 'opt/hasslefree/camera-app-active.json'
ENTRY_PATH = 'opt/hasslefree/ui/on-device/main.py'
SERVICE_ENTRIES={
 'usr/local/libexec/DaOS-system-service.py':'support/DaOS-system-service.py',
 'usr/local/libexec/DaOS-usb-adb-session.py':'support/DaOS-usb-adb-session.py',
 'usr/local/libexec/DaOS-camera-link.py':'support/DaOS-camera-link.py'}
BOOTSTRAP = '''"""DaOS application-only version selector; all selected files are prepared first."""
import json
from pathlib import Path
import re
import runpy
import sys

base = Path(__file__).resolve().parents[2]
pointer = base / 'camera-app-active.json'
if pointer.is_symlink() or pointer.stat().st_size > 4096:
    raise SystemExit('Invalid camera application selector')
active = json.loads(pointer.read_text(encoding='utf-8'))
version = active.get('version', '')
if not re.fullmatch('camera-experience-[0-9]{3}', version):
    raise SystemExit('Invalid camera application version')
release = base / 'releases' / version
ready = release / 'release-ready.json'
if release.is_symlink() or ready.is_symlink() or ready.stat().st_size > 4096:
    raise SystemExit('Camera application release is not ready')
record = json.loads(ready.read_text(encoding='utf-8'))
if record.get('version') != version or record.get('manifest_sha256') != active.get('manifest_sha256'):
    raise SystemExit('Camera application release identity mismatch')
program = release / 'ui/on-device/main.py'
sys.path.insert(0, str(program.parent))
sys.argv[0] = str(program)
runpy.run_path(str(program), run_name='__main__')
'''


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def sync_directory(path):
    if os.name!='nt':
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def save(path,value,mode=None):
    temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
        if mode is not None:os.chmod(temp,mode)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
    sync_directory(path.parent)


def allowed(name):
    part=PurePosixPath(name)
    return (isinstance(name,str) and not part.is_absolute() and '..' not in part.parts
            and '\\' not in name and str(part)==name
            and (name.startswith('opt/hasslefree/ui/on-device/')
                 or name.startswith('opt/hasslefree/camera/')
                 or name.startswith('opt/hasslefree/simulation/')
                 or name.startswith('opt/hasslefree/daos/capability_runtime/')
                 or name.startswith('opt/hasslefree/daos/camera_link/')
                 or name.startswith('opt/hasslefree/support/')
                 or name in ('opt/hasslefree/bin/hf-camerad','opt/hasslefree/bin/hf-imager')
                 or name in SERVICE_ENTRIES
                 or name=='opt/hasslefree/daos/__init__.py'
                 or name==ACTIVE_PATH
                 or name=='usr/share/DaOS/camera-app-installer.py'))


def target(root,name):
    if not allowed(name):raise ValueError('application destination not allowed')
    path=root/name
    for parent in (path,*path.parents):
        if parent.is_symlink():raise ValueError('redirected application destination')
        if parent==root:break
    if path.exists() and not path.is_file():raise ValueError('non-file application destination')
    return path


def unpack(archive,expected,work):
    if not re.fullmatch('[0-9a-f]{64}',expected) or sha(archive)!=expected:
        raise ValueError('application archive hash mismatch')
    incoming=work/'incoming';incoming.mkdir()
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers()
        if len(members)>2048 or sum(m.size for m in members)>MAX_TOTAL:
            raise ValueError('application payload exceeds bounds')
        names=set()
        for member in members:
            if (member.name in names or not member.isfile() or member.size<0
                    or member.name!='manifest.json' and not member.name.startswith('payload/')):
                raise ValueError('invalid application archive member')
            names.add(member.name)
            path=PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('unsafe application archive path')
        manifest_member=bundle.getmember('manifest.json')
        if manifest_member.size>512*1024:raise ValueError('manifest exceeds bound')
        manifest=json.loads(bundle.extractfile(manifest_member).read())
        if manifest.get('format')!=FORMAT or not isinstance(manifest.get('files'),list):
            raise ValueError('unsupported application manifest')
        if not re.fullmatch('camera-experience-[0-9]{3}',str(manifest.get('version',''))):
            raise ValueError('invalid application version')
        expected_names={'manifest.json'}
        seen=set()
        for entry in manifest['files']:
            name=entry['path']
            expected_mode=0o755 if name.startswith('opt/hasslefree/bin/') else 0o644
            if (not allowed(name) or name in seen or entry.get('mode')!=expected_mode
                    or type(entry.get('bytes')) is not int or not 0<=entry['bytes']<=MAX_TOTAL
                    or not re.fullmatch('[0-9a-f]{64}',entry.get('sha256',''))):
                raise ValueError('invalid application file entry')
            seen.add(name);expected_names.add('payload/'+name)
            member=bundle.getmember('payload/'+name)
            if member.size!=entry['bytes']:raise ValueError('application length mismatch')
            raw=bundle.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('application file hash mismatch')
            destination=incoming/name;destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('xb') as stream:stream.write(raw)
        if names!=expected_names:raise ValueError('unlisted application member')
    return incoming,manifest


def write_file(path,source,mode=0o644):
    missing=[];current=path.parent
    while not current.exists():
        missing.append(current);current=current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        os.chmod(directory,0o755)
        sync_directory(directory.parent)
    temp=path.with_name(path.name+'.camera-update.tmp')
    try:
        with temp.open('xb') as destination,source.open('rb') as original:
            shutil.copyfileobj(original,destination,1024*1024)
            os.chmod(temp,mode)
            destination.flush();os.fsync(destination.fileno())
        os.replace(temp,path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def install_files(root,incoming,manifest,work):
    """Prepare one complete immutable release, then select it atomically.

    Only the stable entrypoint and small selector replace existing files. A
    hard interruption at any individual file write leaves the previous app
    usable; the selector is published only after every new file is verified.
    """
    version=manifest['version']
    if not re.fullmatch('camera-experience-[0-9]{3}',version):raise ValueError('invalid release identity')
    release=root/'opt/hasslefree/releases'/version
    if release.exists() or release.is_symlink():raise ValueError('choose a fresh application version')
    backups={}
    service_entries={name:relative for name,relative in SERVICE_ENTRIES.items()
                     if any(e['path']=='opt/hasslefree/'+relative for e in manifest['files'])}
    for name in (ENTRY_PATH,ACTIVE_PATH,*service_entries):
        path=target(root,name)
        backups[name]=None
        if path.is_file():
            info=path.stat()
            backup=work/'backup'/name;backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,backup)
            backups[name]={'sha256':sha(path),'mode':stat.S_IMODE(info.st_mode)}
    receipt={'format':FORMAT,'version':manifest['version'],'status':'backed-up',
             'root':str(root),'backups':backups,'installed':manifest['files'],
             'release':str(release),'activation':'complete-version-directory'}
    save(work/'deployment.json',receipt)
    for entry in manifest['files']:
        name=entry['path']
        local=(name[len('opt/hasslefree/'):] if name.startswith('opt/hasslefree/')
               else 'support/camera-app-installer.py')
        path=release/local
        if any(parent.is_symlink() for parent in (path,*path.parents)):
            raise ValueError('redirected release destination')
        write_file(path,incoming/entry['path'],entry['mode'])
        if sha(path)!=entry['sha256']:raise ValueError('application readback mismatch')
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    manifest_sha=hashlib.sha256(raw).hexdigest()
    save(release/'release-ready.json',{'version':version,'manifest_sha256':manifest_sha},0o644)
    pointer_source=work/'active.json'
    save(pointer_source,{'version':version,'manifest_sha256':manifest_sha})
    bootstrap_source=work/'bootstrap.py';bootstrap_source.write_text(BOOTSTRAP,encoding='utf-8')
    # First installation: old main ignores the new selector until this complete
    # trampoline replaces it. Later installations switch between complete trees.
    # Stable service selectors are prepared before the single activation point.
    # A 003 selector has no service support: keep its exact old service as a
    # hash-bound fallback, so interruption before activation stays usable.
    for name,relative in service_entries.items():
        fallback=root/'opt/hasslefree/service-fallback'/Path(name).name
        original=target(root,name)
        if not fallback.exists() and original.exists():write_file(fallback,original)
        fallback_hash=sha(fallback) if fallback.exists() else ''
        script=BOOTSTRAP.replace("base = Path(__file__).resolve().parents[2]", "base = Path('/opt/hasslefree')")
        script=script.replace("program = release / 'ui/on-device/main.py'", "program = release / "+repr(relative)+"\nif not program.is_file():\n    import hashlib\n    program = base / 'service-fallback' / "+repr(Path(name).name)+"\n    if program.is_symlink() or not program.is_file() or hashlib.sha256(program.read_bytes()).hexdigest() != "+repr(fallback_hash)+":\n        raise SystemExit('Selected service is unavailable')")
        source=work/(Path(name).name+'.selector');source.write_text(script,encoding='utf-8')
        write_file(original,source)
    write_file(target(root,ENTRY_PATH),bootstrap_source)
    write_file(target(root,ACTIVE_PATH),pointer_source)
    receipt['status']='installed'
    save(work/'deployment.json',receipt)
    return receipt


def rollback_files(root,work):
    receipt=json.loads((work/'deployment.json').read_text(encoding='utf-8'))
    if receipt.get('format')!=FORMAT or receipt.get('root')!=str(root):
        raise ValueError('rollback root mismatch')
    # Version trees remain as recovery material. Only entry/selector roll back.
    for name,entry in receipt['backups'].items():
        target(root,name)
        if entry is not None and sha(work/'backup'/name)!=entry['sha256']:
            raise ValueError('rollback backup changed')
    for name,entry in receipt['backups'].items():
        path=target(root,name)
        if entry is None:path.unlink(missing_ok=True)
        else:write_file(path,work/'backup'/name,entry['mode'])
    receipt['status']='rolled-back';save(work/'deployment.json',receipt)
    return receipt


def service(action,name='DaOS-camera'):
    subprocess.run(['/usr/sbin/service',name,action],check=True,
                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)

def restart_usb_later():
    """Return the installer result before disconnecting its authorized USB host."""
    command="import time,subprocess;time.sleep(3);subprocess.run(['/usr/sbin/service','DaOS-usb-adb','restart'],check=True)"
    subprocess.Popen(['/usr/local/bin/python3.12','-c',command],stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)


def wait_ready(log_offset,version):
    path=Path('/var/log/DaOS-camera.log');deadline=time.monotonic()+40
    ready_pid=None
    health_pid=None
    while time.monotonic()<deadline:
        if path.exists():
            with path.open('r',errors='replace') as stream:
                stream.seek(min(log_offset,path.stat().st_size))
                text=stream.read(1024*1024)
            for line in text.splitlines():
                if 'DaOS_APP_HEALTH ' in line:
                    try:health=json.loads(line.split('DaOS_APP_HEALTH ',1)[1])
                    except ValueError:continue
                    if (health.get('format')=='DaOS-camera-app-health-v1' and health.get('version')==version and health.get('backend')=='native'
                            and health.get('authority_ready') is True and health.get('worker_ready') is True and type(health.get('pid')) is int):health_pid=health['pid']
                if 'DaOS_GUI_EVENT ' not in line:continue
                try:event=json.loads(line.split('DaOS_GUI_EVENT ',1)[1])
                except ValueError:continue
                if event.get('event')=='ui-ready':ready_pid=event.get('pid')
                if event.get('event')=='session-failed':raise RuntimeError('new camera session failed')
            if type(ready_pid) is int and ready_pid>1 and ready_pid==health_pid:
                os.kill(ready_pid,0)
                return ready_pid
        time.sleep(.2)
    raise RuntimeError('new camera UI did not report readiness')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',nargs='?');parser.add_argument('--sha256')
    parser.add_argument('--tag',default='camera-experience-001')
    parser.add_argument('--rollback',action='store_true')
    parser.add_argument('--stage-root',type=Path)
    parser.add_argument('--work-dir',type=Path)
    args=parser.parse_args()
    if not re.fullmatch('camera-experience-[0-9]{3}',args.tag):raise ValueError('invalid update tag')
    live=args.stage_root is None
    if live and (not sys.platform.startswith('freebsd') or os.geteuid()!=0):
        raise ValueError('live application updates require native FreeBSD root')
    root=Path('/') if live else args.stage_root.resolve(strict=True)
    if not live and root==Path(root.anchor):raise ValueError('stage root must be a scratch directory')
    work=(args.work_dir or (Path('/var/DaOS/updates')/args.tag)).absolute()
    if work.is_symlink():raise ValueError('redirected update work directory')
    if args.rollback:
        if live:service('stop');service('stop','DaOS-system')
        if live:subprocess.run(['/sbin/mount','-u','-w','/'],check=True)
        try:receipt=rollback_files(root,work)
        finally:
            if live:subprocess.run(['/sbin/mount','-u','-r','/'],check=True)
            if live:service('start','DaOS-system');service('start');restart_usb_later()
        print(json.dumps({'status':receipt['status']}));return
    if not args.archive or not args.sha256:parser.error('archive and --sha256 are required')
    work.mkdir(mode=0o700,parents=True)
    incoming,manifest=unpack(Path(args.archive),args.sha256,work)
    for entry in manifest['files']:target(root,entry['path'])
    log=Path('/var/log/DaOS-camera.log')
    writable=stopped=healthy=False
    has_system=any(e['path']=='opt/hasslefree/support/DaOS-system-service.py' for e in manifest['files'])
    try:
        if live:
            service('stop');stopped=True
            if has_system:service('stop','DaOS-system')
        offset=log.stat().st_size if live and log.exists() else 0
        if live:
            subprocess.run(['/sbin/mount','-u','-w','/'],check=True);writable=True
        receipt=install_files(root,incoming,manifest,work)
        if live:
            subprocess.run(['/sbin/mount','-u','-r','/'],check=True);writable=False
            if has_system:service('start','DaOS-system')
            service('start');receipt['ready_pid']=wait_ready(offset,manifest['version'])
        receipt['status']='ready' if live else 'staged-not-booted'
        save(work/'deployment.json',receipt)
        healthy=True
    except BaseException:
        if (work/'deployment.json').exists():
            if live:
                try:service('stop')
                except Exception:pass
                if has_system:
                    try:service('stop','DaOS-system')
                    except Exception:pass
                if not writable:subprocess.run(['/sbin/mount','-u','-w','/'],check=True);writable=True
            rollback_files(root,work)
        raise
    finally:
        if live and writable:subprocess.run(['/sbin/mount','-u','-r','/'],check=True)
        # Pre-install failures (including mount/backup ENOSPC) have no receipt,
        # but the original service still needs to be started again.
        if live and stopped and not healthy:
            if has_system:service('start','DaOS-system')
            service('start')
    if live and has_system:restart_usb_later()
    print(json.dumps({'status':receipt['status'],'receipt':str(work/'deployment.json')}))


if __name__=='__main__':main()

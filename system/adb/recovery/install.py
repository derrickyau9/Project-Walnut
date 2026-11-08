#!/usr/local/bin/python3.12
"""Hash-bound early USB recovery installer; file copies only, no raw disks.

plan/apply require --bundle DIRECTORY --sha256 SHA256_OF_manifest.json.
Scratch roots are the default. The real / requires --live and FreeBSD root.
Rollback uses the original receipt/backups, never the currently installed init
as a new baseline. Loader activation is published after every payload file.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import time

FORMAT='DaOS-usb-recovery-bundle-v1'
VERSION='usb-recovery-001'
RECEIPT='DaOS-usb-recovery-install-v1'
HOOK='/usr/local/libexec/DaOS-recovery/early-init.sh'
LOADER='boot/loader.conf.local'
REQUIRED={'sbin/init','usr/local/libexec/DaOS-recovery/early-init.sh',
          'usr/local/libexec/DaOS-recovery/early_adb.py',
          'usr/local/libexec/DaOS-recovery/early_daemon.py',
          'etc/daos/recovery-adb.json',
          'usr/local/etc/rc.d/DaOS-usb-adb',
          'usr/local/libexec/DaOS-usb-adb-session.py'}
FIXED=REQUIRED|{'etc/daos/recovery-adb.json','usr/local/libexec/DaOS-recovery/kernel-watchdog'}
MAX_FILE=256*1024**2
MAX_TOTAL=768*1024**2
IMMUTABLE_FLAGS=getattr(stat,'UF_IMMUTABLE',0)|getattr(stat,'SF_IMMUTABLE',0)


def digest(data):return hashlib.sha256(data).hexdigest()


def sha(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def relative(name):
    if not isinstance(name,str) or not name or '\\' in name:
        raise ValueError('Invalid relative path')
    p=PurePosixPath(name)
    if p.is_absolute() or p.as_posix()!=name or any(v in ('..','.') for v in p.parts):
        raise ValueError('Invalid relative path')
    return name


def unredirected(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Redirected path: '+str(path))
    # Canonical lexical normalization is essential for the real-root guard:
    # /tmp/.. is /, not a private scratch root. Check links before collapsing.
    return Path(os.path.abspath(path))


def target(root,name):
    relative(name)
    path=unredirected(root/name)
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():raise ValueError('A parent is not a directory')
    if path.exists() and not path.is_file():raise ValueError('Destination is not a regular file')
    return path


def allowed(name):
    if name in FIXED:return True
    if re.fullmatch(r'usr/local/libexec/DaOS-recovery/[A-Za-z0-9_-]+\.(?:py|sh)',name):return True
    return name.startswith('boot/DaOS-recovery/') and len(name)>len('boot/DaOS-recovery/')


def static_arm64(data):
    if len(data)<64 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
        raise ValueError('Patched init must be an ARM64 ELF')
    offset=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
    if not count or size<56 or offset+size*count>len(data):raise ValueError('Invalid init ELF program table')
    if any(struct.unpack_from('<I',data,offset+size*i)[0] in (2,3) for i in range(count)):
        raise ValueError('Patched init must be statically linked')


def load_bundle(bundle,expected):
    bundle=unredirected(bundle)
    if not bundle.is_dir() or not re.fullmatch('[0-9a-f]{64}',expected or ''):
        raise ValueError('A bundle directory and SHA256 manifest binding are required')
    manifest_path=unredirected(bundle/'manifest.json')
    raw=manifest_path.read_bytes()
    if digest(raw)!=expected:raise ValueError('Bundle manifest SHA256 mismatch')
    manifest=json.loads(raw)
    if manifest.get('format')!=FORMAT or manifest.get('version')!=VERSION:
        raise ValueError('Unsupported recovery bundle identity')
    if not re.fullmatch('[0-9a-f]{64}',manifest.get('base_init_sha256','')):
        raise ValueError('Original init SHA256 is required')
    names=set();payload=[];total=0
    for entry in manifest.get('files',[]):
        if set(entry)!={'source','path','bytes','sha256','mode'}:raise ValueError('Invalid bundle entry')
        name=relative(entry['path']);source=relative(entry['source'])
        if name in names or not allowed(name):raise ValueError('Unexpected/duplicate recovery destination')
        names.add(name)
        if type(entry['bytes']) is not int or not 0<=entry['bytes']<=MAX_FILE or entry['mode'] not in (0o644,0o755):
            raise ValueError('Invalid payload length/mode')
        total+=entry['bytes']
        if total>MAX_TOTAL:raise ValueError('Recovery payload exceeds size bound')
        p=unredirected(bundle/source)
        if not p.is_file() or p.stat().st_size!=entry['bytes'] or sha(p)!=entry['sha256']:
            raise ValueError('Bundle source SHA256/length mismatch')
        if name=='sbin/init':static_arm64(p.read_bytes())
        payload.append(dict(entry))
    if not REQUIRED<=names:raise ValueError('Incomplete early recovery bundle')
    if next(e for e in payload if e['path']=='sbin/init')['mode']!=0o755:
        raise ValueError('Patched init must be executable')
    return bundle,manifest,payload


def activate_loader(data):
    text=data.decode('utf-8')
    matches=re.findall(r'^\s*init_script\s*=\s*[\'\"]?([^\'\"\n]+)',text,re.M)
    if any(value.strip()!=HOOK for value in matches):
        raise ValueError('Existing init_script must be reconciled before recovery activation')
    lines=[line for line in text.splitlines() if not re.match(r'^\s*init_script\s*=',line)]
    return ('\n'.join(lines)+'\n# DaOS authenticated early USB recovery\ninit_script="'+HOOK+'"\n').encode()


def file_state(path):
    if not path.exists():return None
    info=path.stat()
    return {'sha256':sha(path),'mode':stat.S_IMODE(info.st_mode),'flags':getattr(info,'st_flags',0),
            'uid':getattr(info,'st_uid',0),'gid':getattr(info,'st_gid',0)}


def make_plan(root,bundle,expected):
    bundle,manifest,payload=load_bundle(bundle,expected)
    original=target(root,'sbin/init')
    if not original.is_file() or sha(original)!=manifest['base_init_sha256']:
        raise ValueError('Installed original init does not match bundle baseline')
    entries=[]
    for entry in payload:
        p=target(root,entry['path'])
        before=file_state(p)
        entries.append({'path':entry['path'],'before':before,
                        'after':{'sha256':entry['sha256'],'mode':entry['mode'],'flags':before['flags'] if before else 0},'source':entry['source']})
    loader=target(root,LOADER)
    old=loader.read_bytes() if loader.exists() else b''
    new=activate_loader(old)
    entries.append({'path':LOADER,'before':file_state(loader),
                    'after':{'sha256':digest(new),'mode':stat.S_IMODE(loader.stat().st_mode) if loader.exists() else 0o644,
                             'flags':getattr(loader.stat(),'st_flags',0) if loader.exists() else 0},
                    'generated':new.decode()})
    # The init binary is installed before hooks; loader activation stays last.
    entries.sort(key=lambda e:(2 if e['path']==LOADER else 0 if e['path']=='sbin/init' else 1,e['path']))
    ram=unredirected(root/'DaOS-recovery-ram')
    if ram.exists() and not ram.is_dir():raise ValueError('Recovery RAM mountpoint is not a directory')
    return {'format':RECEIPT,'version':VERSION,'root':str(root),'bundle':str(bundle),
            'manifest_sha256':expected,'base_init_sha256':manifest['base_init_sha256'],
            'status':'planned','files':entries,'created_directories':[],
            'ram_directory_existed':ram.exists(),'physical_raw_disk_writes':False}


def atomic(path,data,mode=0o644,owner=None,flags=None):
    temporary=path.with_name(path.name+'.recovery-tmp')
    if temporary.exists() or temporary.is_symlink():raise ValueError('Stale temporary file requires inspection')
    made=False;cleared=False;replaced=False;original_flags=0
    try:
        with temporary.open('xb') as f:
            made=True;f.write(data);os.chmod(temporary,mode)
            if owner is not None and hasattr(os,'chown'):os.chown(temporary,*owner)
            f.flush();os.fsync(f.fileno())
        # FreeBSD installs /sbin/init with schg. Prepare and fsync the complete
        # replacement before opening the tiny flags-clear/rename window. Clear
        # only immutable bits, never append/nodump or other original metadata.
        if path.exists():
            info=path.stat();original_flags=getattr(info,'st_flags',0)
            if original_flags&IMMUTABLE_FLAGS:
                if info.st_nlink!=1:raise ValueError('Refusing immutable multi-link replacement')
                os.chflags(path,original_flags&~IMMUTABLE_FLAGS)
                cleared=True
        os.replace(temporary,path)
        replaced=True
        restore_flags=original_flags if flags is None else flags
        if hasattr(os,'chflags'):os.chflags(path,restore_flags)
        if os.name!='nt':
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:
        # A denied clear (e.g. securelevel) leaves the original untouched.
        # A rename failure after a successful clear restores its full flags.
        if cleared and not replaced:os.chflags(path,original_flags)
        if made:temporary.unlink(missing_ok=True)


def save(work,record):atomic(work/'deployment.json',(json.dumps(record,indent=2)+'\n').encode(),0o600)


def assert_state(path,expected):
    state=file_state(path)
    if expected is None:
        if state is not None:raise ValueError('Destination was created outside this operation')
    elif state is None or state['sha256']!=expected['sha256'] or state['mode']!=expected['mode'] or ('flags' in expected and state['flags']!=expected['flags']):
        raise ValueError('Destination changed outside this operation: '+str(path))


def ensure_parents(root,path,record):
    missing=[];parent=path.parent
    while parent!=root and not parent.exists():missing.append(parent);parent=parent.parent
    for item in reversed(missing):
        item.mkdir(mode=0o755)
        record['created_directories'].append(item.relative_to(root).as_posix())


def restore_files(root,work,record):
    # Preflight every destination/backup before restoring any byte. A normal
    # rollback may see before or after on a file if installation was interrupted.
    for entry in record['files']:
        p=target(root,entry['path']);state=file_state(p)
        acceptable=[v for v in (entry['before'],entry['after']) if v is not None]
        if state is None:
            if entry['before'] is not None:raise ValueError('Missing installed file: '+entry['path'])
        elif not any(state['sha256']==v['sha256'] and state['mode']==v['mode'] and ('flags' not in v or state['flags']==v['flags']) for v in acceptable):
            raise ValueError('Refusing to overwrite externally changed recovery file: '+entry['path'])
        if entry['before'] is not None:
            backup=unredirected(work/'backup'/entry['path'])
            if not backup.is_file() or sha(backup)!=entry['before']['sha256']:
                raise ValueError('Original rollback backup changed')
    # Selector goes back first, then file contents in reverse publication order.
    for entry in reversed(record['files']):
        p=target(root,entry['path']);before=entry['before']
        if before is None:p.unlink(missing_ok=True)
        else:
            atomic(p,(work/'backup'/entry['path']).read_bytes(),before['mode'],
                   (before['uid'],before['gid']) if os.name!='nt' else None,before.get('flags'))
    for name in reversed(record['created_directories']):
        p=unredirected(root/relative(name))
        try:p.rmdir()
        except OSError:pass # Existing/new external content must be preserved.
    if not record['ram_directory_existed']:
        try:unredirected(root/'DaOS-recovery-ram').rmdir()
        except OSError:pass


def apply(root,bundle,expected,work,writer=atomic):
    work=unredirected(work)
    if work.exists():raise ValueError('Recovery work directory already exists; use its rollback receipt')
    record=make_plan(root,bundle,expected)
    work.mkdir(parents=True,mode=0o700)
    record['started_at']=time.time();record['status']='backing-up'
    for entry in record['files']:
        if entry['before'] is not None:
            backup=work/'backup'/entry['path'];backup.parent.mkdir(parents=True,exist_ok=True)
            atomic(backup,target(root,entry['path']).read_bytes(),0o600)
            if sha(backup)!=entry['before']['sha256']:raise ValueError('Backup mismatch')
    save(work,record)
    try:
        record['status']='applying';save(work,record)
        ram=root/'DaOS-recovery-ram'
        if not ram.exists():ram.mkdir(mode=0o755)
        for entry in record['files']:
            p=target(root,entry['path']);assert_state(p,entry['before'])
            ensure_parents(root,p,record)
            # Persist new parent ownership before the file can be published.
            save(work,record)
            data=entry['generated'].encode() if 'generated' in entry else unredirected(Path(record['bundle'])/entry['source']).read_bytes()
            if digest(data)!=entry['after']['sha256']:raise ValueError('Payload changed after planning')
            writer(p,data,entry['after']['mode'])
            assert_state(p,entry['after'])
            entry['installed']=True;save(work,record)
        record['status']='installed-reboot-required';record['completed_at']=time.time();save(work,record)
        return record
    except BaseException as error:
        record['error']=str(error)
        try:
            restore_files(root,work,record)
            record['status']='failed-rolled-back'
        except BaseException as rollback_error:
            record['status']='failed-rollback-refused';record['rollback_error']=str(rollback_error)
        save(work,record)
        raise


def rollback(root,work):
    work=unredirected(work);p=unredirected(work/'deployment.json')
    record=json.loads(p.read_text())
    if record.get('format')!=RECEIPT or record.get('version')!=VERSION or record.get('root')!=str(root):
        raise ValueError('Unexpected rollback receipt identity')
    names=[e['path'] for e in record['files']]
    if len(names)!=len(set(names)) or not REQUIRED<=set(names) or names[-1]!=LOADER:
        raise ValueError('Invalid rollback destination list')
    if any(not allowed(relative(n)) and n!=LOADER for n in names):raise ValueError('Rollback destination escaped recovery scope')
    if record.get('status') not in ('applying','installed-reboot-required','failed-rollback-refused','failed-rolled-back'):
        raise ValueError('No applicable recovery installation')
    restore_files(root,work,record)
    record['status']='rolled-back';record['rolled_back_at']=time.time();save(work,record)
    return record


@contextmanager
def writable_root(root,live):
    readonly=live and bool(os.statvfs(root).f_flag & getattr(os,'ST_RDONLY',1))
    if readonly:subprocess.run(['/sbin/mount','-u','-w','/'],check=True,timeout=30)
    try:yield
    finally:
        if readonly:subprocess.run(['/sbin/mount','-u','-r','/'],check=True,timeout=30)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','apply','rollback'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--work',type=Path)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args();root=unredirected(args.root)
    if not root.is_dir():raise ValueError('Existing root directory required')
    if root==Path(root.anchor):
        if not args.live or root!=Path('/') or not sys.platform.startswith('freebsd') or os.geteuid()!=0:
            raise ValueError('The actual root requires --live and native FreeBSD uid0')
    elif args.live:raise ValueError('--live is only valid for the actual / root')
    work=unredirected(args.work or root/'var/DaOS/updates'/VERSION)
    if args.action in ('plan','apply'):
        if args.bundle is None:parser.error('plan/apply requires --bundle and --sha256')
        # Full read-only preflight before a live root remount or any work writes.
        record=make_plan(root,args.bundle,args.sha256)
        if args.action=='plan':print(json.dumps(record,indent=2));return 0
    with writable_root(root,args.live):
        record=rollback(root,work) if args.action=='rollback' else apply(root,args.bundle,args.sha256,work)
    print(json.dumps({'status':record['status'],'work':str(work),'reboot_performed':False,'raw_disks_opened':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())

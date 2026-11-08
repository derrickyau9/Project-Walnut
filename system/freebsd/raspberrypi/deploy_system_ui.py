#!/usr/local/bin/python3.12
"""Install a hash-bound UI/boot payload, with local backups and rollback."""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

BOOT_VALUES={'loader_brand':'daos','loader_logo':'none','loader_gfx':'NO',
             'loader_menu_title':'Welcome to DaOS','boot_mute':'YES'}
RC_VALUES={'DaOS_boot_enable':'YES','DaOS_system_enable':'YES','rc_startmsgs':'NO'}
PREFIXES=('opt/hasslefree/ui/on-device/','usr/local/libexec/','usr/local/etc/rc.d/',
          'usr/share/DaOS/boot/','boot/lua/','etc/rc.d/')

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def unpack(archive,expected,work):
    if sha(archive)!=expected:raise ValueError('Payload hash mismatch')
    incoming=work/'incoming';incoming.mkdir()
    with tarfile.open(archive) as t:
        members=t.getmembers()
        if len(members)>512 or sum(m.size for m in members)>32*1024**2:raise ValueError('Payload exceeds bounds')
        for m in members:
            p=PurePosixPath(m.name)
            if not m.isfile() or p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive member')
        t.extractall(incoming,filter='data')
    manifest=json.loads((incoming/'manifest.json').read_text())
    for entry in manifest['files']:
        rel=entry['path'];p=PurePosixPath(rel)
        if p.is_absolute() or '..' in p.parts or not rel.startswith(PREFIXES):raise ValueError('Unsafe install path')
        source=incoming/'payload'/rel
        if source.is_symlink() or not source.is_file() or sha(source)!=entry['sha256']:raise ValueError('Payload file mismatch')
        if entry['mode'] not in (0o644,0o755):raise ValueError('Unsupported install mode')
    for entry in manifest.get('removals',[]):
        if entry['path']!='usr/local/etc/rc.d/DaOS-boot' or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):raise ValueError('Unsupported cleanup path')
    return incoming,manifest

def edit_conf(path,values):
    text=path.read_text() if path.exists() else ''
    lines=[line for line in text.splitlines() if not any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in values)]
    return ('\n'.join(lines)+'\n# DaOS boot and settings\n'+''.join(k+'="'+v+'"\n' for k,v in values.items())).encode()

def targets(manifest):return [e['path'] for e in manifest['files']+manifest.get('removals',[])]+['boot/loader.conf','etc/rc.conf']

def apply(root,incoming,manifest,tag):
    records={}
    for entry in manifest.get('removals',[]):
        old=root/entry['path']
        if old.exists() and (old.is_symlink() or sha(old)!=entry['sha256']):raise ValueError('Prior boot service differs from the owned version')
    for entry in manifest['files']+[
            {'path':'boot/loader.conf','mode':0o644}, {'path':'etc/rc.conf','mode':0o644}]:
        rel=entry['path'];dst=root/rel
        if any(p.is_symlink() for p in (dst,*dst.parents)):raise ValueError('Redirected system path')
        before=sha(dst) if dst.is_file() else None
        if rel=='boot/loader.conf':data=edit_conf(dst,BOOT_VALUES)
        elif rel=='etc/rc.conf':data=edit_conf(dst,RC_VALUES)
        else:data=(incoming/'payload'/rel).read_bytes()
        dst.parent.mkdir(parents=True,exist_ok=True)
        # New public payload directories must be traversable by UID 1000 even
        # when the privileged updater inherits the service's private umask.
        base=root/('opt/hasslefree/ui/on-device' if rel.startswith('opt/hasslefree/ui/on-device/') else 'usr/share/DaOS' if rel.startswith('usr/share/DaOS/') else str(PurePosixPath(rel).parent))
        current=dst.parent
        while current!=base.parent and current.is_relative_to(base):
            os.chmod(current,0o755);os.chown(current,0,0)
            current=current.parent
        temp=dst.with_name(dst.name+'.'+tag+'.tmp')
        with temp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chown(temp,0,0);os.chmod(temp,entry['mode']);os.replace(temp,dst)
        records[rel]={'before':before,'after':sha(dst)}
    for entry in manifest.get('removals',[]):
        old=root/entry['path']
        if old.exists():records[entry['path']]={'before':sha(old),'after':None};old.unlink()
    return records

def main():
    p=argparse.ArgumentParser()
    p.add_argument('archive');p.add_argument('sha256');p.add_argument('--tag',default='system-ui-001')
    p.add_argument('--rollback',action='store_true')
    p.add_argument('--files-only',action='store_true',help='only when running application files already match')
    args=p.parse_args()
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native root shell required')
    if not re.fullmatch('system-ui-[0-9]{3}',args.tag):raise ValueError('Invalid update identity')
    work=Path('/var/DaOS/updates')/args.tag
    if args.rollback:
        record=json.loads((work/'deployment.json').read_text())
        subprocess.run(['/usr/sbin/service','DaOS-camera','stop'],capture_output=True,timeout=45)
        subprocess.run(['/usr/sbin/service','DaOS-system','stop'],capture_output=True,timeout=15)
        subprocess.run(['/sbin/mount','-u','-w','/'],check=True,capture_output=True)
        for rel,entry in record['backups'].items():
            dst=Path('/')/rel
            if entry is None:dst.unlink(missing_ok=True)
            else:shutil.copy2(work/'backup'/rel,dst)
        subprocess.run(['/sbin/mount','-u','-r','/'],check=True,capture_output=True)
        subprocess.run(['/usr/sbin/service','DaOS-camera','start'],check=True,capture_output=True,timeout=20)
        print('Original UI and boot configuration restored')
        return
    work.mkdir(mode=0o700)
    incoming,manifest=unpack(Path(args.archive),args.sha256,work)
    if args.files_only:
        for entry in manifest['files']:
            if entry['path'].startswith(('opt/','usr/local/libexec/')):
                if sha(Path('/')/entry['path'])!=entry['sha256']:raise ValueError('Running code changed; normal service restart required')
    backup=work/'backup';backup.mkdir()
    backups={}
    for rel in targets(manifest):
        src=Path('/')/rel
        if src.is_symlink():raise ValueError('Redirected destination')
        backups[rel]=sha(src) if src.is_file() else None
        if src.is_file():
            dst=backup/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    record={'format':'DaOS-system-ui-deployment-v1','status':'backed-up','backups':backups,
            'payload_sha256':args.sha256,'photos_or_camera_settings_changed':False}
    receipt=work/'deployment.json';receipt.write_text(json.dumps(record,indent=2)+'\n')
    try:
        if not args.files_only:subprocess.run(['/usr/sbin/service','DaOS-camera','stop'],check=True,capture_output=True,timeout=45)
        if not args.files_only and Path('/var/run/DaOS-system-supervisor.pid').is_file():
            subprocess.run(['/usr/sbin/service','DaOS-system','stop'],check=True,capture_output=True,timeout=20)
        subprocess.run(['/sbin/mount','-u','-w','/'],check=True,capture_output=True)
        record['installed']=apply(Path('/'),incoming,manifest,args.tag)
        subprocess.run(['/sbin/mount','-u','-r','/'],check=True,capture_output=True)
        if not args.files_only:
            subprocess.run(['/usr/sbin/service','DaOS-system','start'],check=True,capture_output=True,timeout=20)
            subprocess.run(['/usr/sbin/service','DaOS-camera','start'],check=True,capture_output=True,timeout=20)
        record['status']='files-updated-running-code-unchanged' if args.files_only else 'installed-services-started'
    except BaseException as error:
        record.update(status='failed',error=type(error).__name__)
        receipt.write_text(json.dumps(record,indent=2)+'\n')
        print('Installation stopped; saved backups permit --rollback',file=sys.stderr)
        raise
    receipt.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':record['status'],'receipt':str(receipt)}))

if __name__=='__main__':main()

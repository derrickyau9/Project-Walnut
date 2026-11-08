#!/usr/local/bin/python3.12
"""Publish optional automatic FAT rescue failover after complete verified staging.

Scratch plan/apply: --root DIR --efi DIR. Actual device: --root / --live.
No EEPROM/OTP or raw-device writes. --enable-watchdog is explicit and last.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import boot_config
import install as early

FORMAT='DaOS-auto-failover-bundle-v1'
RECEIPT='DaOS-auto-failover-install-v1'
MANIFEST='failover-manifest.json'
POLICY='etc/daos/recovery-watchdog.enabled'
CLEAN='etc/rc.d/DaOS-rescue-clean'
SELECTOR='boot.scr'
MARKER='DaOS-recovery/normal.running'
REQUIRED={'DaOS-recovery/loader.efi','DaOS-recovery/loader.lua','DaOS-recovery/root.ufs.gz',
          'DaOS-recovery/kernel/kernel','DaOS-recovery/kernel/usb_template.ko','DaOS-recovery/kernel/daos_adb_template.ko'}
POLICY_DATA=b'DaOS-recovery-watchdog-v1\n'


def sha(path):return early.sha(path)
def digest(data):return hashlib.sha256(data).hexdigest()


def payload_path(name):
    early.relative(name)
    if name in REQUIRED:return True
    return bool(re.fullmatch(r'DaOS-recovery/kernel/[A-Za-z0-9_.-]+',name))


def create_manifest(bundle,identity,primary_efi_sha256,primary_config_sha256,stable_kernel_sha256):
    """Packaging helper: creates no live files and never includes global EFI config."""
    bundle=early.unredirected(bundle)
    files=[]
    for p in sorted((bundle/'DaOS-recovery').rglob('*')):
        if p.is_symlink():raise ValueError('Redirected bundle payload')
        if not p.is_file():continue
        name=p.relative_to(bundle).as_posix()
        if not payload_path(name):continue
        files.append({'path':name,'source':name,'bytes':p.stat().st_size,'sha256':sha(p),'mode':0o644})
    value={'format':FORMAT,'identity':identity,'primary_efi_sha256':primary_efi_sha256,
           'primary_config_sha256':primary_config_sha256,'stable_kernel_sha256':stable_kernel_sha256,'files':files}
    if not REQUIRED<={e['path'] for e in files}:raise ValueError('Incomplete independent rescue boot payload')
    path=bundle/MANIFEST
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2);f.write('\n')
    return digest(path.read_bytes())


def load_bundle(bundle,expected):
    bundle=early.unredirected(bundle)
    if not re.fullmatch('[a-f0-9]{64}',expected or ''):raise ValueError('Bound manifest SHA256 required')
    raw=early.target(bundle,MANIFEST).read_bytes()
    if digest(raw)!=expected:raise ValueError('Failover manifest changed')
    m=json.loads(raw)
    if m.get('format')!=FORMAT:raise ValueError('Wrong failover bundle format')
    identity=m.get('identity',{})
    if identity.get('format')!='DaOS-recovery-identity-v1' or not re.fullmatch(r'/dev/(?:mmcsd|sdda)[0-9]+',identity.get('disk','')):
        raise ValueError('Fixed bound SD identity required')
    if not re.fullmatch('[a-f0-9-]{36}',identity.get('disk_uuid','')):raise ValueError('Fixed GPT disk UUID required')
    for key in ('primary_efi_sha256','primary_config_sha256','stable_kernel_sha256'):
        if not re.fullmatch('[a-f0-9]{64}',m.get(key,'')):raise ValueError('Bound primary boot identities required')
    names=set();total=0
    for e in m.get('files',[]):
        if set(e)!={'path','source','bytes','sha256','mode'} or not payload_path(e['path']) or e['path'] in names:
            raise ValueError('Unexpected or duplicate FAT payload destination')
        names.add(e['path']);early.relative(e['source'])
        if type(e['bytes']) is not int or not 0<e['bytes']<=256*1024**2 or e['mode']!=0o644:
            raise ValueError('Invalid FAT payload size/mode')
        total+=e['bytes'];p=early.target(bundle,e['source'])
        if not p.is_file() or p.stat().st_size!=e['bytes'] or sha(p)!=e['sha256']:raise ValueError('FAT payload changed')
    if not REQUIRED<=names or total>512*1024**2:raise ValueError('Incomplete or oversized FAT payload')
    return bundle,m


def state(path):
    if not path.exists():return None
    return {'sha256':sha(path),'mode':stat.S_IMODE(path.stat().st_mode)}


def fat_atomic(path,data,mode=0o644):
    """msdosfs rejects fsync(directory); fsync the file, rename, then sync FAT."""
    path=early.unredirected(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
        if os.name!='nt':
            fd=os.open(path.parent,os.O_RDONLY)
            try:
                try:os.fsync(fd)
                except OSError as error:
                    if error.errno not in (errno.EINVAL,errno.EOPNOTSUPP):raise
                    # Do not reinterpret EINVAL on UFS or other filesystems.
                    rows=[line.split() for line in command(['/sbin/mount','-p']).splitlines()]
                    candidates=[row for row in rows if len(row)==6 and (str(path.parent)==row[1] or str(path.parent).startswith(row[1].rstrip('/')+'/'))]
                    if not candidates or max(candidates,key=lambda row:len(row[1]))[2]!='msdosfs':raise
                    os.sync()
            finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)


def matches(path,wanted,area):
    actual=state(path)
    if actual is None or wanted is None:return actual==wanted
    return actual['sha256']==wanted['sha256'] and (area=='efi' or actual['mode']==wanted['mode'])


def make_plan(root,efi,bundle,expected,enable_watchdog=False):
    root=early.unredirected(root);efi=early.unredirected(efi);bundle,m=load_bundle(bundle,expected)
    if root==efi:raise ValueError('Separate root and FAT staging trees required')
    for base,name,key in ((root,'boot/kernel/kernel','stable_kernel_sha256'),(efi,'EFI/BOOT/BOOTAA64.EFI','primary_efi_sha256'),(efi,'config.txt','primary_config_sha256')):
        if sha(early.target(base,name))!=m[key]:raise ValueError('Installed primary boot identity differs: '+name)
    if not early.target(root,'boot/nextboot.conf').read_bytes().startswith(b'nextboot_enable="NO"'):
        raise ValueError('Clear the one-shot trial before activation')
    hook=early.target(root,'usr/local/libexec/DaOS-recovery/early-init.sh')
    if not hook.is_file():raise ValueError('Install authenticated early recovery first')
    text=early.target(root,'boot/loader.conf.local').read_text()
    if not re.search(r'^\s*init_script="/usr/local/libexec/DaOS-recovery/early-init.sh"\s*$',text,re.M):
        raise ValueError('Early recovery init hook is not active')
    if enable_watchdog and not early.target(root,'usr/local/libexec/DaOS-recovery/kernel-watchdog').is_file():
        raise ValueError('Install the native watchdog binary before enabling it')
    entries=[]
    def add(area,name,mode,*,source=None,data=None):
        p=early.target(root if area=='root' else efi,name)
        value={'area':area,'path':name,'before':state(p),'mode':mode}
        if source is not None:value['source']=source;value['after_sha256']=sha(early.target(bundle,source))
        else:value['data_hex']=data.hex();value['after_sha256']=digest(data)
        entries.append(value)
    for e in m['files']:add('efi',e['path'],e['mode'],source=e['source'])
    if shutil.disk_usage(efi).free<sum((e['bytes']+4095)//4096*4096 for e in m['files'])+4*1024**2:
        raise ValueError('Insufficient FAT space for verified rescue staging')
    add('root',CLEAN,0o755,data=boot_config.CLEAN_SHUTDOWN.encode())
    mark=early.target(efi,MARKER)
    if mark.exists():
        if mark.read_bytes() not in (b'\0',b'\1'):raise ValueError('Malformed existing boot marker')
    else:add('efi',MARKER,0o644,data=b'\0')
    # Activation preserves firmware config and does not assume an unused pin or
    # configured pull-up. The separately generated GPIO selector stays optional.
    add('efi',SELECTOR,0o644,data=boot_config.script_image(boot_config.armed_failover(gpio_override=False)))
    if enable_watchdog:add('root',POLICY,0o444,data=POLICY_DATA)
    return {'format':RECEIPT,'root':str(root),'efi':str(efi),'manifest_sha256':expected,'bundle':str(bundle),
            'disk_uuid':m['identity']['disk_uuid'],'files':entries,'created_directories':[],'status':'planned',
            'watchdog_requested':enable_watchdog,'otp_modified':False,'raw_disk_writes':False,'reboot_performed':False,
            'unexpected_power_loss_enters_rescue':True}


def save(work,record):early.atomic(work/'deployment.json',(json.dumps(record,indent=2)+'\n').encode(),0o600)


def destination(root,efi,e):return early.target(root if e['area']=='root' else efi,e['path'])


def ensure_parents(root,efi,path,area,record):
    base=root if area=='root' else efi
    missing=[];p=path.parent
    while p!=base and not p.exists():missing.append(p);p=p.parent
    for p in reversed(missing):p.mkdir(mode=0o755);record['created_directories'].append({'area':area,'path':p.relative_to(base).as_posix()})


def restore(root,efi,work,r):
    # Validate the entire transaction before restoring even the selector.
    for e in r['files']:
        p=destination(root,efi,e);after={'sha256':e['after_sha256'],'mode':e['mode']}
        if not matches(p,e['before'],e['area']) and not matches(p,after,e['area']):raise ValueError('Refusing externally changed rollback destination')
        if e['before'] is not None and sha(early.target(work/'backup'/e['area'],e['path']))!=e['before']['sha256']:
            raise ValueError('Failover rollback backup changed')
    ordered=sorted(r['files'],key=lambda e:(0 if e['area']=='efi' and e['path']==SELECTOR else 1 if e['path']==POLICY else 2))
    for e in ordered:
        p=destination(root,efi,e)
        if matches(p,e['before'],e['area']):continue
        if e['before'] is None:p.unlink()
        else:(fat_atomic if e['area']=='efi' else early.atomic)(p,early.target(work/'backup'/e['area'],e['path']).read_bytes(),e['before']['mode'])
    for e in reversed(r['created_directories']):
        p=early.unredirected((root if e['area']=='root' else efi)/e['path'])
        try:p.rmdir()
        except OSError:pass


def apply(root,efi,bundle,expected,work,enable_watchdog=False,writer=None):
    root=early.unredirected(root);efi=early.unredirected(efi);work=early.unredirected(work)
    r=make_plan(root,efi,bundle,expected,enable_watchdog)
    if work.exists():raise ValueError('Fresh failover work directory required')
    work.mkdir(parents=True,mode=0o700)
    for e in r['files']:
        if e['before'] is not None:
            p=work/'backup'/e['area']/e['path'];p.parent.mkdir(parents=True,exist_ok=True)
            early.atomic(p,destination(root,efi,e).read_bytes(),0o600)
    save(work,r)
    try:
        mountpoint=early.unredirected(root/'DaOS-recovery-efi')
        if mountpoint.exists() and not mountpoint.is_dir():raise ValueError('FAT shutdown mountpoint is not a directory')
        if not mountpoint.exists():mountpoint.mkdir(mode=0o755);r['created_directories'].append({'area':'root','path':'DaOS-recovery-efi'})
        for e in r['files']:
            p=destination(root,efi,e)
            if not matches(p,e['before'],e['area']):raise ValueError('Destination changed after planning')
            ensure_parents(root,efi,p,e['area'],r);save(work,r)
            data=bytes.fromhex(e['data_hex']) if 'data_hex' in e else early.target(Path(r['bundle']),e['source']).read_bytes()
            if digest(data)!=e['after_sha256']:raise ValueError('Payload changed during installation')
            (writer or (fat_atomic if e['area']=='efi' else early.atomic))(p,data,e['mode'])
            if not matches(p,{'sha256':e['after_sha256'],'mode':e['mode']},e['area']):raise ValueError('Failover write readback failed')
            save(work,r)
        r['status']='installed-reboot-required';save(work,r);return r
    except BaseException:
        try:restore(root,efi,work,r);r['status']='failed-rolled-back'
        except BaseException as error:r['status']='failed-rollback-refused';r['rollback_error']=str(error)
        save(work,r);raise


def rollback(root,efi,work):
    root=early.unredirected(root);efi=early.unredirected(efi);work=early.unredirected(work)
    r=json.loads(early.target(work,'deployment.json').read_text())
    if r.get('format')!=RECEIPT or r.get('root')!=str(root) or r.get('efi')!=str(efi):raise ValueError('Wrong failover rollback identity')
    seen=set()
    for e in r.get('files',[]):
        area,name=e.get('area'),e.get('path');early.relative(name)
        if (area,name) in seen or area not in ('root','efi'):raise ValueError('Invalid rollback path')
        seen.add((area,name))
        if area=='root' and name not in (CLEAN,POLICY) or area=='efi' and name not in (SELECTOR,MARKER) and not payload_path(name):raise ValueError('Rollback escaped failover scope')
    if ('efi',SELECTOR) not in seen:raise ValueError('Missing selector backup')
    for d in r.get('created_directories',[]):
        early.relative(d['path'])
        if d['area'] not in ('root','efi'):raise ValueError('Invalid rollback directory')
    restore(root,efi,work,r);r['status']='rolled-back';save(work,r);return r


def command(args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,timeout=20).strip()


def require_efi_unmounted(identity,label_text,mount_text):
    """Resolve GEOM aliases before opening a second writable view of the ESP."""
    aliases={}
    for line in label_text.splitlines():
        fields=line.split()
        if len(fields)!=3:raise ValueError('Unexpected GEOM label table')
        name='/dev/'+fields[0];provider='/dev/'+fields[2]
        if name in aliases:raise ValueError('Duplicate GEOM label identity')
        aliases[name]=provider
    def resolve(source):
        seen=set()
        while source in aliases:
            if source in seen:raise ValueError('GEOM alias cycle')
            seen.add(source);source=aliases[source]
        return source
    disk=identity['disk'];provider=disk+'p1'
    if resolve('/dev/gpt/DaOS-efi')!=provider:
        raise ValueError('DaOS EFI label does not resolve to the bound SD')
    for line in mount_text.splitlines():
        fields=line.split()
        if len(fields)!=6:raise ValueError('Unexpected mount table')
        if resolve(fields[0]) in (provider,disk):
            raise ValueError('Bound EFI provider is already mounted; refusing another FAT mount')


def native_identity(identity):
    if command(['/sbin/sysctl','-n','kern.bootfile'])!='/boot/kernel/kernel':raise ValueError('Activation requires the stable kernel')
    import flash
    disk=identity['disk'];size=identity['disk_bytes'];sector=identity['sector_size']
    if int(command(['/usr/sbin/diskinfo',disk]).split()[2])!=size:raise ValueError('Physical SD size changed')
    fd=os.open(disk,os.O_RDONLY|os.O_NOFOLLOW)
    try:uuid,parts=flash.parse_gpt(fd,size,sector)
    finally:os.close(fd)
    if uuid!=identity['disk_uuid']:raise ValueError('GPT disk identity changed')
    if parts.get(1,{}).get('label')!='DaOS-efi' or parts[1]['type']!='c12a7328-f81f-11d2-ba4b-00a0c93ec93b':
        raise ValueError('Bound first partition is not the DaOS EFI system partition')
    for name,part in identity['partitions'].items():
        actual=parts[part['index']]
        if actual['uuid']!=part['uuid'] or actual['start_lba']!=part['start_lba'] or actual['sectors']!=part['sectors']:raise ValueError('Bound SD partition changed')
    require_efi_unmounted(identity,command(['/sbin/geom','label','status','-s']),command(['/sbin/mount','-p']))


@contextmanager
def mounted_efi(identity,writable):
    native_identity(identity)
    with tempfile.TemporaryDirectory(prefix='DaOS-failover-',dir='/tmp') as folder:
        p=Path(folder)
        subprocess.run(['/sbin/mount','-t','msdosfs','-o','rw' if writable else 'ro','/dev/gpt/DaOS-efi',str(p)],check=True,timeout=30)
        try:yield p
        finally:subprocess.run(['/sbin/umount',str(p)],check=True,timeout=30)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('plan','apply','rollback'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--efi',type=Path);p.add_argument('--live',action='store_true')
    p.add_argument('--bundle',type=Path);p.add_argument('--sha256');p.add_argument('--work',type=Path);p.add_argument('--enable-watchdog',action='store_true')
    a=p.parse_args();root=early.unredirected(a.root)
    if a.live:
        if root!=Path('/') or not sys.platform.startswith('freebsd') or os.geteuid()!=0 or a.efi is not None:raise ValueError('--live requires native FreeBSD root and internally mounted EFI')
    elif root==Path(root.anchor) or a.efi is None:raise ValueError('Scratch mode requires private --root and --efi directories')
    work=early.unredirected(a.work or root/'var/DaOS/updates/usb-failover-001')
    if a.bundle is None: p.error('All actions require the bound --bundle and --sha256')
    _,manifest=load_bundle(a.bundle,a.sha256)
    def execute(efi):
        # Live EFI mount paths are temporary; receipt uses the fixed provider token.
        if a.action=='plan':return make_plan(root,efi,a.bundle,a.sha256,a.enable_watchdog)
        with early.writable_root(root,a.live):
            if a.action=='rollback':
                if a.live:
                    record=json.loads((work/'deployment.json').read_text())
                    if record.get('manifest_sha256')!=a.sha256 or record.get('disk_uuid')!=manifest['identity']['disk_uuid']:
                        raise ValueError('Rollback manifest/card binding changed')
                    record['efi']=str(efi);save(work,record)
                return rollback(root,efi,work)
            return apply(root,efi,a.bundle,a.sha256,work,a.enable_watchdog)
    if a.live:
        with mounted_efi(manifest['identity'],a.action!='plan') as efi:result=execute(efi)
    else:result=execute(early.unredirected(a.efi))
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())

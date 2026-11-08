#!/usr/bin/env python3
"""Prepare an isolated factory-image derivative without accessing a camera card.

The original archive is immutable input. Preparation mounts only autoallocated
vnode devices backed by the new image. It neither copies a live card nor imports
the card-bound recovery identity or watchdog policy.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid

SOURCE_SHA='9ab7335f1752331270fe9cf451e31085f89063e58460adb5627d88f2f6f3a859'
RAW_SHA='1788ed68190e78bfe76b1f2ca56d6904883dfada800064312101dae386e160db'
RAW_BYTES=3254820864
WORK_PARENT=Path('/var/DaOS004-work')
EXPECTED_LABELS=('DaOS-efi','DaOS-root','DaOS-state','DaOS-calibration','DaOS-photos')
CORE_PATHS={'sbin/init','usr/local/libexec/DaOS-recovery/early-init.sh',
    'usr/local/libexec/DaOS-recovery/early-init-base.sh',
    'usr/local/libexec/DaOS-recovery/early_adb.py','usr/local/libexec/DaOS-recovery/early_daemon.py',
    'etc/daos/recovery-adb.json','usr/local/etc/rc.d/DaOS-usb-adb','usr/local/libexec/DaOS-usb-adb-session.py'}
CORE_INIT='7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427'
OLD_INIT='70621ffd991e433ca1cbc177c4d7355f5ab16253e2f28a9da1307f22b8d5feae'
HELPER='d6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa'
OLD_HELPER='fab7cc1ffbbfd0875ef740df655771368d3da4b2d7cd5a4178903caa01bdfafd'
KERNEL='5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec'
LEGACY_PROFILE={'format':'DaOS-factory-input-profile-v1','source_xz_sha256':SOURCE_SHA,
    'source_raw_sha256':RAW_SHA,'raw_bytes':RAW_BYTES,'source_core_preinstalled':False,
    'application_version':'camera-experience-005','image_basename':'DaOS-Factory-Preview-001.img',
    'export_basename':'DaOS-Factory-Preview-001-arm64.img.xz','project_name':'DaOS Camera','tagline':''}
WALNUT_PROFILE={'format':'DaOS-factory-input-profile-v1',
    'source_xz_sha256':'d3f9272d04c37993eaf2e53a54dd09ce2c4e0b8f8b5bb10174106bd12e249ee6',
    'source_raw_sha256':'3d094085f22e119586e46f80cff9b992f0c02c6296fa39b99155efc3282e2dd4',
    'raw_bytes':RAW_BYTES,'source_core_preinstalled':True,'application_version':'camera-experience-006',
    'image_basename':'Project-Walnut-v0.1.0-preview.2-arm64.img',
    'export_basename':'Project-Walnut-v0.1.0-preview.2-arm64.img.xz',
    'project_name':'Project Walnut','tagline':'They made the leaf. We cracked the nut.'}

def input_profile(args):
    path=getattr(args,'input_profile',None);expected=getattr(args,'input_profile_sha256',None)
    if path is None:
        if expected is not None:raise ValueError('Input profile file and SHA256 must be supplied together')
        return dict(LEGACY_PROFILE)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8192 or not re.fullmatch('[0-9a-f]{64}',expected or ''):
        raise ValueError('Bound input profile required')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Input profile SHA256 changed')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate input profile field')
            result[key]=value
        return result
    profile=json.loads(raw,object_pairs_hook=unique)
    if (profile!=WALNUT_PROFILE or type(profile.get('source_core_preinstalled')) is not bool
            or type(profile.get('raw_bytes')) is not int):
        raise ValueError('Only the approved hash-bound Walnut preview derivation is supported')
    return profile

def profile_sha(profile):
    return hashlib.sha256(json.dumps(profile,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def sync_directory(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def publish_metadata(path,value,history,expected_before):
    """Keep predecessor bytes before publishing one current metadata file."""
    if path.is_symlink() or path.exists() and not path.is_file():raise ValueError('Ordinary metadata file required')
    before=path.read_bytes() if path.exists() else None
    if before!=expected_before:raise ValueError('Predecessor metadata changed')
    if before is not None:
        if history.is_symlink() or history.exists() and not history.is_dir():raise ValueError('Ordinary metadata history directory required')
        history.mkdir(exist_ok=True)
        backup=history/(path.stem+'-'+hashlib.sha256(before).hexdigest()+'.json')
        if backup.exists() or backup.is_symlink():
            if backup.is_symlink() or not backup.is_file() or backup.read_bytes()!=before:raise ValueError('Metadata history changed')
        else:
            with backup.open('xb') as output:output.write(before);output.flush();os.fsync(output.fileno())
        sync_directory(history)
    raw=(json.dumps(value,indent=2)+'\n').encode()
    candidate=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with candidate.open('xb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
        os.chmod(candidate,0o644)
        if (path.read_bytes() if path.exists() else None)!=before:raise ValueError('Metadata changed before publication')
        os.replace(candidate,path);sync_directory(path.parent)
        if path.read_bytes()!=raw:raise ValueError('Published metadata readback failed')
    finally:candidate.unlink(missing_ok=True)
    return {'previous_sha256':None if before is None else hashlib.sha256(before).hexdigest(),
            'current_sha256':hashlib.sha256(raw).hexdigest(),
            'history_file':None if before is None else backup.name}

def inherited_core(root,profile):
    """Read only: exact full-source pins are checked before this mounted view."""
    if not profile['source_core_preinstalled']:return None
    names=('factory-preview.json','factory-preview-app-manifest.json');history={}
    for name in names:
        path=root/'usr/share/DaOS'/name
        if path.is_symlink() or not path.is_file():raise ValueError('Validated predecessor metadata required')
        history[name]={'sha256':sha(path),'document':json.loads(path.read_text())}
    metadata=history[names[0]]['document'];manifest=history[names[1]]['document']
    if metadata.get('format')!='DaOS-factory-preview-profile-v1' or metadata.get('application_version')!='camera-experience-005' or manifest.get('version')!='camera-experience-005':
        raise ValueError('Validated predecessor application identity changed')
    expected=metadata.get('core_files',{})
    if set(expected)!=CORE_PATHS|{'usr/local/libexec/daos-usb-adb','boot/kernel/kernel','etc/fstab'}:
        raise ValueError('Inherited core inventory changed')
    actual={name:sha(root/name) for name in expected}
    if actual!=expected or actual['sbin/init']!=CORE_INIT or actual['usr/local/libexec/daos-usb-adb']!=HELPER or actual['boot/kernel/kernel']!=KERNEL:
        raise ValueError('Inherited core verification failed')
    for name in ('etc/daos/recovery-identity.json','etc/daos/recovery-watchdog.enabled','usr/local/libexec/DaOS-recovery/flash.py'):
        if (root/name).exists() or (root/name).is_symlink():raise ValueError('Card-bound recovery artifact in inherited core')
    if not (root/'sbin/init').stat().st_flags&stat.SF_IMMUTABLE:raise ValueError('Inherited immutable init flag changed')
    if any(not (root/'etc'/name).is_symlink() or os.readlink(root/'etc'/name)!='/DaOS-identity/'+name for name in ('hostid','machine-id')):
        raise ValueError('Inherited volatile identity links changed')
    return {'core_files':actual,'predecessor_metadata':history}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()

def run(command,timeout=60,check=True):
    p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    result={'command':command,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    if check and p.returncode:raise RuntimeError(json.dumps(result))
    return result

def save(path,value):
    with path.open('x',encoding='utf-8') as target:
        json.dump(value,target,indent=2);target.write('\n');target.flush();os.fsync(target.fileno())

def path_info(root,name):
    path=root/name
    if not path.exists() and not path.is_symlink():return {'exists':False}
    info=path.lstat()
    result={'exists':True,'mode':oct(stat.S_IMODE(info.st_mode)),'uid':info.st_uid,'gid':info.st_gid,
            'flags':info.st_flags,'bytes':info.st_size,'inode':info.st_ino,
            'kind':'symlink' if stat.S_ISLNK(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'other'}
    if stat.S_ISLNK(info.st_mode):result['link']=os.readlink(path)
    if stat.S_ISREG(info.st_mode):result['sha256']=sha(path)
    return result

def empty_mountpoint(path):
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):raise ValueError('Owned mountpoint must be an empty directory')
    else:path.mkdir()

def release_owned(md,targets,work,phase):
    """Discover mount effects before cleanup; never force-detach a busy vnode."""
    if md is None:return
    active_error=sys.exc_info()[1]
    receipt={'format':'DaOS-factory-owned-vnode-cleanup-v1','phase':phase,'vnode':md,'unmounts':[],'errors':[]}
    safe=True
    try:
        for provider,target in reversed(targets):
            table=run(['/sbin/mount','-p'])['stdout']
            found=[line.split() for line in table.splitlines() if len(line.split())==6 and line.split()[1]==str(target)]
            if not found:continue
            if len(found)!=1 or found[0][0]!=provider or found[0][2]!='ufs':
                safe=False;receipt['errors'].append('Unexpected provider at owned mountpoint '+str(target));continue
            result=run(['/sbin/umount',str(target)],check=False);receipt['unmounts'].append(result)
            table=run(['/sbin/mount','-p'])['stdout']
            if any(len(line.split())==6 and line.split()[1]==str(target) for line in table.splitlines()):
                safe=False;receipt['errors'].append('Owned mountpoint remains mounted '+str(target))
        if safe:
            receipt['detach']=run(['/sbin/mdconfig','-d','-u',md],check=False)
            if receipt['detach']['returncode']:receipt['errors'].append('Owned vnode detach failed')
    except BaseException as error:
        receipt['errors'].append(type(error).__name__+': '+str(error))
    receipt['released']=not receipt['errors']
    for number in range(1,100):
        report=work/(phase+'-cleanup-'+str(number).zfill(3)+'.json')
        if not report.exists():save(report,receipt);break
    else:raise ValueError('Cleanup report count exceeded')
    if receipt['errors']:
        message='Owned factory vnode cleanup incomplete; inspect '+str(report)
        if active_error is not None:active_error.add_note(message)
        else:raise RuntimeError(message)

def inspect(root):
    names=('var','etc/hostid','etc/machine-id','etc/fstab','etc/rc.conf','boot/loader.conf',
        'boot/loader.conf.local','boot/nextboot.conf','sbin/init','boot/kernel/kernel',
        'usr/local/libexec/daos-usb-adb','etc/daos/adb_keys','etc/daos/usb-adb.json',
        'etc/daos/recovery-identity.json','etc/daos/recovery-watchdog.enabled','DaOS-recovery-ram','DaOS-recovery-efi')
    result={'paths':{name:path_info(root,name) for name in names},'configuration':{},'key_paths':[]}
    for name in ('etc/fstab','etc/rc.conf','boot/loader.conf','boot/loader.conf.local','boot/nextboot.conf'):
        path=root/name
        if path.is_file() and not path.is_symlink():result['configuration'][name]=path.read_text()
    for directory in ('etc/ssh','root/.ssh','home/camera/.ssh','etc/daos'):
        base=root/directory
        if base.exists():
            for path in sorted(base.rglob('*')):
                if path.is_file() and not path.is_symlink() and re.search(r'key|identity|authorized',path.name,re.I):
                    name=path.relative_to(root).as_posix();entry={'path':name,**path_info(root,name)}
                    raw=path.read_bytes()
                    entry['private_pem']=bool(re.search(rb'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----',raw))
                    entry['authorized_public_key_rows']=len([line for line in raw.splitlines() if line.strip() and not line.startswith(b'#')]) if path.name in ('adb_keys','authorized_keys') else None
                    result['key_paths'].append(entry)
    for name in ('etc/master.passwd','etc/ssh/sshd_config'):
        path=root/name
        if path.is_file():
            raw=path.read_text()
            if name.endswith('master.passwd'):
                result['account_password_states']={row.split(':')[0]:('locked' if row.split(':')[1].startswith(('*','!')) else 'empty' if not row.split(':')[1] else 'hash-present') for row in raw.splitlines() if ':' in row and row.split(':')[0] in ('root','camera')}
            else:result['sshd_config_sha256']=sha(path)
    return result

def privacy_scan(root):
    findings=[];files=0
    pattern=re.compile(rb'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----')
    complete=re.compile(rb'-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY)-----\r?\n([^\x00]{1,32768}?)-----END \1-----')
    for parent,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(parent)/name).is_symlink()]
        for name in names:
            path=Path(parent)/name
            if not stat.S_ISREG(path.lstat().st_mode):continue
            files+=1;found=False;tail=b'';processed=0;complete_offsets=set()
            with path.open('rb') as stream:
                for part in iter(lambda:stream.read(1024**2),b''):
                    data=tail+part
                    if pattern.search(data):found=True
                    for match in complete.finditer(data):
                        lines=[line.strip() for line in match[2].splitlines() if line.strip() and not line.startswith((b'Proc-Type:',b'DEK-Info:'))]
                        try:decoded=base64.b64decode(b''.join(lines),validate=True)
                        except ValueError:continue
                        if len(decoded)>=32:complete_offsets.add(processed-len(tail)+match.start())
                    processed+=len(part);tail=data[-65536:]
            if found:findings.append({'path':path.relative_to(root).as_posix(),'sha256':sha(path),'bytes':path.stat().st_size,
                'complete_base64_private_key_blocks':len(complete_offsets)})
    return {'files_scanned':files,'private_key_pem_files':findings,'secret_contents_exported':False,
        'scope':'PEM marker detection in this factory filesystem only; public test fixture classification requires independent provenance.'}

def outside_root(image):
    result=[]
    for offset,length in ((0,134238208),(2818592768,RAW_BYTES-2818592768)):
        h=hashlib.sha256()
        with image.open('rb') as stream:
            stream.seek(offset);remaining=length
            while remaining:
                part=stream.read(min(1024**2,remaining))
                if not part:raise ValueError('Truncated full image')
                h.update(part);remaining-=len(part)
        result.append({'offset':offset,'bytes':length,'sha256':h.hexdigest()})
    return result

def owned_work(args):
    profile=input_profile(args)
    work=args.work
    if work.parent!=WORK_PARENT or WORK_PARENT.resolve(strict=True)!=WORK_PARENT or work.is_symlink() or work.resolve(strict=True)!=work:
        raise ValueError('Existing owned work directory required')
    bound=work/'input-profile.json'
    if bound.exists():
        if bound.is_symlink() or json.loads(bound.read_text())!=profile:raise ValueError('Work input profile changed')
    elif profile!=LEGACY_PROFILE:raise ValueError('Prepared input profile required')
    image=work/profile['image_basename']
    if image.is_symlink() or not image.is_file() or image.stat().st_size!=RAW_BYTES:raise ValueError('Unexpected full image')
    return work,image

def update_core(args):
    if input_profile(args)['source_core_preinstalled']:raise ValueError('Validated source core is inherited during prepare; do not reinstall it')
    work,image=owned_work(args)
    if sha(image)!=RAW_SHA:raise ValueError('Core installation requires exact original factory copy')
    if not args.core_inputs or not re.fullmatch('[0-9a-f]{64}',args.core_sha or ''):raise ValueError('Bound generic core inputs required')
    archive=args.core_inputs
    if archive.is_symlink() or not archive.is_file() or sha(archive)!=args.core_sha:raise ValueError('Core input archive mismatch')
    incoming=work/'generic-core-inputs';incoming.mkdir(mode=0o700)
    with tarfile.open(archive,'r:gz') as bundle:
        total=0;names=set()
        for member in bundle.getmembers():
            parts=Path(member.name).parts
            if not member.isfile() or member.name.startswith('/') or '..' in parts or '\\' in member.name or member.name in names:
                raise ValueError('Unexpected generic input archive member')
            names.add(member.name);total+=member.size
            if len(names)>20 or member.size>2*1024**2 or total>4*1024**2:raise ValueError('Generic core inputs exceed bound')
            target=incoming/member.name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as out:
                out.write(bundle.extractfile(member).read());out.flush();os.fsync(out.fileno())
    bindings=json.loads((incoming/'inputs.json').read_text())
    if set(names)!={'inputs.json',*bindings['files']}:raise ValueError('Generic input archive inventory mismatch')
    for name,entry in bindings['files'].items():
        if sha(incoming/name)!=entry['sha256'] or (incoming/name).stat().st_size!=entry['bytes']:raise ValueError('Generic input binding mismatch')
    manifest=json.loads((incoming/'bundle/manifest.json').read_text())
    if manifest.get('base_init_sha256')!=OLD_INIT or {entry['path'] for entry in manifest['files']}!=CORE_PATHS:
        raise ValueError('Generic core must contain only the early USB files and factory identity wrapper')
    if next(entry['sha256'] for entry in manifest['files'] if entry['path']=='sbin/init')!=CORE_INIT or sha(incoming/'helper')!=HELPER:
        raise ValueError('Generic core binary mismatch')
    spec=importlib.util.spec_from_file_location('factory_early_usb_installer',incoming/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    manifest_sha=sha(incoming/'bundle/manifest.json')
    installer.load_bundle(incoming/'bundle',manifest_sha)
    before_ranges=outside_root(image);record={'format':'DaOS-factory-preview-generic-core-v1','status':'preparing',
        'core_archive_sha256':args.core_sha,'manifest_sha256':manifest_sha,'input_bindings':bindings,
        'source_image_sha256':RAW_SHA,'outside_root_before':before_ranges,'physical_card_accessed':False,
        'automatic_recovery_enrolled':False,'watchdog_policy_included':False,'disk_identity_included':False,
        'known_limitations':['Factory developer public-key enrollment is retained for this private preview.',
            'hostid_save may warn because this preview retains the read-only factory identity layout.',
            'No physical boot of this derived image has been performed.']}
    md=None;mounted=False;root=work/'root';empty_mountpoint(root)
    try:
        md=run(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)])['stdout'].strip()
        if not re.fullmatch(r'md[0-9]+',md):raise ValueError('Unexpected autoallocated vnode name')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        if sha(root/'sbin/init')!=OLD_INIT or sha(root/'usr/local/libexec/daos-usb-adb')!=OLD_HELPER or sha(root/'boot/kernel/kernel')!=KERNEL:
            raise ValueError('Factory core baseline changed')
        for name in ('etc/daos/recovery-identity.json','etc/daos/recovery-watchdog.enabled','usr/local/libexec/DaOS-recovery/flash.py'):
            if (root/name).exists():raise ValueError('Unexpected card-bound recovery artifact')
        record['before']=inspect(root)
        transaction=installer.apply(root,incoming/'bundle',manifest_sha,work/'generic-core-transaction')
        installer.atomic(root/'usr/local/libexec/daos-usb-adb',(incoming/'helper').read_bytes(),0o755,(0,0),0)
        record['transaction_status']=transaction['status']
        record['after']=inspect(root)
        for entry in manifest['files']:
            if sha(root/entry['path'])!=entry['sha256']:raise ValueError('Installed early USB file mismatch')
        if sha(root/'boot/kernel/kernel')!=KERNEL or sha(root/'usr/local/libexec/daos-usb-adb')!=HELPER:raise ValueError('Installed generic core verification failed')
        if not (root/'sbin/init').stat().st_flags&stat.SF_IMMUTABLE:raise ValueError('Patched init immutable flag was lost')
        if (root/'etc/fstab').read_bytes()!=record['before']['configuration']['etc/fstab'].encode():raise ValueError('Factory fstab changed')
        if record['after']['paths']['var']['kind']!='directory':raise ValueError('Factory /var mountpoint changed')
        identity=root/'DaOS-identity'
        if identity.exists() or identity.is_symlink():raise ValueError('Factory identity path unexpectedly exists')
        identity.mkdir(mode=0o755)
        for name in ('hostid','machine-id'):
            path=root/'etc'/name
            if path.exists() or path.is_symlink():raise ValueError('Factory identity file unexpectedly exists')
            path.symlink_to('/DaOS-identity/'+name)
        record['identity_layout']={'mountpoint':'/DaOS-identity','mode':'0755','filesystem':'dedicated volatile tmpfs',
            'hostid_link':os.readlink(root/'etc/hostid'),'machine_id_link':os.readlink(root/'etc/machine-id'),
            'private_usb_state_mode':'0700','kernel_and_rc_sources_unchanged':True,'boot_validation_pending':True}
        record['after']=inspect(root)
        record['status']='generic-core-installed-app-pending'
    finally:
        release_owned(md,[('/dev/'+str(md)+'p2',root)],work,'core')
    record['outside_root_after']=outside_root(image)
    if record['outside_root_after']!=before_ranges:raise ValueError('Bytes outside owned root partition changed')
    record['image_sha256']=sha(image)
    save(work/'generic-core.json',record)
    print(json.dumps(record),flush=True)

def qa_export(args):
    profile=input_profile(args)
    work,image=owned_work(args)
    final=args.action=='qa-final'
    core=json.loads((work/('application.json' if final else 'generic-core.json')).read_text())
    if sha(image)!=core['image_sha256']:raise ValueError('QA input changed')
    prefix='final' if final else 'core'
    qa_name=prefix+'-boot-qa.img' if profile==LEGACY_PROFILE else profile['image_basename'][:-4]+'-'+prefix+'-boot-qa.img'
    target=work/qa_name
    if target.exists() or target.is_symlink():raise ValueError('New QA copy required')
    with image.open('rb') as original,target.open('xb') as copied:
        shutil.copyfileobj(original,copied,1024**2);copied.flush();os.fsync(copied.fileno())
    md=None;mounted=False;root=work/'root';empty_mountpoint(root)
    record={'format':'DaOS-factory-core-boot-QA-image-v1','candidate_sha256':core['image_sha256'],
        'console_changes_are_qa_copy_only':True,'physical_hardware_tested':False,'core_files':{},
        'input_profile':profile,'input_profile_sha256':profile_sha(profile)}
    try:
        md=run(['/sbin/mdconfig','-a','-t','vnode','-f',str(target)])['stdout'].strip()
        if not re.fullmatch(r'md[0-9]+',md):raise ValueError('Unexpected autoallocated vnode name')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        for name in sorted(CORE_PATHS|{'usr/local/libexec/daos-usb-adb'}):record['core_files'][name]=sha(root/name)
        if final:
            record['application']={'version':core['version'],'archive_sha256':core['application_archive_sha256'],
                'manifest_file_sha256':sha(root/'usr/share/DaOS/factory-preview-app-manifest.json')}
        path=root/'boot/loader.conf.local'
        before=path.read_bytes();extra=b'\n# Private virtual boot QA only\nconsole="comconsole"\nboot_single="YES"\nboot_mute="NO"\nautoboot_delay="0"\n'
        with path.open('wb') as output:output.write(before+extra);output.flush();os.fsync(output.fileno())
        record['qa_loader_before_sha256']=hashlib.sha256(before).hexdigest()
        record['qa_loader_after_sha256']=sha(path)
        run(['/sbin/umount',str(root)]);mounted=False
        record['filesystem_checks']=[run(['/sbin/fsck_msdosfs','-M','-n','/dev/'+md+'p1'],120)]
        record['filesystem_checks'] += [run(['/sbin/fsck_ffs','-n','/dev/'+md+'p'+str(index)],120) for index in range(2,6)]
    finally:
        release_owned(md,[('/dev/'+str(md)+'p2',root)],work,'qa-export-'+prefix)
    if sha(image)!=core['image_sha256'] or outside_root(target)!=core['outside_root_after']:raise ValueError('Candidate or non-root QA bytes changed')
    compressed=Path(str(target)+'.gz')
    with compressed.open('xb') as output:
        subprocess.run(['/usr/bin/gzip','-1','-c',str(target)],stdout=output,check=True,timeout=240)
        output.flush();os.fsync(output.fileno())
    record.update(raw_sha256=sha(target),raw_bytes=target.stat().st_size,gzip_sha256=sha(compressed),gzip_bytes=compressed.stat().st_size,gzip_file=compressed.name,
        input_candidate_unchanged=True,outside_root_unchanged=True)
    save(work/(prefix+'-boot-qa.json'),record)
    print(json.dumps(record),flush=True)

def load_bound_module(path,expected,name):
    if path is None or path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}',expected or '') or sha(path)!=expected:
        raise ValueError('Bound native build helper required')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def elf_closure(root,inspector):
    entries=[];search=set();config_sources=[]
    for prefix in ('usr/local','opt/hasslefree'):
        for parent,dirs,names in os.walk(root/prefix,followlinks=False):
            dirs[:]=[name for name in dirs if not (Path(parent)/name).is_symlink()]
            for name in names:
                path=Path(parent)/name;info=path.lstat();relative=path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):entries.append({'path':relative,'type':'link','link':os.readlink(path)})
                elif stat.S_ISREG(info.st_mode) and inspector.parse_elf(path) is not None:
                    entries.append({'path':relative,'type':'file','bytes':info.st_size,'sha256':sha(path)})
    configs=root/'usr/local/libdata/ldconfig'
    if configs.is_dir() and not configs.is_symlink():
        for path in sorted(configs.iterdir()):
            if path.is_file() and not path.is_symlink():
                lines=path.read_text().splitlines()
                for line in lines:
                    value=line.split('#',1)[0].strip()
                    if value:
                        if not value.startswith('/') or any(c.isspace() for c in value) or '..' in Path(value).parts:raise ValueError('Unexpected ldconfig path')
                        search.add(value)
                config_sources.append({'path':path.relative_to(root).as_posix(),'sha256':sha(path)})
    result=inspector.verify_closure(root,entries,inspector.base_manifest(root,'arm64'),architecture='arm64',library_dirs=sorted(search))
    result['ldconfig_configuration_sources']=config_sources
    return result

def update_app(args):
    profile=input_profile(args)
    work,image=owned_work(args)
    core=json.loads((work/'generic-core.json').read_text())
    if sha(image)!=core['image_sha256']:raise ValueError('App installation requires verified generic core')
    installer=load_bound_module(args.app_installer,args.app_installer_sha,'factory_app_installer')
    inspector=load_bound_module(args.elf_inspector,args.elf_inspector_sha,'factory_elf_inspector')
    archive=args.app_archive
    if archive is None or archive.is_symlink() or not archive.is_file() or not re.fullmatch('[0-9a-f]{64}',args.app_sha or '') or sha(archive)!=args.app_sha:
        raise ValueError('Bound bilingual application archive required')
    transaction=work/'app-transaction';transaction.mkdir(mode=0o700)
    incoming,manifest=installer.unpack(archive,args.app_sha,transaction)
    if manifest['version']!=profile['application_version']:raise ValueError('This preview requires its bound bilingual application version')
    locales={entry['path'] for entry in manifest['files'] if '/locales/' in entry['path']}
    if not any(name.endswith('/en_US.qm') for name in locales) or not any(name.endswith('/zh_CN.qm') for name in locales):
        raise ValueError('Both native Qt translation catalogs are required')
    record={'format':'DaOS-factory-preview-application-v1','status':'preparing','version':manifest['version'],
        'input_profile':profile,'input_profile_sha256':profile_sha(profile),
        'source_core_sha256':core['image_sha256'],'application_archive_sha256':args.app_sha,
        'installer_sha256':args.app_installer_sha,'elf_inspector_sha256':args.elf_inspector_sha,
        'physical_card_accessed':False,'physical_board_boot_validated':False,'generic_auto_recovery_enrolled':False,
        'wd_policy_included':False,'application_manifest':manifest}
    before_ranges=outside_root(image);md=None;mounted=False;root=work/'root';empty_mountpoint(root)
    try:
        md=run(['/sbin/mdconfig','-a','-t','vnode','-f',str(image)])['stdout'].strip()
        if not re.fullmatch(r'md[0-9]+',md):raise ValueError('Unexpected autoallocated vnode name')
        run(['/sbin/mount','-t','ufs','/dev/'+md+'p2',str(root)]);mounted=True
        previous_metadata={name:(root/'usr/share/DaOS'/name).read_bytes() if (root/'usr/share/DaOS'/name).is_file() else None
                           for name in ('factory-preview.json','factory-preview-app-manifest.json')}
        if profile['source_core_preinstalled']:
            inherited_core(root,profile)
        before_core={name:sha(root/name) for name in CORE_PATHS|{'usr/local/libexec/daos-usb-adb','boot/kernel/kernel','etc/fstab'}}
        record['installation']=installer.install_files(root,incoming,manifest,transaction)
        release=root/'opt/hasslefree/releases'/manifest['version']
        for entry in manifest['files']:
            relative=entry['path'][len('opt/hasslefree/'):] if entry['path'].startswith('opt/hasslefree/') else 'support/camera-app-installer.py'
            path=release/relative
            if path.is_symlink() or not path.is_file() or sha(path)!=entry['sha256']:raise ValueError('Installed application source correspondence failed')
        record['elf_closure']=elf_closure(root,inspector)
        after_core={name:sha(root/name) for name in before_core}
        expected_service_change='usr/local/libexec/DaOS-usb-adb-session.py'
        if any(value!=after_core[name] for name,value in before_core.items() if name!=expected_service_change):raise ValueError('Application changed an early USB, kernel or filesystem file')
        record['core_files_after']=after_core
        if any((root/name).exists() for name in ('etc/daos/recovery-identity.json','etc/daos/recovery-watchdog.enabled','usr/local/libexec/DaOS-recovery/flash.py')):
            raise ValueError('Card-bound recovery must not enter generic preview')
        metadata={'format':'DaOS-factory-preview-profile-v1','profile':'private-bilingual-preview-normal-usb',
            'source_factory_xz_sha256':profile['source_xz_sha256'],'source_factory_raw_sha256':profile['source_raw_sha256'],
            'input_profile':profile,'input_profile_sha256':profile_sha(profile),
            'project_name':profile['project_name'],'tagline':profile['tagline'],
            'application_archive_sha256':args.app_sha,'application_version':manifest['version'],
            'application_manifest_canonical_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'core_files':after_core,'generic_auto_recovery_enrolled':False,'hardware_watchdog_policy_installed':False,
            'enrollment':'Two existing factory developer public keys retained; private preview requires explicit recipient key enrollment before wider distribution.',
            'data_partitions':'Pristine factory state/calibration/photos; no physical card data copied.',
            'hardware':'Raspberry Pi4,800x480DSI; camera backend HF-Sim; Wi-Fi unavailable.',
            'validation':'Derived image native filesystem/static-ELF validation; virtual init QA is separate; not freshly flashed on a physical board.'}
        record['metadata_publications']={}
        for name,value in (('factory-preview.json',metadata),('factory-preview-app-manifest.json',manifest)):
            record['metadata_publications'][name]=publish_metadata(root/'usr/share/DaOS'/name,value,
                root/'usr/share/DaOS/factory-preview-history',previous_metadata[name])
        record['root_after']=inspect(root)
        record['privacy_after']=privacy_scan(root)
        run(['/sbin/umount',str(root)]);mounted=False
        record['filesystem_checks']=[run(['/sbin/fsck_msdosfs','-M','-n','/dev/'+md+'p1'],120)]
        record['filesystem_checks'] += [run(['/sbin/fsck_ffs','-n','/dev/'+md+'p'+str(index)],120) for index in range(2,6)]
    finally:
        release_owned(md,[('/dev/'+str(md)+'p2',root)],work,'app')
    record['outside_root_after']=outside_root(image)
    if before_ranges!=record['outside_root_after'] or before_ranges!=core['outside_root_after']:raise ValueError('Application changed bytes outside its root partition')
    record.update(status='prepared-not-physically-booted',image_sha256=sha(image),raw_bytes=image.stat().st_size)
    save(work/'application.json',record)
    print(json.dumps(record),flush=True)

def finalize(args):
    profile=input_profile(args)
    work,image=owned_work(args)
    application=json.loads((work/'application.json').read_text())
    if application['status']!='prepared-not-physically-booted' or sha(image)!=application['image_sha256']:
        raise ValueError('Only the verified bilingual factory preview can be exported')
    if outside_root(image)!=application['outside_root_after'] or not application['elf_closure']['passed'] or any(item['returncode'] for item in application['filesystem_checks']):
        raise ValueError('Preview verification is incomplete')
    source=args.source
    if source.is_symlink() or not source.is_file() or sha(source)!=profile['source_xz_sha256']:raise ValueError('Original input archive changed')
    output=work/profile['export_basename']
    with output.open('xb') as compressed:
        subprocess.run(['/usr/bin/xz','-T4','-6','-c',str(image)],stdout=compressed,check=True,timeout=480)
        compressed.flush();os.fsync(compressed.fileno())
    h=hashlib.sha256();length=0
    with lzma.open(output,'rb') as compressed:
        for chunk in iter(lambda:compressed.read(1024**2),b''):
            length+=len(chunk)
            if length>RAW_BYTES:raise ValueError('Final archive decompression exceeded image bound')
            h.update(chunk)
    if length!=RAW_BYTES or h.hexdigest()!=application['image_sha256'] or sha(image)!=application['image_sha256']:
        raise ValueError('Final compressed image roundtrip failed')
    record={'format':'DaOS-factory-preview-export-v1','status':'private-preview-not-physically-flashed',
        'input_profile':profile,'input_profile_sha256':profile_sha(profile),
        'project_name':profile['project_name'],'tagline':profile['tagline'],
        'image':str(output),'compressed_bytes':output.stat().st_size,'compressed_sha256':sha(output),
        'raw_bytes':length,'raw_sha256':h.hexdigest(),'full_decompression_crc_and_sha_verified':True,
        'original_factory_archive_sha256':profile['source_xz_sha256'],'original_factory_archive_unchanged':sha(source)==profile['source_xz_sha256'],
        'application_archive_sha256':application['application_archive_sha256'],'application_version':application['version'],
        'native_elf_closure_passed':True,'filesystem_checks':[item['returncode'] for item in application['filesystem_checks']],
        'FAT_GPT_state_calibration_photos_bytes_unchanged':True,'physical_card_accessed':False,
        'generic_automatic_recovery_enrolled':False,'hardware_watchdog_policy_included':False,
        'authentication':'Two existing developer public keys retained; private preview, recipient enrollment required.',
        'camera':'HF-Sim; physical camera sensor capabilities are unavailable.','wifi':'Unavailable; experimental kernels are excluded.'}
    save(work/'export.json',record)
    print(json.dumps(record),flush=True)

def prepare(args):
    profile=input_profile(args)
    source=args.source.resolve(strict=True)
    if args.source.is_symlink() or not source.is_file() or sha(source)!=profile['source_xz_sha256']:raise ValueError('Original factory archive identity mismatch')
    work=args.work
    if work.parent!=WORK_PARENT or WORK_PARENT.resolve(strict=True)!=WORK_PARENT or work.is_symlink():raise ValueError('Owned work directory required')
    image=work/profile['image_basename']
    if args.action=='prepare':
        if work.exists():raise ValueError('New owned work directory required')
        work.mkdir(mode=0o700)
        h=hashlib.sha256();length=0
        with lzma.open(source,'rb') as compressed,image.open('xb') as raw:
            for chunk in iter(lambda:compressed.read(1024**2),b''):
                length+=len(chunk)
                if length>RAW_BYTES:raise ValueError('Factory decompression size exceeded')
                h.update(chunk);raw.write(chunk)
            raw.flush();os.fsync(raw.fileno())
        if length!=RAW_BYTES or h.hexdigest()!=profile['source_raw_sha256']:raise ValueError('Original raw factory identity mismatch')
        save(work/'input-profile.json',profile)
    elif work.resolve(strict=True)!=work or image.is_symlink() or image.stat().st_size!=RAW_BYTES or sha(image)!=profile['source_raw_sha256']:
        raise ValueError('Retained original raw factory identity mismatch')
    if profile!=LEGACY_PROFILE and (not (work/'input-profile.json').is_file() or json.loads((work/'input-profile.json').read_text())!=profile):
        raise ValueError('Prepared input profile changed')
    record={'format':'DaOS-factory-preview-prepare-v1','source':str(source),'source_sha256':profile['source_xz_sha256'],
        'input_profile':profile,'input_profile_sha256':profile_sha(profile),
        'raw_source_sha256':profile['source_raw_sha256'],'image':str(image),'raw_bytes':RAW_BYTES,'original_archive_unchanged':sha(source)==profile['source_xz_sha256'],
        'physical_card_accessed':False,'card_bound_recovery_imported':False,'watchdog_policy_imported':False}
    md=None;mounted=[];inherited=None
    try:
        md=run(['/sbin/mdconfig','-a','-t','vnode','-o','readonly','-f',str(image)])['stdout'].strip()
        if not re.fullmatch(r'md[0-9]+',md):raise ValueError('Unexpected autoallocated vnode name')
        record['gpt']=run(['/sbin/gpart','list',md])
        actual=re.findall(r'^\s+label: (.*)$',record['gpt']['stdout'],re.M)
        if actual!=list(EXPECTED_LABELS):raise ValueError('Factory GPT layout/labels changed')
        record['filesystem_checks']=[]
        for index in range(1,6):
            command=['/sbin/fsck_msdosfs','-M','-n','/dev/'+md+'p1'] if index==1 else ['/sbin/fsck_ffs','-n','/dev/'+md+'p'+str(index)]
            record['filesystem_checks'].append(run(command,timeout=120))
        root=work/'root';empty_mountpoint(root)
        run(['/sbin/mount','-t','ufs','-o','ro','/dev/'+md+'p2',str(root)]);mounted.append(root)
        record['root']=inspect(root)
        inherited=inherited_core(root,profile)
        if args.action=='audit':
            record['public_boot_sources']={path.relative_to(root).as_posix():path.read_text() for path in sorted((root/'etc/rc.d').glob('hostid*')) if path.is_file()}
            record['public_boot_sources']['etc/defaults/rc.conf-hostid-settings']='\n'.join(line for line in (root/'etc/defaults/rc.conf').read_text().splitlines() if re.search(r'hostid|machine_id',line))
            record['root_privacy_scan']=privacy_scan(root)
        for index,label in ((3,'state'),(4,'calibration'),(5,'photos')):
            target=work/label;empty_mountpoint(target)
            run(['/sbin/mount','-t','ufs','-o','ro','/dev/'+md+'p'+str(index),str(target)]);mounted.append(target)
            record[label+'_top_level']=sorted(path.name for path in target.iterdir())
            # Metadata only: never export private state, image contents or key bytes.
            record[label+'_file_count']=sum(1 for path in target.rglob('*') if path.is_file())
            if args.action=='audit':
                record[label+'_files']=[{'path':path.relative_to(target).as_posix(),**path_info(target,path.relative_to(target))} for path in sorted(target.rglob('*')) if path.is_file() and not path.is_symlink()]
                record[label+'_privacy_scan']=privacy_scan(target)
    finally:
        release_owned(md,[('/dev/'+str(md)+'p'+str(index),work/name) for index,name in ((2,'root'),(3,'state'),(4,'calibration'),(5,'photos'))],work,args.action)
    record['image_unchanged_during_inspection']=sha(image)==profile['source_raw_sha256']
    if not record['image_unchanged_during_inspection']:raise ValueError('Source image changed during inspection')
    if args.action=='prepare' and inherited is not None:
        ranges=outside_root(image)
        save(work/'generic-core.json',{'format':'DaOS-factory-preview-generic-core-v1','status':'inherited-validated-core-app-pending',
            'input_profile':profile,'input_profile_sha256':profile_sha(profile),'image_sha256':profile['source_raw_sha256'],
            'outside_root_before':ranges,'outside_root_after':ranges,'core_inherited_without_writes':True,
            'physical_card_accessed':False,'automatic_recovery_enrolled':False,'watchdog_policy_included':False,
            'disk_identity_included':False,**inherited})
    report=work/('audit.json' if args.action=='audit' else 'prepare.json')
    if report.exists():
        for number in range(2,100):
            candidate=report.with_name(report.stem+'-'+str(number).zfill(3)+'.json')
            if not candidate.exists():report=candidate;break
        else:raise ValueError('Inspection report count exceeded')
    record['report']=str(report)
    save(report,record)
    print(json.dumps(record),flush=True)

def main():
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root builder required')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','inspect','audit','core','qa-export','qa-final','app','finalize'))
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--input-profile',type=Path)
    parser.add_argument('--input-profile-sha256')
    parser.add_argument('--core-inputs',type=Path)
    parser.add_argument('--core-sha')
    parser.add_argument('--app-archive',type=Path)
    parser.add_argument('--app-sha')
    parser.add_argument('--app-installer',type=Path)
    parser.add_argument('--app-installer-sha')
    parser.add_argument('--elf-inspector',type=Path)
    parser.add_argument('--elf-inspector-sha')
    args=parser.parse_args()
    if args.action=='core':update_core(args)
    elif args.action in ('qa-export','qa-final'):qa_export(args)
    elif args.action=='app':update_app(args)
    elif args.action=='finalize':finalize(args)
    else:prepare(args)

if __name__=='__main__':main()

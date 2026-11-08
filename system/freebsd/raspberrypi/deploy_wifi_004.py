#!/usr/local/bin/python3.12
"""Withdrawn 004 candidate: offline inspection or restoration only.

The physical Pi trial reproduced root SD I/O errors after radio attach.
Keep live rollback available; do not stage, boot or promote this candidate.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,re,shutil,stat,subprocess,sys,tarfile,time
from pathlib import Path
KERNEL='kernel.daos-wifi-004'
KERNEL_SHA='95fbf122f7255ee614399935a749a21e0e99e9fac1da079974bde739b9b6101f'
PAYLOAD_SHA='de5674bc3b76e47a03b17b26302467e19b6ee4a658a0bb82eb27e33394425c8c'
WORK=Path('/var/DaOS/updates/wifi-004')
WITHDRAWN_REASON='Wi-Fi 004 withdrawn: physical radio attach caused root SD read EIO; use a separately validated replacement'
BOOT={'kernel':KERNEL,'module_path':'/boot/'+KERNEL+';/boot/modules;/boot/dtb;/boot/dtb/overlays',
      'fdt_overlays':'/boot/dtb/overlays/daos-wifi-layout-002.dtbo','sdio_load':'YES','daos_wifi_trial':'004'}
CHECKS={'country_us','scan_24ghz','scan_5ghz','wpa2_dhcp','reconnect','storage_concurrent','no_new_storage_errors','usb_dsi_camera_observed'}

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def run(arguments):return subprocess.check_output(arguments,text=True,stderr=subprocess.STDOUT,timeout=45).strip()
def atomic(path,document):
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.004.tmp')
    with temporary.open('x',encoding='utf-8') as stream:json.dump(document,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
def write_bytes(path,data,mode=0o644):
    temporary=path.with_name(path.name+'.004.tmp')
    created=False
    try:
        with temporary.open('xb') as stream:
            created=True;stream.write(data);os.chmod(temporary,mode);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        if os.name!='nt':
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:
        if created:temporary.unlink(missing_ok=True)

def validate_payload(root,manifest):
    if manifest.get('kernel_directory')!=KERNEL or manifest.get('update_id')!='wifi-004':raise ValueError('Wrong staged candidate identity')
    for item in manifest['files']:
        relative=Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts or not item['path'].startswith('root/') or '\\' in item['path']:
            raise ValueError('Unsafe staged candidate destination')
        path=root/item['path'][5:]
        if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or sha(path)!=item['sha256']:
            raise ValueError('Candidate modules/firmware changed or were not completely written')
        if os.name!='nt' and stat.S_IMODE(path.stat().st_mode)!=item['mode']:raise ValueError('Candidate permissions changed')

def arm_trial(root,*,defer_radio=False):
    """Publish the one-shot selector last; a failed trial restores both files."""
    nextboot=root/'boot/nextboot.conf';rc=root/'etc/rc.conf'
    if any(p.is_symlink() for p in (nextboot,rc,*nextboot.parents,*rc.parents)):raise ValueError('Redirected trial destination')
    old_nextboot=nextboot.read_bytes();old_rc=rc.read_bytes()
    if not old_nextboot.startswith(b'nextboot_enable="NO"'):raise ValueError('One-shot flag is not safely cleared')
    text=old_rc.decode();values={'DaOS_wifi_driver_enable':'NO' if defer_radio else 'YES','DaOS_system_enable':'YES'}
    lines=[line for line in text.splitlines() if not any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in values)]
    rc_data=('\n'.join(lines)+'\n'+''.join(k+'="'+v+'"\n' for k,v in values.items())).encode()
    boot_data=('nextboot_enable="YES"\n'+''.join(k+'="'+v+'"\n' for k,v in BOOT.items())+'boot_mute="NO"\n').encode()
    try:
        write_bytes(rc,rc_data)
        write_bytes(nextboot,boot_data)
    except BaseException:
        # Clear/restore the selector before restoring service configuration.
        write_bytes(nextboot,old_nextboot)
        write_bytes(rc,old_rc)
        raise
def commit_defaults(root):
    """Enable the validated radio before publishing its default kernel selector."""
    loader=root/'boot/loader.conf.local';rc=root/'etc/rc.conf'
    if any(p.is_symlink() for p in (loader,rc,*loader.parents,*rc.parents)):raise ValueError('Redirected commit destination')
    old_loader=loader.read_bytes() if loader.exists() else None;old_rc=rc.read_bytes()
    loader_mode=stat.S_IMODE(loader.stat().st_mode) if old_loader is not None else 0o644
    rc_mode=stat.S_IMODE(rc.stat().st_mode)
    values={'DaOS_wifi_driver_enable':'YES','DaOS_system_enable':'YES'}
    rc_lines=[line for line in old_rc.decode().splitlines() if not any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in values)]
    rc_data=('\n'.join(rc_lines)+'\n'+''.join(k+'="'+v+'"\n' for k,v in values.items())).encode()
    loader_lines=[line for line in (old_loader or b'').decode().splitlines() if not any(re.match(r'\s*'+re.escape(k)+r'\s*=',line) for k in BOOT)]
    loader_data=('\n'.join(loader_lines)+'\n'+''.join(k+'="'+v+'"\n' for k,v in BOOT.items())).encode()
    try:
        write_bytes(rc,rc_data,rc_mode)
        write_bytes(loader,loader_data,loader_mode)
    except BaseException:
        # Restore the old default selector first, then its service configuration.
        try:
            if old_loader is not None:write_bytes(loader,old_loader,loader_mode)
            else:
                loader.unlink(missing_ok=True)
                if os.name!='nt':
                    directory=os.open(loader.parent,os.O_RDONLY)
                    try:os.fsync(directory)
                    finally:os.close(directory)
        finally:write_bytes(rc,old_rc,rc_mode)
        raise

def native():
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0 or os.uname().machine not in ('arm64','aarch64'):
        raise ValueError('需要原生 FreeBSD ARM64 root；此工具不写物理磁盘镜像')
def _read_payload(archive,incoming=None):
    if sha(archive)!=PAYLOAD_SHA:raise ValueError('004 candidate archive SHA256 mismatch; withdrawn candidates are prohibited')
    allowed_prefixes=('root/boot/'+KERNEL+'/', 'root/boot/firmware/brcmfmac43455-sdio.', 'root/usr/share/DaOS/wifi/')
    exact={'root/boot/dtb/overlays/daos-wifi-layout-002.dtbo','root/usr/local/libexec/DaOS-wifi-load.py','root/usr/local/etc/rc.d/DaOS-wifi-driver'}
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers();names=set()
        if len(members)>1024 or sum(m.size for m in members)>160*1024**2:raise ValueError('Wi-Fi payload exceeds bounds')
        for entry in members:
            p=Path(entry.name)
            if not entry.isfile() or entry.name in names or p.is_absolute() or '..' in p.parts or '\\' in entry.name:raise ValueError('Unsafe Wi-Fi archive')
            names.add(entry.name)
        manifest=json.loads(bundle.extractfile('manifest.json').read())
        if manifest.get('kernel_directory')!=KERNEL or manifest.get('update_id')!='wifi-004' or manifest.get('physical_wifi_validated') is not False:raise ValueError('Wrong candidate identity')
        listed={'manifest.json'}
        for item in manifest['files']:
            name=item['path']
            if name in listed or name not in exact and not name.startswith(allowed_prefixes):raise ValueError('Unexpected candidate destination')
            listed.add(name);member=bundle.getmember(name)
            if member.size!=item['bytes'] or item['mode'] not in (0o644,0o755):raise ValueError('Candidate length/mode mismatch')
            data=bundle.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Candidate file hash mismatch')
            if incoming is not None:
                destination=incoming/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
        if listed!=names:raise ValueError('Unlisted candidate files')
    kernel=next((item for item in manifest['files'] if item['path']=='root/boot/'+KERNEL+'/kernel'),None)
    if kernel is None or kernel['sha256']!=KERNEL_SHA:raise ValueError('Wrong compiled kernel')
    if incoming is not None and sha(incoming/'root/boot'/KERNEL/'kernel')!=KERNEL_SHA:raise ValueError('Wrong compiled kernel')
    return manifest

def unpack(archive,work):
    incoming=work/'incoming';incoming.mkdir()
    return incoming,_read_payload(archive,incoming)

def backup_paths(manifest):
    paths=[e['path'][5:] for e in manifest['files'] if not e['path'].startswith(('root/boot/'+KERNEL+'/','root/usr/share/DaOS/wifi/','root/boot/firmware/'))]
    return paths+['boot/loader.conf.local','boot/nextboot.conf','etc/rc.conf','etc/resolvconf.conf','etc/resolv.conf']

def check_staging_space(root,work,manifest):
    # The persistent update cache may be a separate nullfs mount. Account for
    # its actual filesystem, rather than the small /var parent appliance volume.
    parent=work.parent
    while not parent.exists():parent=parent.parent
    allocation=lambda length:((length+8191)//8192)*8192
    payload=sum(allocation(item['bytes']) for item in manifest['files'])
    backup=sum(allocation(path.stat().st_size) for name in backup_paths(manifest) if not (path:=root/name).is_symlink() and path.is_file())
    reserve=32*1024**2
    root_free=shutil.disk_usage(root).free;work_free=shutil.disk_usage(parent).free
    if root.stat().st_dev==parent.stat().st_dev:
        if root_free<payload*2+backup+reserve:raise ValueError('Insufficient shared root/update-cache staging space')
    else:
        if root_free<payload+reserve:raise ValueError('Insufficient root space for complete candidate and reserve')
        if work_free<payload+backup+reserve:raise ValueError('Insufficient update-cache staging space')
def validate_commit(document,*,boot_time,now=None):
    if document.get('format')!='DaOS-wifi-004-validation-v1' or document.get('kernel_sha256')!=KERNEL_SHA or document.get('payload_sha256')!=PAYLOAD_SHA:
        raise ValueError('004 validation identity mismatch')
    if document.get('passed') is not True or document.get('errors')!=[]:raise ValueError('Validation did not complete successfully')
    if document.get('boot_time')!=boot_time:raise ValueError('Validation belongs to a different boot')
    if not 0<=(time.time() if now is None else now)-document.get('completed_at',0)<=1800:raise ValueError('Validation is stale')
    if any(document.get('checks',{}).get(key) is not True for key in CHECKS):raise ValueError('Both-band/reconnect/concurrent storage/USB/DSI/camera checks must all pass')
def restore(root,receipt,work=WORK):
    for name,value in receipt['backups'].items():
        path=root/name
        if any(p.is_symlink() for p in (path,*path.parents)) and name!='etc/resolv.conf':raise ValueError('Redirected rollback destination')
        if value is None:path.unlink(missing_ok=True)
        elif 'link' in value:
            path.unlink(missing_ok=True);os.symlink(value['link'],path)
        else:
            source=work/'backup'/name
            if sha(source)!=value['sha256']:raise ValueError('Rollback backup changed')
            path.parent.mkdir(parents=True,exist_ok=True)
            write_bytes(path,source.read_bytes(),value.get('mode',stat.S_IMODE(source.stat().st_mode)))
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['stage','trial','commit','rollback'])
    parser.add_argument('--archive',type=Path);parser.add_argument('--validation',type=Path);parser.add_argument('--fallback-observed',action='store_true')
    parser.add_argument('--defer-radio',action='store_true',help='Trial kernel only; attach the radio manually after checking boot and recovery')
    parser.add_argument('--stage-root',type=Path);parser.add_argument('--work-dir',type=Path)
    args=parser.parse_args();live=args.stage_root is None
    if args.defer_radio and args.action!='trial':parser.error('--defer-radio is only valid for trial')
    if live and args.action!='rollback':raise ValueError(WITHDRAWN_REASON)
    if live:native()
    elif args.action!='stage':raise ValueError('Scratch mode only supports staging, never boot selection')
    root=Path('/') if live else args.stage_root.resolve(strict=True)
    if not live and root==Path(root.anchor):raise ValueError('Choose a private scratch directory')
    work=WORK if live else args.work_dir
    if work is None or work.is_symlink():raise ValueError('Choose a fresh private work directory')
    if args.action in ('trial','commit','rollback'):
        receipt=json.loads((work/'deployment.json').read_text())
        if receipt.get('payload_sha256')!=PAYLOAD_SHA:raise ValueError('No matching 004 staged payload')
        if args.action in ('trial','commit') and receipt.get('status') not in ('staged-default-unchanged','one-shot-armed-stable-default-retained'):
            raise ValueError('No completely staged 004 candidate')
    if args.action=='stage':
        if not args.archive:parser.error('stage requires --archive')
        if live:
            boot=run(['/sbin/sysctl','-n','kern.bootfile'])
            if boot!='/boot/kernel/kernel':raise ValueError('Stage from the restored stable kernel')
            active=json.loads(Path('/opt/hasslefree/camera-app-active.json').read_text())
            if active.get('version','')<'camera-experience-004':raise ValueError('Install the complete 004 application first')
        if work.exists():raise ValueError('Choose a fresh private work directory')
        # Verify the complete archive before creating state or making root writable.
        manifest=_read_payload(args.archive)
        if live:check_staging_space(root,work,manifest)
        work.mkdir(parents=True,mode=0o700);incoming,manifest=unpack(args.archive,work)
        changes=backup_paths(manifest)
        backups={}
        for name in changes:
            path=root/name
            if path.is_symlink():
                if name!='etc/resolv.conf' or os.readlink(path)!='/var/run/resolv.conf':raise ValueError('Redirected staging target')
                backups[name]={'link':os.readlink(path)}
            elif path.exists():
                backups[name]={'sha256':sha(path),'mode':stat.S_IMODE(path.stat().st_mode)};dest=work/'backup'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
            else:backups[name]=None
        receipt={'format':'DaOS-wifi-004-trial-v1','status':'prepared','payload_sha256':PAYLOAD_SHA,'backups':backups,'kernel_sha256':KERNEL_SHA,'physical_validated':False}
        atomic(work/'deployment.json',receipt)
        atomic(work/'payload-manifest.json',manifest)
        if live:run(['/sbin/mount','-u','-w','/'])
        try:
            if (root/'boot'/KERNEL).exists():raise ValueError('Candidate directory already exists')
            for item in manifest['files']:
                dst=root/item['path'][5:]
                if any(p.is_symlink() for p in (dst,*dst.parents)):raise ValueError('Redirected candidate destination')
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(incoming/item['path'],dst);os.chmod(dst,item['mode'])
                if sha(dst)!=item['sha256']:raise ValueError('Staging readback failed')
            # The stable boot selector, rc.conf and resolver remain byte-for-byte
            # unchanged until explicit one-shot trial is requested.
            receipt['status']='staged-default-unchanged';atomic(work/'deployment.json',receipt)
        except BaseException:
            restore(root,receipt,work);receipt['status']='failed';atomic(work/'deployment.json',receipt);raise
        finally:
            if live:run(['/sbin/mount','-u','-r','/'])
    elif args.action=='trial':
        if not args.fallback_observed:raise ValueError('Observe nextboot flag clearing on this board before --fallback-observed')
        if run(['/sbin/sysctl','-n','kern.bootfile'])!='/boot/kernel/kernel':raise ValueError('Start trial from stable kernel')
        nextboot=Path('/boot/nextboot.conf')
        if not nextboot.read_text().startswith('nextboot_enable="NO"'):raise ValueError('One-shot flag is not safely cleared')
        if sha(Path('/boot')/KERNEL/'kernel')!=KERNEL_SHA:raise ValueError('Staged kernel changed')
        manifest=json.loads((work/'payload-manifest.json').read_text())
        validate_payload(Path('/'),manifest)
        if Path('/var/DaOS/system/wifi/country').read_text().strip()!='US':raise ValueError('请先在机身Wi-Fi设置保存美国US地区')
        run(['/sbin/mount','-u','-w','/'])
        try:
            arm_trial(Path('/'),defer_radio=args.defer_radio)
        finally:run(['/sbin/mount','-u','-r','/'])
        receipt['status']='one-shot-armed-stable-default-retained';receipt['radio_attach_deferred']=args.defer_radio;atomic(work/'deployment.json',receipt)
    elif args.action=='commit':
        if not args.validation:parser.error('commit requires a complete native --validation receipt')
        if run(['/sbin/sysctl','-n','kern.bootfile'])!='/boot/'+KERNEL+'/kernel' or sha('/boot/'+KERNEL+'/kernel')!=KERNEL_SHA:raise ValueError('004 trial kernel is not running')
        validate_commit(json.loads(args.validation.read_text()),boot_time=run(['/sbin/sysctl','-n','kern.boottime']))
        validate_payload(Path('/'),json.loads((work/'payload-manifest.json').read_text()))
        run(['/sbin/mount','-u','-w','/'])
        try:
            commit_defaults(Path('/'))
        finally:run(['/sbin/mount','-u','-r','/'])
        receipt['status']='validated-default-committed';receipt['validation_sha256']=sha(args.validation);atomic(work/'deployment.json',receipt)
    else:
        run(['/sbin/mount','-u','-w','/'])
        try:restore(root,receipt)
        finally:run(['/sbin/mount','-u','-r','/'])
        receipt['status']='stable-configuration-restored';atomic(work/'deployment.json',receipt)
    print(json.dumps({'status':receipt['status'],'default_changed':args.action=='commit','reboot_performed':False}))
if __name__=='__main__':main()

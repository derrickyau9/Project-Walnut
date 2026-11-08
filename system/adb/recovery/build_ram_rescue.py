"""Build a private FreeBSD ARM64 RAM-root rescue from a read-only DaOS root copy."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import struct
import sys


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(args):
    r=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=180)
    if r.returncode:raise RuntimeError(str(args)+'\n'+r.stdout+r.stderr)
    return r.stdout


def usb_helper_override(path, expected):
    """Snapshot an explicitly SHA-bound native helper before any build writes."""
    if (path is None)!=(expected is None):
        raise ValueError('--usb-helper and --usb-helper-sha256 must be supplied together')
    if path is None:return None
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
        raise ValueError('Exact lowercase USB helper SHA256 required')
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Redirected USB helper source')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 64<=info.st_size<=2*1024**2:
            raise ValueError('Bounded ordinary USB helper file required')
        data=stream.read(2*1024**2+1)
    if hashlib.sha256(data).hexdigest()!=expected:
        raise ValueError('USB helper SHA256 mismatch')
    if (len(data)<64 or data[:8]!=b'\x7fELF\x02\x01\x01\x09'
            or struct.unpack_from('<HHI',data,16) not in ((2,183,1),(3,183,1))):
        raise ValueError('Native FreeBSD ARM64 USB helper required')
    return data


def rescue_rc():
    # A supervisor's flock proves ownership, never host configuration or AUTH.
    # Keep failures visible on the attached LCD when USB itself is unavailable.
    return b'''#!/bin/sh
LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib; export LD_LIBRARY_PATH
# Only the PID1 init_script hook can have prepared this tmpfs before /etc/rc.
# Inspect it before this script performs any bootstrap mounts or hook calls.
pre_rc_ram=no
while read -r source target kind options dump pass; do
 if [ "$target" = /DaOS-recovery-ram ] && [ "$kind" = tmpfs ]; then
  case ",$options," in *,rw,*) pre_rc_ram=yes;; esac
 fi
done <<EOF
$(/rescue/mount -p)
EOF
[ "$pre_rc_ram" = yes ] && echo DAOS_RAM_PRE_RC_TMPFS_READY
/rescue/mount -t devfs devfs /dev 2>/dev/null || :
echo DAOS_RAM_BOOTSTRAP_START
/rescue/sysctl kern.bootfile kern.module_path
/rescue/kenv init_script=/usr/local/libexec/DaOS-recovery/early-init.sh
/bin/sh /usr/local/libexec/DaOS-recovery/early-init.sh
/rescue/sleep 2
if /usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py status; then
 echo DAOS_RAM_SUPERVISOR_ALIVE
else
 echo DAOS_RAM_SUPERVISOR_FAILED
fi
if /usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py health; then
 echo DAOS_RAM_ADB_AUTHENTICATED
else
 echo DAOS_RAM_USB_PENDING_SEE_HEALTH_JSON
fi
# No physical filesystem is mounted or written for diagnostic persistence.
exit 1
'''


RUNTIME_SCRIPTS=('early-init.sh','early_adb.py','early_daemon.py','flash.py','return-normal.py')
OPTIONAL_EXECUTABLE='/usr/local/libexec/DaOS-recovery/kernel-watchdog'


def image_resolve(root,name):
    """Resolve absolute image symlinks under root, never against the builder."""
    p=Path(name.lstrip('/'))
    for _ in range(32):
        current=root
        for index,part in enumerate(p.parts):
            current=current/part
            if current.is_symlink():
                link=os.readlink(current)
                prefix=Path(link.lstrip('/')) if link.startswith('/') else Path(*p.parts[:index])/link
                p=Path(os.path.normpath(str(prefix.joinpath(*p.parts[index+1:]))))
                if '..' in p.parts:raise ValueError('Image symlink escapes root: '+name)
                break
        else:return current
    raise ValueError('Image symlink cycle: '+name)


def runtime_preflight(root,scripts=None):
    """Fail the build on a missing reachable command, applet or ELF library."""
    root=Path(root)
    if scripts is None:
        scripts={name:(root/'usr/local/libexec/DaOS-recovery'/name).read_text() for name in RUNTIME_SCRIPTS}
    paths={'/rescue/rescue','/usr/local/bin/python3.12','/usr/sbin/daemon',
           '/usr/local/libexec/daos-usb-adb'}
    applets=set()
    for text in scripts.values():
        paths.update(re.findall(r'/(?:rescue|sbin|bin|usr|libexec)/[A-Za-z0-9_.+/-]+',text))
        applets.update(re.findall(r"['\"]/rescue/rescue['\"]\s*,\s*['\"]([A-Za-z0-9_]+)['\"]",text))
    report={'format':'DaOS-rescue-runtime-dependency-QA-v1','paths':{},'libraries':{},'rescue_applets':[],'optional_skipped':[]}
    rescue=image_resolve(root,'/rescue/rescue').read_bytes()
    seen=set();pending=[]
    for name in sorted(paths):
        if name==OPTIONAL_EXECUTABLE and not (root/'etc/daos/recovery-watchdog.enabled').exists():
            report['optional_skipped'].append({'path':name,'reason':'watchdog enable policy absent'})
            continue
        p=image_resolve(root,name)
        if not p.exists():raise ValueError('Missing reachable RAM rescue path: '+name)
        if p.is_dir():
            report['paths'][name]={'type':'directory'}
            continue
        if not p.is_file():raise ValueError('Invalid reachable RAM rescue file: '+name)
        if not p.stat().st_mode&0o111:raise ValueError('RAM rescue executable is not executable: '+name)
        if name.startswith('/rescue/') and name!='/rescue/rescue':applets.add(Path(name).name)
        report['paths'][name]={'type':'file','resolved':p.relative_to(root).as_posix(),'sha256':sha(p)}
        if p not in seen:pending.append(p);seen.add(p)
    for name in sorted(applets):
        if (name+'\0').encode() not in rescue:
            raise ValueError('Required rescue multicall applet is absent: '+name)
    report['rescue_applets']=sorted(applets)
    while pending:
        p=pending.pop()
        with p.open('rb') as file:header=file.read(20)
        if header[:4]!=b'\x7fELF':continue # Python/shell script arguments.
        if header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',header,18)[0]!=183:
            raise ValueError('Non-ARM64 RAM executable/library: '+str(p))
        dynamic=run(['/usr/bin/readelf','-d',p])
        for name in re.findall(r'\(NEEDED\).*?\[([^]]+)\]',dynamic):
            if '/' in name:raise ValueError('Invalid RAM ELF dependency')
            choices=[image_resolve(root,'/'+str(d/name)) for d in (Path('lib'),Path('usr/lib'),Path('usr/local/lib'))]
            library=next((v for v in choices if v.is_file()),None)
            if library is None:raise ValueError('Missing RAM ELF dependency: '+name)
            report['libraries'][name]={'path':library.relative_to(root).as_posix(),'sha256':sha(library)}
            if library not in seen:pending.append(library);seen.add(library)
    report['passed']=True
    return report


def build(args):
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD root builder required')
    helper=usb_helper_override(getattr(args,'usb_helper',None),getattr(args,'usb_helper_sha256',None))
    out=args.output.absolute()
    if out.exists() or out.is_symlink():raise ValueError('Fresh output directory required')
    if args.root_copy.is_symlink() or sha(args.root_copy)!=args.root_sha256:raise ValueError('Read-only root-copy identity mismatch')
    out.mkdir(mode=0o700);mount=out/'source';mount.mkdir();root=out/'root';root.mkdir()
    md=run(['/sbin/mdconfig','-a','-t','vnode','-f',args.root_copy,'-o','readonly']).strip()
    if not re.fullmatch('md[0-9]+',md):raise ValueError('Unexpected owned md')
    mounted=False
    try:
        run(['/sbin/mount','-t','ufs','-o','ro,noatime','/dev/'+md,mount]);mounted=True
        def put(rel,src=None,data=None,mode=None):
            dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            if src is not None:shutil.copyfile(src,dst)
            else:dst.write_bytes(data)
            if mode is not None:dst.chmod(mode)
            return dst
        def resolve(rel):
            p=Path(rel)
            for _ in range(32):
                current=mount
                for i,part in enumerate(p.parts):
                    current=current/part
                    if current.is_symlink():
                        target=os.readlink(current)
                        target=Path(target.lstrip('/')) if target.startswith('/') else Path(*p.parts[:i])/target
                        p=Path(os.path.normpath(str(target.joinpath(*p.parts[i+1:]))))
                        if '..' in p.parts:raise ValueError('Source link escapes root')
                        break
                else:return current
            raise ValueError('Source link cycle')
        # Copy interpreter/runtime without application or photo state.
        for rel in ('usr/local/bin/python3.12','usr/local/libexec/daos-usb-adb','libexec/ld-elf.so.1','rescue/rescue','usr/sbin/daemon','usr/sbin/diskinfo','lib/geom/geom_label.so','lib/geom/geom_part.so'):
            put(rel,resolve(rel),mode=0o755)
        if helper is not None:
            # Include the new executable before deriving the ELF library closure.
            put('usr/local/libexec/daos-usb-adb',data=helper,mode=0o755)
        py=mount/'usr/local/lib/python3.12'
        shutil.copytree(py,root/'usr/local/lib/python3.12',ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests','ensurepip','idlelib','tkinter'),symlinks=False)
        shutil.copytree(mount/'usr/local/libexec/daos-adb',root/'usr/local/libexec/daos-adb',ignore=shutil.ignore_patterns('__pycache__'))
        # Resolve every ELF dependency from the source filesystem. Never run ARM ldd.
        pending=[p for p in root.rglob('*') if p.is_file() and p.open('rb').read(4)==b'\x7fELF']
        copied=set()
        while pending:
            p=pending.pop()
            dynamic=run(['/usr/bin/readelf','-d',p])
            for name in re.findall(r'\(NEEDED\).*?\[([^]]+)\]',dynamic):
                if name in copied:continue
                if '/' in name:raise ValueError('Invalid ELF dependency')
                choices=[(d/ name) for d in (Path('lib'),Path('usr/lib'),Path('usr/local/lib')) if (mount/d/name).exists()]
                if not choices:raise ValueError('Unresolved rescue library '+name)
                rel=choices[0];dst=put(str(rel),resolve(str(rel)),mode=0o755);copied.add(name);pending.append(dst)
        for name in ('sh','mount','umount','kldload','kldstat','sysctl','reboot','mdconfig','fsck_ufs','gpart','geom','kenv','sleep','cat','ls','dd','sync','ps','tail','dmesg','md5','mkdir','chmod','rm'):
            (root/'rescue'/name).symlink_to('rescue')
        for rel in ('sbin/init','bin/sh','sbin/mount','sbin/umount','sbin/kldload','sbin/kldstat','sbin/sysctl','sbin/reboot','sbin/mdconfig','sbin/fsck_ufs','sbin/gpart','sbin/geom','bin/kenv','bin/sleep','bin/cat','bin/ls','bin/dd','bin/sync','bin/ps','usr/bin/tail','bin/mkdir','bin/chmod','bin/rm'):
            if rel=='sbin/init':put(rel,args.patched_init,mode=0o555)
            else:
                dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.symlink_to('/rescue/rescue')
        # All relevant module filenames come from the stable kernel, never Wi-Fi candidates.
        for name in ('kernel','usb_template.ko'):
            put('boot/kernel/'+name,resolve('boot/kernel/'+name),mode=0o644)
        put('boot/modules/daos_adb_template.ko',resolve('boot/modules/daos_adb_template.ko'),mode=0o644)
        put('etc/daos/adb_keys',resolve('etc/daos/adb_keys'),mode=0o600)
        for rel in ('etc/master.passwd','etc/passwd','etc/group','etc/pwd.db','etc/spwd.db','etc/login.conf','etc/localtime','etc/termcap'):
            if (mount/rel).exists():put(rel,resolve(rel),mode=0o644)
        for p in args.sources.iterdir():
            if p.suffix in ('.py','.sh'):put('usr/local/libexec/DaOS-recovery/'+p.name,p,mode=0o755)
        put('etc/daos/recovery-adb.json',args.config,mode=0o600)
        put('etc/daos/recovery-identity.json',args.identity,mode=0o600)
        for rel,mode in [('dev',0o755),('tmp',0o1777),('var',0o755),('run/daos-recovery',0o700),('DaOS-recovery-ram',0o700)]:
            (root/rel).mkdir(parents=True,exist_ok=True);(root/rel).chmod(mode)
        put('etc/fstab',data=b'# RAM rescue: physical DaOS partitions stay unmounted\n',mode=0o644)
        put('etc/rc',data=rescue_rc(),mode=0o755)
        marker={'format':'DaOS-ram-rescue-v1','flash_enabled':True}
        put('run/daos-recovery/ram-root.json',data=(json.dumps(marker)+'\n').encode(),mode=0o600)
        preflight=runtime_preflight(root)
        (out/'runtime-dependencies.json').write_text(json.dumps(preflight,indent=2)+'\n')
        image=out/'DaOS-rescue.ufs'
        run(['/usr/sbin/makefs','-t','ffs','-B','little','-s','384m','-o','version=2,label=DaOS-rescue',image,root])
        check=run(['/sbin/fsck_ufs','-n',image]);(out/'fsck.txt').write_text(check)
        with image.open('rb') as src,gzip.GzipFile(filename=str(image)+'.gz',mode='wb',mtime=0) as dst:shutil.copyfileobj(src,dst)
        report={'format':'DaOS-RAM-rescue-build-v1','root_copy_sha256':args.root_sha256,'init_sha256':sha(args.patched_init),'image_sha256':sha(image),'gzip_sha256':sha(str(image)+'.gz'),'bytes':image.stat().st_size,'gzip_bytes':Path(str(image)+'.gz').stat().st_size,'physical_usb_validated':False,'libraries':sorted(copied),
                'usb_helper':{'override':helper is not None,'sha256':sha(root/'usr/local/libexec/daos-usb-adb'),'bytes':(root/'usr/local/libexec/daos-usb-adb').stat().st_size}}
        (out/'build.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    finally:
        if mounted:run(['/sbin/umount',mount])
        run(['/sbin/mdconfig','-d','-u',md[2:]])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root-copy',type=Path,required=True);p.add_argument('--root-sha256',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--patched-init',type=Path,required=True);p.add_argument('--sources',type=Path,required=True);p.add_argument('--identity',type=Path,required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--usb-helper',type=Path);p.add_argument('--usb-helper-sha256');build(p.parse_args())

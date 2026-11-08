#!/usr/local/bin/python3.12
"""005 diagnostic: RF is possible only after physical SD filesystems are read-only.

plan and watchdog-source never operate hardware. start replaces itself with a
static RAM shell; runtime faults keep SD read-only and request the stable reboot.
Preparation errors never enable RF but may leave services or mounts quiesced;
the printed owned RAM receipt supports the separate reboot-stable action.
The initial diagnostic always reboots to stable, including on success. restore
is a reboot-only compatibility action; it never enables SD writes on 005.
"""
from __future__ import annotations
import argparse, errno, hashlib, json, os, re, shutil, sqlite3, stat, struct, subprocess, sys, time, uuid
from pathlib import Path
from deploy_wifi_005 import KERNEL,KERNEL_SHA,PAYLOAD_SHA,WORK,_read_payload,require_running_trial,validate_payload

WATCHDOG_SHA='b852a5db1804b5237e5b97d66e0bd309eda976af58dfd44ce67629c22dd991bc'
RAM_PREFIX='/tmp/DaOS-wifi005-ro-'
EXPECTED={'/':'/dev/gpt/DaOS-root','/var':'/dev/gpt/DaOS-state',
          '/var/DaOS/calibration':'/dev/gpt/DaOS-calibration',
          '/var/DaOS/camera/photos':'/dev/gpt/DaOS-photos'}
ALIASES=('/var/DaOS/ui/apps','/var/DaOS/camera/library-imports','/var/DaOS/updates')
APPLETS=('sh','cat','sysctl','kenv','dmesg','tail','sleep','kldload','kldstat','reboot','ps',
         'ifconfig','dd','md5','kill','sync','date','mount','mount_nullfs','umount','pgrep')
ERROR_PATTERN=re.compile(r'(?i)(g_vfs_done|I/O error|Input/output error|pager read error|panic:|CAM status:|mmc.*(?:timeout|error)|sdhci.*(?:timeout|error))')
FORMAT='DaOS-wifi005-readonly-diagnostic-v1'
READONLY_FLAG=getattr(os,'ST_RDONLY',1)
WATCHDOG_MARKER=b'DaOS-wifi005-ram-watchdog-v1'
EARLY_USB_SUPERVISOR=Path('/usr/local/libexec/DaOS-recovery/early_adb.py')
EARLY_USB_RECEIPT=Path('/DaOS-recovery-ram/early.json')
NORMAL_USB_PID=Path('/var/run/DaOS-usb-adb-session.pid')

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def command(args,*,check=True,timeout=15):
    r=subprocess.run([str(v) for v in args],capture_output=True,text=True,timeout=timeout)
    if check and r.returncode:raise RuntimeError('Command failed: '+str(args)+': '+r.stderr[-2000:])
    return r

def parse_mounts(text):
    rows=[]
    for line in text.splitlines():
        fields=line.split()
        if not fields:continue
        if len(fields)!=6:raise ValueError('Unexpected mount table')
        rows.append({'source':fields[0],'target':fields[1],'type':fields[2],'options':fields[3].split(',')})
    return rows

def physical_mounts(rows,*,protected):
    found={}
    for row in rows:
        if row['type']=='ufs':
            target=row['target']
            if target not in EXPECTED or row['source']!=EXPECTED[target] or target in found:
                raise ValueError('Unknown, redirected, or duplicate physical UFS mount')
            if ('ro' in row['options'])==('rw' in row['options']):raise ValueError('Unclear UFS access mode')
            found[target]=row
        elif row['type'] not in ('devfs','tmpfs','nullfs','procfs','fdescfs') and 'rw' in row['options']:
            raise ValueError('Other writable physical filesystem is mounted')
    if set(found)!=set(EXPECTED):raise ValueError('All four physical DaOS UFS providers are required')
    if 'ro' not in found['/']['options']:raise ValueError('Root must already be read-only')
    if protected and any('ro' not in row['options'] for row in found.values()):
        raise ValueError('A physical DaOS filesystem is still writable')
    return found

def require_tmpfs(path,rows):
    name=Path(path).as_posix()
    ancestors=[row for row in rows if name==row['target'] or name.startswith(row['target'].rstrip('/')+'/')]
    if not ancestors or max(ancestors,key=lambda row:len(row['target']))['type']!='tmpfs':
        raise ValueError('Diagnostic directory must be on actual tmpfs')

def owned(path,*,directory=False):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Redirected diagnostic path')
    s=path.stat()
    if s.st_uid!=0 or (not directory and not stat.S_ISREG(s.st_mode)):raise ValueError('Diagnostic files must be root-owned')
    if directory and (not stat.S_ISDIR(s.st_mode) or stat.S_IMODE(s.st_mode)!=0o700):
        raise ValueError('RAM diagnostic directory must have mode 0700')
    return path

def static_arm64(path):
    data=Path(path).read_bytes()
    if len(data)<64 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
        raise ValueError('Expected static ARM64 ELF')
    phoff=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
    if not count or size<56 or phoff+size*count>len(data):raise ValueError('Invalid ELF program table')
    if any(struct.unpack_from('<I',data,phoff+n*size)[0]==3 for n in range(count)):
        raise ValueError('Dynamic interpreter is forbidden for RAM recovery tools')
    return data

def save(path,value):
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('x',encoding='utf-8') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.chmod(temporary,0o600);os.replace(temporary,path)

def idle_statuses():
    c=sqlite3.connect('file:/var/DaOS/camera/journal.sqlite3?mode=ro',uri=True)
    try:rows=list(c.execute('SELECT status,count(*) FROM captures GROUP BY status'))
    finally:c.close()
    if any(status not in ('complete','failed','cancelled') and count for status,count in rows):
        raise ValueError('Nonterminal or unknown exposure result: diagnostic refused')
    return rows

def blocked_write(path):
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except OSError as e:
        if e.errno in (errno.EROFS,errno.ENXIO):return e.errno
        raise ValueError('Write probe did not prove read-only protection: '+str(e)) from e
    else:
        os.close(fd);Path(path).unlink()
        raise ValueError('Write probe succeeded: no RF is permitted')

def protect(ram,original,probes,run=command):
    parents={Path('/opt/hasslefree') if target=='/' else Path(target) for target in tuple(EXPECTED)+ALIASES}
    required={p/('.DaOS-wifi005-write-probe-'+ram.name[-32:]) for p in parents}
    if set(probes)!=required or len(probes)!=len(required):raise ValueError('Every physical mount and bulk alias needs its owned write probe')
    # Child data mounts before /var; force revokes old USB log writer vnodes.
    for target in ('/var/DaOS/camera/photos','/var/DaOS/calibration','/var'):
        run([ram/'mount','-u','-r','-f',target])
    rows=parse_mounts(run([ram/'mount','-p']).stdout)
    physical_mounts(rows,protected=True)
    for target in EXPECTED:
        if not os.statvfs(target).f_flag&READONLY_FLAG:raise ValueError('Physical statvfs is not read-only: '+target)
    proof={str(p):blocked_write(p) for p in probes}
    return proof

def diagnostic_plan():
    return {'format':FORMAT,'actions':['bound005/kernel/PIO/nextboot/radio/idle checks',
        'fresh root-owned tmpfs; hash static rescue, static watchdog, module and firmware',
        'stop camera and system; recheck idle; sync',
        'force physical Photos, calibration, State read-only; verify lower mounts and alias write denial',
        'arm RAM hardware watchdog and require nonce ARMED acknowledgement',
        'exec RAM collector; baseline 1MiB root checksum, one RAM radio attach, concurrent 8MiB read, fresh post-attach checksum',
        'failure or protected-ready success: keep all SD read-only and RAM reboot to stable',
        'restore/reboot-stable compatibility action only reboots; no 005 write restoration'],
        'requires_static_watchdog_sha_bound':bool(re.fullmatch('[a-f0-9]{64}',WATCHDOG_SHA)),
        'automatic_restore':False,'rw_restore_supported':False,'physical_wifi_validated':False}

def radio_idle(ram=None):
    base=Path('/sbin') if ram is None else ram
    pgrep=Path('/bin/pgrep') if ram is None else ram/'pgrep'
    if command([base/'kldstat','-q','-n','if_brcmfmac.ko'],check=False).returncode==0:raise ValueError('Radio is already loaded')
    if command([base/'sysctl','-n','net.wlan.devices']).stdout.strip():raise ValueError('An existing radio prevents an isolated diagnostic')
    if command([pgrep,'-f',r'DaOS-wifi-load[.]py|kldload.*(brcmfmac|sdio)'],check=False).returncode==0:
        raise ValueError('Another radio attach process is active')
    if command([base/'sysctl','-n','vm.swap_total']).stdout.strip()!='0':raise ValueError('Active swap bypasses filesystem read-only protection')
    for dependency in ('pci','firmware','wlan','sdiob'):
        if command([base/'kldstat','-q','-m',dependency],check=False).returncode:
            raise ValueError('Radio dependency is not already resident: '+dependency)

def usb_session_pid():
    """Honor the authenticated pre-rc owner; never trust a stale PID alone."""
    if EARLY_USB_SUPERVISOR.exists() or EARLY_USB_SUPERVISOR.is_symlink():
        supervisor=owned(EARLY_USB_SUPERVISOR)
        info=supervisor.stat()
        if info.st_mode & 0o022 or info.st_nlink!=1:
            raise ValueError('Untrusted early USB supervisor')
        status=command([sys.executable,'-I','-B',supervisor,'status'],check=False)
        if status.returncode==0:
            receipt=owned(EARLY_USB_RECEIPT)
            info=receipt.stat()
            if info.st_mode & 0o022 or info.st_nlink!=1 or info.st_size>4096:
                raise ValueError('Untrusted early USB receipt permissions/length')
            data=json.loads(receipt.read_text())
            if data.get('format')!='DaOS-early-recovery-adb-v1' or data.get('auth_required') is not True:
                raise ValueError('Unexpected early USB authentication receipt')
            pid=data.get('pid')
            if type(pid) is not int or pid<=1:raise ValueError('Invalid early USB owner PID')
            os.kill(pid,0)
            return pid
        if status.returncode!=1 or getattr(status,'stderr','').strip():raise ValueError('Early USB owner status could not be verified')
    text=owned(NORMAL_USB_PID).read_text().strip()
    if not text.isdecimal() or int(text)<=1:raise ValueError('Invalid ordinary USB session PID')
    pid=int(text);os.kill(pid,0)
    return pid


def start(archive,watchdog):
    require_running_trial()
    if os.geteuid()!=0 or os.uname().machine not in ('arm64','aarch64'):raise ValueError('Native ARM64 root required')
    if not re.fullmatch('[a-f0-9]{64}',WATCHDOG_SHA):raise ValueError('Static watchdog artifact is not bound; RF refused')
    if not Path('/boot/nextboot.conf').read_text().startswith('nextboot_enable="NO"'):raise ValueError('One-shot fallback flag did not clear')
    if command(['/usr/sbin/service','DaOS-wifi-driver','status'],check=False).returncode==0:raise ValueError('Radio loader service is already active')
    radio_idle()
    usb_pid=usb_session_pid()
    if Path('/var/DaOS/system/wifi/country').read_text().strip()!='US':raise ValueError('Saved US installation country required')
    manifest=_read_payload(owned(archive))
    if manifest!=json.loads((WORK/'payload-manifest.json').read_text()):raise ValueError('Installed staging manifest does not match bound archive')
    validate_payload(Path('/'),manifest)
    deployment=json.loads((WORK/'deployment.json').read_text())
    if deployment.get('payload_sha256')!=PAYLOAD_SHA or deployment.get('kernel_sha256')!=KERNEL_SHA or deployment.get('radio_attach_deferred') is not True:
        raise ValueError('Matching deferred 005 deployment receipt required')
    statuses=idle_statuses()
    before=command(['/sbin/mount','-p']).stdout;rows=parse_mounts(before);original=physical_mounts(rows,protected=False)
    baseline=command(['/sbin/dmesg','-a']).stdout
    if ERROR_PATTERN.search(baseline):raise ValueError('Current boot already has storage or CAM errors')
    if shutil.disk_usage('/tmp').free<64*1024**2:raise ValueError('Insufficient RAM diagnostic space')
    nonce=uuid.uuid4().hex;ram=Path(RAM_PREFIX+nonce);require_tmpfs(ram,rows);ram.mkdir(mode=0o700);owned(ram,directory=True)
    (ram/'before-protection.dmesg').write_text(baseline)
    rescue=owned('/rescue/rescue');static_arm64(rescue)
    shutil.copyfile(rescue,ram/'rescue');os.chmod(ram/'rescue',0o755)
    if digest(rescue)!=digest(ram/'rescue'):raise ValueError('Rescue RAM copy hash mismatch')
    for name in APPLETS:os.link(ram/'rescue',ram/name)
    watchdog=owned(watchdog);binary=static_arm64(watchdog)
    if WATCHDOG_MARKER not in binary or digest(watchdog)!=WATCHDOG_SHA:raise ValueError('Static watchdog identity mismatch')
    shutil.copyfile(watchdog,ram/'watchdog');os.chmod(ram/'watchdog',0o755)
    if digest(ram/'watchdog')!=WATCHDOG_SHA:raise ValueError('Watchdog RAM copy changed')
    module='root/boot/'+KERNEL+'/if_brcmfmac.ko'
    entries={v['path']:v for v in manifest['files']}
    required=[module]+[p for p in entries if p.startswith('root/boot/firmware/brcmfmac43455-sdio.')]
    if len(required)!=4:raise ValueError('Expected exactly one module and three bound firmware assets')
    (ram/'firmware').mkdir(mode=0o755)
    for name in required:
        src=owned(Path('/')/name[5:]);dst=ram/'if_brcmfmac.ko' if name==module else ram/'firmware'/Path(name).name
        shutil.copyfile(src,dst);os.chmod(dst,0o644)
        if digest(dst)!=entries[name]['sha256']:raise ValueError('Bound RAM radio asset changed')
    names=command([ram/'sysctl','-aN']).stdout.splitlines()
    dumps=[v for v in names if re.fullmatch(r'dev\.[A-Za-z0-9_]+\.[0-9]+\.slot0\.debug\.dump(regs|caps)',v)]
    if len(dumps)<4:raise ValueError('Both controllers require read-only register/capability dumps')
    firmware_target=Path('/boot/firmware')
    if any(row['target']==str(firmware_target) for row in rows):raise ValueError('Firmware already has an unexpected overlay')
    command([ram/'mount_nullfs','-o','ro',ram/'firmware',firmware_target])
    script=COLLECTOR.replace('__RAM__',str(ram)).replace('__DUMPS__',' '.join(dumps)).replace('__NONCE__',nonce)
    (ram/'run.sh').write_text(script);os.chmod(ram/'run.sh',0o700)
    command([ram/'sh','-n',ram/'run.sh'])
    services={name:command(['/usr/sbin/service',name,'status'],check=False).returncode==0 for name in ('DaOS-camera','DaOS-system')}
    receipt={'format':FORMAT,'nonce':nonce,'boot_time':command([ram/'sysctl','-n','kern.boottime']).stdout.strip(),
        'kernel_sha256':KERNEL_SHA,'payload_sha256':PAYLOAD_SHA,'original_mounts':list(original.values()),
        'services':services,'usb_pid':usb_pid,'capture_statuses':statuses,'firmware_overlay':{'source':str(ram/'firmware'),'target':'/boot/firmware'},
        'ram_hashes':{'rescue':digest(ram/'rescue'),'watchdog':WATCHDOG_SHA,'run.sh':digest(ram/'run.sh'),
                      'if_brcmfmac.ko':entries[module]['sha256']},'status':'prepared-no-radio'}
    save(ram/'receipt.json',receipt)
    print(json.dumps({'status':'prepared-no-radio','ram_directory':str(ram),'preparation_failure_recovery_action':'reboot-stable','rw_restore_supported':False}),flush=True)
    for name,running in services.items():
        if running:command(['/usr/sbin/service',name,'stop'])
    idle_statuses()
    command([ram/'sync'])
    probes=[]
    for target in tuple(EXPECTED)+ALIASES:
        parent=Path('/opt/hasslefree') if target=='/' else Path(target)
        if any(p.is_symlink() for p in (parent,*parent.parents)):raise ValueError('Redirected probe parent')
        probe=parent/('.DaOS-wifi005-write-probe-'+nonce)
        if probe.exists():raise ValueError('Probe destination already exists')
        probes.append(probe)
    receipt['status']='protecting-no-radio';save(ram/'receipt.json',receipt)
    proof=protect(ram,original,probes)
    try:
        radio_idle(ram);os.kill(usb_pid,0)
        current=parse_mounts(command([ram/'mount','-p']).stdout)
        overlay=[r for r in current if r['target']=='/boot/firmware']
        if len(overlay)!=1 or overlay[0]['source']!=str(ram/'firmware') or 'ro' not in overlay[0]['options']:raise ValueError('RAM firmware is not read-only mounted')
        receipt.update(status='protected-no-radio',write_denial=proof);save(ram/'receipt.json',receipt)
        (ram/'heartbeat').write_text('pre-radio\n');os.chmod(ram/'heartbeat',0o600)
        with (ram/'watchdog.log').open('xb') as log:
            dog=subprocess.Popen([str(ram/'watchdog'),str(ram),nonce],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not (ram/'watchdog.armed').exists():
            if dog.poll() is not None:raise ValueError('Hardware watchdog failed to arm; RF refused')
            time.sleep(.05)
        if not (ram/'watchdog.armed').exists() or (ram/'watchdog.armed').read_text().strip()!='ARMED '+str(dog.pid)+' '+nonce:
            raise ValueError('Missing hardware watchdog ARMED acknowledgement; RF refused')
        receipt.update(status='protected-watchdog-armed',watchdog_pid=dog.pid);save(ram/'receipt.json',receipt)
        print(json.dumps({'status':'protected-watchdog-armed','ram_directory':str(ram),'watchdog_pid':dog.pid,'physical_providers_readonly':EXPECTED,'write_denial':proof}),flush=True)
        env={'PATH':str(ram),'HOME':str(ram),'TMPDIR':str(ram),'LC_ALL':'C'}
        os.execve(ram/'sh',[str(ram/'sh'),str(ram/'run.sh')],env)
    except BaseException:
        print(json.dumps({'status':'protected-pre-attach-refused','ram_directory':str(ram),'rebooting_stable':True}),flush=True)
        command([ram/'reboot','-qn'],check=False)
        raise

def reboot_stable(ram):
    if os.geteuid()!=0:raise ValueError('Root required')
    if not re.fullmatch(re.escape(RAM_PREFIX)+r'[a-f0-9]{32}',str(ram)):raise ValueError('Unexpected RAM receipt path')
    owned(ram,directory=True)
    receipt=json.loads(owned(ram/'receipt.json').read_text())
    if receipt.get('format')!=FORMAT or receipt.get('nonce')!=ram.name[-32:] or receipt.get('kernel_sha256')!=KERNEL_SHA or receipt.get('payload_sha256')!=PAYLOAD_SHA:
        raise ValueError('Owned diagnostic identity mismatch')
    rescue=owned(ram/'rescue')
    if digest(rescue)!=receipt.get('ram_hashes',{}).get('rescue'):raise ValueError('RAM rescue tool changed')
    rows=parse_mounts(command([ram/'mount','-p']).stdout);require_tmpfs(ram,rows)
    physical=physical_mounts(rows,protected=False);all_readonly=all('ro' in row['options'] for row in physical.values())
    if receipt['boot_time']!=command([ram/'sysctl','-n','kern.boottime']).stdout.strip():raise ValueError('Receipt belongs to a different boot')
    if command([ram/'sysctl','-n','kern.bootfile']).stdout.strip()!='/boot/'+KERNEL+'/kernel':raise ValueError('Different kernel is running')
    print(json.dumps({'status':'reboot-stable-requested','rw_restore_supported':False,'ram_directory':str(ram),'quick_without_sync':all_readonly}),flush=True)
    command([ram/'reboot']+(['-qn'] if all_readonly else []),check=False)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('plan','watchdog-source','start','restore','reboot-stable'))
    p.add_argument('--archive',type=Path);p.add_argument('--watchdog',type=Path);p.add_argument('--ram-directory',type=Path)
    args=p.parse_args()
    if args.action=='plan':print(json.dumps(diagnostic_plan(),indent=2));return
    if args.action=='watchdog-source':sys.stdout.buffer.write(WATCHDOG_SOURCE.encode('utf-8'));return
    if not sys.platform.startswith('freebsd'):raise ValueError('Native FreeBSD required')
    if args.action=='start':
        if args.archive is None or args.watchdog is None:p.error('start requires bound --archive and --watchdog')
        start(args.archive,args.watchdog)
    else:
        if args.ram_directory is None:p.error('restore requires --ram-directory')
        reboot_stable(args.ram_directory)

COLLECTOR=r'''#!/bin/sh
set -u
umask 077
R=__RAM__
beat() { printf 'alive\n' >"$R/heartbeat"; }
fail() {
 trap '' HUP INT TERM PIPE
 printf 'failed\n' >"$R/result"
 beat
 for name in __DUMPS__; do "$R/sysctl" "$name" >>"$R/failure-regs.log" 2>&1; done
 "$R/cat" "$R/load.log" "$R/root-read.log" "$R/sample-read.log" "$R/root-before.md5" "$R/root-after.md5" "$R/after-read.log" "$R/failure-regs.log" 2>/dev/null || :
 printf '\nRAM_PROTECTED_FAILURE_REBOOT_STABLE\n'
 "$R/reboot" -qn
 exit 10
}
check_errors() {
 case "$1" in
  *g_vfs_done*|*'I/O error'*|*'Input/output error'*|*'pager read error'*|*'panic:'*|*'CAM status:'*|*'Controller timeout'*|*'controller timeout'*|*mmc*error*|*mmc*timeout*|*sdhci*error*|*sdhci*timeout*) fail ;;
 esac
}
trap fail HUP INT TERM PIPE
beat
# Use a RAM file rather than a pipeline: a successful digest must never hide
# a failed/short device read. md5 is part of the pinned static rescue binary;
# it detects accidental transfer corruption here, not artifact authenticity.
sample_root() {
 "$R/dd" if=/dev/gpt/DaOS-root of="$R/root-sample.bin" bs=64k skip=4096 count=16 >"$R/sample-read.out" 2>"$R/sample-read.log" || return 1
 stats=$("$R/cat" "$R/sample-read.log") || return 1
 case "$stats" in *'1048576 bytes transferred'*) ;; *) return 1;; esac
 "$R/md5" -q "$R/root-sample.bin"
}
baseline=$(sample_root) || fail
[ "${#baseline}" -eq 32 ] || fail
printf '%s\n' "$baseline" >"$R/root-before.md5"
beat
"$R/dmesg" -ac >"$R/before-attach.log" || fail
message=$("$R/cat" "$R/before-attach.log") || fail
check_errors "$message"
"$R/kenv" hw.brcmfmac.country=US || fail
for name in __DUMPS__; do beat; "$R/sysctl" "$name" >>"$R/initial-regs.log" 2>&1 || fail; done
beat
printf 'RAM_RO_VERIFIED_BEGIN_ONE_RADIO_ATTACH\n'
( "$R/kldload" -n "$R/if_brcmfmac.ko" >"$R/load.log" 2>&1; printf '%s\n' "$?" >"$R/load.rc" ) &
loader=$!
( "$R/dd" if=/dev/gpt/DaOS-root of=/dev/null bs=64k skip=4096 count=128 >"$R/root-read.out" 2>"$R/root-read.log"; printf '%s\n' "$?" >"$R/root-read.rc" ) &
i=0
ready=0
while [ "$i" -lt 260 ]; do
 beat
 "$R/sleep" .5
 i=$((i+1))
 "$R/dmesg" -ac >"$R/chunk-$i.log" || fail
 message=$("$R/cat" "$R/chunk-$i.log") || fail
 check_errors "$message"
 if [ -n "$message" ]; then beat; printf '\nRADIO_CHUNK_%s\n' "$i"; "$R/tail" -c 3800 "$R/chunk-$i.log"; fi
 beat
 devices=$("$R/sysctl" -n net.wlan.devices) || fail
 case "$devices" in brcmfmac[0-9]|brcmfmac[0-9][0-9]) ready=$((ready+1));; *) ready=0;; esac
 if [ -f "$R/load.rc" ] && [ "$("$R/cat" "$R/load.rc")" != 0 ]; then fail; fi
 if [ -f "$R/root-read.rc" ] && [ "$("$R/cat" "$R/root-read.rc")" != 0 ]; then fail; fi
 if [ "$ready" -ge 16 ] && [ -f "$R/load.rc" ] && [ -f "$R/root-read.rc" ]; then
  beat
  country=$("$R/sysctl" -n dev.brcmfmac.0.country) || fail
  [ "$country" = US ] || fail
  # The concurrent read may finish before the radio attaches. Require a new
  # physical read after readiness, then inspect errors produced by that read.
  beat
  after=$(sample_root) || fail
  printf '%s\n' "$after" >"$R/root-after.md5"
  if [ "$after" != "$baseline" ]; then
   printf 'ROOT_SAMPLE_MISMATCH_AFTER_RADIO_ATTACH\n'
   fail
  fi
  beat
  "$R/dmesg" -ac >"$R/after-read.log" || fail
  message=$("$R/cat" "$R/after-read.log") || fail
  check_errors "$message"
  printf 'ROOT_SAMPLE_MATCH_AFTER_RADIO_ATTACH\n'
  printf 'protected-ready\n' >"$R/result"
  beat
  "$R/cat" "$R/initial-regs.log" "$R/load.log" "$R/root-read.log"
  beat
  printf '\nPROTECTED_READY SD_REMAINS_READ_ONLY LOGS=%s REBOOTING_STABLE\n' "$R"
  "$R/reboot" -qn
  exit 0
 fi
done
fail
'''
WATCHDOG_SOURCE='/* DaOS RAM-only hardware watchdog. Compile static against the pinned FreeBSD SDK. */\n#include <sys/types.h>\n#include <sys/stat.h>\n#include <sys/mount.h>\n#include <sys/ioctl.h>\n#include <sys/time.h>\n#include <sys/watchdog.h>\n#include <fcntl.h>\n#include <unistd.h>\n#include <time.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <limits.h>\n#include <errno.h>\nstatic const char identity[] = "DaOS-wifi005-ram-watchdog-v1";\nstatic int path(char *out, size_t n, const char *dir, const char *name) {\n    return snprintf(out,n,"%s/%s",dir,name)>=(int)n ? -1 : 0;\n}\nstatic int owned_read(const char *name, char *out, size_t n) {\n    struct stat s; int fd=open(name,O_RDONLY|O_NOFOLLOW);\n    if(fd<0) return -1;\n    if(fstat(fd,&s)||!S_ISREG(s.st_mode)||s.st_uid!=0||s.st_nlink!=1||s.st_size>=(off_t)n) { close(fd);return -1; }\n    ssize_t len=read(fd,out,n-1);close(fd);if(len<0)return -1;\n    while(len>0&&(out[len-1]==\'\\n\'||out[len-1]==\'\\r\'))len--;\n    out[len]=0;return 0;\n}\nstatic int publish(const char *name,const char *text) {\n    int fd=open(name,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);\n    if(fd<0)return -1;\n    size_t n=strlen(text);int bad=write(fd,text,n)!=(ssize_t)n||fsync(fd);close(fd);return bad?-1:0;\n}\nint main(int argc,char **argv) {\n    char resolved[PATH_MAX],hb[PATH_MAX],stop[PATH_MAX],ready[PATH_MAX],pidpath[PATH_MAX],done[PATH_MAX],result[PATH_MAX],text[256];\n    struct stat s;struct statfs fs;sbintime_t current=0,timeout=8*SBT_1S;int soft=0,control=WD_CTRL_ENABLE;\n    fprintf(stderr,"%s\\n",identity);\n    if(argc!=3||geteuid()!=0||strlen(argv[2])!=32||strspn(argv[2],"0123456789abcdef")!=32) return 64;\n    if(strncmp(argv[1],"/tmp/DaOS-wifi005-ro-",21)||!realpath(argv[1],resolved)||strcmp(resolved,argv[1])||\n       lstat(resolved,&s)||!S_ISDIR(s.st_mode)||s.st_uid!=0||(s.st_mode&0777)!=0700||\n       statfs(resolved,&fs)||strcmp(fs.f_fstypename,"tmpfs")) return 65;\n    if(path(hb,sizeof hb,resolved,"heartbeat")||path(stop,sizeof stop,resolved,"watchdog.stop")||\n       path(ready,sizeof ready,resolved,"watchdog.armed")||path(pidpath,sizeof pidpath,resolved,"watchdog.pid")||\n       path(done,sizeof done,resolved,"watchdog.disarmed")||path(result,sizeof result,resolved,"result"))return 65;\n    int fd=open("/dev/fido",O_RDWR);\n    if(fd<0||ioctl(fd,WDIOC_GETTIMEOUT,&current)||current!=0) return 66;\n    if(ioctl(fd,WDIOC_SETSOFT,&soft)||ioctl(fd,WDIOC_SETTIMEOUT,&timeout)||ioctl(fd,WDIOC_CONTROL,&control)||ioctl(fd,WDIOC_SETTIMEOUT,&timeout))return 67;\n    /* From this point, failure/kill never disarms the watchdog. */\n    snprintf(text,sizeof text,"%ld\\n",(long)getpid());if(publish(pidpath,text))return 68;\n    snprintf(text,sizeof text,"ARMED %ld %s\\n",(long)getpid(),argv[2]);if(publish(ready,text))return 68;\n    time_t started=time(NULL);\n    for(;;) {\n        time_t now=time(NULL);\n        if(lstat(hb,&s)||!S_ISREG(s.st_mode)||s.st_uid!=0||s.st_nlink!=1||now<s.st_mtime-2||now-s.st_mtime>4||now-started>300)return 69;\n        if(access(stop,F_OK)==0) {\n            if(owned_read(stop,text,sizeof text)||strcmp(text,argv[2])||owned_read(result,text,sizeof text)||strcmp(text,"protected-ready"))return 70;\n            control=WD_CTRL_DISABLE;if(ioctl(fd,WDIOC_CONTROL,&control))return 71;\n            if(publish(done,"DISARMED\\n"))return 72;\n            close(fd);return 0;\n        }\n        if(ioctl(fd,WDIOC_SETTIMEOUT,&timeout))return 73;\n        sleep(1);\n    }\n}\r\n'

if __name__=='__main__':main()

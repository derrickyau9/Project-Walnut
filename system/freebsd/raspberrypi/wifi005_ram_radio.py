#!/usr/local/bin/python3.12
"""One RAM-only 005 attach with an independent, nonextendable 120s feeder deadline.

Default plan is read-only. arm modifies only resident RAM and arms the separately
pinned watchdog; attach is the sole RF operation. Every armed trial ends by
hardware reset to stable FAT recovery, including a successful attachment.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid

FORMAT='DaOS-wifi005-RAM-radio-v1'
COLLECTOR_SHA='932c3479794989af14100a26b4decfd84b2a0a2dec81994c636201a29a87fc2a'
DEADLINE_SECONDS=120
WDIOC_GETTIMEOUT=0x40085736
WATCHDOG='ram-watchdog-aarch64'
PINS={
    WATCHDOG:(710856,'b852a5db1804b5237e5b97d66e0bd309eda976af58dfd44ce67629c22dd991bc'),
    'if_brcmfmac.ko':(97544,'d30ee8c38e29324ea55bbe95d6b1275bd48b4a66a8502adb93389634b1c8d404'),
    'brcmfmac43455-sdio.bin':(609309,'d608f866582519c0a28d86db43040f4f1b98dd1d153e72e9752586546b4a36c3'),
    'brcmfmac43455-sdio.clm_blob':(2676,'9823842cae9fb9a5dd1e5fb31f595516ec7deee341354bef30bb3026eee29cc1'),
    'brcmfmac43455-sdio.txt':(1883,'edb6f4e4fb19e18940004124feb4ffe160d72fc607243a07a4480338a28b2748')}
FIRMWARE=tuple(n for n in PINS if n.startswith('brcmfmac'))


def load_collector():
    path=Path(__file__).with_name('collect_wifi_005_ram.py')
    # This is an adjacent uploaded source, not an arbitrary import/search path.
    for p in (path,*path.parents):
        s=p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('Untrusted adjacent collector')
    if path.stat().st_nlink!=1 or path.stat().st_size>40000 or hashlib.sha256(path.read_bytes()).hexdigest()!=COLLECTOR_SHA:
        raise ValueError('Pinned adjacent collector changed')
    spec=importlib.util.spec_from_file_location('guarded_wifi005_no_rf',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def paths(c,nonce):
    if not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('Exact current trial nonce required')
    return c.RAM/('wifi005-radio-'+nonce),Path('/tmp/DaOS-wifi005-ro-'+nonce)


def save_new(path,value):
    data=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(data)>24576:raise ValueError('Bounded RAM receipt required')
    temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    created=False
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.link(temporary,path,follow_symlinks=False)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if created:temporary.unlink()


def read_json(c,path,maximum=24576):
    c.trusted(path);info=path.stat()
    if info.st_size>maximum or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('Invalid owned receipt')
    return json.loads(path.read_text())


def watchdog_timeout():
    fd=os.open('/dev/fido',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Watchdog must be a character device')
        data=bytearray(8);fcntl.ioctl(fd,WDIOC_GETTIMEOUT,data,True)
        return struct.unpack('=q',data)[0]
    finally:os.close(fd)


def file_identity(path):
    s=path.lstat()
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,stat.S_IMODE(s.st_mode)]


def asset_check(c,assets,nonce):
    if assets!=c.RAM/('wifi005-rf-assets-'+nonce):raise ValueError('Fixed owned RAM asset directory required')
    c.trusted(assets,directory=True)
    if set(p.name for p in assets.iterdir())!=set(PINS):raise ValueError('Only the five fixed radio inputs are accepted')
    for name,pin in PINS.items():c.pinned(assets/name,pin)
    data=(assets/WATCHDOG).read_bytes()
    if data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<HH',data,16)!=(2,183):raise ValueError('Static ARM64 watchdog required')
    offset=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
    if not count or size<56 or offset+size*count>len(data) or any(struct.unpack_from('<I',data,offset+i*size)[0]==3 for i in range(count)):
        raise ValueError('Watchdog ELF interpreter/program table invalid')


def binary_gate(c):
    rescue=Path('/rescue/rescue');c.trusted(rescue)
    if not os.access(rescue,os.X_OK):raise ValueError('Static rescue binary is not executable')
    identity=rescue.stat()
    for name in ('/rescue/kldload','/rescue/kenv','/rescue/mount'):
        path=Path(name);info=path.lstat()
        # The frozen RAM image intentionally uses these trusted crunched aliases.
        if info.st_uid!=0 or path.resolve(strict=True)!=rescue or not os.access(path,os.X_OK):
            raise ValueError('Expected executable rescue alias missing: '+name)
        actual=path.stat()
        if (actual.st_dev,actual.st_ino)!=(identity.st_dev,identity.st_ino):raise ValueError('Rescue alias target differs')


def plan(c,artifacts,assets,nonce):
    evidence=c.collect(artifacts,nonce)
    binary_gate(c)
    asset_check(c,assets,nonce)
    if watchdog_timeout()!=0:raise ValueError('RAM trial watchdog must initially be unarmed')
    state,run=paths(c,nonce)
    if state.exists() or state.is_symlink() or run.exists() or run.is_symlink():raise ValueError('Trial was already prepared; no same-boot retry')
    return {'format':FORMAT,'status':'planned-no-radio','nonce':nonce,'baseline':evidence,
            'assets':{name:{'bytes':pin[0],'sha256':pin[1]} for name,pin in PINS.items()},
            'feeding_deadline_seconds':DEADLINE_SECONDS,'rf_operations':0,'physical_card_io':False}


def firmware_prepare(c,assets,runner):
    rows=c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
    if 'ro' not in next(row for row in rows if row[1]=='/')[3].split(','):raise ValueError('RAM root must start read-only')
    target=Path('/boot/firmware')
    if target.exists() or target.is_symlink():raise ValueError('Fresh RAM firmware directory required')
    try:
        runner.run(['/rescue/mount','-u','-w','/'],limit=1024)
        target.mkdir(mode=0o755)
        for name in FIRMWARE:
            data=(assets/name).read_bytes();pin=PINS[name]
            if len(data)!=pin[0] or hashlib.sha256(data).hexdigest()!=pin[1]:raise ValueError('Firmware changed before RAM copy')
            fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            c.pinned(target/name,pin)
    finally:
        runner.run(['/rescue/mount','-u','-r','/'],limit=1024)
    after=c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
    if 'ro' not in next(row for row in after if row[1]=='/')[3].split(','):raise ValueError('RAM root RO restoration failed')


def prepare_tmp(c,runner):
    rows=c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
    tmp=[row for row in rows if row[1]=='/tmp']
    if tmp:raise ValueError('Fresh private /tmp RAM mount required')
    runner.run(['/rescue/mount','-t','tmpfs','-o','size=4m,mode=0700','tmpfs','/tmp'],limit=1024)
    rows=c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
    tmp=[row for row in rows if row[1]=='/tmp']
    if len(tmp)!=1 or tmp[0][2]!='tmpfs' or 'rw' not in tmp[0][3].split(','):raise ValueError('Private watchdog tmpfs unavailable')
    c.trusted(Path('/tmp'),directory=True)


def heartbeat(path,nonce,now):
    data=(nonce+' '+format(now,'.6f')+'\n').encode()
    temporary=path.with_name('.heartbeat-'+str(os.getpid()))
    created=False
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush()
        os.replace(temporary,path)
    finally:
        if created:temporary.unlink(missing_ok=True)


def deadline_loop(child,deadline,beat,*,clock=time.monotonic,sleep=time.sleep):
    """Own the direct Popen child; never signal a PID obtained from a receipt."""
    while clock()<deadline:
        beat(clock())
        sleep(min(.5,max(0,deadline-clock())))
    # Popen checks/reaps only its own child before signaling. A running or
    # unreaped exited child retains its PID; an already reaped child is skipped.
    child.send_signal(signal.SIGSTOP)


def child_file_bound():
    resource.setrlimit(resource.RLIMIT_FSIZE,(131072,131072))


def coordinate(c,assets,nonce):
    state,run=paths(c,nonce);c.trusted(state,directory=True);c.trusted(run,directory=True)
    launch=read_json(c,state/'launch.json');now=time.monotonic()
    if launch.get('nonce')!=nonce or launch.get('deadline')!=launch.get('origin',0)+DEADLINE_SECONDS or not launch.get('origin',0)<=now<launch.get('origin',0)+8:
        raise ValueError('Stale or extended launch deadline')
    if any(c.native_kenv(key)!=value for key,value in (
            ('daos_wifi005_trial_nonce',nonce),('daos_recovery','YES'),
            ('hw.bcm2835.sdhci.pio_mode','1'),('daos_wifi005_no_rf','YES'))):
        raise ValueError('Wrong RAM boot nonce/PIO/no-RF environment')
    runner=c.Runner();c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
    md=runner.text(['/sbin/mdconfig','-l','-v','-u','0'],limit=4096)[1].split()
    if len(md)<2 or md[0]!='md0' or md[1] not in ('preload','malloc'):
        raise ValueError('Coordinator root is not resident RAM')
    if runner.text(['/sbin/sysctl','-n','vm.swap_total'],limit=128)[1]!='0' or runner.text(['/sbin/sysctl','-n','kern.bootfile'],limit=1024)[1]!=c.BOOTFILE:
        raise ValueError('Coordinator requires exact 005 kernel and no swap')
    asset_check(c,assets,nonce)
    if watchdog_timeout()!=0:raise ValueError('Watchdog already armed')
    lock=os.open(state/'owner.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    child=None
    try:
        signal.signal(signal.SIGCHLD,signal.SIG_DFL)
        identity=file_identity(assets/WATCHDOG)
        heartbeat(run/'heartbeat',nonce,time.monotonic())
        log=os.open(state/'watchdog.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:child=subprocess.Popen([str(assets/WATCHDOG),str(run),nonce],stdin=subprocess.DEVNULL,stdout=log,stderr=log,preexec_fn=child_file_bound)
        finally:os.close(log)
        ack=run/'watchdog.armed';stop=min(launch['deadline'],time.monotonic()+5)
        while not ack.exists():
            if child.poll() is not None or time.monotonic()>=stop:raise ValueError('Hardware ARMED acknowledgement unavailable')
            heartbeat(run/'heartbeat',nonce,time.monotonic());time.sleep(.05)
        c.trusted(ack)
        if ack.stat().st_size>128 or ack.read_text().strip()!='ARMED '+str(child.pid)+' '+nonce or file_identity(assets/WATCHDOG)!=identity:
            raise ValueError('Watchdog nonce/PID/inode changed')
        if watchdog_timeout()!=8*(1<<32):raise ValueError('Eight-second hardware timeout not confirmed')
        c.pinned(assets/WATCHDOG,PINS[WATCHDOG])
        save_new(state/'ready.json',{'format':FORMAT,'nonce':nonce,'origin':launch['origin'],'deadline':launch['deadline'],
            'owner_pid':os.getpid(),'feeder_pid':child.pid,'watchdog_identity':identity,'owner_lock_identity':file_identity(state/'owner.lock'),
            'armed_ack':ack.read_text().strip(),'ready_monotonic':time.monotonic(),'hardware_seconds':8})
        deadline_loop(child,launch['deadline'],lambda now:heartbeat(run/'heartbeat',nonce,now))
        save_new(state/'deadline-fired.json',{'nonce':nonce,'monotonic':time.monotonic(),'direct_child_stopped':True})
        while True:time.sleep(1)  # retain ownership until the hardware reset
    finally:
        if child is not None:
            try:child.send_signal(signal.SIGSTOP)
            except ProcessLookupError:pass
        os.close(lock)


def arm(c,artifacts,assets,nonce):
    report=plan(c,artifacts,assets,nonce);state,run=paths(c,nonce);runner=c.Runner()
    firmware_prepare(c,assets,runner);prepare_tmp(c,runner)
    os.chmod(assets/WATCHDOG,0o500)
    state.mkdir(mode=0o700);run.mkdir(mode=0o700)
    origin=time.monotonic();save_new(state/'launch.json',{'format':FORMAT,'nonce':nonce,'origin':origin,'deadline':origin+DEADLINE_SECONDS})
    log=os.open(state/'coordinator.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:owner=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).absolute()),'_coordinate','--assets',str(assets),'--trial-nonce',nonce],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    finally:os.close(log)
    stop=time.monotonic()+7
    while not (state/'ready.json').exists():
        if owner.poll() is not None or time.monotonic()>=stop:raise ValueError('Independent deadline owner READY unavailable; do not retry or attach')
        time.sleep(.05)
    ready=verify_ready(c,state,run,nonce)
    return {'format':FORMAT,'status':'armed-no-radio','nonce':nonce,'deadline':ready['deadline'],'owner_pid':ready['owner_pid'],
            'feeder_pid':ready['feeder_pid'],'hardware_seconds':8,'rf_operations':0,'next_action':'one reviewed attach or allow reset; never clear FAT marker'}


def verify_ready(c,state,run,nonce,*,clock=time.monotonic):
    c.trusted(state,directory=True);c.trusted(run,directory=True)
    ready=read_json(c,state/'ready.json');now=clock()
    if ready.get('format')!=FORMAT or ready.get('nonce')!=nonce or ready.get('deadline')!=ready.get('origin',0)+DEADLINE_SECONDS or not ready.get('origin',0)<=now<ready.get('deadline',0)-10:
        raise ValueError('Expired or extended deadline ticket')
    if file_identity(state/'owner.lock')!=ready.get('owner_lock_identity'):raise ValueError('Deadline owner lock changed')
    fd=os.open(state/'owner.lock',os.O_RDWR|os.O_NOFOLLOW)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:raise ValueError('Deadline owner no longer holds its lock')
    finally:os.close(fd)
    c.trusted(run/'heartbeat')
    if (run/'heartbeat').stat().st_size>256:raise ValueError('Oversized heartbeat')
    words=(run/'heartbeat').read_text().split()
    if len(words)!=2 or words[0]!=nonce or not 0<=now-float(words[1])<2:raise ValueError('Stale or wrong-nonce heartbeat')
    c.trusted(run/'watchdog.armed')
    if (run/'watchdog.armed').stat().st_size>128:raise ValueError('Oversized ARMED acknowledgement')
    if (run/'watchdog.armed').read_text().strip()!=ready.get('armed_ack') or ready.get('armed_ack')!='ARMED '+str(ready.get('feeder_pid'))+' '+nonce:
        raise ValueError('ARMED acknowledgement changed')
    for forbidden in ('watchdog.stop','watchdog.disarmed','result'):
        if (run/forbidden).exists() or (run/forbidden).is_symlink():raise ValueError('Unexpected watchdog disarm protocol file')
    if watchdog_timeout()!=8*(1<<32):raise ValueError('Hardware watchdog is no longer armed at eight seconds')
    return ready


def attach(c,artifacts,assets,nonce,country):
    if country!='US':raise ValueError('The reviewed first trial requires explicit US country')
    baseline=c.collect(artifacts,nonce);asset_check(c,assets,nonce)
    state,run=paths(c,nonce);ready=verify_ready(c,state,run,nonce)
    if file_identity(assets/WATCHDOG)!=ready.get('watchdog_identity'):
        raise ValueError('Feeder executable inode changed after ARMED')
    if ready['deadline']-time.monotonic()<60:raise ValueError('Insufficient fixed deadline remaining; no retry')
    for name in FIRMWARE:c.pinned(Path('/boot/firmware')/name,PINS[name])
    save_new(state/'attach.started',{'format':FORMAT,'nonce':nonce,'monotonic':time.monotonic(),'country':'US'})
    # This exclusive receipt is created before the sole attach attempt. Failure
    # never deletes it or disarms the independent coordinator.
    runner=c.Runner(seconds=100)
    runner.run(['/rescue/kenv','hw.brcmfmac.country=US'],limit=1024)
    loadlog=os.open(state/'attach.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:loader=subprocess.Popen(['/rescue/kldload','-n',str(assets/'if_brcmfmac.ko')],stdin=subprocess.DEVNULL,stdout=loadlog,stderr=loadlog,preexec_fn=child_file_bound)
    finally:os.close(loadlog)
    stop=min(ready['deadline']-10,time.monotonic()+90);result='deadline-pending';devices='';tail=''
    stable_since=None;country_observed=None;firmware_line=None
    while time.monotonic()<stop:
        verify_ready(c,state,run,nonce)
        c.isolation(c.mounts(runner.text(['/rescue/mount','-p'],limit=32768)[1]))
        if (state/'attach.log').stat().st_size>131072:raise ValueError('Attach output exceeded bound; reset remains armed')
        log=runner.text(['/rescue/dmesg','-a'],limit=262144)[1];tail=log[-4096:]
        try:c.dmesg_evidence(log)
        except c.Refused:result='kernel-error-reset-pending';break
        devices=runner.text(['/sbin/sysctl','-n','net.wlan.devices'],limit=1024)[1]
        code=loader.poll()
        if code is not None and code!=0:
            result='attach-failed-reset-pending'
            break
        if code==0 and re.fullmatch(r'brcmfmac\d+',devices):
            unit=devices[len('brcmfmac'):]
            country_observed=runner.text(['/sbin/sysctl','-n','dev.brcmfmac.'+unit+'.country'],limit=128)[1]
            firmware=next((line for line in log.splitlines() if line.startswith(devices+': firmware: ')),None)
            firmware_line=firmware[:512] if firmware else None
            if country_observed=='US' and firmware_line:
                if stable_since is None:stable_since=time.monotonic()
                if time.monotonic()-stable_since>=5:
                    result='attached-candidate-reset-pending';break
            else:stable_since=None
        else:stable_since=None
        time.sleep(.5)
    report={'format':FORMAT,'status':result,'nonce':nonce,'rf_operations':1,'country':'US','devices':devices,
            'deadline':ready['deadline'],'physical_card_io':False,'fat_marker_cleared':False,'watchdog_disarmed':False,
            'scan_or_association_requested':False,'dmesg_tail':tail,'instructions':'Capture RAM logs; allow independent deadline to reset to stable FAT rescue'}
    report.update(country_observed=country_observed,firmware_log_line=firmware_line,
                  candidate_stable_seconds=(time.monotonic()-stable_since if stable_since is not None else 0),
                  wifi_repaired=False,network_test_authorized=False)
    save_new(state/'result.json',report);return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',nargs='?',default='plan',choices=('plan','arm','attach','_coordinate'))
    parser.add_argument('--artifacts',type=Path);parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--trial-nonce',required=True);parser.add_argument('--country')
    a=parser.parse_args()
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0 or os.uname().machine not in ('arm64','aarch64'):raise ValueError('Native FreeBSD ARM64 RAM required')
    c=load_collector()
    if a.action=='_coordinate':coordinate(c,a.assets,a.trial_nonce);return 0
    if a.artifacts is None:parser.error('--artifacts is required')
    result={'plan':lambda:plan(c,a.artifacts,a.assets,a.trial_nonce),'arm':lambda:arm(c,a.artifacts,a.assets,a.trial_nonce),
            'attach':lambda:attach(c,a.artifacts,a.assets,a.trial_nonce,a.country)}[a.action]()
    data=json.dumps(result,sort_keys=True,separators=(',',':'))
    if len(data.encode())>24576:raise ValueError('Radio report exceeds bound')
    print(data);return 0


if __name__=='__main__':raise SystemExit(main())

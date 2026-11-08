#!/usr/local/bin/python3.12
"""Pre-rc USB supervisor. All mutable state is private tmpfs, never /var.

The normal rc USB service must yield while `status` returns zero. The same
hook can restart after init's death_single kills every non-init process.
On an ordinary root filesystem Python/libs still require readable root;
the independent recovery mfsroot packages those dependencies into RAM.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time

RAM = Path('/DaOS-recovery-ram')
CONFIG = Path('/etc/daos/recovery-adb.json')
FORMAT = 'DaOS-early-recovery-adb-v1'
STOP = threading.Event()
HEALTH_FORMAT = 'DaOS-early-recovery-adb-health-v1'
DEFAULT_CONFIG = {'format': FORMAT, 'vid': 65535, 'pid': 55813,
                  'authorized_keys': '/etc/daos/adb_keys',
                  'daemon_root': '/usr/local/libexec/daos-adb',
                  'helper': '/usr/local/libexec/daos-usb-adb',
                  'python': '/usr/local/bin/python3.12',
                  'entry': '/usr/local/libexec/DaOS-recovery/early_daemon.py'}


def owned(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Redirected recovery path: '+str(path))
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Recovery files must be owned by root and not writable by others')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Unexpected recovery file type')
    return path


def mount_rows(text):
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 6:
            raise ValueError('Invalid mount table')
        rows.append(fields)
    return rows


def validate_ram(reader=subprocess.check_output):
    owned(RAM, directory=True)
    if stat.S_IMODE(RAM.stat().st_mode) != 0o700:
        raise ValueError('Recovery RAM directory must have mode 0700')
    rows = mount_rows(reader(['/sbin/mount', '-p'], text=True, timeout=5))
    found = [r for r in rows if r[1] == str(RAM)]
    if len(found) != 1 or found[0][2] != 'tmpfs' or 'rw' not in found[0][3].split(','):
        raise ValueError('Recovery state requires its dedicated writable tmpfs')


def load_config(path=CONFIG):
    config = json.loads(owned(path).read_text())
    if config != DEFAULT_CONFIG:
        raise ValueError('Unexpected recovery configuration')
    return config


def helper_command(config):
    if config != DEFAULT_CONFIG:
        raise ValueError('Unexpected recovery configuration')
    for name in ('helper', 'python', 'entry', 'authorized_keys'):
        owned(config[name])
    runtime = owned(config['daemon_root'], directory=True)
    for name in ('__init__.py','auth.py','protocol.py','server.py','shell_service.py','sync_service.py','pty_child.py'):
        owned(runtime/name)
    return [config['helper'], '--vid', hex(config['vid']), '--pid', hex(config['pid']), '--wait', '--',
            config['python'], '-I', '-B', config['entry']]


def configure_usb(runner=subprocess.run, reader=subprocess.check_output, report=None):
    bootfile = reader(['/sbin/sysctl','-n','kern.bootfile'], text=True, timeout=5).strip()
    boot = Path(bootfile)
    matching = boot.parent/'daos_adb_template.ko'
    module = matching if boot.parent.parent == Path('/boot') and matching.is_file() else Path('/boot/modules/daos_adb_template.ko')
    matching_usb=boot.parent/'usb_template.ko'
    usb_module=matching_usb if boot.parent.parent==Path('/boot') and matching_usb.is_file() else Path('/boot/kernel/usb_template.ko')
    if report:report.update('configuring-usb',bootfile=bootfile,usb_template_selected=False,host_configured=False,adb_authenticated=False)
    for name,path in (('usb_template',usb_module),('daos_adb_template',module)):
        # Loader-preloaded modules remain resident after switching to md0. Do
        # not require their original absolute path/module_path inside RAM.
        loaded=runner(['/sbin/kldstat','-q','-m',name],check=False,timeout=5,
                      stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if loaded.returncode!=0:
            # usb_template publishes MODULE_VERSION without DECLARE_MODULE;
            # kldstat -m alone can miss it. -n uses the resident KLD basename.
            loaded=runner(['/sbin/kldstat','-q','-n',name+'.ko'],check=False,timeout=5,
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if loaded.returncode==0:continue
        owned(path)
        if report:report.update('loading-module',module=name,module_file=str(path))
        runner(['/sbin/kldload','-n',str(path)], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    current = reader(['/sbin/sysctl','-n','hw.usb.template'], text=True, timeout=5).strip()
    if current != '55813':
        runner(['/sbin/sysctl','hw.usb.template=55813'], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if report:report.update('template-selected',usb_template_selected=True,last_error=None)


def lock_state(*, blocking=False):
    flags = os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW
    fd = os.open(RAM/'early.lock', flags, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError('Unsafe recovery lock')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
    except BaseException:
        os.close(fd)
        raise
    return fd


def running():
    # flock is the authority; no process is signalled using a stale PID.
    validate_ram()
    try:
        fd = lock_state()
    except BlockingIOError:
        return True
    else:
        os.close(fd)
        return False


def error_details(error):
    result={'type':type(error).__name__,'message':str(error)[:700]}
    if isinstance(error,subprocess.CalledProcessError):
        result['command']=[str(v) for v in error.cmd] if isinstance(error.cmd,(list,tuple)) else str(error.cmd)
        result['returncode']=error.returncode
    stderr=getattr(error,'stderr',None)
    if stderr:
        result['stderr']=(stderr.decode(errors='replace') if isinstance(stderr,bytes) else str(stderr))[-1200:]
    return result


class HealthReporter:
    """RAM diagnostics with bounded console milestones; never claims USB from PID."""
    def __init__(self,ram=RAM,console=Path('/dev/console'),clock=time.time):
        self.ram=Path(ram);self.console=Path(console);self.clock=clock
        self.console_times={}
        self.state={'format':HEALTH_FORMAT,'pid':os.getpid(),'phase':'starting',
                    'usb_template_selected':False,'host_configured':False,'adb_authenticated':False,
                    'helper_pid':None,'last_error':None}
        self.update('starting')

    def update(self,phase,**fields):
        self.state.update(fields,phase=phase,updated_at=self.clock())
        # The dedicated root-only tmpfs is validated before construction.
        # A diagnostic write failure must not tear down an active ADB session.
        temporary=self.ram/('health.'+str(os.getpid())+'.tmp')
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as file:json.dump(self.state,file,sort_keys=True);file.write('\n')
            os.replace(temporary,self.ram/'health.json')
        except OSError:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
        if phase in ('starting','setup-failed','bootstrap-failed','helper-failed','helper-exited',
                     'helper-waiting-host','host-configured','adb-authenticated','auth-denied','stopped'):
            now=self.clock();previous=self.console_times.get(phase)
            if previous is None or now-previous>=30:
                self.console_times[phase]=now
                detail=''
                if fields.get('last_error'):
                    error=fields['last_error'];detail=error.get('stderr') or error.get('message','')
                line='DAOS_USB_RECOVERY phase='+phase
                if detail:line+=' detail='+str(detail).replace('\n',' ')[:400]
                try:
                    with self.console.open('a') as output:output.write(line+'\n')
                except OSError:pass

    def error(self,phase,error):
        self.update(phase,last_error=error_details(error),host_configured=False,adb_authenticated=False,helper_pid=None)

    def observe(self,line):
        if line.startswith('daos-usb-adb: serving peripheral '):
            self.update('host-configured',host_configured=True,adb_authenticated=False,last_error=None)
            return
        try:event=json.loads(line).get('event')
        except (ValueError,AttributeError):return
        if event=='authenticated':
            self.update('adb-authenticated',host_configured=True,adb_authenticated=True,last_error=None)
        elif event=='authorization_denied':
            self.update('auth-denied',adb_authenticated=False)
        elif event=='transport_closed':
            self.update('helper-disconnected',host_configured=False,adb_authenticated=False)


def health():
    state={'format':HEALTH_FORMAT,'phase':'unavailable','host_configured':False,'adb_authenticated':False}
    try:
        alive=running()
        file=RAM/'health.json'
        if file.exists():
            owned(file)
            if file.stat().st_size>16384:raise ValueError('Oversized recovery health report')
            saved=json.loads(file.read_text())
            if saved.get('format')!=HEALTH_FORMAT:raise ValueError('Unexpected recovery health identity')
            state.update(saved)
        state['supervisor_alive']=alive
        if not alive:
            state['host_configured']=False;state['adb_authenticated']=False
        state['ready']=alive and state.get('adb_authenticated') is True and state.get('host_configured') is True
    except (OSError,ValueError) as error:
        state.update(supervisor_alive=False,ready=False,last_error=error_details(error))
    return state


def terminate(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill();child.wait(timeout=5)


def monitor(command, logger, spawn=subprocess.Popen, report=None):
    child = spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
                  env={'PATH':'/rescue:/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/bin',
                       'LD_LIBRARY_PATH':'/lib:/usr/lib:/usr/local/lib',
                       'HOME':'/', 'TMPDIR':str(RAM), 'LC_ALL':'C', 'PYTHONDONTWRITEBYTECODE':'1'})
    selector = selectors.DefaultSelector()
    selector.register(child.stderr, selectors.EVENT_READ)
    pending=bytearray();last_lines=[]
    try:
        logger.info('native USB helper pid=%s; authorized recovery shell; no TCP listener',child.pid)
        if report:report.update('helper-waiting-host',helper_pid=child.pid,host_configured=False,adb_authenticated=False)
        while not STOP.is_set():
            events = selector.select(.25)
            if events:
                data=os.read(child.stderr.fileno(),4096)
                if not data:break
                pending.extend(data)
                while b'\n' in pending:
                    line,_,tail=pending.partition(b'\n');pending[:]=tail
                    message=line.decode(errors='replace');logger.info('transport: %s',message)
                    last_lines.append(message[-400:]);last_lines[:]=last_lines[-4:]
                    if report:report.observe(message)
                if len(pending)>16384:
                    logger.info('transport: oversized diagnostic line discarded');pending.clear()
            if child.poll() is not None:break
    finally:
        selector.close();terminate(child);child.stderr.close()
        if report:
            error=None if STOP.is_set() else {'type':'HelperExit','message':'USB helper exited with status '+str(child.returncode),'stderr':'\n'.join(last_lines)[-1200:]}
            report.update('helper-exited',helper_pid=None,host_configured=False,adb_authenticated=False,helper_returncode=child.returncode,last_error=error)


def serve():
    if not sys.platform.startswith('freebsd') or os.geteuid() != 0:
        raise ValueError('Native FreeBSD root is required')
    validate_ram()
    try:
        fd=lock_state()
    except BlockingIOError:
        return 0
    handler=None;report=None
    try:
        os.umask(0o077)
        report=HealthReporter()
        config=load_config();command=helper_command(config)
        logger=logging.getLogger('DaOS-early-USB');logger.setLevel(logging.INFO)
        handler=RotatingFileHandler(RAM/'early.log',maxBytes=256*1024,backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(handler)
        (RAM/'early.json').write_text(json.dumps({'format':FORMAT,'pid':os.getpid(),'started':time.time(),'auth_required':True,'transport':'native-usb','normal_root_requires_readable_usr':True})+'\n')
        for signum in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            signal.signal(signum,lambda *_: STOP.set())
        while not STOP.is_set():
            try:
                configure_usb(report=report)
            except Exception as error:
                report.error('setup-failed',error)
                logger.exception('early USB retry')
                STOP.wait(2)
                continue
            try:monitor(command,logger,report=report)
            except Exception as error:
                report.error('helper-failed',error)
                logger.exception('early USB helper retry')
            STOP.wait(2)
        return 0
    except Exception as error:
        if report:report.error('bootstrap-failed',error)
        return 1
    finally:
        if report and STOP.is_set():report.update('stopped',host_configured=False,adb_authenticated=False,helper_pid=None)
        if handler is not None:handler.close()
        os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('serve','status','health'))
    args=parser.parse_args()
    if args.action=='status':return 0 if running() else 1
    if args.action=='health':
        state=health();print(json.dumps(state,sort_keys=True));return 0 if state['ready'] else 1
    return serve()


if __name__=='__main__':raise SystemExit(main())

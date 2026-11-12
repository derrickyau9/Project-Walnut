#!/usr/local/bin/python3.12
"""Root Xorg/UID1000 Qt session. No restart, TCP listener or stored cookie."""
from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import secrets
import signal
import stat
import subprocess
import threading
import time

RUNTIME = Path('/var/run/DaOS-camera')
stopping = False
started_at = time.monotonic()
event_lock = threading.Lock()


def event(name, **fields):
    line = 'DaOS_GUI_EVENT ' + json.dumps({'event': name,
        'elapsed_ms': round((time.monotonic()-started_at)*1000, 3),
        'monotonic_ns':time.monotonic_ns(), **fields}, sort_keys=True)
    with event_lock:
        print(line, flush=True)
    # Persistent daemon log only: console writes would overwrite the splash.


def boot_phase(value):
    try: Path('/var/run/DaOS-boot-stage').write_text(str(value)+'\n')
    except OSError: pass


def stop(signum, frame):
    global stopping
    stopping = True


def terminate(process, grace):
    if process is None or process.poll() is not None:
        return False
    # Signal Qt alone first: its finally block owns orderly native shutdown.
    process.terminate()
    try:
        process.wait(timeout=grace)
        return False
    except subprocess.TimeoutExpired:
        event('forced-stop', pid=process.pid)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        return True


class IdentityAudit:
    """Informational whole-kernel hashing runs after UI readiness, not before X.

    Account, mounts, runtime ownership, X authority and native handoff remain
    synchronous admission checks. Never publish a partial or cached digest.
    """
    def __init__(self, identity, paths=None):
        self.identity = identity
        self.paths = paths or {'kernel_sha256':'/boot/kernel/kernel', 'shell_sha256':'/bin/sh'}
        self.cancelled = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name='DaOS-identity-audit', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            values = {}
            for key, path in self.paths.items():
                value = hashlib.sha256()
                with open(path, 'rb') as stream:
                    while True:
                        if self.cancelled.is_set():
                            raise InterruptedError('session stopped before audit completed')
                        block = stream.read(1024*1024)
                        if not block:
                            break
                        value.update(block)
                values[key] = value.hexdigest()
            if not self.cancelled.is_set():
                event('base-identity', **self.identity, **values)
        except (OSError, ValueError) as error:
            event('base-identity-incomplete', error_type=type(error).__name__)

    def close(self):
        self.cancelled.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


def splash_exited(pidfile, *, clock=time.monotonic, sleep=time.sleep, lstat=os.lstat):
    """daemon -P removes its root pidfile after the renderer exits.

    The renderer already observes the stop file every 100 ms. Wait briefly for
    that actual exit instead of always paying rc.subr's service-stop polling.
    A timeout uses the existing bounded service-stop path; it never grants the
    framebuffer while the native renderer is still running.
    """
    deadline=clock()+1
    while True:
        try:info=lstat(pidfile)
        except FileNotFoundError:return True
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('Invalid native splash supervisor pidfile')
        if stopping or clock()>=deadline:return False
        sleep(.02)


def main():
    if platform.system() != 'FreeBSD' or os.geteuid() != 0:
        raise RuntimeError('root session supervision requires native FreeBSD')
    event('session-start')
    camera = pwd.getpwnam('camera')
    if camera.pw_uid != 1000 or camera.pw_gid != 1000:
        raise RuntimeError('unexpected camera account identity')
    identity = dict(system=platform.system(), architecture=platform.machine(),
          release=platform.release(), kernel_ident=subprocess.check_output(['/sbin/sysctl','-n','kern.ident'],text=True,timeout=5).strip(),
          )
    audit = IdentityAudit(identity)
    event('session-identity', **identity, full_digest='pending-ui-ready')
    mounts = subprocess.check_output(['/sbin/mount', '-p'], text=True, timeout=5)
    mounted = {line.split()[1] for line in mounts.splitlines() if len(line.split()) >= 3}
    if not {'/var', '/var/DaOS/calibration', '/var/DaOS/camera/photos'}.issubset(mounted):
        raise RuntimeError('required persistent volumes are not mounted; no storage fallback')
    if Path('/tmp/.X11-unix/X0').exists():
        raise RuntimeError('display :0 is already in use; no other session is terminated')
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o750, exist_ok=True)
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RuntimeError('runtime directory is redirected or not root owned')
    os.chown(RUNTIME, 0, camera.pw_gid); os.chmod(RUNTIME, 0o750)
    lock = (RUNTIME/'session.lock').open('a+b')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client_runtime = RUNTIME/'client'
    client_runtime.mkdir(mode=0o700, exist_ok=True)
    if client_runtime.is_symlink(): raise RuntimeError('client runtime directory is redirected')
    os.chown(client_runtime, camera.pw_uid, camera.pw_gid); os.chmod(client_runtime, 0o700)
    authority = RUNTIME/'Xauthority'
    if authority.exists() or authority.is_symlink():
        old = authority.lstat()
        if not stat.S_ISREG(old.st_mode) or old.st_uid != 0:
            raise RuntimeError('unexpected existing authority file')
        authority.unlink()
    authority.touch(mode=0o600, exist_ok=False)
    cookie = secrets.token_hex(16)
    subprocess.run(['/usr/local/bin/xauth', '-f', str(authority), 'source', '-'],
        input='add :0 MIT-MAGIC-COOKIE-1 '+cookie+'\n', text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=10)
    cookie = None
    os.chown(authority, 0, camera.pw_gid); os.chmod(authority, 0o640)
    for item in (signal.SIGTERM, signal.SIGINT): signal.signal(item, stop)
    server = application = None
    overlap = os.environ.get('DAOS_CAMERA_FASTBOOT') == 'YES'
    display_ready = RUNTIME / ('display-' + secrets.token_hex(8))
    try:
        boot_phase(45)
        command = ['/usr/local/libexec/Xorg', ':0', 'vt9', '-auth', str(authority),
                   '-nolisten', 'tcp', '-noreset', '-background', 'none', '-logfile', '/var/log/DaOS-Xorg.log']
        server = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
        event('Xorg-started', pid=server.pid, qpa='xcb', tcp=False)
        ready_tag = secrets.token_hex(8)
        splash_ready = client_runtime / ('splash-' + ready_tag)
        ui_ready = client_runtime / ('ready-' + ready_tag)
        native_handoff = client_runtime / ('handoff-' + ready_tag)
        environment = {'PATH': '/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/bin:/usr/local/sbin',
            'HOME': '/var/DaOS/home/camera', 'USER': 'camera', 'LOGNAME': 'camera',
            'DISPLAY': ':0', 'XAUTHORITY': str(authority), 'XDG_RUNTIME_DIR': str(client_runtime),
            'XDG_CACHE_HOME': '/var/DaOS/cache', 'QT_QPA_PLATFORM': 'xcb',
            'QT_QUICK_BACKEND': 'software', 'QSG_RENDER_LOOP': 'basic',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'LANG': 'C.UTF-8',
            'DAOS_SPLASH_READY_FILE': str(splash_ready), 'DAOS_UI_READY_FILE': str(ui_ready),
            'DAOS_NATIVE_HANDOFF_FILE': str(native_handoff)}
        if overlap:
            environment['DAOS_X_READY_FILE'] = str(display_ready)
        def launch_application():
            child = subprocess.Popen(['/usr/local/bin/python3.12', '-B', '/usr/local/libexec/DaOS-camera-app.py'],
                env=environment, stdin=subprocess.DEVNULL, user=camera.pw_uid, group=camera.pw_gid,
                extra_groups=[], start_new_session=True)
            event('camera-started', pid=child.pid, uid=camera.pw_uid, gid=camera.pw_gid,
                  architecture=platform.machine(), framebuffer='scfb', physical_camera=False,
                  overlapping_imports=overlap)
            return child
        if overlap:
            application = launch_application()
        deadline = time.monotonic()+30
        while not Path('/tmp/.X11-unix/X0').exists():
            if (stopping or server.poll() is not None or time.monotonic() >= deadline
                    or application is not None and application.poll() is not None):
                raise RuntimeError('Xorg/application did not provide the selected local display')
            time.sleep(.05)
        if overlap:
            fd = os.open(display_ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        else:
            application = launch_application()
        boot_phase(65)
        event('display-ready', overlapping_imports=overlap)
        handed_off = False
        ready_recorded = False
        while not stopping and application.poll() is None and server.poll() is None:
            if not handed_off and splash_ready.is_file():
                Path('/var/run/DaOS-boot-native.stop').touch()
                if Path('/var/run/DaOS-boot-supervisor.pid').is_file():
                    if not overlap or not splash_exited(Path('/var/run/DaOS-boot-supervisor.pid')):
                        stopped=subprocess.run(['/usr/sbin/service', 'DaOS-boot', 'stop'],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                        if overlap and not splash_exited(Path('/var/run/DaOS-boot-supervisor.pid')):
                            raise RuntimeError('Native framebuffer handoff failed')
                fd = os.open(native_handoff, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                handed_off = True
                boot_phase(75)
                event('native-splash-handoff', pid=application.pid)
            if not ready_recorded and ui_ready.is_file():
                ready_recorded = True
                boot_phase(100)
                event('ui-ready', pid=application.pid)
                audit.start()
            time.sleep(.2)
        if not stopping and server.poll() is not None:
            raise RuntimeError('Xorg exited while the camera application was running')
        if not stopping and application.returncode not in (None, 0):
            raise RuntimeError('camera application exited unsuccessfully')
    finally:
        forced = terminate(application, 15)
        terminate(server, 5)
        audit.close()
        if display_ready.exists(): display_ready.unlink()
        if authority.is_file() and not authority.is_symlink(): authority.unlink()
        event('session-stopped', forced_application_stop=forced,
              application_returncode=application.returncode if application else None,
              server_returncode=server.returncode if server else None)
        lock.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        event('session-failed', error_type=type(error).__name__, message=str(error))
        raise SystemExit(1)

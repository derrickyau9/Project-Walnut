#!/usr/pkg/bin/python3.12
"""Root Xorg supervisor with a separate uid 1000 NetBSD Qt application."""
from __future__ import annotations
import fcntl
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
import time

RUNTIME = Path('/var/run/daos-ui')
stopping = False

def require_native():
    if platform.system() != 'NetBSD' or os.geteuid() != 0:
        raise RuntimeError('Native NetBSD/aarch64 root supervision is required')
    architecture = subprocess.check_output(['/sbin/sysctl', '-n', 'hw.machine_arch'], text=True, timeout=5).strip()
    if architecture != 'aarch64':
        raise RuntimeError('Native NetBSD/aarch64 root supervision is required')
    return architecture

def event(name, **fields):
    line = 'DAOS_GUI_EVENT ' + json.dumps({'event': name, **fields}, sort_keys=True)
    print(line, flush=True)
    try:
        descriptor = os.open('/dev/console', os.O_WRONLY | os.O_NONBLOCK)
        try: os.write(descriptor, (line + '\n').encode())
        finally: os.close(descriptor)
    except OSError: pass

def stop(signum, frame):
    global stopping
    stopping = True

def terminate(process, grace):
    if process is None or process.poll() is not None: return False
    process.terminate()
    try:
        process.wait(timeout=grace)
        return False
    except subprocess.TimeoutExpired:
        event('forced-stop', pid=process.pid)
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
        return True

def sha(path):
    value = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()

def directory(path, uid, gid, mode):
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink(): raise RuntimeError('Redirected state directory: ' + str(path))
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid); os.chmod(path, mode)
    return path

def prepare_packages():
    event('package-finalization-started')
    with open('/var/log/daos-ui-packages.log', 'ab', buffering=0) as log:
        process = subprocess.Popen(['/usr/pkg/bin/python3.12', '-B', '/usr/libexec/daos/ui-finalize.py'],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
        try:
            while process.poll() is None and not stopping: time.sleep(.2)
            if stopping:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
                raise RuntimeError('Package finalization interrupted by shutdown')
            if process.returncode != 0:
                raise RuntimeError('Native package finalization failed; see /var/log/daos-ui-packages.log')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
    event('package-finalization-complete')

def main():
    architecture = require_native()
    for item in (signal.SIGTERM, signal.SIGINT): signal.signal(item, stop)
    camera = pwd.getpwnam('daos-camera')
    if camera.pw_uid != 1000 or camera.pw_gid != 1000:
        raise RuntimeError('Unexpected DaOS camera account')
    state, photos = Path('/var'), Path('/var/DaOS/camera/photos')
    if state.is_symlink() or photos.is_symlink(): raise RuntimeError('Redirected persistent volume')
    if state.stat().st_dev == Path('/').stat().st_dev or photos.stat().st_dev == state.stat().st_dev:
        raise RuntimeError('Separate state and photos volumes must be mounted; no storage fallback')
    photo_info = photos.stat()
    if photo_info.st_uid != 1000 or photo_info.st_gid != 1000 or stat.S_IMODE(photo_info.st_mode) != 0o700:
        raise RuntimeError('Photos volume camera ownership/mode mismatch')
    prepare_packages()
    if Path('/tmp/.X11-unix/X0').exists(): raise RuntimeError('Display :0 is already in use')
    event('base-identity', system=platform.system(), release=platform.release(),
          architecture=architecture, kernel_machine=platform.machine(), kernel_sha256=sha('/netbsd'),
          kernel_version=subprocess.check_output(['/sbin/sysctl', '-n', 'kern.version'], text=True).strip(),
          shell_sha256=sha('/bin/sh'), source_built_Qt=False)
    os.umask(0o077)
    for name in ('camera', 'calibration', 'ui', 'cache', 'home/camera'):
        directory('/var/DaOS/' + name, 1000, 1000, 0o700)
    directory(RUNTIME, 0, 1000, 0o750)
    lock = (RUNTIME / 'session.lock').open('a+b')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client_runtime = directory(RUNTIME / 'client', 1000, 1000, 0o700)
    authority = RUNTIME / 'Xauthority'
    if authority.exists() or authority.is_symlink():
        info = authority.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0: raise RuntimeError('Unexpected authority file')
        authority.unlink()
    authority.touch(mode=0o600, exist_ok=False)
    cookie = secrets.token_hex(16)
    subprocess.run(['/usr/X11R7/bin/xauth', '-f', str(authority), 'source', '-'],
                   input='add :0 MIT-MAGIC-COOKIE-1 ' + cookie + '\n', text=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=10)
    cookie = None
    os.chown(authority, 0, 1000); authority.chmod(0o640)
    server = application = None
    try:
        command = ['/usr/X11R7/bin/Xorg', ':0', 'vt1', '-auth', str(authority),
                   '-nolisten', 'tcp', '-noreset', '-logfile', '/var/log/DaOS-Xorg.log']
        server = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
        event('Xorg-started', pid=server.pid, framebuffer='wsfb', tcp=False)
        deadline = time.monotonic() + 45
        while not Path('/tmp/.X11-unix/X0').exists():
            if stopping or server.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Xorg failed to open display :0')
            time.sleep(.1)
        environment = {
            'PATH': '/sbin:/bin:/usr/sbin:/usr/bin:/usr/pkg/bin:/usr/X11R7/bin',
            'HOME': '/var/DaOS/home/camera', 'USER': 'daos-camera', 'LOGNAME': 'daos-camera',
            'DISPLAY': ':0', 'XAUTHORITY': str(authority), 'XDG_RUNTIME_DIR': str(client_runtime),
            'XDG_CACHE_HOME': '/var/DaOS/cache', 'QT_QPA_PLATFORM': 'xcb',
            'QT_PLUGIN_PATH': '/usr/pkg/qt5/plugins', 'QML2_IMPORT_PATH': '/usr/pkg/qt5/qml',
            'QT_QUICK_BACKEND': 'software', 'QSG_RENDER_LOOP': 'basic',
            'FONTCONFIG_FILE': '/usr/share/daos/fonts.conf',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'LANG': 'C.UTF-8'}
        application = subprocess.Popen(['/usr/pkg/bin/python3.12', '-B', '/usr/libexec/daos/camera-app.py'],
                        env=environment, stdin=subprocess.DEVNULL, user=1000, group=1000,
                        extra_groups=[], start_new_session=True)
        event('camera-started', pid=application.pid, uid=1000, framebuffer='wsfb', physical_camera=False)
        while not stopping and application.poll() is None and server.poll() is None: time.sleep(.2)
        if not stopping and server.poll() is not None: raise RuntimeError('Xorg exited during the camera session')
        if not stopping and application.returncode not in (None, 0): raise RuntimeError('Camera UI exited unsuccessfully')
    finally:
        forced = terminate(application, 20)
        terminate(server, 5)
        if authority.is_file() and not authority.is_symlink(): authority.unlink()
        event('session-stopped', forced_application_stop=forced,
              application_returncode=application.returncode if application else None,
              server_returncode=server.returncode if server else None)
        lock.close()
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        event('session-failed', error_type=type(error).__name__, message=str(error))
        raise SystemExit(1)

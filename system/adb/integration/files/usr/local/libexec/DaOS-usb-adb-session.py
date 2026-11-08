#!/usr/local/bin/python3.12
"""Background native USB ADB supervisor; no network listener or key enrollment."""
from __future__ import annotations
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import threading
import time

STOP = threading.Event()
CONFIG = '/usr/local/etc/daos/usb-adb.json'
LOG = '/var/log/DaOS-usb-adb.log'

def early_recovery_running(runner=subprocess.run):
    entry=Path('/usr/local/libexec/DaOS-recovery/early_adb.py')
    if not entry.is_file():return False
    try:
        return runner(['/usr/local/bin/python3.12','-I','-B',str(entry),'status'],
                      timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False

def target_check(runner=subprocess.check_output):
    if platform.system() != 'FreeBSD': raise RuntimeError('native FreeBSD required')
    arch = runner(['/sbin/sysctl', '-n', 'hw.machine_arch'], text=True, timeout=5).strip()
    if arch != 'aarch64': raise RuntimeError('native aarch64 required')
    if os.geteuid() != 0: raise RuntimeError('USB developer service must run as root')

def configure_usb(runner=subprocess.run, reader=subprocess.check_output):
    bootfile = reader(['/sbin/sysctl', '-n', 'kern.bootfile'], text=True, timeout=5).strip()
    matching = Path(bootfile).parent / 'daos_adb_template.ko'
    template = str(matching) if (Path(bootfile).parent.parent == Path('/boot')
                                and matching.is_file() and not matching.is_symlink()) else '/boot/modules/daos_adb_template.ko'
    for module in ('usb_template', template):
        runner(['/sbin/kldload', '-n', module], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    current = reader(['/sbin/sysctl', '-n', 'hw.usb.template'], text=True, timeout=5).strip()
    if current != '55813':
        runner(['/sbin/sysctl', 'hw.usb.template=55813'], check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def helper_command(config):
    if set(config) != {'format', 'vid', 'pid', 'authorized_keys', 'daemon_root'} or config['format'] != 'DaOS-USB-ADB-session-v1':
        raise ValueError('invalid USB developer configuration')
    for name in ('vid', 'pid'):
        if not isinstance(config[name], int) or not 1 <= config[name] <= 65535: raise ValueError('invalid USB identity')
    if config['authorized_keys'] != '/etc/daos/adb_keys' or config['daemon_root'] != '/usr/local/libexec/daos-adb':
        raise ValueError('unexpected ADB source or key path')
    # The application selector chooses a complete, immutable support directory.
    # Image installations without a versioned support tree retain the old path.
    bundled=Path(__file__).resolve().parent/'daos-adb'
    daemon_root=str(bundled) if (bundled/'daos_adbd.py').is_file() and not bundled.is_symlink() else config['daemon_root']
    return ['/usr/local/libexec/daos-usb-adb', '--vid', hex(config['vid']), '--pid', hex(config['pid']), '--wait', '--',
            '/usr/local/bin/python3.12', '-B', daemon_root+'/daos_adbd.py', '--stdio',
            '--authorized-keys', config['authorized_keys'], '--shell', '/bin/sh', '--cwd', '/', '--sync-root', '/']

def stop_child(child):
    if child.poll() is not None: return
    child.terminate()  # helper forwards clean shutdown to authenticated daemon.
    try: child.wait(timeout=5)
    except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)

def serve(command, logger, spawn=subprocess.Popen, retry=2):
    while not STOP.is_set():
        child = None
        try:
            child = spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          close_fds=True, start_new_session=True)
            logger.info('native USB helper started pid=%s; waiting for configured USB host', child.pid)
            selector = selectors.DefaultSelector(); selector.register(child.stderr, selectors.EVENT_READ)
            pending = bytearray()
            try:
                while not STOP.is_set():
                    events = selector.select(0.25)
                    if events:
                        data = os.read(child.stderr.fileno(), 4096)
                        if not data: break
                        pending.extend(data)
                        while b'\n' in pending:
                            line, _, tail = pending.partition(b'\n'); pending[:] = tail
                            logger.info('transport: %s', line.decode('utf-8', errors='replace'))
                        if len(pending) > 16384:
                            logger.info('transport: %s', pending.decode('utf-8', errors='replace')); pending.clear()
                    if child.poll() is not None: break
                if pending: logger.info('transport: %s', pending.decode('utf-8', errors='replace'))
            finally:
                selector.close(); stop_child(child); child.stderr.close()
            logger.info('USB helper stopped status=%s; next connection requires AUTH', child.returncode)
        except Exception:
            logger.exception('native USB helper failed')
            if child is not None: stop_child(child)
        if not STOP.is_set(): STOP.wait(retry)

def main():
    os.umask(0o077)
    if early_recovery_running():return 0
    logger = logging.getLogger('DaOS-usb-adb'); logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG, maxBytes=2*1024*1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s')); logger.addHandler(handler)
    for signo in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(signo, lambda *_: STOP.set())
    try:
        target_check(); config = json.loads(Path(CONFIG).read_text()); command = helper_command(config)
        while not STOP.is_set():
            try: configure_usb(); break
            except Exception: logger.exception('USB template setup failed'); STOP.wait(10)
        if not STOP.is_set(): serve(command, logger)
    except Exception: logger.exception('USB service failed'); return 1
    finally: logger.info('USB developer service stopped'); handler.close()
    return 0

if __name__ == '__main__': raise SystemExit(main())

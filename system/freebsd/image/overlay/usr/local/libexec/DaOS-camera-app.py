#!/usr/local/bin/python3.12
"""Unprivileged Qt entrypoint with a graceful rc.d termination path."""
import os
from pathlib import Path
import platform
import runpy
import signal
import sys

if platform.system() != 'FreeBSD' or os.geteuid() != 1000:
    raise SystemExit('DaOS camera application requires the native unprivileged camera account')

from PyQt5.QtCore import QCoreApplication


def stop(signum, frame):
    app = QCoreApplication.instance()
    if app is None:
        raise SystemExit(128 + signum)
    app.quit()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
program = Path('/opt/hasslefree/ui/on-device/main.py')
sys.path.insert(0, str(program.parent))
if os.environ.get('DAOS_X_READY_FILE'):
    import importlib.util
    import json
    import time
    spec = importlib.util.spec_from_file_location('daos_camera_startup', Path(__file__).with_name('DaOS-camera-startup.py'))
    startup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(startup)
    print('DaOS_BOOT_MARK '+json.dumps({'event':'python-preload-start','monotonic_ns':time.monotonic_ns()}),flush=True)
    startup.preload_modules()
    print('DaOS_BOOT_MARK '+json.dumps({'event':'python-preload-end','monotonic_ns':time.monotonic_ns()}),flush=True)
    startup.wait_for_display(os.environ['DAOS_X_READY_FILE'])
sys.argv = [str(program), '--kiosk', '--native-command', '/usr/local/bin/hf-camerad',
            '--data-dir', '/var/DaOS/camera', '--ui-state-dir', '/var/DaOS/ui']
runpy.run_path(str(program), run_name='__main__')

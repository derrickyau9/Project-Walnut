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
sys.argv = [str(program), '--kiosk', '--native-command', '/usr/local/bin/hf-camerad',
            '--data-dir', '/var/DaOS/camera', '--ui-state-dir', '/var/DaOS/ui']
runpy.run_path(str(program), run_name='__main__')

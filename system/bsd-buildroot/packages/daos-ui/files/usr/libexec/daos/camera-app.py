#!/usr/pkg/bin/python3.12
"""Unprivileged NetBSD entry point for the current R14 camera UI."""
import os
from pathlib import Path
import platform
import runpy
import signal
import subprocess
import sys

def require_native():
    if platform.system() != 'NetBSD' or os.geteuid() != 1000:
        raise RuntimeError('DaOS camera UI requires the native NetBSD/aarch64 camera account')
    architecture = subprocess.check_output(['/sbin/sysctl', '-n', 'hw.machine_arch'], text=True, timeout=5).strip()
    if architecture != 'aarch64':
        raise RuntimeError('DaOS camera UI requires the native NetBSD/aarch64 camera account')
    return architecture

def main():
    require_native()
    from PyQt5.QtCore import QCoreApplication

    def stop(signum, frame):
        app = QCoreApplication.instance()
        if app is None: raise SystemExit(128 + signum)
        app.quit()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    program = Path('/usr/share/daos/camera-app/ui/on-device/main.py')
    sys.path.insert(0, str(program.parent))
    sys.argv = [str(program), '--kiosk', '--native-command', '/usr/libexec/daos/hf-camerad',
                '--data-dir', '/var/DaOS/camera', '--ui-state-dir', '/var/DaOS/ui',
                '--width', '1024', '--height', '768']
    runpy.run_path(str(program), run_name='__main__')

if __name__ == '__main__': main()

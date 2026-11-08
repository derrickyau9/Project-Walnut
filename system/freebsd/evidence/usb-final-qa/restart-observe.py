#!/usr/bin/env python3
"""Restart the owned final USB desktop and immediately observe its BootLogo."""
from pathlib import Path
import subprocess
import sys

project = Path('/mnt/f/Project HasselFree')
work = Path('/home/dy09/daos-vm-usb-adb')
output = work / 'verification/boot-logo-restart-1'
assert not output.exists(), 'Preserve previous observation; choose a new attempt name.'
subprocess.run([sys.executable, '-u', str(project / 'tools/launch_bsd_desktop.py'),
                'start', '--work-dir', str(work), '--image-dir',
                str(project / 'build/DaOS_BSD_RPi4_USB_ADB_ARM64')], check=True)
subprocess.run([sys.executable, '-u', str(project / 'system/bsd-buildroot/verify_logo.py'),
                '--freebsd-desktop', '--run-dir', str(work),
                '--output-dir', str(output), '--seconds', '180'], check=True)

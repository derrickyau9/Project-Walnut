"""Opt-in overlap of unprivileged Python imports with root-owned Xorg startup."""
import importlib
import os
from pathlib import Path
import re
import stat
import time

PRELOAD = ('PyQt5.QtGui','PyQt5.QtQml','PyQt5.QtQuick','controller',
           'shell_model','apps_controller','soft_keyboard','system_settings',
           'terminal_surface','image_pipeline','performance','shooting_workspace',
           'input_router','image_processing','system_control','camera_link','task_hub')


def preload_modules():
    # Import definitions only: no QApplication, service, storage or camera is
    # instantiated here. The real UI is created only after root permits it.
    for name in PRELOAD:
        importlib.import_module(name)


def wait_for_display(marker, *, runtime=Path('/var/run/DaOS-camera'),
                     clock=time.monotonic, sleep=time.sleep, lstat=os.lstat):
    path = Path(marker)
    if path.parent != runtime or not re.fullmatch('display-[0-9a-f]{16}',path.name):
        raise ValueError('Unexpected display handoff path')
    parent = lstat(runtime)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ValueError('Display handoff parent must be an unredirected root-owned directory')
    deadline = clock()+30
    while True:
        try:
            value = lstat(path)
        except FileNotFoundError:
            value = None
        if value is not None:
            if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0
                    or value.st_mode & 0o022 or value.st_nlink != 1 or value.st_size != 0):
                raise ValueError('Invalid root display handoff')
            return
        if clock() >= deadline:
            raise TimeoutError('Xorg display handoff did not arrive')
        sleep(.05)

"""Opt-in overlap of unprivileged Python imports with root-owned Xorg startup."""
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

PRELOAD = ('PyQt5.QtGui','PyQt5.QtQml','PyQt5.QtQuick','controller',
           'shell_model','apps_controller','soft_keyboard','system_settings',
           'terminal_surface','image_pipeline','performance','shooting_workspace',
           'input_router','image_processing','system_control','camera_link','task_hub')


def resolve_program(program):
    """Pin one ready application tree before importing any application modules.

    The stable entrypoint can be the version selector, not the application's
    actual main.py. Execute the selected tree directly after validation so an
    activation-pointer change during preload cannot mix two releases.
    """
    program=Path(program)
    base=program.resolve().parents[2]
    pointer=base/'camera-app-active.json'
    if pointer.is_symlink():raise ValueError('Redirected application selector')
    if not pointer.exists():return program
    def read_record(path):
        if path.is_symlink() or not path.is_file():raise ValueError('Invalid application record')
        with path.open('rb') as stream:data=stream.read(4097)
        if len(data)>4096:raise ValueError('Application record exceeds bound')
        value=json.loads(data)
        if not isinstance(value,dict):raise ValueError('Invalid application record')
        return value
    active=read_record(pointer);version=active.get('version','');digest=active.get('manifest_sha256','')
    if not isinstance(version,str) or not re.fullmatch('camera-experience-[0-9]{3}',version):
        raise ValueError('Invalid application version')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('Invalid application manifest identity')
    release=base/'releases'/version
    if release.is_symlink():raise ValueError('Redirected application release')
    ready=read_record(release/'release-ready.json')
    if ready.get('version')!=version or ready.get('manifest_sha256')!=digest:
        raise ValueError('Application release identity mismatch')
    selected=release/'ui/on-device/main.py'
    if selected.is_symlink() or not selected.is_file():raise ValueError('Application entrypoint missing')
    return selected


def preload_modules(program=None):
    # Import definitions only: no QApplication, service, storage or camera is
    # instantiated here. The real UI is created only after root permits it.
    if program is not None:
        sys.path.insert(0,str(Path(program).resolve().parents[2]))
        sys.path.insert(0,str(Path(program).parent))
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

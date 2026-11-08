"""Explicit QEMU-user ARM64 Python/Qt import probe, not a board or chroot test."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from tools.rootfs_build import digest, json_write, verify_stage

PROGRAM = r'''
import json, pathlib, platform, sqlite3, ssl, sys, zlib
from PyQt5 import QtCore, QtGui, QtQml, QtQuick, QtTest
root = pathlib.Path(sys.argv[1]).resolve()
def identity(path):
    path = pathlib.Path(path).resolve()
    if not path.is_relative_to(root): raise RuntimeError('module escaped staged rootfs: ' + str(path))
    header = path.read_bytes()[:64]
    if len(header) < 64 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
        raise RuntimeError('expected real AArch64 ELF: ' + str(path))
    return str(path.relative_to(root))
if platform.system() != 'Linux' or platform.machine() != 'aarch64': raise RuntimeError('expected emulated ARM64 Linux identity')
modules = {module.__name__: identity(module.__file__) for module in (QtCore,QtGui,QtQml,QtQuick,QtTest)}
app = QtGui.QGuiApplication([])
engine = QtQml.QQmlEngine()
component = QtQml.QQmlComponent(engine)
component.setData(b'import QtQuick 2.15\nimport QtQuick.Controls 2.15\nimport QtQuick.Layouts 1.15\nimport QtQuick.Window 2.15\nItem { width: 64; height: 64; RowLayout { Button { text: "ARM64" } } }', QtCore.QUrl())
instance = component.create()
if instance is None: raise RuntimeError('; '.join(error.toString() for error in component.errors()))
app.processEvents()
print(json.dumps(dict(python=sys.version, executable=identity(sys.executable),machine=platform.machine(),
                     pyqt=QtCore.PYQT_VERSION_STR, qt_compile=QtCore.QT_VERSION_STR,qt_runtime=QtCore.qVersion(),
                     modules=modules, sqlite=sqlite3.sqlite_version,qml_controls_layouts_window_created=True)))
instance.deleteLater()
'''


def probe(rootfs, manifest, output):
    root = Path(rootfs).resolve(); output = Path(output).absolute()
    validated = verify_stage(root, manifest)
    if os.name != 'posix' or platform.system() != 'Linux': raise ValueError('explicit user emulation probe requires Linux host')
    emulator = shutil.which('qemu-aarch64')
    if not emulator: raise ValueError('qemu-aarch64 is required; no binfmt fallback')
    if output.exists() or output.is_symlink(): raise ValueError('probe evidence must be a new file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hf-rootfs-probe-') as temporary:
        scratch = Path(temporary); runtime = scratch / 'run'; runtime.mkdir(mode=0o700)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(scratch), 'XDG_CACHE_HOME': str(scratch / 'cache'),
               'XDG_RUNTIME_DIR': str(runtime), 'PYTHONHOME': str(root / 'usr'),
               'PYTHONPATH': str(root / 'usr/lib/python3/dist-packages'), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'LD_LIBRARY_PATH': str(root / 'usr/lib/aarch64-linux-gnu'), 'QEMU_LD_PREFIX': str(root),
               'QT_PLUGIN_PATH': str(root / 'usr/lib/aarch64-linux-gnu/qt5/plugins'),
               'QML2_IMPORT_PATH': str(root / 'usr/lib/aarch64-linux-gnu/qt5/qml'),
               'FONTCONFIG_PATH': str(root / 'etc/fonts'), 'FONTCONFIG_FILE': str(root / 'etc/fonts/fonts.conf'),
               'QT_QPA_FONTDIR': str(root / 'usr/share/fonts/truetype/dejavu'),
               'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software', 'QSG_RENDER_LOOP': 'basic'}
        command = [emulator, '-L', str(root), str(root / 'usr/bin/python3'), '-B', '-c', PROGRAM, str(root)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        if result.returncode: raise RuntimeError('actual ARM64 Python/Qt probe failed: ' + result.stderr[-12000:])
        runtime_result = json.loads(result.stdout)
    final = verify_stage(root, manifest)
    if final != validated: raise ValueError('probe changed immutable staged rootfs')
    evidence = {'format': 'hf-arm64-qt-rootfs-probe-v1', 'passed': True, 'user_mode_emulation': True,
                'full_system_guest': False, 'physical_board_tested': False, 'host_machine': platform.machine(),
                'emulator': {'path': emulator, 'sha256': digest(emulator),
                             'version': subprocess.check_output([emulator, '--version'], text=True).splitlines()[0]},
                'stage_manifest_sha256': digest(manifest), 'rootfs': validated, 'runtime': runtime_result,
                'stderr': result.stderr, 'staged_tree_unchanged': True}
    json_write(output, evidence); print(json.dumps(evidence, indent=2)); return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True); parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); probe(args.rootfs, args.manifest, args.output)


if __name__ == '__main__': main()

"""Install only enumerated application sources into an owned Buildroot target.

No project build output, personal/runtime data, generated photos, imported
hardware, git metadata, test environment or old Debian binary is considered.
The native ELFs have already been compiled/installed by the package recipe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


TREES = ('camera', 'simulation', 'daos/capability_runtime', 'daos/console',
         'daos/camera-contracts', 'ui/on-device')
SUFFIXES = {'.py', '.qml', '.js', '.html', '.css', '.json', '.svg'}
SKIP_PARTS = {'__pycache__', '.git', '.venv', 'venv', 'node_modules', 'build',
              'var', 'captures', 'evidence', 'native', 'tests'}
LICENSE_FILES = {
    'project-rights.json': 'licenses/INVENTORY.json',
    'design-provenance.md': 'docs/design-provenance.md',
    'nlohmann-LICENSE.MIT': 'camera/native/third_party/nlohmann/LICENSE.MIT',
    'sqlite3-public-domain-header.h': 'camera/native/third_party/sqlite/sqlite3.h',
}
MAX_FILES = 512
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('application input must be a regular non-link file: ' + str(path))
    data = path.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('application input exceeds bounded file size')
    return data


def write_owned(path, data, target):
    # Buildroot owns the target. Refuse unexpected links rather than following
    # an app-directory substitution into another part of the host filesystem.
    relative = path.relative_to(target)
    current = target
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError('linked application installation directory')
        current.mkdir(exist_ok=True)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError('non-regular application installation destination')
    path.write_bytes(data)
    path.chmod(0o644)


def install(project_root, target_dir):
    root = Path(project_root).resolve(strict=True)
    target = Path(target_dir).resolve(strict=True)
    if not root.is_dir() or not target.is_dir() or root == target:
        raise ValueError('distinct project and Buildroot target directories required')
    inputs = {}
    for name in TREES:
        source = root / name
        if source.is_symlink() or not source.is_dir():
            raise ValueError('required application source tree missing or linked')
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(root)
            if SKIP_PARTS.intersection(relative.parts):
                continue
            if path.is_symlink():
                raise ValueError('linked application source entry')
            if path.is_dir() or path.suffix not in SUFFIXES:
                continue
            inputs[relative.as_posix()] = regular_bytes(path)
    # The namespace package is present in the current source; include it when
    # supplied, while accepting Python's intentional namespace-package form.
    if (root / 'daos/__init__.py').exists():
        inputs['daos/__init__.py'] = regular_bytes(root / 'daos/__init__.py')
    if not 1 <= len(inputs) <= MAX_FILES or sum(map(len, inputs.values())) > MAX_SOURCE_BYTES:
        raise ValueError('application input bounds exceeded')
    required = {'camera/camerad/native_client.py', 'camera/camerad/server.py',
                'simulation/bayer.py', 'ui/on-device/Main.qml', 'ui/on-device/controller.py',
                'ui/on-device/main.py', 'daos/console/index.html'}
    if not required.issubset(inputs):
        raise ValueError('incomplete runnable camera application source set')
    app = target / 'opt/hasslefree'
    expected = set(inputs) | {'BUILDROOT-APPLICATION-INPUTS.json'}
    if app.exists():
        if app.is_symlink():
            raise ValueError('linked application root')
        for path in app.rglob('*'):
            if path.is_symlink() or path.is_file() and path.relative_to(app).as_posix() not in expected:
                raise ValueError('unexpected existing application entry; use a fresh Buildroot target')
    hashes = {name: digest(data) for name, data in inputs.items()}
    for name, data in inputs.items():
        write_owned(app / name, data, target)
    for name, source in LICENSE_FILES.items():
        write_owned(target / 'usr/share/licenses/hasslefree-camera' / name,
                    regular_bytes(root / source), target)
    # Check the actual source bytes again so a concurrently edited UI cannot
    # silently produce a mixed input commitment.
    if any(digest(regular_bytes(root / name)) != checksum for name, checksum in hashes.items()):
        raise ValueError('application sources changed during installation; rebuild after freezing inputs')
    native = {}
    for name in ('hf-camerad', 'hf-native-worker'):
        path = target / 'usr/bin' / name
        data = regular_bytes(path)
        if not data.startswith(b'\x7fELF'):
            raise ValueError('native application is not an ELF')
        native[name] = digest(data)
    manifest = {'format': 'hf-buildroot-application-inputs-v1', 'simulated': True,
                'physical_camera_validated': False, 'daos_framework_source_imported': False,
                'source_sha256': hashes,
                'native_sha256_before_buildroot_final_strip': native,
                'rights_inventory': '/usr/share/licenses/hasslefree-camera/project-rights.json',
                'rights_status': 'project grants pending; third-party terms retained'}
    write_owned(app / 'BUILDROOT-APPLICATION-INPUTS.json',
                (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode(), target)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--target-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.project_root, args.target_dir), indent=2, sort_keys=True))

"""Validate and launch a static ARM64 Linux synthetic simulator package.

Execution requires a real Linux aarch64/arm64 Python host identity. There is no
emulator selection, architecture override or automatic fallback in this runtime.
"""
from __future__ import annotations

import os
import sys

if __name__ == '__main__' and sys.platform != 'linux':
    print('execution requires a Linux aarch64/arm64 Python host; no emulation fallback is provided', file=sys.stderr)
    raise SystemExit(2)

# Run this entry point without inherited Python overlays or site hooks. This
# occurs before imports such as platform/pathlib can be shadowed by PYTHONPATH.
if __name__ == '__main__' and (not sys.flags.isolated or not sys.flags.no_site):
    os.execv(sys.executable, [sys.executable, '-I', '-S', '-B', os.path.abspath(__file__), *sys.argv[1:]])

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import stat

FORMAT = 'hf-static-arm64-sim-package-v1'
TARGET = 'aarch64-linux-static-simulator'
MAX_BYTES = 512 * 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate package JSON member')
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite package JSON')))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_package(directory):
    """Cross-host integrity/ELF validation; this never executes a target binary."""
    from tools.arm64_elf import inspect_arm64_elf
    original = Path(directory)
    if original.is_symlink():
        raise ValueError('use an explicit release directory, not a symlink')
    root = original.resolve(strict=True)
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError('missing or oversized static ARM64 package manifest')
    manifest = _json(manifest_path.read_bytes())
    fields = {'format', 'package_id', 'target', 'architecture', 'simulated', 'python_min',
              'project_rights', 'native_evidence_sha256', 'files'}
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise ValueError('unsupported static ARM64 package fields')
    if (manifest['format'] != FORMAT or manifest['target'] != TARGET or manifest['architecture'] != 'aarch64' or
            manifest['simulated'] is not True or manifest['python_min'] != '3.10' or manifest['project_rights'] != 'pending'):
        raise ValueError('incompatible static ARM64 simulator package profile')
    if not isinstance(manifest['package_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', manifest['package_id']):
        raise ValueError('invalid package identity')
    entries = manifest['files']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4096:
        raise ValueError('invalid package inventory count')
    expected, total = set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256', 'mode'}:
            raise ValueError('invalid package inventory entry')
        name = entry['path']
        if not isinstance(name, str) or '\\' in name or ':' in name or '\x00' in name:
            raise ValueError('unsafe package path')
        relative = PurePosixPath(name)
        if (relative.is_absolute() or relative.as_posix() != name or '..' in relative.parts or not relative.parts or
                relative.parts[0] not in {'bin', 'runtime', 'notices'} or name in expected):
            raise ValueError('unsafe or duplicate package path')
        if type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_BYTES or type(entry['mode']) is not int or entry['mode'] not in (0o644, 0o755):
            raise ValueError('invalid package length or mode')
        if not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise ValueError('invalid package digest')
        total += entry['size']
        if total > MAX_BYTES:
            raise ValueError('package byte budget exceeded')
        path = root.joinpath(*relative.parts)
        if any(component.is_symlink() for component in (path, *path.parents) if component != root.parent):
            raise ValueError('package symlinks are not allowed')
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size != entry['size'] or file_hash(path) != entry['sha256']:
            raise ValueError('package file missing, changed or not regular: ' + name)
        expected.add(name)
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('package symlinks are not allowed')
        if path == manifest_path:
            continue
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError('special files are not allowed in an application package')
    if actual != expected:
        raise ValueError('undeclared package files')
    required = {'bin/hf-camerad', 'bin/hf-native-worker', 'runtime/camera/camerad/server.py',
                'runtime/system/arm64/runtime.py', 'runtime/tools/arm64_elf.py', 'runtime/tools/diagnostics.py',
                'notices/arm64-build-evidence.json', 'notices/project-rights.md',
                'notices/gcc-cross-copyright.txt', 'notices/glibc-cross-copyright.txt'}
    required.update('runtime/' + name + '/__init__.py' for name in ('camera', 'daos', 'simulation', 'system', 'tools'))
    if not required.issubset(expected) or {name for name in expected if name.startswith('bin/')} != {'bin/hf-camerad', 'bin/hf-native-worker'}:
        raise ValueError('static ARM64 package runtime is incomplete')
    evidence_path = root / 'notices/arm64-build-evidence.json'
    if file_hash(evidence_path) != manifest['native_evidence_sha256']:
        raise ValueError('native build evidence digest mismatch')
    evidence = _json(evidence_path.read_bytes())
    if (not isinstance(evidence, dict) or evidence.get('format') != 'hf-arm64-cross-build-v1' or evidence.get('tests_passed') is not True or
            evidence.get('target_triple') != 'aarch64-linux-gnu' or not evidence.get('native_input_sha256')):
        raise ValueError('successful source-bound ARM64 evidence is required')
    for name in ('hf-camerad', 'hf-native-worker'):
        path = root / 'bin' / name
        inspect_arm64_elf(path, require_static=True)
        if file_hash(path) != evidence.get('artifacts', {}).get(name, {}).get('sha256'):
            raise ValueError('release executable differs from the tested native artifact')
    return {'format': FORMAT, 'package_id': manifest['package_id'], 'architecture': 'aarch64',
            'simulated': True, 'project_rights': 'pending', 'files': len(entries), 'bytes': total,
            'manifest_sha256': file_hash(manifest_path), 'native_evidence_sha256': manifest['native_evidence_sha256']}


def require_native_host():
    machine = platform.machine().lower()
    if sys.platform != 'linux' or machine not in ('aarch64', 'arm64'):
        raise RuntimeError('execution requires a Linux aarch64/arm64 Python host; no emulation fallback is provided')
    if os.geteuid() == 0:
        raise RuntimeError('run the simulator as a nonroot user')
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required')


def storage_path(release_root, data_dir):
    data = Path(data_dir).expanduser()
    if not data.is_absolute():
        raise ValueError('persistent data must be an explicit absolute path')
    release, data = Path(release_root).resolve(), data.resolve()
    if data == release or release in data.parents or data in release.parents:
        raise ValueError('persistent data must be separate from the application release')
    for ancestor in (data, *data.parents):
        manifest_path = ancestor / 'manifest.json'
        if manifest_path.is_file() and manifest_path.stat().st_size <= 1024 * 1024:
            candidate = _json(manifest_path.read_bytes())
            if isinstance(candidate, dict) and candidate.get('format') in (FORMAT, 'hf-sim-host-bundle-v1'):
                raise ValueError('persistent data cannot be inside an application package')
        if ancestor != data and (ancestor / 'journal.sqlite3').exists():
            raise ValueError('persistent data cannot nest inside another camera journal')
    return data


def launch_plan(release_root, data_dir, port=8787):
    require_native_host()  # Before touching data or interpreting a release as runnable.
    validate_package(release_root)
    release = Path(release_root).resolve()
    data = storage_path(release, data_dir)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('port must be 1024-65535')
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('camera.camerad.server',run_name='__main__')"
    return release / 'runtime', data, [sys.executable, '-I', '-S', '-B', '-c', bootstrap, str(release / 'runtime'),
           '--authority', 'native', '--native-daemon', str(release / 'bin/hf-camerad'),
           '--data-dir', str(data), '--port', str(port)]


def main():
    # Prevent imports or the server from adding bytecode to the immutable release.
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ.pop('PYTHONPATH', None)
    os.environ.pop('PYTHONHOME', None)
    if __package__ in (None, ''):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    runtime, data, command = launch_plan(args.release_root, args.data_dir, args.port)
    os.umask(0o077)
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(runtime)
    os.execv(sys.executable, command)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)

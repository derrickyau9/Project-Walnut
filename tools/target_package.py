"""Stage/validate a distinct static ARM64 simulator release, without installing it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

from system.arm64.runtime import FORMAT, TARGET, file_hash, validate_package
from tools.arm64_build import source_inputs
from tools.arm64_elf import inspect_arm64_elf


def stage(root, native_dir, output, *, package_id):
    root, native_dir = Path(root).resolve(), Path(native_dir).resolve()
    original_output = Path(output).absolute()
    if not isinstance(package_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', package_id):
        raise ValueError('invalid package identity')
    if original_output.exists() or original_output.is_symlink():
        raise ValueError('release output must be new; existing releases are never overwritten')
    output = original_output.resolve()
    if output == root or (root in output.parents and output.relative_to(root).parts[0] != 'build'):
        raise ValueError('stage outside the source tree or under its dedicated build directory')
    for ancestor in output.parents:
        if (ancestor / 'journal.sqlite3').exists():
            raise ValueError('release output cannot be inside camera data')
        manifest_path = ancestor / 'manifest.json'
        if manifest_path.is_file() and manifest_path.stat().st_size <= 1024 * 1024:
            value = json.loads(manifest_path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and value.get('format') in (FORMAT, 'hf-sim-host-bundle-v1'):
                raise ValueError('release output cannot be inside another application package')
    evidence_path = native_dir / 'arm64-build-evidence.json'
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    expected_inputs = source_inputs(root)
    if (evidence.get('format') != 'hf-arm64-cross-build-v1' or evidence.get('tests_passed') is not True or
            evidence.get('target_triple') != 'aarch64-linux-gnu' or evidence.get('native_input_sha256') != expected_inputs):
        raise ValueError('current source-bound tested ARM64 build evidence is required')
    files = []
    for name in ('hf-camerad', 'hf-native-worker'):
        path = native_dir / name
        inspect_arm64_elf(path, require_static=True)
        if file_hash(path) != evidence.get('artifacts', {}).get(name, {}).get('sha256'):
            raise ValueError('ARM64 executable changed after its tested build')
        files.append((path, 'bin/' + name, 0o755))
    for component in ('camera', 'simulation', 'daos'):
        for path in sorted((root / component).rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.html', '.css', '.js') and '__pycache__' not in path.parts:
                files.append((path, 'runtime/' + path.relative_to(root).as_posix(), 0o644))
    files.append((root / 'system/arm64/runtime.py', 'runtime/system/arm64/runtime.py', 0o644))
    for name in ('__init__.py', 'arm64_elf.py', 'diagnostics.py'):
        path = root / 'tools' / name
        if name != '__init__.py' or path.is_file():
            files.append((path, 'runtime/tools/' + name, 0o644))
    files.extend([
        (evidence_path, 'notices/arm64-build-evidence.json', 0o644),
        (root / 'licenses/README.md', 'notices/project-rights.md', 0o644),
        (root / 'camera/native/third_party/README.md', 'notices/native-third-party.md', 0o644),
        (root / 'camera/native/third_party/manifest.json', 'notices/native-source-manifest.json', 0o644),
        (root / 'camera/native/third_party/nlohmann/LICENSE.MIT', 'notices/nlohmann-LICENSE.MIT', 0o644),
    ])
    for name in ('gcc-cross-copyright.txt', 'glibc-cross-copyright.txt', 'sources.json'):
        files.append((root / 'system/arm64/notices' / name, 'notices/' + name, 0o644))
    for path, _, _ in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError('release input is missing or linked: ' + str(path))
    output.mkdir(parents=True)
    entries = []
    for source, relative, mode in sorted(files, key=lambda item: item[1]):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(mode)
        digest = file_hash(destination)
        if relative.startswith('bin/') and digest != evidence['artifacts'][destination.name]['sha256']:
            raise ValueError('staged native executable differs from tested artifact')
        entries.append({'path': relative, 'size': destination.stat().st_size, 'sha256': digest, 'mode': mode})
    # Regular package markers prevent an unrelated installed/PYTHONPATH package
    # from winning over a verified namespace directory. Never alter source dirs.
    inventory = {entry['path'] for entry in entries}
    for name in ('camera', 'daos', 'simulation', 'system', 'tools'):
        relative = 'runtime/' + name + '/__init__.py'
        if relative not in inventory:
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# Verified static ARM64 simulator runtime package.\n')
            path.chmod(0o644)
            entries.append({'path': relative, 'size': path.stat().st_size, 'sha256': file_hash(path), 'mode': 0o644})
    if source_inputs(root) != expected_inputs:
        raise ValueError('native inputs changed during release staging')
    manifest = {'format': FORMAT, 'package_id': package_id, 'target': TARGET, 'architecture': 'aarch64',
                'simulated': True, 'python_min': '3.10', 'project_rights': 'pending',
                'native_evidence_sha256': file_hash(output / 'notices/arm64-build-evidence.json'), 'files': entries}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return validate_package(output)


def emulation_smoke(release, *, qemu='qemu-aarch64'):
    """Host Python + explicit external QEMU launcher; never a native-host claim."""
    import os
    import platform
    import shlex
    import signal
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    import urllib.request
    import uuid
    from camera.camerad.native_client import NativeClient
    from camera.dng.inspect import inspect_dng
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('the explicit emulation smoke requires a nonroot Linux host')
    release = Path(release).resolve()
    verified = validate_package(release)
    before = {path.relative_to(release).as_posix(): file_hash(path) for path in release.rglob('*') if path.is_file()}
    emulator = shutil.which(qemu)
    if not emulator:
        raise RuntimeError('explicit qemu-aarch64 executable not found')
    with tempfile.TemporaryDirectory(prefix='hf-static-arm64-package-') as temporary:
        temporary = Path(temporary)
        data, refused_data = temporary / 'persistent-camera', temporary / 'wrong-host-must-not-exist'
        wrong_host = None
        if platform.machine().lower() not in ('aarch64', 'arm64'):
            refused = subprocess.run([sys.executable, '-I', '-S', '-B', str(release / 'runtime/system/arm64/runtime.py'),
                                      '--release-root', str(release), '--data-dir', str(refused_data)],
                                     capture_output=True, text=True, timeout=10)
            if refused.returncode != 2 or refused_data.exists() or 'requires a Linux aarch64/arm64 Python host' not in refused.stderr:
                raise RuntimeError('normal native runtime did not refuse the actual wrong host')
            wrong_host = True
        launcher = temporary / 'external-camerad-emulator'
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(emulator) + ' ' + shlex.quote(str(release / 'bin/hf-camerad')) + ' "$@"\n')
        launcher.chmod(0o755)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        bootstrap = ("import pathlib,runpy,sys; r=pathlib.Path(sys.argv.pop(1));sys.path.insert(0,str(r));"
                     "import camera,daos,simulation,system,tools;"
                     "assert all(pathlib.Path(m.__file__).resolve().is_relative_to(r) for m in (camera,daos,simulation,system,tools));"
                     "print('HF_ARM64_PACKAGE_HOST_IMPORTS_OK',flush=True);"
                     "runpy.run_module('camera.camerad.server',run_name='__main__')")
        environment = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
        environment.pop('PYTHONPATH', None); environment.pop('PYTHONHOME', None)
        command = [sys.executable, '-I', '-S', '-B', '-c', bootstrap, str(release / 'runtime'),
                   '--authority', 'native', '--native-daemon', str(launcher), '--data-dir', str(data), '--port', str(port)]
        server = subprocess.Popen(command, cwd=release / 'runtime', env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        endpoint = 'http://127.0.0.1:' + str(port)
        def request(path, body=None, *, binary=False):
            call = urllib.request.Request(endpoint + path, data=None if body is None else json.dumps(body).encode(),
                                          headers={'Content-Type': 'application/json'})
            with opener.open(call, timeout=5) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise RuntimeError('smoke response exceeds bound')
                return raw if binary else json.loads(raw)
        output = errors = ''
        try:
            until = time.monotonic() + 10
            while True:
                if server.poll() is not None:
                    raise RuntimeError('packaged server exited before readiness')
                try:
                    state = request('/api/v1/state'); break
                except OSError:
                    if time.monotonic() >= until: raise RuntimeError('packaged server startup timeout')
                    time.sleep(.05)
            if state.get('authority') != 'native' or state.get('journal_healthy') is not True:
                raise RuntimeError('packaged server did not select a healthy native authority')
            lease = request('/api/v1/control/acquire', {'owner': 'explicit-arm64-package-emulation'})
            rid = str(uuid.uuid4())
            body = {'request_id': rid, 'sensor_mode': 'sim-small', 'requested_exposure_us': 10000,
                    'requested_analogue_gain': 1.0, 'output_format': 'dng', 'simulation_scene': 'scene',
                    'token': lease['token'], 'control_epoch': lease['epoch'], 'deadline_unix_ms': int(time.time() * 1000) + 10000}
            request('/api/v1/captures', body)
            until = time.monotonic() + 10
            while True:
                record = request('/api/v1/captures/' + rid)
                if record['status'] not in ('accepted', 'capturing'): break
                if time.monotonic() >= until: raise RuntimeError('packaged capture timeout; no resubmission')
                time.sleep(.01)
            if record['status'] != 'complete': raise RuntimeError('packaged synthetic capture failed')
            listing = request('/api/v1/captures?limit=5')
            if rid not in [item['request_id'] for item in listing['items']]:
                raise RuntimeError('packaged retained listing omitted the completed capture')
            thumbnail = request('/api/v1/captures/' + rid + '/thumbnail.png', binary=True)
            exported = request('/api/v1/captures/' + rid + '/file.dng', binary=True)
            if not thumbnail.startswith(b'\x89PNG\r\n\x1a\n') or exported != (data / 'photos' / (rid + '.dng')).read_bytes():
                raise RuntimeError('packaged thumbnail/export content mismatch')
            exported_path = temporary / 'exported.dng'; exported_path.write_bytes(exported)
            inspected = inspect_dng(exported_path)
            if inspected['request_id'] != rid or (inspected['width'], inspected['height']) != (128, 96):
                raise RuntimeError('packaged export identity/dimensions mismatch')
            request('/api/v1/control/release', {'token': lease['token'], 'epoch': lease['epoch']})
        finally:
            if server.poll() is None:
                server.send_signal(signal.SIGINT)
            try:
                output, errors = server.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); output, errors = server.communicate(timeout=5)
        if server.returncode != 0 or output.count('HF_ARM64_PACKAGE_HOST_IMPORTS_OK') != 1:
            raise RuntimeError('packaged import proof or clean server shutdown failed: ' + errors[-2000:])
        with NativeClient(data, [emulator, str(release / 'bin/hf-camerad')]) as resumed:
            if resumed.query_capture(rid) != record:
                raise RuntimeError('completed capture changed across packaged service restart')
        if validate_package(release) != verified or before != {path.relative_to(release).as_posix(): file_hash(path) for path in release.rglob('*') if path.is_file()}:
            raise RuntimeError('emulation smoke changed the immutable release')
        return {'format': 'hf-static-arm64-package-emulation-v1', 'passed': True, 'simulated': True,
                'mode': 'host Python with explicit external QEMU user launcher', 'host_python_machine': platform.machine(),
                'native_arm_host_runtime_executed': False, 'physical_board_tested': False,
                'package': verified, 'wrong_host_refused_without_data_creation': wrong_host,
                'packaged_imports_verified': True, 'pythonpath_removed_and_site_disabled': True,
                'capture_status': record['status'], 'request_id': rid, 'retained_listing': True,
                'thumbnail_sha256': file_hash_bytes(thumbnail), 'export_sha256': file_hash_bytes(exported),
                'export_bytes': len(exported), 'restart_query_same_record': True, 'release_bytes_unchanged': True,
                'emulator': emulator, 'emulator_version': subprocess.check_output([emulator, '--version'], text=True).splitlines()[0]}


def file_hash_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('stage')
    command.add_argument('--native-dir', type=Path, default=Path('build/arm64-native'))
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--package-id', required=True)
    command = sub.add_parser('validate'); command.add_argument('release', type=Path)
    command = sub.add_parser('emulation-smoke'); command.add_argument('release', type=Path)
    command.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'stage':
        result = stage(Path(__file__).resolve().parents[1], args.native_dir, args.output, package_id=args.package_id)
    elif args.action == 'validate': result = validate_package(args.release)
    else:
        if Path(args.release).resolve() in args.report.resolve().parents:
            raise ValueError('emulation report must be outside the immutable release')
        result = emulation_smoke(args.release)
        with args.report.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""DaOS BSD Buildroot: source-locked BSD base, native packages and board images.

On this Windows workstation the command delegates to the F-backed Debian WSL
filesystem. Linux can also invoke it directly. No Linux Buildroot or FreeBSD
binary is used to satisfy a NetBSD build stage.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
DEFAULT_WORKSPACE = '/home/dy09/daos-bsd-buildroot-ui'
DEFAULT_USB_WORKSPACE = '/home/dy09/daos-bsd-buildroot-usb-scratchguard-002'
DEFAULT_PROFILE = 'rpi4-aarch64-usb'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def module(name):
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module('backends.' + name)


def profile(value):
    path = Path(value) if value.endswith('.json') else HERE / 'targets' / value / 'profile.json'
    path = path.resolve(strict=True)
    data = read(path)
    backend = data.get('backend')
    if data.get('format') != 'DaOS-bsd-build-profile-v1' or backend not in ('netbsd', 'freebsd_usb'):
        raise ValueError('A supported DaOS BSD source-build profile is required')
    machine = 'evbarm' if backend == 'netbsd' else 'arm64'
    if data['target'].get('architecture') != 'aarch64' or data['target'].get('machine') != machine:
        raise ValueError('The selected backend requires its explicit AArch64 machine profile')
    if backend == 'netbsd':
        for item in data['packages']:
            recipe = read(HERE / 'packages' / item / 'recipe.json')
            if recipe['name'] != item or recipe['target'].get('system') != 'NetBSD':
                raise ValueError('Package target mismatch: ' + item)
    lock = (path.parent / data['source_lock']).resolve(strict=True)
    source = read(lock)
    if backend == 'netbsd' and source.get('upstream', {}).get('name') != 'NetBSD':
        raise ValueError('NetBSD upstream lock required')
    if backend == 'freebsd_usb' and source.get('format') != 'DaOS-freebsd-base-source-v2':
        raise ValueError('Pinned FreeBSD source archive lock required')
    return path, data, lock


def windows_storage(distro):
    """Read physical placement as well as WSL's virtual free-space figure."""
    powershell = 'powershell.exe' if os.name == 'nt' else '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    script = r"""
$ErrorActionPreference='Stop'
$items=@(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object { $v=Get-ItemProperty $_.PSPath; [ordered]@{name=$v.DistributionName;base_path=$v.BasePath} })
$vol=@(Get-Volume -DriveLetter F,C | ForEach-Object { [ordered]@{drive=$_.DriveLetter;free_bytes=$_.SizeRemaining;size_bytes=$_.Size} })
[ordered]@{distributions=$items;volumes=$vol} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.check_output([powershell, '-NoProfile', '-NonInteractive', '-Command', script], text=True, timeout=30)
    result = json.loads(result.lstrip('\ufeff'))
    selected = next((d for d in result['distributions'] if d['name'] == distro), None)
    if not selected or not selected['base_path'].upper().replace('\\?\\', '').startswith('F:'):
        raise ValueError('The selected WSL filesystem must reside on F: ' + repr(selected))
    volume = next(v for v in result['volumes'] if v['drive'] == 'F')
    if volume['free_bytes'] < 40 * 1024**3:
        raise ValueError('At least 40 GiB of physical F: space is required')
    return result


def doctor(workspace, distro):
    p = Path(workspace).absolute()
    existing = p
    while not existing.exists():
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    if disk.free < 40 * 1024**3:
        raise ValueError('At least 40 GiB free in the build filesystem is required')
    if str(p).startswith('/mnt/c/') or str(p) in ('/', '/home', '/tmp'):
        raise ValueError('Use a private F-backed Linux workspace, not C: or a shared root')
    missing = [name for name in ('sh', 'make', 'cc', 'c++', 'tar', 'gzip', 'curl') if not shutil.which(name)]
    if missing:
        raise ValueError('Missing host tools: ' + ', '.join(missing))
    result = {'format': 'DaOS-BSD-host-preflight-v1', 'workspace': str(p),
              'linux_free_bytes': disk.free, 'host_system': os.uname().sysname,
              'host_machine': os.uname().machine, 'target_system': 'NetBSD',
              'target_binaries_executed_on_host': False}
    if 'microsoft' in Path('/proc/version').read_text().lower():
        result['windows_storage'] = windows_storage(distro)
    return result


def run(command):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def package_inputs(profile_path, data, lock_path, base):
    backend = module('netbsd')
    spec = backend._profile_inputs(profile_path)
    backend._configured(base, spec)
    distribution = read(base / 'state/distribution.json')
    if distribution.get('status') != 'success' or distribution.get('input_hash') != spec['input_hash']:
        raise ValueError('A completed source-built distribution is required before packages')
    backend._cached_artifacts(distribution, base)
    paths = {p for p in (HERE / 'packages').rglob('*') if p.is_file()
             and not p.is_symlink() and '__pycache__' not in p.parts}
    for folder in ('camera', 'daos', 'simulation', 'ui'):
        paths.update(p for p in (PROJECT / folder).rglob('*') if p.is_file()
                     and not p.is_symlink() and p.suffix in ('.py', '.qml', '.json', '.svg', '.html', '.css', '.js', '.c', '.cpp', '.h', '.hpp')
                     and '__pycache__' not in p.parts
                     and not any(part.startswith('evidence') for part in p.relative_to(PROJECT).parts))
    paths.update(PROJECT / name for name in ('tools/__init__.py', 'tools/diagnostics.py'))
    paths.update(p for p in (PROJECT / 'camera/native/third_party').rglob('*')
                 if p.is_file() and ('LICENSE' in p.name.upper() or p.name == 'manifest.json'))
    return {'profile_sha256': sha(profile_path), 'source_lock_sha256': sha(lock_path),
            'coordinator_sha256': sha(HERE / 'daos.py'),
            'distribution_receipt_sha256': sha(base / 'state/distribution.json'),
            'source_sha256': {str(p.relative_to(PROJECT)): sha(p) for p in sorted(paths)}}


def validate_package_set(profile_path, data, lock_path, workspace):
    """Check a selected overlay against current inputs without staging packages."""
    backend = module('netbsd')
    base = backend._workspace(workspace)
    with backend._locked(base):
        selected = backend._inside(base, base / 'packages/package-set.json')
        if not selected.is_file():
            raise ValueError('Stage packages before assembling an image')
        previous = read(selected)
        if previous.get('inputs') != package_inputs(profile_path, data, lock_path, base):
            raise ValueError('Package sources/configuration changed; run packages before image')
        module('image')._packages(base)
        return previous


def archive_package_set(base, previous, inputs, validated):
    """Retain a verified old selection and every original build attempt."""
    import uuid
    backend = module('netbsd')
    output = backend._inside(base, base / 'packages')
    history = backend._inside(base, output / 'history' / uuid.uuid4().hex)
    history.mkdir(parents=True)
    receipt_path = history / 'history.json'
    moves = [(output / name, history / name) for name in ('overlay', 'manifests', 'package-set.json')]
    for source, destination in moves:
        backend._inside(base, source)
        backend._inside(base, destination)
        if not source.exists() or destination.exists():
            raise ValueError('Package history paths changed before archiving')
    receipt = {'format': 'DaOS-BSD-package-history-v1', 'status': 'running',
               'operation': 'archive_verified_package_set', 'executed': True,
               'started_at': backend._now(), 'previous_inputs': previous['inputs'],
               'next_inputs': inputs, 'previous_set_sha256': sha(output / 'package-set.json'),
               'original_attempt': previous.get('attempt'),
               'verified_payloads_sha256': backend._identity(validated[1]),
               'verified_manifests': validated[2], 'moves': []}
    backend._write_json(receipt_path, receipt)
    try:
        for source, destination in moves:
            os.rename(source, destination)
            receipt['moves'].append({'from': str(source), 'to': str(destination)})
            backend._write_json(receipt_path, receipt)
        receipt.update(status='success', finished_at=backend._now())
        backend._write_json(receipt_path, receipt)
        return history
    except BaseException as error:
        receipt.update(status='failed', error=str(error), finished_at=backend._now())
        backend._write_json(receipt_path, receipt)
        raise


def install_packages(profile_path, data, lock_path, workspace, jobs):
    """Stage a revision under the workspace lock, retaining verified history."""
    backend = module('netbsd')
    base = backend._workspace(workspace)
    with backend._locked(base):
        return _install_packages_locked(profile_path, data, lock_path, base, jobs)


def _install_packages_locked(profile_path, data, lock_path, base, jobs):
    backend = module('netbsd')
    output = backend._inside(base, base / 'packages')
    output.mkdir(exist_ok=True)
    inputs = package_inputs(profile_path, data, lock_path, base)
    selected = backend._inside(base, output / 'package-set.json')
    history = None
    if selected.exists():
        previous = read(selected)
        validated = module('image')._packages(base)
        if previous.get('inputs') == inputs:
            return dict(previous, cached=True, executed=False)
        history = archive_package_set(base, previous, inputs, validated)
    if (output / 'overlay').exists() or (output / 'manifests').exists():
        raise ValueError('Package overlay already exists; preserve it and use a new workspace/revision')
    import uuid
    attempt = backend._inside(base, output / 'attempts' / uuid.uuid4().hex)
    attempt.mkdir(parents=True)
    build_dir = attempt / 'camera-build'
    overlay = attempt / 'overlay'
    manifests = attempt / 'manifests'
    overlay.mkdir()
    manifests.mkdir()
    camera = HERE / 'packages/daos-camera/build.py'
    if 'daos-camera' in data['packages']:
        run([sys.executable, camera, 'build', '--project-root', PROJECT,
             '--toolchain-dir', base / 'tools', '--sysroot', base / 'destdir',
             '--build-dir', build_dir, '--jobs', str(min(jobs, 16))])
        run([sys.executable, camera, 'install', '--project-root', PROJECT,
             '--build-dir', build_dir, '--destdir', overlay, '--with-tests',
             '--with-ui-source', '--manifest', manifests / 'daos-camera.json'])
    if 'daos-ui' in data['packages']:
        ui = HERE / 'packages/daos-ui'
        run([sys.executable, ui / 'install.py', 'fetch', '--lock', ui / 'runtime.lock.json',
             '--cache', base / 'ui-packages', '--jobs', str(min(jobs, 8))])
        run([sys.executable, ui / 'install.py', 'stage', '--lock', ui / 'runtime.lock.json',
             '--cache', base / 'ui-packages', '--base', base / 'destdir',
             '--destdir', overlay, '--manifest', manifests / 'daos-ui.json'])
    upstream = read(lock_path)['upstream']
    release = attempt / 'release-input.json'
    write_new(release, {'version': '0.1.0', 'base': {'name': 'NetBSD', 'version': upstream['release'],
              'source_lock_sha256': sha(lock_path)}, 'profile': data['name'],
              'source_built_base': True, 'original_daos_typescript_runtime_integrated': False,
              'ui_runtime_included': 'daos-ui' in data['packages'], 'target_execution_validated': False})
    command = [sys.executable, HERE / 'packages/daos-core/install.py', '--destdir', overlay,
               '--release', release, '--manifest', manifests / 'daos-core.json']
    for item in sorted(manifests.glob('*.json')):
        command.extend(['--component-manifest', item])
    run(command)
    if package_inputs(profile_path, data, lock_path, base) != inputs:
        raise ValueError('Package inputs changed during staging; failed attempt is preserved')
    published = []
    try:
        for source, destination in ((overlay, output / 'overlay'), (manifests, output / 'manifests')):
            os.rename(backend._inside(base, source), backend._inside(base, destination))
            published.append((source, destination))
        module('image')._packages(base)
        if package_inputs(profile_path, data, lock_path, base) != inputs:
            raise ValueError('Package inputs changed during final validation; failed attempt is preserved')
        result = {'format': 'DaOS-BSD-package-set-v1', 'inputs': inputs, 'attempt': str(attempt),
                  'overlay': str(output / 'overlay'), 'manifests': str(output / 'manifests'),
                  'target_tests_executed': False, 'executed': True,
                  'previous_revision': str(history) if history else None}
        write_new(output / 'package-set.json', result)
        return result
    except BaseException as error:
        rollback = []
        if selected.exists():
            destination = backend._inside(base, attempt / 'failed-package-set.json')
            os.rename(selected, destination)
            rollback.append({'from': str(selected), 'to': str(destination)})
        for source, destination in reversed(published):
            if destination.exists() and not source.exists():
                os.rename(destination, source)
                rollback.append({'from': str(destination), 'to': str(source)})
        write_new(attempt / 'publish-failure.json', {'format': 'DaOS-package-publish-failure-v1',
                  'status': 'failed', 'error': str(error), 'at': backend._now(),
                  'inputs': inputs, 'rollback': rollback})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'plan', 'status', 'fetch', 'configure', 'build', 'packages', 'image', 'all', 'revise'))
    parser.add_argument('--profile', default=DEFAULT_PROFILE)
    parser.add_argument('--workspace', help='Defaults to a separate workspace for the selected BSD backend')
    parser.add_argument('--jobs', type=int, default=12)
    parser.add_argument('--stage', choices=('tools', 'world', 'distribution', 'kernel', 'release', 'base', 'usb', 'all'), default='all')
    parser.add_argument('--distro', default='Debian')
    parser.add_argument('--apply', action='store_true', help='Apply a reviewed configuration revision (revise only)')
    parser.add_argument('--rebuild-base', action='store_true', help='Explicitly rebuild the FreeBSD source world/kernel in a fresh workspace')
    parser.add_argument('--guest-image-work-dir', help='New private FreeBSD image work directory beneath the profile work root')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error('jobs must be between 1 and 32')
    if args.command == 'all' and args.stage != 'all':
        parser.error('Use build --stage for an individual base stage; all builds a complete image')
    if args.apply and args.command != 'revise':
        parser.error('--apply is available only for revise')
    if os.name == 'nt':
        windows_storage(args.distro)
        from vm import windows_path
        source = windows_path(str(HERE / 'daos.py'))
        selected_profile = windows_path(args.profile) if args.profile.endswith('.json') else args.profile
        if args.workspace is None:
            selected_path = Path(args.profile) if args.profile.endswith('.json') else HERE / 'targets' / args.profile / 'profile.json'
            selected_backend = read(selected_path).get('backend')
            args.workspace = DEFAULT_USB_WORKSPACE if selected_backend == 'freebsd_usb' else DEFAULT_WORKSPACE
        command = ['wsl', '-d', args.distro, '--exec', 'python3', source, args.command,
                   '--profile', selected_profile, '--workspace', windows_path(args.workspace),
                   '--jobs', str(args.jobs), '--stage', args.stage, '--distro', args.distro]
        if args.apply:
            command.append('--apply')
        if args.rebuild_base:
            command.append('--rebuild-base')
        if args.guest_image_work_dir:
            command += ['--guest-image-work-dir', args.guest_image_work_dir]
        raise SystemExit(subprocess.call(command))
    profile_path, data, lock_path = profile(args.profile)
    backend_name = data.get('backend', 'netbsd')
    if args.workspace is None:
        args.workspace = DEFAULT_USB_WORKSPACE if backend_name == 'freebsd_usb' else DEFAULT_WORKSPACE
    if backend_name == 'netbsd' and (args.rebuild_base or args.stage in ('base', 'usb') or args.guest_image_work_dir):
        parser.error('The selected NetBSD profile uses tools/world/distribution/kernel/release stages')
    if backend_name == 'freebsd_usb' and args.stage not in ('all', 'base', 'usb'):
        parser.error('The FreeBSD USB backend uses base, usb or all build stages')
    if args.command == 'revise':
        if backend_name != 'netbsd':
            parser.error('Use a new USB workspace for a changed profile; source base reuse is verified there')
        revision = module('revise')
        review = revision.plan(profile_path, args.workspace)
        result = revision.apply(profile_path, args.workspace, revision_hash=review['revision_hash']) if args.apply else review
        print(json.dumps(result, indent=2))
        return
    if args.command == 'status':
        base = Path(args.workspace)
        def compact(item):
            return {key: item[key] for key in ('operation', 'stage', 'status', 'finished_at',
                    'receipt', 'log', 'executed', 'reused', 'verification_executed',
                    'world_recompiled', 'kernel_recompiled', 'inherited_from', 'error') if key in item}
        stages = {p.stem: compact(read(p)) for p in sorted((base / 'state').glob('*.json'))}
        receipts = [read(p) for p in sorted((base / 'receipts').glob('*.json'))]
        failed = [compact(item) for item in receipts if item.get('status') == 'failed']
        running = [compact(item) for item in receipts if item.get('status') == 'running']
        print(json.dumps({'workspace': str(base), 'profile': data['name'], 'stages': stages,
                          'running_receipts': running, 'failed_attempts': failed,
                          'physical_board_validated': False}, indent=2))
        return
    if args.command == 'plan':
        if backend_name == 'freebsd_usb':
            print(json.dumps(module('freebsd_usb').plan(profile_path, args.workspace, jobs=args.jobs,
                             rebuild_base=args.rebuild_base, guest_work_dir=args.guest_image_work_dir), indent=2))
            return
        print(json.dumps({'executed': False, 'profile': data, 'source_lock': read(lock_path),
                          'workspace': args.workspace, 'stages': ['fetch', 'configure', 'tools',
                          'distribution', 'kernel', 'packages', 'image'],
                          'base_plan': module('netbsd').plan(profile_path, args.workspace,
                                                           jobs=args.jobs, stage=args.stage),
                          'Qt_runtime_origin': 'NetBSD pkgsrc binary packages' if 'daos-ui' in data['packages'] else None}, indent=2))
        return
    backend = module(backend_name)
    workspace = backend._workspace(args.workspace)
    host = doctor(workspace, args.distro)
    if args.command == 'doctor':
        print(json.dumps(host, indent=2))
        return
    workspace.mkdir(parents=True, exist_ok=True)
    if backend_name == 'freebsd_usb':
        if args.command == 'fetch':
            print(json.dumps(backend.fetch(profile_path, workspace, rebuild_base=args.rebuild_base), indent=2))
        if args.command in ('configure', 'all'):
            print(json.dumps(backend.configure(profile_path, workspace, rebuild_base=args.rebuild_base), indent=2))
        if args.command in ('build', 'all'):
            if args.stage in ('base', 'all'):
                print(json.dumps(backend.build_base(profile_path, workspace, jobs=min(args.jobs, 16),
                                                   rebuild_base=args.rebuild_base), indent=2))
            if args.stage in ('usb', 'all'):
                print(json.dumps(backend.build_usb(profile_path, workspace, jobs=min(args.jobs, 16)), indent=2))
        if args.command in ('packages', 'all'):
            print(json.dumps(backend.packages(profile_path, workspace), indent=2))
        if args.command in ('image', 'all'):
            print(json.dumps(backend.image(profile_path, workspace, guest_work_dir=args.guest_image_work_dir), indent=2))
        return
    if args.command in ('fetch', 'all'):
        print(json.dumps(backend.fetch(lock_path, workspace), indent=2))
    if args.command in ('configure', 'all'):
        print(json.dumps(backend.configure(profile_path, workspace), indent=2))
    if args.command in ('build', 'all'):
        print(json.dumps(backend.build_base(profile_path, workspace, jobs=args.jobs,
                                           stage=args.stage), indent=2))
    if args.command in ('packages', 'all'):
        print(json.dumps(install_packages(profile_path, data, lock_path, workspace, args.jobs), indent=2))
    if args.command in ('image', 'all'):
        validate_package_set(profile_path, data, lock_path, workspace)
        print(json.dumps(module('image').build(profile_path, workspace), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS BSD Buildroot: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

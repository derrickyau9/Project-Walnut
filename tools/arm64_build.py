"""Cross-build static AArch64 synthetic camera executables and test with QEMU user mode.

This is explicit user-mode emulation on a Linux host. It supplies no BSP, sensor
driver, physical board validation, real-time result or actual camera hardware.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

from tools.arm64_elf import inspect_arm64_elf
from tools.arm64_toolchain import prepare, sha256
from tools.linux_build import native_input_hashes


def source_inputs(root):
    root = Path(root).resolve()
    result = native_input_hashes(root)
    paths = list((root / 'tools').glob('arm64_*.py')) + list((root / 'tests').glob('test_arm64_*.py'))
    paths.append(root / 'system/arm64/toolchain-lock.json')
    for path in paths:
        result[path.relative_to(root).as_posix()] = sha256(path)
    return dict(sorted(result.items()))


def tree_fingerprint(directory):
    directory = Path(directory).resolve()
    entries = [(path.relative_to(directory).as_posix(), sha256(path))
               for path in sorted(directory.rglob('*')) if path.is_file()]
    if not entries:
        raise RuntimeError('empty target header directory: ' + str(directory))
    return {'root': str(directory), 'files': len(entries),
            'sha256': hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()}


def tool(path):
    executable = shutil.which(path)
    if not executable:
        raise RuntimeError('required installed Linux tool not found: ' + path)
    # clang++ is commonly a symlink to clang. Resolving its invocation name
    # changes driver mode and omits the C++ runtime at link time.
    return str(Path(executable).absolute())


def build(root, destination, tools_dir, *, jobs=2, rpc_tests=True):
    if sys.platform != 'linux':
        raise RuntimeError('ARM64 cross compilation/emulation requires an actual Linux host process')
    root, destination = Path(root).resolve(), Path(destination).resolve()
    extra = prepare(tools_dir, root / 'system/arm64/toolchain-lock.json')
    inputs = source_inputs(root)
    cc, cxx, qemu = tool('aarch64-linux-gnu-gcc'), tool('clang++'), tool('qemu-aarch64')
    triple = subprocess.check_output([cc, '-dumpmachine'], text=True).strip()
    if triple != 'aarch64-linux-gnu':
        raise RuntimeError('installed C compiler is not the expected AArch64 GNU target')
    sysroot = Path('/usr/aarch64-linux-gnu')
    headers = Path(extra['headers'])
    libstd = Path(extra['library'])
    if not (sysroot / 'include/stdio.h').is_file() or not (sysroot / 'lib/libc.a').is_file():
        raise RuntimeError('installed ARM64 GNU C sysroot/static libc is missing')
    fingerprint = {'cross_cxx_headers': tree_fingerprint(headers),
                   'cross_c_headers': tree_fingerprint(sysroot / 'include'),
                   'clang_resource_headers': tree_fingerprint(Path(subprocess.check_output([cxx, '-print-resource-dir'], text=True).strip()) / 'include')}
    critical = {'libstdc++.a': libstd, 'libc.a': sysroot / 'lib/libc.a', 'libm.a': sysroot / 'lib/libm.a'}
    for name in ('libgcc.a', 'libgcc_eh.a', 'crtbeginT.o', 'crtend.o'):
        path = Path(subprocess.check_output([cc, '-print-file-name=' + name], text=True).strip())
        if not path.is_file():
            raise RuntimeError('missing cross runtime input: ' + name)
        critical[name] = path
    for name in ('crt1.o', 'crti.o', 'crtn.o'):
        critical[name] = sysroot / 'lib' / name
    runtime_hashes = {name: {'path': str(path.resolve()), 'sha256': sha256(path)} for name, path in critical.items()}
    destination.mkdir(parents=True, exist_ok=True)
    evidence_path = destination / 'arm64-build-evidence.json'
    evidence_path.unlink(missing_ok=True)
    source = root / 'camera/native'
    commands = []

    def run(command, *, capture=False, env=None, timeout=180):
        command = [str(part) for part in command]
        print('+ ' + shlex.join(command), flush=True)
        commands.append(command)
        return subprocess.run(command, cwd=root, env=env, check=True, timeout=timeout,
                              capture_output=capture, text=capture)

    target = ['--target=aarch64-linux-gnu', '--gcc-toolchain=/usr', '--rtlib=libgcc']
    cxx_headers = ['-nostdinc++', '-isystem', str(headers), '-isystem', str(headers / 'aarch64-linux-gnu'),
                   '-isystem', str(headers / 'backward')]
    includes = ['-I' + str(source / 'include'), '-I' + str(source / 'third_party'), '-I' + str(source / 'third_party/sqlite')]
    flags = ['-std=c++17', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Wpedantic', '-ffp-contract=off', '-pthread']
    units = [(name, source / ('tests/' if name.startswith('test_') else 'src/') / (name + '.cpp'))
             for name in ('core', 'dng', 'daemon', 'worker', 'camerad', 'sha256', 'calibration', 'test_core', 'test_daemon', 'test_sha256', 'test_calibration')]
    compilation = [[cxx, *target, *cxx_headers, *flags, *includes, '-c', path, '-o', destination / (name + '.o')]
                   for name, path in units]
    compilation.append([cc, '-O2', '-DNDEBUG', '-DSQLITE_THREADSAFE=1', '-DSQLITE_DQS=0',
                        '-DSQLITE_OMIT_LOAD_EXTENSION=1', '-pthread', '-c', source / 'third_party/sqlite/sqlite3.c',
                        '-o', destination / 'sqlite3.o'])
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        list(executor.map(run, compilation))
    groups = {'hf-core-tests': ['test_core', 'core'],
              'hf-daemon-tests': ['test_daemon', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
              'hf-sha256-tests': ['test_sha256', 'sha256'],
              'hf-calibration-tests': ['test_calibration', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
              'hf-native-worker': ['worker', 'dng', 'core'],
              'hf-camerad': ['camerad', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration']}
    artifacts = {}
    for name, objects in groups.items():
        path = destination / name
        run([cxx, *target, '-L' + str(libstd.parent), *(destination / (item + '.o') for item in objects),
             '-static', '-pthread', '-o', path])
        artifacts[name] = {'sha256': sha256(path), 'bytes': path.stat().st_size,
                           'elf': inspect_arm64_elf(path, require_static=True)}
    tests = {}
    for name, executable in (('native_core', 'hf-core-tests'), ('native_authority', 'hf-daemon-tests'), ('sha256', 'hf-sha256-tests')):
        result = run([qemu, destination / executable], capture=True)
        print(result.stdout, end='', flush=True)
        if 'PASS:' not in result.stdout:
            raise RuntimeError('ARM64 native test pass marker missing')
        tests[name] = {'command': [qemu, str(destination / executable)], 'returncode': result.returncode,
                       'stdout': result.stdout, 'stderr': result.stderr}
    command = [qemu, destination / 'hf-calibration-tests', root / 'camera/calibration/fixtures']
    result = run(command, capture=True)
    print(result.stdout, end='', flush=True)
    if 'PASS:' not in result.stdout: raise RuntimeError('ARM64 calibration test pass marker missing')
    tests['calibration'] = {'command': [str(item) for item in command], 'returncode': result.returncode,
                            'stdout': result.stdout, 'stderr': result.stderr}
    launchers = {}
    for name in ('hf-camerad', 'hf-native-worker'):
        path = destination / (name + '-qemu')
        path.write_text('#!/bin/sh\nexec ' + shlex.quote(qemu) + ' ' + shlex.quote(str(destination / name)) + ' "$@"\n')
        path.chmod(0o755)
        launchers[name] = {'path': str(path), 'sha256': sha256(path), 'mode': 'explicit qemu user-mode launcher'}
    environment = dict(os.environ, HF_NATIVE_DAEMON=launchers['hf-camerad']['path'],
                       HF_NATIVE_WORKER=launchers['hf-native-worker']['path'])
    if rpc_tests:
        for pattern in ('test_native_daemon.py', 'test_native_settings.py', 'test_native_calibration_binding.py', 'test_arm64_*.py'):
            result = run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'],
                         capture=True, env=environment)
            print(result.stdout + result.stderr, end='', flush=True)
            tests[pattern] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    if source_inputs(root) != inputs:
        raise RuntimeError('native/ARM64 build inputs changed during build/test; freeze and rebuild')
    if any(sha256(path) != runtime_hashes[name]['sha256'] for name, path in critical.items()):
        raise RuntimeError('target runtime inputs changed during build/test')
    toolchain = {}
    for name, executable in (('c_compiler', cc), ('cxx_compiler', cxx), ('qemu_user', qemu), ('linker', tool('aarch64-linux-gnu-ld'))):
        toolchain[name] = {'path': executable, 'resolved_path': str(Path(executable).resolve()), 'sha256': sha256(executable),
                           'version': subprocess.check_output([executable, '--version'], text=True).splitlines()[0]}
    evidence = {'format': 'hf-arm64-cross-build-v1', 'simulated': True, 'target_triple': triple,
                'target_architecture': 'aarch64', 'host': {'machine': platform.machine(), 'platform': platform.platform()},
                'user_mode_emulation': True, 'physical_board_tested': False, 'tests_passed': True,
                'native_input_sha256': inputs, 'toolchain': toolchain, 'extra_tool_package': extra,
                'target_runtime_inputs': runtime_hashes, 'target_header_trees': fingerprint,
                'commands': commands, 'artifacts': artifacts, 'launchers': launchers, 'tests': tests,
                'sqlite_compile_profile': {'threadsafe': 1, 'dqs': 0, 'omit_load_extension': 1},
                'limits': ['QEMU user mode maps target process syscalls to the host Linux kernel.',
                           'No board BSP, bootloader, sensor/FPGA/DMA driver or physical timing evidence.',
                           'Static target archives and tool hashes are recorded, not a hermetic toolchain attestation.']}
    evidence_path.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('ARM64_EVIDENCE=' + str(evidence_path), flush=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build/arm64-native'))
    parser.add_argument('--tools-dir', type=Path, default=Path('build/arm64-tools'))
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--native-tests-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be 1-8')
    build(Path(__file__).resolve().parents[1], args.build_dir, args.tools_dir, jobs=args.jobs,
          rpc_tests=not args.native_tests_only)


if __name__ == '__main__':
    main()

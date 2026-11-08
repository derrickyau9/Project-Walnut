"""Cross-build DaOS ARM64 camera binaries on a real amd64 FreeBSD host.

The target sysroot must be the completed, pinned source-built FreeBSD15.1-p6
ARM64 stage. Six ARM64 ELFs are inspected but are NEVER executed on the amd64
build host. ARM64 CTest, Python/native and UI execution require a separate
actual ARM64 guest validation and are explicitly not counted here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys

from tools import freebsd_build as common


ROOT = Path(__file__).resolve().parents[1]
TARGET = 'aarch64-unknown-freebsd15.1'
SOURCE_COMMIT = '95719d8a895bf8170851e9844d9b4092b1d2b6fe'
SOURCE_ARCHIVE_SHA256 = '62616e1381734098f3351a0dc97dd153fe78d5cdd74cea41c771c1c6e2e3c9b6'
BASE_ANCHORS = ('bin/sh', 'lib/libc.so.7', 'boot/kernel/kernel',
                'etc/rc', 'etc/master.passwd', 'METALOG')
SDK_ANCHORS = ('libexec/ld-elf.so.1', 'usr/lib/crt1.o', 'usr/lib/crti.o',
               'usr/lib/crtn.o', 'lib/libc++.so.1', 'lib/libcxxrt.so.1',
               'usr/lib/libc++.so', 'usr/lib/libcxxrt.so',
               'usr/include/stdio.h', 'usr/include/c++/v1/__config',
               'usr/include/c++/v1/filesystem')
SDK_ELFS = ('bin/sh', 'lib/libc.so.7', 'boot/kernel/kernel',
            'libexec/ld-elf.so.1', 'lib/libc++.so.1', 'lib/libcxxrt.so.1',
            'usr/lib/libcxxrt.so')
CXX_LINKER_TOKENS = ('GROUP', '(', '/lib/libc++.so.1', '/usr/lib/libcxxrt.so', ')')


def require_cross_host():
    machine = common.require_freebsd()
    if machine not in ('amd64', 'x86_64') or os.uname().release.split('-')[0] != '15.1':
        raise RuntimeError('DaOS ARM64 cross-build requires an actual amd64 FreeBSD15.1 host; this is not a target execution path.')
    return machine


def _json_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('base evidence must be a bounded regular non-link JSON file')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate base-evidence JSON member')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('non-finite base evidence')
    def finite(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('non-finite base evidence')
        return parsed
    return json.loads(path.read_text(), object_pairs_hook=unique,
                      parse_constant=constant, parse_float=finite)


def _sdk_file(root, name):
    """Resolve only contained SDK links, never a host-library escape."""
    path = root / name
    resolved = path.resolve(strict=True)
    if (not resolved.is_relative_to(root) or not resolved.is_file()
            or not 0 < resolved.stat().st_size <= 256 * 1024 * 1024):
        raise ValueError('SDK anchor is absent, oversized or outside the target sysroot: ' + name)
    return resolved


def _target_header(path, machine=183, *, kernel=False):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01'
            or header[7] not in ((0, 9) if kernel else (9,))
            or struct.unpack_from('<H', header, 18)[0] != machine):
        raise ValueError('expected actual ARM64 FreeBSD ELF64 sysroot anchor')
    return {'elf_class': 64, 'endianness': 'little', 'osabi': header[7], 'machine': machine}


def _cxx_linker_script(root, anchors):
    """Accept only the actual pinned world's target-contained libc++ script.

    Absolute names in this GNU linker script are interpreted beneath the
    explicit Clang/LLD sysroot. They must not be resolved as host Path objects.
    Extra directives, search directories and alternate host library names are
    refused rather than treated as a general-purpose linker-script language.
    """
    path = _sdk_file(root, 'usr/lib/libc++.so')
    with path.open('rb') as stream:
        raw = stream.read(4097)
    try:
        tokens = tuple(raw.decode('ascii').split())
    except UnicodeDecodeError as error:
        raise ValueError('target libc++ linker script must be bounded ASCII') from error
    if len(raw) > 4096 or tokens != CXX_LINKER_TOKENS:
        raise ValueError('target libc++ linker script differs from the pinned FreeBSD ARM64 profile')
    targets = {}
    for token in CXX_LINKER_TOKENS[2:4]:
        name = token.removeprefix('/')
        item = anchors[name]
        targets[token] = {'resolved_relative_path': item['resolved_relative_path'],
                          'sha256': item['sha256'], 'machine': item['machine']}
    if anchors['usr/lib/libcxxrt.so']['sha256'] != anchors['lib/libcxxrt.so.1']['sha256']:
        raise ValueError('target libcxxrt linker entry differs from the versioned ARM64 runtime')
    return {'kind': 'GNU_GROUP', 'resolution': 'explicit_target_sysroot', 'targets': targets}


def validate_sysroot(sysroot, base_evidence):
    """Validate actual files against a completed pinned source-build receipt.

    This is consistency and architecture validation, not a signature or proof
    of an ARM64 boot. Unit fixtures exercising it do not execute target code.
    """
    supplied = Path(sysroot)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValueError('sysroot must be an explicit absolute non-link directory')
    root = supplied.resolve(strict=True)
    if root in (Path('/'), Path('/usr'), Path('/usr/local'), Path('/lib')) or not root.is_dir():
        raise ValueError('live host trees cannot serve as the target sysroot')
    evidence_path = Path(base_evidence).resolve(strict=True)
    state = _json_file(base_evidence)
    if not isinstance(state, dict):
        raise ValueError('base evidence must be a JSON object')
    target = state.get('target', {})
    source = state.get('source', {})
    policy = state.get('install_policy', {})
    if not all(isinstance(value, dict) for value in (target, source, policy)):
        raise ValueError('base target/source/install policy must be objects')
    if (state.get('format') != 'DaOS-freebsd-base-build-v2' or state.get('status') != 'completed'
            or target.get('architecture') != 'arm64' or target.get('make_target') != 'arm64'
            or target.get('make_target_arch') != 'aarch64' or target.get('elf_machine') != 183
            or target.get('kernel_ident') != 'DaOS'
            or source.get('architecture') != 'arm64' or source.get('release') != '15.1-RELEASE-p6'
            or source.get('commit') != SOURCE_COMMIT
            or source.get('archive_sha256') != SOURCE_ARCHIVE_SHA256
            or policy.get('no_root') is not True or policy.get('live_root_installed') is not False
            or policy.get('destination_is_private') is not True):
        raise ValueError('sysroot needs completed DaOS ARM64 p6 source-build evidence; amd64, unfinished and unpinned stages are refused')
    for key in ('destination', 'stage_directory'):
        recorded = Path(str(state.get(key, '')))
        if not recorded.is_absolute() or recorded.resolve(strict=True) != root:
            raise ValueError('requested sysroot differs from recorded private ARM64 stage')
    recorded_hashes = state.get('artifact_sha256')
    if not isinstance(recorded_hashes, dict):
        raise ValueError('base anchor hash inventory is missing')
    anchors = {}
    for name in (*BASE_ANCHORS, *SDK_ANCHORS):
        path = _sdk_file(root, name)
        checksum = common.sha(path)
        if name in BASE_ANCHORS and recorded_hashes.get(name) != checksum:
            raise ValueError('staged ARM64 base anchor differs from completed evidence: ' + name)
        item = {'sha256': checksum, 'bytes': path.stat().st_size,
                'resolved_relative_path': path.relative_to(root).as_posix()}
        if name in SDK_ELFS:
            # Kernel ELF branding may be System V; its exact completed-source
            # hash and ARM64 machine are still required. Userland is OSABI9.
            item.update(_target_header(path, kernel=name == 'boot/kernel/kernel'))
        anchors[name] = item
    anchors['usr/lib/libc++.so']['linker_script'] = _cxx_linker_script(root, anchors)
    return {'sysroot': str(root), 'base_evidence': str(evidence_path),
            'base_evidence_sha256': common.sha(evidence_path),
            'base_source_commit': SOURCE_COMMIT, 'base_release': source['release'],
            'target': target, 'anchors': anchors,
            'target_execution_validated': False}


def cross_source_inputs(root):
    values = common.source_inputs(root)
    for name in ('tools/freebsd_cross_build.py', 'tests/test_freebsd_cross_build.py',
                 'docs/testing/freebsd-arm64-cross-build.md'):
        path = Path(root) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing or linked cross-build source input: ' + name)
        values[name] = common.sha(path)
    return dict(sorted(values.items()))


def _cmake_string(value):
    value = str(value)
    if any(char in value for char in ('\n', '\r', '\0', ';')):
        raise ValueError('CMake path contains unsupported separators')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def toolchain_text(sysroot, tools):
    root = _cmake_string(sysroot)
    lines = ['# DaOS ARM64 cross-build; generated from verified private target sysroot.',
             'set(CMAKE_SYSTEM_NAME FreeBSD)', 'set(CMAKE_SYSTEM_VERSION 15.1)',
             'set(CMAKE_SYSTEM_PROCESSOR aarch64)',
             'set(CMAKE_C_COMPILER ' + _cmake_string(tools['cc']['invoked_path']) + ')',
             'set(CMAKE_CXX_COMPILER ' + _cmake_string(tools['cxx']['invoked_path']) + ')',
             'set(CMAKE_C_COMPILER_TARGET ' + TARGET + ')',
             'set(CMAKE_CXX_COMPILER_TARGET ' + TARGET + ')',
             'set(CMAKE_LINKER ' + _cmake_string(tools['lld']['invoked_path']) + ')',
             'set(CMAKE_SYSROOT ' + root + ')', 'set(CMAKE_FIND_ROOT_PATH ' + root + ')',
             'set(CMAKE_C_FLAGS_INIT "-fuse-ld=lld")',
             'set(CMAKE_CXX_FLAGS_INIT "-fuse-ld=lld")',
             'set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")',
             'set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)',
             'set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)',
             'set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)',
             'set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)',
             'set(CMAKE_CROSSCOMPILING_EMULATOR "")', '']
    return '\n'.join(lines)


def build(root, *, sysroot, base_evidence, destination, jobs=8):
    host_machine = require_cross_host()  # Before reading SDK/tool inputs or writing.
    if type(jobs) is not int or not 1 <= jobs <= 32:
        raise ValueError('jobs must be an integer from1 through32')
    root = Path(root).resolve(strict=True)
    destination = common.validate_destination(root, destination)
    sdk = validate_sysroot(sysroot, base_evidence)
    source_hashes = cross_source_inputs(root)
    tools = {label: common.tool_identity(name) for label, name in (
        ('cc', '/usr/bin/cc'), ('cxx', '/usr/bin/c++'), ('lld', '/usr/bin/ld.lld'),
        ('cmake', 'cmake'), ('make', '/usr/bin/make'),
        ('ar', '/usr/bin/ar'), ('ranlib', '/usr/bin/ranlib'))}
    destination.mkdir(parents=True, exist_ok=False)
    evidence = destination / 'freebsd-arm64-cross-build-evidence.json'
    state = {'format': 'DaOS-freebsd-arm64-cross-build-v1', 'status': 'running',
             'passed': False, 'build_kind': 'cross', 'target': {'architecture': 'arm64',
             'triple': TARGET, 'elf_machine': 183, 'osabi': 9},
             'host': {'system': 'FreeBSD', 'machine': host_machine,
                      'release': os.uname().release, 'uid': os.geteuid()},
             'sysroot': sdk, 'tools': tools, 'source_input_sha256': source_hashes,
             'commands': [], 'artifacts': {}, 'simulated_camera': True,
             'target_runtime_validated': False, 'physical_camera_validated': False,
             'target_tests': {'status': 'not_run', 'ctest_executed': False,
                              'python_native_tests_executed': False,
                              'reason': 'ARM64 binaries must be executed in a separate actual ARM64 FreeBSD guest, never on the amd64 build host.',
                              'expected_ctest_targets': sorted(common.CTEST_NAMES)}}
    common._write_state(evidence, state)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    for key in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LIBRARY_PATH',
                'CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH', 'SDKROOT', 'LD_LIBRARY_PATH',
                'CMAKE_TOOLCHAIN_FILE', 'CMAKE_GENERATOR', 'CMAKE_PREFIX_PATH',
                'CMAKE_LIBRARY_PATH', 'CMAKE_INCLUDE_PATH'):
        env.pop(key, None)
    def run(label, command, timeout=120):
        state['active_stage'] = label
        common._write_state(evidence, state)
        result = common.run_bounded(command, cwd=root, log_path=destination / (label + '.log'),
                                    env=env, timeout=timeout)
        state['commands'].append(result)
        common._write_state(evidence, state)
        return destination / result['log']
    try:
        for label in ('cc', 'cxx', 'lld', 'cmake'):
            log = run(label + '-version', [tools[label]['invoked_path'], '--version'])
            tools[label]['version_output'] = log.read_text(errors='replace')[:8192]
        log = run('make-version', [tools['make']['invoked_path'], '-V', 'MAKE_VERSION'])
        tools['make']['version_output'] = log.read_text(errors='replace')[:8192]
        notice_dir = destination / 'notices'; notice_dir.mkdir()
        state['notices'] = {}
        for name in common.NOTICE_FILES:
            copied = notice_dir / name.replace('/', '__')
            copied.write_bytes((root / name).read_bytes())
            state['notices'][name] = {'file': str(copied.relative_to(destination)), 'sha256': common.sha(copied)}
        toolchain = destination / 'DaOS-arm64-toolchain.cmake'
        toolchain.write_text(toolchain_text(sdk['sysroot'], tools))
        state['toolchain_file_sha256'] = common.sha(toolchain)
        run('configure', [tools['cmake']['invoked_path'], '-S', root / 'camera/native',
                          '-B', destination, '-G', 'Unix Makefiles',
                          '-DCMAKE_TOOLCHAIN_FILE=' + str(toolchain), '-DCMAKE_BUILD_TYPE=Release',
                          '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
                          '-DCMAKE_MAKE_PROGRAM=' + tools['make']['invoked_path'],
                          '-DCMAKE_AR=' + tools['ar']['invoked_path'],
                          '-DCMAKE_RANLIB=' + tools['ranlib']['invoked_path'],
                          '-DHF_BUILD_DAEMON=ON', '-DHF_BUILD_WORKER=ON'])
        run('compile', [tools['cmake']['invoked_path'], '--build', destination,
                        '--parallel', str(jobs)], timeout=900)
        for name in common.ARTIFACTS:
            state['artifacts'][name] = common.inspect_freebsd_elf(destination / name, 183)
        commands_file = destination / 'compile_commands.json'
        compile_commands = json.loads(commands_file.read_text())
        if (not isinstance(compile_commands, list) or not compile_commands
                or any(TARGET not in item.get('command', '')
                       or sdk['sysroot'] not in item.get('command', '') for item in compile_commands)):
            raise RuntimeError('actual CMake compile commands do not identify the ARM64 target SDK')
        state['compile_commands_sha256'] = common.sha(commands_file)
        if cross_source_inputs(root) != source_hashes:
            raise RuntimeError('cross-build source inputs changed; freeze and choose a new output directory')
        if validate_sysroot(sysroot, base_evidence) != sdk:
            raise RuntimeError('target SDK/base evidence changed during cross-build')
        if common.sha(toolchain) != state['toolchain_file_sha256']:
            raise RuntimeError('generated target toolchain changed during compilation')
        for tool in tools.values():
            if common.sha(tool['resolved_path']) != tool['sha256']:
                raise RuntimeError('host cross-build tool changed during compilation')
        state.update(status='completed', passed=True, active_stage=None,
                     scope='DaOS ARM64 native application cross-compilation only; target CTest/Python/UI execution and ARM64 boot remain separately unvalidated')
        common._write_state(evidence, state)
        return state
    except BaseException as error:
        state.update(status='failed', passed=False, error_type=type(error).__name__, error=str(error))
        common._write_state(evidence, state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sysroot', type=Path, required=True)
    parser.add_argument('--base-evidence', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, default=Path('build/freebsd-arm64-native'))
    parser.add_argument('--jobs', type=int, default=8)
    args = parser.parse_args(argv)
    print(json.dumps(build(ROOT, sysroot=args.sysroot, base_evidence=args.base_evidence,
                          destination=args.build_dir, jobs=args.jobs), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('DaOS ARM64 cross-build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

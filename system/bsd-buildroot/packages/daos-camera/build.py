#!/usr/bin/env python3
"""Compile the camera from source for NetBSD/aarch64 and install a bound package.

The host may be Linux. Target executables are never run on the build host.
Only the selected NetBSD build.sh toolchain and DESTDIR are used; old Linux or
FreeBSD executables/packages cannot satisfy this recipe.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
GROUPS = {
    'hf-core-tests': ['test_core', 'core'],
    'hf-daemon-tests': ['test_daemon', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
    'hf-sha256-tests': ['test_sha256', 'sha256'],
    'hf-calibration-tests': ['test_calibration', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
    'hf-native-worker': ['worker', 'dng', 'core'],
    'hf-camerad': ['camerad', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
}
UNITS = {'core': 'src/core.cpp', 'dng': 'src/dng.cpp', 'sha256': 'src/sha256.cpp',
         'calibration': 'src/calibration.cpp', 'daemon': 'src/daemon.cpp',
         'worker': 'src/worker.cpp', 'camerad': 'src/camerad.cpp',
         'test_core': 'tests/test_core.cpp', 'test_daemon': 'tests/test_daemon.cpp',
         'test_sha256': 'tests/test_sha256.cpp', 'test_calibration': 'tests/test_calibration.cpp'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def ordinary(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('Regular input required: ' + str(path))
    return path


def source_hashes(root):
    native = root / 'camera/native'
    files = {native / 'CMakeLists.txt', HERE / 'build.py', HERE / 'recipe.json'}
    for folder in ('src', 'include', 'tests', 'third_party'):
        files.update(p for p in (native / folder).rglob('*') if p.is_file()
                     and p.suffix in ('.c', '.cpp', '.h', '.hpp'))
    files.update((root / 'camera/calibration/fixtures').glob('*.json'))
    files.update({native / 'third_party/nlohmann/LICENSE.MIT', native / 'third_party/manifest.json'})
    result = {}
    for path in sorted(files):
        ordinary(path)
        name = path.relative_to(root).as_posix() if path.is_relative_to(root) else 'recipe/' + path.name
        result[name] = sha(path)
    return result


def target_elf(path):
    """Verify ELF64 aarch64 with a real NetBSD ABI note, not a brand string."""
    ordinary(path)
    data = path.read_bytes()
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', data, 18)[0] != 183
            or struct.unpack_from('<H', data, 16)[0] not in (2, 3)
            or data[7] not in (0, 2)):
        raise ValueError('Expected little-endian ELF64/aarch64: ' + str(path))
    offset = struct.unpack_from('<Q', data, 32)[0]
    entry_size, count = struct.unpack_from('<HH', data, 54)
    if entry_size != 56 or not 1 <= count <= 1024 or offset + count * entry_size > len(data):
        raise ValueError('Invalid ELF program-header bounds')
    notes = []; interpreter = None
    for index in range(count):
        kind, flags, begin, vaddr, paddr, size, memsize, alignment = struct.unpack_from('<IIQQQQQQ', data, offset + index * entry_size)
        if begin + size > len(data): raise ValueError('ELF segment exceeds file')
        segment = data[begin:begin + size]
        if kind == 3:
            interpreter = segment.rstrip(b'\0').decode('ascii')
            if interpreter not in ('/libexec/ld.elf_so', '/usr/libexec/ld.elf_so'):
                raise ValueError('Foreign target interpreter: ' + interpreter)
        if kind != 4: continue
        cursor = 0
        while cursor + 12 <= len(segment):
            namesz, descsz, note_type = struct.unpack_from('<III', segment, cursor); cursor += 12
            if namesz > 4096 or descsz > 65536: raise ValueError('Unbounded ELF note')
            end_name = cursor + namesz
            name = segment[cursor:end_name].rstrip(b'\0'); cursor += (namesz + 3) & ~3
            end_desc = cursor + descsz
            if end_name > len(segment) or end_desc > len(segment): raise ValueError('Truncated ELF note')
            desc = segment[cursor:end_desc]; cursor += (descsz + 3) & ~3
            if name in (b'FreeBSD', b'OpenBSD') or (name == b'GNU' and note_type == 1):
                raise ValueError('Foreign target ELF ABI note')
            if name == b'NetBSD' and note_type == 1 and descsz == 4:
                notes.append(struct.unpack('<I', desc)[0])
    if len(notes) != 1: raise ValueError('Exactly one NetBSD ABI version note is required')
    return {'elf_class': 64, 'machine': 183, 'byte_order': 'little',
            'OSABI': data[7], 'NetBSD_version_note': notes[0], 'interpreter': interpreter}


def compiler(path, toolchain, name):
    candidate = Path(path) if path else Path(toolchain) / 'bin' / ('aarch64--netbsd-' + name)
    if not candidate.is_file(): raise ValueError('Missing NetBSD build.sh tool: ' + str(candidate))
    machine = subprocess.check_output([str(candidate), '-dumpmachine'], text=True, timeout=15).strip()
    if not machine.startswith('aarch64') or 'netbsd' not in machine.lower() or 'linux' in machine.lower():
        raise ValueError('Compiler is not a NetBSD/aarch64 cross compiler: ' + machine)
    return str(candidate), {'path': str(candidate), 'sha256': sha(candidate.resolve()), 'target': machine,
                            'version': subprocess.check_output([str(candidate), '--version'], text=True, timeout=15).splitlines()[0]}


def commands(root, destination, sysroot, cc, cxx):
    native = root / 'camera/native'
    include = ['-I' + str(native / 'include'), '-I' + str(native / 'third_party'), '-I' + str(native / 'third_party/sqlite')]
    flags = ['--sysroot=' + str(sysroot), '-std=c++17', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Wpedantic', '-ffp-contract=off', '-pthread']
    compile_steps = [[cxx, *flags, *include, '-c', str(native / source), '-o', str(destination / (name + '.o'))]
                     for name, source in UNITS.items()]
    compile_steps.append([cc, '--sysroot=' + str(sysroot), '-O2', '-DNDEBUG', '-DSQLITE_THREADSAFE=1', '-DSQLITE_DQS=0',
                          '-pthread', '-c', str(native / 'third_party/sqlite/sqlite3.c'), '-o', str(destination / 'sqlite3.o')])
    link_steps = [[cxx, '--sysroot=' + str(sysroot), *(str(destination / (unit + '.o')) for unit in units),
                   '-pthread', '-o', str(destination / name)] for name, units in GROUPS.items()]
    return compile_steps, link_steps


def preflight(args):
    root = args.project_root.resolve(strict=True)
    sysroot = args.sysroot.resolve(strict=True)
    if sysroot == Path('/'): raise ValueError('An explicit target DESTDIR is required')
    header = ordinary(sysroot / 'usr/include/sys/param.h')
    if b'NetBSD' not in header.read_bytes(): raise ValueError('Sysroot is not a NetBSD DESTDIR')
    ordinary(sysroot / 'libexec/ld.elf_so')
    cc, cc_info = compiler(args.cc, args.toolchain_dir, 'gcc')
    cxx, cxx_info = compiler(args.cxx, args.toolchain_dir, 'g++')
    destination = args.build_dir.absolute().parent.resolve(strict=True) / args.build_dir.name
    for forbidden in (sysroot, root / 'camera/native', args.toolchain_dir.resolve(strict=True)):
        if destination == forbidden or forbidden in destination.parents:
            raise ValueError('Build output overlaps an input tree')
    return root, sysroot, destination, cc, cxx, {'cc': cc_info, 'cxx': cxx_info, 'sysroot_param_sha256': sha(header)}


def build(args):
    root, sysroot, destination, cc, cxx, toolchain = preflight(args)
    compile_steps, link_steps = commands(root, destination, sysroot, cc, cxx)
    if args.action == 'plan':
        print(json.dumps({'target': 'NetBSD/aarch64', 'compile': compile_steps, 'link': link_steps,
                          'toolchain': toolchain, 'target_tests_executed': False}, indent=2)); return
    if os.path.lexists(destination): raise ValueError('New build directory required; failed outputs are preserved')
    destination.mkdir(mode=0o700)
    inputs = source_hashes(root)
    state = {'format': 'DaOS-NetBSD-camera-cross-build-v1', 'status': 'building',
             'target': {'system': 'NetBSD', 'architecture': 'aarch64', 'machine': 183},
             'toolchain': toolchain, 'source_sha256': inputs, 'commands': compile_steps + link_steps,
             'target_tests_compiled': False, 'target_tests_executed': False, 'target_runtime_validated': False,
             'sysroot': str(sysroot), 'project_root': str(root), 'artifacts': {}}
    evidence = destination / 'build-evidence.json'; save(evidence, state)
    def execute(item):
        index, command = item
        with (destination / ('command-%02d.log' % index)).open('xb') as log:
            process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=(os.name == 'posix'))
            try:
                code = process.wait(timeout=args.timeout)
            except BaseException:
                if os.name == 'posix':
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else: process.kill()
                process.wait()
                raise
            if code: raise subprocess.CalledProcessError(code, command)
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(execute, enumerate(compile_steps)))
        for index, command in enumerate(link_steps, len(compile_steps)): execute((index, command))
        for name in GROUPS:
            path = destination / name
            state['artifacts'][name] = {'bytes': path.stat().st_size, 'sha256': sha(path), 'elf': target_elf(path)}
        if source_hashes(root) != inputs: raise ValueError('Sources changed during compilation')
        state.update(status='completed', build_passed=True, target_tests_compiled=True)
        save(evidence, state)
    except BaseException as error:
        state.update(status='failed', error_type=type(error).__name__, error=str(error)); save(evidence, state)
        raise
    print(json.dumps({'status': state['status'], 'evidence': str(evidence), 'artifacts': state['artifacts'],
                      'target_tests_executed': False}, indent=2))


def destination_file(stage, rel):
    if not rel or rel.startswith('/') or '..' in Path(rel).parts: raise ValueError('Confined relative path required')
    target = stage / rel
    for parent in target.parents:
        if parent == stage.parent: break
        if parent.is_symlink(): raise ValueError('Redirected staging parent')
    if os.path.lexists(target): raise ValueError('Package would overwrite an existing entry: ' + rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def install(args):
    if os.path.lexists(args.manifest): raise ValueError('New manifest path required')
    root = args.project_root.resolve(strict=True); build_dir = args.build_dir.resolve(strict=True)
    evidence_path = ordinary(build_dir / 'build-evidence.json')
    record = json.loads(evidence_path.read_text())
    if (record.get('format') != 'DaOS-NetBSD-camera-cross-build-v1' or record.get('status') != 'completed'
            or record.get('build_passed') is not True or source_hashes(root) != record['source_sha256']):
        raise ValueError('Completed source-bound NetBSD camera build required')
    stage = args.destdir.resolve(strict=True)
    if stage == Path('/') or args.destdir.is_symlink(): raise ValueError('Owned staging DESTDIR required')
    files = []
    def put(source, rel, mode=0o644):
        source = ordinary(source); checksum = sha(source)
        target = destination_file(stage, rel); shutil.copyfile(source, target); target.chmod(mode)
        if sha(target) != checksum: raise ValueError('Installed copy differs: ' + rel)
        files.append({'path': rel, 'type': 'file', 'bytes': target.stat().st_size, 'sha256': checksum, 'mode': mode})
    for name in GROUPS:
        source = build_dir / name; expected = record['artifacts'][name]
        if sha(source) != expected['sha256'] or target_elf(source) != expected['elf']:
            raise ValueError('NetBSD artifact changed: ' + name)
        rel = 'usr/libexec/daos/' + (('tests/' + name) if name.endswith('tests') else name)
        if args.with_tests or not name.endswith('tests'): put(source, rel, 0o755)
    if args.with_tests:
        for source in sorted((root / 'camera/calibration/fixtures').glob('*.json')):
            put(source, 'usr/share/daos/tests/calibration/' + source.name)
        run = destination_file(stage, 'usr/libexec/daos/tests/run-native-tests')
        run.write_text('#!/bin/sh\nset -eu\nfor test in hf-core-tests hf-daemon-tests hf-sha256-tests; do\n'
                       '  echo DAOS_NATIVE_TEST_BEGIN "$test"\n  "/usr/libexec/daos/tests/$test"\ndone\n'
                       'echo DAOS_NATIVE_TEST_BEGIN hf-calibration-tests\n'
                       '/usr/libexec/daos/tests/hf-calibration-tests /usr/share/daos/tests/calibration\n'
                       'echo DAOS_NATIVE_TESTS_PASSED 4\n')
        run.chmod(0o755); files.append({'path': run.relative_to(stage).as_posix(), 'type': 'file', 'bytes': run.stat().st_size, 'sha256': sha(run), 'mode': 0o755})
    if args.with_ui_source:
        suffixes = {'.py', '.qml', '.json', '.svg', '.html', '.css', '.js'}
        for folder in ('camera', 'daos', 'simulation', 'ui'):
            for source in sorted((root / folder).rglob('*')):
                rel = source.relative_to(root)
                if (not source.is_file() or source.is_symlink() or source.suffix not in suffixes
                        or '__pycache__' in rel.parts or any(p.startswith('evidence') for p in rel.parts)
                        or rel.parts[:2] == ('camera', 'native')): continue
                put(source, 'usr/share/daos/camera-app/' + rel.as_posix())
        for name in ('tools/__init__.py', 'tools/diagnostics.py'):
            put(root / name, 'usr/share/daos/camera-app/' + name)
    for name in ('camera/native/third_party/nlohmann/LICENSE.MIT', 'camera/native/third_party/manifest.json'):
        put(root / name, 'usr/share/daos/licenses/camera/' + Path(name).name)
    sqlite = (root / 'camera/native/third_party/sqlite/sqlite3.h').read_bytes()
    end = sqlite.find(b'*/')
    if not sqlite.startswith(b'/*') or not 0 < end < 16384: raise ValueError('SQLite upstream notice missing')
    notice = destination_file(stage, 'usr/share/daos/licenses/camera/SQLite-UPSTREAM.txt')
    notice.write_bytes(sqlite[:end+2] + b'\n')
    files.append({'path': notice.relative_to(stage).as_posix(), 'type': 'file', 'bytes': notice.stat().st_size, 'sha256': sha(notice), 'mode': 0o644})
    manifest = {'format': 'DaOS-BSD-component-manifest-v1', 'name': 'daos-camera', 'version': '0.1.0',
                'target': record['target'], 'build_evidence_sha256': sha(evidence_path), 'files': files,
                'ui_source_installed': args.with_ui_source, 'ui_runtime_validated': False,
                'target_tests_executed': False, 'third_party_binaries_imported': False}
    output = args.manifest.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, manifest); print(json.dumps({'manifest': str(output), 'files': len(files)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'build', 'install'))
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--toolchain-dir', type=Path)
    parser.add_argument('--sysroot', type=Path)
    parser.add_argument('--cc'); parser.add_argument('--cxx')
    parser.add_argument('--jobs', type=int, default=4); parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--destdir', type=Path); parser.add_argument('--manifest', type=Path)
    parser.add_argument('--with-tests', action='store_true'); parser.add_argument('--with-ui-source', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16 or not 30 <= args.timeout <= 7200: parser.error('Bounded jobs/time required')
    if args.action == 'install':
        if not args.destdir or not args.manifest: parser.error('install needs --destdir and --manifest')
        install(args)
    else:
        if not args.toolchain_dir or not args.sysroot: parser.error('plan/build need --toolchain-dir and --sysroot')
        build(args)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS NetBSD camera package: ' + str(error), file=sys.stderr); raise SystemExit(1)

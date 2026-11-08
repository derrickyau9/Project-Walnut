"""Build and test the native synthetic camera on an actual FreeBSD host.

This does not turn a Linux executable, an ELF parser result or an emulated
environment variable into FreeBSD execution evidence. Run with ordinary native
FreeBSD Python/CMake/Clang tools from a writable project checkout. No packages,
host services, installed OS files or physical devices are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from tools.linux_build import native_input_hashes


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ('hf-core-tests', 'hf-daemon-tests', 'hf-sha256-tests',
             'hf-calibration-tests', 'hf-native-worker', 'hf-camerad')
CTEST_NAMES = {'native-core', 'native-authority', 'native-sha256', 'native-calibration'}
PYTHON_TEST_MODULES = (
    'tests.test_native_daemon', 'tests.test_native_client', 'tests.test_native_settings',
    'tests.test_native_calibration_binding', 'tests.test_calibration_activation',
    'tests.test_persistent_settings', 'tests.test_client_calibration_context',
    'tests.test_camera_cli', 'tests.test_camera_offline',
    'tests.test_reference_journal_context', 'tests.test_calibration_http',
    'tests.test_native_staging_integrity',
)
NOTICE_FILES = ('licenses/INVENTORY.json', 'docs/design-provenance.md',
                'camera/native/third_party/manifest.json',
                'camera/native/third_party/nlohmann/LICENSE.MIT',
                'camera/native/third_party/sqlite/sqlite3.h')
MACHINES = {'amd64': 62, 'x86_64': 62, 'arm64': 183, 'aarch64': 183}
MAX_LOG_BYTES = 16 * 1024 * 1024


def require_freebsd():
    """Inspect the running interpreter and kernel, never an environment hint."""
    if (not sys.platform.startswith('freebsd') or not hasattr(os, 'uname')
            or os.uname().sysname != 'FreeBSD' or platform.system() != 'FreeBSD'):
        raise RuntimeError('This builder requires an actual FreeBSD interpreter and kernel; no Linux fallback or environment override is accepted.')
    machine = os.uname().machine.lower()
    if machine not in MACHINES:
        raise RuntimeError('This native FreeBSD evidence recipe supports only 64-bit amd64 or arm64 hosts.')
    return machine


def validate_destination(root, destination):
    """Pure, create-only path check; builds stay inside this checkout's build/."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('project root must be an existing directory')
    base = root / 'build'
    if base.is_symlink():
        raise ValueError('linked project build root is refused')
    given = Path(destination)
    given = given if given.is_absolute() else root / given
    current = given.absolute()
    for path in (current, *current.parents):
        if path.is_symlink():
            raise ValueError('linked build destination or ancestor is refused')
    resolved = given.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError('use a new descendant of this project build directory, never source or user data')
    if os.path.lexists(resolved):
        raise ValueError('existing build destinations are preserved; choose a new directory')
    return resolved


def sha(path):
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def source_inputs(root):
    # Reuse only the pure fingerprint routine; never call the Linux builder.
    root = Path(root)
    values = native_input_hashes(root)
    extra = [root / 'tools/freebsd_build.py', root / 'tests/test_freebsd_build.py']
    extra.extend(root / name for name in NOTICE_FILES)
    for tree in ('camera', 'simulation', 'daos'):
        extra.extend(path for path in (root / tree).rglob('*')
                     if path.suffix in {'.py', '.json'}
                     and not {'native', '__pycache__', 'evidence'}.intersection(path.relative_to(root).parts))
    extra.extend(root / (name.replace('.', '/') + '.py') for name in PYTHON_TEST_MODULES)
    for path in sorted(set(extra)):
        if path.is_symlink() or not path.is_file():
            raise ValueError('required FreeBSD source/test/notice input is missing or linked: ' + str(path))
        values[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(values.items()))


def tool_identity(name):
    if not name or any(char.isspace() for char in name) or '\0' in name:
        raise ValueError('compiler/tool setting must name one executable, without shell arguments')
    found = shutil.which(name)
    if not found:
        raise RuntimeError('required native FreeBSD tool not found: ' + name)
    path = Path(found).absolute()
    target = path.resolve(strict=True)
    if not target.is_file():
        raise ValueError('tool does not resolve to a regular file')
    return {'invoked_path': str(path), 'resolved_path': str(target), 'sha256': sha(target)}


def inspect_freebsd_elf(path, expected_machine):
    """Inspect bytes only; this parser is not OS execution or authenticity proof."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('native artifact must be a regular non-link file')
    size = path.stat().st_size
    if not 64 <= size <= 64 * 1024 * 1024:
        raise ValueError('native ELF size exceeds bounds')
    with path.open('rb') as stream:
        header = stream.read(64)
        if header[:7] != b'\x7fELF\x02\x01\x01' or header[7] != 9:
            raise ValueError('expected ELF64 little-endian with FreeBSD OSABI9')
        fields = struct.unpack('<16sHHIQQQIHHHHHH', header)
        _, kind, machine, version, entry, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = fields
        if (kind not in (2, 3) or machine != expected_machine or version != 1
                or ehsize != 64 or phentsize != 56 or not 1 <= phnum <= 128
                or phoff < 64 or phoff + phentsize * phnum > size):
            raise ValueError('native FreeBSD ELF architecture or program-header bounds differ')
        stream.seek(phoff)
        programs = [struct.unpack('<IIQQQQQQ', stream.read(56)) for _ in range(phnum)]
        interpreter = None
        for program in programs:
            kind_p, _, offset, _, _, file_size, _, _ = program
            if offset + file_size > size:
                raise ValueError('ELF program payload exceeds file')
            if kind_p == 3:
                if interpreter is not None or not 2 <= file_size <= 512:
                    raise ValueError('invalid ELF interpreter extent')
                stream.seek(offset)
                raw = stream.read(file_size)
                if not raw.endswith(b'\0') or b'\0' in raw[:-1]:
                    raise ValueError('invalid ELF interpreter path')
                interpreter = raw[:-1].decode('ascii')
                if interpreter != '/libexec/ld-elf.so.1':
                    raise ValueError('expected native FreeBSD ELF interpreter')
        if not any(item[0] == 1 and item[5] for item in programs):
            raise ValueError('ELF has no file-backed loadable segment')
    return {'bytes': size, 'sha256': sha(path), 'elf_class': 64, 'endianness': 'little',
            'osabi': 9, 'osabi_name': 'FreeBSD', 'machine': machine,
            'machine_name': 'EM_X86_64' if machine == 62 else 'EM_AARCH64',
            'elf_type': kind, 'entry': entry, 'interpreter': interpreter}


def _stop_owned(process):
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=3)


def run_bounded(command, *, cwd, log_path, env=None, timeout=120, max_bytes=MAX_LOG_BYTES):
    """Run one owned POSIX process group with finite output and wall time."""
    if not 0 < timeout <= 1800 or not 0 < max_bytes <= 64 * 1024 * 1024:
        raise ValueError('bounded command limits are invalid')
    command = [str(part) for part in command]
    start = time.monotonic()
    process = None
    selector = selectors.DefaultSelector()
    count = 0
    exit_at = None
    try:
        with Path(log_path).open('xb') as log:
            process = subprocess.Popen(command, cwd=cwd, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                if now - start > timeout:
                    raise RuntimeError('command exceeded wall-time limit')
                if process.poll() is not None:
                    exit_at = exit_at or now
                    if now - exit_at > 2:
                        raise RuntimeError('command exited with an owned descendant holding output open')
                for key, _ in selector.select(timeout=.1):
                    data = os.read(key.fd, min(16384, max_bytes - count + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if count + len(data) > max_bytes:
                        log.write(data[:max_bytes - count])
                        raise RuntimeError('command exceeded output limit')
                    log.write(data)
                    count += len(data)
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise RuntimeError('command exceeded wall-time limit')
            code = process.wait(timeout=remaining)
            log.flush()
            os.fsync(log.fileno())
        if code:
            raise RuntimeError('command failed with exit status ' + str(code))
        return {'command': command, 'returncode': code, 'seconds': round(time.monotonic() - start, 3),
                'log': Path(log_path).name, 'log_bytes': count, 'log_sha256': sha(log_path)}
    finally:
        selector.close()
        if process is not None:
            _stop_owned(process)
            if process.stdout:
                process.stdout.close()


def _write_state(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


_PYTHON_RUNNER = r'''
import json,sys,unittest
from pathlib import Path
class CapturingResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.successes=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.successes.append(test.id())
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
result=unittest.TextTestRunner(verbosity=2,resultclass=CapturingResult).run(suite)
record={'format':'hf-freebsd-native-python-tests-v1','passed':result.wasSuccessful(),
        'ran':result.testsRun,'passed_count':len(result.successes),'successful_tests':result.successes,
        'failures':[test.id() for test,_ in result.failures],
        'errors':[test.id() for test,_ in result.errors],
        'skipped':[{'test':test.id(),'reason':reason} for test,reason in result.skipped]}
with Path(sys.argv[1]).open('x',encoding='utf-8') as stream:json.dump(record,stream,indent=2)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def build(root, destination, *, jobs=8):
    machine = require_freebsd()  # Before looking up tools or creating anything.
    if type(jobs) is not int or not 1 <= jobs <= 32:
        raise ValueError('jobs must be an integer from1 through32')
    root = Path(root).resolve(strict=True)
    destination = validate_destination(root, destination)
    initial = source_inputs(root)
    tools = {label: tool_identity(name) for label, name in (
        ('cc', os.environ.get('CC', 'cc')), ('cxx', os.environ.get('CXX', 'c++')),
        ('cmake', 'cmake'), ('ctest', 'ctest'), ('make', 'make'),
        ('freebsd_version', 'freebsd-version'))}
    destination.mkdir(parents=True, exist_ok=False)
    record = {'format': 'hf-freebsd-native-build-v1', 'status': 'running', 'passed': False,
              'simulated': True, 'physical_camera_validated': False, 'sys_platform': sys.platform,
              'uname': dict(zip(('sysname', 'nodename', 'release', 'version', 'machine'), os.uname())),
              'python': sys.version, 'uid': os.geteuid(), 'tools': tools,
              'source_input_sha256': initial, 'commands': [], 'artifacts': {}}
    evidence = destination / 'freebsd-build-evidence.json'
    _write_state(evidence, record)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    env.pop('CMAKE_TOOLCHAIN_FILE', None)
    env.pop('CMAKE_GENERATOR', None)
    def run(label, command, *, cwd=root, timeout=120):
        record['active_stage'] = label
        _write_state(evidence, record)
        value = run_bounded(command, cwd=cwd, log_path=destination / (label + '.log'),
                            env=env, timeout=timeout)
        record['commands'].append(value)
        _write_state(evidence, record)
        return destination / value['log']
    try:
        for name in ('cc', 'cxx', 'cmake', 'ctest'):
            output = run(name + '-version', [tools[name]['invoked_path'], '--version'])
            tools[name]['version_output'] = output.read_text(errors='replace')[:8192]
        make_version = run('make-version', [tools['make']['invoked_path'], '-V', 'MAKE_VERSION'])
        tools['make']['version_output'] = make_version.read_text(errors='replace')[:8192]
        version_log = run('freebsd-version', [tools['freebsd_version']['invoked_path'], '-ku'])
        record['freebsd_version_kernel_userland'] = version_log.read_text().splitlines()
        notice_dir = destination / 'notices'
        notice_dir.mkdir()
        record['notices'] = {}
        for name in NOTICE_FILES:
            output = notice_dir / name.replace('/', '__')
            output.write_bytes((root / name).read_bytes())
            record['notices'][name] = {'file': str(output.relative_to(destination)), 'sha256': sha(output)}
        run('configure', [tools['cmake']['invoked_path'], '-S', root / 'camera/native', '-B', destination,
                          '-G', 'Unix Makefiles', '-DCMAKE_BUILD_TYPE=Release',
                          '-DCMAKE_C_COMPILER=' + tools['cc']['invoked_path'],
                          '-DCMAKE_CXX_COMPILER=' + tools['cxx']['invoked_path'],
                          '-DCMAKE_MAKE_PROGRAM=' + tools['make']['invoked_path'],
                          '-DHF_BUILD_WORKER=ON', '-DHF_BUILD_DAEMON=ON'])
        run('compile', [tools['cmake']['invoked_path'], '--build', destination,
                        '--parallel', str(jobs)], timeout=900)
        for name in ARTIFACTS:
            record['artifacts'][name] = inspect_freebsd_elf(destination / name, MACHINES[machine])
        listing = run('ctest-list', [tools['ctest']['invoked_path'], '--show-only=json-v1'], cwd=destination)
        tests = json.loads(listing.read_text())
        if {item['name'] for item in tests['tests']} != CTEST_NAMES or len(tests['tests']) != 4:
            raise RuntimeError('configured native test set is not the required four targets')
        run('ctest', [tools['ctest']['invoked_path'], '--output-on-failure', '--timeout', '60',
                      '--output-junit', str(destination / 'ctest-results.xml')], cwd=destination, timeout=300)
        suite = ET.parse(destination / 'ctest-results.xml').getroot()
        cases = suite.findall('.//testcase')
        if (len(cases) != 4 or {item.attrib.get('name') for item in cases} != CTEST_NAMES
                or any(item.find('failure') is not None or item.find('error') is not None
                       or item.find('skipped') is not None for item in cases)):
            raise RuntimeError('CTest result does not establish four passing native targets')
        record['ctest'] = {'passed': True, 'targets': sorted(CTEST_NAMES), 'ran': len(cases),
                           'result_sha256': sha(destination / 'ctest-results.xml')}
        env.update(HF_NATIVE_DAEMON=str(destination / 'hf-camerad'),
                   HF_NATIVE_CAMERAD=str(destination / 'hf-camerad'),
                   HF_R9_NATIVE_DAEMON=str(destination / 'hf-camerad'),
                   HF_NATIVE_WORKER=str(destination / 'hf-native-worker'))
        report = destination / 'python-tests.json'
        run('python-tests', [sys.executable, '-B', '-c', _PYTHON_RUNNER, report, *PYTHON_TEST_MODULES], timeout=300)
        record['python_tests'] = json.loads(report.read_text())
        if record['python_tests']['passed'] is not True:
            raise RuntimeError('focused Python tests did not pass')
        record['python_tests']['modules'] = list(PYTHON_TEST_MODULES)
        record['python_tests']['report_sha256'] = sha(report)
        if source_inputs(root) != initial:
            raise RuntimeError('source inputs changed during build/tests; freeze and choose a new build destination')
        for value in tools.values():
            if sha(value['resolved_path']) != value['sha256']:
                raise RuntimeError('native build tool changed during execution')
        for name, value in record['artifacts'].items():
            if sha(destination / name) != value['sha256']:
                raise RuntimeError('native artifact changed during tests')
        record.update(status='completed', passed=True, active_stage=None,
                      scope='actual native FreeBSD process and synthetic camera; no physical camera or filesystem power-loss validation')
        _write_state(evidence, record)
        return record
    except BaseException as error:
        record.update(status='failed', passed=False, error_type=type(error).__name__, error=str(error))
        _write_state(evidence, record)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build/freebsd-native'))
    parser.add_argument('--jobs', type=int, default=8)
    args = parser.parse_args(argv)
    print(json.dumps(build(ROOT, args.build_dir, jobs=args.jobs), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('FreeBSD build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

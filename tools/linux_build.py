"""Build and exercise the synthetic native camera on an actual Linux host.

The direct compiler path needs only Python 3.10+, cc and c++; it installs nothing
and uses the pinned SQLite/JSON sources already in the repository.
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

PYTHON_TEST_PATTERNS = ('test_native_daemon.py', 'test_native_client.py', 'test_native_settings.py',
                        'test_native_calibration_binding.py', 'test_http_client.py',
                        'test_linux_*.py', 'test_build_native.py')


def native_input_hashes(root: Path):
    """Fingerprint native source/header inputs and the direct build recipe."""
    root = Path(root).resolve()
    source = root / 'camera/native'
    paths = [source / 'CMakeLists.txt', root / 'tools/linux_build.py']
    paths.extend(root / 'camera/calibration/fixtures' / name for name in
                 ('binding-uncalibrated.json', 'binding-synthetic.json', 'binding-unicode.json'))
    for directory in ('include', 'src', 'tests', 'third_party/sqlite', 'third_party/nlohmann'):
        paths.extend(path for path in (source / directory).rglob('*')
                     if path.is_file() and path.suffix in ('.c', '.cpp', '.h', '.hpp', '.inc'))
    result = {}
    for path in sorted(set(paths)):
        if not path.is_file() or path.is_symlink():
            raise ValueError('native build input missing or linked: ' + str(path))
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def build(root: Path, destination: Path, *, jobs=2, python_tests=True):
    if sys.platform != 'linux':
        raise RuntimeError('This helper requires a real Linux process; Windows compilation is not Linux execution.')
    root, destination = root.resolve(), destination.resolve()
    source = root / 'camera/native'
    inputs_before = native_input_hashes(root)
    cc, cxx = shutil.which(os.environ.get('CC', 'cc')), shutil.which(os.environ.get('CXX', 'c++'))
    if not cc or not cxx:
        raise RuntimeError('A Linux C compiler and C++17 compiler are required (CC=cc, CXX=c++).')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'build-evidence.json').unlink(missing_ok=True)
    commands = []

    def run(command, *, env=None):
        command = [str(item) for item in command]
        print('+ ' + shlex.join(command), flush=True)
        commands.append(command)
        subprocess.run(command, check=True, cwd=root, env=env)

    include = ['-I' + str(source / 'include'), '-I' + str(source / 'third_party'),
               '-I' + str(source / 'third_party/sqlite')]
    flags = ['-std=c++17', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Wpedantic', '-ffp-contract=off', '-pthread']
    units = [('core', source / 'src/core.cpp'), ('dng', source / 'src/dng.cpp'),
             ('sha256', source / 'src/sha256.cpp'), ('calibration', source / 'src/calibration.cpp'),
             ('daemon', source / 'src/daemon.cpp'), ('worker', source / 'src/worker.cpp'),
             ('camerad', source / 'src/camerad.cpp'), ('test_core', source / 'tests/test_core.cpp'),
             ('test_daemon', source / 'tests/test_daemon.cpp'), ('test_sha256', source / 'tests/test_sha256.cpp'),
             ('test_calibration', source / 'tests/test_calibration.cpp')]
    compilation = [[cxx, *flags, *include, '-c', path, '-o', destination / (name + '.o')]
                   for name, path in units]
    compilation.append([cc, '-O2', '-DNDEBUG', '-DSQLITE_THREADSAFE=1', '-DSQLITE_DQS=0', '-pthread',
                        '-c', source / 'third_party/sqlite/sqlite3.c', '-o', destination / 'sqlite3.o'])
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        list(executor.map(run, compilation))
    groups = {
        'hf-core-tests': ['test_core', 'core'],
        'hf-daemon-tests': ['test_daemon', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
        'hf-sha256-tests': ['test_sha256', 'sha256'],
        'hf-calibration-tests': ['test_calibration', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
        'hf-native-worker': ['worker', 'dng', 'core'],
        'hf-camerad': ['camerad', 'daemon', 'dng', 'core', 'sqlite3', 'sha256', 'calibration'],
    }
    for name, objects in groups.items():
        run([cxx, *(destination / (item + '.o') for item in objects), '-pthread', '-ldl', '-o', destination / name])
    run([destination / 'hf-core-tests'])
    run([destination / 'hf-daemon-tests'])
    run([destination / 'hf-sha256-tests'])
    run([destination / 'hf-calibration-tests', root / 'camera/calibration/fixtures'])
    environment = dict(os.environ, HF_NATIVE_DAEMON=str(destination / 'hf-camerad'),
                       HF_NATIVE_WORKER=str(destination / 'hf-native-worker'))
    if python_tests:
        for pattern in PYTHON_TEST_PATTERNS:
            if not any((root / 'tests').glob(pattern)):
                raise RuntimeError('required regression tests are missing: ' + pattern)
            run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'], env=environment)
    if native_input_hashes(root) != inputs_before:
        raise RuntimeError('native inputs changed during build/tests; freeze the sources and rebuild')
    evidence = {
        'format': 'hf-sim-linux-build-evidence-v1', 'simulated': True,
        'platform': platform.platform(), 'architecture': platform.machine(),
        'python': sys.version, 'cc': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        'cxx': subprocess.check_output([cxx, '--version'], text=True).splitlines()[0],
        'commands': commands, 'artifacts': {}, 'native_tests_passed': True,
        'native_input_sha256': inputs_before,
        'python_test_patterns': list(PYTHON_TEST_PATTERNS) if python_tests else [],
    }
    for name in groups:
        evidence['artifacts'][name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
    (destination / 'build-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('HF_NATIVE_DAEMON=' + str(destination / 'hf-camerad'), flush=True)
    print('HF_NATIVE_WORKER=' + str(destination / 'hf-native-worker'), flush=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build/linux-native'))
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--native-tests-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error('--jobs must be between 1 and 16')
    build(Path(__file__).resolve().parents[1], args.build_dir, jobs=args.jobs,
          python_tests=not args.native_tests_only)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Linux build failed: ' + str(error), file=sys.stderr)
        sys.exit(1)

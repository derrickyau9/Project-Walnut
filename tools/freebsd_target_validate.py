"""Execute existing cross-built camera tests on actual nonroot FreeBSD ARM64.

No compiler, configure step, host SDK, package installation or emulator is used.
The receipt binds real target execution to the original cross-build bytes and
source inventory. Synthetic header/parser unit fixtures are never such proof.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from tools import freebsd_build as common
from tools import freebsd_cross_build as cross


ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'DaOS-freebsd-arm64-target-validation-v1'
PYTHON_TEST_COUNT = 98
CTEST_EXECUTABLES = {'native-core': 'hf-core-tests', 'native-authority': 'hf-daemon-tests',
                     'native-sha256': 'hf-sha256-tests', 'native-calibration': 'hf-calibration-tests'}
_SHA = re.compile(r'[0-9a-f]{64}')
ALLOWED_SKIPS = {
    **{'tests.test_native_staging_integrity.NativeStagingIntegrityTests.' + name:
       'requires Linux /proc, a C compiler and LD_PRELOAD' for name in (
           'test_concurrent_final_is_preserved_without_overwrite',
           'test_fifo_replacement_is_bounded_preserved_and_not_published',
           'test_in_place_modified_contents_are_not_published_or_removed',
           'test_same_size_replacement_is_preserved_and_not_published',
           'test_symlink_replacement_preserves_target_and_is_not_published',
           'test_untampered_closed_staging_completes')},
    **{'tests.test_calibration_activation.' + name + '.test_bound_record_is_explicit_v2_and_does_not_silently_change_v1_schema':
       'optional jsonschema validator' for name in (
           'NativeCalibrationActivationTests', 'ReferenceCalibrationActivationTests')},
}


def require_target():
    machine = common.require_freebsd()
    if machine not in ('arm64', 'aarch64') or os.uname().release.split('-')[0] != '15.1':
        raise RuntimeError('DaOS target validation requires actual ARM64 FreeBSD15.1 execution; an amd64 host or ELF parser is not target execution.')
    if os.geteuid() != 1000:
        raise RuntimeError('DaOS target validation requires the ordinary camera user UID1000, never root.')
    return machine


def _recorded_sources(root, inventory):
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError('cross-build source inventory is missing')
    for name, checksum in inventory.items():
        if not isinstance(name, str):
            raise ValueError('cross-build source paths must be strings')
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts
                or path.as_posix() != name or not isinstance(checksum, str)
                or _SHA.fullmatch(checksum) is None):
            raise ValueError('cross-build source inventory has an unsafe path or checksum')
    actual = cross.cross_source_inputs(root)
    if actual != inventory:
        raise ValueError('target project source differs from the completed cross-build inventory')
    return actual


def validate_cross(root, binary_dir, cross_evidence):
    """Read/inspect only: consistency checks are not target test execution."""
    root = Path(root).resolve(strict=True)
    supplied = Path(binary_dir)
    if not supplied.is_absolute():
        supplied = root / supplied
    if supplied.is_symlink():
        raise ValueError('cross-built binary directory cannot be a link')
    for parent in (supplied.absolute(), *supplied.absolute().parents):
        if parent.is_symlink():
            raise ValueError('linked cross-built directory ancestors are refused')
    directory = supplied.resolve(strict=True)
    if not directory.is_dir() or not directory.is_relative_to(root / 'build'):
        raise ValueError('cross-built binaries must be in this project build tree')
    for parent in (directory, *directory.parents):
        if parent.is_symlink():
            raise ValueError('linked cross-built directory ancestors are refused')
    record = cross._json_file(cross_evidence)
    if not isinstance(record, dict):
        raise ValueError('cross-build receipt must be an object')
    target = record.get('target', {})
    sdk = record.get('sysroot', {})
    if not isinstance(target, dict) or not isinstance(sdk, dict):
        raise ValueError('cross-build target and source SDK metadata must be objects')
    if (record.get('format') != 'DaOS-freebsd-arm64-cross-build-v1'
            or record.get('status') != 'completed' or record.get('passed') is not True
            or record.get('build_kind') != 'cross' or target.get('architecture') != 'arm64'
            or target.get('elf_machine') != 183 or target.get('osabi') != 9
            or target.get('triple') != cross.TARGET
            or sdk.get('base_source_commit') != cross.SOURCE_COMMIT
            or sdk.get('base_release') != '15.1-RELEASE-p6'
            or record.get('physical_camera_validated') is not False):
        raise ValueError('target validation needs a completed pinned ARM64 p6 cross-build receipt')
    sources = _recorded_sources(root, record.get('source_input_sha256'))
    recorded_artifacts = record.get('artifacts')
    if not isinstance(recorded_artifacts, dict) or set(recorded_artifacts) != set(common.ARTIFACTS):
        raise ValueError('cross-build receipt must name all six native artifacts')
    artifacts = {}
    for name in common.ARTIFACTS:
        actual = common.inspect_freebsd_elf(directory / name, 183)
        if actual != recorded_artifacts[name]:
            raise ValueError('target binary differs from the cross-build receipt: ' + name)
        artifacts[name] = actual
    testfile = directory / 'CTestTestfile.cmake'
    if testfile.is_symlink() or not testfile.is_file() or testfile.stat().st_size > 1024 * 1024:
        raise ValueError('original bounded CTestTestfile.cmake must accompany the cross-built binaries')
    return {'cross_build': record, 'cross_evidence_sha256': common.sha(cross_evidence),
            'source_input_sha256': sources, 'artifacts': artifacts,
            'ctest_testfile_sha256': common.sha(testfile), 'binary_directory': str(directory)}


def _calibration_argument(value, root):
    """Resolve CMake's native/../calibration spelling without following links."""
    if not isinstance(value, str) or '\0' in value:
        raise ValueError('CTest calibration argument must be an absolute contained directory')
    root = Path(root).resolve(strict=True)
    supplied = Path(value)
    if not supplied.is_absolute() or not supplied.is_relative_to(root):
        raise ValueError('CTest calibration argument must remain inside the project')
    depth = 0
    for part in supplied.relative_to(root).parts:
        depth += -1 if part == '..' else 1
        if depth < 0:
            raise ValueError('CTest calibration argument cannot escape and reenter the project')
    # Check the original components before resolve removes native/..; a linked
    # native directory must not change what that parent traversal means.
    for path in (supplied, *supplied.parents):
        if path.is_symlink():
            raise ValueError('linked CTest calibration argument or ancestor is refused')
    expected = root / 'camera/calibration/fixtures'
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as error:
        raise ValueError('CTest calibration argument must exist within the project') from error
    if resolved != expected or not resolved.is_dir():
        raise ValueError('CTest calibration argument must name the verified calibration fixtures')


def validate_ctest_listing(value, directory, root):
    tests = value.get('tests') if isinstance(value, dict) else None
    if (not isinstance(tests, list) or len(tests) != 4
            or {item.get('name') for item in tests if isinstance(item, dict)} != common.CTEST_NAMES):
        raise ValueError('actual CTest listing must contain exactly the four native targets')
    for item in tests:
        command = item.get('command')
        calibration = item['name'] == 'native-calibration'
        if (not isinstance(command, list) or len(command) != (2 if calibration else 1)
                or command[0] != str(Path(directory) / CTEST_EXECUTABLES[item['name']])):
            raise ValueError('CTest target command is not the verified binary and calibration fixture path')
        if calibration:
            _calibration_argument(command[1], root)


def validate_ctest_results(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('CTest result must be a bounded regular file')
    cases = ET.parse(path).getroot().findall('.//testcase')
    if (len(cases) != 4 or {item.attrib.get('name') for item in cases} != common.CTEST_NAMES
            or any(item.find('failure') is not None or item.find('error') is not None
                   or item.find('skipped') is not None for item in cases)):
        raise ValueError('CTest results do not establish four actual passing native targets')
    return {'passed': True, 'ran': 4, 'targets': sorted(common.CTEST_NAMES),
            'result_sha256': common.sha(path)}


def validate_python_results(value):
    if (not isinstance(value, dict) or value.get('format') != 'hf-freebsd-native-python-tests-v1'
            or value.get('passed') is not True or value.get('ran') != PYTHON_TEST_COUNT
            or value.get('failures') != [] or value.get('errors') != []):
        raise ValueError('focused ARM64 Python suite must run all98 tests with no failures or errors')
    successes, skips = value.get('successful_tests'), value.get('skipped')
    if (not isinstance(successes, list) or not isinstance(skips, list)
            or not all(isinstance(item, str) and item for item in successes)
            or not all(isinstance(item, dict) and isinstance(item.get('test'), str)
                       and item['test'] and isinstance(item.get('reason'), str) and item['reason'] for item in skips)
            or not successes or value.get('passed_count') != len(successes)
            or len(successes) + len(skips) != PYTHON_TEST_COUNT):
        raise ValueError('Python result must preserve every actual success and explicit skip')
    identifiers = successes + [item['test'] for item in skips]
    if len(set(identifiers)) != PYTHON_TEST_COUNT or any(
            not any(name.startswith(module + '.') for module in common.PYTHON_TEST_MODULES) for name in identifiers):
        raise ValueError('Python results duplicate tests or include tests outside the pinned focused modules')
    if any(ALLOWED_SKIPS.get(item['test']) != item['reason'] for item in skips):
        raise ValueError('Python skip is not a documented Linux-only or optional-jsonschema check; native target tests must execute')
    return value


def validate(root, *, binary_dir, cross_evidence, destination):
    machine = require_target()  # Before SDK/source reads, tool lookup or writes.
    root = Path(root).resolve(strict=True)
    destination = common.validate_destination(root, destination)
    initial = validate_cross(root, binary_dir, cross_evidence)
    directory = Path(initial['binary_directory'])
    validator_inputs = {name: common.sha(root / name) for name in
                        ('tools/freebsd_target_validate.py', 'tests/test_freebsd_target_validate.py')}
    tools = {name: common.tool_identity(value) for name, value in
             (('ctest', 'ctest'), ('python', sys.executable), ('freebsd_version', 'freebsd-version'))}
    destination.mkdir(parents=True, exist_ok=False)
    evidence = destination / 'freebsd-arm64-target-validation.json'
    record = {'format': FORMAT, 'status': 'running', 'passed': False,
              'execution_kind': 'actual_target', 'build_kind': 'cross',
              'target': {'architecture': 'arm64', 'elf_machine': 183, 'osabi': 9},
              'machine': machine, 'sys_platform': sys.platform, 'uid': os.geteuid(),
              'uname': dict(zip(('sysname', 'nodename', 'release', 'version', 'machine'), os.uname())),
              'python': sys.version, 'simulated': True, 'target_runtime_validated': False,
              'physical_camera_validated': False, 'tools': tools, 'commands': [],
              'validator_source_sha256': validator_inputs, **initial}
    common._write_state(evidence, record)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
               HF_NATIVE_DAEMON=str(directory / 'hf-camerad'),
               HF_NATIVE_CAMERAD=str(directory / 'hf-camerad'),
               HF_R9_NATIVE_DAEMON=str(directory / 'hf-camerad'),
               HF_NATIVE_WORKER=str(directory / 'hf-native-worker'))
    for name in ('PYTHONPATH', 'PYTHONSTARTUP', 'CTEST_PARALLEL_LEVEL'):
        env.pop(name, None)
    def run(label, command, timeout=120):
        record['active_stage'] = label; common._write_state(evidence, record)
        result = common.run_bounded(command, cwd=root, log_path=destination / (label + '.log'),
                                    env=env, timeout=timeout)
        record['commands'].append(result); common._write_state(evidence, record)
        return destination / result['log']
    try:
        for name in ('ctest', 'python'):
            log = run(name + '-version', [tools[name]['invoked_path'], '--version'])
            tools[name]['version_output'] = log.read_text(errors='replace')[:8192]
        log = run('freebsd-version', [tools['freebsd_version']['invoked_path'], '-ku'])
        record['freebsd_version_kernel_userland'] = log.read_text().splitlines()
        listing = run('ctest-list', [tools['ctest']['invoked_path'], '--test-dir', directory, '--show-only=json-v1'])
        validate_ctest_listing(cross._json_file(listing), directory, root)
        results = destination / 'ctest-results.xml'
        run('ctest', [tools['ctest']['invoked_path'], '--test-dir', directory,
                      '--output-on-failure', '--timeout', '180', '--parallel', '1',
                      '--output-junit', results], timeout=900)
        record['ctest'] = validate_ctest_results(results)
        report = destination / 'python-tests.json'
        run('python-tests', [sys.executable, '-B', '-c', common._PYTHON_RUNNER,
                             report, *common.PYTHON_TEST_MODULES], timeout=1800)
        record['python_tests'] = validate_python_results(cross._json_file(report))
        record['python_tests'].update(modules=list(common.PYTHON_TEST_MODULES), report_sha256=common.sha(report))
        if validate_cross(root, binary_dir, cross_evidence) != initial:
            raise RuntimeError('cross receipt, source, binary or CTest input changed during actual target tests')
        for name, checksum in validator_inputs.items():
            if common.sha(root / name) != checksum:
                raise RuntimeError('target validator source changed during execution')
        for value in tools.values():
            if common.sha(value['resolved_path']) != value['sha256']:
                raise RuntimeError('target test tool changed during execution')
        record.update(status='completed', passed=True, active_stage=None, target_runtime_validated=True,
                      scope='actual ARM64 FreeBSD execution of cross-built synthetic-camera native and Python tests; no target recompilation, UI, physical hardware or power-loss validation')
        common._write_state(evidence, record)
        return record
    except BaseException as error:
        record.update(status='failed', passed=False, error_type=type(error).__name__, error=str(error))
        common._write_state(evidence, record)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary-dir', type=Path, required=True)
    parser.add_argument('--cross-evidence', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('build/arm64-validation-001'))
    args = parser.parse_args(argv)
    print(json.dumps(validate(ROOT, binary_dir=args.binary_dir, cross_evidence=args.cross_evidence,
                              destination=args.output_dir), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('DaOS ARM64 target validation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

"""Host-safe synthetic parser/guard checks, never ARM64 execution proof."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import freebsd_build as common
from tools import freebsd_target_validate as target


def synthetic_elf(path, machine=183):
    identity = bytearray(b'\x7fELF\x02\x01\x01' + bytes(9)); identity[7] = 9
    header = struct.pack('<16sHHIQQQIHHHHHH', bytes(identity), 2, machine, 1,
                         0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    program = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, 120, 120, 4096)
    path.write_bytes(header + program)


class CrossReceiptParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.binary = self.root / 'build/cross'; self.binary.mkdir(parents=True)
        artifacts = {}
        for name in common.ARTIFACTS:
            path = self.binary / name; synthetic_elf(path)
            artifacts[name] = common.inspect_freebsd_elf(path, 183)
        (self.binary / 'CTestTestfile.cmake').write_text('# synthetic parsing fixture, never executed\n')
        self.sources = {'camera/native/CMakeLists.txt': '1' * 64}
        self.state = {'format': 'DaOS-freebsd-arm64-cross-build-v1', 'status': 'completed',
                      'passed': True, 'build_kind': 'cross', 'physical_camera_validated': False,
                      'target': {'architecture': 'arm64', 'elf_machine': 183,
                                 'osabi': 9, 'triple': target.cross.TARGET},
                      'sysroot': {'base_source_commit': target.cross.SOURCE_COMMIT,
                                  'base_release': '15.1-RELEASE-p6'},
                      'source_input_sha256': self.sources, 'artifacts': artifacts}
        self.receipt = self.root / 'cross-evidence.json'; self.write()
        patch = mock.patch.object(target.cross, 'cross_source_inputs', return_value=self.sources)
        patch.start(); self.addCleanup(patch.stop)

    def write(self): self.receipt.write_text(json.dumps(self.state))

    def test_parse_binds_six_arm64_files_and_needs_no_build_host_sdk(self):
        result = target.validate_cross(self.root, self.binary, self.receipt)
        self.assertEqual(6, len(result['artifacts']))
        self.assertEqual(hashlib.sha256(self.receipt.read_bytes()).hexdigest(), result['cross_evidence_sha256'])
        self.assertNotIn('passed', result)
        self.assertNotIn('target_runtime_validated', result)

    def test_incomplete_amd64_unpinned_or_native_receipts_are_refused(self):
        for field, value in (('status', 'running'), ('passed', False), ('build_kind', 'native'),
                             ('format', 'hf-freebsd-native-build-v1'), ('target.architecture', 'amd64'),
                             ('target.elf_machine', 62), ('target.osabi', 0),
                             ('sysroot.base_source_commit', '0' * 40), ('sysroot.base_release', '15.1-RELEASE')):
            original = copy.deepcopy(self.state); row = self.state; parts = field.split('.')
            for part in parts[:-1]: row = row[part]
            row[parts[-1]] = value; self.write()
            with self.subTest(field=field), self.assertRaises(ValueError):
                target.validate_cross(self.root, self.binary, self.receipt)
            self.state = original

    def test_changed_or_host_architecture_binary_cannot_match_receipt(self):
        path = self.binary / 'hf-camerad'
        for machine in (62, 183):
            synthetic_elf(path, machine)
            if machine == 183: path.write_bytes(path.read_bytes() + b'changed')
            with self.assertRaises(ValueError): target.validate_cross(self.root, self.binary, self.receipt)

    def test_changed_source_inventory_or_unsafe_paths_are_refused(self):
        self.state['source_input_sha256'] = {'../outside': '1' * 64}; self.write()
        with self.assertRaises(ValueError): target.validate_cross(self.root, self.binary, self.receipt)
        self.state['source_input_sha256'] = {'camera/native/CMakeLists.txt': '2' * 64}; self.write()
        with self.assertRaises(ValueError): target.validate_cross(self.root, self.binary, self.receipt)

    def test_missing_ctest_file_and_outside_binary_tree_are_refused(self):
        with self.assertRaises(ValueError): target.validate_cross(self.root, self.root, self.receipt)
        (self.binary / 'CTestTestfile.cmake').unlink()
        with self.assertRaises(ValueError): target.validate_cross(self.root, self.binary, self.receipt)

    @unittest.skipUnless(os.name == 'posix', 'actual POSIX linked target-path checks')
    def test_linked_binary_ancestor_is_refused_without_changes(self):
        alias = self.root / 'build/alias'; alias.symlink_to(self.binary, target_is_directory=True)
        with self.assertRaises(ValueError): target.validate_cross(self.root, alias, self.receipt)
        self.assertTrue(alias.is_symlink())


class TestResultParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'camera/native').mkdir(parents=True)
        (self.root / 'camera/calibration/fixtures').mkdir(parents=True)

    def listing(self, directory, root):
        rows = []
        for name, executable in target.CTEST_EXECUTABLES.items():
            command = [str(directory / executable)]
            if name == 'native-calibration': command.append(str(root / 'camera/calibration/fixtures'))
            rows.append({'name': name, 'command': command})
        return {'tests': rows}

    def test_ctest_listing_cannot_skip_or_replace_actual_target_commands(self):
        root = self.root; directory = root / 'build/cross'
        value = self.listing(directory, root); target.validate_ctest_listing(value, directory, root)
        for changed in ({'tests': value['tests'][:-1]}, self.listing(directory, root)):
            if len(changed['tests']) == 4: changed['tests'][0]['command'] = ['/usr/bin/true']
            with self.assertRaises(ValueError): target.validate_ctest_listing(changed, directory, root)

    def test_actual_cmake_native_parent_calibration_argument_is_accepted(self):
        directory = self.root / 'build/cross'; value = self.listing(directory, self.root)
        calibration = next(row for row in value['tests'] if row['name'] == 'native-calibration')
        # Exact spelling observed in the real ARM64 cross-build CTestTestfile:
        # /opt/hasslefree/camera/native/../calibration/fixtures.
        calibration['command'][1] = str(self.root / 'camera/native/../calibration/fixtures')
        target.validate_ctest_listing(value, directory, self.root)

    def test_calibration_argument_cannot_escape_reenter_or_add_arguments(self):
        directory = self.root / 'build/cross'
        for argument in (str(self.root.parent),
                         str(self.root / '..' / self.root.name / 'camera/calibration/fixtures'),
                         str(self.root / 'camera/native'), 'camera/calibration/fixtures'):
            value = self.listing(directory, self.root)
            calibration = next(row for row in value['tests'] if row['name'] == 'native-calibration')
            calibration['command'][1] = argument
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                target.validate_ctest_listing(value, directory, self.root)
        value = self.listing(directory, self.root)
        value['tests'][-1]['command'].append('--extra')
        with self.assertRaises(ValueError): target.validate_ctest_listing(value, directory, self.root)

    @unittest.skipUnless(os.name == 'posix', 'actual POSIX calibration-ancestor link checks')
    def test_native_parent_spelling_cannot_hide_a_linked_directory(self):
        native = self.root / 'camera/native'; native.rmdir()
        real = self.root / 'camera/real-native'; real.mkdir()
        native.symlink_to(real, target_is_directory=True)
        argument = str(self.root / 'camera/native/../calibration/fixtures')
        with self.assertRaisesRegex(ValueError, 'linked CTest'):
            target._calibration_argument(argument, self.root)
        self.assertTrue(native.is_symlink())

    def test_ctest_results_require_all_four_cases_without_failure_or_skip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'results.xml'
            rows = ''.join('<testcase name="' + name + '"/>' for name in sorted(common.CTEST_NAMES))
            path.write_text('<testsuite>' + rows + '</testsuite>')
            result = target.validate_ctest_results(path)
            self.assertEqual(4, result['ran'])
            for tag in ('skipped', 'failure', 'error'):
                path.write_text('<testsuite>' + rows.replace('/>', '><' + tag + '/></testcase>', 1) + '</testsuite>')
                with self.assertRaises(ValueError): target.validate_ctest_results(path)

    def python_report(self):
        skips = [{'test': name, 'reason': reason} for name, reason in list(target.ALLOWED_SKIPS.items())[:6]]
        successes = ['tests.test_native_daemon.SyntheticParserFixture.test_' + str(n) for n in range(92)]
        return {'format': 'hf-freebsd-native-python-tests-v1', 'passed': True, 'ran': 98,
                'passed_count': 92, 'successful_tests': successes, 'skipped': skips,
                'failures': [], 'errors': []}

    def test_python_summary_preserves_successes_and_exact_legitimate_skips(self):
        value = self.python_report(); self.assertIs(value, target.validate_python_results(value))
        for field, changed in (('passed', False), ('ran', 97), ('passed_count', 98), ('errors', ['broken'])):
            item = self.python_report(); item[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError): target.validate_python_results(item)

    def test_native_skips_duplicates_or_outside_modules_never_count_as_coverage(self):
        for mode in ('native-skip', 'duplicate', 'outside'):
            value = self.python_report()
            if mode == 'native-skip': value['skipped'][0] = {'test': 'tests.test_native_daemon.Test.test_x', 'reason': 'missing daemon'}
            elif mode == 'duplicate': value['successful_tests'][0] = value['successful_tests'][1]
            else: value['successful_tests'][0] = 'tests.unrelated.Test.test_x'
            with self.subTest(mode=mode), self.assertRaises(ValueError): target.validate_python_results(value)


@unittest.skipIf(sys.platform.startswith('freebsd'), 'actual non-FreeBSD host refusal')
class ActualHostRefusalTests(unittest.TestCase):
    def test_actual_platform_refused_before_reads_tools_or_output_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / 'build/new'
            with (mock.patch.object(target, 'validate_cross') as inputs,
                  mock.patch.object(common, 'tool_identity') as tools,
                  self.assertRaisesRegex(RuntimeError, 'actual FreeBSD')):
                target.validate(root, binary_dir=root/'missing', cross_evidence=root/'none', destination=output)
            inputs.assert_not_called(); tools.assert_not_called(); self.assertFalse(output.exists())

    def test_environment_does_not_spoof_real_arm64_target_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'not-created'
            result = subprocess.run([sys.executable, '-B', '-m', 'tools.freebsd_target_validate',
                                     '--binary-dir', temp, '--cross-evidence', temp + '/missing.json',
                                     '--output-dir', str(output)],
                                    cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True,
                                    env=dict(os.environ, HF_PLATFORM='FreeBSD', TARGET_ARCH='aarch64'), timeout=15)
            self.assertNotEqual(0, result.returncode); self.assertIn('actual FreeBSD', result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()

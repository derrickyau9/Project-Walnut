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

from system.arm64.runtime import FORMAT, launch_plan, storage_path, validate_package
from tools.arm64_build import source_inputs
from tools.target_package import stage

ROOT = Path(__file__).resolve().parents[1]


def elf_fixture():
    """A structural test fixture, never executed or represented as a tested program."""
    ident = b'\x7fELF' + bytes((2, 1, 1)) + bytes(9)
    return (struct.pack('<16sHHIQQQIHHHHHH', ident, 2, 183, 1, 0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0) +
            struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, 128, 128, 4096)).ljust(128, b'\0')


def tree_hash(root):
    values = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in Path(root).rglob('*') if path.is_file()}
    return values


class TargetPackageTests(unittest.TestCase):
    def fixture(self, temporary):
        root, native = Path(temporary) / 'source', Path(temporary) / 'native'
        text_files = ('camera/native/CMakeLists.txt', 'camera/native/src/core.cpp', 'tools/linux_build.py',
                      'camera/calibration/fixtures/binding-uncalibrated.json',
                      'camera/calibration/fixtures/binding-synthetic.json',
                      'camera/calibration/fixtures/binding-unicode.json',
                      'system/arm64/toolchain-lock.json', 'camera/camerad/server.py', 'simulation/bayer.py',
                      'daos/console/index.html', 'tools/__init__.py', 'tools/diagnostics.py',
                      'licenses/README.md', 'camera/native/third_party/README.md',
                      'camera/native/third_party/manifest.json', 'camera/native/third_party/nlohmann/LICENSE.MIT',
                      'system/arm64/notices/gcc-cross-copyright.txt', 'system/arm64/notices/glibc-cross-copyright.txt',
                      'system/arm64/notices/sources.json')
        for relative in text_files:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('pending rights / synthetic structural fixture', encoding='utf-8')
        (root / 'tools/__init__.py').write_text('# fixture package\n', encoding='utf-8')
        for relative in ('system/arm64/runtime.py', 'tools/arm64_elf.py'):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / relative).read_bytes())
        native.mkdir()
        artifacts = {}
        for name in ('hf-camerad', 'hf-native-worker'):
            data = elf_fixture(); (native / name).write_bytes(data)
            artifacts[name] = {'sha256': hashlib.sha256(data).hexdigest()}
        evidence = {'format': 'hf-arm64-cross-build-v1', 'tests_passed': True,
                    'target_triple': 'aarch64-linux-gnu', 'native_input_sha256': source_inputs(root),
                    'artifacts': artifacts, 'unit_test_fixture_only': True}
        (native / 'arm64-build-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        return root, native, Path(temporary) / 'new-release'

    def test_distinct_manifest_and_runtime_notices_without_emulator_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            result = stage(root, native, output, package_id='arm64-sim-test')
            self.assertEqual(result['format'], FORMAT)
            self.assertEqual(result['architecture'], 'aarch64')
            self.assertEqual(result['project_rights'], 'pending')
            self.assertEqual(validate_package(output), result)
            names = set(tree_hash(output))
            self.assertFalse(any('qemu' in name.lower() for name in names))
            self.assertEqual({name for name in names if name.startswith('bin/')}, {'bin/hf-camerad', 'bin/hf-native-worker'})
            self.assertIn('notices/gcc-cross-copyright.txt', names)
            self.assertIn('notices/glibc-cross-copyright.txt', names)
            self.assertIn('runtime/tools/diagnostics.py', names)

    def test_existing_release_and_invalid_id_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            with self.assertRaises(ValueError): stage(root, native, output, package_id='../invalid')
            self.assertFalse(output.exists())
            stage(root, native, output, package_id='first')
            before = tree_hash(output)
            with self.assertRaises(ValueError): stage(root, native, output, package_id='second')
            self.assertEqual(tree_hash(output), before)

    def test_changed_native_source_or_artifact_refused_before_writing(self):
        for changed in ('source', 'artifact'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root, native, output = self.fixture(temporary)
                if changed == 'source': (root / 'camera/native/src/core.cpp').write_text('changed source')
                else: (native / 'hf-camerad').write_bytes(b'changed artifact')
                with self.assertRaises(ValueError): stage(root, native, output, package_id='invalid-build')
                self.assertFalse(output.exists())

    def test_tampered_files_and_host_format_are_not_valid_arm_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            stage(root, native, output, package_id='integrity')
            (output / 'runtime/camera/camerad/server.py').write_text('changed runtime')
            with self.assertRaises(ValueError): validate_package(output)
            manifest = json.loads((output / 'manifest.json').read_text())
            manifest['format'] = 'hf-sim-host-bundle-v1'
            (output / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): validate_package(output)

    def test_persistent_storage_cannot_overlap_release_or_camera_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            stage(root, native, output, package_id='paths')
            with self.assertRaises(ValueError): storage_path(output, 'relative-data')
            with self.assertRaises(ValueError): storage_path(output, output / 'new-data')
            camera = Path(temporary) / 'camera'
            camera.mkdir(); (camera / 'journal.sqlite3').write_bytes(b'owner journal sentinel')
            with self.assertRaises(ValueError): storage_path(output, camera / 'photos/new-data')
            outside = Path(temporary) / 'persistent-arm64'
            self.assertEqual(storage_path(output, outside), outside)
            self.assertFalse(outside.exists())

    def test_runtime_refuses_actual_wrong_host_before_creating_data(self):
        import platform
        if sys.platform == 'linux' and platform.machine().lower() in ('aarch64', 'arm64'):
            self.skipTest('this regression requires an actual non-ARM64 Python host')
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            stage(root, native, output, package_id='wrong-host')
            before = tree_hash(output)
            data = Path(temporary) / 'must-not-be-created'
            result = subprocess.run([sys.executable, '-B', str(output / 'runtime/system/arm64/runtime.py'),
                                     '--release-root', str(output), '--data-dir', str(data)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('requires a Linux aarch64/arm64 Python host', result.stderr)
            self.assertFalse(data.exists())
            self.assertEqual(tree_hash(output), before)

    def test_unit_launch_plan_uses_only_packaged_native_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            stage(root, native, output, package_id='plan')
            data = Path(temporary) / 'persistent'
            with mock.patch('system.arm64.runtime.require_native_host'):
                runtime, selected, command = launch_plan(output, data)
            self.assertEqual(runtime, output / 'runtime')
            self.assertEqual(selected, data)
            self.assertEqual(command[command.index('--native-daemon') + 1], str(output / 'bin/hf-camerad'))
            self.assertFalse(any('qemu' in part for part in command))
            self.assertIn('-I', command)
            self.assertIn('-S', command)
            self.assertFalse(data.exists())

    def test_verified_regular_packages_win_over_external_pythonpath_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, output = self.fixture(temporary)
            stage(root, native, output, package_id='imports')
            external = Path(temporary) / 'external'
            for name in ('camera', 'daos', 'simulation', 'system', 'tools'):
                (external / name).mkdir(parents=True)
                (external / name / '__init__.py').write_text('ORIGIN="external-shadow"\n')
            environment = dict(os.environ, PYTHONPATH=str(external), PYTHONNOUSERSITE='1')
            code = 'import camera,daos,simulation,system,tools,json; print(json.dumps([m.__file__ for m in (camera,daos,simulation,system,tools)]))'
            result = subprocess.run([sys.executable, '-S', '-B', '-c', code], cwd=output / 'runtime', env=environment,
                                    capture_output=True, text=True, check=True, timeout=10)
            for path in json.loads(result.stdout):
                self.assertTrue(Path(path).is_relative_to(output / 'runtime'))
            validate_package(output)

    def test_staging_inside_original_source_is_refused_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, native, _ = self.fixture(temporary)
            before = tree_hash(root)
            output = root / 'camera/nested-release'
            with self.assertRaises(ValueError): stage(root, native, output, package_id='source-pollution')
            self.assertFalse(output.exists())
            self.assertEqual(tree_hash(root), before)


if __name__ == '__main__':
    unittest.main()

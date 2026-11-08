import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.linux_package import package
from tools.linux_build import native_input_hashes
from system.linux.recovery import RecoveryError, validate_bundle


class PackageTests(unittest.TestCase):
    def fixture(self, root):
        root = Path(root)
        source, build = root / 'source', root / 'build'
        for relative in ('camera/camerad/server.py', 'simulation/bayer.py', 'daos/console/index.html',
                         'system/linux/__init__.py', 'system/linux/serve.py', 'system/linux/hf-sim.service',
                         'tools/__init__.py', 'tools/diagnostics.py',
                         'tools/linux_build.py', 'camera/native/CMakeLists.txt', 'camera/native/src/core.cpp',
                         'camera/calibration/fixtures/binding-uncalibrated.json',
                         'camera/calibration/fixtures/binding-synthetic.json',
                         'camera/calibration/fixtures/binding-unicode.json',
                         'camera/native/third_party/README.md', 'camera/native/third_party/manifest.json',
                         'camera/native/third_party/nlohmann/LICENSE.MIT'):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture runtime or preserved notice', encoding='utf-8')
        build.mkdir()
        artifacts = {}
        for name in ('hf-camerad', 'hf-native-worker'):
            data = b'\x7fELFtest fixture, not an executable'
            (build / name).write_bytes(data)
            artifacts[name] = hashlib.sha256(data).hexdigest()
        evidence = {'format': 'hf-sim-linux-build-evidence-v1', 'native_tests_passed': True,
                    'platform': 'Linux-fixture', 'architecture': 'x86_64', 'artifacts': artifacts,
                    'native_input_sha256': native_input_hashes(source)}
        (build / 'build-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        return source, build

    def test_stage_bundle_checksums_runtime_and_preserved_notices(self):
        with tempfile.TemporaryDirectory() as root:
            source, build = self.fixture(root)
            output = Path(root) / 'new-package'
            result = package(source, build, output, package_id='sim-test-001')
            self.assertEqual(result['package_id'], 'sim-test-001')
            self.assertEqual(validate_bundle(output), result)
            self.assertTrue((output / 'runtime/camera/camerad/server.py').is_file())
            self.assertTrue((output / 'runtime/tools/diagnostics.py').is_file())
            self.assertTrue((output / 'licenses/nlohmann-LICENSE.MIT').is_file())
            self.assertFalse((output / 'photos').exists())
            with self.assertRaises(ValueError): package(source, build, output, package_id='overwrite')
            (output / 'bin/hf-camerad').write_bytes(b'corrupted')
            with self.assertRaises(RecoveryError): validate_bundle(output)

    def test_reject_untested_or_changed_build_and_windows_binary(self):
        for mutation in ('untested', 'changed', 'windows'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                source, build = self.fixture(root)
                evidence_path = build / 'build-evidence.json'
                evidence = json.loads(evidence_path.read_text())
                if mutation == 'untested': evidence['native_tests_passed'] = False
                elif mutation == 'changed': (build / 'hf-camerad').write_bytes(b'\x7fELFchanged')
                else:
                    data = b'MZWindows executable'
                    (build / 'hf-camerad').write_bytes(data)
                    evidence['artifacts']['hf-camerad'] = hashlib.sha256(data).hexdigest()
                evidence_path.write_text(json.dumps(evidence))
                with self.assertRaises(ValueError): package(source, build, Path(root) / 'output', package_id='invalid')

    def test_reject_stale_native_source_or_missing_source_evidence_before_writing(self):
        for mutation in ('changed_source', 'missing_hashes'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                source, build = self.fixture(root)
                output = Path(root) / 'output'
                if mutation == 'changed_source':
                    (source / 'camera/native/src/core.cpp').write_text('changed after the tested build')
                else:
                    evidence_path = build / 'build-evidence.json'
                    evidence = json.loads(evidence_path.read_text())
                    del evidence['native_input_sha256']
                    evidence_path.write_text(json.dumps(evidence))
                with self.assertRaises(ValueError): package(source, build, output, package_id='stale-build')
                self.assertFalse(output.exists())

    def test_invalid_package_identity_does_not_create_partial_output(self):
        with tempfile.TemporaryDirectory() as root:
            source, build = self.fixture(root)
            output = Path(root) / 'output'
            with self.assertRaises(ValueError): package(source, build, output, package_id='../invalid')
            self.assertFalse(output.exists())

    def test_executable_changed_during_copy_never_becomes_verified_bundle(self):
        from unittest import mock
        import shutil
        with tempfile.TemporaryDirectory() as root:
            source, build = self.fixture(root)
            original = shutil.copyfile
            def altered_copy(src, dst):
                result = original(src, dst)
                if Path(dst).name == 'hf-camerad':
                    Path(dst).write_bytes(b'\x7fELFuntested replacement during staging')
                return result
            with mock.patch('tools.linux_package.shutil.copyfile', side_effect=altered_copy):
                with self.assertRaises(ValueError): package(source, build, Path(root) / 'output', package_id='copy-race')


if __name__ == '__main__':
    unittest.main()

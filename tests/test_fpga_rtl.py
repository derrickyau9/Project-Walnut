"""Run the self-checking capture RTL with a real Icarus compiler/runtime."""
import importlib.util
import hashlib
import json
import shutil
import tempfile
from unittest.mock import patch
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hf_rtl_sim', ROOT / 'fpga/testbench/rtl_sim.py')
rtl_sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtl_sim)


class CaptureRtlTests(unittest.TestCase):
    def test_actual_rtl_scoreboard_and_error_matrix(self):
        try:
            rtl_sim.discover_tools()
        except rtl_sim.ToolUnavailable as error:
            self.skipTest(str(error))
        results = rtl_sim.run_matrix()
        self.assertEqual(6, len(results))
        for result in results:
            with self.subTest(parameters=result['parameters']):
                self.assertEqual(0, result['compile_returncode'])
                self.assertEqual(0, result['run_returncode'])
                self.assertIn('HF_RTL_PASS', result['stdout'])
                self.assertIn('Icarus Verilog', result['tool_versions']['iverilog'])

    def test_invalid_parameters_fail_the_actual_simulator(self):
        try:
            rtl_sim.discover_tools()
        except rtl_sim.ToolUnavailable as error:
            self.skipTest(str(error))
        with self.assertRaises(rtl_sim.RtlSimulationError) as caught:
            rtl_sim.run_iverilog(ROOT / 'fpga/testbench/tb_capture_stream.sv', top='tb_capture_stream',
                                 parameters={'FIFO_DEPTH': 0})
        self.assertNotEqual(0, caught.exception.result.get('run_returncode', caught.exception.result['compile_returncode']))


class ToolchainProvenanceTests(unittest.TestCase):
    def make_runtime(self, root, *, compiler=b'compiler-v1', runtime=b'runtime-v1'):
        binary = root / 'ucrt64/bin'
        binary.mkdir(parents=True)
        paths = {'iverilog': binary / 'iverilog.exe', 'vvp': binary / 'vvp.exe'}
        for name, content in [('iverilog', compiler), ('vvp', runtime)]:
            paths[name].write_bytes(content)
        manifest = {'source': 'test fixture', 'packages': [{'name': 'fixture-v1', 'files': [
            {'path': path.relative_to(root).as_posix(),
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths.values()]}]}
        (root / 'toolchain-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return {name: str(path) for name, path in paths.items()}

    def test_only_declared_locations_with_matching_hashes_attach_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'moved-runtime-v1'
            tools = self.make_runtime(root)
            result = rtl_sim.toolchain_provenance(tools)
            self.assertEqual('selected_executables_verified', result['toolchain_manifest_status'])
            self.assertEqual('test fixture', result['toolchain_manifest']['source'])
            verified = result['toolchain_manifest_verification']
            self.assertEqual(str((root / 'toolchain-manifest.json').resolve()), verified['manifest_path'])
            for name, path in tools.items():
                self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), result['tool_executable_sha256'][name])
                self.assertEqual(str(Path(path).resolve()), verified['executables'][name]['path'])
                self.assertEqual('fixture-v1', verified['executables'][name]['package'])

    def test_unrelated_global_temp_manifest_never_attributes_override_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = self.make_runtime(root / 'hf-iverilog-runtime')
            outside = root / 'other-toolchain/bin'
            outside.mkdir(parents=True)
            tools = {}
            for name, source in official.items():
                target = outside / Path(source).name
                shutil.copyfile(source, target)  # Even identical bytes are outside the declared locations.
                tools[name] = str(target)
            with patch.object(rtl_sim.tempfile, 'gettempdir', return_value=str(root)), \
                 patch.dict(rtl_sim.os.environ, {'HF_IVERILOG': tools['iverilog'], 'HF_VVP': tools['vvp']}):
                selected = rtl_sim.discover_tools()
                self.assertEqual(tools, selected)
                result = rtl_sim.toolchain_provenance(selected)
            self.assertNotIn('toolchain_manifest', result)
            self.assertNotIn('toolchain_manifest_verification', result)
            self.assertEqual({'iverilog', 'vvp'}, set(result['tool_executable_sha256']))

    def test_run_uses_selected_provenance_instead_of_an_unrelated_temp_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = self.make_runtime(root / 'hf-iverilog-runtime')
            outside = root / 'other/bin'
            outside.mkdir(parents=True)
            tools = {}
            for name, source in official.items():
                path = outside / Path(source).name
                path.write_bytes(b'unrelated ' + name.encode())
                tools[name] = str(path)

            def fake_process(command, **kwargs):
                output = 'Icarus Verilog fixture version' if '-V' in command else 'fixture output'
                return rtl_sim.subprocess.CompletedProcess(command, 0, output, '')

            with patch.object(rtl_sim, 'discover_tools', return_value=tools), \
                 patch.object(rtl_sim.tempfile, 'gettempdir', return_value=str(root)), \
                 patch.object(rtl_sim.subprocess, 'run', side_effect=fake_process):
                result = rtl_sim.run_iverilog(ROOT / 'fpga/testbench/tb_capture_stream.sv', top='tb_capture_stream')
            self.assertNotIn('toolchain_manifest', result)
            self.assertNotIn('toolchain_manifest_verification', result)
            self.assertEqual('no_matching_manifest_for_selected_executables', result['toolchain_manifest_status'])
            for name, path in tools.items():
                self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), result['tool_executable_sha256'][name])

    def test_path_discovery_is_also_bound_to_its_selected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_runtime(root / 'hf-iverilog-runtime')
            tools = self.make_runtime(root / 'path-selected-runtime')
            with patch.dict(rtl_sim.os.environ, {}, clear=True), \
                 patch.object(rtl_sim.shutil, 'which', side_effect=lambda name: tools[name]):
                selected = rtl_sim.discover_tools()
            result = rtl_sim.toolchain_provenance(selected)
            expected_manifest = (root / 'path-selected-runtime/toolchain-manifest.json').resolve()
            self.assertEqual(str(expected_manifest), result['toolchain_manifest_verification']['manifest_path'])
            self.assertTrue(all(Path(entry['path']).is_relative_to(root / 'path-selected-runtime')
                                for entry in result['toolchain_manifest_verification']['executables'].values()))

    def test_tampered_selected_executable_cannot_inherit_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = self.make_runtime(Path(directory) / 'runtime')
            Path(tools['vvp']).write_bytes(b'changed runtime')
            result = rtl_sim.toolchain_provenance(tools)
            self.assertNotIn('toolchain_manifest', result)
            self.assertTrue(any('SHA-256 mismatch' in rejection['reason']
                                for rejection in result['toolchain_manifest_rejected']))
            self.assertEqual(hashlib.sha256(b'changed runtime').hexdigest(), result['tool_executable_sha256']['vvp'])

    def test_mixed_runtime_locations_do_not_share_one_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.make_runtime(root / 'first')
            second = self.make_runtime(root / 'second')
            result = rtl_sim.toolchain_provenance({'iverilog': first['iverilog'], 'vvp': second['vvp']})
            self.assertNotIn('toolchain_manifest', result)
            self.assertIn('toolchain_manifest_rejected', result)

    def test_malformed_and_missing_manifests_leave_observed_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            tools = self.make_runtime(root)
            manifest = root / 'toolchain-manifest.json'
            for contents in ['not json', '[]', '{"packages":[]}', '{"packages":[null]}']:
                with self.subTest(contents=contents):
                    manifest.write_text(contents, encoding='utf-8')
                    result = rtl_sim.toolchain_provenance(tools)
                    self.assertNotIn('toolchain_manifest', result)
                    self.assertEqual(2, len(result['tool_executable_sha256']))
            manifest.unlink()
            result = rtl_sim.toolchain_provenance(tools)
            self.assertNotIn('toolchain_manifest', result)
            self.assertNotIn('toolchain_manifest_rejected', result)

    def test_undeclared_traversal_path_is_not_a_valid_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            tools = self.make_runtime(root)
            manifest_path = root / 'toolchain-manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['packages'][0]['files'][0]['path'] = '../runtime/ucrt64/bin/iverilog.exe'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            result = rtl_sim.toolchain_provenance(tools)
            self.assertNotIn('toolchain_manifest', result)


if __name__ == '__main__':
    unittest.main()

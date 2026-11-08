from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.build_native import ensure_build_type


class NativeBuildConfigurationTests(unittest.TestCase):
    def test_windows_ninja_style_single_config_sets_requested_build_type(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'CMakeCache.txt').write_text('CMAKE_GENERATOR:INTERNAL=Ninja\nCMAKE_BUILD_TYPE:STRING=\n')
            with mock.patch('tools.build_native.run') as run:
                ensure_build_type(['cmake', '-S', 'source', '-B', directory], directory, 'Debug')
                self.assertEqual(run.call_args.args[0][-1], '-DCMAKE_BUILD_TYPE=Debug')

    def test_multi_config_keeps_configuration_selection_on_build_command(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'CMakeCache.txt').write_text('CMAKE_CONFIGURATION_TYPES:STRING=Debug;Release\n')
            with mock.patch('tools.build_native.run') as run:
                ensure_build_type(['cmake'], directory, 'Release')
                run.assert_not_called()

    def test_existing_matching_single_config_needs_no_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'CMakeCache.txt').write_text('CMAKE_BUILD_TYPE:STRING=RelWithDebInfo\n')
            with mock.patch('tools.build_native.run') as run:
                ensure_build_type(['cmake'], directory, 'RelWithDebInfo')
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

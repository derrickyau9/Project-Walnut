from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import build_native
from tools.build_native import ensure_build_type


class NativeBuildConfigurationTests(unittest.TestCase):
    def configure_command(self, platform, environment=None, cached=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cmake=root/'cmake.exe'
            (root/('ctest.exe' if platform=='nt' else 'ctest')).touch()
            build=root/'output';build.mkdir()
            if cached:(build/'CMakeCache.txt').write_text('CMAKE_GENERATOR:INTERNAL=Visual Studio 18 2026\n')
            suffix='.exe' if platform=='nt' else ''
            for name in ('hf-native-worker','hf-camerad'):(build/(name+suffix)).touch()
            with mock.patch.object(build_native,'os',SimpleNamespace(name=platform,environ=environment or {})), \
                 mock.patch.object(build_native,'find_cmake',return_value=cmake), \
                 mock.patch.object(build_native,'ensure_build_type') as ensure, \
                 mock.patch.object(build_native,'run') as run, \
                 mock.patch('sys.argv',['build_native.py','--build-dir',str(build)]), \
                 mock.patch('builtins.print'):
                self.assertEqual(0,build_native.main())
            ensure.assert_called_once_with(run.call_args_list[0].args[0],build,'Release')
            return run.call_args_list[0].args[0]

    def test_fresh_windows_uses_cmake_installed_default(self):
        command=self.configure_command('nt')
        self.assertNotIn('-G',command)
        self.assertNotIn('-A',command)
        self.assertIn('-DHF_BUILD_WORKER=ON',command)
        self.assertIn('-DHF_BUILD_DAEMON=ON',command)

    def test_explicit_generator_and_existing_cache_are_not_overridden(self):
        for environment,cached in (({'CMAKE_GENERATOR':'Ninja'},False),({},True)):
            with self.subTest(environment=environment,cached=cached):
                command=self.configure_command('nt',environment,cached)
                self.assertNotIn('-G',command)
                self.assertNotIn('-A',command)

    def test_posix_keeps_requested_configuration(self):
        command=self.configure_command('posix')
        self.assertIn('-DCMAKE_BUILD_TYPE=Release',command)
        self.assertNotIn('-G',command)

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

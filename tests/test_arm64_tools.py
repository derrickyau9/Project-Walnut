import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.arm64_build import tool


class Arm64ToolSelectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX executable symlink semantics')
    def test_clangxx_invocation_name_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory) / 'clang'
            real.write_bytes(b'compiler fixture')
            invocation = Path(directory) / 'clang++'
            invocation.symlink_to(real)
            with mock.patch('tools.arm64_build.shutil.which', return_value=str(invocation)):
                self.assertEqual(tool('clang++'), str(invocation))
                self.assertNotEqual(tool('clang++'), str(real))


if __name__ == '__main__':
    unittest.main()

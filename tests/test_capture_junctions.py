"""Real Windows junction boundaries for native and reference photo storage."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from camera.camerad.native_client import NativeClient
from camera.camerad.service import CameraError, CameraService


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows NTFS directory junction regression')
class PhotoJunctionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-photo-junction-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'authority'
        self.outside = self.base / 'outside'
        self.root.mkdir()
        self.outside.mkdir()
        self.photos = self.root / 'photos'
        self.sentinel = self.outside / 'preserve.bin'
        self.sentinel.write_bytes(b'foreign outside bytes')
        powershell = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
        if powershell is None:
            self.skipTest('existing Windows PowerShell required to create a test junction')
        environment = dict(os.environ, HF_TEST_JUNCTION_PATH=str(self.photos),
                           HF_TEST_JUNCTION_TARGET=str(self.outside))
        command = [powershell, '-NoProfile', '-NonInteractive', '-Command',
                   '[void](New-Item -ItemType Junction -Path $env:HF_TEST_JUNCTION_PATH '
                   '-Target $env:HF_TEST_JUNCTION_TARGET -ErrorAction Stop)']
        result = subprocess.run(command, env=environment, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertTrue(self.photos.lstat().st_file_attributes & 0x400)
        # os.rmdir removes the junction entry itself, never its target tree.
        self.addCleanup(lambda: self.photos.rmdir() if self.photos.exists() else None)

    def assert_preserved(self):
        self.assertEqual(list(self.outside.iterdir()), [self.sentinel])
        self.assertEqual(self.sentinel.read_bytes(), b'foreign outside bytes')
        self.assertFalse((self.root / 'journal.sqlite3').exists())

    def test_reference_startup_refuses_junction_and_releases_lock(self):
        with self.assertRaises(CameraError) as caught:
            unexpected = CameraService(self.root)
            unexpected.close()
        self.assertEqual(caught.exception.code, 'storage_error')
        self.assert_preserved()
        self.photos.rmdir()
        self.photos.mkdir()
        restored = CameraService(self.root)
        try:
            self.assertIsNotNone(restored.acquire('restored-reference'))
        finally:
            restored.close()

    def test_native_startup_refuses_junction_and_releases_process_lock(self):
        executable = Path(os.environ.get('HF_NATIVE_DAEMON', ROOT / 'build/native/Release/hf-camerad.exe'))
        if not executable.is_file():
            self.skipTest('build native authority or set HF_NATIVE_DAEMON')
        with self.assertRaises(CameraError) as caught:
            unexpected = NativeClient(self.root, command=[str(executable.resolve())])
            unexpected.abort()
        self.assertEqual(caught.exception.code, 'storage_error')
        self.assert_preserved()
        self.photos.rmdir()
        self.photos.mkdir()
        with NativeClient(self.root, command=[str(executable.resolve())]) as restored:
            self.assertIsNotNone(restored.acquire('restored-native'))


if __name__ == '__main__':
    unittest.main()

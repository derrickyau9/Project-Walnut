"""Guard against labeling host desktop execution as ARM Qt evidence."""
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


class ArmQtEvidenceGuardTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == 'linux' and platform.machine().lower() in {'aarch64', 'arm64'},
                     'this regression checks refusal on a non-ARM build host')
    def test_host_python_cannot_create_arm_ui_evidence(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'must-not-exist'
            result = subprocess.run([sys.executable, '-B', str(root / 'tests/ui/arm64_smoke.py'),
                '--native-daemon', str(root / 'build/native/Release/hf-camerad.exe'),
                '--output-dir', str(output), '--execution-mode', 'qemu-system', '--phase', 'first'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn('requires actual Linux ARM64 Python', result.stderr)
            self.assertFalse(output.exists())
            self.assertNotIn('HF_IMAGE_UI_RESULT', result.stdout)
            self.assertNotIn('_PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()

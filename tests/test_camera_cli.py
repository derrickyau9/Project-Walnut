import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


class BasicCaptureCliTests(unittest.TestCase):
    def run_cli(self, folder, *args):
        return subprocess.run([sys.executable, '-m', 'camera.camerad', '--data-dir', folder, *args],
                              capture_output=True, text=True, timeout=20)

    def test_independent_capture_query_and_same_id_does_not_retake(self):
        with tempfile.TemporaryDirectory() as tmp:
            rid = str(uuid.uuid4())
            command = ('capture', '--request-id', rid, '--exposure-us', '12000')
            result = self.run_cli(tmp, *command)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            record = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(record['status'], 'complete')
            self.assertEqual(record['actual_exposure_us'], 12000)
            path = Path(record['file_path'])
            old = (path.stat().st_mtime_ns, path.read_bytes())
            repeat = self.run_cli(tmp, *command)
            self.assertEqual(repeat.returncode, 0, repeat.stdout)
            self.assertEqual(old, (path.stat().st_mtime_ns, path.read_bytes()))
            queried = self.run_cli(tmp, 'query', rid)
            self.assertEqual(json.loads(queried.stdout)['capture_id'], rid)

    def test_fault_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            fault = self.run_cli(tmp, 'capture', '--fault', 'storage_full')
            self.assertEqual(fault.returncode, 3, fault.stdout)
            self.assertEqual(json.loads(fault.stdout.splitlines()[-1])['status'], 'failed')
            self.assertFalse(list((Path(tmp) / 'photos').glob('*.dng')))
            invalid = self.run_cli(tmp, 'capture', '--exposure-us', '-1')
            self.assertEqual(invalid.returncode, 2, invalid.stdout)


if __name__ == '__main__':
    unittest.main()

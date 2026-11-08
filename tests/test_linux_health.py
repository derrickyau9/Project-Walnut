import os
from pathlib import Path
import tempfile
import unittest

from system.linux.healthcheck import check
from system.linux.serve import validate_paths

ROOT = Path(__file__).resolve().parents[1]
DAEMON = os.environ.get('HF_NATIVE_DAEMON')
if not DAEMON:
    filename = 'hf-camerad.exe' if os.name == 'nt' else 'hf-camerad'
    DAEMON = next((str(path) for path in (ROOT / 'build/native/Release' / filename,
                                         ROOT / 'build/native' / filename) if path.is_file()), None)


class HealthTests(unittest.TestCase):
    def test_existing_directory_is_never_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owner-file'
            path.write_bytes(b'untouched')
            with self.assertRaises(ValueError): check(['never-executed'], directory)
            self.assertEqual(path.read_bytes(), b'untouched')

    @unittest.skipUnless(DAEMON, 'compiled native daemon not available')
    def test_actual_native_capture_and_restart_health(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / 'new-health-capture'
            evidence = check([DAEMON], scratch, timeout=5)
            self.assertTrue(evidence['passed'])
            self.assertTrue(evidence['journal_healthy'])
            self.assertEqual(evidence['capture_status'], 'complete')
            self.assertEqual(len(evidence['artifact_sha256']), 64)
            self.assertEqual(len(list((scratch / 'photos').glob('*.dng'))), 1)

    def test_runtime_cannot_contain_persistent_data(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / 'bundle'
            runtime = bundle / 'runtime'
            (runtime / 'camera/camerad').mkdir(parents=True)
            (runtime / 'camera/camerad/server.py').write_text('')
            daemon = bundle / 'hf-camerad'; daemon.write_text('fixture')
            with self.assertRaises(ValueError): validate_paths(runtime, daemon, runtime / 'data')
            with self.assertRaises(ValueError): validate_paths(runtime, daemon, 'relative-data')
            data = Path(directory) / 'persistent-camera'
            self.assertEqual(validate_paths(runtime, daemon, data)[2], data)

    def test_user_service_has_explicit_native_storage_and_bounded_restart(self):
        text = (ROOT / 'system/linux/hf-sim.service').read_text()
        for required in ('--native-daemon', '--data-dir', 'native-authority', 'KillSignal=SIGINT',
                         'StartLimitBurst=3', 'TimeoutStopSec=15', 'NoNewPrivileges=true', 'python3 -B -m'):
            self.assertIn(required, text)
        self.assertNotIn('User=root', text)


if __name__ == '__main__':
    unittest.main()

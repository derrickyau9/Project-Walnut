import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest

from system.linux.recovery import FORMAT, TARGET, RecoveryError, RecoveryStore, validate_bundle


def bundle(root, name, content=b'host simulator fixture'):
    directory = Path(root) / name
    (directory / 'bin').mkdir(parents=True)
    (directory / 'bin/hf-camerad').write_bytes(content)
    manifest = {'format': FORMAT, 'target': TARGET, 'package_id': name, 'architecture': 'x86_64',
                'files': [{'path': 'bin/hf-camerad', 'size': len(content), 'mode': 0o755,
                           'sha256': hashlib.sha256(content).hexdigest()}]}
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return directory


def health(selection):
    return {'format': 'hf-sim-health-v1', 'passed': True, 'simulated': True, 'authority': 'native',
            'journal_healthy': True, 'capture_status': 'complete', 'attempt_id': selection['attempt_id'],
            'manifest_sha256': selection['manifest_sha256'], 'artifact_sha256': 'a' * 64,
            'native_binary_sha256': hashlib.sha256((Path(selection['path']) / 'bin/hf-camerad').read_bytes()).hexdigest()}


def tree_digest(root):
    entries = {}
    for path in Path(root).rglob('*'):
        entries[path.relative_to(root).as_posix()] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                                     if path.is_file() else 'directory')
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


class BundleTests(unittest.TestCase):
    def test_checksum_architecture_and_exact_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            path = bundle(root, 'valid')
            self.assertEqual(validate_bundle(path, architecture='x86_64')['files'], 1)
            with self.assertRaises(RecoveryError):
                validate_bundle(path, architecture='aarch64')
            (path / 'bin/hf-camerad').write_bytes(b'damaged')
            with self.assertRaises(RecoveryError):
                validate_bundle(path)
            path = bundle(root, 'extra')
            (path / 'surprise').write_text('unmanifested')
            with self.assertRaises(RecoveryError):
                validate_bundle(path)

    def test_traversal_duplicate_permissions_and_manifest_keys(self):
        for mutation in ('traversal', 'duplicate', 'privileged', 'duplicate_json'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as root:
                path = bundle(root, 'candidate')
                manifest_path = path / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                if mutation == 'traversal': manifest['files'][0]['path'] = '../outside'
                elif mutation == 'duplicate': manifest['files'].append(dict(manifest['files'][0]))
                elif mutation == 'privileged': manifest['files'][0]['mode'] = 0o4755
                else:
                    manifest_path.write_text('{"format":"wrong",' + manifest_path.read_text()[1:])
                if mutation != 'duplicate_json': manifest_path.write_text(json.dumps(manifest))
                with self.assertRaises(RecoveryError):
                    validate_bundle(path)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink validation')
    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = bundle(root, 'candidate')
            target = path / 'bin/hf-camerad'
            target.unlink()
            target.symlink_to('/etc/passwd')
            with self.assertRaises(RecoveryError): validate_bundle(path)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.first, self.second = bundle(self.root, 'first'), bundle(self.root, 'second')
        self.directory = self.root / 'recovery-only'

    def tearDown(self):
        self.temp.cleanup()

    def store(self):
        return RecoveryStore(self.directory, architecture='x86_64')

    def test_trial_confirmation_and_stale_health(self):
        with self.store() as store:
            store.bootstrap(self.first); store.stage(self.second, attempts=2)
            first_try = store.choose_boot()
            self.assertEqual((first_try['mode'], first_try['slot']), ('trial', 'B'))
            second_try = store.choose_boot()
            before = store.state()
            with self.assertRaises(RecoveryError): store.confirm(first_try['attempt_id'], health(first_try))
            self.assertEqual(store.state(), before)
            bad = health(second_try); bad['journal_healthy'] = False
            with self.assertRaises(RecoveryError): store.confirm(second_try['attempt_id'], bad)
            store.confirm(second_try['attempt_id'], health(second_try))
            self.assertEqual(store.state()['active'], 'B')
            self.assertIsNone(store.state()['trial'])
        with self.store() as store:
            self.assertEqual(store.choose_boot()['package_id'], 'second')

    def test_abrupt_process_exit_consumes_attempt_before_return(self):
        with self.store() as store:
            store.bootstrap(self.first); store.stage(self.second, attempts=1)
        script = ('import os,sys,json\nfrom system.linux.recovery import RecoveryStore\n'
                  's=RecoveryStore(sys.argv[1],architecture="x86_64")\n'
                  'print(json.dumps(s.choose_boot()),flush=True)\nos._exit(0)\n')
        result = subprocess.run([sys.executable, '-c', script, str(self.directory)], check=True,
                                capture_output=True, text=True, timeout=10)
        trial = json.loads(result.stdout)
        self.assertEqual(trial['mode'], 'trial')
        with self.store() as store:
            fallback = store.choose_boot()
            self.assertEqual((fallback['mode'], fallback['package_id']), ('confirmed', 'first'))
            self.assertIn('budget exhausted', store.state()['last_error'])
            with self.assertRaises(RecoveryError): store.confirm(trial['attempt_id'], health(trial))

    def test_changed_bundle_falls_back_without_touching_data(self):
        photos, calibration = self.root / 'camera/photos', self.root / 'calibration'
        photos.mkdir(parents=True); calibration.mkdir()
        sentinel = photos / 'owner-photo.bin'; sentinel.write_bytes(b'preserve photo')
        calib = calibration / 'owner-calibration.bin'; calib.write_bytes(b'preserve calibration')
        with self.store() as store:
            store.bootstrap(self.first); store.stage(self.second)
            (self.second / 'bin/hf-camerad').write_bytes(b'corrupt')
            self.assertEqual(store.choose_boot()['package_id'], 'first')
            self.assertIsNone(store.state()['trial'])
        self.assertEqual(sentinel.read_bytes(), b'preserve photo')
        self.assertEqual(calib.read_bytes(), b'preserve calibration')
        with self.assertRaises(RecoveryError): RecoveryStore(photos.parent, architecture='x86_64')

    def test_empty_state_recovery_and_invalid_attempt_budget(self):
        with self.store() as store:
            self.assertEqual(store.choose_boot()['mode'], 'recovery')
            store.bootstrap(self.first)
            for attempts in (0, 6, True):
                with self.assertRaises(RecoveryError): store.stage(self.second, attempts=attempts)
            store.stage(self.second)
            with self.assertRaises(RecoveryError): store.stage(self.first)
            with self.assertRaises(RecoveryError): store.bootstrap(self.first)

    def test_bundle_ancestry_refused_before_directory_or_database_creation(self):
        before = tree_digest(self.first)
        original_reference = validate_bundle(self.first)
        for location in (self.first, self.first / 'runtime/new-recovery/state'):
            with self.subTest(location=location):
                with self.assertRaises(RecoveryError):
                    with RecoveryStore(location, architecture='x86_64'):
                        pass
                self.assertEqual(tree_digest(self.first), before)
                self.assertEqual(validate_bundle(self.first), original_reference)
                self.assertFalse((location / 'recovery.sqlite3').exists())
        self.assertFalse((self.first / 'runtime').exists())

    def test_camera_ancestry_refused_without_changing_journal_or_photo_tree(self):
        camera = self.root / 'owner-camera'
        photos = camera / 'photos'
        photos.mkdir(parents=True)
        (photos / 'owner-photo.bin').write_bytes(b'preserve owner photograph')
        with closing(sqlite3.connect(camera / 'journal.sqlite3')) as connection:
            with connection:
                connection.execute('CREATE TABLE owner_evidence(value TEXT)')
                connection.execute("INSERT INTO owner_evidence VALUES('preserve journal')")
        before = tree_digest(camera)
        for location in (camera, photos, photos / 'new-recovery/state', camera / 'new/nested-state'):
            with self.subTest(location=location):
                with self.assertRaises(RecoveryError):
                    with RecoveryStore(location, architecture='x86_64'):
                        pass
                self.assertEqual(tree_digest(camera), before)
                self.assertFalse((location / 'recovery.sqlite3').exists())
        self.assertFalse((photos / 'new-recovery').exists())
        self.assertFalse((camera / 'new').exists())

    def test_missing_persisted_state_is_not_reset(self):
        with self.store() as store:
            store.bootstrap(self.first); store.stage(self.second, attempts=1); store.choose_boot()
        database = self.directory / 'recovery.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                connection.execute('DELETE FROM state')
        original = database.read_bytes()
        with self.assertRaises(RecoveryError): self.store()
        self.assertEqual(database.read_bytes(), original)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink validation')
    def test_symlinked_recovery_database_is_refused_without_touching_target(self):
        self.directory.mkdir()
        owner = self.root / 'owner-data'; owner.write_bytes(b'preserve owner data')
        (self.directory / 'recovery.sqlite3').symlink_to(owner)
        with self.assertRaises(RecoveryError): self.store()
        self.assertEqual(owner.read_bytes(), b'preserve owner data')


if __name__ == '__main__':
    unittest.main()

"""Durable metadata/file guards; actual ARM boots have separate model evidence.

These unit fixtures use tiny regular payloads and mock only filesystem payload
inspection. Hashes, SQLite transactions, process locks and crash consumption are
real. They cannot establish guest health; the real QEMU demo does that.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import struct
import unittest
from unittest import mock
import uuid

from tools import update_model as model


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() != 0,
                     'requires non-root Linux ext4 tools')
class UpdatePayloadTests(unittest.TestCase):
    def test_bound_source_profile_pins_real_copied_sqlite_and_binding_artifact(self):
        import sqlite3
        import shutil
        from camera.calibration import create_binding, create_synthetic_package
        from tools.image_build import DATA_UUID, write_tar
        from tools.image_fs import create_ext4
        for tool in ('mke2fs', 'e2fsck', 'debugfs'):
            if shutil.which(tool) is None:
                self.skipTest('existing ext4 tools required')
        with tempfile.TemporaryDirectory(prefix='update-source-test-', dir=model.ROOT / 'build') as temporary:
            directory = Path(temporary)
            binding = create_binding(create_synthetic_package('HF-Sim', 'source-profile-fixture', '1', width=128, height=96))
            rid = str(uuid.uuid4())
            settings = {'sensor_mode': 'sim-small', 'requested_exposure_us': 11700,
                        'requested_analogue_gain': 1.0, 'output_format': 'dng', 'simulation_scene': 'scene'}
            record = {'request_id': rid, 'capture_id': rid, 'frame_id': rid, 'capture_record_version': 2,
                      'calibration_binding': binding, 'actual_exposure_us': 11700,
                      'hardware_calibrated': False, 'pixel_corrections_applied': False}
            journal = directory / 'journal.sqlite3'
            database = sqlite3.connect(journal)
            try:
                database.executescript('CREATE TABLE captures(request_id TEXT,record_json TEXT,status TEXT); CREATE TABLE meta(key TEXT,value TEXT);')
                database.execute('INSERT INTO captures VALUES(?,?,?)', (rid, model._canonical(record).decode(), 'complete'))
                database.executemany('INSERT INTO meta VALUES(?,?)', [
                    ('settings_format', 'hf-device-settings-v1'),
                    ('settings', model._canonical({'format': 'hf-device-settings-v1', 'settings': settings}).decode()),
                    ('calibration_binding_format', 'hf-active-calibration-v1'),
                    ('calibration_binding', model._canonical({'format': 'hf-active-calibration-v1', 'binding': binding}).decode())])
                database.commit()
            finally:
                database.close()
            artifact = directory / 'binding.json'
            artifact.write_bytes(model._canonical(binding))
            tar = directory / 'data.tar'
            write_tar(tar, [
                {'path': 'ui-r7/offline/basic/journal.sqlite3', 'type': 'file', 'source': journal, 'mode': 0o600},
                {'path': 'ui-r7/offline/binding.json', 'type': 'file', 'source': artifact, 'mode': 0o600}])
            create_ext4(tar, directory / 'data.ext4', size_mib=32, label='HF_SOURCE_TEST', uuid=DATA_UUID)
            basic = {'request_id': rid, 'dng_sha256': 'c' * 64, 'capture_record_version': 2,
                     'settings_persistent': True, 'calibration_id': binding['binding_id'],
                     'package_checksum': binding['package_checksum'], 'actual_exposure_us': 11700}
            last = {'guest_report': {'request_id': rid, 'dng_sha256': 'c' * 64,
                'native_authority': {'sha256': 'b' * 64},
                'offline_camera': {'format': 'hf-offline-camera-smoke-v2', 'passed': True, 'basic': basic}}}
            before = model._hash(directory / 'data.ext4')
            profile, actual_id, actual_sha = model._source_profile(directory, last, 'bound-v2')
            self.assertEqual((actual_id, actual_sha), (rid, 'c' * 64))
            self.assertEqual(profile['expected_settings'], settings)
            self.assertEqual(profile['expected_binding'], binding)
            self.assertEqual(profile['data_abi'], 2)
            with self.assertRaisesRegex(model.UpdateError, 'legacy'):
                model._source_profile(directory, last, 'legacy-v1', 'ui-r7/offline/basic')
            self.assertEqual(model._hash(directory / 'data.ext4'), before)

    def test_guest_release_marker_readability_is_validated_on_a_real_regular_image(self):
        import hashlib
        from tools.image_build import ROOT_UUID, write_tar
        from tools.image_fs import create_ext4, _run
        import shutil
        for tool in ('mke2fs', 'e2fsck', 'debugfs'):
            if shutil.which(tool) is None:
                self.skipTest('existing ext4 tools required')
        with tempfile.TemporaryDirectory(prefix='update-payload-test-', dir=model.ROOT / 'build') as temporary:
            directory = Path(temporary)
            marker = directory / 'release.json'
            marker.write_bytes(model._canonical({'format': 'hf-update-release-v1', 'target': model.TARGET,
                'version': 1, 'health_mode': 'healthy', 'data_abi': model.DATA_ABI, 'native_abi': model.APP_ABI}))
            native = directory / 'native'
            # Minimal AArch64 ELF metadata fixture; it is never executed.
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', header, 18, 183)
            native.write_bytes(header)
            expected_native = hashlib.sha256(header).hexdigest()
            tar = directory / 'root.tar'
            write_tar(tar, [
                {'path': 'etc/hf-update-release.json', 'type': 'file', 'source': marker, 'mode': 0o644},
                {'path': 'opt/hasslefree/bin/hf-camerad', 'type': 'file', 'source': native, 'mode': 0o755}])
            create_ext4(tar, directory / 'rootfs.ext4', size_mib=32, label='HF_UPDATE_TEST', uuid=ROOT_UUID)
            manifest = {'version': 1, 'health_mode': 'healthy', 'native_sha256': expected_native}
            policy = {'native_sha256': expected_native}
            model._payload(directory, manifest, policy)
            changed = _run([shutil.which('debugfs'), '-w', '-R',
                'set_inode_field /etc/hf-update-release.json mode 0100600', str(directory / 'rootfs.ext4')])
            self.assertEqual(changed['returncode'], 0)
            with self.assertRaisesRegex(model.UpdateError, 'readable'):
                model._payload(directory, manifest, policy)


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() != 0,
                     'requires non-root Linux regular-file/flock model')
class UpdateModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='update-model-test-', dir=model.ROOT / 'build')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'bundles').mkdir()
        (self.root / 'attempts').mkdir()
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/bootstrap.cpio.gz').write_bytes(b'test-only-bootstrap')
        (self.root / 'data.ext4').write_bytes(b'test-only-shared-data')
        self.policy = {'format': 'hf-update-model-policy-v1', 'store_id': str(uuid.uuid4()),
            'target': model.TARGET, 'data_abi': 1, 'native_abi': model.APP_ABI,
            'source_build_sha256': 'a' * 64, 'native_sha256': 'b' * 64,
            'bootstrap_sha256': model._hash(self.root / 'runtime/bootstrap.cpio.gz'),
            'kernel_sha256': __import__('hashlib').sha256(b'test-only-kernel').hexdigest(),
            'capture_id': str(uuid.uuid4()), 'dng_sha256': 'c' * 64}
        model._write(self.root / 'policy.json', self.policy)
        self.protected = {'configuration': 'd' * 64, 'calibration': 'e' * 64,
                          'original_dng': 'c' * 64, 'journal_capture_rows': 'f' * 64}
        database = model._database(self.root / 'control.sqlite3')
        try:
            model._schema(database)
            database.execute('INSERT INTO state VALUES(1,?,NULL,0,NULL,?,?)',
                ('A', model._canonical(self.protected).decode(), model._hash(self.root / 'data.ext4')))
            database.commit()
        finally:
            database.close()
        patch = mock.patch('tools.update_model._payload')
        patch.start()
        self.addCleanup(patch.stop)
        initial = self.bundle(1)
        with model.UpdateStore(self.root, initializing=True) as store:
            store.db.execute('INSERT INTO slots VALUES(?,?,?,?)',
                ('A', initial.relative_to(self.root).as_posix(), model._hash(initial / 'manifest.json'), 1))
            store.db.commit()

    def bundle(self, version, health='healthy', **changes):
        directory = self.root / 'bundles' / str(uuid.uuid4())
        directory.mkdir()
        (directory / 'rootfs.ext4').write_bytes(('test-only-root-version-' + str(version)).encode())
        (directory / 'kernel.img').write_bytes(b'test-only-kernel')
        manifest = {'format': model.FORMAT, 'target': model.TARGET, 'architecture': 'aarch64',
            'data_abi': self.policy.get('data_abi', 1), 'native_abi': model.APP_ABI, 'version': version, 'health_mode': health,
            'native_sha256': self.policy['native_sha256'], 'bootstrap_sha256': self.policy['bootstrap_sha256'],
            'source_build_sha256': self.policy['source_build_sha256'],
            'files': {name: {'size': (directory / name).stat().st_size, 'sha256': model._hash(directory / name)}
                      for name in ('rootfs.ext4', 'kernel.img')}}
        manifest.update(changes)
        model._write(directory / 'manifest.json', manifest)
        return directory

    def receipt(self, store, selected, *, healthy=True):
        _, bundle, manifest = store._slot(selected['slot'])
        root_sha = model._hash(bundle / 'rootfs.ext4')
        value = {'format': 'hf-arm64-update-boot-v1', 'passed': True, 'healthy': healthy,
            'attempt_id': selected['attempt_id'], 'slot': selected['slot'], 'version': selected['version'],
            'health_mode': selected['health_mode'], 'rootfs_unchanged': True,
            'rootfs_sha256_before': root_sha, 'rootfs_sha256_after': root_sha,
            'kernel_sha256': self.policy['kernel_sha256'], 'bootstrap_sha256': self.policy['bootstrap_sha256'],
            'data_sha256_before': selected['data_sha256_before'], 'data_sha256_after': model._hash(self.root / 'data.ext4'),
            'protected_sha256': dict(self.protected)}
        if self.policy.get('health_profile', {}).get('name') == 'bound-v2':
            value.update(data_profile='bound-v2', data_abi=2,
                         capture_root=self.policy['health_profile']['capture_root'])
        directory = self.root / 'attempts' / selected['attempt_id']
        directory.mkdir()
        model._write(directory / 'boot-evidence.json', value)
        return value

    def bound_policy(self):
        import hashlib
        from camera.calibration import create_binding, create_synthetic_package
        binding = create_binding(create_synthetic_package('HF-Sim', 'update-unit-fixture', '1', width=128, height=96))
        settings = {'sensor_mode': 'sim-small', 'requested_exposure_us': 11700,
                    'requested_analogue_gain': 1.0, 'output_format': 'dng', 'simulation_scene': 'scene'}
        calibration = {key: binding[key] for key in ('sensor_id', 'binding_id', 'package_checksum',
                       'hardware_calibrated', 'pixel_corrections_applied')}
        calibration['calibration_id'] = binding['binding_id']
        profile = {'format': 'hf-update-health-profile-v1', 'name': 'bound-v2', 'data_abi': 2,
            'capture_root': 'ui-r7/offline/basic', 'expected_settings': settings, 'expected_binding': binding,
            'expected_calibration': calibration, 'expected_capture_id': self.policy['capture_id'],
            'expected_dng_sha': self.policy['dng_sha256'], 'expected_native_sha': self.policy['native_sha256']}
        self.policy.update(health_profile=profile, data_abi=2)
        (self.root / 'policy.json').write_bytes(model._canonical(self.policy))
        self.protected.update(device_settings=hashlib.sha256(model._canonical(settings) + b'\n').hexdigest(),
                              calibration_binding=hashlib.sha256(model._canonical(binding) + b'\n').hexdigest())
        database = model._database(self.root / 'control.sqlite3')
        try:
            relative = database.execute("SELECT bundle FROM slots WHERE slot='A'").fetchone()[0]
            path = self.root / relative / 'manifest.json'
            manifest = model._read(path)
            manifest['data_abi'] = 2
            path.write_bytes(model._canonical(manifest))
            database.execute("UPDATE slots SET manifest_sha=? WHERE slot='A'", (model._hash(path),))
            database.execute('UPDATE state SET protected=? WHERE id=1', (model._canonical(self.protected).decode(),))
            database.commit()
        finally:
            database.close()

    def test_bound_receipt_requires_actual_pinned_device_settings_and_binding(self):
        self.bound_policy()
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            selected = store.select_boot()
            receipt = self.receipt(store, selected)
            for key in ('device_settings', 'calibration_binding'):
                changed = dict(receipt, protected_sha256=dict(receipt['protected_sha256'], **{key: '0' * 64}))
                with self.subTest(key=key), self.assertRaises(model.UpdateError):
                    store._record_result(selected, changed)
            with self.assertRaises(model.UpdateError):
                store._record_result(selected, dict(receipt, data_abi=1))
            self.assertEqual(store.status()['confirmed'], 'A')
            store._record_result(selected, receipt)
            self.assertEqual(store.confirm(selected['attempt_id'])['confirmed_slot'], 'B')

    def test_different_native_binary_cannot_claim_compatibility_merely_by_declaring_abi2(self):
        self.bound_policy()
        with model.UpdateStore(self.root) as store:
            before = model._hash(self.root / 'data.ext4')
            candidate = self.bundle(2, native_sha256='0' * 64)
            with self.assertRaises(model.UpdateError):
                store.stage(candidate)
            self.assertIsNone(store.status()['trial'])
            self.assertFalse(store.status()['attempts'])
            self.assertEqual(model._hash(self.root / 'data.ext4'), before)

    def test_trial_attempts_survive_real_process_exit_and_exhaust_to_confirmed_slot(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2), attempts=2)
        # The subprocess mocks payload inspection only, then exits without
        # closing SQLite after its committed selection, modeling lost control.
        code = ('import os,json,sys; from tools import update_model as m; m._payload=lambda *a:None; '
                's=m.UpdateStore(sys.argv[1]); r=s.select_boot(); print(json.dumps(r),flush=True); os._exit(77)')
        first = subprocess.run([sys.executable, '-c', code, str(self.root)], cwd=model.ROOT,
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 77, first.stderr)
        first_id = json.loads(first.stdout)['attempt_id']
        with model.UpdateStore(self.root) as store:
            second = store.select_boot()
            self.assertEqual((second['slot'], second['attempts_remaining']), ('B', 0))
            self.assertNotEqual(first_id, second['attempt_id'])
        with model.UpdateStore(self.root) as store:
            fallback = store.select_boot()
            self.assertEqual((fallback['slot'], fallback['is_trial']), ('A', False))
            self.assertEqual(store.status()['confirmed'], 'A')
            self.assertEqual(store.status()['attempts'][0]['status'], 'interrupted')

    def test_healthy_boot_needs_explicit_confirmation_and_unhealthy_boot_cannot_confirm(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            selected = store.select_boot()
            store._record_result(selected, self.receipt(store, selected))
            self.assertEqual(store.status()['confirmed'], 'A')
        with model.UpdateStore(self.root) as store:
            self.assertEqual(store.confirm(selected['attempt_id'])['confirmed_slot'], 'B')
            store.stage(self.bundle(3, 'fail'), attempts=1)
            failed = store.select_boot()
            store._record_result(failed, self.receipt(store, failed, healthy=False))
            with self.assertRaises(model.UpdateError):
                store.confirm(failed['attempt_id'])
            fallback = store.select_boot()
            self.assertEqual((fallback['slot'], fallback['is_trial']), ('B', False))
            with self.assertRaises(model.UpdateError):
                store.confirm(selected['attempt_id'])

    def test_boot_receipt_requires_matching_attempt_image_and_protected_data(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            selected = store.select_boot()
            receipt = self.receipt(store, selected)
            changes = ({'attempt_id': str(uuid.uuid4())}, {'slot': 'A'}, {'version': 99},
                       {'rootfs_sha256_after': '0' * 64}, {'data_sha256_after': '0' * 64},
                       {'protected_sha256': dict(self.protected, calibration='0' * 64)})
            for change in changes:
                with self.subTest(change=change), self.assertRaises(model.UpdateError):
                    store._record_result(selected, dict(receipt, **change))
            self.assertEqual(store.status()['confirmed'], 'A')
            self.assertEqual(store.status()['attempts'][-1]['status'], 'selected')

    def test_stage_rejects_incompatible_or_changed_candidate_without_touching_confirmed_slot(self):
        with model.UpdateStore(self.root) as store:
            before = store.status()['slots']
            candidates = [self.bundle(2, data_abi=2), self.bundle(2, target='physical-board'), self.bundle(1)]
            corrupt = self.bundle(2)
            (corrupt / 'rootfs.ext4').write_bytes(b'changed stored bytes')
            candidates.append(corrupt)
            for candidate in candidates:
                with self.subTest(candidate=candidate), self.assertRaises(model.UpdateError):
                    store.stage(candidate)
                self.assertEqual(store.status()['slots'], before)
                self.assertIsNone(store.status()['trial'])

    def test_changed_actual_bootstrap_is_refused_before_consuming_attempt(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            (self.root / 'runtime/bootstrap.cpio.gz').write_bytes(b'changed runtime')
            with self.assertRaises(model.UpdateError):
                store.select_boot()
            self.assertEqual(store.status()['remaining'], 2)
            self.assertFalse(store.status()['attempts'])

    def test_evidence_directory_sync_failure_does_not_publish_healthy_state(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            selected = store.select_boot()
            receipt = self.receipt(store, selected)
            with mock.patch('tools.update_model._sync_directory', side_effect=OSError('injected evidence sync failure')):
                with self.assertRaises(OSError):
                    store._record_result(selected, receipt)
            self.assertEqual(store.status()['confirmed'], 'A')
            self.assertEqual(store.status()['attempts'][-1]['status'], 'selected')
            with self.assertRaises(model.UpdateError):
                store.confirm(selected['attempt_id'])

    def test_vm_lifecycle_lock_is_exclusive_and_released(self):
        with model.UpdateStore(self.root):
            with self.assertRaisesRegex(model.UpdateError, 'another coordinator'):
                model.UpdateStore(self.root)
        with model.UpdateStore(self.root) as store:
            self.assertEqual(store.status()['confirmed'], 'A')

    def test_confirmation_rejects_changed_receipt_or_shared_data(self):
        with model.UpdateStore(self.root) as store:
            store.stage(self.bundle(2))
            selected = store.select_boot()
            receipt = self.receipt(store, selected)
            store._record_result(selected, receipt)
            path = self.root / 'attempts' / selected['attempt_id'] / 'boot-evidence.json'
            before = path.read_bytes()
            path.write_text('{}')
            with self.assertRaises(model.UpdateError):
                store.confirm(selected['attempt_id'])
            path.write_bytes(before)
            (self.root / 'data.ext4').write_bytes(b'outside mutation')
            with self.assertRaises(model.UpdateError):
                store.confirm(selected['attempt_id'])
            self.assertEqual(store.status()['confirmed'], 'A')

    def test_corrupt_state_and_json_fail_closed(self):
        with model.UpdateStore(self.root) as store:
            store.db.execute('UPDATE state SET remaining=-1 WHERE id=1')
            store.db.commit()
        with self.assertRaises(model.UpdateError):
            model.UpdateStore(self.root)
        for raw in (b'{"version":1,"version":2}', b'{"value":1e999}', b'{"value":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(model.UpdateError):
                model._json(raw)


if __name__ == '__main__':
    unittest.main()

"""Bootchain transactions use only private temporary filesystem trees."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'system/adb/recovery'
sys.path.insert(0, str(SOURCE))
try:
    spec = importlib.util.spec_from_file_location('install_bootchain', SOURCE / 'install_bootchain.py')
    boot = importlib.util.module_from_spec(spec); spec.loader.exec_module(boot)
finally:
    sys.path.pop(0)


class BootchainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.root = self.base / 'root'; self.efi = self.base / 'efi'
        self.bundle = self.base / 'bundle'; self.work = self.base / 'persistent-work'
        for path in (self.root, self.efi, self.bundle): path.mkdir()
        for name in boot.ROOT_PINS: self.put(self.root, name, ('root ' + name).encode())
        for name in boot.EFI_PINS: self.put(self.efi, name, ('efi ' + name).encode())
        self.config = b'[all]\r\narm_64bit=1\r\nkernel=u-boot.bin\r\n[pi4]\r\narmstub=armstub8-gic.bin\r\n'
        self.put(self.efi, 'config.txt', self.config)
        self.put(self.root, 'boot/nextboot.conf', b'nextboot_enable="NO"\n')
        self.put(self.efi, boot.failover.MARKER, b'\0')
        self.put(self.bundle, boot.UBOOT, b'candidate ARM64 U-Boot' * 8)
        self.identity = {'format': 'DaOS-recovery-identity-v1', 'disk': '/dev/mmcsd0',
                         'disk_uuid': '01234567-1234-1234-1234-0123456789ab',
                         'disk_bytes': 1 << 30, 'sector_size': 512, 'partitions': {}}
        for role, index in (('efi', 1), ('root', 2), ('state', 3)):
            self.identity['partitions'][role] = {'index': index, 'uuid': '01234567-1234-1234-1234-0123456789ab',
                                                 'start_lba': index * 4096, 'sectors': 4096}
        self.expected = boot.create_manifest(self.bundle, self.identity,
            {name: boot.early.sha(self.root / name) for name in boot.ROOT_PINS},
            {name: boot.early.sha(self.efi / name) for name in boot.EFI_PINS})
        self.original = self.snapshot()

    def tearDown(self): self.tmp.cleanup()
    def put(self, base, name, data):
        path = base / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    def snapshot(self):
        return {(area, p.relative_to(base).as_posix()): p.read_bytes()
                for area, base in (('root', self.root), ('efi', self.efi))
                for p in base.rglob('*') if p.is_file()}
    def apply(self, **kwargs):
        return boot.apply(self.root, self.efi, self.bundle, self.expected, self.work, **kwargs)
    def rollback(self):
        return boot.rollback(self.root, self.efi, self.bundle, self.expected, self.work)

    def test_plan_is_readonly_and_only_config_and_new_filename_are_writable(self):
        result = boot.make_plan(self.root, self.efi, self.bundle, self.expected)
        self.assertEqual(result['publication_order'], [boot.UBOOT, 'config.txt'])
        self.assertEqual(self.original, self.snapshot()); self.assertFalse(self.work.exists())
        self.assertFalse(result['root_written']); self.assertFalse(result['raw_disk_writes'])

    def test_apply_publishes_verified_image_before_config_and_preserves_other_bytes(self):
        writes = []
        def writer(path, data, mode):
            # Persistent receipt and exact original are durable before first FAT write.
            self.assertEqual((self.work / 'backup/config.txt').read_bytes(), self.config)
            self.assertTrue((self.work / 'deployment.json').is_file())
            if path.name == 'config.txt':
                self.assertEqual(boot.early.sha(self.efi / boot.UBOOT), boot.early.sha(self.bundle / boot.UBOOT))
            writes.append(path.name); boot.failover.fat_atomic(path, data, mode)
        result = self.apply(writer=writer)
        self.assertEqual(writes, [boot.UBOOT, 'config.txt'])
        self.assertEqual(result['status'], 'installed-reboot-required')
        self.assertEqual((self.efi / 'config.txt').read_bytes(), self.config.replace(b'u-boot.bin', boot.UBOOT.encode()))
        actual = self.snapshot(); actual.pop(('efi', boot.UBOOT)); actual[('efi', 'config.txt')] = self.config
        self.assertEqual(actual, self.original)

    def test_rollback_restores_config_before_removing_only_owned_image(self):
        self.apply(); original_atomic = boot.failover.fat_atomic
        def restore(path, data, *args):
            if path.name == 'config.txt': self.assertTrue((self.efi / boot.UBOOT).exists())
            return original_atomic(path, data, *args)
        with patch.object(boot.failover, 'fat_atomic', side_effect=restore): self.rollback()
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(self.rollback()['status'], 'rolled-back')

    def test_failure_after_config_rename_restores_original_bootchain(self):
        def writer(path, data, mode):
            boot.failover.fat_atomic(path, data, mode)
            if path.name == 'config.txt': raise OSError('fault after selector rename')
        with self.assertRaises(OSError): self.apply(writer=writer)
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(json.loads((self.work / 'deployment.json').read_text())['status'], 'failed-rolled-back')

    def test_interruption_after_image_is_recoverable_from_persisted_receipt(self):
        def writer(path, data, mode):
            boot.failover.fat_atomic(path, data, mode)
            raise OSError('power loss window after image staging')
        with patch.object(boot, 'rollback', side_effect=RuntimeError('process unavailable')):
            with self.assertRaises(OSError): self.apply(writer=writer)
        self.assertEqual((self.efi / 'config.txt').read_bytes(), self.config)
        self.assertTrue((self.efi / boot.UBOOT).exists())
        self.rollback(); self.assertEqual(self.snapshot(), self.original)

    def test_interruption_after_config_before_final_receipt_is_recoverable(self):
        def writer(path, data, mode):
            boot.failover.fat_atomic(path, data, mode)
            if path.name == 'config.txt': raise OSError('power loss before final receipt')
        with patch.object(boot, 'rollback', side_effect=RuntimeError('process unavailable')):
            with self.assertRaises(OSError): self.apply(writer=writer)
        self.assertEqual((self.efi / 'config.txt').read_bytes(), boot.selector(self.config))
        self.rollback(); self.assertEqual(self.snapshot(), self.original)

    def test_changed_dependency_before_config_publication_never_activates_image(self):
        def writer(path, data, mode):
            boot.failover.fat_atomic(path, data, mode)
            if path.name == boot.UBOOT: (self.efi / 'boot.scr').write_bytes(b'external selector')
        with self.assertRaisesRegex(ValueError, 'dependency changed'): self.apply(writer=writer)
        self.assertEqual((self.efi / 'config.txt').read_bytes(), self.config)
        self.assertEqual(json.loads((self.work / 'deployment.json').read_text())['status'], 'failed-rollback-refused')

    def test_external_destination_or_backup_change_refuses_rollback_before_config_write(self):
        self.apply(); activated = (self.efi / 'config.txt').read_bytes()
        candidates = (self.efi / boot.UBOOT, self.work / 'backup/config.txt', self.efi / 'boot.scr')
        for path in candidates:
            original = path.read_bytes(); path.write_bytes(b'changed')
            try:
                with self.subTest(path=path), self.assertRaises(ValueError): self.rollback()
                self.assertEqual((self.efi / 'config.txt').read_bytes(), activated)
            finally: path.write_bytes(original)

    def test_externally_changed_config_refuses_rollback_and_keeps_candidate(self):
        self.apply(); (self.efi / 'config.txt').write_bytes(b'external firmware config')
        with self.assertRaisesRegex(ValueError, 'externally changed firmware'): self.rollback()
        self.assertTrue((self.efi / boot.UBOOT).exists())

    def test_all_primary_pins_and_marker_trial_policy_are_preflighted_before_work(self):
        for base, names in ((self.root, boot.ROOT_PINS), (self.efi, boot.EFI_PINS)):
            for name in names:
                path = base / name; old = path.read_bytes(); path.write_bytes(b'changed')
                try:
                    with self.subTest(name=name), self.assertRaises(ValueError): self.apply()
                    self.assertFalse(self.work.exists())
                finally: path.write_bytes(old)
        for base, name, data in ((self.efi, boot.failover.MARKER, b'\1'),
                                 (self.root, 'boot/nextboot.conf', b'nextboot_enable="YES"\n'),
                                 (self.root, boot.failover.POLICY, b'enabled')):
            path = base / name; old = path.read_bytes() if path.exists() else None
            self.put(base, name, data)
            try:
                with self.subTest(name=name), self.assertRaises(ValueError): self.apply()
                self.assertFalse(self.work.exists())
            finally:
                if old is None: path.unlink()
                else: path.write_bytes(old)

    def test_existing_candidate_or_reused_work_never_overwritten(self):
        self.put(self.efi, boot.UBOOT, b'external existing file')
        with self.assertRaisesRegex(ValueError, 'already exists'): self.apply()
        self.assertFalse(self.work.exists()); (self.efi / boot.UBOOT).unlink()
        self.work.mkdir()
        with self.assertRaisesRegex(ValueError, 'Fresh persistent'): self.apply()
        self.assertEqual(self.snapshot(), self.original)

    def test_manifest_candidate_or_scope_tamper_rejected(self):
        source = self.bundle / boot.UBOOT; original = source.read_bytes(); source.write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.apply()
        source.write_bytes(original)
        manifest = self.bundle / boot.MANIFEST
        value = json.loads(manifest.read_text()); value['pins']['efi']['ubootefi.var'] = 'a' * 64
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'pin set'): boot.load_bundle(self.bundle, boot.early.sha(manifest))
        self.assertFalse(self.work.exists())

    def test_selector_rejects_multiple_overrides_or_nonexact_kernel(self):
        for data in (b'[all]\nkernel=u-boot.bin\n[pi4]\nkernel=other.bin\n',
                     b'kernel=other.bin\n', b'[all]\narm_64bit=1\n'):
            with self.subTest(data=data), self.assertRaises(ValueError): boot.selector(data)

    def test_root_and_persistent_work_require_bound_aliases_and_ro_root_rw_state(self):
        labels = 'gpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'
        mounts = '/dev/gpt/DaOS-root / ufs ro 1 1\n/dev/gpt/DaOS-state /var ufs rw 2 2\n'
        work = '/var/DaOS/updates/boot-test'
        # Path.absolute on Windows is deliberately unrelated to native FreeBSD.
        with patch.object(boot.early, 'unredirected', side_effect=lambda p: p):
            boot.require_normal_root(self.identity, labels, mounts, work)
            cases = (mounts.replace('ufs ro', 'ufs rw'),
                     mounts.replace('/var ufs rw', '/var ufs ro'),
                     mounts.replace('/dev/gpt/DaOS-root', '/dev/md0'),
                     mounts + 'tmpfs /var/DaOS tmpfs rw 0 0\n')
            for table in cases:
                with self.subTest(table=table), self.assertRaises(ValueError):
                    boot.require_normal_root(self.identity, labels, table, work)

    def test_rollback_accepts_sticky_rescue_marker_without_modifying_it(self):
        self.apply(); (self.efi / boot.failover.MARKER).write_bytes(b'\1')
        self.rollback()
        self.assertEqual((self.efi / boot.failover.MARKER).read_bytes(), b'\1')

    def test_usb_recovery_prefix_on_bound_state_is_accepted(self):
        labels = 'gpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'
        mounts = '/dev/gpt/DaOS-root / ufs ro 1 1\n/dev/gpt/DaOS-state /var ufs rw 2 2\n'
        with patch.object(boot.early, 'unredirected', side_effect=lambda p: p):
            boot.require_normal_root(self.identity, labels, mounts, '/var/DaOS/usb-recovery/boot-test')

    def test_updates_nullfs_on_photos_is_rejected(self):
        labels = 'gpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'
        mounts = ('/dev/gpt/DaOS-root / ufs ro 1 1\n/dev/gpt/DaOS-state /var ufs rw 2 2\n'
                  '/photos/updates /var/DaOS/updates nullfs rw 0 0\n')
        with patch.object(boot.early, 'unredirected', side_effect=lambda p: p):
            with self.assertRaisesRegex(ValueError, 'bound writable state'):
                boot.require_normal_root(self.identity, labels, mounts, '/var/DaOS/updates/boot-test')

    def test_usb_recovery_prefix_on_foreign_mount_is_rejected(self):
        labels = 'gpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'
        mounts = ('/dev/gpt/DaOS-root / ufs ro 1 1\n/dev/gpt/DaOS-state /var ufs rw 2 2\n'
                  '/dev/da1p3 /var/DaOS/usb-recovery ufs rw 0 0\n')
        with patch.object(boot.early, 'unredirected', side_effect=lambda p: p):
            with self.assertRaisesRegex(ValueError, 'bound writable state'):
                boot.require_normal_root(self.identity, labels, mounts, '/var/DaOS/usb-recovery/boot-test')


if __name__ == '__main__': unittest.main()

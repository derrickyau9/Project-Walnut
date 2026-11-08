"""Host tests for script-free, contained ARM64 rootfs construction."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from tools import rootfs_build as build


class RootfsBuildTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix' and shutil.which('gpgv') and Path('/usr/share/keyrings/debian-archive-keyring.gpg').is_file(), 'Debian archive public keyring and gpgv required')
    def test_pinned_metadata_replays_offline_into_empty_cache(self):
        lock = json.loads((build.ROOT / 'system/arm64-rootfs/package-lock.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            text, auth = build.authenticate(cache, lock['profile'], expected=lock['authentication'], offline=True)
            self.assertIn('Package: python3\n', text)
            self.assertEqual(auth['index_sha256'], lock['authentication']['index_sha256'])
            (cache / 'InRelease').write_bytes((cache / 'InRelease').read_bytes().replace(b'Codename: trixie', b'Codename: forged'))
            with self.assertRaises(ValueError): build.authenticate(cache, lock['profile'], expected=lock['authentication'], offline=True)

    def test_profile_refuses_installation_and_foreign_architecture(self):
        profile = build.profile_read(build.ROOT / 'system/arm64-rootfs/debian-trixie-qt5.json')
        for key, value in [('architecture', 'amd64'), ('execute_maintainer_scripts', True), ('include_recommends', True)]:
            with self.subTest(key=key), self.assertRaises(ValueError): build.profile_read({**profile, key: value})

    def test_release_identity_and_freshness(self):
        profile = {'suite': 'trixie', 'architecture': 'arm64', 'component': 'main'}
        release = b'Origin: Debian\nCodename: trixie\nArchitectures: all arm64\nComponents: main\nDate: Sat, 12 Sep 2026 07:55:41 UTC\n'
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.assertEqual(build.release_metadata(release, profile, now=now)['codename'], 'trixie')
        for bad in [release.replace(b'trixie', b'bookworm'), release.replace(b'arm64', b'amd64'), release.replace(b'2026', b'2024'), release + b'Codename: trixie\n']:
            with self.assertRaises(ValueError): build.release_metadata(bad, profile, now=now)
        self.assertTrue(build.release_metadata(release.replace(b'2026', b'2024'), profile, now=now, allow_historical=True)['historical_replay_permitted'])

    def test_guest_paths_and_symlinks(self):
        self.assertEqual(build.guest_path('./usr/lib/a.so'), 'usr/lib/a.so')
        for name in ('/etc/passwd', '../outside', 'usr/../outside', 'bad\\file', 'bad\x00file'):
            with self.assertRaises(ValueError): build.guest_path(name)
        self.assertEqual(build.normalized_link('usr/lib/example.so', '/usr/lib/example.so.1'), 'example.so.1')
        with self.assertRaises(ValueError): build.normalized_link('usr/a', '../../../host')

    def test_package_declaration_validation(self):
        good = {'Package': 'example', 'Version': '1', 'Architecture': 'arm64', 'Filename': 'pool/main/e/example/a.deb', 'SHA256': 'a' * 64, 'Size': '12'}
        self.assertEqual(build.package_entry(good)['bytes'], 12)
        for filename in ('../host', 'pool/../host', 'pool/bad\\name', 'https://example.org/a'):
            with self.assertRaises(ValueError): build.package_entry({**good, 'Filename': filename})

    def test_stage_validation_rejects_missing_or_modified_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'example.py').write_text('x = 1\n')
            entries, code = build.inventory(root)
            manifest = {'format': build.STAGE_FORMAT, 'architecture': 'arm64', 'no_postinst': True,
                        'authentication': {'signature_verified': True}, 'package_lock_sha256': 'a' * 64,
                        'entries': entries, 'tree_sha256': build.tree_digest(entries), 'code_validation': code}
            self.assertTrue(build.verify_stage(root, manifest)['verified'])
            (root / 'extra').write_text('unexpected')
            with self.assertRaises(ValueError): build.verify_stage(root, manifest)
            (root / 'extra').unlink(); (root / 'example.py').write_text('x = 2\n')
            with self.assertRaises(ValueError): build.verify_stage(root, manifest)

    def test_foreign_elf_and_invalid_python_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; elf[18:20] = (62).to_bytes(2, 'little')
            (root / 'host').write_bytes(elf)
            with self.assertRaises(ValueError): build.inventory(root)
            (root / 'host').unlink(); python = root / 'usr/lib/python3.13'; python.mkdir(parents=True)
            (python / 'bad.py').write_text('def !invalid\n')
            with self.assertRaises(SyntaxError): build.inventory(root)
            self.assertFalse(any(root.rglob('__pycache__')))

    @unittest.skipUnless(os.name == 'posix' and shutil.which('dpkg-deb'), 'actual Linux dpkg data extraction required')
    def test_actual_package_data_only_and_special_bits_stripped(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; control = package / 'DEBIAN'; control.mkdir(parents=True)
            (control / 'control').write_text('Package: hf-rootfs-test\nVersion: 1\nArchitecture: arm64\nMaintainer: Test <test@example.invalid>\nDescription: extraction test\n')
            marker = base / 'must-not-exist'
            (control / 'postinst').write_text('#!/bin/sh\ntouch ' + str(marker) + '\n'); (control / 'postinst').chmod(0o755)
            data = package / 'usr/bin/example'; data.parent.mkdir(parents=True); data.write_text('data'); data.chmod(0o4755)
            (data.parent / 'alias').symlink_to('/usr/bin/example')
            os.link(data, data.parent / 'hardlink')
            archive = base / 'test.deb'
            subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(archive)], check=True, capture_output=True)
            root = base / 'root'; root.mkdir(); transforms = {'rewritten_symlinks': [], 'stripped_special_permissions': []}
            build.extract_archive(archive, root, transforms)
            self.assertFalse(marker.exists()); self.assertFalse((root / 'DEBIAN').exists())
            self.assertEqual((root / 'usr/bin/example').stat().st_mode & 0o7777, 0o755)
            self.assertIn('usr/bin/example', transforms['stripped_special_permissions'])
            self.assertEqual(os.readlink(root / 'usr/bin/alias'), 'example')
            self.assertEqual((root / 'usr/bin/hardlink').read_text(), 'data')
            repeated = base / 'repeated'; repeated.mkdir()
            build.extract_archive(archive, repeated, {'rewritten_symlinks': [], 'stripped_special_permissions': []})
            self.assertEqual(build.inventory(root), build.inventory(repeated))


if __name__ == '__main__': unittest.main()

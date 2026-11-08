"""Offline runtime evidence tests; never execute FreeBSD code or package tools."""
import argparse
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

IMAGE = Path(__file__).resolve().parents[1]/'system/freebsd/image'
sys.path.insert(0, str(IMAGE))
offline = importlib.import_module('verify_runtime_offline')
profile = importlib.import_module('runtime_profile')


class OfflineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = self.root/'payload'; self.payload.mkdir()
        self.provisional = self.root/'provisional.json'
        self.output = self.root/'offline.json'
        self.args = argparse.Namespace(provisional=str(self.provisional), payload=str(self.payload),
            output=str(self.output), runtime_output=None, verify_package_checksums=False)
        self.path = 'usr/local/bin/camera'
        self.data = b'camera resource\n'
        self.write_payload(self.path, self.data)
        self.row = {'path':self.path, 'type':'file', 'bytes':len(self.data), 'mode':(self.payload/self.path).stat().st_mode&0o777,
            'uid':0, 'gid':0, 'packages':['python312'],
            'declared_checksum':hashlib.sha256(self.data).hexdigest()}
        self.record = {'format':'DaOS-freebsd-runtime-provisional-v1', 'status':'metadata-only-unhashed',
            'architecture':'arm64', 'host':{'system':'FreeBSD','release':'15.1-RELEASE-p6','machine':'arm64'},
            'requested':['python312'], 'packages':[{'name':'python312','version':'3.12.12','origin':'lang/python312',
                'abi':'FreeBSD:15:aarch64','repository':'FreeBSD','package_checksum':'package-index-checksum',
                'dependencies':[], 'licenses':['PSFL']}], 'profile':'camera-runtime-v1',
            'curation_policy':profile.POLICY, 'entries':[self.row], 'omissions':[],
            'installed_checksums_verified':False, 'package_scripts_run':False,
            'repository_signature_verified_by_collector':False,
            'source_code_sha256':{'export_runtime_metadata.py':'b'*64},
            'base_dependency_manifest':[{'path':'lib/libc.so.7','type':'file','sha256':'a'*64,
                'machine':183,'osabi':9,'needed':[]}], 'loader_configuration':[]}
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def write_payload(self, name, data):
        path = self.payload/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data); path.chmod(0o644)
        return path

    def save(self):
        _, _, statistics = profile.curate(self.record['entries']+self.record['omissions'], self.record['profile'])
        self.record['statistics'] = statistics
        self.provisional.write_text(json.dumps(self.record), encoding='utf-8')

    def test_unhashed_source_gets_new_observation_without_pkg_or_target_claims(self):
        with patch.object(offline.platform, 'system', return_value='Linux'):
            result = offline.verify(self.args)
        self.assertEqual('DaOS-freebsd-runtime-offline-validation-v1', result['format'])
        self.assertEqual('FreeBSD', result['source_host']['system'])
        self.assertEqual('Linux', result['verification_host']['system'])
        self.assertEqual(1, result['offline_hash_observations']['new_sha256_files'])
        self.assertFalse(result['source_claims']['installed_checksums_verified'])
        self.assertNotIn('installed_checksums_verified', result)
        self.assertFalse(result['target_execution_validated'])
        self.assertFalse(result['physical_hardware_validated'])
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), result['entries'][0]['sha256'])
        self.assertEqual(hashlib.sha256(self.provisional.read_bytes()).hexdigest(), result['source_provisional_sha256'])
        self.assertFalse((self.root/'runtime.json').exists())

    def test_prior_hash_is_required_to_match_and_not_replaced(self):
        self.record['entries'][0]['sha256'] = '0'*64
        self.record['status'] = 'payload-hashed-awaiting-elf'; self.save()
        with self.assertRaisesRegex(ValueError, 'SHA256 differs'): offline.verify(self.args)
        self.assertFalse(self.output.exists())

    def test_hashed_status_rejects_absent_hash(self):
        self.record['status'] = 'payload-hashed-awaiting-elf'; self.save()
        with self.assertRaisesRegex(ValueError, 'lacks retained file hashes'): offline.verify(self.args)

    def test_existing_or_reserved_receipts_are_never_overwritten(self):
        self.output.write_text('preserve')
        with self.assertRaises(ValueError): offline.verify(self.args)
        self.assertEqual('preserve', self.output.read_text())
        self.args.output = str(self.root/'runtime.json')
        with self.assertRaises(ValueError): offline.verify(self.args)

    def test_report_inside_payload_refused_without_changes(self):
        self.args.output = str(self.payload/'report.json')
        with self.assertRaises(ValueError): offline.verify(self.args)
        self.assertFalse((self.payload/'report.json').exists())

    def test_exact_set_size_and_mode_are_enforced(self):
        self.write_payload('foreign', b'unexpected')
        with self.assertRaisesRegex(ValueError, 'exact declared profile'): offline.verify(self.args)
        (self.payload/'foreign').unlink()
        (self.payload/self.path).write_bytes(b'truncated')
        with self.assertRaisesRegex(ValueError, 'size/mode'): offline.verify(self.args)

    def test_profile_statistics_and_curation_cannot_be_forged(self):
        self.record['statistics']['kept_bytes'] += 1
        self.provisional.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, 'profile/counts'): offline.verify(self.args)
        self.save(); self.record['curation_policy'] = {'id':'different'}; self.save()
        with self.assertRaisesRegex(ValueError, 'curation policy'): offline.verify(self.args)

    def test_wrong_source_package_or_base_architecture_is_rejected(self):
        self.record['packages'][0]['abi'] = 'FreeBSD:15:amd64'; self.save()
        with self.assertRaisesRegex(ValueError, 'ABI'): offline.verify(self.args)
        self.record['packages'][0]['abi'] = 'FreeBSD:15:*'
        self.record['base_dependency_manifest'][0]['machine'] = 62; self.save()
        with self.assertRaisesRegex(ValueError, 'base ELF'): offline.verify(self.args)

    def test_wrong_payload_elf_architecture_fails_static_closure(self):
        ident = b'\x7fELF\x02\x01\x01\x09' + b'\0'*8
        data = struct.pack('<16sHHIQQQIHHHHHH', ident, 3, 62, 1, 0, 0, 0, 0, 64, 56, 0, 0, 0, 0)
        self.write_payload(self.path, data); self.record['entries'][0]['bytes'] = len(data); self.save()
        with self.assertRaisesRegex(ValueError, 'machine'): offline.verify(self.args)
        self.assertFalse(self.output.exists())

    def test_loader_hint_data_is_reconstructed_and_bound(self):
        name = 'usr/local/libdata/ldconfig/camera'
        data = b'/usr/local/lib/private\n'; self.write_payload(name, data)
        row = dict(self.row, path=name, bytes=len(data), declared_checksum=hashlib.sha256(data).hexdigest())
        self.record['entries'].append(row)
        self.record['loader_configuration'] = [{'path':name,'sha256':hashlib.sha256(data).hexdigest(),
                                                 'directories':['/usr/local/lib/private']}]
        self.save(); result = offline.verify(self.args)
        self.assertEqual(['/usr/local/lib/private'], result['elf_dependency_check']['extra_search_paths'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX literal symlink fixture')
    def test_literal_symlinks_and_redirected_ancestors_are_checked(self):
        name = 'usr/local/bin/alias'; (self.payload/name).symlink_to('camera')
        self.record['entries'].append(dict(self.row, path=name, type='link', link='camera'))
        self.save(); result = offline.verify(self.args)
        self.assertEqual(2, len(result['entries']))
        self.output.unlink(); (self.payload/name).unlink(); (self.payload/name).symlink_to('other')
        with self.assertRaisesRegex(ValueError, 'literal symlink'): offline.verify(self.args)

    def test_runtime_export_requires_source_pkg_check_evidence(self):
        self.args.runtime_output = str(self.root/'DaOS-runtime-verified')
        with self.assertRaisesRegex(ValueError, 'source pkg checks'): offline.verify(self.args)
        self.assertFalse(Path(self.args.runtime_output).exists())

    def test_runtime_export_fresh_declared_checksums_and_truthful_host(self):
        self.record.update({'installed_checksums_verified':True,
            'completed_runtime':False, 'payload_content_verified':False,
            'signature_scope':'Pending payload verification and static ELF closure.'})
        self.save()
        self.args.runtime_output = str(self.root/'DaOS-runtime-verified')
        before = (self.payload/self.path).stat()
        result = offline.verify(self.args)
        runtime_path = Path(self.args.runtime_output)/'runtime.json'
        runtime = json.loads(runtime_path.read_text())
        exported = Path(self.args.runtime_output)/'payload'/self.path
        self.assertEqual('DaOS-freebsd-runtime-v2', runtime['format'])
        self.assertTrue(runtime['installed_checksums_verified'])
        self.assertTrue(runtime['offline_declared_checksums_verified'])
        self.assertEqual('completed', runtime['status'])
        self.assertTrue(runtime['completed_runtime'])
        self.assertTrue(runtime['payload_content_verified'])
        self.assertEqual('metadata-only-unhashed', runtime['source_status'])
        self.assertFalse(runtime['source_provisional_claims']['completed_runtime'])
        self.assertFalse(runtime['source_provisional_claims']['payload_content_verified'])
        self.assertEqual(self.record['signature_scope'], runtime['source_provisional_claims']['signature_scope'])
        self.assertEqual('metadata-only-unhashed', runtime['source_provisional_claims']['status'])
        self.assertIn('declared pkg checksum and SHA256', runtime['signature_scope'])
        self.assertIn('static ELF dependency closure', runtime['signature_scope'])
        self.assertIn('does not independently attest archive/repository signatures', runtime['signature_scope'])
        self.assertNotIn('Pending', runtime['signature_scope'])
        self.assertFalse(runtime['target_execution_validated'])
        self.assertFalse(runtime['physical_hardware_validated'])
        self.assertEqual('arm64', runtime['host']['machine'])
        self.assertEqual(1, result['offline_hash_observations']['declared_package_checksum_files_verified'])
        self.assertEqual(before.st_ino, exported.stat().st_ino)
        self.assertEqual(before.st_mtime_ns, (self.payload/self.path).stat().st_mtime_ns)
        self.assertEqual(before.st_mode, (self.payload/self.path).stat().st_mode)

    def test_invalid_declared_checksum_cannot_export_a_runtime(self):
        self.record['installed_checksums_verified'] = True
        self.record['entries'][0]['declared_checksum'] = 'f'*64; self.save()
        self.args.runtime_output = str(self.root/'DaOS-runtime-verified')
        with self.assertRaisesRegex(ValueError, 'checksum'): offline.verify(self.args)
        self.assertFalse(Path(self.args.runtime_output).exists())

    def test_output_overlap_and_duplicate_json_fields_refused(self):
        self.record['installed_checksums_verified'] = True; self.save()
        self.args.runtime_output = str(self.payload/'DaOS-runtime-nested')
        with self.assertRaisesRegex(ValueError, 'separate'): offline.verify(self.args)
        self.args.runtime_output = None
        self.provisional.write_text('{"format":"x","format":"y"}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'): offline.verify(self.args)


if __name__ == '__main__': unittest.main()

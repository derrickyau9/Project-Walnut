"""Cross-build guards and synthetic SDK parsing, never target execution proof."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import freebsd_cross_build as cross


def elf_header(machine=183, osabi=9):
    identity = bytearray(b'\x7fELF\x02\x01\x01' + bytes(9)); identity[7] = osabi
    return struct.pack('<16sHHIQQQIHHHHHH', bytes(identity), 2, machine, 1,
                       0, 0, 0, 0, 64, 0, 0, 0, 0, 0)


class SysrootReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'stage'; self.root.mkdir()
        hashes = {}
        for name in (*cross.BASE_ANCHORS, *cross.SDK_ANCHORS):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            data = elf_header() if name in cross.SDK_ELFS else b'unit-test fixture only\n'
            if name == 'usr/lib/libc++.so':
                data = b'GROUP ( /lib/libc++.so.1 /usr/lib/libcxxrt.so )\n'
            path.write_bytes(data)
            if name in cross.BASE_ANCHORS: hashes[name] = hashlib.sha256(data).hexdigest()
        self.state = {'format': 'DaOS-freebsd-base-build-v2', 'status': 'completed',
                      'target': {'architecture': 'arm64', 'make_target': 'arm64',
                                 'make_target_arch': 'aarch64', 'kernel_ident': 'DaOS', 'elf_machine': 183},
                      'source': {'architecture': 'arm64', 'release': '15.1-RELEASE-p6',
                                 'commit': cross.SOURCE_COMMIT, 'archive_sha256': cross.SOURCE_ARCHIVE_SHA256},
                      'install_policy': {'no_root': True, 'destination_is_private': True,
                                         'live_root_installed': False},
                      'destination': str(self.root), 'stage_directory': str(self.root),
                      'artifact_sha256': hashes}
        self.receipt = Path(self.temp.name) / 'base-evidence.json'
        self.write(self.state)

    def write(self, state): self.receipt.write_text(json.dumps(state))

    def test_valid_synthetic_receipt_is_consistency_only_not_runtime_success(self):
        result = cross.validate_sysroot(self.root, self.receipt)
        self.assertFalse(result['target_execution_validated'])
        self.assertNotIn('passed', result)
        self.assertEqual(183, result['anchors']['bin/sh']['machine'])

    def test_actual_profile_has_lib_cxx_runtimes_and_contained_linker_script(self):
        self.assertFalse((self.root / 'usr/lib/libc++.so.1').exists())
        result = cross.validate_sysroot(self.root, self.receipt)
        for name in ('lib/libc++.so.1', 'lib/libcxxrt.so.1', 'usr/lib/libcxxrt.so'):
            self.assertEqual(183, result['anchors'][name]['machine'])
            self.assertEqual(9, result['anchors'][name]['osabi'])
        script = result['anchors']['usr/lib/libc++.so']['linker_script']
        self.assertEqual('explicit_target_sysroot', script['resolution'])
        self.assertEqual({'/lib/libc++.so.1', '/usr/lib/libcxxrt.so'}, set(script['targets']))

    def test_cxx_linker_script_cannot_add_host_paths_or_extra_directives(self):
        path = self.root / 'usr/lib/libc++.so'
        for changed in (b'GROUP ( /usr/local/lib/libc++.so.1 /usr/lib/libcxxrt.so )\n',
                        b'GROUP ( /lib/libc++.so.1 /usr/lib/libcxxrt.so )\nSEARCH_DIR(/lib)\n',
                        b'GROUP ( ../../lib/libc++.so.1 /usr/lib/libcxxrt.so )\n',
                        b'\xff', b' ' * 4097):
            with self.subTest(script=changed[:80]):
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, 'libc\\+\\+ linker script'):
                    cross.validate_sysroot(self.root, self.receipt)

    def test_cxx_runtime_or_linker_entry_cannot_hide_host_architecture(self):
        for name in ('lib/libc++.so.1', 'lib/libcxxrt.so.1', 'usr/lib/libcxxrt.so'):
            path = self.root / name; original = path.read_bytes()
            path.write_bytes(elf_header(machine=62))
            with self.subTest(anchor=name), self.assertRaisesRegex(ValueError, 'ARM64 FreeBSD ELF'):
                cross.validate_sysroot(self.root, self.receipt)
            path.write_bytes(original)

    @unittest.skipUnless(os.name == 'posix', 'real POSIX target-runtime link checks')
    def test_cxx_runtime_link_resolves_inside_target_not_live_host(self):
        path = self.root / 'usr/lib/libcxxrt.so'; path.unlink()
        path.symlink_to('../../lib/libcxxrt.so.1')
        result = cross.validate_sysroot(self.root, self.receipt)
        self.assertEqual('lib/libcxxrt.so.1', result['anchors']['usr/lib/libcxxrt.so']['resolved_relative_path'])
        path.unlink(); path.symlink_to('/lib/libcxxrt.so.1')
        with self.assertRaises((ValueError, FileNotFoundError)):
            cross.validate_sysroot(self.root, self.receipt)

    def test_completed_amd64_unpinned_or_live_root_receipts_are_rejected(self):
        changes = [('status', 'running'), ('format', 'daos-freebsd-base-build-v1'),
                   ('target.architecture', 'amd64'), ('target.elf_machine', 62),
                   ('source.commit', '0' * 40), ('source.release', '15.1-RELEASE'),
                   ('source.archive_sha256', '0' * 64), ('source.architecture', 'amd64'),
                   ('install_policy.no_root', False), ('install_policy.live_root_installed', True),
                   ('install_policy.destination_is_private', False), ('destination', '/'),
                   ('stage_directory', str(self.root.parent))]
        for field, changed in changes:
            with self.subTest(field=field):
                state = copy.deepcopy(self.state); target = state; parts = field.split('.')
                for part in parts[:-1]: target = target[part]
                target[parts[-1]] = changed; self.write(state)
                with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)

    def test_modified_missing_or_host_architecture_sdk_anchors_are_rejected(self):
        path = self.root / 'bin/sh'; original = path.read_bytes()
        for changed in (b'changed', elf_header(machine=62), elf_header(osabi=0)):
            path.write_bytes(changed)
            with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)
        path.write_bytes(original)
        (self.root / 'usr/include/stdio.h').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            cross.validate_sysroot(self.root, self.receipt)

    def test_even_matching_forged_hash_cannot_hide_amd64_elf(self):
        path = self.root / 'lib/libc.so.7'; path.write_bytes(elf_header(machine=62))
        self.state['artifact_sha256']['lib/libc.so.7'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write(self.state)
        with self.assertRaisesRegex(ValueError, 'ARM64 FreeBSD ELF'):
            cross.validate_sysroot(self.root, self.receipt)

    @unittest.skipUnless(os.name == 'posix', 'real POSIX symlink checks')
    def test_linked_sysroot_or_sdk_escape_is_preserved_and_refused(self):
        alias = self.root.parent / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): cross.validate_sysroot(alias, self.receipt)
        path = self.root / 'usr/include/stdio.h'; path.unlink()
        outside = self.root.parent / 'outside'; outside.write_text('private fixture')
        path.symlink_to(outside)
        with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)
        self.assertEqual('private fixture', outside.read_text())

    def test_duplicate_json_and_relative_sysroot_do_not_select_target(self):
        self.receipt.write_text('{"status":"completed","status":"failed"}')
        with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)
        with self.assertRaises(ValueError): cross.validate_sysroot(Path('relative-stage'), self.receipt)

    def test_non_object_metadata_and_overflowing_numbers_are_rejected(self):
        for value in ([], dict(self.state, target=[]), dict(self.state, source=[]),
                      dict(self.state, install_policy=[])):
            self.write(value)
            with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)
        self.receipt.write_text('{"overflow":1e999}')
        with self.assertRaises(ValueError): cross.validate_sysroot(self.root, self.receipt)


class ToolchainTests(unittest.TestCase):
    def test_target_toolchain_pins_arm64_sysroot_lld_and_never_an_emulator(self):
        tools = {key: {'invoked_path': value} for key, value in (
            ('cc', '/usr/bin/cc'), ('cxx', '/usr/bin/c++'), ('lld', '/usr/bin/ld.lld'))}
        text = cross.toolchain_text('/private/DaOS-stage', tools)
        self.assertIn('CMAKE_SYSTEM_PROCESSOR aarch64', text)
        self.assertIn('aarch64-unknown-freebsd15.1', text)
        self.assertIn('CMAKE_SYSROOT "/private/DaOS-stage"', text)
        self.assertIn('CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY', text)
        self.assertIn('CMAKE_CROSSCOMPILING_EMULATOR ""', text)
        self.assertNotIn('linux', text.lower())

    def test_cmake_path_injection_is_refused(self):
        for value in ('/tmp/a;b', '/tmp/a\nset(EVIL yes)', '/tmp/a\0b'):
            with self.assertRaises(ValueError): cross._cmake_string(value)


@unittest.skipIf(sys.platform.startswith('freebsd'), 'actual non-FreeBSD refusal coverage')
class ActualHostRefusalTests(unittest.TestCase):
    def test_refusal_precedes_sysroot_tool_reads_and_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / 'build/new'
            with (mock.patch.object(cross, 'validate_sysroot') as sdk,
                  mock.patch.object(cross, 'cross_source_inputs') as sources,
                  self.assertRaisesRegex(RuntimeError, 'actual FreeBSD')):
                cross.build(root, sysroot=root/'missing', base_evidence=root/'none', destination=output)
            sdk.assert_not_called(); sources.assert_not_called(); self.assertFalse(output.exists())

    def test_real_cli_environment_cannot_spoof_arm64_freebsd_build(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'not-created'
            result = subprocess.run([sys.executable, '-B', '-m', 'tools.freebsd_cross_build',
                                     '--sysroot', temp, '--base-evidence', str(Path(temp)/'missing.json'),
                                     '--build-dir', str(output)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                    env=dict(os.environ, HF_PLATFORM='FreeBSD', TARGET_ARCH='aarch64',
                                             PYTHONDONTWRITEBYTECODE='1'), timeout=15)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('actual FreeBSD', result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()

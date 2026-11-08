"""Focused guards for the reusable selected-image update guest adapter."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tools.image_build import newc
from tools import update_guest_boot as guest

ATTEMPT = '00000000-0000-0000-0000-000000000001'
CAPTURE = '00000000-0000-0000-0000-000000000002'


def serial(*, healthy=True, **changes):
    compact = {'attempt_id': ATTEMPT, 'slot': 'B', 'version': 2, 'health_mode': 'healthy',
               'healthy': healthy, 'report': ATTEMPT + '.json', 'report_sha256': 'a' * 64}
    compact.update(changes)
    return ('\n'.join([
        'HF_UPDATE_INIT pid=1 machine=aarch64 attempt_id=' + ATTEMPT + ' slot=B version=2',
        'HF_UPDATE_ROOT readonly=1 filesystem=ext4 device=vda',
        'HF_UPDATE_DATA readwrite=1 filesystem=ext4 device=vdb',
        'HF_UPDATE_RUNTIME uid=1000 attempt_id=' + ATTEMPT,
        'HF_UPDATE_HEALTH ' + json.dumps(compact),
        'HF_UPDATE_CHILD status=0 attempt_id=' + ATTEMPT,
        'HF_UPDATE_DATA_UNMOUNTED clean=1',
        'HF_UPDATE_RESULT PASS attempt_id=' + ATTEMPT + ' slot=B version=2']) + '\n').encode()


class UpdateGuestGuards(unittest.TestCase):
    def parse(self, raw):
        return guest.parse_serial(raw, attempt_id=ATTEMPT, slot='B', version=2, health_mode='healthy')

    def test_serial_requires_actual_identity_and_unique_clean_lifecycle(self):
        good = serial()
        self.assertTrue(self.parse(good)['healthy'])
        for line in good.splitlines():
            for raw in (good.replace(line + b'\n', b''), good + line + b'\n'):
                with self.subTest(line=line[:60]), self.assertRaises(ValueError):
                    self.parse(raw)
        for old, replacement in [(b'pid=1', b'pid=42'), (b'uid=1000', b'uid=0'),
                                 (b'readonly=1', b'readonly=0'), (b'status=0', b'status=1'),
                                 (b'clean=1', b'clean=0'), (b'PASS', b'FAIL'),
                                 (b'version=2', b'version=3')]:
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.parse(good.replace(old, replacement))

    def test_compact_rejects_wrong_types_and_identity(self):
        for changes in ({'healthy': 1}, {'version': True}, {'slot': 'A'}, {'attempt_id': CAPTURE},
                        {'report': '../escape.json'}, {'report_sha256': 'A' * 64}, {'health_mode': 'fail'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(serial(**changes))
        with self.assertRaises(ValueError): self.parse(b'x' * (guest.MAX_SERIAL + 1))

    def test_explicit_unhealthy_result_is_preserved_as_false(self):
        raw = serial(healthy=False, health_mode='fail')
        result = guest.parse_serial(raw, attempt_id=ATTEMPT, slot='B', version=2, health_mode='fail')
        self.assertIs(result['healthy'], False)

    def test_json_rejects_duplicates_constants_and_overflowed_floats(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): guest.strict_json(raw)

    def test_newc_reads_payloads_without_host_extraction(self):
        raw = newc([('modules', b'', stat.S_IFDIR | 0o755, 0, 0),
                    ('modules/test.ko', b'fixed\0payload', stat.S_IFREG | 0o600, 0, 0)])
        parsed = guest.parse_newc(raw)
        self.assertEqual(parsed['modules/test.ko'][1], b'fixed\0payload')
        self.assertEqual(set(parsed), {'modules', 'modules/test.ko'})

    def test_newc_rejects_truncation_bad_headers_ownership_and_unsafe_names(self):
        raw = newc([('init', b'payload', stat.S_IFREG | 0o755, 0, 0)])
        bad_owner = bytearray(raw)
        bad_owner[22:30] = b'00000001'
        bad_path = raw.replace(b'init\0', b'../x\0')
        for invalid in (b'not cpio', raw[:-1], raw[:50], bytes(bad_owner), bad_path, raw + b'x'):
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError): guest.parse_newc(invalid)

    def test_output_directory_is_exclusive_and_canonical(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'new-runtime'
            self.assertEqual(guest._new_directory(path), path)
            with self.assertRaises(ValueError): guest._new_directory(path)

    def test_boot_identity_validation_rejects_kernel_argument_injection(self):
        valid = (ATTEMPT, 'A', 1, 'healthy', CAPTURE, 'a' * 64, 'b' * 64)
        guest._identities(*valid)
        for index, value in [(0, ATTEMPT.upper().replace('0', 'A', 1)), (1, 'A extra=1'),
                             (2, True), (2, '1 root=/dev/host'), (2, 0),
                             (3, 'pass'), (4, '../capture'), (5, 'a' * 63)]:
            changed = list(valid); changed[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError): guest._identities(*changed)

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() != 0 and
                         shutil.which('aarch64-linux-gnu-gcc') and shutil.which('qemu-aarch64'),
                         'requires existing non-root Linux, ARM64 static compiler and user QEMU')
    def test_actual_static_init_refuses_user_mode_before_privileged_operations(self):
        with tempfile.TemporaryDirectory(prefix='hf-update-init-guard-') as temporary:
            output = Path(temporary) / 'init'
            compiled = subprocess.run([shutil.which('aarch64-linux-gnu-gcc'), '-std=c11', '-O2', '-static',
                '-Wall', '-Wextra', '-Werror', str(guest.ROOT / 'system/update-model/init.c'), '-o', str(output)],
                capture_output=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr.decode('utf-8', 'replace'))
            environment = dict(os.environ, HASSLEFREE_ARM64_UPDATE='1', HF_UPDATE_GUEST='1',
                HF_UPDATE_ATTEMPT_ID=ATTEMPT, HF_UPDATE_SLOT='A', HF_UPDATE_VERSION='1',
                HF_UPDATE_HEALTH_MODE='healthy', HF_UPDATE_CAPTURE_ID=CAPTURE,
                HF_UPDATE_DNG_SHA='a' * 64, HF_UPDATE_NATIVE_SHA='b' * 64, HF_UPDATE_INITIALIZE_FIXTURES='0')
            result = subprocess.run([shutil.which('qemu-aarch64'), '-strace', str(output)], env=environment,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertIn(b'HF_UPDATE_INIT guest-only', result.stderr)
            self.assertNotRegex(result.stderr.decode('utf-8', 'replace'), r'\b(?:mount|finit_module|chroot|reboot)\(')


if __name__ == '__main__':
    unittest.main()

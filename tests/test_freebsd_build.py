"""Host-safe guard/parser checks; no fixture is FreeBSD execution evidence."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools import freebsd_build


class FreeBSDDestinationTests(unittest.TestCase):
    def test_only_new_project_build_child_is_accepted_without_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / 'build/new-native'
            self.assertEqual(expected.resolve(), freebsd_build.validate_destination(root, 'build/new-native'))
            self.assertFalse(expected.exists())
            self.assertFalse((root / 'build').exists())

    def test_existing_source_userdata_outside_and_build_root_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'; root.mkdir()
            existing = root / 'build/existing'; existing.mkdir(parents=True)
            for path in (root, root / 'build', existing, root / 'camera/native/new',
                         root / 'var/photos/new', root.parent / 'other', root / 'build/../camera/out'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    freebsd_build.validate_destination(root, path)
            self.assertEqual([existing], list((root / 'build').iterdir()))

    @unittest.skipUnless(os.name == 'posix', 'actual POSIX symlink checks')
    def test_linked_ancestor_and_dangling_destination_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'; root.mkdir(); (root / 'build').mkdir()
            external = Path(tmp) / 'external'; external.mkdir()
            link = root / 'build/linked'; link.symlink_to(external, target_is_directory=True)
            dangling = root / 'build/dangling'; dangling.symlink_to(root / 'missing')
            for path in (link / 'new', dangling):
                with self.assertRaises(ValueError): freebsd_build.validate_destination(root, path)
            self.assertTrue(link.is_symlink()); self.assertTrue(dangling.is_symlink())
            self.assertEqual([], list(external.iterdir()))


@unittest.skipIf(sys.platform.startswith('freebsd'), 'refusal is tested only on an actual non-FreeBSD host')
class ActualHostRefusalTests(unittest.TestCase):
    def test_build_refuses_before_tools_source_reads_or_destination_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'never-created'
            with (mock.patch.object(freebsd_build, 'source_inputs') as inputs,
                  mock.patch.object(freebsd_build, 'tool_identity') as tools,
                  self.assertRaisesRegex(RuntimeError, 'actual FreeBSD')):
                freebsd_build.build(Path(tmp), destination)
            inputs.assert_not_called(); tools.assert_not_called()
            self.assertFalse(destination.exists())

    def test_environment_label_cannot_make_real_cli_claim_freebsd(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'never-created'
            result = subprocess.run([sys.executable, '-B', '-m', 'tools.freebsd_build',
                                     '--build-dir', str(destination)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                    env=dict(os.environ, HF_PLATFORM='FreeBSD', HF_FREEBSD='1',
                                             OSTYPE='FreeBSD', PYTHONDONTWRITEBYTECODE='1'), timeout=15)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('actual FreeBSD', result.stderr)
            self.assertFalse(destination.exists())


class ElfParserTests(unittest.TestCase):
    def fixture(self, path, *, osabi=9, machine=62):
        identity = bytearray(b'\x7fELF\x02\x01\x01' + bytes(9)); identity[7] = osabi
        header = struct.pack('<16sHHIQQQIHHHHHH', bytes(identity), 2, machine, 1,
                             0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        program = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, 120, 120, 4096)
        path.write_bytes(header + program)

    def test_synthetic_header_parse_is_labeled_bytes_not_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'header-only'; self.fixture(path)
            result = freebsd_build.inspect_freebsd_elf(path, 62)
            self.assertEqual(9, result['osabi']); self.assertEqual('EM_X86_64', result['machine_name'])
            self.assertNotIn('passed', result); self.assertEqual(120, result['bytes'])

    def test_linux_osabi_wrong_arch_truncation_and_bad_segments_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'header-only'
            for osabi, machine in ((0, 62), (3, 62), (9, 183)):
                self.fixture(path, osabi=osabi, machine=machine)
                with self.assertRaises(ValueError): freebsd_build.inspect_freebsd_elf(path, 62)
            self.fixture(path); path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError): freebsd_build.inspect_freebsd_elf(path, 62)


@unittest.skipUnless(os.name == 'posix', 'bounded owned POSIX process group checks')
class BoundedCommandTests(unittest.TestCase):
    def test_output_limit_is_not_a_success_and_log_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'output.log'
            with self.assertRaisesRegex(RuntimeError, 'output limit'):
                freebsd_build.run_bounded([sys.executable, '-c', 'import os;os.write(1,b"x"*50000)'],
                                          cwd=tmp, log_path=log, max_bytes=512, timeout=5)
            self.assertLessEqual(log.stat().st_size, 512)

    def test_timeout_is_bounded_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'timeout.log'; marker = Path(tmp) / 'started'
            code = 'from pathlib import Path;import time;Path("started").write_text("one");time.sleep(30)'
            start = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'wall-time'):
                freebsd_build.run_bounded([sys.executable, '-c', code], cwd=tmp,
                                          log_path=log, timeout=.2)
            self.assertLess(time.monotonic() - start, 5)
            self.assertEqual('one', marker.read_text())


if __name__ == '__main__':
    unittest.main()

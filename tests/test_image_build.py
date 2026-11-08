"""Image packaging and boot evidence guards; actual user-mode init must refuse PID != 1."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import image_boot, image_build


RID = 'a7c41d22-6607-4cd6-9000-000000000099'


def _compact(phase='first', **changes):
    result = {'phase': phase, 'passed': True, 'request_id': RID,
              'report': phase + '.json', 'report_sha256': 'a' * 64,
              'journal_rows': 1, 'native_process_closed': True}
    result.update(changes)
    return result


def _serial(phase='first', compact=None):
    lines = [
        'HF_IMAGE_INIT pid=1 machine=aarch64 phase=' + phase + ' kernel=fixture',
        'HF_IMAGE_ROOT readonly=1 filesystem=ext4 device=vda',
        'HF_IMAGE_DATA readwrite=1 filesystem=ext4 device=vdb',
        'HF_IMAGE_RUNTIME uid=1000 phase=' + phase + ' python=/usr/bin/python3',
        'HF_IMAGE_UI_RESULT ' + json.dumps(_compact(phase) if compact is None else compact),
        'HF_IMAGE_CHILD phase=' + phase + ' status=0',
        'HF_IMAGE_DATA_UNMOUNTED clean=1',
        'HF_IMAGE_RESULT PASS phase=' + phase + ' immutable-rootfs separate-data synthetic-only',
    ]
    return ('kernel noise\n' + '\n'.join(lines) + '\n').encode()


def _read_newc(data):
    """Independent minimal newc reader checks lengths, alignment and root ownership."""
    result, offset = [], 0
    while offset < len(data):
        if data[offset:offset + 6] != b'070701':
            raise AssertionError('not a newc header')
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        name_start = offset + 110
        name_end = name_start + fields[11]
        name = data[name_start:name_end]
        if not name.endswith(b'\0'):
            raise AssertionError('newc name is not terminated')
        content_start = (name_end + 3) & ~3
        content_end = content_start + fields[6]
        result.append((name[:-1].decode(), fields, data[content_start:content_end]))
        offset = (content_end + 3) & ~3
        if name == b'TRAILER!!!\0':
            if offset != len(data):
                raise AssertionError('data follows the newc terminator')
            return result
    raise AssertionError('missing newc terminator')


def _elf(machine=183, interpreter=False):
    identity = b'\x7fELF\x02\x01\x01' + bytes(9)
    header = struct.pack('<16sHHIQQQIHHHHHH', identity, 2, machine, 1, 0x400000,
                         64, 0, 0, 64, 56, 1, 0, 0, 0)
    program = struct.pack('<IIQQQQQQ', 3 if interpreter else 1, 5, 0, 0x400000,
                          0x400000, 120, 120, 4096)
    return header + program


class ImageBuildGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-image-build-guard-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.payload = self.root / 'payload'
        self.payload.write_bytes(b'fixed rootfs payload\n')

    def test_member_names_reject_absolute_traversal_aliases_and_controls(self):
        for value in ['usr/bin/python3', 'etc/absolute-link', 'usr/share/caf\u00e9']:
            self.assertEqual(image_build.safe_name(value), value)
        for value in ['', '/root', '../escape', 'usr/../../escape', 'usr/./file',
                      'usr//file', 'usr/', 'usr\\file', 'nul\0name', 'line\nname',
                      'del\x7fname', None, 42, b'bin']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                image_build.safe_name(value)

    def test_tar_is_sorted_deterministic_root_owned_fixed_epoch_and_link_preserving(self):
        entries = [
            {'path': 'usr/share/payload', 'type': 'file', 'source': self.payload, 'mode': '0640'},
            {'path': 'etc/link', 'type': 'symlink', 'target': '/usr/share/payload', 'mode': 0o777},
            {'path': 'var/empty', 'type': 'directory', 'mode': 0o750},
        ]
        first, second = self.root / 'one.tar', self.root / 'two.tar'
        before = image_build.write_tar(first, entries)
        os.utime(self.payload, (1, 1))
        after = image_build.write_tar(second, list(reversed(entries)))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual(before['sha256'], hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertEqual(before['file_bytes'], len(self.payload.read_bytes()))
        with tarfile.open(first, 'r:') as archive:
            names = archive.getnames()
            self.assertEqual(names, sorted(names))
            self.assertEqual(set(names), {'usr', 'usr/share', 'usr/share/payload',
                                          'etc', 'etc/link', 'var', 'var/empty'})
            for member in archive:
                self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, '', ''))
                self.assertEqual(member.mtime, image_build.EPOCH)
            self.assertEqual(archive.extractfile('usr/share/payload').read(), self.payload.read_bytes())
            self.assertEqual(archive.getmember('usr/share/payload').mode, 0o640)
            self.assertTrue(archive.getmember('etc/link').issym())
            self.assertEqual(archive.getmember('etc/link').linkname, '/usr/share/payload')
        self.assertFalse((self.root / 'usr').exists())

    def test_tar_duplicate_and_link_or_file_parents_rejected_before_output_creation(self):
        file_entry = {'path': 'parent/item', 'type': 'file', 'source': self.payload, 'mode': 0o644}
        cases = [[file_entry, file_entry],
                 [{'path': '../escape', 'type': 'directory', 'mode': 0o755}],
                 [{'path': 'parent', 'type': 'symlink', 'target': '/usr', 'mode': 0o777}, file_entry],
                 [{'path': 'parent', 'type': 'file', 'source': self.payload, 'mode': 0o644}, file_entry]]
        for index, entries in enumerate(cases):
            destination = self.root / ('bad-' + str(index) + '.tar')
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                image_build.write_tar(destination, entries)
            self.assertFalse(destination.exists())
        target = self.root / 'exists.tar'
        target.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            image_build.write_tar(target, [])
        self.assertEqual(target.read_bytes(), b'keep this')

    def test_newc_is_deterministic_root_owned_aligned_and_preserves_guest_device_numbers(self):
        entries = [('init', b'fixture\0bytes', stat.S_IFREG | 0o755, 0, 0),
                   ('dev', b'', stat.S_IFDIR | 0o755, 0, 0),
                   ('dev/console', b'', stat.S_IFCHR | 0o600, 5, 1)]
        data = image_build.newc(entries)
        self.assertEqual(data, image_build.newc(list(reversed(entries))))
        self.assertEqual(len(data) % 4, 0)
        members = _read_newc(data)
        self.assertEqual([name for name, _, _ in members], ['dev', 'dev/console', 'init', 'TRAILER!!!'])
        for _, fields, _ in members:
            self.assertEqual(fields[2:4], [0, 0])
            self.assertEqual(fields[5], image_build.EPOCH)
        self.assertEqual(members[1][1][9:11], [5, 1])
        self.assertEqual(members[2][2], b'fixture\0bytes')

    def test_newc_rejects_unsafe_duplicate_reserved_and_parent_alias_entries(self):
        regular = stat.S_IFREG | 0o644
        cases = [[('../init', b'x', regular, 0, 0)],
                 [('/init', b'x', regular, 0, 0)],
                 [('a//b', b'x', regular, 0, 0)],
                 [('TRAILER!!!', b'x', regular, 0, 0)],
                 [('init', b'x', regular, 0, 0)] * 2,
                 [('link', b'/tmp', stat.S_IFLNK | 0o777, 0, 0),
                  ('link/file', b'x', regular, 0, 0)],
                 [('file', b'x', regular, 0, 0), ('file/child', b'y', regular, 0, 0)]]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                image_build.newc(entries)

    def test_newc_rejects_invalid_fixed_width_fields_and_entry_payload_types(self):
        regular = stat.S_IFREG | 0o644
        cases = [('init', b'x', -1, 0, 0), ('init', b'x', 0x100000000 | regular, 0, 0),
                 ('init', b'x', regular, -1, 0), ('init', b'x', regular, 4096, 0),
                 ('init', b'x', regular, True, 0), ('init', b'x', regular, 0, 1.5),
                 ('init', 'not bytes', regular, 0, 0),
                 ('directory', b'not empty', stat.S_IFDIR | 0o755, 0, 0),
                 ('pipe', b'', stat.S_IFIFO | 0o600, 0, 0)]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                image_build.newc([entry])

    def test_destination_must_be_new_and_canonical_under_workspace_build(self):
        fake_root = self.root / 'workspace'
        fake_root.mkdir()
        desired = fake_root / 'build' / 'image-new'
        self.assertEqual(image_build.new_destination(desired, root=fake_root), desired)
        self.assertTrue(desired.parent.is_dir())
        self.assertFalse(desired.exists())
        desired.mkdir()
        nested = desired / 'nested'
        self.assertEqual(image_build.new_destination(nested, root=fake_root), nested)
        existing_file = fake_root / 'build' / 'existing-file'
        existing_file.write_bytes(b'preserve')
        outside = self.root / 'outside'
        for value in [desired, existing_file, fake_root / 'build', outside, fake_root / 'build' / '..' / 'escape',
                      fake_root / 'build' / 'missing' / 'child']:
            with self.subTest(value=value), self.assertRaises((ValueError, FileNotFoundError)):
                image_build.new_destination(value, root=fake_root)
        self.assertFalse(outside.exists())
        self.assertEqual(existing_file.read_bytes(), b'preserve')

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink guard without Windows link privilege')
    def test_destination_and_elf_symlinks_are_not_followed(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        (workspace / 'build').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            image_build.new_destination(workspace / 'build' / 'escape', root=workspace)
        (workspace / 'build').unlink()
        (workspace / 'build').mkdir()
        (workspace / 'build' / 'alias').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            image_build.new_destination(workspace / 'build' / 'alias' / 'escape', root=workspace)
        dangling = workspace / 'build' / 'dangling'
        dangling.symlink_to(self.root / 'nonexistent')
        with self.assertRaises(ValueError):
            image_build.new_destination(dangling, root=workspace)
        target = self.root / 'elf'
        target.write_bytes(_elf())
        linked = self.root / 'linked'
        linked.symlink_to(target)
        with self.assertRaises(ValueError):
            image_build.arm_elf(linked)

    def test_arm_elf_wrong_arch_class_endian_truncation_and_dynamic_input_rejected(self):
        target = self.root / 'elf'
        target.write_bytes(_elf())
        image_build.arm_elf(target)
        image_build.arm_elf(target, static=True)
        for data in [_elf(62), _elf()[:63], b'not an executable',
                     _elf()[:4] + b'\x01' + _elf()[5:], _elf()[:5] + b'\x02' + _elf()[6:]]:
            target.write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                image_build.arm_elf(target)
        target.write_bytes(_elf(interpreter=True))
        with self.assertRaises(ValueError):
            image_build.arm_elf(target, static=True)


class ImageBootGuardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO/nonblocking open guards')
    def test_pinned_refuses_fifo_before_open_and_swap_race_without_blocking(self):
        with tempfile.TemporaryDirectory(prefix='hf-pinned-guard-') as temporary:
            source = Path(temporary) / 'input'
            os.mkfifo(source)
            with mock.patch('tools.image_boot.os.open', side_effect=AssertionError('must not open FIFO')):
                with self.assertRaises(ValueError):
                    image_boot.pinned(source)
            source.unlink()
            source.write_bytes(b'image')
            original_open = os.open
            def replaced(path, flags):
                self.assertTrue(flags & os.O_NONBLOCK)
                source.unlink()
                os.mkfifo(source)
                return original_open(path, flags)
            with mock.patch('tools.image_boot.os.open', side_effect=replaced):
                with self.assertRaises(ValueError):
                    image_boot.pinned(source)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux owned-process cleanup check')
    def test_noisy_tool_is_bounded_and_its_owned_process_reaped(self):
        from tools.image_fs import _run
        with tempfile.TemporaryDirectory(prefix='hf-tool-output-') as temporary:
            pidfile = Path(temporary) / 'pid'
            script = ('import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); '
                      'os.write(1,b"x"*1048576); time.sleep(60)')
            with self.assertRaisesRegex(RuntimeError, 'output exceeded'):
                _run([sys.executable, '-c', script, str(pidfile)], timeout=3)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)

    def test_qemu_command_uses_software_cpu_private_disks_and_pinned_descriptors(self):
        files = {'kernel': '/proc/self/fd/10', 'bootstrap': '/proc/self/fd/11',
                 'root': '/proc/self/fd/12', 'data': '/proc/self/fd/13'}
        for phase in ('first', 'resume'):
            command = image_boot.qemu_command('/usr/bin/qemu-system-aarch64', files, phase)
            self.assertEqual(command[command.index('-accel') + 1], 'tcg')
            self.assertEqual(command[command.index('-nic') + 1], 'none')
            self.assertEqual(command[command.index('-display') + 1], 'none')
            self.assertEqual(command[command.index('-monitor') + 1], 'none')
            self.assertIn('-nodefaults', command)
            self.assertIn('-no-reboot', command)
            self.assertEqual(command[command.index('-kernel') + 1], files['kernel'])
            self.assertEqual(command[command.index('-initrd') + 1], files['bootstrap'])
            drives = [command[index + 1] for index, arg in enumerate(command) if arg == '-drive']
            self.assertEqual(len(drives), 2)
            self.assertIn('file=/proc/self/fd/12', drives[0])
            self.assertIn('readonly=on', drives[0])
            self.assertIn('file=/proc/self/fd/13', drives[1])
            self.assertNotIn('readonly=on', drives[1])
            self.assertIn('HF_IMAGE_PHASE=' + phase, command[command.index('-append') + 1])
            self.assertIn('HASSLEFREE_ARM64_IMAGE=1', command[command.index('-append') + 1])
            for forbidden in ['-enable-kvm', '-netdev', '-net', '-virtfs', '-fsdev', '-usb', '-daemonize']:
                self.assertNotIn(forbidden, command)
            self.assertIn('elevateprivileges=deny', command[command.index('-sandbox') + 1])
        for phase in ('', 'FIRST', 'resume extra=1', None):
            with self.assertRaises(ValueError):
                image_boot.qemu_command('qemu', files, phase)

    def test_ext4_magic_uuid_and_length_are_checked_and_stream_rewound(self):
        import uuid
        header = bytearray(1152)
        header[1080:1082] = b'\x53\xef'
        header[1128:1144] = uuid.UUID(image_build.ROOT_UUID).bytes
        stream = io.BytesIO(header)
        stream.seek(12)
        image_boot.check_ext4(stream, image_build.ROOT_UUID)
        self.assertEqual(stream.tell(), 0)
        for data in [header[:1151], bytes(1152), header[:1128] + uuid.UUID(image_build.DATA_UUID).bytes + header[1144:]]:
            stream = io.BytesIO(data)
            with self.subTest(data=data[-24:]), self.assertRaises(ValueError):
                image_boot.check_ext4(stream, image_build.ROOT_UUID)
            self.assertEqual(stream.tell(), 0)

    def test_serial_requires_complete_unique_structural_markers_for_both_phases(self):
        for phase in ('first', 'resume'):
            valid = _serial(phase)
            self.assertEqual(image_boot.parse_serial(valid, phase), _compact(phase))
            self.assertEqual(image_boot.parse_serial(valid.replace(b'\n', b'\r\n'), phase), _compact(phase))
            markers = [line for line in valid.splitlines() if line.startswith(b'HF_IMAGE_')]
            for marker in markers:
                for bad in [valid.replace(marker + b'\n', b''), valid + marker + b'\n']:
                    with self.subTest(phase=phase, marker=marker, mode='missing_or_duplicate'), self.assertRaises(ValueError):
                        image_boot.parse_serial(bad, phase)
            conflicts = [b'HF_IMAGE_INIT pid=99 machine=aarch64 phase=' + phase.encode() + b' kernel=other',
                         b'HF_IMAGE_ROOT readonly=0 filesystem=ext4 device=vda',
                         b'HF_IMAGE_DATA readwrite=0 filesystem=ext4 device=vdb',
                         b'HF_IMAGE_RUNTIME uid=0 phase=' + phase.encode() + b' python=/usr/bin/python3',
                         b'HF_IMAGE_CHILD phase=' + phase.encode() + b' status=1',
                         b'HF_IMAGE_DATA_UNMOUNTED clean=0',
                         b'HF_IMAGE_RESULT FAIL phase=' + phase.encode(),
                         b'HF_IMAGE_RESULT PASS phase=' + ('resume' if phase == 'first' else 'first').encode() +
                         b' immutable-rootfs separate-data synthetic-only']
            for marker in conflicts:
                with self.subTest(phase=phase, conflict=marker), self.assertRaises(ValueError):
                    image_boot.parse_serial(valid + marker + b'\n', phase)
        with self.assertRaises(ValueError):
            image_boot.parse_serial(_serial('first'), 'resume')
        with self.assertRaises(ValueError):
            image_boot.parse_serial(b'x' * (image_boot.MAX_SERIAL + 1), 'first')
        for data, phase in [('not bytes', 'first'), (_serial(), 'invalid')]:
            with self.assertRaises(ValueError):
                image_boot.parse_serial(data, phase)

    def test_compact_result_requires_exact_identity_types_and_strict_json(self):
        cases = [dict(phase='resume'), dict(passed=False), dict(passed=1),
                 dict(native_process_closed=False), dict(native_process_closed=1),
                 dict(journal_rows=0), dict(journal_rows=2), dict(journal_rows=True),
                 dict(journal_rows=1.0), dict(request_id=RID.upper()), dict(request_id='{' + RID + '}'),
                 dict(request_id='not-a-uuid'), dict(request_id=None), dict(request_id=123),
                 dict(report='../first.json'), dict(report='resume.json'),
                 dict(report_sha256='a' * 63), dict(report_sha256='A' * 64), dict(report_sha256=None)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                image_boot.parse_serial(_serial(compact=_compact(**changes)), 'first')
        marker = b'HF_IMAGE_UI_RESULT '
        good = _serial()
        line = next(line for line in good.splitlines() if line.startswith(marker))
        for malformed in [b'{}', b'[]', b'null', b'{"phase":"first","phase":"first"}',
                          json.dumps(_compact()).encode()[:-1] + b',"extra":NaN}',
                          json.dumps(_compact()).encode()[:-1] + b',"extra":Infinity}',
                          b'x' * 65537]:
            with self.subTest(malformed=malformed[:80]), self.assertRaises(ValueError):
                image_boot.parse_serial(good.replace(line, marker + malformed), 'first')


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() != 0 and
                     shutil.which('aarch64-linux-gnu-gcc') and shutil.which('qemu-aarch64'),
                     'requires existing non-root Linux, static AArch64 compiler and user-mode QEMU')
class ImageInitActualTests(unittest.TestCase):
    def test_static_arm64_init_refuses_forged_environment_before_privileged_operations(self):
        build = image_build.ROOT / 'build'
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='image-init-guard-', dir=build) as temporary:
            output = Path(temporary) / 'init'
            command = [shutil.which('aarch64-linux-gnu-gcc'), '-std=c11', '-O2', '-static',
                       '-Wall', '-Wextra', '-Werror', str(image_build.ROOT / 'system/arm64-image/init.c'),
                       '-o', str(output)]
            subprocess.run(command, check=True, capture_output=True, timeout=60)
            image_build.arm_elf(output, static=True)
            for phase in ('first', 'resume'):
                environment = dict(os.environ, HASSLEFREE_ARM64_IMAGE='1', HF_IMAGE_PHASE=phase)
                result = subprocess.run([shutil.which('qemu-aarch64'), '-strace', str(output)],
                                        env=environment, capture_output=True, timeout=10)
                with self.subTest(phase=phase):
                    self.assertEqual(result.returncode, 2, result.stderr.decode(errors='replace'))
                    self.assertEqual(result.stdout, b'')
                    self.assertIn(b'HF_IMAGE_INIT guest-only: ARM64 PID 1', result.stderr)
                    self.assertNotRegex(result.stderr.decode(errors='replace'),
                                        r'\b(?:mount|finit_module|init_module|chroot|reboot)\(')


if __name__ == '__main__':
    unittest.main()

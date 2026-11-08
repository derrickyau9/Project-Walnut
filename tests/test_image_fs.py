"""Guard tests plus actual non-root, mount-free ext4 image verification."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

from tools.image_fs import create_ext4, extract_file, validate_tar


UUID = '26110500-0000-4000-8000-000000000017'
EPOCH = 1700000000


def _tar(path, members=None):
    if members is None:
        members = [('etc', 'dir', b''), ('etc/probe', 'file', b'mount-free-image\n'),
                   ('usr', 'dir', b''), ('usr/share', 'dir', b''),
                   ('usr/share/data', 'file', b'payload\n'),
                   ('etc/absolute', 'sym', '/usr/share/data'),
                   ('etc/relative', 'sym', '../usr/share/data'),
                   ('etc/hard', 'hard', 'usr/share/data')]
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, kind, value in members:
            item = tarfile.TarInfo(name)
            item.uid = item.gid = 0
            item.mode = 0o755 if kind == 'dir' else 0o644
            item.mtime = EPOCH
            item.type = {'dir': tarfile.DIRTYPE, 'file': tarfile.REGTYPE,
                         'sym': tarfile.SYMTYPE, 'hard': tarfile.LNKTYPE,
                         'char': tarfile.CHRTYPE, 'fifo': tarfile.FIFOTYPE}[kind]
            if kind == 'file':
                item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
            else:
                if kind in ('sym', 'hard'):
                    item.linkname = value
                archive.addfile(item)
    return path


class ImageFsGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-image-guard-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = _tar(self.root / 'rootfs.tar')

    def create(self, destination=None, **changes):
        options = {'size_mib': 32, 'label': 'hf-image-test', 'uuid': UUID, 'epoch': EPOCH}
        options.update(changes)
        return create_ext4(self.archive, destination or self.root / 'image.ext4', **options)

    def test_valid_guest_absolute_relative_and_hard_links_are_not_host_followed(self):
        result = validate_tar(self.archive)
        self.assertEqual(result['entries'], 8)
        self.assertEqual(result['regular_bytes'], len(b'mount-free-image\n') + len(b'payload\n'))
        self.assertEqual(result['sha256'], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(set(self.root.iterdir()), {self.archive})

    def test_rejects_traversal_duplicates_devices_and_link_parent_paths(self):
        cases = [
            [('/absolute', 'file', b'x')],
            [('../escape', 'file', b'x')],
            [('safe/../escape', 'file', b'x')],
            [('duplicate', 'file', b'a'), ('duplicate', 'file', b'b')],
            [('device', 'char', b'')],
            [('pipe', 'fifo', b'')],
            [('etc/escape', 'sym', '../../outside')],
            [('link', 'sym', '/usr'), ('link/data', 'file', b'x')],
            [('missing', 'hard', 'absent')],
            [('one', 'hard', 'two'), ('two', 'hard', 'one')],
            [('link', 'sym', '/usr/data'), ('hard', 'hard', 'link')],
            [('bad\\windows', 'file', b'x')],
        ]
        for index, members in enumerate(cases):
            path = _tar(self.root / ('unsafe-' + str(index) + '.tar'), members)
            with self.subTest(members=members), self.assertRaises(ValueError):
                validate_tar(path)

    def test_tar_size_and_regular_input_guards_precede_image_creation(self):
        with self.assertRaises(ValueError):
            validate_tar(self.archive, max_bytes=1)
        with self.assertRaises(ValueError):
            validate_tar(self.root)
        malformed = self.root / 'malformed.tar'
        malformed.write_bytes(b'not a tar file')
        with self.assertRaises(ValueError):
            validate_tar(malformed)
        self.assertFalse((self.root / 'image.ext4').exists())

    def test_argument_guards_do_not_create_configuration_or_image(self):
        cases = [{'size_mib': value} for value in (True, 31, 4097, 32.0, '32')]
        cases += [{'label': value} for value in ('', 'x' * 17, 'has space', '\u76f8\u673a')]
        cases += [{'uuid': value} for value in ('not-a-uuid', UUID.upper(), 17)]
        cases += [{'epoch': value} for value in (True, -1, 0x100000000, 1.5)]
        # This UUID contains no letters, so use an explicitly noncanonical form.
        cases = [case for case in cases if case != {'uuid': UUID.upper()}]
        cases.append({'uuid': UUID.replace('-', '')})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)
        self.assertEqual(set(self.root.iterdir()), {self.archive})

    def test_existing_file_directory_and_configuration_are_never_overwritten(self):
        destination = self.root / 'existing.ext4'
        destination.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.create(destination)
        self.assertEqual(destination.read_bytes(), b'preserve')
        with self.assertRaises(ValueError):
            self.create(self.root)
        config = self.root / 'image.ext4.mke2fs.conf'
        config.write_bytes(b'external configuration')
        with mock.patch('tools.image_fs._linux'), mock.patch('tools.image_fs._tool', return_value='tool'), \
                mock.patch('tools.image_fs._run', return_value={'returncode': 0}):
            with self.assertRaises(FileExistsError):
                self.create()
        self.assertEqual(config.read_bytes(), b'external configuration')
        self.assertFalse((self.root / 'image.ext4').exists())

    def test_failed_tools_preserve_only_new_diagnostic_files_without_success(self):
        for phase in ('mke2fs', 'e2fsck'):
            with self.subTest(phase=phase):
                destination = self.root / (phase + '.ext4')
                def run(command, **kwargs):
                    failing = (phase == 'mke2fs' and '-d' in command or phase == 'e2fsck' and '-n' in command)
                    return {'command': command, 'returncode': 4 if failing else 0, 'stdout': '', 'stderr': 'injected'}
                with mock.patch('tools.image_fs._linux'), mock.patch('tools.image_fs._tool', side_effect=lambda name: name), \
                        mock.patch('tools.image_fs._run', side_effect=run):
                    with self.assertRaises(RuntimeError):
                        self.create(destination)
                self.assertTrue(destination.is_file())
                self.assertEqual(destination.stat().st_size, 32 * 1024**2)
                self.assertTrue(destination.with_name(destination.name + '.mke2fs.conf').is_file())
                with self.assertRaises(ValueError):
                    self.create(destination)

    def test_extract_rejects_command_injection_traversal_and_invalid_bounds(self):
        destination = self.root / 'extracted'
        for guest in ('relative', '/', '/etc/../escape', '/etc/./probe', '//etc/probe',
                      '/etc/space name', '/etc/probe;quit', '/etc/probe\nquit', '/etc/probe"'):
            with self.subTest(guest=guest), self.assertRaises(ValueError):
                extract_file(self.archive, guest, destination)
        for maximum in (True, 0, -1, 64 * 1024**2 + 1):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                extract_file(self.archive, '/etc/probe', destination, max_bytes=maximum)
        self.assertFalse(destination.exists())


def _actual_tools_available():
    return (sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() != 0 and
            all(shutil.which(name) or (Path('/usr/sbin') / name).is_file()
                for name in ('mke2fs', 'e2fsck', 'debugfs')))


@unittest.skipUnless(_actual_tools_available(), 'actual image checks require non-root Linux and existing e2fsprogs')
class ActualImageFsTests(unittest.TestCase):
    def test_byte_identical_images_readonly_fsck_and_bounded_extraction(self):
        with tempfile.TemporaryDirectory(prefix='hf-image-actual-') as directory:
            root = Path(directory)
            archive = _tar(root / 'rootfs.tar')
            images = []
            for index in (1, 2):
                image = root / ('image-' + str(index) + '.ext4')
                report = create_ext4(archive, image, size_mib=32, label='hf-image-test', uuid=UUID, epoch=EPOCH)
                self.assertEqual(report['e2fsck']['returncode'], 0)
                self.assertEqual(report['size_bytes'], 32 * 1024**2)
                self.assertFalse(report['mounted'])
                self.assertNotEqual(report['non_root_uid'], 0)
                images.append(report)
                time.sleep(1.05)
            self.assertEqual(images[0]['sha256'], images[1]['sha256'])
            self.assertEqual(images[0]['config_sha256'], images[1]['config_sha256'])
            output = root / 'probe.txt'
            report = extract_file(root / 'image-1.ext4', '/etc/probe', output)
            self.assertEqual(output.read_bytes(), b'mount-free-image\n')
            self.assertEqual(report['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertFalse(report['mounted'])
            with self.assertRaises(ValueError):
                extract_file(root / 'image-1.ext4', '/etc/probe', root / 'too-large', max_bytes=1)
            self.assertFalse((root / 'too-large').exists())
            with self.assertRaises(ValueError):
                extract_file(root / 'image-1.ext4', '/etc/probe', output)
            with self.assertRaises(ValueError):
                extract_file(root / 'image-1.ext4', '/etc/absolute', root / 'must-not-follow')
            self.assertEqual(hashlib.sha256((root / 'image-1.ext4').read_bytes()).hexdigest(), images[0]['sha256'])

    def test_host_symlinks_and_character_devices_are_refused_before_tools(self):
        with tempfile.TemporaryDirectory(prefix='hf-image-host-guard-') as directory:
            root = Path(directory)
            archive = _tar(root / 'rootfs.tar')
            alias = root / 'alias.tar'
            alias.symlink_to(archive)
            with self.assertRaises(ValueError):
                validate_tar(alias)
            with self.assertRaises(ValueError):
                validate_tar('/dev/null')
            destination = root / 'image.ext4'
            destination.symlink_to(root / 'outside')
            with self.assertRaises(ValueError):
                create_ext4(archive, destination, size_mib=32, label='guard', uuid=UUID)
            self.assertFalse((root / 'outside').exists())


if __name__ == '__main__':
    unittest.main()

"""ISO structures and independent libarchive checks for NoCloud seed media."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.freebsd_seed import MAX_FILE_BYTES, SECTOR_BYTES, create_seed


def _number(raw, offset, width):
    little = int.from_bytes(raw[offset:offset + width], 'little')
    big = int.from_bytes(raw[offset + width:offset + 2 * width], 'big')
    if little != big:
        raise AssertionError('ISO both-endian integer disagrees')
    return little


def _directory(raw):
    primary = raw[16 * SECTOR_BYTES:17 * SECTOR_BYTES]
    root = primary[156:190]
    start = _number(root, 2, 4) * SECTOR_BYTES
    stop = start + _number(root, 10, 4)
    records = []
    position = start
    while position < stop and raw[position]:
        size = raw[position]
        record = raw[position:position + size]
        if size < 34 or size % 2 or len(record) != size:
            raise AssertionError('invalid directory record size')
        identifier = record[33:33 + record[32]]
        offset = 33 + record[32] + (not record[32] % 2)
        system = {}
        while offset + 4 <= len(record) and record[offset]:
            signature, length, version = record[offset:offset + 2], record[offset + 2], record[offset + 3]
            if length < 4 or offset + length > len(record) or version != 1 or signature in system:
                raise AssertionError('invalid system use record')
            system[signature] = record[offset + 4:offset + length]
            offset += length
            if signature == b'ST':
                break
        records.append((identifier, record, system))
        position += size
    return records


class FreeBsdSeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-seed-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.destination = self.root / 'seed.iso'
        self.user = b'#cloud-config\nwrite_files: []\n'
        self.meta = b'instance-id: hf-seed-test\nlocal-hostname: hf-freebsd\n'

    def create(self, destination=None, **changes):
        options = {'user_data': self.user, 'meta_data': self.meta}
        options.update(changes)
        return create_seed(destination or self.destination, **options)

    def test_primary_descriptor_path_tables_rrip_names_and_exact_payloads(self):
        result = self.create()
        raw = self.destination.read_bytes()
        self.assertEqual(result['format'], 'hf-nocloud-iso9660-v1')
        self.assertEqual(result['volume_id'], 'CIDATA')
        self.assertEqual(result['size_bytes'], len(raw))
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(raw[:16 * SECTOR_BYTES], bytes(16 * SECTOR_BYTES))
        self.assertEqual(len(raw) % SECTOR_BYTES, 0)
        primary = raw[16 * SECTOR_BYTES:17 * SECTOR_BYTES]
        self.assertEqual(primary[:7], b'\x01CD001\x01')
        self.assertEqual(primary[40:72].rstrip(), b'CIDATA')
        self.assertEqual(_number(primary, 80, 4), len(raw) // SECTOR_BYTES)
        self.assertEqual(_number(primary, 120, 2), 1)
        self.assertEqual(_number(primary, 124, 2), 1)
        self.assertEqual(_number(primary, 128, 2), SECTOR_BYTES)
        self.assertEqual(_number(primary, 132, 4), 10)
        self.assertEqual(primary[881], 1)
        self.assertEqual(raw[17 * SECTOR_BYTES:17 * SECTOR_BYTES + 7], b'\xffCD001\x01')
        for offset, order in ((140, '<'), (148, '>')):
            table_sector = struct.unpack_from(order + 'I', primary, offset)[0]
            table = raw[table_sector * SECTOR_BYTES:table_sector * SECTOR_BYTES + 10]
            self.assertEqual(table[:2], b'\x01\x00')
            self.assertEqual(struct.unpack_from(order + 'I', table, 2)[0], 20)
            self.assertEqual(struct.unpack_from(order + 'H', table, 6)[0], 1)
            self.assertEqual(table[8:], b'\x00\x00')
        records = _directory(raw)
        self.assertEqual([entry[0] for entry in records], [b'\x00', b'\x01', b'META_DAT.;1', b'USER_DAT.;1'])
        self.assertEqual(records[0][2][b'SP'], b'\xbe\xef\x00')
        self.assertEqual(records[0][2][b'ER'], b'\x0a\x00\x00\x01RRIP_1991A')
        for _, record, system in records:
            self.assertEqual(_number(record, 28, 2), 1)
            self.assertEqual(_number(system[b'PX'], 16, 4), 0)
            self.assertEqual(_number(system[b'PX'], 24, 4), 0)
            self.assertIn(b'TF', system)
        for _, record, system in records[2:]:
            name = system[b'NM'][1:].decode('ascii')
            self.assertEqual(system[b'NM'][0], 0)
            start = _number(record, 2, 4) * SECTOR_BYTES
            length = _number(record, 10, 4)
            expected = {'meta-data': self.meta, 'user-data': self.user}[name]
            self.assertEqual(raw[start:start + length], expected)
            self.assertEqual(result['files'][name], {'size_bytes': len(expected), 'sha256': hashlib.sha256(expected).hexdigest()})
            self.assertEqual(_number(system[b'PX'], 0, 4), stat.S_IFREG | 0o444)

    def test_deterministic_committed_iso_bytes_and_zero_length_files(self):
        self.create()
        second = self.root / 'second.iso'
        self.create(second)
        self.assertEqual(self.destination.read_bytes(), second.read_bytes())
        # This byte commitment catches accidental layout changes, not guest
        # compatibility; the libarchive test below is the independent decoder.
        self.assertEqual(hashlib.sha256(second.read_bytes()).hexdigest(),
                         '6e9e3e35def81878d60e627a19410c799b02ce9293b7810b50ecfa4701bbb2f4')
        empty = self.root / 'empty.iso'
        result = self.create(empty, user_data=b'', meta_data=b'')
        records = _directory(empty.read_bytes())
        self.assertEqual([_number(record, 10, 4) for _, record, _ in records[2:]], [0, 0])
        self.assertEqual(result['files']['user-data']['sha256'], hashlib.sha256(b'').hexdigest())

    def test_payload_bounds_and_types_rejected_before_destination_creation(self):
        for value in (None, 'text', bytearray(b'bytes'), memoryview(b'bytes'), b'x' * (MAX_FILE_BYTES + 1)):
            for key in ('user_data', 'meta_data'):
                with self.subTest(key=key, value_type=type(value)), self.assertRaises(ValueError):
                    self.create(**{key: value})
                self.assertFalse(self.destination.exists())
        result = self.create(user_data=b'a' * MAX_FILE_BYTES, meta_data=b'b' * MAX_FILE_BYTES)
        self.assertEqual(result['files']['user-data']['size_bytes'], MAX_FILE_BYTES)
        self.assertLessEqual(result['size_bytes'], 2 * MAX_FILE_BYTES + 64 * 1024)

    def test_existing_file_directory_and_missing_parent_are_preserved(self):
        self.destination.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.destination.read_bytes(), b'preserve')
        with self.assertRaises(ValueError):
            self.create(self.root)
        with self.assertRaises(FileNotFoundError):
            self.create(self.root / 'missing' / 'seed.iso')
        self.assertFalse((self.root / 'missing').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX unprivileged symlink/FIFO guards')
    def test_symlink_parents_leaves_dangling_links_and_fifo_refused(self):
        parent = self.root / 'alias'
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.create(parent / 'escape.iso')
        target = self.root / 'target'
        target.write_bytes(b'keep')
        self.destination.symlink_to(target)
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(target.read_bytes(), b'keep')
        self.destination.unlink()
        self.destination.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            self.create()
        self.destination.unlink()
        os.mkfifo(self.destination)
        with self.assertRaises(ValueError):
            self.create()

    @unittest.skipUnless(os.name == 'posix', 'POSIX seed privacy and parent descriptor checks')
    def test_private_output_permissions_and_pinned_parent_identity(self):
        self.create()
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        real_fstat = os.fstat
        calls = []
        def changed(descriptor):
            info = real_fstat(descriptor)
            calls.append(descriptor)
            if len(calls) == 1:
                values = list(info)
                values[1] += 1
                return os.stat_result(values)
            return info
        with mock.patch('tools.freebsd_seed.os.fstat', side_effect=changed), self.assertRaises(ValueError):
            self.create(self.root / 'changed.iso')
        self.assertFalse((self.root / 'changed.iso').exists())

    def test_failed_durable_write_never_reports_success_and_retains_new_output(self):
        with mock.patch('tools.freebsd_seed.os.fsync', side_effect=OSError('injected sync failure')):
            with self.assertRaises(OSError):
                self.create()
        self.assertTrue(self.destination.is_file())
        with self.assertRaises(ValueError):
            self.create()


@unittest.skipUnless(shutil.which('bsdtar'), 'independent libarchive bsdtar reader unavailable')
class IndependentSeedReaderTests(unittest.TestCase):
    def test_libarchive_reads_exact_rock_ridge_names_and_binary_bytes(self):
        with tempfile.TemporaryDirectory(prefix='hf-seed-libarchive-') as directory:
            path = Path(directory).resolve() / 'seed.iso'
            values = {'user-data': b'#cloud-config\n\x00\xff\n' + b'u' * 7000,
                      'meta-data': b'instance-id: hf-seed-independent\n'}
            create_seed(path, user_data=values['user-data'], meta_data=values['meta-data'])
            listed = subprocess.run([shutil.which('bsdtar'), '-tf', str(path)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True)
            names = {name.rstrip('/') for name in listed.stdout.decode('utf-8').splitlines()}
            self.assertEqual(names, {'.', 'user-data', 'meta-data'})
            for name, expected in values.items():
                extracted = subprocess.run([shutil.which('bsdtar'), '-xOf', str(path), name],
                                           stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True)
                self.assertEqual(extracted.stdout, expected)


if __name__ == '__main__':
    unittest.main()

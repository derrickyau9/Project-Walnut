"""Offline ARM image guards; actual DaOS guest boots are separate evidence."""
from __future__ import annotations

from contextlib import nullcontext
import copy
import hashlib
import importlib.util
import io
import os
import json
from pathlib import Path, PurePosixPath
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

from tools import freebsd_image_boot as image


_SECTOR = 512
_DISK_BYTES = 32 * 1024**2
_SECTORS = _DISK_BYTES // _SECTOR
_EFI = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b').bytes_le
_UFS = uuid.UUID('516e7cb6-6ecf-11d6-8ff8-00022d09712b').bytes_le


def _entry(kind, start, end, ident, label=''):
    value = bytearray(128)
    value[:16] = kind
    value[16:32] = uuid.UUID(ident).bytes_le
    struct.pack_into('<QQQ', value, 32, start, end, 0)
    encoded_label = label.encode('utf-16-le')
    if len(encoded_label) > 72:
        raise ValueError('test partition label exceeds GPT field')
    value[56:56 + len(encoded_label)] = encoded_label
    return bytes(value)


def _table():
    return (_entry(_EFI, 2048, 4095, 'c915308a-b360-4aa9-936c-13149d9b6e65') +
            _entry(_UFS, 4096, _SECTORS - 34, '6bc2951b-071e-4a9e-8e24-9190b8e991ae') + bytes(126 * 128))


def _appliance_table():
    specs = ((_EFI, 2048, 4095, 'DaOS-efi'), (_UFS, 4096, 16383, 'DaOS-root'),
             (_UFS, 16384, 24575, 'DaOS-state'), (_UFS, 24576, 32767, 'DaOS-calibration'),
             (_UFS, 32768, _SECTORS - 34, 'DaOS-photos'))
    entries = [_entry(kind, start, end, str(uuid.UUID(int=0x100 + index)), label)
               for index, (kind, start, end, label) in enumerate(specs)]
    return b''.join(entries) + bytes((128 - len(entries)) * 128)


def _label(table, index, name):
    encoded = name.encode('utf-16-le')
    value = bytearray(table)
    value[index * 128 + 56:index * 128 + 128] = encoded.ljust(72, b'\x00')
    return bytes(value)


def _header(table, *, backup=False, **changes):
    values = {'current': _SECTORS - 1 if backup else 1, 'other': 1 if backup else _SECTORS - 1,
              'first': 34, 'last': _SECTORS - 34, 'array_lba': _SECTORS - 33 if backup else 2,
              'count': 128, 'entry_size': 128}
    values.update(changes)
    value = bytearray(_SECTOR)
    value[:8] = b'EFI PART'
    struct.pack_into('<IIII', value, 8, 0x00010000, 92, 0, 0)
    struct.pack_into('<QQQQ', value, 24, values['current'], values['other'], values['first'], values['last'])
    value[56:72] = uuid.UUID('0c79838b-2eec-4794-ad51-0a2ab05fbd38').bytes_le
    struct.pack_into('<QIII', value, 72, values['array_lba'], values['count'], values['entry_size'], zlib.crc32(table))
    struct.pack_into('<I', value, 16, zlib.crc32(value[:92]))
    return bytes(value)


def _disk(*, table=None, header_changes=None, path=None):
    """Sparse real stream with valid primary+backup GPT; no filesystem or VM."""
    table = _table() if table is None else table
    stream = tempfile.TemporaryFile('w+b') if path is None else Path(path).open('x+b')
    stream.truncate(_DISK_BYTES)
    protective = bytearray(_SECTOR)
    protective[447:450] = b'\x00\x02\x00'
    protective[450] = 0xee
    protective[451:454] = b'\xff\xff\xff'
    struct.pack_into('<II', protective, 454, 1, _SECTORS - 1)
    protective[510:] = b'\x55\xaa'
    for sector, payload in ((0, protective), (1, _header(table, **(header_changes or {}))), (2, table),
                            (_SECTORS - 33, table), (_SECTORS - 1, _header(table, backup=True))):
        stream.seek(sector * _SECTOR)
        stream.write(payload)
    stream.flush()
    stream.seek(0)
    return stream


def _change(stream, offset, payload):
    stream.seek(offset)
    stream.write(payload)
    stream.flush()
    stream.seek(0)


class FreeBsdRawGptTests(unittest.TestCase):
    def test_real_sparse_protective_mbr_primary_and_backup_gpt_is_accepted(self):
        with _disk() as stream:
            value = image.validate_raw(stream)
        self.assertIsInstance(value, dict)
        self.assertTrue(value)

    def test_truncated_nonraw_mbr_and_header_crc_mutations_are_refused(self):
        for raw in (b'', b'QFI\xfb' + bytes(508), bytes(1024)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                image.validate_raw(io.BytesIO(raw))
        for offset, payload in ((510, b'\x00\x00'), (450, b'\x83'), (_SECTOR, b'NOT GPT!'),
                                (_SECTOR + 16, bytes(4)), (_SECTOR + 56, b'\xff'),
                                ((_SECTORS - 1) * _SECTOR + 56, b'\xff')):
            with self.subTest(offset=offset), _disk() as stream:
                _change(stream, offset, payload)
                with self.assertRaises(ValueError):
                    image.validate_raw(stream)

    def test_partition_array_crc_corruption_is_refused(self):
        for table in (2, _SECTORS - 33):
            with self.subTest(table=table), _disk() as stream:
                _change(stream, table * _SECTOR + 17, b'\xff')
                with self.assertRaises(ValueError):
                    image.validate_raw(stream)

    def test_recomputed_crc_does_not_hide_overlap_or_out_of_bounds_partitions(self):
        for start, end in ((2048, 5000), (20, 4095), (4096, _SECTORS - 1), (5000, 4096)):
            table = bytearray(_table())
            struct.pack_into('<QQ', table, 128 + 32, start, end)
            with self.subTest(start=start, end=end), _disk(table=bytes(table)) as stream:
                with self.assertRaises(ValueError):
                    image.validate_raw(stream)

    def test_missing_efi_or_ufs_type_is_refused_even_with_valid_crcs(self):
        for offset in (0, 128):
            table = bytearray(_table())
            table[offset:offset + 16] = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
            with self.subTest(offset=offset), _disk(table=bytes(table)) as stream:
                with self.assertRaises(ValueError):
                    image.validate_raw(stream)

    def test_recomputed_header_crc_does_not_hide_invalid_disk_or_array_bounds(self):
        for changes in ({'current': 2}, {'other': _SECTORS}, {'first': 0}, {'last': _SECTORS},
                        {'array_lba': _SECTORS + 1}, {'count': 0xffffffff}, {'entry_size': 64}):
            with self.subTest(changes=changes), _disk(header_changes=changes) as stream:
                with self.assertRaises(ValueError):
                    image.validate_raw(stream)

    @unittest.skipUnless(os.name == 'posix', 'POSIX sparse-file upper-bound fixture without large allocation')
    def test_oversize_sparse_raw_image_is_refused(self):
        with _disk() as stream:
            stream.truncate(8 * 1024**3 + _SECTOR)
            stream.seek(0)
            with self.assertRaises(ValueError):
                image.validate_raw(stream)


class FreeBsdApplianceGptTests(unittest.TestCase):
    def test_five_partition_appliance_validates_exact_labels_and_types(self):
        expected = {'DaOS-efi': str(uuid.UUID(bytes_le=_EFI)),
                    **{name: str(uuid.UUID(bytes_le=_UFS)) for name in
                       ('DaOS-root', 'DaOS-state', 'DaOS-calibration', 'DaOS-photos')}}
        with _disk(table=_appliance_table()) as stream:
            generic = image.validate_raw(stream)
            strict = image.validate_raw(stream, appliance=True)
        self.assertFalse(generic['appliance_layout_verified'])
        self.assertTrue(strict['appliance_layout_verified'])
        self.assertEqual({entry['label']: entry['type_guid'] for entry in strict['partitions']}, expected)
        self.assertEqual(len(strict['partitions']), 5)
        self.assertTrue(strict['gpt_primary_crc_verified'])
        self.assertTrue(strict['gpt_backup_crc_verified'])
        self.assertFalse(strict['filesystem_payloads_parsed'])

    def test_generic_one_ufs_remains_supported_but_cannot_claim_appliance_layout(self):
        with _disk() as stream:
            self.assertFalse(image.validate_raw(stream)['appliance_layout_verified'])
            with self.assertRaisesRegex(ValueError, 'five named'):
                image.validate_raw(stream, appliance=True)
        for mode in (None, 0, 1, 'true'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'boolean'):
                image.validate_raw(io.BytesIO(b''), appliance=mode)

    def test_strict_layout_refuses_valid_crc_wrong_roles_duplicate_missing_and_extra_partitions(self):
        valid = _appliance_table()
        wrong_type = bytearray(valid)
        wrong_type[2 * 128:2 * 128 + 16] = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
        swapped = bytearray(valid)
        swapped[:16], swapped[128:144] = _UFS, _EFI
        missing = bytearray(valid)
        missing[4 * 128:5 * 128] = bytes(128)
        extra = bytearray(valid)
        extra[5 * 128:6 * 128] = _entry(uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le,
                                      34, 1023, str(uuid.UUID(int=0x200)), 'DaOS-extra')
        cases = {'wrong-case': _label(valid, 1, 'daos-root'), 'trailing-space': _label(valid, 1, 'DaOS-root '),
                 'unknown-role': _label(valid, 2, 'DaOS-config'), 'duplicate-label': _label(valid, 2, 'DaOS-root'),
                 'empty-label': _label(valid, 3, ''), 'wrong-type': bytes(wrong_type),
                 'swapped-efi-ufs-roles': bytes(swapped), 'missing-partition': bytes(missing),
                 'extra-partition': bytes(extra)}
        for name, table in cases.items():
            # Every case has matching primary/backup tables and recomputed
            # array+header CRCs. Generic GPT admission must succeed first.
            with self.subTest(name=name), _disk(table=table) as stream:
                self.assertTrue(image.validate_raw(stream)['gpt_backup_crc_verified'])
                with self.assertRaisesRegex(ValueError, 'five named'):
                    image.validate_raw(stream, appliance=True)

    def test_invalid_utf16_or_nonzero_text_after_nul_is_not_hidden_by_valid_crcs(self):
        for encoded in (b'\x00\xd8' + bytes(70), b'\x00\xdc' + bytes(70),
                        'DaOS-root\0hidden'.encode('utf-16-le').ljust(72, b'\x00')):
            table = bytearray(_appliance_table())
            table[128 + 56:128 + 128] = encoded
            for mode in (False, True):
                with self.subTest(encoded=encoded[:24], appliance=mode), _disk(table=bytes(table)) as stream:
                    with self.assertRaisesRegex(ValueError, 'UTF-16LE|terminator'):
                        image.validate_raw(stream, appliance=mode)

    def test_prepare_requires_appliance_contract_before_qemu_tools_or_new_directory(self):
        with tempfile.TemporaryDirectory(prefix='hf-legacy-image-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'legacy.raw'
            with _disk(path=source) as stream:
                actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            destination = directory / 'must-not-create'
            with mock.patch.object(image.vm, '_linux'), \
                    mock.patch.object(image.os, 'geteuid', return_value=source.stat().st_uid, create=True), \
                    mock.patch.object(image.vm, '_tool') as tool:
                with self.assertRaisesRegex(ValueError, 'five named'):
                    image.prepare(source, actual_hash, destination, provenance=directory / 'unused-receipt.json')
                tool.assert_not_called()
            self.assertFalse(destination.exists())


class FreeBsdPpmPngTests(unittest.TestCase):
    def test_exact_rgb_bytes_including_header_like_whitespace_pixels_are_preserved(self):
        pixels = b'\x0a\x20\x23\x00\xff\x80'
        raw = b'P6\n2 1\n255\n' + pixels
        self.assertEqual(image.parse_ppm(raw), (2, 1, pixels))

    def test_wrong_format_dimensions_maxval_truncation_and_extra_bytes_are_refused(self):
        invalid = (b'', b'P3\n1 1\n255\n0 0 0\n', b'P6\n0 1\n255\n', b'P6\n4097 1\n255\n',
                   b'P6\n1 -1\n255\n', b'P6\n1 1\n65535\n' + bytes(6),
                   b'P6\n1 1\n0\n' + bytes(3), b'P6\n1 1\n255\n' + bytes(2),
                   b'P6\n1 1\n255\n' + bytes(4), b'P6\n' + b'9' * 10000 + b' 1\n255\n')
        for raw in invalid:
            with self.subTest(prefix=raw[:30]), self.assertRaises(ValueError):
                image.parse_ppm(raw)

    def test_png_chunks_crcs_dimensions_and_exact_deflated_scanlines(self):
        pixels = bytes(range(18))
        raw = b'P6\n3 2\n255\n' + pixels
        encoded = image.ppm_to_png(raw)
        self.assertEqual(encoded[:8], b'\x89PNG\r\n\x1a\n')
        position = 8
        chunks = []
        while position < len(encoded):
            length, = struct.unpack_from('>I', encoded, position)
            kind = encoded[position + 4:position + 8]
            payload = encoded[position + 8:position + 8 + length]
            crc, = struct.unpack_from('>I', encoded, position + 8 + length)
            self.assertEqual(crc, zlib.crc32(kind + payload))
            chunks.append((kind, payload))
            position += 12 + length
        self.assertEqual(position, len(encoded))
        self.assertEqual(chunks[0], (b'IHDR', struct.pack('>IIBBBBB', 3, 2, 8, 2, 0, 0, 0)))
        self.assertEqual(chunks[-1], (b'IEND', b''))
        compressed = b''.join(payload for kind, payload in chunks if kind == b'IDAT')
        self.assertEqual(zlib.decompress(compressed), b'\x00' + pixels[:9] + b'\x00' + pixels[9:])

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'independent Pillow decoder unavailable')
    def test_pillow_independently_decodes_generated_png_to_original_rgb_pixels(self):
        from PIL import Image
        pixels = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 23, 10, 32))
        encoded = image.ppm_to_png(b'P6\n2 2\n255\n' + pixels)
        with Image.open(io.BytesIO(encoded)) as decoded:
            decoded.load()
            self.assertEqual(decoded.format, 'PNG')
            self.assertEqual(decoded.size, (2, 2))
            self.assertEqual(decoded.mode, 'RGB')
            self.assertEqual(decoded.tobytes(), pixels)


class FreeBsdImageCommandTests(unittest.TestCase):
    def test_image_command_has_no_network_ssh_seed_host_shares_or_external_display(self):
        directory = PurePosixPath('/home/task/private-image')
        with mock.patch.object(image, '_metadata', return_value=(directory, {})), \
                mock.patch.object(image.vm, '_tool', return_value='/usr/bin/qemu-system-aarch64'):
            command = image.qemu_command(directory)
        self.assertEqual(command[0], '/usr/bin/qemu-system-aarch64')
        self.assertEqual(command[command.index('-cpu') + 1], 'cortex-a72')
        self.assertEqual(command[command.index('-smp') + 1], '4')
        self.assertEqual(command[command.index('-m') + 1], '4096')
        self.assertEqual(command[command.index('-display') + 1], 'none')
        self.assertEqual(command[command.index('-nic') + 1], 'none')
        self.assertEqual(command[command.index('-accel') + 1].split(',')[0], 'tcg')
        text = ' '.join(command)
        for forbidden in ('-netdev', '-virtfs', '-fsdev', '-daemonize', '-vnc', '-spice', '-smb', '-cdrom',
                          'seed.iso', 'hostfwd', '127.0.0.1:', '0.0.0.0:', 'kvm', '/usr/share/'):
            self.assertNotIn(forbidden, text)
        drives = [command[index + 1] for index, value in enumerate(command) if value == '-drive']
        firmware = [drive for drive in drives if 'if=pflash' in drive]
        self.assertEqual(len(firmware), 2)
        self.assertEqual(sum('readonly=on' in drive for drive in firmware), 1)
        self.assertTrue(all('format=raw' in drive and '/home/task/private-image/' in drive for drive in firmware))
        self.assertTrue(any('format=qcow2' in drive for drive in drives))
        self.assertFalse(any('media=cdrom' in drive for drive in drives))
        devices = [command[index + 1] for index, value in enumerate(command) if value == '-device']
        objects = [command[index + 1] for index, value in enumerate(command) if value == '-object']
        self.assertEqual([value for value in objects if value.startswith('rng-random')],
                         ['rng-random,filename=/dev/urandom,id=DaOS-rng0'])
        self.assertEqual([value for value in devices if value.startswith('virtio-rng-pci')],
                         ['virtio-rng-pci,rng=DaOS-rng0'])
        self.assertEqual([value for value in devices if value.split(',')[0] == 'ramfb'], ['ramfb'])
        self.assertFalse(any('virtio-gpu' in value for value in devices))
        self.assertFalse(any('xres=' in value or 'yres=' in value for value in devices))
        for prefix in ('qemu-xhci', 'usb-kbd', 'usb-tablet'):
            self.assertTrue(any(device.startswith(prefix) for device in devices), prefix)


class FreeBsdImageClickTests(unittest.TestCase):
    def assert_single_press_and_release(self, rpc):
        self.assertEqual(rpc.call_count, 2)
        buttons = []
        for call in rpc.call_args_list:
            self.assertEqual(call.args[:2], ('/unused', 'input-send-event'))
            buttons.extend(event['data'] for event in call.args[2]['events'] if event['type'] == 'btn')
        self.assertEqual(buttons, [{'down': True, 'button': 'left'}, {'down': False, 'button': 'left'}])
        self.assertEqual(rpc.call_args_list[1].args[2]['events'],
                         [{'type': 'btn', 'data': {'down': False, 'button': 'left'}}])

    def test_click_moves_and_presses_then_holds_before_one_release(self):
        timeline = []
        released = {'release': 'acknowledged'}
        def send(work_dir, operation, arguments):
            button = next(event for event in arguments['events'] if event['type'] == 'btn')
            timeline.append('press' if button['data']['down'] else 'release')
            return None if button['data']['down'] else released
        def hold(seconds):
            timeline.append(('hold', seconds))
        with mock.patch.object(image, 'qmp', side_effect=send) as rpc, \
                mock.patch.object(image.time, 'sleep', side_effect=hold) as sleep:
            self.assertIs(image.click('/unused', 20, 30, 1280, 800), released)
        self.assertEqual(timeline, ['press', ('hold', .15), 'release'])
        sleep.assert_called_once_with(.15)
        self.assert_single_press_and_release(rpc)
        events = rpc.call_args_list[0].args[2]['events']
        self.assertEqual([event['type'] for event in events], ['abs', 'abs', 'btn'])
        self.assertEqual([event['data']['axis'] for event in events[:2]], ['x', 'y'])
        self.assertTrue(all(0 <= event['data']['value'] <= 32767 for event in events[:2]))

    def test_uncertain_press_failure_attempts_one_release_without_retrying_press(self):
        for cleanup in (None, RuntimeError('release also failed')):
            original = RuntimeError('press result uncertain')
            with self.subTest(cleanup=cleanup), \
                    mock.patch.object(image, 'qmp', side_effect=[original, cleanup]) as rpc, \
                    mock.patch.object(image.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError) as raised:
                    image.click('/unused', 20, 30, 1280, 800)
                self.assertIs(raised.exception, original)
                sleep.assert_not_called()
                self.assert_single_press_and_release(rpc)

    def test_interrupted_hold_attempts_one_release_and_preserves_original_interruption(self):
        for cleanup in (None, RuntimeError('release also failed')):
            original = KeyboardInterrupt('interrupted during hold')
            with self.subTest(cleanup=cleanup), \
                    mock.patch.object(image, 'qmp', side_effect=[None, cleanup]) as rpc, \
                    mock.patch.object(image.time, 'sleep', side_effect=original) as sleep:
                with self.assertRaises(KeyboardInterrupt) as raised:
                    image.click('/unused', 20, 30, 1280, 800)
                self.assertIs(raised.exception, original)
                sleep.assert_called_once_with(.15)
                self.assert_single_press_and_release(rpc)

    def test_release_only_failure_propagates_without_another_press_or_release(self):
        original = RuntimeError('release result uncertain')
        with mock.patch.object(image, 'qmp', side_effect=[None, original]) as rpc, \
                mock.patch.object(image.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError) as raised:
                image.click('/unused', 20, 30, 1280, 800)
            self.assertIs(raised.exception, original)
            sleep.assert_called_once_with(.15)
            self.assert_single_press_and_release(rpc)


class FreeBsdImageProcessGuards(unittest.TestCase):
    def test_already_exited_child_cannot_bypass_serial_output_budget(self):
        class FinishedChild:
            pid = 12345
            returncode = 0
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory(prefix='hf-image-exited-serial-') as temporary:
            directory = Path(temporary).resolve()
            metadata = {'source_sha256': {}, 'firmware_sha256': {'uefi-code.fd': 'a' * 64, 'uefi-vars.fd': 'a' * 64},
                        'backing_inputs': [], 'source_image_bytes': _DISK_BYTES}
            def completed_process(command, **options):
                options['stdout'].write(b'x' * 1025)
                options['stdout'].flush()
                return FinishedChild()
            with mock.patch.object(image.vm, '_linux'), \
                    mock.patch.object(image, 'prepare', return_value=metadata), \
                    mock.patch.object(image.vm, '_directory', return_value=directory), \
                    mock.patch.object(image, 'qemu_command', return_value=['no-vm-is-launched']), \
                    mock.patch.object(image.vm, '_lock', return_value=nullcontext()), \
                    mock.patch.object(image.vm, '_digest', return_value='a' * 64), \
                    mock.patch.object(image, '_verify_backing_inputs', return_value=True), \
                    mock.patch.object(image, '_verify_provenance_files'), \
                    mock.patch.object(image, '_check_chain', return_value=True), \
                    mock.patch.object(image, '_file_record', return_value={'fixture': 'serial-bound-only'}), \
                    mock.patch.object(image.vm, 'MAX_SERIAL', 1024), \
                    mock.patch.object(image.subprocess, 'Popen', side_effect=completed_process):
                with self.assertRaisesRegex(RuntimeError, 'serial.*bound'):
                    image.run(directory / 'image.raw', 'a' * 64, directory, timeout=30)


def _provenance():
    return {'format': 'DaOS-freebsd-image-build-v1', 'assembled': True,
            'target': {'architecture': 'arm64', 'elf_machine': 183, 'efi_machine': 43620},
            'image': {'sha256': 'a' * 64, 'bytes': 2 * 1024**3},
            'base': {'kernel_ident': 'DaOS', 'kernel_sha256': 'b' * 64, 'state_sha256': 'c' * 64},
            'runtime': {'architecture': 'arm64', 'machine': 'aarch64', 'abi_target': 'FreeBSD:15:aarch64'},
            'native': {'execution_machine': 'arm64', 'sha256': 'd' * 64, 'evidence_sha256': 'e' * 64}}


class FreeBsdImageProvenanceTests(unittest.TestCase):
    def test_complete_arm_receipt_binds_exact_image_kernel_runtime_and_native(self):
        receipt = _provenance()
        self.assertEqual(image.validate_provenance(receipt, 'a' * 64, 2 * 1024**3), receipt)
        for sha, size in (('f' * 64, 2 * 1024**3), ('a' * 64, 2 * 1024**3 + 512)):
            with self.subTest(sha=sha, size=size), self.assertRaises(ValueError):
                image.validate_provenance(receipt, sha, size)
        receipt['runtime']['machine'] = 'arm64'
        receipt['native']['execution_machine'] = 'aarch64'
        self.assertEqual(image.validate_provenance(receipt, 'a' * 64, 2 * 1024**3), receipt)

    def test_incomplete_amd64_mistyped_or_unbound_provenance_is_refused(self):
        changes = ((None, 'format', 'another-format'), (None, 'assembled', 1),
                   ('target', 'architecture', 'amd64'), ('target', 'elf_machine', 62),
                   ('target', 'elf_machine', 183.0), ('target', 'efi_machine', 43620.0),
                   ('base', 'kernel_ident', 'GENERIC'), ('base', 'kernel_sha256', 'not-a-hash'),
                   ('base', 'state_sha256', None), ('runtime', 'architecture', 'amd64'),
                   ('runtime', 'machine', 'amd64'), ('runtime', 'abi_target', 'FreeBSD:15:amd64'),
                   ('native', 'execution_machine', 'amd64'), ('native', 'sha256', ''),
                   ('native', 'evidence_sha256', None))
        for section, key, value in changes:
            receipt = _provenance()
            (receipt if section is None else receipt[section])[key] = value
            with self.subTest(section=section, key=key, value=value), self.assertRaises(ValueError):
                image.validate_provenance(receipt, 'a' * 64, 2 * 1024**3)
        for section in ('target', 'image', 'base', 'runtime', 'native'):
            receipt = _provenance()
            receipt[section] = None
            with self.subTest(section=section), self.assertRaises(ValueError):
                image.validate_provenance(receipt, 'a' * 64, 2 * 1024**3)


def _file_seal(path):
    info = path.stat()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path), 'sha256': digest,
            'identity': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]}


def _put_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() != 0,
                     'resume ownership tests require actual nonroot Linux filesystem/flock/proc')
class FreeBsdImageResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hf-image-resume-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.parent = self.root / 'parent'; self.parent.mkdir(mode=0o700)
        self.child = self.root / 'new-child'
        self.raw = self.root / 'source.raw'
        with _disk(table=_appliance_table(), path=self.raw):
            pass
        (self.parent / 'guest.qcow2').write_bytes(b'unit-fixture-parent-COW-state')
        for name, prefix in (('uefi-code.fd', b'CODE'), ('uefi-vars.fd', b'VARS')):
            with (self.parent / name).open('xb') as stream:
                stream.write(prefix); stream.truncate(64 * 1024**2)
            (self.parent / name).chmod(0o444 if name == 'uefi-code.fd' else 0o600)
        source_dir = self.parent / 'source'; source_dir.mkdir()
        sources = {}
        for name in ('freebsd_image_boot.py', 'freebsd_vm.py'):
            path = source_dir / name; path.write_bytes(b'preserved parent runner snapshot ' + name.encode())
            sources['tools/' + name] = _file_seal(path)['sha256']
        raw_seal = _file_seal(self.raw)
        provenance = _provenance()
        provenance['image'] = {'sha256': raw_seal['sha256'], 'bytes': _DISK_BYTES}
        _put_json(self.parent / 'provenance.json', provenance)
        self.metadata = {'format': 'DaOS-freebsd-image-boot-inputs-v1', 'brand': 'DaOS', 'prepared': True,
                         'source_image': str(self.raw), 'source_image_sha256': raw_seal['sha256'],
                         'source_image_bytes': _DISK_BYTES, 'network': False, 'SSH': False, 'NoCloud': False,
                         'source_sha256': sources, 'provenance_sha256': _file_seal(self.parent / 'provenance.json')['sha256'],
                         'firmware_sha256': {name: _file_seal(self.parent / name)['sha256'] for name in
                                             ('uefi-code.fd', 'uefi-vars.fd')},
                         'backing_inputs': [dict(raw_seal, format='raw')], 'ancestor_work_directories': []}
        _put_json(self.parent / 'image.json', self.metadata)
        self.launch = {'format': 'DaOS-freebsd-image-launch-v1', 'pid': 2147483647, 'process_start_ticks': 1,
                       'input_metadata_sha256': _file_seal(self.parent / 'image.json')['sha256']}
        _put_json(self.parent / 'launch.json', self.launch)
        self.closed = {'format': 'DaOS-freebsd-image-boot-observation-v1', 'completed': True,
                       'process_returncode': 0, 'all_buffers_synced_marker_seen': True,
                       'source_image_unchanged': True, 'UEFI_code_unchanged': True, 'backing_inputs_unchanged': True,
                       'input_metadata_sha256': _file_seal(self.parent / 'image.json')['sha256'],
                       'provenance_sha256': self.metadata['provenance_sha256'],
                       'image_sha256': self.metadata['source_image_sha256'],
                       'private_state': {'overlay': _file_seal(self.parent / 'guest.qcow2'),
                                         'uefi_variables': _file_seal(self.parent / 'uefi-vars.fd')}}
        _put_json(self.parent / 'observation.json', self.closed)
        (self.parent / '.vm.lock').touch()
        self.commands = []
        self.chain_mutation = None
        self.create_mutation = None
        self.check_error = False

    def _commit_metadata(self):
        _put_json(self.parent / 'image.json', self.metadata)
        self.closed['input_metadata_sha256'] = _file_seal(self.parent / 'image.json')['sha256']
        self.launch['input_metadata_sha256'] = self.closed['input_metadata_sha256']
        _put_json(self.parent / 'launch.json', self.launch)
        _put_json(self.parent / 'observation.json', self.closed)

    def _qemu(self, command, **options):
        self.commands.append(command)
        operation = command[1]
        if operation == 'check':
            return {'returncode': 2 if self.check_error else 0, 'stdout': '', 'stderr': ''}
        if operation == 'create':
            self.assertEqual(command[command.index('-F') + 1], 'qcow2')
            self.assertEqual(command[command.index('-b') + 1], str(self.parent / 'guest.qcow2'))
            Path(command[-1]).write_bytes(b'new independent child COW fixture')
            if self.create_mutation is not None:
                self.create_mutation()
            return {'returncode': 0, 'stdout': '', 'stderr': ''}
        self.assertEqual(operation, 'info')
        self.assertIn('--backing-chain', command)
        overlay = Path(command[-1])
        entries = [(str(overlay), 'qcow2')]
        if overlay != self.parent / 'guest.qcow2':
            entries.append((str(self.parent / 'guest.qcow2'), 'qcow2'))
        entries.extend((record['path'], record['format']) for record in self.metadata['backing_inputs'])
        chain = [{'filename': filename, 'format': kind, 'virtual-size': _DISK_BYTES} for filename, kind in entries]
        if self.chain_mutation is not None:
            chain = self.chain_mutation(chain)
        return {'returncode': 0, 'stdout': json.dumps(chain), 'stderr': ''}

    def resume(self):
        # External qemu-img's inspection/create results are the only mocks.
        # All JSON, SHA, file identity, source checks, locks and PID checks run.
        with mock.patch.object(image.vm, '_tool', return_value='/existing/qemu-img'), \
                mock.patch.object(image.vm, '_run', side_effect=self._qemu):
            return image.prepare_resume(self.parent, self.child)

    def test_new_child_backs_stopped_parent_and_preserves_all_parent_input_seals(self):
        paths = [self.raw, *self.parent.glob('*'), *self.parent.joinpath('source').glob('*')]
        before = {str(path): _file_seal(path) for path in paths if path.is_file()}
        result = self.resume()
        self.assertEqual({str(path): _file_seal(path) for path in paths if path.is_file()}, before)
        self.assertEqual(result['ancestor_work_directories'], [str(self.parent)])
        self.assertEqual(result['backing_inputs'][0], dict(self.closed['private_state']['overlay'], format='qcow2'))
        self.assertEqual(result['backing_inputs'][-1], self.metadata['backing_inputs'][-1])
        self.assertEqual((self.child / 'uefi-vars.fd').read_bytes(), (self.parent / 'uefi-vars.fd').read_bytes())
        self.assertEqual((self.child / 'uefi-code.fd').read_bytes(), (self.parent / 'uefi-code.fd').read_bytes())
        self.assertNotEqual((self.child / 'uefi-vars.fd').stat().st_ino, (self.parent / 'uefi-vars.fd').stat().st_ino)
        self.assertNotEqual((self.child / 'guest.qcow2').read_bytes(), (self.parent / 'guest.qcow2').read_bytes())
        self.assertEqual(json.loads((self.child / 'image.json').read_text()), result)
        self.assertFalse(result['network']); self.assertFalse(result['SSH']); self.assertFalse(result['NoCloud'])

    def test_missing_or_unclean_parent_receipts_are_rejected_before_any_tool_or_child(self):
        changes = {'format': 'other', 'completed': False, 'process_returncode': 1,
                   'all_buffers_synced_marker_seen': False, 'source_image_unchanged': False,
                   'UEFI_code_unchanged': False, 'backing_inputs_unchanged': False,
                    'input_metadata_sha256': 'f' * 64, 'image_sha256': 'f' * 64,
                    'provenance_sha256': 'f' * 64, 'private_state': None}
        for key, value in changes.items():
            observation = dict(self.closed, **{key: value})
            _put_json(self.parent / 'observation.json', observation)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.resume()
            self.assertFalse(self.child.exists())
        observation = dict(self.closed, process_returncode=False)
        _put_json(self.parent / 'observation.json', observation)
        with self.assertRaises(ValueError):
            self.resume()
        _put_json(self.parent / 'observation.json', self.closed)
        launch = dict(self.launch, input_metadata_sha256='f' * 64)
        _put_json(self.parent / 'launch.json', launch)
        with self.assertRaises(ValueError):
            self.resume()
        _put_json(self.parent / 'launch.json', self.launch)
        for name in ('launch.json', 'observation.json'):
            path = self.parent / name
            original = path.read_bytes(); path.unlink()
            with self.subTest(missing=name), self.assertRaises((ValueError, OSError)):
                self.resume()
            path.write_bytes(original)
            self.assertFalse(self.child.exists())
        self.assertEqual(self.commands, [])

    def test_existing_child_directory_cannot_be_reused_or_overwritten(self):
        self.child.mkdir(mode=0o700)
        sentinel = self.child / 'guest.qcow2'
        sentinel.write_bytes(b'existing private state must survive')
        before = _file_seal(sentinel)
        with self.assertRaises((ValueError, OSError)):
            self.resume()
        self.assertEqual(_file_seal(sentinel), before)
        self.assertFalse((self.child / 'image.json').exists())
        self.assertFalse(any(command[1] == 'create' for command in self.commands))

    def test_child_runtime_rechecks_preserved_parent_code_provenance_and_sources(self):
        for relative in ('uefi-code.fd', 'provenance.json', 'source/freebsd_image_boot.py',
                         'source/freebsd_vm.py'):
            with self.subTest(relative=relative):
                fixture = type(self)('test_new_child_backs_stopped_parent_and_preserves_all_parent_input_seals')
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                child_metadata = fixture.resume()
                self.assertTrue(image._verify_backing_inputs(child_metadata))
                path = fixture.parent / relative
                path.chmod(0o600)
                with path.open('ab') as output:
                    output.write(b'parent changed after child preparation')
                with self.assertRaises(ValueError):
                    image._verify_backing_inputs(child_metadata)

    def test_live_flock_qmp_entry_and_matching_pid_ticks_refuse_parent_reuse(self):
        with image.vm._lock(self.parent):
            with self.assertRaisesRegex(RuntimeError, 'already owned'):
                self.resume()
        (self.parent / 'qmp.sock').write_bytes(b'stale socket name must not be deleted')
        with self.assertRaisesRegex(ValueError, 'QMP'):
            self.resume()
        (self.parent / 'qmp.sock').unlink()
        launch = dict(self.launch, pid=os.getpid(), process_start_ticks=image._process_start_ticks(os.getpid()))
        _put_json(self.parent / 'launch.json', launch)
        with self.assertRaisesRegex(ValueError, 'still active'):
            self.resume()
        self.assertFalse(self.child.exists())
        self.assertEqual(self.commands, [])

    def test_replaced_overlay_same_bytes_and_changed_vars_are_rejected(self):
        overlay = self.parent / 'guest.qcow2'
        replacement = self.parent / 'replacement'
        replacement.write_bytes(overlay.read_bytes())
        os.replace(replacement, overlay)
        with self.assertRaisesRegex(ValueError, 'identity or bytes changed'):
            self.resume()
        # Restore a newly sealed baseline to isolate the variables mutation.
        self.closed['private_state']['overlay'] = _file_seal(overlay)
        _put_json(self.parent / 'observation.json', self.closed)
        with (self.parent / 'uefi-vars.fd').open('r+b') as output:
            output.write(b'changed-state')
        with self.assertRaisesRegex(ValueError, 'identity or bytes changed'):
            self.resume()
        self.assertFalse(self.child.exists())

    def test_changed_original_raw_provenance_firmware_and_saved_sources_are_refused(self):
        cases = ((self.raw, b'bad raw', 'resume input'),
                 (self.parent / 'provenance.json', b'bad provenance', 'provenance'),
                 (self.parent / 'uefi-code.fd', b'bad firmware', 'UEFI code'),
                 (self.parent / 'source' / 'freebsd_vm.py', b'bad source', 'runner source'))
        for path, replacement, message in cases:
            original = path.read_bytes()
            before = path.stat()
            path.chmod(0o600)
            with path.open('r+b') as output:
                output.write(replacement)
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, message):
                self.resume()
            path.write_bytes(original)
            path.chmod(before.st_mode & 0o777)
            # Raw is stat-sealed, so restore its current identity in metadata.
            if path == self.raw:
                self.metadata['backing_inputs'][-1] = dict(_file_seal(self.raw), format='raw')
                self._commit_metadata()
        self.assertFalse(self.child.exists())

    def test_parent_check_failure_and_unexpected_backing_paths_formats_or_data_files_refuse_child(self):
        self.check_error = True
        with self.assertRaisesRegex(RuntimeError, 'verify clean'):
            self.resume()
        self.check_error = False
        mutations = (lambda entries: entries[:-1],
                     lambda entries: [dict(entries[0], filename='/outside/other.qcow2'), *entries[1:]],
                     lambda entries: [dict(entries[0], format='raw'), *entries[1:]],
                     lambda entries: [dict(entries[0], **{'virtual-size': _DISK_BYTES + 512}), *entries[1:]],
                     lambda entries: [dict(entries[0], **{'data-file': '/outside/file'}), *entries[1:]],
                     lambda entries: [dict(entries[0], **{'format-specific': {'data': {'data-file': '/outside/file'}}}), *entries[1:]])
        for index, mutation in enumerate(mutations):
            self.chain_mutation = mutation
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.resume()
            self.assertFalse(self.child.exists())

    def test_ancestry_limit_cycle_and_changed_backing_identity_are_rejected(self):
        original = copy.deepcopy(self.metadata)
        for ancestors in ([str(self.parent)], [str(self.root)] * image.MAX_BACKING_INPUTS):
            self.metadata['ancestor_work_directories'] = ancestors
            self._commit_metadata()
            with self.subTest(ancestors=ancestors), self.assertRaisesRegex(ValueError, 'ancestry'):
                self.resume()
            self.assertFalse(self.child.exists())
        self.metadata = original
        self.metadata['backing_inputs'][-1]['identity'][3] -= 1
        self._commit_metadata()
        with self.assertRaisesRegex(ValueError, 'identity or bytes changed'):
            self.resume()

    def test_parent_input_changes_during_cow_creation_cannot_publish_success(self):
        # Each independent fixture starts with valid receipts and real byte/stat seals.
        # qemu-img is the only external boundary replaced; the input mutation is real.
        for relative in ('uefi-code.fd', 'uefi-vars.fd', 'guest.qcow2', 'image.json',
                         'launch.json', 'observation.json', 'provenance.json',
                         'source/freebsd_image_boot.py', 'source/freebsd_vm.py', '../source.raw'):
            with self.subTest(relative=relative):
                fixture = type(self)('test_new_child_backs_stopped_parent_and_preserves_all_parent_input_seals')
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                path = (fixture.parent / relative).resolve()
                def change_input():
                    path.chmod(0o600)
                    with path.open('ab') as output:
                        output.write(b'changed during external tool')
                fixture.create_mutation = change_input
                with self.assertRaises((ValueError, RuntimeError)):
                    fixture.resume()
                self.assertFalse((fixture.child / 'image.json').exists())


if __name__ == '__main__':
    unittest.main()

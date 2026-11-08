"""Boot a sanitized DaOS FreeBSD ARM64 raw image without network or host shares.

Only a private qcow2 overlay and private UEFI variables are writable. Source
bytes remain unchanged. Framebuffer captures are evidence for human inspection;
nonblack pixels and process startup never establish that the camera UI is ready.
"""
from __future__ import annotations

import argparse
import binascii
from contextlib import ExitStack
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from tools import freebsd_vm as vm

MAX_RAW = 8 * 1024**3
MAX_COW = MAX_RAW + 128 * 1024**2
MAX_BACKING_INPUTS = 8
MAX_PPM = 4096 * 4096 * 3 + 4096
EFI_GUID = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b')
UFS_GUID = uuid.UUID('516e7cb6-6ecf-11d6-8ff8-00022d09712b')
_SHA = re.compile('[0-9a-f]{64}')


def validate_raw(stream, *, appliance=False):
    """Verify a bounded protective GPT and both CRC-protected partition tables.

    EFI/UFS partition type GUIDs are checked; filesystem payloads are not parsed.
    Full source-image hash and build receipt bind those payloads separately.
    """
    if type(appliance) is not bool:
        raise ValueError('appliance GPT validation mode must be boolean')
    original = stream.tell()
    try:
        stream.seek(0, 2); size = stream.tell()
        if not 32 * 1024**2 <= size <= MAX_RAW or size % 512:
            raise ValueError('raw image must be 32 MiB..8 GiB in whole 512-byte sectors')
        sectors = size // 512
        stream.seek(0); mbr = stream.read(512)
        if len(mbr) != 512 or mbr[510:512] != b'\x55\xaa':
            raise ValueError('protective MBR signature missing')
        entries = [mbr[446 + n * 16:462 + n * 16] for n in range(4)]
        protective = [entry for entry in entries if entry[4] == 0xee]
        if (len(protective) != 1 or any(entry[4] not in (0, 0xee) for entry in entries) or
                struct.unpack_from('<I', protective[0], 8)[0] != 1 or
                struct.unpack_from('<I', protective[0], 12)[0] != min(sectors - 1, 0xffffffff)):
            raise ValueError('ordinary protective GPT MBR required')
        def header(lba):
            stream.seek(lba * 512); raw = stream.read(512)
            if len(raw) != 512 or raw[:8] != b'EFI PART':
                raise ValueError('GPT header signature missing')
            revision, length, checksum, reserved = struct.unpack_from('<IIII', raw, 8)
            if revision != 0x10000 or not 92 <= length <= 512 or reserved:
                raise ValueError('unsupported GPT header')
            checked = bytearray(raw[:length]); checked[16:20] = b'\0' * 4
            if binascii.crc32(checked) & 0xffffffff != checksum:
                raise ValueError('GPT header CRC mismatch')
            current, alternate, first, last = struct.unpack_from('<QQQQ', raw, 24)
            table, count, entry_size, table_crc = struct.unpack_from('<QIII', raw, 72)
            if (current != lba or alternate >= sectors or current == alternate or
                    not 2 <= first <= last < sectors - 1 or not 1 <= count <= 1024 or
                    entry_size not in (128, 256, 512, 1024) or count * entry_size > 1024 * 1024):
                raise ValueError('GPT metadata exceeds bounds')
            table_sectors = (count * entry_size + 511) // 512
            if not 2 <= table or table + table_sectors > sectors - 1:
                raise ValueError('GPT table outside raw image')
            stream.seek(table * 512); array = stream.read(count * entry_size)
            if len(array) != count * entry_size or binascii.crc32(array) & 0xffffffff != table_crc:
                raise ValueError('GPT partition-array CRC mismatch')
            return {'current': current, 'alternate': alternate, 'first': first, 'last': last,
                    'guid': raw[56:72], 'table': table, 'count': count, 'entry_size': entry_size,
                    'table_crc': table_crc, 'table_sectors': table_sectors, 'array': array}
        primary, backup = header(1), header(sectors - 1)
        if (primary['alternate'] != sectors - 1 or backup['alternate'] != 1 or
                primary['guid'] == b'\0' * 16 or primary['table'] + primary['table_sectors'] > primary['first'] or
                backup['table'] <= primary['last'] or backup['table'] + backup['table_sectors'] > sectors - 1 or
                any(primary[key] != backup[key] for key in ('first', 'last', 'guid', 'count', 'entry_size', 'table_crc', 'array'))):
            raise ValueError('primary and backup GPT do not form one contained image')
        partitions, identities = [], set()
        for offset in range(0, len(primary['array']), primary['entry_size']):
            entry = primary['array'][offset:offset + primary['entry_size']]
            if entry[:16] == b'\0' * 16:
                continue
            identity = entry[16:32]
            first, last = struct.unpack_from('<QQ', entry, 32)
            if (identity == b'\0' * 16 or identity in identities or
                    not primary['first'] <= first <= last <= primary['last']):
                raise ValueError('GPT partition identity or range invalid')
            identities.add(identity)
            try:
                encoded_label = entry[56:128].decode('utf-16-le')
            except UnicodeError as error:
                raise ValueError('GPT partition label is not valid UTF-16LE') from error
            label, separator, padding = encoded_label.partition('\0')
            if separator and padding.strip('\0'):
                raise ValueError('GPT label has nonzero bytes after its terminator')
            partitions.append({'type_guid': str(uuid.UUID(bytes_le=entry[:16])),
                               'partition_guid': str(uuid.UUID(bytes_le=identity)),
                               'label': label, 'first_lba': first, 'last_lba': last, 'size_bytes': (last - first + 1) * 512})
        ordered = sorted(partitions, key=lambda item: item['first_lba'])
        if any(left['last_lba'] >= right['first_lba'] for left, right in zip(ordered, ordered[1:])):
            raise ValueError('GPT partitions overlap')
        efi = sum(item['type_guid'] == str(EFI_GUID) for item in partitions)
        ufs = sum(item['type_guid'] == str(UFS_GUID) for item in partitions)
        if efi != 1 or not 1 <= ufs <= 4:
            raise ValueError('one EFI and one through four FreeBSD UFS partitions required')
        if appliance:
            expected = {'DaOS-efi': str(EFI_GUID), 'DaOS-root': str(UFS_GUID), 'DaOS-state': str(UFS_GUID),
                        'DaOS-calibration': str(UFS_GUID), 'DaOS-photos': str(UFS_GUID)}
            if (len(partitions) != len(expected) or len({item['label'] for item in partitions}) != len(expected) or
                    {item['label']: item['type_guid'] for item in partitions} != expected):
                raise ValueError('appliance requires exactly the five named DaOS EFI/root/state/calibration/photos partitions')
        return {'format': 'hf-raw-gpt-image-v1', 'size_bytes': size, 'sector_bytes': 512,
                'disk_guid': str(uuid.UUID(bytes_le=primary['guid'])), 'partitions': partitions,
                'gpt_primary_crc_verified': True, 'gpt_backup_crc_verified': True,
                'appliance_layout_verified': appliance, 'filesystem_payloads_parsed': False}
    finally:
        stream.seek(original)


def parse_ppm(raw):
    """Parse one bounded P6 framebuffer without changing any RGB bytes."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PPM:
        raise ValueError('bounded P6 image bytes required')
    cursor = 0
    def token():
        nonlocal cursor
        while cursor < len(raw):
            if raw[cursor] in b' \t\r\n':
                cursor += 1
            elif raw[cursor] == 35:
                end = raw.find(b'\n', cursor)
                if end < 0 or end - cursor > 1024:
                    raise ValueError('PPM comment exceeds bound')
                cursor = end + 1
            else:
                break
        start = cursor
        while cursor < len(raw) and raw[cursor] not in b' \t\r\n#':
            cursor += 1
            if cursor - start > 16:
                raise ValueError('PPM header token exceeds bound')
        if start == cursor or cursor > 4096:
            raise ValueError('PPM header missing or exceeds bound')
        return raw[start:cursor]
    magic, width, height, maximum = token(), token(), token(), token()
    if magic != b'P6' or maximum != b'255' or not width.isdigit() or not height.isdigit():
        raise ValueError('only bounded 8-bit RGB P6 is supported')
    width, height = int(width), int(height)
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError('PPM framebuffer dimensions exceed bound')
    if cursor >= len(raw) or raw[cursor] not in b' \t\r\n':
        raise ValueError('PPM pixel delimiter missing')
    # Consume exactly the header delimiter. Pixel bytes may themselves be space,
    # newline or '#'; stripping whitespace here would silently change the image.
    if raw[cursor:cursor + 2] == b'\r\n':
        cursor += 2
    else:
        cursor += 1
    pixels = raw[cursor:]
    if len(pixels) != width * height * 3:
        raise ValueError('PPM pixel byte count differs from dimensions')
    return width, height, pixels


def ppm_to_png(raw):
    width, height, pixels = parse_ppm(raw)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)
    scanlines = b''.join(b'\0' + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(scanlines, 6)) + chunk(b'IEND', b''))


def validate_provenance(value, image_sha256, image_bytes):
    if type(value) is not dict or any(type(value.get(key)) is not dict for key in ('target', 'image', 'base', 'runtime', 'native')):
        raise ValueError('complete image build receipt objects required')
    if (value.get('format') != 'DaOS-freebsd-image-build-v1' or
            value.get('assembled') is not True or
            value.get('target', {}).get('architecture') != 'arm64' or
            type(value.get('target', {}).get('elf_machine')) is not int or
            type(value.get('target', {}).get('efi_machine')) is not int or
            value.get('target', {}).get('elf_machine') != 183 or value.get('target', {}).get('efi_machine') != 43620 or
            value.get('image', {}).get('sha256') != image_sha256 or value.get('image', {}).get('bytes') != image_bytes or
            value.get('base', {}).get('kernel_ident') != 'DaOS' or
            not isinstance(value.get('base', {}).get('kernel_sha256'), str) or
            not _SHA.fullmatch(value['base']['kernel_sha256']) or
            not isinstance(value.get('base', {}).get('state_sha256'), str) or
            not _SHA.fullmatch(value['base']['state_sha256']) or
            value.get('runtime', {}).get('architecture') != 'arm64' or
            value.get('runtime', {}).get('machine') not in ('arm64', 'aarch64') or
            value.get('runtime', {}).get('abi_target') != 'FreeBSD:15:aarch64' or
            value.get('native', {}).get('execution_machine') not in ('arm64', 'aarch64') or
            not isinstance(value.get('native', {}).get('sha256'), str) or
            not _SHA.fullmatch(value['native']['sha256']) or
            not isinstance(value.get('native', {}).get('evidence_sha256'), str) or
            not _SHA.fullmatch(value['native']['evidence_sha256'])):
        raise ValueError('raw image receipt must bind DaOS ARM64 kernel/build state and actual ARM64 runtime')
    return value


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _file_record(path, *, maximum=MAX_COW):
    path = Path(os.path.abspath(os.fspath(path)))
    with vm._pin(path) as stream:
        before = os.fstat(stream.fileno())
        if before.st_uid != os.geteuid() or not 0 < before.st_size <= maximum:
            raise ValueError('bounded task-owned resume input required')
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        if _identity(before) != _identity(os.fstat(stream.fileno())) or _identity(before) != _identity(path.lstat()):
            raise ValueError('resume input changed while being pinned and hashed')
        return {'path': str(path), 'sha256': digest.hexdigest(), 'identity': _identity(before)}


def _verify_record(record):
    if (type(record) is not dict or set(record) != {'path', 'sha256', 'identity'} or
            not isinstance(record['path'], str) or not isinstance(record['sha256'], str) or
            not _SHA.fullmatch(record['sha256']) or type(record['identity']) is not list or
            len(record['identity']) != 5 or any(type(value) is not int for value in record['identity'])):
        raise ValueError('complete immutable file identity/hash required')
    if _file_record(record['path']) != record:
        raise ValueError('sealed resume input identity or bytes changed')


def _verify_backing_inputs(metadata):
    records = metadata.get('backing_inputs')
    if (type(records) is not list or not 1 <= len(records) <= MAX_BACKING_INPUTS or
            any(type(record) is not dict or not isinstance(record.get('path'), str) for record in records) or
            len({record['path'] for record in records}) != len(records)):
        raise ValueError('bounded unique immutable COW backing chain required')
    for index, record in enumerate(records):
        if type(record) is not dict or record.get('format') != ('raw' if index == len(records) - 1 else 'qcow2'):
            raise ValueError('COW ancestors must end in the original raw image')
        _verify_record({key: value for key, value in record.items() if key != 'format'})
    raw = records[-1]
    if (raw['path'] != metadata['source_image'] or raw['sha256'] != metadata['source_image_sha256'] or
            raw['identity'][2] != metadata['source_image_bytes']):
        raise ValueError('COW chain no longer binds the original source image')
    if vm._digest(Path(raw['path'])) != metadata['source_image_sha256']:
        raise ValueError('original raw source changed')
    parent = metadata.get('resume_parent')
    if parent is not None:
        if type(parent) is not dict:
            raise ValueError('complete resume-parent commitment required')
        directory = vm._directory(parent['directory'])
        if (vm._digest(directory / 'observation.json') != parent['observation_sha256'] or
                vm._digest(directory / 'image.json') != parent['input_metadata_sha256']):
            raise ValueError('parent evidence changed during resume')
        guarded = parent.get('guarded_inputs')
        if type(guarded) is not dict or not 1 <= len(guarded) <= 8:
            raise ValueError('complete parent evidence/firmware/source seals required')
        for record in guarded.values():
            _verify_record(record)
        _verify_record(parent['private_state']['uefi_variables'])
    return True


def _check_chain(overlay, backing_inputs, virtual_bytes):
    checked = vm._run([vm._tool('qemu-img'), 'info', '--output=json', '--backing-chain', '-f', 'qcow2', str(overlay)],
                      timeout=30, maximum=65536)
    if checked['returncode']:
        raise RuntimeError('offline COW chain inspection failed or image is active')
    chain = vm._json(checked['stdout'])
    expected = [(str(overlay), 'qcow2')] + [(record['path'], record['format']) for record in backing_inputs]
    if type(chain) is not list or len(chain) != len(expected):
        raise ValueError('unexpected COW backing-chain length')
    for item, (path, kind) in zip(chain, expected):
        if (type(item) is not dict or item.get('filename') != path or item.get('format') != kind or
                item.get('virtual-size') != virtual_bytes or item.get('encrypted') is True or
                item.get('data-file') or item.get('format-specific', {}).get('data', {}).get('data-file')):
            raise ValueError('unexpected COW path/format/size or external data file')
    return True


def _process_start_ticks(pid):
    if type(pid) is not int or pid <= 0:
        return None
    try:
        raw = Path('/proc/' + str(pid) + '/stat').read_text()
        return int(raw[raw.rfind(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _verify_provenance_files(directory, metadata):
    if vm._digest(directory / 'provenance.json') != metadata.get('provenance_sha256'):
        raise ValueError('source-build/runtime provenance changed')
    validate_provenance(vm._read_json(directory / 'provenance.json'), metadata['source_image_sha256'], metadata['source_image_bytes'])
    sources = metadata.get('source_sha256')
    if type(sources) is not dict or set(sources) != {'tools/freebsd_image_boot.py', 'tools/freebsd_vm.py'}:
        raise ValueError('complete saved runner sources required')
    for relative, expected in sources.items():
        if vm._digest(directory / 'source' / Path(relative).name) != expected:
            raise ValueError('saved runner source changed')


def prepare_resume(parent_work_dir, work_dir):
    """Make a NEW COW layer over a verified clean stopped run; never reset it."""
    vm._linux()
    parent, metadata = _metadata(parent_work_dir)
    with vm._pin(parent / '.vm.lock') as lock:
        if os.fstat(lock.fileno()).st_uid != os.geteuid():
            raise ValueError('owned original parent lifecycle lock required')
    with vm._lock(parent, timeout=0):
        if os.path.lexists(parent / 'qmp.sock'):
            raise ValueError('parent has an active or stale QMP socket; no automatic recovery')
        guarded_names = ('image.json', 'launch.json', 'observation.json', 'provenance.json', 'uefi-code.fd',
                         'source/freebsd_image_boot.py', 'source/freebsd_vm.py')
        parent_records = {name: _file_record(parent / name) for name in guarded_names}
        launch = vm._read_json(parent / 'launch.json')
        ticks = _process_start_ticks(launch.get('pid'))
        if ticks is not None and ticks == launch.get('process_start_ticks'):
            raise ValueError('parent QEMU process is still active')
        closed = vm._read_json(parent / 'observation.json')
        if (closed.get('format') != 'DaOS-freebsd-image-boot-observation-v1' or closed.get('completed') is not True or
                type(closed.get('process_returncode')) is not int or closed['process_returncode'] != 0 or
                closed.get('all_buffers_synced_marker_seen') is not True or closed.get('source_image_unchanged') is not True or
                closed.get('UEFI_code_unchanged') is not True or closed.get('backing_inputs_unchanged') is not True or
                closed.get('input_metadata_sha256') != vm._digest(parent / 'image.json') or
                closed.get('input_metadata_sha256') != launch.get('input_metadata_sha256') or
                closed.get('image_sha256') != metadata['source_image_sha256'] or
                closed.get('provenance_sha256') != metadata.get('provenance_sha256')):
            raise ValueError('resume requires a clean sealed parent; no reset or crash replay')
        state = closed.get('private_state')
        if type(state) is not dict or set(state) != {'overlay', 'uefi_variables'}:
            raise ValueError('clean parent lacks private-state identity/hash seals')
        for key, name in [('overlay', 'guest.qcow2'), ('uefi_variables', 'uefi-vars.fd')]:
            if type(state[key]) is not dict or state[key].get('path') != str(parent / name):
                raise ValueError('parent private-state path differs from its seal')
            _verify_record(state[key])
        _verify_backing_inputs(metadata); _verify_provenance_files(parent, metadata)
        with vm._pin(metadata['source_image']) as source:
            validate_raw(source, appliance=True)
        if vm._digest(parent / 'uefi-code.fd') != metadata['firmware_sha256']['uefi-code.fd']:
            raise ValueError('parent immutable UEFI code changed')
        if (state['uefi_variables']['identity'][2] != 64 * 1024**2 or
                (parent / 'uefi-code.fd').stat().st_size != 64 * 1024**2):
            raise ValueError('parent AAVMF code and variables must retain their original 64 MiB sizes')
        ancestors = metadata.get('ancestor_work_directories', [])
        if (type(ancestors) is not list or any(not isinstance(path, str) for path in ancestors) or
                len(set(ancestors)) != len(ancestors) or len(ancestors) + 2 > MAX_BACKING_INPUTS or str(parent) in ancestors):
            raise ValueError('resume ancestry limit exceeded or cycle found')
        for directory in ancestors:
            if vm._directory(directory) == parent:
                raise ValueError('cyclic parent ancestry')
        checked = vm._run([vm._tool('qemu-img'), 'check', '-f', 'qcow2', str(parent / 'guest.qcow2')],
                          timeout=60, maximum=65536)
        if checked['returncode']:
            raise RuntimeError('parent COW did not verify clean offline')
        _verify_record(state['overlay'])
        _check_chain(parent / 'guest.qcow2', metadata['backing_inputs'], metadata['source_image_bytes'])
        backing = [dict(state['overlay'], format='qcow2'), *metadata['backing_inputs']]
        directory = vm._directory(work_dir, new=True)
        created = vm._run([vm._tool('qemu-img'), 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
                          str(parent / 'guest.qcow2'), str(directory / 'guest.qcow2')], timeout=30)
        if created['returncode']:
            raise RuntimeError('new resume COW creation failed; parent is untouched')
        firmware = {}
        for name in ('uefi-code.fd', 'uefi-vars.fd'):
            with vm._pin(parent / name) as source, (directory / name).open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024); output.flush(); os.fsync(output.fileno())
            (directory / name).chmod(0o444 if name == 'uefi-code.fd' else 0o600)
            firmware[name] = vm._digest(directory / name)
        if (firmware['uefi-vars.fd'] != state['uefi_variables']['sha256'] or
                firmware['uefi-code.fd'] != metadata['firmware_sha256']['uefi-code.fd']):
            raise ValueError('parent UEFI code or variables changed during copy')
        (directory / 'screens').mkdir(mode=0o700); (directory / 'source').mkdir()
        source_hashes = {}
        for relative in ('tools/freebsd_image_boot.py', 'tools/freebsd_vm.py'):
            shutil.copyfile(ROOT / relative, directory / 'source' / Path(relative).name)
            source_hashes[relative] = vm._digest(ROOT / relative)
        shutil.copyfile(parent / 'provenance.json', directory / 'provenance.json')
        evidence = directory / 'parent'; evidence.mkdir()
        for name in ('image.json', 'launch.json', 'observation.json'):
            shutil.copyfile(parent / name, evidence / name)
        result = dict(metadata, firmware_sha256=firmware, source_sha256=source_hashes, backing_inputs=backing,
                      ancestor_work_directories=[*ancestors, str(parent)],
                      resume_parent={'directory': str(parent), 'observation_sha256': vm._digest(parent / 'observation.json'),
                                     'input_metadata_sha256': vm._digest(parent / 'image.json'),
                                     'private_state': state, 'guarded_inputs': parent_records},
                      guest_writes='new private COW over stopped parent; parent bytes unchanged')
        _check_chain(directory / 'guest.qcow2', backing, metadata['source_image_bytes'])
        _verify_backing_inputs(metadata); _verify_provenance_files(parent, metadata)
        for record in parent_records.values():
            _verify_record(record)
        _verify_record(state['overlay']); _verify_record(state['uefi_variables'])
        vm._write_json(directory / 'image.json', result)
        return result


def prepare(image, expected_sha256, work_dir, *, provenance=None):
    vm._linux()
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        raise ValueError('exact source raw-image SHA256 required')
    image = Path(os.path.abspath(os.fspath(image)))
    with vm._pin(image) as source:
        info = os.fstat(source.fileno())
        if info.st_uid != os.geteuid():
            raise ValueError('task-owned regular raw image required')
        raw = validate_raw(source, appliance=True)
        if vm._digest(image) != expected_sha256:
            raise ValueError('source raw image SHA256 mismatch')
    if provenance is None:
        raise ValueError('custom ARM64 kernel/build/runtime provenance receipt required')
    receipt = validate_provenance(vm._read_json(provenance), expected_sha256, raw['size_bytes'])
    executable, image_tool = vm._tool('qemu-system-aarch64'), vm._tool('qemu-img')
    code = Path('/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd')
    variables = Path('/usr/share/AAVMF/AAVMF_VARS.fd')
    for path in (code, variables):
        with vm._pin(path) as stream:
            if os.fstat(stream.fileno()).st_size != 64 * 1024**2:
                raise ValueError('existing regular AAVMF code and variables required')
    directory = vm._directory(work_dir, new=True)
    firmware = {}
    for name, path in [('uefi-code.fd', code), ('uefi-vars.fd', variables)]:
        with vm._pin(path) as source, (directory / name).open('xb') as output:
            shutil.copyfileobj(source, output, 1024 * 1024); output.flush(); os.fsync(output.fileno())
        (directory / name).chmod(0o444 if name == 'uefi-code.fd' else 0o600)
        firmware[name] = vm._digest(directory / name)
    created = vm._run([image_tool, 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(image),
                       str(directory / 'guest.qcow2')], timeout=30)
    if created['returncode']:
        raise RuntimeError('private custom-image COW creation failed')
    (directory / 'screens').mkdir(mode=0o700)
    sources = directory / 'source'; sources.mkdir()
    source_hashes = {}
    for name in ('tools/freebsd_image_boot.py', 'tools/freebsd_vm.py'):
        shutil.copyfile(ROOT / name, sources / Path(name).name)
        source_hashes[name] = vm._digest(ROOT / name)
    if receipt is not None:
        shutil.copyfile(provenance, directory / 'provenance.json')
    result = {'format': 'DaOS-freebsd-image-boot-inputs-v1', 'brand': 'DaOS', 'prepared': True,
              'source_image': str(image), 'source_image_sha256': expected_sha256, 'source_image_bytes': raw['size_bytes'],
              'raw_gpt': raw, 'firmware_sha256': firmware, 'source_sha256': source_hashes,
              'backing_inputs': [dict(_file_record(image, maximum=MAX_RAW), format='raw')], 'ancestor_work_directories': [],
              'provenance_sha256': vm._digest(directory / 'provenance.json') if receipt else None,
              'build_receipt_validated': receipt is not None, 'qemu': executable,
              'network': False, 'host_mounts': False, 'host_shares': False, 'NoCloud': False,
              'SSH': False, 'temporary_guest_key_injected': False, 'guest_writes': 'private qcow2 and UEFI variables only',
              'UI_readiness_claimed': False}
    vm._write_json(directory / 'image.json', result)
    return result


def _metadata(work_dir):
    directory = vm._directory(work_dir)
    value = vm._read_json(directory / 'image.json')
    if (value.get('format') != 'DaOS-freebsd-image-boot-inputs-v1' or value.get('prepared') is not True or
            value.get('brand') != 'DaOS' or value.get('network') is not False or value.get('NoCloud') is not False or
            value.get('SSH') is not False or not isinstance(value.get('source_image_sha256'), str) or
            not _SHA.fullmatch(value['source_image_sha256'])):
        raise ValueError('isolated DaOS ARM64 image inputs required')
    return directory, value


def qemu_command(work_dir):
    directory, _ = _metadata(work_dir)
    return [vm._tool('qemu-system-aarch64'), '-machine', 'virt,gic-version=3', '-accel', 'tcg,thread=multi',
            '-cpu', 'cortex-a72', '-smp', '4', '-m', '4096', '-nodefaults', '-display', 'none',
            '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot',
            '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off',
            '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=' + str(directory / 'uefi-code.fd'),
            '-drive', 'if=pflash,format=raw,unit=1,file=' + str(directory / 'uefi-vars.fd'),
            '-drive', 'file=' + str(directory / 'guest.qcow2') + ',format=qcow2,if=none,id=release,cache=writeback',
            '-device', 'virtio-blk-pci,drive=release', '-device', 'ramfb',
            '-device', 'qemu-xhci,id=usb', '-device', 'usb-kbd,bus=usb.0', '-device', 'usb-tablet,bus=usb.0',
            '-object', 'rng-random,filename=/dev/urandom,id=DaOS-rng0',
            '-device', 'virtio-rng-pci,rng=DaOS-rng0',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def qmp(work_dir, command, arguments=None, *, timeout=5):
    vm._linux(); vm._number(timeout, .1, 30, 'image QMP timeout')
    directory, _ = _metadata(work_dir)
    if command not in ('query-status', 'system_powerdown', 'quit', 'screendump', 'send-key', 'input-send-event'):
        raise ValueError('unsupported isolated-image QMP operation')
    path = directory / 'qmp.sock'
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError('owned private image QMP socket required')
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout); connection.connect(str(path))
        with connection.makefile('rb') as stream:
            def receive(identifier=None):
                for _ in range(32):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError('image QMP total deadline exceeded')
                    connection.settimeout(remaining)
                    raw = stream.readline(65537)
                    if not raw or len(raw) > 65536:
                        raise ValueError('image QMP response exceeds bound')
                    value = vm._json(raw)
                    if type(value) is not dict:
                        raise ValueError('image QMP object required')
                    if identifier is None or value.get('id') == identifier:
                        return value
                raise ValueError('image QMP event budget exceeded')
            if 'QMP' not in receive():
                raise ValueError('image QMP greeting missing')
            for identifier, operation, options in [('cap', 'qmp_capabilities', None), ('request', command, arguments)]:
                request = {'execute': operation, 'id': identifier}
                if options is not None:
                    request['arguments'] = options
                encoded = (json.dumps(request, allow_nan=False) + '\n').encode()
                if len(encoded) > 16384:
                    raise ValueError('image QMP request exceeds bound')
                connection.sendall(encoded)
                result = receive(identifier)
                if 'error' in result or 'return' not in result:
                    raise RuntimeError('image QMP rejected operation: ' + json.dumps(result.get('error', {}))[:512])
            return result['return']


def screenshot(work_dir, name):
    vm._linux()
    directory, _ = _metadata(work_dir)
    if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('portable unique screenshot name required')
    if len(list((directory / 'screens').glob('*.json'))) >= 32:
        raise ValueError('image framebuffer capture budget exhausted')
    ppm, png = directory / 'screens' / (name + '.ppm'), directory / 'screens' / (name + '.png')
    if os.path.lexists(ppm) or os.path.lexists(png):
        raise ValueError('framebuffer outputs must be NEW')
    with ppm.open('x+b') as output:
        before = os.fstat(output.fileno())
        # QEMU opens this pinned parent descriptor, not a guest-controlled path.
        target = '/proc/' + str(os.getpid()) + '/fd/' + str(output.fileno())
        qmp(directory, 'screendump', {'filename': target, 'format': 'ppm'}, timeout=10)
        output.flush(); os.fsync(output.fileno())
        current = ppm.lstat(); opened = os.fstat(output.fileno())
        if ((current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not 1 <= opened.st_size <= MAX_PPM):
            raise RuntimeError('owned framebuffer file changed or exceeded bound')
        output.seek(0); raw = output.read(MAX_PPM + 1)
    width, height, pixels = parse_ppm(raw)
    encoded = ppm_to_png(raw)
    with png.open('xb') as output:
        output.write(encoded); output.flush(); os.fsync(output.fileno())
    result = {'format': 'DaOS-virtual-framebuffer-v1', 'name': name, 'width': width, 'height': height,
              'ppm': str(ppm), 'ppm_sha256': hashlib.sha256(raw).hexdigest(),
              'png': str(png), 'png_sha256': hashlib.sha256(encoded).hexdigest(),
              'rgb_sha256': hashlib.sha256(pixels).hexdigest(),
              'nonblack_pixels': sum(pixels[index:index + 3] != b'\0\0\0' for index in range(0, len(pixels), 3)),
              'conversion': 'RGB pixels unchanged; P6 to PNG format only', 'UI_readiness_claimed': False}
    vm._write_json(directory / 'screens' / (name + '.json'), result)
    return result


def send_key(work_dir, keys):
    if (not isinstance(keys, str) or not 1 <= len(keys) <= 64 or
            any(not re.fullmatch(r'(?:[a-z0-9]|f(?:[1-9]|1[0-2])|ret|spc|tab|esc|up|down|left|right|ctrl|alt|shift|backspace|delete|home|end|pgup|pgdn)', key)
                for key in keys.split('-')) or not 1 <= len(keys.split('-')) <= 4):
        raise ValueError('bounded explicit QEMU key chord required')
    return qmp(work_dir, 'send-key', {'keys': [{'type': 'qcode', 'data': key} for key in keys.split('-')], 'hold-time': 100})


def click(work_dir, x, y, width, height):
    if (any(type(value) is not int for value in (x, y, width, height)) or
            not 1 <= width <= 4096 or not 1 <= height <= 4096 or not 0 <= x < width or not 0 <= y < height):
        raise ValueError('explicit bounded framebuffer click coordinates required')
    move = [{'type': 'abs', 'data': {'axis': 'x', 'value': round(x * 32767 / max(width - 1, 1))}},
            {'type': 'abs', 'data': {'axis': 'y', 'value': round(y * 32767 / max(height - 1, 1))}}]
    qmp(work_dir, 'input-send-event', {'events': move + [{'type': 'btn', 'data': {'down': True, 'button': 'left'}}]})
    return qmp(work_dir, 'input-send-event', {'events': [{'type': 'btn', 'data': {'down': False, 'button': 'left'}}]})


def _owned_child(parent_pid):
    # QEMU gets its own process group and dies if its owning runner is killed.
    # These settings apply to this new child only and need no host privilege.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        os._exit(125)


def _stop_group(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def run(image, expected_sha256, work_dir, *, timeout=600, provenance=None, one_shot=False):
    vm._number(timeout, 30, 1200, 'custom ARM image lifetime')
    if type(one_shot) is not bool:
        raise ValueError('one-shot mode must be boolean')
    metadata = prepare(image, expected_sha256, work_dir, provenance=provenance)
    return _run_prepared(image, expected_sha256, work_dir, metadata, timeout=timeout, one_shot=one_shot)


def resume(parent_work_dir, work_dir, *, timeout=600, one_shot=False):
    vm._number(timeout, 30, 1200, 'custom ARM image lifetime')
    if type(one_shot) is not bool:
        raise ValueError('one-shot mode must be boolean')
    metadata = prepare_resume(parent_work_dir, work_dir)
    return _run_prepared(metadata['source_image'], metadata['source_image_sha256'], work_dir, metadata,
                         timeout=timeout, one_shot=one_shot)


def _run_prepared(image, expected_sha256, work_dir, metadata, *, timeout=600, one_shot=False):
    vm._linux(); vm._number(timeout, 30, 1200, 'custom ARM image lifetime')
    if type(one_shot) is not bool:
        raise ValueError('one-shot mode must be boolean')
    directory = vm._directory(work_dir)
    command = qemu_command(directory)
    started = time.monotonic(); reason = 'not started'; process = None; captures = []; failure = None
    shutdown_grace = min(120, max(5, timeout / 5))
    run_budget = timeout - shutdown_grace
    with ExitStack() as ownership:
        for ancestor in sorted(metadata.get('ancestor_work_directories', [])):
            ownership.enter_context(vm._lock(vm._directory(ancestor), timeout=0))
        ownership.enter_context(vm._lock(directory))
        _verify_backing_inputs(metadata)
        _verify_provenance_files(directory, metadata)
        for name in ('uefi-code.fd', 'uefi-vars.fd'):
            if vm._digest(directory / name) != metadata['firmware_sha256'][name]:
                raise ValueError('prepared UEFI code or variables changed before launch')
        _check_chain(directory / 'guest.qcow2', metadata['backing_inputs'], metadata['source_image_bytes'])
        try:
            with (directory / 'serial.log').open('xb') as serial:
                parent_pid = os.getpid()
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT,
                    start_new_session=True, preexec_fn=lambda: _owned_child(parent_pid))
                vm._write_json(directory / 'launch.json', {'format': 'DaOS-freebsd-image-launch-v1', 'command': command,
                    'pid': process.pid, 'process_group': process.pid, 'parent_death_signal': 'SIGTERM',
                    'process_start_ticks': _process_start_ticks(process.pid),
                    'input_metadata_sha256': vm._digest(directory / 'image.json'),
                    'source_sha256': metadata['source_sha256'], 'image_sha256': expected_sha256,
                    'UI_readiness_claimed': False, 'network': False, 'SSH': False, 'NoCloud': False})
                announced = False
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if os.fstat(serial.fileno()).st_size > vm.MAX_SERIAL:
                        raise RuntimeError('custom ARM image serial exceeded bound')
                    if elapsed >= run_budget:
                        reason = 'bounded lifetime complete'
                        break
                    if not announced and os.path.exists(directory / 'qmp.sock'):
                        qmp(directory, 'query-status')
                        announced = True
                        print(json.dumps({'format': 'DaOS-freebsd-image-running-v1', 'work_directory': str(directory),
                            'pid': process.pid, 'image_sha256': expected_sha256, 'QEMU_running': True,
                            'UI_readiness_claimed': False}, sort_keys=True), flush=True)
                    if announced and len(captures) < 3 and elapsed >= (len(captures) + 1) * min(60, run_budget / 4):
                        captures.append(screenshot(directory, 'auto-' + str(len(captures) + 1)))
                        print(json.dumps(captures[-1], sort_keys=True), flush=True)
                        if one_shot and len(captures) == 3:
                            reason = 'three bounded framebuffer observations complete'
                            break
                    time.sleep(.1)
                else:
                    reason = 'guest exited'
                if os.fstat(serial.fileno()).st_size > vm.MAX_SERIAL:
                    raise RuntimeError('custom ARM image serial exceeded bound after process exit')
                if process.poll() is not None and process.returncode:
                    raise RuntimeError('custom ARM image QEMU exited with an error')
        except BaseException as error:
            reason = type(error).__name__ + ': ' + str(error); failure = error
        finally:
            if process is not None and process.poll() is None:
                try:
                    qmp(directory, 'system_powerdown', timeout=2)
                    process.wait(timeout=max(1, min(shutdown_grace, timeout - (time.monotonic() - started))))
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    pass
                _stop_group(process)
            unchanged = vm._digest(image) == expected_sha256
            code_unchanged = vm._digest(directory / 'uefi-code.fd') == metadata['firmware_sha256']['uefi-code.fd']
            backing_unchanged, private_state, state_error = False, None, None
            try:
                backing_unchanged = _verify_backing_inputs(metadata)
                private_state = {'overlay': _file_record(directory / 'guest.qcow2'),
                                 'uefi_variables': _file_record(directory / 'uefi-vars.fd')}
            except (OSError, ValueError, RuntimeError) as error:
                state_error = type(error).__name__ + ': ' + str(error)
                if failure is None:
                    failure = RuntimeError('private COW state or immutable ancestor could not be sealed')
            serial_path = directory / 'serial.log'
            serial_size = serial_path.stat().st_size if serial_path.exists() else 0
            with serial_path.open('rb') if serial_path.exists() else tempfile.TemporaryFile() as serial_input:
                serial_data = serial_input.read(vm.MAX_SERIAL)
            serial_capped = serial_size > vm.MAX_SERIAL
            result = {'format': 'DaOS-freebsd-image-boot-observation-v1', 'completed': failure is None,
                'reason': reason, 'image_sha256': expected_sha256, 'source_image_unchanged': unchanged,
                'UEFI_code_unchanged': code_unchanged, 'process_returncode': process.returncode if process else None,
                'seconds': time.monotonic() - started, 'source_sha256': metadata['source_sha256'],
                'input_metadata_sha256': vm._digest(directory / 'image.json'),
                'provenance_sha256': metadata.get('provenance_sha256'),
                'backing_inputs_unchanged': backing_unchanged, 'private_state': private_state,
                'private_state_error': state_error, 'resume_parent': metadata.get('resume_parent'),
                'serial_sha256': hashlib.sha256(serial_data).hexdigest() if not serial_capped else None,
                'serial_prefix_sha256': hashlib.sha256(serial_data).hexdigest() if serial_capped else None,
                'serial_bytes': serial_size, 'serial_read_capped': serial_capped, 'framebuffer_observations': captures,
                'all_buffers_synced_marker_seen': b'All buffers synced.' in serial_data,
                'UI_readiness_claimed': False, 'requires_human_framebuffer_review': True,
                'network': False, 'host_mounts': False, 'host_shares': False, 'SSH': False, 'NoCloud': False}
            vm._write_json(directory / 'observation.json', result)
            if not unchanged or not code_unchanged:
                raise RuntimeError('immutable custom image or UEFI code changed')
        if failure is not None:
            raise failure
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('serve', 'boot'):
        command = sub.add_parser(name)
        command.add_argument('--image', type=Path, required=True)
        command.add_argument('--sha256', required=True)
        command.add_argument('--work-dir', type=Path, required=True)
        command.add_argument('--timeout', type=float, default=600)
        command.add_argument('--provenance', type=Path, required=True)
    command = sub.add_parser('resume')
    command.add_argument('--parent-work-dir', type=Path, required=True)
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--timeout', type=float, default=600)
    command.add_argument('--one-shot', action='store_true')
    for name in ('status', 'stop', 'screenshot', 'key', 'click'):
        command = sub.add_parser(name); command.add_argument('--work-dir', type=Path, required=True)
        if name == 'screenshot':
            command.add_argument('--name', required=True)
        elif name == 'key':
            command.add_argument('--keys', required=True)
        elif name == 'click':
            for field in ('x', 'y', 'width', 'height'):
                command.add_argument('--' + field, type=int, required=True)
    args = parser.parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda number, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.action == 'resume':
        result = resume(args.parent_work_dir, args.work_dir, timeout=args.timeout, one_shot=args.one_shot)
    elif args.action in ('serve', 'boot'):
        result = run(args.image, args.sha256, args.work_dir, timeout=args.timeout,
                     provenance=args.provenance, one_shot=args.action == 'boot')
    elif args.action == 'screenshot':
        result = screenshot(args.work_dir, args.name)
    elif args.action == 'key':
        result = send_key(args.work_dir, args.keys)
    elif args.action == 'click':
        result = click(args.work_dir, args.x, args.y, args.width, args.height)
    else:
        result = qmp(args.work_dir, 'query-status' if args.action == 'status' else 'system_powerdown')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

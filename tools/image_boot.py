"""Bounded isolated full-system ARM64 image boot and persistent UI verification.

Only new regular image files produced by image_build are attached. Root is
read-only, /data is a separate private writable image, and networking/host
filesystem sharing are absent. No host mounts, root or package installs occur.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_SERIAL = 8 * 1024**2


def digest(stream):
    stream.seek(0)
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(block)
    stream.seek(0)
    return result.hexdigest()


def pinned(path, *, writable=False):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('regular pinned image input required')
    # NONBLOCK also closes a swap-to-FIFO race between lstat and open. The
    # descriptor is still accepted only after regular-file identity checks.
    descriptor = os.open(path, (os.O_RDWR if writable else os.O_RDONLY) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(current.st_mode) or
                (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError('regular pinned image input required')
        return os.fdopen(descriptor, 'r+b' if writable else 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def check_ext4(stream, expected_uuid):
    stream.seek(1024)
    header = stream.read(128)
    stream.seek(0)
    if len(header) != 128 or header[56:58] != b'\x53\xef' or header[104:120] != uuid.UUID(expected_uuid).bytes:
        raise ValueError('expected owned ext4 image identity is missing')


def qemu_command(executable, files, phase, *, memory_mib=1536):
    if phase not in ('first', 'resume'):
        raise ValueError('boot phase must be first or resume')
    if type(memory_mib) is not int or not 256 <= memory_mib <= 4096:
        raise ValueError('guest memory must be bounded within 256-4096 MiB')
    return [str(executable), '-machine', 'virt,gic-version=3', '-cpu', 'cortex-a53', '-smp', '2',
            '-m', str(memory_mib), '-accel', 'tcg', '-nodefaults', '-display', 'none',
            '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',
            '-kernel', files['kernel'], '-initrd', files['bootstrap'],
            '-append', 'console=ttyAMA0 panic=-1 HASSLEFREE_ARM64_IMAGE=1 HF_IMAGE_PHASE=' + phase + ' rdinit=/init',
            '-drive', 'file=' + files['root'] + ',format=raw,if=none,id=hfroot,readonly=on',
            '-device', 'virtio-blk-pci,drive=hfroot',
            '-drive', 'file=' + files['data'] + ',format=raw,if=none,id=hfdata,cache=writeback',
            '-device', 'virtio-blk-pci,drive=hfdata']


def parse_serial(data, phase):
    if not isinstance(data, bytes) or len(data) > MAX_SERIAL or phase not in ('first', 'resume'):
        raise ValueError('invalid bounded guest serial input')
    text = data.decode('utf-8', errors='replace').replace('\r', '')
    required = [r'^HF_IMAGE_INIT pid=1 machine=aarch64 phase=' + phase + r' kernel=.+$',
                r'^HF_IMAGE_ROOT readonly=1 filesystem=ext4 device=vda$',
                r'^HF_IMAGE_DATA readwrite=1 filesystem=ext4 device=vdb$',
                r'^HF_IMAGE_RUNTIME uid=1000 phase=' + phase + r' python=/usr/bin/python3$',
                r'^HF_IMAGE_CHILD phase=' + phase + r' status=0$',
                r'^HF_IMAGE_DATA_UNMOUNTED clean=1$',
                r'^HF_IMAGE_RESULT PASS phase=' + phase + r' immutable-rootfs separate-data synthetic-only$']
    if any(len(re.findall(pattern, text, re.M)) != 1 for pattern in required) or re.search(r'^HF_IMAGE_RESULT FAIL', text, re.M):
        raise ValueError('guest did not uniquely confirm the full image boot sequence')
    for prefix in ('HF_IMAGE_INIT ', 'HF_IMAGE_ROOT ', 'HF_IMAGE_DATA ', 'HF_IMAGE_RUNTIME ',
                   'HF_IMAGE_CHILD ', 'HF_IMAGE_DATA_UNMOUNTED ', 'HF_IMAGE_RESULT '):
        if len(re.findall('^' + re.escape(prefix), text, re.M)) != 1:
            raise ValueError('conflicting or repeated guest boot marker')
    matches = re.findall(r'^HF_IMAGE_UI_RESULT (.+)$', text, re.M)
    if len(matches) != 1 or len(matches[0]) > 65536:
        raise ValueError('missing or repeated bounded ARM Qt result')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('duplicate guest result key')
            value[key] = item
        return value
    def finite(text):
        value = float(text)
        if not math.isfinite(value): raise ValueError('nonfinite guest result')
        return value
    def constant(_): raise ValueError('nonfinite guest result')
    value = json.loads(matches[0], object_pairs_hook=unique, parse_float=finite, parse_constant=constant)
    if (not isinstance(value, dict) or value.get('phase') != phase or value.get('passed') is not True or
            value.get('report') != phase + '.json' or type(value.get('journal_rows')) is not int or value['journal_rows'] != 1 or
            value.get('native_process_closed') is not True or
            not isinstance(value.get('report_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['report_sha256'])):
        raise ValueError('guest Qt result lacks required identities and completion')
    request_id = value.get('request_id')
    if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
        raise ValueError('guest request ID is not a canonical UUID')
    return value


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def _png(path):
    with Path(path).open('rb') as source: header = source.read(24)
    if len(header) != 24 or not header.startswith(b'\x89PNG\r\n\x1a\n') or header[12:16] != b'IHDR':
        raise ValueError('guest output is not a PNG')
    width, height = struct.unpack_from('>II', header, 16)
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError('guest PNG dimensions exceed bound')
    return [width, height]


def boot(build_dir, phase, *, timeout=330):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('image boot requires a non-root Linux host')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError('boot timeout must be finite and within 1-600 seconds')
    from tools.image_build import DATA_UUID, ROOT_UUID, regular, sha, write_json
    from tools.image_fs import extract_file, _run
    from camera.dng.inspect import inspect_dng
    directory = Path(build_dir).resolve(strict=True)
    if not directory.is_relative_to((ROOT / 'build').resolve()):
        raise ValueError('image build directory must be beneath this workspace build')
    manifest_path = regular(directory / 'build-evidence.json')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 'hf-arm64-image-build-v1' or manifest.get('uid') != 1000:
        raise ValueError('verified full image build evidence required')
    for relative, expected in manifest.get('image_input_sha256', {}).items():
        if not (ROOT / relative).resolve().is_relative_to(ROOT) or sha(ROOT / relative) != expected:
            raise ValueError('image bootstrap/build inputs changed; use a new build')
    if phase not in ('first', 'resume'):
        raise ValueError('boot phase must be first or resume')
    prior = None
    if phase == 'resume':
        prior = json.loads(regular(directory / 'boots/first/boot-evidence.json').read_text())
        if prior.get('passed') is not True or prior.get('rootfs_unchanged') is not True:
            raise ValueError('resume requires a completed first boot')
    runs = directory / 'boots'
    runs.mkdir(exist_ok=True)
    output = runs / phase
    if os.path.lexists(output): raise ValueError('boot output must be NEW; never reuse a failed or completed phase')
    lock_path = directory / '.image-boot.lock'
    lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.close(lock)
    process = None
    try:
        output.mkdir(mode=0o700)
        with ExitStack() as stack:
            files = {name: stack.enter_context(pinned(directory / filename, writable=name == 'data')) for name, filename in
                     [('root', 'rootfs.ext4'), ('data', 'data.ext4'), ('kernel', 'kernel.img'), ('bootstrap', 'bootstrap.cpio.gz')]}
            check_ext4(files['root'], ROOT_UUID); check_ext4(files['data'], DATA_UUID)
            before = {name: digest(stream) for name, stream in files.items()}
            expected_data = prior['data_sha256_after'] if prior else manifest['data_image']['sha256']
            if (before['root'] != manifest['rootfs_image']['sha256'] or before['data'] != expected_data or
                    before['kernel'] != manifest['kernel_sha256'] or before['bootstrap'] != manifest['bootstrap_sha256']):
                raise ValueError('image inputs differ from build or previous clean boot; no automatic retry')
            executable = shutil.which('qemu-system-aarch64')
            if not executable: raise RuntimeError('installed ARM64 full-system QEMU is required')
            descriptors = tuple(stream.fileno() for stream in files.values())
            arguments = {name: '/proc/self/fd/' + str(stream.fileno()) for name, stream in files.items()}
            command = qemu_command(executable, arguments, phase)
            serial_path = output / 'serial.log'
            with serial_path.open('xb') as serial:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, pass_fds=descriptors)
                problem = []
                def collect():
                    total = 0
                    try:
                        while True:
                            block = process.stdout.read(4096)
                            if not block: break
                            total += len(block)
                            if total > MAX_SERIAL:
                                problem.append('guest serial exceeded bound'); process.terminate(); break
                            serial.write(block); serial.flush()
                    except (OSError, ValueError) as error: problem.append(type(error).__name__)
                reader = threading.Thread(target=collect, name='arm64-image-serial', daemon=True)
                reader.start()
                started = time.monotonic()
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('bounded guest boot timed out; partial data image retained, never replayed') from error
                finally:
                    _stop(process)
                    reader.join(timeout=5)
                    process.stdout.close()
                if reader.is_alive() or problem or code != 0:
                    raise RuntimeError('guest boot failed; see retained serial.log')
            compact = parse_serial(serial_path.read_bytes(), phase)
            after = {name: digest(stream) for name, stream in files.items()}
            if before['root'] != after['root'] or before['kernel'] != after['kernel'] or before['bootstrap'] != after['bootstrap']:
                raise RuntimeError('immutable guest input changed during boot')
        fsck = shutil.which('e2fsck')
        if not fsck: raise RuntimeError('installed read-only filesystem checker required')
        with pinned(directory / 'data.ext4') as data:
            checked = _run([fsck, '-fn', '/proc/self/fd/' + str(data.fileno())],
                           pass_fds=(data.fileno(),), timeout=60)
        if checked['returncode']:
            raise RuntimeError('persistent data filesystem did not verify clean after shutdown')
        extracted = output / 'guest'
        extracted.mkdir()
        reports = {}
        descriptor = extract_file(directory / 'data.ext4', '/ui-r7/' + phase + '.json', extracted / (phase + '.json'), 256 * 1024)
        if descriptor['sha256'] != compact['report_sha256']:
            raise ValueError('extracted guest report differs from serial commitment')
        report = json.loads((extracted / (phase + '.json')).read_text())
        if (report.get('passed') is not True or report.get('phase') != phase or report.get('execution_mode') != 'qemu-system' or
                report.get('machine') != 'aarch64' or report.get('effective_uid') != 1000 or
                report.get('request_id') != compact['request_id'] or report.get('capture_id') != compact['request_id'] or
                report.get('frame_id') != compact['request_id'] or report.get('capture_status') != 'complete' or
                report.get('journal_rows') != 1 or report.get('native_process_closed') is not True or
                report.get('qt_platform') != 'offscreen' or report.get('qt_quick_backend') != 'software'):
            raise ValueError('guest report does not establish actual normal ARM64 Qt execution')
        for image in [*report['screenshots'], report['thumbnail']]:
            name = image['file']
            if not isinstance(name, str) or not re.fullmatch(re.escape(phase) + r'-[a-z-]+\.png', name):
                raise ValueError('unsafe guest PNG name')
            metadata = extract_file(directory / 'data.ext4', '/ui-r7/' + name, extracted / name, 16 * 1024**2)
            if metadata['sha256'] != image['sha256']:
                raise ValueError('guest PNG hash mismatch')
            metadata['dimensions'] = _png(extracted / name)
            reports[name] = metadata
        for guest, host, expected in [('/ui-r7/session/photos/' + compact['request_id'] + '.dng', phase + '-source.dng', report['dng_sha256']),
                                       ('/ui-r7/' + phase + '-copy.dng', phase + '-copy.dng', report['export_sha256'])]:
            metadata = extract_file(directory / 'data.ext4', guest, extracted / host, 64 * 1024**2)
            if metadata['sha256'] != expected or inspect_dng(extracted / host)['request_id'] != compact['request_id']:
                raise ValueError('guest DNG hash or capture identity mismatch')
            reports[host] = metadata
        if report['dng_sha256'] != report['export_sha256']:
            raise ValueError('guest retained export changed bytes')
        if prior and (prior['request_id'] != compact['request_id'] or report.get('restart_same_id_without_recapture') is not True):
            raise ValueError('second boot did not reconcile the original capture')
        version = _run([executable, '--version'], timeout=10)
        if version['returncode'] or not version['stdout'].splitlines():
            raise RuntimeError('QEMU version probe did not complete')
        result = {'format': 'hf-arm64-image-boot-v1', 'passed': True, 'phase': phase, 'request_id': compact['request_id'],
            'rootfs_unchanged': True, 'rootfs_sha256_before': before['root'], 'rootfs_sha256_after': after['root'],
            'data_sha256_before': before['data'], 'data_sha256_after': after['data'],
            'persistent_same_request_id': bool(prior), 'guest_report': report, 'extracted': reports,
            'build_evidence_sha256': sha(manifest_path), 'boot_script_sha256': sha(Path(__file__)),
            'qemu_version': version['stdout'].splitlines()[0],
            'command': command, 'seconds': time.monotonic() - started, 'serial_sha256': sha(serial_path),
            'data_fsck': checked,
            'host_mounts': False, 'network': False, 'host_filesystem_sharing': False,
            'qemu_inherited_process_group': True, 'scope': 'generic full-system virtual ARM64, software camera only'}
        write_json(output / 'boot-evidence.json', result)
        return result
    finally:
        if process is not None: _stop(process)
        lock_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('boot')
    command.add_argument('--build-dir', type=Path, required=True)
    command.add_argument('--phase', choices=('first', 'resume'), required=True)
    command.add_argument('--timeout', type=float, default=330)
    args = parser.parse_args(argv)
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = boot(args.build_dir, args.phase, timeout=args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    if __package__ in (None, ''): sys.path.insert(0, str(ROOT))
    main()

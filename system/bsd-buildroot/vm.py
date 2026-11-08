#!/usr/bin/env python3
"""Run an evidence-checked DaOS NetBSD image in its own ARM64 QEMU instance."""
from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = '/home/dy09/daos-bsd-buildroot-ui'
DEFAULT_RUN = '/home/dy09/daos-netbsd-vm'
RUN_FORMAT = 'DaOS-NetBSD-QEMU-run-v1'
HEX64 = re.compile(r'[0-9a-f]{64}')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def plain_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symlink path components are not allowed: ' + str(path))
    return path


def read(path):
    path = plain_path(path)
    if not path.is_file():
        raise ValueError('A regular evidence file is required: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Evidence must be a JSON object: ' + str(path))
    return value


def save(path, data):
    path = plain_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(data, indent=2, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run_directory(path, *, new=False):
    path = plain_path(path)
    protected = ('/usr', '/etc', '/bin', '/sbin', '/lib', '/lib64', '/boot',
                 '/var', '/dev', '/proc', '/sys', '/run', '/opt')
    if (len(path.parts) < 4 or path == Path.home()
            or any(Path(root) == path or Path(root) in path.parents for root in protected)):
        raise ValueError('Use a private VM run directory: ' + str(path))
    if any(character in str(path) for character in ',\n\r\x00'):
        raise ValueError('VM directory contains a QEMU option delimiter')
    for name in ('serial.sock', 'qmp.sock'):
        if len(os.fsencode(path / name)) >= 104:
            raise ValueError('VM directory is too long for Unix sockets')
    if new:
        if path.exists():
            raise ValueError('Use a new run directory; previous disks and receipts are preserved')
    else:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Existing VM directory must be private and owned by this user')
    return path


def image_path(explicit, *, workspace=None):
    if explicit is not None:
        return plain_path(explicit)
    # Assembly directories have unique names. Resolve the successful state
    # receipt, then its exact artifact path instead of guessing a filename.
    state = read(Path(workspace or DEFAULT_WORKSPACE) / 'state/image.json')
    receipt = read(state['receipt'])
    if state.get('image') != receipt.get('image'):
        raise ValueError('Image state differs from its assembly receipt')
    return plain_path(receipt['image']['path'])


def _hex(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError(label + ' must be a SHA256 digest')
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + ' must be a JSON object')
    return value


def file_stamp(path):
    info = Path(path).stat()
    return {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns}


def validate_image(path, *, allow_relocated=False):
    path = plain_path(path)
    if not path.is_file():
        raise ValueError('A regular raw image is required')
    evidence_path = path.parent / 'image-evidence.json'
    evidence = read(evidence_path)
    if (evidence.get('format') != 'DaOS-NetBSD-image-v1'
            or evidence.get('status') != 'assembled-not-boot-validated'):
        raise ValueError('A completed DaOS NetBSD image assembly receipt is required')
    artifact = _object(evidence.get('image'), 'Image artifact')
    expected = _hex(artifact.get('sha256'), 'Image SHA256')
    assembly_path = artifact.get('path')
    if not isinstance(assembly_path, str) or not Path(assembly_path).is_absolute():
        raise ValueError('Assembly evidence must identify its original absolute image path')
    relocated = Path(os.path.abspath(assembly_path)) != path
    if relocated and not allow_relocated:
        raise ValueError('Image path differs from its assembly receipt')
    target = _object(_object(evidence.get('profile'), 'Image profile').get('target'), 'Image target')
    if target.get('machine') != 'evbarm' or target.get('architecture') != 'aarch64':
        raise ValueError('An evbarm/aarch64 NetBSD image is required')
    _hex(evidence.get('source_lock_sha256'), 'Source lock')
    _hex(evidence.get('input_hash'), 'Image build inputs')
    kernel = _object(evidence.get('kernel'), 'Image kernel')
    _hex(kernel.get('sha256'), 'Source-built kernel')
    if _object(kernel.get('elf'), 'Kernel ELF').get('machine') != 'AArch64':
        raise ValueError('Image kernel evidence is not AArch64')
    stages = _object(evidence.get('base_stage_receipts'), 'Source build stages')
    for stage in ('tools', 'distribution', 'kernel'):
        value = stages.get(stage)
        _hex(value.get('sha256') if isinstance(value, dict) else value,
             'Source build stage ' + stage)
    before = file_stamp(path)
    if artifact.get('size') != before['size'] or sha(path) != expected or file_stamp(path) != before:
        raise ValueError('Raw image changed or differs from its assembly receipt')
    return {'raw_image': str(path), 'assembly_image_path': assembly_path,
            'artifact_relocated': relocated, 'raw_sha256': expected, 'raw_stamp': before,
            'evidence_sha256': sha(evidence_path), 'evidence': evidence}


def socket_path(path):
    path = plain_path(path)
    info = path.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('A private Unix socket belonging to this user is required')
    return path


class QMP:
    def __init__(self, path, *, expected_pid=None):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.stream = None
        try:
            self.socket.settimeout(10)
            self.socket.connect(str(socket_path(path)))
            if expected_pid is not None:
                peer_pid, peer_uid, _ = struct.unpack('3i', self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if peer_pid != expected_pid or peer_uid != os.getuid():
                    raise ValueError('QMP peer process differs from the recorded QEMU process')
            self.stream = self.socket.makefile('rb', buffering=0)
            greeting = self.read()
            if (not isinstance(greeting.get('QMP'), dict)
                    or not isinstance(greeting['QMP'].get('version'), dict)
                    or not isinstance(greeting['QMP'].get('capabilities'), list)):
                raise ValueError('Invalid QMP greeting')
            self.call('qmp_capabilities')
        except BaseException:
            self.close()
            raise

    def read(self):
        line = self.stream.readline(1024 * 1024 + 1)
        if not line:
            raise RuntimeError('QMP connection closed')
        if len(line) > 1024 * 1024 or not line.endswith(b'\n'):
            raise ValueError('QMP response exceeds the bounded message size')
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('QMP message must be a JSON object')
        return value

    def call(self, name, arguments=None):
        request = {'execute': name, 'id': uuid.uuid4().hex}
        if arguments is not None:
            request['arguments'] = arguments
        self.socket.sendall((json.dumps(request) + '\r\n').encode())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.socket.settimeout(max(.01, deadline - time.monotonic()))
            response = self.read()
            if 'event' in response and 'id' not in response:
                continue
            if response.get('id') != request['id']:
                raise ValueError('QMP response ID differs from the command ID')
            if 'error' in response:
                raise RuntimeError(str(response['error']))
            if 'return' not in response:
                raise ValueError('QMP response contains neither return nor error')
            return response['return']
        raise TimeoutError('QMP response deadline expired')

    def close(self):
        if self.stream is not None:
            self.stream.close()
        self.socket.close()


def process_stamp(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise ValueError('Invalid recorded process ID')
    directory = Path('/proc') / str(pid)
    fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'start_ticks': int(fields[19]),
            'executable': str((directory / 'exe').resolve(strict=True))}


def read_record(directory):
    record = read(directory / 'vm.json')
    if (record.get('format') != RUN_FORMAT or record.get('run_directory') != str(directory)
            or not re.fullmatch(r'DaOS-BSD-source-[0-9a-f]{12}', record.get('name', ''))):
        raise ValueError('Invalid DaOS QEMU run record')
    if not isinstance(record.get('uuid'), str) or str(uuid.UUID(record['uuid'])) != record['uuid']:
        raise ValueError('Invalid QEMU instance UUID')
    return record


def identity(directory):
    directory = run_directory(directory)
    record = read_record(directory)
    if record.get('status') != 'running':
        raise ValueError('This recorded QEMU instance is not running')
    stamp = process_stamp(record.get('qemu_pid'))
    if stamp != record.get('qemu_process') or stamp['executable'] != record.get('qemu_executable'):
        raise ValueError('Recorded QEMU process exited or its PID was reused')
    command = (Path('/proc') / str(record['qemu_pid']) / 'cmdline').read_bytes().split(b'\0')
    if [os.fsdecode(word) for word in command if word] != record.get('argv'):
        raise ValueError('Recorded QEMU command line differs from the live process')
    if int(plain_path(directory / 'qemu.pid').read_text().strip()) != record['qemu_pid']:
        raise ValueError('QEMU pidfile differs from its run record')
    client = QMP(directory / 'qmp.sock', expected_pid=record['qemu_pid'])
    try:
        if _object(client.call('query-name'), 'QMP query-name').get('name') != record['name']:
            raise ValueError('QMP instance name differs from the run record')
        if _object(client.call('query-uuid'), 'QMP query-uuid').get('UUID') != record['uuid']:
            raise ValueError('QMP instance UUID differs from the run record')
        return record, client
    except BaseException:
        client.close()
        raise


def _program(name):
    value = shutil.which(name)
    if not value:
        raise ValueError('Required host program is missing: ' + name)
    return str(Path(value).resolve(strict=True))


def _socket_support(directory):
    probe = directory / 'socket-test.sock'
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.bind(str(probe))
    except OSError as error:
        raise ValueError('VM run directory must support Unix sockets; use the F-backed WSL ext4 filesystem') from error
    finally:
        connection.close()
        if probe.exists():
            probe.unlink()


def overlay_info(directory, record):
    overlay = plain_path(directory / 'disk.qcow2')
    if not overlay.is_file():
        raise ValueError('The private writable VM overlay is missing')
    descriptor = json.loads(subprocess.check_output(
        [record['qemu_img_executable'], 'info', '--output=json', str(overlay)], text=True, timeout=30))
    if (descriptor.get('format') != 'qcow2'
            or descriptor.get('full-backing-filename') != record['raw_image']
            or descriptor.get('backing-filename-format') != 'raw'):
        raise ValueError('QEMU writable overlay has an unexpected backing image')
    return descriptor


def persistent_state(directory):
    result = {}
    for name in ('disk.qcow2', 'uefi-vars.fd'):
        path = plain_path(directory / name)
        if not path.is_file():
            raise ValueError('Persistent VM file is missing: ' + str(path))
        before = file_stamp(path)
        checksum = sha(path)
        if file_stamp(path) != before:
            raise ValueError('Persistent VM file changed while recording it: ' + str(path))
        result[name] = {'size': before['size'], 'sha256': checksum}
    return result


def same_image(args, record):
    validated = validate_image(image_path(args.image, workspace=args.workspace),
                               allow_relocated=args.image is not None)
    for field in ('raw_image', 'raw_sha256', 'evidence_sha256'):
        if validated[field] != record.get(field):
            raise ValueError('The latest/requested image differs from this VM; use a new --run-dir')
    return validated


def await_running(directory):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = read_record(directory)
        if current['status'] in ('failed', 'exited'):
            raise RuntimeError('QEMU did not remain running; see ' + str(directory / 'supervisor.log'))
        if current['status'] == 'running' and (directory / 'qmp.sock').exists() and (directory / 'qemu.pid').exists():
            verified, client = identity(directory)
            try:
                result = {'run_directory': str(directory), 'name': verified['name'],
                          'uuid': verified['uuid'], 'boot_number': verified.get('boot_number', 1),
                          'qemu': client.call('query-status'), 'guest_boot_validated': False,
                          'serial_log': str(directory / 'serial.log')}
                print(json.dumps(result, indent=2))
                return result
            finally:
                client.close()
        time.sleep(.2)
    raise RuntimeError('QEMU startup deadline expired; run status before retrying, see supervisor.log')


def start(args):
    directory = run_directory(args.run_dir, new=True)
    validated = validate_image(image_path(args.image, workspace=args.workspace),
                               allow_relocated=args.image is not None)
    qemu, qemu_img = _program('qemu-system-aarch64'), _program('qemu-img')
    firmware = Path('/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd')
    variables = Path('/usr/share/AAVMF/AAVMF_VARS.fd')
    if not firmware.is_file() or not variables.is_file():
        raise ValueError('Install qemu-efi-aarch64/AAVMF firmware first')
    directory.mkdir(parents=True, mode=0o700)
    record = {'format': RUN_FORMAT, 'status': 'preparing',
              'name': 'DaOS-BSD-source-' + uuid.uuid4().hex[:12], 'uuid': str(uuid.uuid4()),
              'boot_number': 1,
              'run_directory': str(directory), 'created_at_unix': time.time(), **validated,
              'firmware_sha256': sha(firmware), 'variables_template_sha256': sha(variables),
              'qemu_executable': qemu, 'qemu_img_executable': qemu_img, 'visible': args.visible,
              'platform': 'QEMU virt / AArch64', 'physical_Pi4_validation': False,
              'guest_boot_validated': False}
    save(directory / 'vm.json', record)
    supervisor = None
    try:
        _socket_support(directory)
        shutil.copyfile(firmware, directory / 'uefi-code.fd')
        shutil.copyfile(variables, directory / 'uefi-vars.fd')
        with (directory / 'qemu-img.log').open('xb') as log:
            subprocess.run([qemu_img, 'create', '-f', 'qcow2', '-F', 'raw', '-b',
                            validated['raw_image'], str(directory / 'disk.qcow2')],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        descriptor = overlay_info(directory, record)
        record.update(status='prepared', overlay=descriptor)
        save(directory / 'vm.json', record)
        with (directory / 'supervisor.log').open('xb') as log:
            command = [sys.executable, str(HERE / 'vm.py'), '_run', '--run-dir', str(directory)]
            supervisor = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        save(directory / 'launch.json', {'supervisor_pid': supervisor.pid,
                                        'command': command, 'created_at_unix': time.time()})
    except BaseException as error:
        if supervisor is None:
            record.update(status='failed', error=str(error), failed_at_unix=time.time())
            save(directory / 'vm.json', record)
        else:
            # Once launched, only the supervisor owns vm.json. Preserve its
            # running/exited receipt even if the parent cannot save launch.json.
            save(directory / 'launch-error.json', {'supervisor_pid': supervisor.pid,
                                                  'error': str(error), 'at_unix': time.time()})
        raise
    return await_running(directory)


def resume(args):
    directory = run_directory(args.run_dir)
    import fcntl
    with plain_path(directory / 'control.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another VM resume/launch operation is using this run') from error
        try:
            return _resume_locked(args, directory)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _resume_locked(args, directory):
    record = read_record(directory)
    if record.get('status') != 'exited':
        raise ValueError('Resume requires a recorded, stopped QEMU instance')
    if record.get('qemu_exit_code') != 0:
        raise ValueError('Resume requires a clean recorded QEMU exit; inspect this failed boot first')
    try:
        alive = process_stamp(record.get('qemu_pid'))
    except (FileNotFoundError, ProcessLookupError):
        alive = None
    if alive is not None and alive == record.get('qemu_process'):
        raise ValueError('The recorded QEMU process is still alive')
    validated = same_image(args, record)
    if (record.get('qemu_executable') != _program('qemu-system-aarch64')
            or record.get('qemu_img_executable') != _program('qemu-img')):
        raise ValueError('Recorded QEMU program paths differ from the installed host tools')
    if sha(plain_path(directory / 'uefi-code.fd')) != record['firmware_sha256']:
        raise ValueError('Private UEFI firmware changed after the previous boot')
    if persistent_state(directory) != record.get('persistent_state'):
        raise ValueError('The stopped writable disk or UEFI variables differ from the shutdown receipt')
    descriptor = overlay_info(directory, record)
    previous_boot = record.get('boot_number', 1)
    if not isinstance(previous_boot, int) or isinstance(previous_boot, bool) or previous_boot < 1:
        raise ValueError('Invalid recorded boot number')
    history = plain_path(directory / 'boots' / (f'{previous_boot:04d}-' + uuid.uuid4().hex[:12]))
    history.mkdir(parents=True)
    archive = {'format': 'DaOS-QEMU-boot-history-v1', 'status': 'archiving',
               'boot_number': previous_boot, 'previous_record_sha256': sha(directory / 'vm.json'),
               'persistent_state': record['persistent_state'], 'moves': []}
    save(history / 'history.json', archive)
    names = ('vm.json', 'qemu.log', 'serial.log', 'supervisor.log', 'qemu-img.log',
             'launch.json', 'launch-error.json', 'supervisor.lock', 'qemu.pid', 'qmp.sock', 'serial.sock')
    for name in names:
        source = plain_path(directory / name)
        if source.exists():
            os.rename(source, history / name)
            archive['moves'].append(name)
            save(history / 'history.json', archive)
    archive.update(status='archived', archived_at_unix=time.time())
    save(history / 'history.json', archive)
    resumed = dict(record, **validated, status='prepared',
                   name='DaOS-BSD-source-' + uuid.uuid4().hex[:12],
                   boot_number=previous_boot + 1, previous_boot_receipt=str(history / 'vm.json'),
                   boot_prepared_at_unix=time.time(), visible=args.visible or record.get('visible', False),
                   overlay=descriptor, guest_boot_validated=False)
    for key in ('qemu_pid', 'qemu_process', 'argv', 'qemu_exit_code', 'exited_at_unix',
                'supervisor_pid', 'supervisor_process', 'qemu_started_at_unix', 'error', 'failed_at_unix'):
        resumed.pop(key, None)
    save(directory / 'vm.json', resumed)
    supervisor = None
    try:
        with (directory / 'supervisor.log').open('xb') as log:
            command = [sys.executable, str(HERE / 'vm.py'), '_run', '--run-dir', str(directory)]
            supervisor = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        save(directory / 'launch.json', {'supervisor_pid': supervisor.pid, 'command': command,
                                        'boot_number': resumed['boot_number'], 'created_at_unix': time.time()})
    except BaseException as error:
        if supervisor is None:
            resumed.update(status='failed', error=str(error), failed_at_unix=time.time())
            save(directory / 'vm.json', resumed)
        else:
            save(directory / 'launch-error.json', {'supervisor_pid': supervisor.pid,
                                                  'error': str(error), 'at_unix': time.time()})
        raise
    return await_running(directory)


def launch(args):
    directory = plain_path(args.run_dir)
    if not directory.exists():
        return start(args)
    directory = run_directory(directory)
    record = read_record(directory)
    if record.get('status') == 'running':
        same_image(args, record)
        verified, client = identity(directory)
        try:
            result = {'run_directory': str(directory), 'reused_running': True,
                      'boot_number': verified.get('boot_number', 1),
                      'name': verified['name'], 'uuid': verified['uuid'],
                      'qemu': client.call('query-status')}
            print(json.dumps(result, indent=2))
            return result
        finally:
            client.close()
    if record.get('status') == 'exited':
        return resume(args)
    raise ValueError('This run is not safely stopped; inspect status and its receipts before retrying')


def qemu_command(directory, record):
    return [record['qemu_executable'], '-name', record['name'], '-uuid', record['uuid'],
            '-machine', 'virt,gic-version=3', '-accel', 'tcg,thread=multi', '-cpu', 'cortex-a72',
            '-smp', '4', '-m', '4096', '-nodefaults', '-display', 'gtk' if record['visible'] else 'none',
            '-pidfile', str(directory / 'qemu.pid'),
            '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off',
            '-monitor', 'none', '-no-reboot', '-nic', 'none',
            '-chardev', 'socket,id=daosserial,path=' + str(directory / 'serial.sock') +
            ',server=on,wait=off,logfile=' + str(directory / 'serial.log'), '-serial', 'chardev:daosserial',
            '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=' + str(directory / 'uefi-code.fd'),
            '-drive', 'if=pflash,format=raw,unit=1,file=' + str(directory / 'uefi-vars.fd'),
            '-drive', 'file=' + str(directory / 'disk.qcow2') + ',format=qcow2,if=none,id=release',
            '-device', 'virtio-blk-pci,drive=release', '-device', 'ramfb',
            '-device', 'qemu-xhci,id=usb', '-device', 'usb-kbd,bus=usb.0',
            '-device', 'usb-mouse,bus=usb.0', '-object', 'rng-random,filename=/dev/urandom,id=rng0',
            '-device', 'virtio-rng-pci,rng=rng0',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def supervise(args):
    directory = run_directory(args.run_dir)
    record = read_record(directory)
    if record.get('status') != 'prepared':
        raise ValueError('Supervisor can launch only a freshly prepared run')
    with (directory / 'supervisor.lock').open('x') as lock:
        lock.write(str(os.getpid()) + '\n')
    try:
        record.update(status='starting', supervisor_pid=os.getpid(),
                      supervisor_process=process_stamp(os.getpid()))
        save(directory / 'vm.json', record)
        if file_stamp(plain_path(record['raw_image'])) != record['raw_stamp']:
            raise ValueError('Backing image changed after validation')
        if sha(directory / 'uefi-code.fd') != record['firmware_sha256']:
            raise ValueError('Private firmware code differs from its preparation receipt')
        command = qemu_command(directory, record)
        with (directory / 'qemu.log').open('xb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            record.update(status='running', qemu_pid=process.pid,
                          qemu_process=process_stamp(process.pid), argv=command,
                          qemu_started_at_unix=time.time())
            save(directory / 'vm.json', record)
            code = process.wait()
        record.update(status='exited', qemu_exit_code=code, exited_at_unix=time.time(),
                      persistent_state=persistent_state(directory))
        save(directory / 'vm.json', record)
    except BaseException as error:
        record.update(status='failed', error=str(error), failed_at_unix=time.time())
        save(directory / 'vm.json', record)
        raise


def windows_path(value, *, cwd=None):
    value = os.fspath(value)
    if value.startswith('/') and not value.startswith('//'):
        return value
    if not re.match(r'^[A-Za-z]:[\\/]', value):
        if ntpath.splitdrive(value)[0] or value.startswith(('\\', '//')):
            raise ValueError('Use an absolute Windows drive path or an absolute WSL path')
        value = ntpath.join(cwd or os.getcwd(), value)
    if not re.match(r'^[A-Za-z]:[\\/]', value):
        raise ValueError('Cannot convert this path into WSL: ' + value)
    value = ntpath.normpath(value)
    return '/mnt/' + value[0].lower() + '/' + value[3:].replace('\\', '/')


def wsl_command(args, *, source=None, cwd=None):
    command = ['wsl', '-d', args.distro, '--exec', 'python3',
               windows_path(source or str(HERE / 'vm.py'), cwd=cwd), args.action,
               '--run-dir', windows_path(args.run_dir, cwd=cwd),
               '--workspace', windows_path(args.workspace, cwd=cwd)]
    if args.image is not None:
        command += ['--image', windows_path(args.image, cwd=cwd)]
    if args.output is not None:
        command += ['--output', windows_path(args.output, cwd=cwd)]
    if args.visible:
        command.append('--visible')
    if args.text is not None:
        command += ['--text', args.text]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'resume', 'launch', '_run', 'status', 'stop', 'screenshot', 'console'))
    # Keep path strings until after Windows delegation. WindowsPath would turn
    # a /home/... default into \\home\\..., losing its WSL meaning.
    parser.add_argument('--image', help='Defaults to the successful image assembly state receipt')
    parser.add_argument('--workspace', default=DEFAULT_WORKSPACE)
    parser.add_argument('--run-dir', default=DEFAULT_RUN)
    parser.add_argument('--visible', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--text')
    parser.add_argument('--distro', default='Debian')
    args = parser.parse_args()
    if os.name == 'nt':
        raise SystemExit(subprocess.call(wsl_command(args)))
    if args.action == 'start':
        return start(args)
    if args.action == 'resume':
        return resume(args)
    if args.action == 'launch':
        return launch(args)
    if args.action == '_run':
        return supervise(args)
    directory = run_directory(args.run_dir)
    if args.action == 'status':
        result = read_record(directory)
        if result['status'] == 'running':
            _, client = identity(directory)
            try:
                result['qemu_status'] = client.call('query-status')
            finally:
                client.close()
        print(json.dumps(result, indent=2))
        return
    if args.action == 'console':
        if args.text is None:
            parser.error('console requires --text')
        payload = args.text.encode('utf-8')
        if len(payload) > 65536:
            raise ValueError('Console input exceeds 64 KiB')
        record, client = identity(directory)
        client.close()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(socket_path(directory / 'serial.sock')))
            peer_pid, peer_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer_pid != record['qemu_pid'] or peer_uid != os.getuid():
                raise ValueError('Serial socket belongs to a different process')
            connection.sendall(payload)
        return
    output = None
    if args.action == 'screenshot':
        if args.output is None:
            parser.error('screenshot requires --output')
        output = plain_path(args.output)
        if output.exists() or output.suffix.lower() != '.ppm' or not output.parent.is_dir():
            raise ValueError('A new --output .ppm path in an existing directory is required')
    _, client = identity(directory)
    try:
        if args.action == 'stop':
            print(json.dumps({'power_button_requested': client.call('system_powerdown'),
                              'forced_termination': False}))
        elif args.action == 'screenshot':
            client.call('screendump', {'filename': str(output)})
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError('QMP did not create a screenshot')
            print(str(output))
    finally:
        client.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS ARM64 VM: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

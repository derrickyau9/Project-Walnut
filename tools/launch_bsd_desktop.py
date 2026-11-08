"""Start the R14 ARM64 BSD appliance in a persistent, local QEMU/GTK window.

On Windows this dispatches to Debian WSL. No network or shared guest directory
is created. Use ``status`` and ``stop`` for process state and guest poweroff.
The private disk under ~/daos-vm-r14 is reused on subsequent starts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'DaOS-r14-desktop-vm-v1'
MAX_RAW = 8 * 1024**3


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Expected an ordinary file: ' + str(path))
    return path


def read_json(path):
    regular(path)
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError('JSON exceeds the 8 MiB limit: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def now():
    return datetime.now(timezone.utc).isoformat()


def receipt(artifacts):
    value = read_json(artifacts / 'image-evidence.json')
    target = value.get('target', {})
    image = value.get('image', {})
    if (value.get('format') != 'DaOS-freebsd-image-build-v1'
            or value.get('assembled') is not True
            or target.get('architecture') != 'arm64'
            or target.get('elf_machine') != 183
            or target.get('efi_machine') != 0xaa64
            or type(image.get('bytes')) is not int
            or not 32 * 1024**2 <= image['bytes'] <= MAX_RAW
            or image['bytes'] % 512
            or not re.fullmatch('[0-9a-f]{64}', str(image.get('sha256', '')))):
        raise ValueError('Receipt does not identify an assembled ARM64 BSD image')
    return value, image


def process_ticks(pid):
    try:
        value = Path('/proc/' + str(pid) + '/stat').read_text()
        return int(value[value.rfind(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def alive(state):
    pid = state.get('qemu_pid')
    if type(pid) is not int or process_ticks(pid) != state.get('qemu_start_ticks'):
        return False
    try:
        args = Path('/proc/' + str(pid) + '/cmdline').read_bytes().split(b'\0')
        return (b'DaOS-BSD-R14' in args and any(b'/guest.qcow2,' in arg for arg in args))
    except OSError:
        return False


def qmp(work, execute):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(work / 'qmp.sock'))
        with connection.makefile('rwb') as stream:
            greeting = json.loads(stream.readline(1024 * 1024))
            if 'QMP' not in greeting:
                raise ValueError('QMP greeting missing')
            for index, command in enumerate(('qmp_capabilities', execute)):
                stream.write((json.dumps({'execute': command, 'id': index}) + '\n').encode())
                stream.flush()
                while True:
                    line = stream.readline(1024 * 1024)
                    if not line:
                        raise RuntimeError('QMP closed before acknowledging ' + command)
                    reply = json.loads(line)
                    if reply.get('id') == index:
                        if 'error' in reply:
                            raise RuntimeError('QMP rejected command: ' + str(reply['error']))
                        break
            return reply.get('return')


def private_directory(path):
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError('Work directory must not be a symbolic link')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.resolve() != path or path.stat().st_uid != os.getuid():
        raise ValueError('Work directory must be canonical and owned by the current user')
    if path.stat().st_mode & 0o077:
        raise ValueError('Work directory must be private (chmod 700): ' + str(path))
    if len(str(path / 'qmp.sock').encode()) > 100:
        raise ValueError('Work directory path is too long for a Unix QMP socket')
    return path


def prepare(work, artifacts, update):
    value, image = receipt(artifacts)
    metadata = work / 'image.json'
    raw = work / 'DaOS-arm64.img'
    if metadata.exists():
        previous = read_json(metadata)
        if previous.get('image') != image:
            raise ValueError('Saved VM belongs to a different image; choose a new --work-dir')
    elif any((work / name).exists() for name in ('guest.qcow2', 'uefi-vars.fd')):
        raise ValueError('Existing VM files have no image identity; choose a new --work-dir')
    if not raw.exists():
        update('decompressing', detail='Decompressing the R14 BSD image')
        compressed_record = value.get('compressed_image')
        compressed_name = Path(str((compressed_record or {}).get('path', 'DaOS-arm64.img.xz'))).name
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.img\.xz', compressed_name):
            raise ValueError('Invalid compressed image filename in receipt')
        compressed = regular(artifacts / compressed_name)
        if compressed_record is not None:
            if (compressed_record.get('format') != 'xz'
                    or compressed_record.get('raw_sha256') != image['sha256']
                    or compressed_record.get('raw_bytes') != image['bytes']
                    or compressed_record.get('bytes') != compressed.stat().st_size
                    or digest(compressed) != compressed_record.get('sha256')):
                raise ValueError('Compressed image differs from its receipt')
        partial = work / ('DaOS-arm64.' + uuid.uuid4().hex + '.partial')
        total = 0
        checksum = hashlib.sha256()
        with lzma.open(compressed, 'rb') as source, partial.open('xb') as output:
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > image['bytes']:
                    raise ValueError('Decompressed image exceeds its declared length')
                checksum.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if total != image['bytes'] or checksum.hexdigest() != image['sha256']:
            raise ValueError('Decompressed image SHA-256/length mismatch; partial file retained')
        partial.chmod(0o444)
        partial.rename(raw)
    update('verifying', detail='Verifying the complete raw image SHA-256')
    regular(raw)
    if raw.stat().st_size != image['bytes'] or digest(raw) != image['sha256']:
        raise ValueError('Cached raw image SHA-256/length mismatch')
    raw.chmod(0o444)
    firmware = Path('/usr/share/AAVMF')
    for name in ('AAVMF_CODE.no-secboot.fd', 'AAVMF_VARS.fd'):
        source = regular(firmware / name)
        if source.stat().st_size != 64 * 1024**2:
            raise ValueError('AAVMF must contain two regular 64 MiB firmware files')
    code = work / 'uefi-code.fd'
    if not code.exists():
        with code.open('xb') as output, (firmware / 'AAVMF_CODE.no-secboot.fd').open('rb') as source:
            shutil.copyfileobj(source, output)
        code.chmod(0o444)
    regular(code)
    if code.stat().st_size != 64 * 1024**2:
        raise ValueError('Private UEFI code length mismatch')
    if metadata.exists() and previous.get('uefi_code_sha256') != digest(code):
        raise ValueError('Saved VM UEFI code changed')
    variables = work / 'uefi-vars.fd'
    if not variables.exists():
        with variables.open('xb') as output, (firmware / 'AAVMF_VARS.fd').open('rb') as source:
            shutil.copyfileobj(source, output)
    regular(variables)
    if variables.stat().st_size != 64 * 1024**2:
        raise ValueError('Private UEFI variables length mismatch')
    disk = work / 'guest.qcow2'
    if not disk.exists():
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(raw), str(disk)],
                       check=True, timeout=30)
    regular(disk)
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(disk)], timeout=30))
    if (info.get('format') != 'qcow2' or info.get('virtual-size') != image['bytes']
            or info.get('full-backing-filename') != str(raw) or info.get('backing-filename-format') != 'raw'):
        raise ValueError('Persistent disk is not backed by the verified raw image')
    subprocess.run(['qemu-img', 'check', '-f', 'qcow2', str(disk)], check=True, timeout=120)
    if not metadata.exists():
        write_json(metadata, {'format': FORMAT, 'image': image, 'receipt_sha256': digest(artifacts / 'image-evidence.json'),
                              'uefi_code_sha256': digest(code), 'created': now()})
    return image


def command(work, serial, headless=False):
    return ['qemu-system-aarch64', '-name', 'DaOS-BSD-R14', '-machine', 'virt,gic-version=3',
            '-accel', 'tcg,thread=multi', '-cpu', 'cortex-a72', '-smp', '4', '-m', '4096',
            '-nodefaults', '-display', 'none' if headless else 'gtk', '-serial', 'file:' + str(serial), '-monitor', 'none',
            '-nic', 'none', '-no-reboot', '-qmp', 'unix:' + str(work / 'qmp.sock') + ',server=on,wait=off',
            '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=' + str(work / 'uefi-code.fd'),
            '-drive', 'if=pflash,format=raw,unit=1,file=' + str(work / 'uefi-vars.fd'),
            '-drive', 'file=' + str(work / 'guest.qcow2') + ',format=qcow2,if=none,id=release,cache=writeback',
            '-device', 'virtio-blk-pci,drive=release', '-device', 'ramfb', '-device', 'qemu-xhci,id=usb',
            '-device', 'usb-kbd,bus=usb.0', '-device', 'usb-tablet,bus=usb.0',
            '-object', 'rng-random,filename=/dev/urandom,id=DaOS-rng0',
            '-device', 'virtio-rng-pci,rng=DaOS-rng0',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def manager(args):
    import fcntl
    work = private_directory(args.work_dir)
    with (work / 'owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 2
        if (work / 'status.json').exists() and alive(read_json(work / 'status.json')):
            raise ValueError('An existing owned QEMU process is still running; use stop')
        state = {'format': FORMAT, 'run': args.run, 'manager_pid': os.getpid(), 'started': now()}
        def update(phase, **values):
            state.update(values, state=phase, updated=now())
            write_json(work / 'status.json', state)
        try:
            for tool in ('qemu-system-aarch64', 'qemu-img'):
                if not shutil.which(tool):
                    raise ValueError('Required Debian tool is missing: ' + tool)
            if not args.headless and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
                raise ValueError('No WSLg display is available')
            update('preparing', detail='Preparing the persistent R14 BSD virtual machine')
            image = prepare(work, args.image_dir, update)
            socket_path = work / 'qmp.sock'
            if socket_path.exists():
                if not stat.S_ISSOCK(socket_path.lstat().st_mode):
                    raise ValueError('Unexpected non-socket at QMP path')
                socket_path.unlink()
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + args.run[:8]
            serial = work / ('serial-' + stamp + '.log')
            output = work / ('qemu-' + stamp + '.log')
            argv = command(work, serial, args.headless)
            write_json(work / ('launch-' + stamp + '.json'), {'format': FORMAT, 'command': argv,
                       'image_sha256': image['sha256'], 'network': False, 'host_shares': False})
            with output.open('xb') as log:
                child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                update('launching', qemu_pid=child.pid, qemu_start_ticks=process_ticks(child.pid),
                       serial_log=str(serial), qemu_log=str(output), image_sha256=image['sha256'],
                       detail='Starting QEMU without a host window' if args.headless else 'Opening the QEMU/GTK window')
                deadline = time.monotonic() + 30
                qmp_ready = False
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError('QEMU exited during startup; see ' + str(output))
                    if socket_path.exists():
                        try:
                            qmp(work, 'query-status')
                            qmp_ready = True
                            break
                        except (OSError, ValueError, RuntimeError):
                            pass
                    time.sleep(0.1)
                if not qmp_ready:
                    raise RuntimeError('QEMU did not expose its control socket within 30 seconds; use stop and inspect ' + str(output))
                update('running', detail='QEMU started; inspect its window to establish UI readiness')
                result = child.wait()
            sync_seen = serial.exists() and b'All buffers synced' in serial.read_bytes()
            update('stopped' if result == 0 else 'failed', exit_code=result,
                   all_buffers_synced_marker_seen=sync_seen, ended=now())
            return result
        except Exception as error:
            update('failed', error=str(error), ended=now())
            print(str(error), file=sys.stderr, flush=True)
            return 1


def start(args):
    import fcntl
    work = private_directory(args.work_dir)
    with (work / 'owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        except BlockingIOError:
            raise ValueError('This R14 VM is already running or preparing; use status')
    token = uuid.uuid4().hex
    with (work / ('manager-' + token + '.log')).open('xb') as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_run',
                                  '--image-dir', str(args.image_dir), '--work-dir', str(work), '--run', token,
                                  *(['--headless'] if args.headless else [])],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 600
    previous = None
    while time.monotonic() < deadline:
        status_path = work / 'status.json'
        if status_path.exists():
            state = read_json(status_path)
            if state.get('run') == token:
                phase = state.get('state')
                if phase != previous:
                    print(state.get('detail', phase), flush=True)
                    previous = phase
                if phase == 'failed':
                    raise RuntimeError(state.get('error', 'QEMU failed; see ' + str(work)))
                if phase == 'running' and alive(state):
                    print('Headless QEMU is running; use QMP to review its framebuffer.' if args.headless else
                          'Window: QEMU (DaOS-BSD-R14). First boot can take several minutes.', flush=True)
                    print('Persistent VM files: ' + str(work), flush=True)
                    print('For a normal guest shutdown: python3 tools/launch_bsd_desktop.py stop', flush=True)
                    return 0
        if child.poll() is not None:
            raise RuntimeError('VM manager exited; inspect ' + str(work / ('manager-' + token + '.log')))
        time.sleep(0.5)
    raise RuntimeError('VM is still preparing; use status and inspect logs in ' + str(work))


def status(args):
    path = args.work_dir / 'status.json'
    if not path.exists():
        print('R14 virtual machine has not been started in ' + str(args.work_dir))
        return 0
    state = read_json(path)
    state['qemu_process_alive'] = alive(state)
    if state['qemu_process_alive']:
        state['qmp_status'] = qmp(args.work_dir, 'query-status')
    print(json.dumps(state, indent=2))
    return 0


def stop(args):
    state = read_json(args.work_dir / 'status.json')
    if not alive(state):
        print('No owned R14 QEMU process is running.')
        return 0
    qmp(args.work_dir, 'system_powerdown')
    print('Guest poweroff requested; waiting for BSD to shut down...', flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = read_json(args.work_dir / 'status.json')
        if not alive(state) and state.get('state') in ('stopped', 'failed'):
            if state.get('exit_code') != 0:
                raise RuntimeError('QEMU ended with an error; inspect ' + str(args.work_dir))
            print('QEMU exited normally. All buffers synced marker: ' + str(state.get('all_buffers_synced_marker_seen')))
            return 0
        time.sleep(0.5)
    raise RuntimeError('BSD has not powered off within 180 seconds; VM remains running. No forced stop was sent.')


def windows_dispatch(arguments):
    distro = 'Debian'
    if '--distro' in arguments:
        index = arguments.index('--distro')
        if index + 1 >= len(arguments):
            raise ValueError('--distro requires a WSL distribution name')
        distro = arguments[index + 1]
        arguments = arguments[:index] + arguments[index + 2:]
    def linux_path(path):
        return subprocess.check_output(['wsl.exe', '-d', distro, '--exec', 'wslpath', '-a', path], text=True).strip()
    converted = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        option, equals, value = argument.partition('=')
        if option in ('--image-dir', '--work-dir'):
            if not equals:
                index += 1
                if index >= len(arguments):
                    raise ValueError(option + ' requires a path')
                value = arguments[index]
            if not value.startswith(('/', '~')):
                value = linux_path(str(Path(value).resolve()))
            converted.extend((option, value))
        else:
            converted.append(argument)
        index += 1
    return subprocess.call(['wsl.exe', '-d', distro, '--exec', 'python3', linux_path(str(Path(__file__).resolve())), *converted])


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.name == 'nt':
        return windows_dispatch(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='start', choices=('start', 'status', 'stop', '_run'))
    parser.add_argument('--image-dir', type=Path, default=ROOT / 'build/DaOS_BSD_Unix_ARM64_R14')
    parser.add_argument('--work-dir', type=Path, default=Path.home() / 'daos-vm-r14')
    parser.add_argument('--run', help=argparse.SUPPRESS)
    parser.add_argument('--headless', action='store_true', help='isolated QMP framebuffer/input validation without a host window')
    args = parser.parse_args(arguments)
    args.image_dir = args.image_dir.expanduser().resolve()
    args.work_dir = args.work_dir.expanduser().absolute()
    return {'start': start, 'status': status, 'stop': stop, '_run': manager}[args.action](args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS R14 VM: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

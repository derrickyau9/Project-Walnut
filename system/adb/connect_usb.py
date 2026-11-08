#!/usr/bin/env python3
"""Open an authorized DaOS USB shell through the bundled Windows adb.

The project owns a separate foreground adb server on a non-5037 port. It never
kills the user's normal adb server and filters USB enumeration by DaOS serial.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
DEFAULT_TOOLS = PROJECT / 'build/DaOS_USB_Development/host-tools'
DEFAULT_SERIAL = 'DaOS-USB-ADB-DEVELOPMENT'
DEFAULT_PORT = 5049


def read(path):
    if path.is_symlink():
        raise ValueError('Refusing a redirected host receipt: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Host receipt must be a JSON object')
    return value


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write('\n')
    os.replace(temporary, path)


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def process_stamp(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    if os.name != 'nt':
        root = Path('/proc') / str(pid)
        try:
            fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            return {'pid': pid, 'start_ticks': int(fields[19]),
                    'executable': str((root / 'exe').resolve(strict=True))}
        except (FileNotFoundError, ProcessLookupError):
            return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise OSError(ctypes.get_last_error(), 'Cannot verify project adb process identity')
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise OSError(ctypes.get_last_error(), 'Cannot read project adb process path')
        created, exited, used, elapsed = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(used), ctypes.byref(elapsed)):
            raise OSError(ctypes.get_last_error(), 'Cannot read project adb process start time')
        ticks = created.dwLowDateTime | created.dwHighDateTime << 32
        return {'pid': pid, 'start_ticks': ticks, 'executable': str(Path(buffer.value).resolve())}
    finally:
        kernel.CloseHandle(handle)


@contextlib.contextmanager
def server_lock(path):
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError('Another DaOS USB client is preparing this adb server') from error
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def socket_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.15):
            return True
    except OSError:
        return False


def key_environment(tools, adb):
    key = tools / 'keys/adbkey'
    public = tools / 'keys/adbkey.pub'
    key.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami'], text=True, timeout=5).strip()
        subprocess.run(['icacls', str(key.parent), '/inheritance:r', '/grant:r', identity + ':(OI)(CI)F'],
                       check=True, capture_output=True, timeout=10)
    else:
        key.parent.chmod(0o700)
    if not key.exists():
        if public.exists():
            raise ValueError('Project host private key is missing; refusing to replace its authorized public key')
        subprocess.run([str(adb), 'keygen', str(key)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name != 'nt':
            key.chmod(0o600)
    if key.is_symlink() or public.is_symlink() or not key.is_file() or not public.is_file():
        raise ValueError('Regular project-specific host key files are required')
    sys.path.insert(0, str(HERE))
    from daemon.auth import load_authorized_keys
    fingerprint = load_authorized_keys(public)[0].fingerprint
    profile = tools / 'client-home'
    android = profile / '.android'
    android.mkdir(parents=True, exist_ok=True)
    for name, source in (('adbkey', key), ('adbkey.pub', public)):
        destination = android / name
        if not destination.exists():
            os.link(source, destination)
        elif destination.is_symlink() or not os.path.samefile(source, destination):
            raise ValueError('Project adb profile key differs from its configured key')
    environment = dict(os.environ, ADB_VENDOR_KEYS=str(key), HOME=str(profile), USERPROFILE=str(profile),
                       ANDROID_USER_HOME=str(android), ADB_MDNS_AUTO_CONNECT='',
                       ADB_LOCAL_TRANSPORT_MAX_PORT='0')
    return environment, fingerprint


def ensure_server(tools, adb, port, serial):
    if not 1024 <= port <= 65535 or port == 5037:
        raise ValueError('Use a project adb server port from 1024 to 65535, excluding 5037')
    environment, fingerprint = key_environment(tools, adb)
    state = tools / 'state'
    state.mkdir(exist_ok=True)
    receipt_path = state / ('server-' + str(port) + '.json')
    with server_lock(state / ('server-' + str(port) + '.lock')):
        previous = read(receipt_path) if receipt_path.exists() else None
        if previous is not None:
            actual = process_stamp(previous.get('pid'))
            if actual is not None:
                if (actual != previous.get('process') or Path(actual['executable']).resolve() != adb.resolve()
                        or previous.get('authorized_public_fingerprint') != fingerprint
                        or previous.get('serial_filter') != serial):
                    raise ValueError('This port belongs to another key/device configuration; choose another --server-port')
                if not socket_open(port):
                    raise ValueError('Recorded project adb server is alive but its port is unavailable')
                return dict(previous, cached=True), environment
        if socket_open(port):
            raise ValueError('The requested port is already owned by an unrecorded server; choose another --server-port')
        run_id = str(port) + '-' + uuid.uuid4().hex[:12]
        log_path = state / ('server-' + run_id + '.log')
        command = [str(adb), '-L', 'tcp:' + str(port), '--one-device', serial, 'server', 'nodaemon']
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with log_path.open('xb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, env=environment,
                                       creationflags=flags, close_fds=True,
                                       start_new_session=os.name != 'nt')
        deadline = time.monotonic() + 8
        while not socket_open(port):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Project adb server did not start; inspect ' + str(log_path))
            time.sleep(.1)
        record = {'format': 'DaOS-project-adb-server-v1', 'pid': process.pid,
                  'process': process_stamp(process.pid), 'port': port, 'serial_filter': serial,
                  'authorized_public_fingerprint': fingerprint, 'argv': command,
                  'adb_sha256': hash_file(adb), 'log': str(log_path), 'created_at_unix': time.time()}
        if record['process'] is None:
            raise RuntimeError('Project adb server exited during identity verification')
        if previous is not None:
            history = state / ('server-' + run_id + '-previous.json')
            save(history, previous)
        save(receipt_path, record)
        return record, environment


def adb_call(adb, port, environment, *arguments, timeout=10):
    return subprocess.run([str(adb), '-P', str(port), *arguments], env=environment,
                          capture_output=True, text=True, timeout=timeout)


def parse_devices(output):
    devices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] == 'List' or line.startswith('*'):
            continue
        item = {'serial': fields[0], 'state': fields[1]}
        for field in fields[2:]:
            if ':' in field:
                name, value = field.split(':', 1)
                item[name] = value
        devices.append(item)
    return devices


def daos_devices(devices, serial):
    result = []
    for device in devices:
        name = device['serial']
        if ':' in name or not name.casefold().startswith('daos'):
            continue
        if serial and name != serial:
            continue
        metadata = ' '.join(device.get(key, '') for key in ('product', 'model', 'device')).casefold()
        if device['state'] == 'device' and 'daos' not in metadata:
            continue
        result.append(device)
    return result


def select_device(devices, *, selection=None, interactive=True):
    ready = [item for item in devices if item['state'] == 'device']
    if selection is not None:
        if not 1 <= selection <= len(ready):
            raise ValueError('--select is outside the DaOS device list')
        return ready[selection - 1]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        return None
    for index, item in enumerate(ready, 1):
        print(f"{index}. {item['serial']} transport_id={item.get('transport_id', '?')} usb={item.get('usb', '?')}")
    if not interactive or not sys.stdin.isatty():
        raise ValueError('Multiple DaOS devices found; use --select or a unique --serial')
    choice = input('Select DaOS device number: ').strip()
    if not choice.isdecimal() or not 1 <= int(choice) <= len(ready):
        raise ValueError('Invalid DaOS device selection')
    return ready[int(choice) - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=DEFAULT_TOOLS)
    parser.add_argument('--server-port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--serial', default=os.environ.get('DAOS_USB_SERIAL', DEFAULT_SERIAL))
    parser.add_argument('--select', type=int)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--no-shell', action='store_true', help='Wait and report the selected USB transport')
    args = parser.parse_args()
    if not re.fullmatch(r'(?i:daos)[A-Za-z0-9._-]{1,60}', args.serial):
        parser.error('--serial must name a DaOS USB device')
    if not 0 < args.timeout <= 600:
        parser.error('--timeout must be between 0 and 600 seconds')
    tools = args.tools.resolve(strict=True)
    adb = tools / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb')
    if not adb.is_file():
        raise ValueError('Bundled official platform-tools adb is missing: ' + str(adb))
    server, environment = ensure_server(tools, adb, args.server_port, args.serial)
    print(f"DaOS USB: adb port {args.server_port}; exact USB serial filter {args.serial}")
    deadline = time.monotonic() + args.timeout
    while True:
        result = adb_call(adb, args.server_port, environment, 'devices', '-l')
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'adb device listing failed')
        candidates = daos_devices(parse_devices(result.stdout), args.serial)
        if args.list:
            print(json.dumps(candidates, indent=2))
            return
        selected = select_device(candidates, selection=args.select)
        if selected is not None:
            break
        if time.monotonic() > deadline:
            states = ', '.join(item['state'] for item in candidates) or 'no matching USB transport'
            raise TimeoutError('DaOS USB was not ready: ' + states + '. Verify the device image, cable, authorization and Windows USB driver; no network transport was selected.')
        time.sleep(.4)
    selector = ['-t', selected['transport_id']] if selected.get('transport_id', '').isdecimal() else ['-s', selected['serial']]
    check = adb_call(adb, args.server_port, environment, *selector, 'get-state')
    if check.returncode or check.stdout.strip() != 'device':
        raise RuntimeError('Selected DaOS USB transport changed before opening the shell')
    print('Opening DaOS USB shell: ' + selected['serial'])
    if args.no_shell:
        print(json.dumps(selected, indent=2))
        return
    code = subprocess.call([str(adb), '-P', str(args.server_port), *selector, 'shell'], env=environment)
    raise SystemExit(code)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS USB client: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

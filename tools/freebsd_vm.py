"""Private, key-only FreeBSD development VM on an existing non-root Linux host.

The official verified qcow2 is never modified. A new bounded sparse overlay owns
all guest changes. Package installation/builds happen only inside that guest;
there are no host mounts, host shares or host package installations. Provisioning
may use guest user-network egress; SSH is always forwarded to IPv4 loopback only.
The temporary bootstrap root key is development transport, not a deployment key.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import lzma
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 2 * 1024**3
MAX_BASE = 16 * 1024**3
MAX_SERIAL = 64 * 1024**2
MAX_OUTPUT = 16 * 1024**2
MAX_COPY = 2 * 1024**3
_SHA = re.compile(r'[0-9a-f]{64}')


def _linux():
    if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() == 0:
        raise RuntimeError('VM controls require a non-root Linux host')


def _number(value, low, high, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or not low <= value <= high):
        raise ValueError(name + ' exceeds its finite bound')
    return value


def _directory(path, *, new=False):
    path = Path(os.path.abspath(os.fspath(path)))
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path.as_posix()):
        raise ValueError('VM directory requires a portable Linux path without option delimiters')
    if new:
        if path.parent.resolve(strict=True) != path.parent or os.path.lexists(path):
            raise ValueError('new VM directory requires an existing canonical parent')
        path.mkdir(mode=0o700)
    if path.resolve(strict=True) != path or not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError('VM directories must be canonical ordinary directories')
    return path


def _pin(path, *, writable=False):
    path = Path(os.path.abspath(os.fspath(path)))
    before = path.lstat()
    if path.resolve(strict=True) != path or not stat.S_ISREG(before.st_mode):
        raise ValueError('canonical regular non-link file required')
    descriptor = os.open(path, (os.O_RDWR if writable else os.O_RDONLY) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('file identity changed during open')
        return os.fdopen(descriptor, 'r+b' if writable else 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def _digest(path):
    with _pin(path) as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
        return result.hexdigest()


def _json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON member')
            result[key] = value
        return result
    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite JSON value')
        return result
    def invalid(_):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=invalid)


def _read_json(path):
    with _pin(path) as stream:
        raw = stream.read(1024 * 1024 + 1)
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError('VM metadata exceeds bound')
    value = _json(raw)
    if type(value) is not dict:
        raise ValueError('VM metadata object required')
    return value


def _write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as output:
        json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
        output.write('\n'); output.flush(); os.fsync(output.fileno())


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def _run(command, *, timeout=60, maximum=MAX_OUTPUT, pass_fds=()):
    _number(timeout, .1, 28800, 'command timeout')
    if type(maximum) is not int or not 1 <= maximum <= MAX_OUTPUT:
        raise ValueError('command output bound invalid')
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   pass_fds=pass_fds)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started >= timeout:
                    raise RuntimeError('owned VM command exceeded its time budget')
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > maximum:
                    raise RuntimeError('owned VM command output exceeded bound')
                time.sleep(.03)
            if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > maximum:
                raise RuntimeError('owned VM command output exceeded bound')
            stdout.seek(0); stderr.seek(0)
            return {'returncode': process.returncode, 'stdout': stdout.read(maximum).decode('utf-8', 'replace'),
                    'stderr': stderr.read(maximum).decode('utf-8', 'replace'),
                    'seconds': time.monotonic() - started}
        finally:
            _stop(process)


def _tool(name):
    result = shutil.which(name)
    if not result:
        raise RuntimeError('existing host tool required: ' + name)
    return result


@contextmanager
def _lock(directory, timeout=1):
    import fcntl
    _number(timeout, 0, 30, 'lock timeout')
    descriptor = os.open(directory / '.vm.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('VM lock must be regular')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('VM lifecycle is already owned') from None
                time.sleep(.03)
        yield
    finally:
        os.close(descriptor)
        # Keep the lock inode: unlinking it can split concurrent flock owners.


def seed_data(public_key, instance):
    if (not isinstance(public_key, str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [A-Za-z0-9._-]+)?', public_key)
            or len(public_key) > 1024 or str(uuid.UUID(instance)) != instance):
        raise ValueError('canonical task public key and instance UUID required')
    sshd = '\n'.join(('Port 22', 'PermitRootLogin prohibit-password', 'PubkeyAuthentication yes',
                      'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
                      'AuthenticationMethods publickey', 'UsePAM yes', 'AllowUsers root camera',
                      'Subsystem sftp /usr/libexec/sftp-server', ''))
    script = '''set -eu
sysrc ifconfig_vtnet0=DHCP
sysrc sshd_enable=YES
if ! id camera >/dev/null 2>&1; then
    previous=$(pw usershow -u 1000 -7 2>/dev/null | cut -d: -f1 || true)
    if [ "$previous" = freebsd ]; then
        pw usermod freebsd -l camera -d /home/camera -m
    elif [ -z "$previous" ]; then
        pw useradd camera -u 1000 -m -s /bin/sh -w no
    else
        echo 'HF_FREEBSD_BOOTSTRAP_FAIL unexpected UID1000 owner' >/dev/console
        exit 1
    fi
fi
pw usermod camera -w no
if id freebsd >/dev/null 2>&1; then pw usermod freebsd -w no; fi
pw usermod root -w no
install -d -m 0700 /root/.ssh
install -d -m 0700 -o camera -g "$(id -g camera)" /home/camera/.ssh
printf '%s\\n' 'PUBLIC_KEY' >/root/.ssh/authorized_keys
printf '%s\\n' 'PUBLIC_KEY' >/home/camera/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys /home/camera/.ssh/authorized_keys
chown camera:"$(id -g camera)" /home/camera/.ssh/authorized_keys
install -d -m 0755 -o camera -g "$(id -g camera)" /data
printf '\\nconsole="comconsole"\\nboot_serial="YES"\\n' >>/boot/loader.conf
service sshd restart || service sshd start
touch /var/db/hf-task-bootstrap.done
echo 'HF_FREEBSD_BOOTSTRAP_READY key-only task-guest' >/dev/console
'''.replace('PUBLIC_KEY', public_key)
    # nuageinit write_files runs before the SSH service; credentials are disabled
    # before the first network-facing daemon starts, not only after SSH access.
    user = '#cloud-config\nssh_pwauth: false\npackage_update: false\npackage_upgrade: false\nnetwork:\n  ethernets:\n    vtnet0:\n      dhcp4: true\nwrite_files:\n'
    user += "  - path: /etc/ssh/sshd_config\n    permissions: '0600'\n    content: |\n"
    user += ''.join('      ' + line + '\n' for line in sshd.splitlines())
    user += 'runcmd:\n  - |\n' + ''.join('      ' + line + '\n' for line in script.splitlines())
    meta = 'instance-id: ' + instance + '\nlocal-hostname: daos-freebsd-dev\n'
    return user.encode('ascii'), meta.encode('ascii')


def prepare(archive, expected_sha256, work_dir, *, port=22241):
    _linux()
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        raise ValueError('official archive SHA256 required')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('unprivileged loopback SSH port required')
    archive = Path(os.path.abspath(os.fspath(archive)))
    with _pin(archive) as source:
        if not 1 <= os.fstat(source.fileno()).st_size <= MAX_ARCHIVE or _digest(archive) != expected_sha256:
            raise ValueError('official VM archive size or SHA256 mismatch')
    tools = {name: _tool(name) for name in ('qemu-img', 'qemu-system-x86_64', 'ssh', 'ssh-keygen', 'scp')}
    if shutil.disk_usage(Path(work_dir).parent).free < 28 * 1024**3:
        raise RuntimeError('at least 28 GiB free workspace budget required')
    directory = _directory(work_dir, new=True)
    base = directory / 'base.qcow2'
    with _pin(archive) as source, base.open('xb') as output, lzma.LZMAFile(source) as packed:
        total = 0
        while True:
            block = packed.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_BASE:
                raise ValueError('expanded official VM exceeds bounded base size')
            if block == b'\0' * len(block):
                output.seek(len(block), os.SEEK_CUR)
            else:
                output.write(block)
        output.truncate(total); output.flush(); os.fsync(output.fileno())
    base.chmod(0o444)
    info_result = _run([tools['qemu-img'], 'info', '--output=json', '-f', 'qcow2', str(base)], maximum=65536)
    if info_result['returncode']:
        raise RuntimeError('official qcow2 inspection failed')
    info = _json(info_result['stdout'])
    if (info.get('format') != 'qcow2' or 'backing-filename' in info or 'data-file' in info or
            type(info.get('virtual-size')) is not int or not 1 <= info['virtual-size'] <= 32 * 1024**3):
        raise ValueError('official base must be standalone bounded qcow2')
    checked = _run([tools['qemu-img'], 'check', '-f', 'qcow2', str(base)], maximum=65536)
    if checked['returncode']:
        raise RuntimeError('official qcow2 did not check clean')
    overlay = directory / 'guest.qcow2'
    created = _run([tools['qemu-img'], 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base), str(overlay), '32G'])
    if created['returncode']:
        raise RuntimeError('private sparse VM overlay creation failed')
    key = directory / 'task_ed25519'
    generated = _run([tools['ssh-keygen'], '-q', '-t', 'ed25519', '-N', '', '-C', 'hf-task-bootstrap', '-f', str(key)])
    if generated['returncode']:
        raise RuntimeError('temporary task key generation failed')
    key.chmod(0o600)
    instance = str(uuid.uuid4())
    user_data, meta_data = seed_data((directory / 'task_ed25519.pub').read_text().strip(), instance)
    from tools.freebsd_seed import create_seed
    seed = create_seed(directory / 'seed.iso', user_data=user_data, meta_data=meta_data)
    result = {'format': 'hf-freebsd-task-vm-v1', 'instance_id': instance, 'prepared': True,
              'archive': str(archive), 'archive_sha256': expected_sha256, 'base_sha256': _digest(base),
              'base_bytes': base.stat().st_size, 'base_virtual_bytes': info['virtual-size'],
              'overlay_virtual_bytes': 32 * 1024**3, 'seed_sha256': seed['sha256'],
              'ssh_host': '127.0.0.1', 'ssh_port': port, 'task_key_file': str(key),
              'source_sha256': {'tools/freebsd_vm.py': _digest(Path(__file__)),
                                'tools/freebsd_seed.py': _digest(ROOT / 'tools/freebsd_seed.py')},
              'host_uid': os.geteuid(), 'host_mounts': False, 'host_shares': False,
              'bootstrap_root_ssh': 'temporary task key only; remove before distribution',
              'official_image_authentication': 'provided SHA256 comparison; signature provenance supplied by caller'}
    _write_json(directory / 'vm.json', result)
    return result


def _metadata(work_dir):
    directory = _directory(work_dir)
    value = _read_json(directory / 'vm.json')
    if (value.get('format') != 'hf-freebsd-task-vm-v1' or value.get('prepared') is not True or
            value.get('ssh_host') != '127.0.0.1' or type(value.get('ssh_port')) is not int or
            not 1024 <= value['ssh_port'] <= 65535 or value.get('task_key_file') != str(directory / 'task_ed25519')):
        raise ValueError('invalid private VM metadata')
    return directory, value


def ssh_arguments(work_dir, *, user='root'):
    directory, metadata = _metadata(work_dir)
    if user not in ('root', 'camera'):
        raise ValueError('task VM user must be root or camera')
    with _pin(directory / 'task_ed25519') as key:
        if os.fstat(key.fileno()).st_mode & 0o077:
            raise ValueError('temporary private key permissions must be 0600')
    return [_tool('ssh'), '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'ConnectTimeout=3', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ClearAllForwardings=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-i', str(directory / 'task_ed25519'), '-p', str(metadata['ssh_port']), user + '@127.0.0.1']


def ssh_command(work_dir, command, *, user='root', timeout=60):
    _linux()
    if not isinstance(command, str) or not command or '\0' in command or len(command.encode()) > 65536:
        raise ValueError('bounded explicit guest shell command required')
    return _run([*ssh_arguments(work_dir, user=user), command], timeout=timeout)


def _guest_path(value):
    if (not isinstance(value, str) or len(value) > 1024 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or value.endswith('/') or '//' in value or any(part in ('.', '..') for part in value.split('/')[1:])):
        raise ValueError('canonical portable absolute guest file path required')
    return value


def copy_to(work_dir, source, destination, *, user='root', timeout=600):
    """Upload an explicit regular host file and verify the guest's received hash.

    The guest destination may be replaced: this is an explicitly requested build
    transport operation, never an automatic capture or a host filesystem export.
    """
    _linux(); _guest_path(destination)
    _number(timeout, .1, 28800, 'copy timeout')
    source = Path(os.path.abspath(os.fspath(source)))
    with _pin(source) as stream:
        before = os.fstat(stream.fileno())
        if not 0 <= before.st_size <= MAX_COPY:
            raise ValueError('upload exceeds file size bound')
        expected = _digest(source)
        arguments = ssh_arguments(work_dir, user=user)
        command = [_tool('scp'), *arguments[1:-1]]
        command[command.index('-p')] = '-P'
        command += ['--', str(source), arguments[-1] + ':' + destination]
        copied = _run(command, timeout=timeout)
        after = source.lstat()
        if copied['returncode']:
            raise RuntimeError('task VM upload failed; no automatic retry')
        if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) or _digest(source) != expected):
            raise RuntimeError('host upload source changed during transfer')
    checked = ssh_command(work_dir, 'sha256 -q ' + destination, user=user, timeout=30)
    if checked['returncode'] or checked['stdout'].strip() != expected:
        raise RuntimeError('guest upload hash mismatch')
    return {'format': 'hf-freebsd-vm-copy-v1', 'direction': 'to-guest', 'source': str(source),
            'destination': destination, 'size_bytes': before.st_size, 'sha256': expected}


def copy_from(work_dir, source, destination, *, user='root', timeout=600, max_bytes=MAX_COPY):
    """Download one bounded guest file into a NEW host regular file, hash checked."""
    _linux(); _guest_path(source)
    _number(timeout, .1, 28800, 'copy timeout')
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_COPY:
        raise ValueError('download size bound invalid')
    destination = Path(os.path.abspath(os.fspath(destination)))
    if destination.parent.resolve(strict=True) != destination.parent or os.path.lexists(destination):
        raise ValueError('download destination must be NEW beneath canonical existing parent')
    checked = ssh_command(work_dir, 'test -f ' + source + ' && stat -f %z ' + source + ' && sha256 -q ' + source,
                          user=user, timeout=30)
    lines = checked['stdout'].splitlines()
    if (checked['returncode'] or len(lines) != 2 or not re.fullmatch(r'[0-9]+', lines[0]) or
            not _SHA.fullmatch(lines[1]) or int(lines[0]) > max_bytes):
        raise ValueError('guest download source exceeds its size/hash contract')
    expected_size, expected_sha = int(lines[0]), lines[1]
    with destination.open('xb') as output, tempfile.TemporaryFile() as error:
        before = os.fstat(output.fileno())
        process = subprocess.Popen([*ssh_arguments(work_dir, user=user), 'cat ' + source],
            stdin=subprocess.DEVNULL, stdout=output, stderr=error)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    raise RuntimeError('owned guest download exceeded time budget')
                if os.fstat(output.fileno()).st_size > max_bytes or os.fstat(error.fileno()).st_size > 65536:
                    raise RuntimeError('owned guest download exceeded output budget')
                time.sleep(.03)
            output.flush(); os.fsync(output.fileno())
            after = destination.lstat()
            if (process.returncode or not stat.S_ISREG(after.st_mode) or
                    (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or after.st_size != expected_size):
                raise RuntimeError('guest download failed; partial owned file retained, never retried')
        finally:
            _stop(process)
    if _digest(destination) != expected_sha:
        raise RuntimeError('guest download hash mismatch; owned output retained for diagnosis')
    return {'format': 'hf-freebsd-vm-copy-v1', 'direction': 'from-guest', 'source': source,
            'destination': str(destination), 'size_bytes': expected_size, 'sha256': expected_sha}


def _resources(memory_mib, cpus):
    if type(memory_mib) is not int or not 2048 <= memory_mib <= 16384:
        raise ValueError('guest memory must be an integer within 2048-16384 MiB')
    if type(cpus) is not int or not 1 <= cpus <= 16:
        raise ValueError('guest CPU count must be an integer within 1-16')


def qemu_command(work_dir, *, restricted=False, memory_mib=16384, cpus=16, work_disk=None):
    _resources(memory_mib, cpus)
    directory, metadata = _metadata(work_dir)
    if type(restricted) is not bool:
        raise ValueError('network restriction must be boolean')
    command = [_tool('qemu-system-x86_64'), '-machine', 'q35,accel=kvm', '-cpu', 'host', '-smp', str(cpus),
            '-m', str(memory_mib), '-display', 'none', '-serial', 'stdio', '-monitor', 'none', '-no-reboot',
            '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off',
            '-drive', 'file=' + str(directory / 'guest.qcow2') + ',format=qcow2,if=virtio,cache=writeback',
            '-drive', 'file=' + str(directory / 'seed.iso') + ',format=raw,media=cdrom,readonly=on',
            '-netdev', 'user,id=tasknet,restrict=' + ('on' if restricted else 'off') +
            ',hostfwd=tcp:127.0.0.1:' + str(metadata['ssh_port']) + '-:22',
            '-device', 'virtio-net-pci,netdev=tasknet', '-sandbox',
            'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']
    if work_disk is not None:
        scratch=Path(work_disk).absolute()
        if scratch != directory.parent/'daos004-work.qcow2' or scratch.is_symlink():
            raise ValueError('only the explicitly owned DaOS004 scratch disk is supported')
        with _pin(scratch,writable=True) as handle:
            if os.fstat(handle.fileno()).st_uid != os.geteuid():
                raise ValueError('scratch disk must belong to the task user')
        inspected=_run([_tool('qemu-img'),'info','--output=json',str(scratch)],timeout=10)
        disk=json.loads(inspected['stdout'])
        if disk.get('format')!='qcow2' or disk.get('virtual-size')!=128*1024**3 or disk.get('backing-filename'):
            raise ValueError('unexpected scratch disk format or backing')
        root_drive='file='+str(directory/'guest.qcow2')+',format=qcow2,if=virtio,cache=writeback'
        command[command.index(root_drive)]='file='+str(directory/'guest.qcow2')+',format=qcow2,if=none,id=daos-root,cache=writeback'
        command += ['-device','virtio-blk-pci,drive=daos-root,bootindex=1']
        command += ['-drive','file='+str(scratch)+',format=qcow2,if=none,id=daos004-work,cache=writeback',
                    '-device','virtio-blk-pci,drive=daos004-work,serial=DAOS004-WORK']
    return command


def qmp(work_dir, command, *, timeout=5):
    _linux()
    directory, _ = _metadata(work_dir)
    _number(timeout, .1, 30, 'QMP timeout')
    if command not in ('query-status', 'system_powerdown', 'quit'):
        raise ValueError('unsupported VM control command')
    path = directory / 'qmp.sock'
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError('owned private QMP socket required')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        stream = connection.makefile('rb')
        def receive(expected=None):
            deadline = time.monotonic() + timeout
            for _ in range(32):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('QMP response deadline exceeded')
                connection.settimeout(remaining)
                raw = stream.readline(65537)
                if not raw or len(raw) > 65536:
                    raise ValueError('QMP response exceeds bounded protocol')
                value = _json(raw)
                if expected is None or value.get('id') == expected:
                    return value
            raise ValueError('QMP event bound exceeded')
        greeting = receive()
        if 'QMP' not in greeting:
            raise ValueError('QMP greeting missing')
        for ident, operation in [('cap', 'qmp_capabilities'), ('request', command)]:
            connection.sendall((json.dumps({'execute': operation, 'id': ident}) + '\n').encode())
            value = receive(ident)
            if 'error' in value or 'return' not in value:
                raise RuntimeError('QMP rejected task control')
        return value['return']


def serve(work_dir, *, boot_timeout=240, lifetime=14400, restricted=False, memory_mib=16384, cpus=16, work_disk=None):
    _resources(memory_mib, cpus)
    _linux()
    _number(boot_timeout, 10, 600, 'boot timeout')
    _number(lifetime, 30, 28800, 'VM lifetime')
    directory, metadata = _metadata(work_dir)
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise RuntimeError('existing KVM access required; no host privilege changes')
    with _lock(directory):
        for name, expected in [('base.qcow2', metadata['base_sha256']), ('seed.iso', metadata['seed_sha256'])]:
            if _digest(directory / name) != expected:
                raise ValueError('immutable VM input hash changed')
        with _pin(directory / 'guest.qcow2', writable=True):
            pass
        for name in ('qmp.sock',):
            if os.path.lexists(directory / name):
                raise ValueError('stale VM socket retained; inspect before a new boot')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', metadata['ssh_port']))
        boot = directory / ('boot-' + str(uuid.uuid4()))
        boot.mkdir(mode=0o700)
        command = qemu_command(directory, restricted=restricted, memory_mib=memory_mib, cpus=cpus,work_disk=work_disk)
        started = time.monotonic(); ready = False; reason = 'not started'
        process = None
        try:
            with (boot / 'serial.log').open('xb') as serial:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT)
                _write_json(boot / 'launch.json', {'format': 'hf-freebsd-vm-launch-v1', 'command': command,
                    'pid': process.pid, 'guest_egress_enabled': not restricted, 'ssh_loopback_only': True,
                    'memory_mib': memory_mib, 'cpus': cpus,
                    'host_mounts': False, 'host_shares': False, 'base_sha256': metadata['base_sha256']})
                next_probe = started
                while process.poll() is None:
                    now = time.monotonic()
                    if os.fstat(serial.fileno()).st_size > MAX_SERIAL:
                        raise RuntimeError('VM serial output exceeded limit')
                    if now - started > lifetime:
                        raise RuntimeError('owned VM lifetime expired')
                    if not ready and now - started > boot_timeout:
                        raise RuntimeError('FreeBSD SSH bootstrap did not finish within boot budget')
                    if not ready and now >= next_probe:
                        check = ssh_command(directory, 'test "$(id -u camera)" = 1000 && '
                            '/usr/sbin/sshd -T | grep -q "^passwordauthentication no$" && '
                            '/usr/sbin/sshd -T | grep -q "^kbdinteractiveauthentication no$" && '
                            '/usr/sbin/sshd -T | grep -q "^authenticationmethods publickey$" && '
                            'uname -s && uname -m && id -u && echo HF_TASK_SSH_READY', timeout=5)
                        if check['returncode'] == 0 and check['stdout'].splitlines() == ['FreeBSD', 'amd64', '0', 'HF_TASK_SSH_READY']:
                            ready = True
                            receipt = {'format': 'hf-freebsd-vm-ready-v1', 'ready': True, 'boot_directory': str(boot),
                                'pid': process.pid, 'ssh_host': '127.0.0.1', 'ssh_port': metadata['ssh_port'],
                                'task_key_file': metadata['task_key_file'], 'guest_system': 'FreeBSD', 'guest_machine': 'amd64',
                                'bootstrap_user': 'root', 'application_user': 'camera', 'guest_egress_enabled': not restricted,
                                'memory_mib': memory_mib, 'cpus': cpus,
                                'seconds': time.monotonic() - started}
                            _write_json(boot / 'ready.json', receipt)
                            print(json.dumps(receipt, sort_keys=True), flush=True)
                        next_probe = time.monotonic() + 2
                    time.sleep(.1)
                reason = 'guest exited'
                if not ready or process.returncode:
                    raise RuntimeError('FreeBSD VM exited without a clean ready lifecycle; inspect serial.log')
        except BaseException as error:
            reason = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        qmp(directory, 'system_powerdown', timeout=2)
                        process.wait(timeout=15)
                    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                        pass
                _stop(process)
            unchanged = _digest(directory / 'base.qcow2') == metadata['base_sha256']
            _write_json(boot / 'closed.json', {'format': 'hf-freebsd-vm-closed-v1', 'ready': ready,
                'returncode': process.returncode if process else None, 'reason': reason,
                'base_unchanged': unchanged, 'serial_sha256': _digest(boot / 'serial.log') if (boot / 'serial.log').exists() else None,
                'seconds': time.monotonic() - started})
            if not unchanged:
                raise RuntimeError('official immutable FreeBSD base changed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--archive', type=Path, required=True)
    command.add_argument('--sha256', required=True)
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--ssh-port', type=int, default=22241)
    command = sub.add_parser('serve')
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--boot-timeout', type=float, default=240)
    command.add_argument('--lifetime', type=float, default=14400)
    command.add_argument('--restricted', action='store_true')
    command.add_argument('--memory-mib', type=int, default=16384)
    command.add_argument('--cpus', type=int, default=16)
    command.add_argument('--work-disk',type=Path)
    command = sub.add_parser('ssh')
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--user', choices=('root', 'camera'), default='root')
    command.add_argument('--timeout', type=float, default=60)
    command.add_argument('--command', required=True)
    for name in ('put', 'get'):
        command = sub.add_parser(name)
        command.add_argument('--work-dir', type=Path, required=True)
        command.add_argument('--user', choices=('root', 'camera'), default='root')
        command.add_argument('--timeout', type=float, default=600)
        command.add_argument('--source', required=True)
        command.add_argument('--destination', required=True)
    for name in ('status', 'stop'):
        command = sub.add_parser(name)
        command.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda number, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.action == 'prepare':
        result = prepare(args.archive, args.sha256, args.work_dir, port=args.ssh_port)
    elif args.action == 'serve':
        serve(args.work_dir, boot_timeout=args.boot_timeout, lifetime=args.lifetime, restricted=args.restricted,
              memory_mib=args.memory_mib, cpus=args.cpus,work_disk=args.work_disk)
        return 0
    elif args.action == 'ssh':
        result = ssh_command(args.work_dir, args.command, user=args.user, timeout=args.timeout)
    elif args.action in ('put', 'get'):
        result = (copy_to if args.action == 'put' else copy_from)(args.work_dir, args.source,
            args.destination, user=args.user, timeout=args.timeout)
    else:
        result = qmp(args.work_dir, 'query-status' if args.action == 'status' else 'system_powerdown')
    print(json.dumps(result, sort_keys=True, indent=2))
    return result.get('returncode', 0) if isinstance(result, dict) else 0


if __name__ == '__main__':
    if __package__ in (None, ''):
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())

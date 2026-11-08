"""Actual ARM64 FreeBSD development target under bounded rootless QEMU TCG.

The amd64 KVM VM remains a build host. This separate target boots the official
ARM64 image with AAVMF, a private writable qcow2 overlay and private UEFI variables.
It shares only generic NoCloud/key/transport code, never amd64 readiness claims.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

from tools import freebsd_vm as vm

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_BYTES = 64 * 1024**2
READY_COMMAND = ('/usr/sbin/sshd -T | grep -q "^passwordauthentication no$" && '
                 '/usr/sbin/sshd -T | grep -q "^kbdinteractiveauthentication no$" && '
                 '/usr/sbin/sshd -T | grep -q "^authenticationmethods publickey$" && '
                 'uname -s && uname -m && uname -p && id -u && id -u camera && '
                 'od -An -tx1 -N20 /bin/sh && echo HF_ARM_TASK_SSH_READY')


def arm_seed_data(public_key, instance):
    user_data, meta_data = vm.seed_data(public_key, instance)
    # Keep the AAVMF-provided serial console on ARM, rather than inheriting the
    # amd64 PC COM-port loader setting from generic development preparation.
    user_data = user_data.replace(b'console="comconsole"', b'console="efi"')
    meta_data = meta_data.replace(b'daos-freebsd-dev', b'daos-arm64-dev')
    return user_data, meta_data


def validate_ready(stdout):
    """Require ARM64 FreeBSD execution and actual system ELF identity, not a label."""
    if not isinstance(stdout, str) or len(stdout.encode('utf-8')) > 16384:
        raise ValueError('bounded ARM64 readiness text required')
    lines = stdout.splitlines()
    if (len(lines) < 7 or lines[:5] != ['FreeBSD', 'arm64', 'aarch64', '0', '1000'] or
            lines[-1] != 'HF_ARM_TASK_SSH_READY'):
        raise ValueError('actual FreeBSD ARM64 root transport and UID1000 app required')
    fields = ' '.join(lines[5:-1]).split()
    if len(fields) != 20 or any(re.fullmatch('[0-9a-fA-F]{2}', part) is None for part in fields):
        raise ValueError('exact bounded guest ELF header required')
    header = bytes(int(part, 16) for part in fields)
    if header[:7] != b'\x7fELF\x02\x01\x01' or header[7] != 9 or int.from_bytes(header[18:20], 'little') != 183:
        raise ValueError('guest /bin/sh must be actual FreeBSD ELF64 AArch64 OSABI9')
    return {'guest_system': 'FreeBSD', 'guest_machine': 'arm64', 'guest_processor': 'aarch64',
            'bootstrap_uid': 0, 'application_uid': 1000,
            'system_executable': {'path': '/bin/sh', 'format': 'ELF64', 'elf_machine': 183, 'elf_osabi': 9}}


def _firmware(path):
    path = Path(path).resolve(strict=True)
    with vm._pin(path) as stream:
        if os.fstat(stream.fileno()).st_size != FIRMWARE_BYTES:
            raise ValueError('installed AAVMF firmware must be a regular 64 MiB image')
    return path


def prepare(archive, expected_sha256, work_dir, *, port=22242,
            firmware_code=Path('/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd'),
            firmware_vars=Path('/usr/share/AAVMF/AAVMF_VARS.fd')):
    vm._linux()
    vm._tool('qemu-system-aarch64')
    code, variables = _firmware(firmware_code), _firmware(firmware_vars)
    if code == variables:
        raise ValueError('separate AAVMF code and variables inputs required')
    # Generic preparation only verifies/extracts the supplied official archive,
    # creates its COW disk and creates a key-only NoCloud seed. It never boots.
    generic = vm.prepare(archive, expected_sha256, work_dir, port=port)
    directory = vm._directory(work_dir)
    user_data, meta_data = arm_seed_data((directory / 'task_ed25519.pub').read_text().strip(), generic['instance_id'])
    from tools.freebsd_seed import create_seed
    seed = create_seed(directory / 'arm-seed.iso', user_data=user_data, meta_data=meta_data)
    firmware = {}
    for name, source in [('uefi-code.fd', code), ('uefi-vars.fd', variables)]:
        with vm._pin(source) as source_stream, (directory / name).open('xb') as output:
            shutil.copyfileobj(source_stream, output, 1024 * 1024)
            output.flush(); os.fsync(output.fileno())
        (directory / name).chmod(0o444 if name == 'uefi-code.fd' else 0o600)
        firmware[name] = {'source': str(source), 'sha256': vm._digest(directory / name),
                          'size_bytes': FIRMWARE_BYTES, 'writable_guest_variables': name == 'uefi-vars.fd'}
    result = {'format': 'hf-freebsd-arm-task-vm-v1', 'prepared': True, 'brand': 'DaOS',
              'guest_system': 'FreeBSD', 'guest_machine': 'arm64', 'guest_processor': 'aarch64',
              'target_architecture': 'aarch64', 'emulator': 'qemu-system-aarch64', 'acceleration': 'tcg',
              'cpu': 'cortex-a72', 'cpus': 4, 'memory_mib': 4096, 'ssh_port': port,
              'generic_vm_metadata_sha256': vm._digest(directory / 'vm.json'),
              'base_sha256': generic['base_sha256'], 'seed_sha256': seed['sha256'], 'seed_file': 'arm-seed.iso',
              'firmware': firmware, 'secure_boot_validated': False,
              'source_sha256': {name: vm._digest(ROOT / name) for name in
                               ('tools/freebsd_arm_vm.py', 'tools/freebsd_vm.py', 'tools/freebsd_seed.py')},
              'host_mounts': False, 'host_shares': False, 'physical_camera_validated': False}
    vm._write_json(directory / 'arm-vm.json', result)
    return result


def _metadata(work_dir):
    directory, generic = vm._metadata(work_dir)
    value = vm._read_json(directory / 'arm-vm.json')
    if (value.get('format') != 'hf-freebsd-arm-task-vm-v1' or value.get('prepared') is not True or
            value.get('guest_machine') != 'arm64' or value.get('target_architecture') != 'aarch64' or
            value.get('acceleration') != 'tcg' or value.get('brand') != 'DaOS' or
            value.get('ssh_port') != generic['ssh_port'] or
            value.get('generic_vm_metadata_sha256') != vm._digest(directory / 'vm.json')):
        raise ValueError('independent ARM64 VM metadata required; no amd64 substitution')
    return directory, value


def qemu_command(work_dir, *, restricted=False, cpus=4):
    directory, metadata = _metadata(work_dir)
    if type(restricted) is not bool or type(cpus) is not int or cpus not in (4, 8):
        raise ValueError('explicit network mode and bounded 4/8 ARM64 CPUs required')
    return [vm._tool('qemu-system-aarch64'), '-machine', 'virt,gic-version=3', '-accel', 'tcg,thread=multi',
            '-cpu', 'cortex-a72', '-smp', str(cpus), '-m', '4096', '-display', 'none', '-serial', 'stdio',
            '-monitor', 'none', '-no-reboot', '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off',
            '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=' + str(directory / 'uefi-code.fd'),
            '-drive', 'if=pflash,format=raw,unit=1,file=' + str(directory / 'uefi-vars.fd'),
            '-drive', 'file=' + str(directory / 'guest.qcow2') + ',format=qcow2,if=none,id=taskroot,cache=writeback',
            '-device', 'virtio-blk-pci,drive=taskroot', '-device', 'virtio-scsi-pci,id=taskscsi',
            '-drive', 'file=' + str(directory / 'arm-seed.iso') + ',format=raw,if=none,id=taskseed,media=cdrom,readonly=on',
            '-device', 'scsi-cd,drive=taskseed,bus=taskscsi.0',
            '-netdev', 'user,id=tasknet,restrict=' + ('on' if restricted else 'off') +
            ',hostfwd=tcp:127.0.0.1:' + str(metadata['ssh_port']) + '-:22',
            '-device', 'virtio-net-pci,netdev=tasknet', '-sandbox',
            'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def serve(work_dir, *, boot_timeout=1200, lifetime=28800, restricted=False, cpus=4):
    vm._linux()
    vm._number(boot_timeout, 10, 1800, 'ARM64 boot timeout')
    vm._number(lifetime, 30, 28800, 'ARM64 VM lifetime')
    directory, metadata = _metadata(work_dir)
    with vm._lock(directory):
        immutable = {'base.qcow2': metadata['base_sha256'], 'arm-seed.iso': metadata['seed_sha256'],
                     'uefi-code.fd': metadata['firmware']['uefi-code.fd']['sha256']}
        for name, expected in immutable.items():
            if vm._digest(directory / name) != expected:
                raise ValueError('immutable ARM64 VM input changed')
        for name in ('guest.qcow2', 'uefi-vars.fd'):
            with vm._pin(directory / name, writable=True) as stream:
                if name == 'uefi-vars.fd' and os.fstat(stream.fileno()).st_size != FIRMWARE_BYTES:
                    raise ValueError('private ARM64 UEFI variables image changed size')
        if os.path.lexists(directory / 'qmp.sock'):
            raise ValueError('stale ARM64 QMP socket retained; inspect before boot')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', metadata['ssh_port']))
        command = qemu_command(directory, restricted=restricted, cpus=cpus)
        boot = directory / ('arm-boot-' + str(uuid.uuid4()))
        boot.mkdir(mode=0o700)
        sources = boot / 'source'; sources.mkdir()
        source_hashes = {}
        for relative in ('tools/freebsd_arm_vm.py', 'tools/freebsd_vm.py', 'tools/freebsd_seed.py'):
            source = ROOT / relative
            shutil.copyfile(source, sources / source.name)
            source_hashes[relative] = vm._digest(source)
        started = time.monotonic(); ready = False; reason = 'not started'; process = None
        try:
            with (boot / 'serial.log').open('xb') as serial:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT)
                vm._write_json(boot / 'launch.json', {'format': 'hf-freebsd-arm-vm-launch-v1', 'brand': 'DaOS',
                    'command': command, 'pid': process.pid, 'acceleration': 'tcg', 'cpus': cpus, 'memory_mib': 4096,
                    'guest_egress_enabled': not restricted, 'ssh_loopback_only': True, 'host_mounts': False,
                    'host_shares': False, 'immutable_sha256': immutable, 'source_sha256': source_hashes})
                next_probe = started
                while process.poll() is None:
                    now = time.monotonic()
                    if os.fstat(serial.fileno()).st_size > vm.MAX_SERIAL:
                        raise RuntimeError('ARM64 VM serial output exceeded bound')
                    if now - started > lifetime:
                        raise RuntimeError('owned ARM64 VM lifetime expired')
                    if not ready and now - started > boot_timeout:
                        raise RuntimeError('ARM64 FreeBSD SSH did not finish within boot budget')
                    if not ready and now >= next_probe:
                        try:
                            checked = vm.ssh_command(directory, READY_COMMAND, timeout=15)
                        except RuntimeError:
                            checked = {'returncode': 255, 'stdout': ''}
                        if checked['returncode'] == 0:
                            identity = validate_ready(checked['stdout'])
                            ready = True
                            result = {'format': 'hf-freebsd-arm-vm-ready-v1', 'ready': True, 'brand': 'DaOS',
                                'boot_directory': str(boot), 'pid': process.pid, 'ssh_host': '127.0.0.1',
                                'ssh_port': metadata['ssh_port'], 'task_key_file': str(directory / 'task_ed25519'),
                                'acceleration': 'tcg', 'cpus': cpus, 'memory_mib': 4096,
                                'guest_egress_enabled': not restricted, 'seconds': time.monotonic() - started,
                                'identity': identity, 'physical_camera_validated': False}
                            vm._write_json(boot / 'ready.json', result)
                            print(json.dumps(result, sort_keys=True), flush=True)
                        next_probe = time.monotonic() + 3
                    time.sleep(.1)
                reason = 'guest exited'
                if not ready or process.returncode:
                    raise RuntimeError('ARM64 VM exited without a ready lifecycle; inspect retained serial.log')
        except BaseException as error:
            reason = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        vm.qmp(directory, 'system_powerdown', timeout=2)
                        process.wait(timeout=120)
                    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                        pass
                vm._stop(process)
            after = {name: vm._digest(directory / name) for name in immutable}
            unchanged = immutable == after
            vm._write_json(boot / 'closed.json', {'format': 'hf-freebsd-arm-vm-closed-v1', 'ready': ready,
                'returncode': process.returncode if process else None, 'reason': reason, 'immutable_unchanged': unchanged,
                'immutable_sha256_after': after, 'serial_sha256': vm._digest(boot / 'serial.log') if (boot / 'serial.log').exists() else None,
                'seconds': time.monotonic() - started})
            if not unchanged:
                raise RuntimeError('immutable ARM64 VM base, seed or firmware code changed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--archive', type=Path, required=True)
    command.add_argument('--sha256', required=True)
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--ssh-port', type=int, default=22242)
    command = sub.add_parser('serve')
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--boot-timeout', type=float, default=1200)
    command.add_argument('--lifetime', type=float, default=28800)
    command.add_argument('--cpus', type=int, choices=(4, 8), default=4)
    command.add_argument('--restricted', action='store_true')
    command = sub.add_parser('ssh')
    command.add_argument('--work-dir', type=Path, required=True)
    command.add_argument('--user', choices=('root', 'camera'), default='root')
    command.add_argument('--timeout', type=float, default=60)
    command.add_argument('--command', required=True)
    for name in ('put', 'get'):
        command = sub.add_parser(name)
        command.add_argument('--work-dir', type=Path, required=True)
        command.add_argument('--user', choices=('root', 'camera'), default='root')
        command.add_argument('--timeout', type=float, default=1200)
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
        serve(args.work_dir, boot_timeout=args.boot_timeout, lifetime=args.lifetime,
              restricted=args.restricted, cpus=args.cpus)
        return 0
    else:
        _metadata(args.work_dir)
        if args.action == 'ssh':
            result = vm.ssh_command(args.work_dir, args.command, user=args.user, timeout=args.timeout)
        elif args.action in ('put', 'get'):
            result = (vm.copy_to if args.action == 'put' else vm.copy_from)(args.work_dir, args.source,
                args.destination, user=args.user, timeout=args.timeout)
        else:
            result = vm.qmp(args.work_dir, 'query-status' if args.action == 'status' else 'system_powerdown')
    print(json.dumps(result, sort_keys=True, indent=2))
    return result.get('returncode', 0) if isinstance(result, dict) else 0


if __name__ == '__main__':
    if __package__ in (None, ''):
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())

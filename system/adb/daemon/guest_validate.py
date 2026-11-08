#!/usr/local/bin/python3.12
"""Private FreeBSD ARM64 reference-guest validation, never a production service."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def elf(path):
    header = Path(path).read_bytes()[:64]
    if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01':
        raise ValueError('ELF64 little-endian required: ' + str(path))
    machine = struct.unpack_from('<H', header, 18)[0]
    if machine != 183:
        raise ValueError('Reference validation refuses a non-AArch64 artifact')
    return {'machine': machine, 'type': struct.unpack_from('<H', header, 16)[0],
            'OSABI': header[7], 'sha256': digest(path), 'bytes': Path(path).stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'stop'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    if not root.is_absolute() or root.is_symlink() or not root.name.startswith('daos-adb-validation-') or root.parent != Path('/var'):
        raise ValueError('Explicit private /var/daos-adb-validation-* root required')
    if args.action == 'stop':
        record = json.loads((root / 'daemon-process.json').read_text())
        pid = record['pid']
        checked = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
        if checked.returncode == 0 and str(root / 'daemon/daos_adbd.py') in checked.stdout:
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                time.sleep(.05)
                checked = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
                if checked.returncode or checked.stdout.strip().startswith('Z'):
                    break
            else:
                raise RuntimeError('Owned test daemon did not stop gracefully')
        result = {'owned_test_daemon_stopped': True, 'pid': pid, 'time': time.time()}
        (root / 'daemon-stop.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return
    receipt = {'format': 'DaOS-FreeBSD-ARM64-USB-target-validation-v1', 'status': 'running',
               'usb_c_hardware_validated': False, 'physical_usb_enumeration_validated': False,
               'started_at_unix': time.time(), 'commands': []}
    def save():
        (root / 'target-module-evidence.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    def run(command, *, required=True):
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=30)
        record = {'argv': list(map(str, command)), 'returncode': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr}
        receipt['commands'].append(record)
        save()
        if required and result.returncode:
            raise RuntimeError('Guest validation command failed: ' + repr(command) + ': ' + result.stderr)
        return result
    try:
        system = run(['/usr/bin/uname', '-s']).stdout.strip()
        machine = run(['/usr/bin/uname', '-m']).stdout.strip()
        arch = run(['/sbin/sysctl', '-n', 'hw.machine_arch']).stdout.strip()
        release = run(['/usr/bin/uname', '-r']).stdout.strip()
        identity = {'system': system, 'machine': machine, 'architecture': arch,
                    'release': release, 'python': sys.version, 'uid': os.geteuid(),
                    'uname': run(['/usr/bin/uname', '-a']).stdout.strip(), 'bin_sh': elf('/bin/sh')}
        if system != 'FreeBSD' or machine != 'arm64' or arch != 'aarch64' or not release.startswith('15.1-RELEASE'):
            raise ValueError('Reference guest identity does not match the FreeBSD 15.1 ARM64 target')
        (root / 'target-identity.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
        receipt['identity'] = identity
        build = json.loads((root / 'native/build-evidence.json').read_text())
        expected = build['artifacts']
        facts = {'transport': elf(root / 'native/daos-usb-adb'),
                 'kernel_module': elf(root / 'native/daos_adb_template.ko')}
        for name, actual in facts.items():
            if actual['sha256'] != expected[name]['sha256'] or actual['bytes'] != expected[name]['size']:
                raise ValueError('Compiled native artifact differs from its source-build receipt')
        manifest = json.loads((root / 'runtime-manifest.json').read_text())
        for name, value in manifest['files'].items():
            if digest(root / 'daemon' / name) != value['sha256']:
                raise ValueError('Frozen Python runtime differs from its manifest: ' + name)
        receipt.update(native_artifacts=facts, build_receipt_sha256=digest(root / 'native/build-evidence.json'),
                       daemon_runtime_manifest_sha256=digest(root / 'runtime-manifest.json'))
        (root / 'native/daos-usb-adb').chmod(0o755)
        (root / 'dmesg-before.txt').write_text(run(['/sbin/dmesg']).stdout)
        run(['/sbin/kldstat'])
        template = run(['/sbin/kldstat', '-n', 'usb_template.ko'], required=False)
        if template.returncode:
            run(['/sbin/kldload', 'usb_template'])
        own = run(['/sbin/kldstat', '-n', 'daos_adb_template.ko'], required=False)
        if own.returncode == 0:
            raise ValueError('A custom ADB template was already loaded; do not substitute unverified existing module evidence')
        receipt['template_before'] = run(['/sbin/sysctl', '-n', 'hw.usb.template']).stdout.strip()
        run(['/sbin/kldload', root / 'native/daos_adb_template.ko'])
        run(['/sbin/sysctl', 'hw.usb.template=55813'])
        selected = run(['/sbin/sysctl', '-n', 'hw.usb.template']).stdout.strip()
        if selected != '55813':
            raise ValueError('Custom USB template did not become selected')
        listed = run([root / 'native/daos-usb-adb', '--list'], required=False)
        if listed.returncode not in (0, 1):
            raise ValueError('Native helper enumeration failed unexpectedly')
        receipt.update(kernel_module_loaded=True, selected_template=55813,
                       helper_list_returncode=listed.returncode, no_physical_usb_expected=True)
        run(['/sbin/kldstat'])
        (root / 'dmesg-after.txt').write_text(run(['/sbin/dmesg']).stdout)
        (root / 'sync').mkdir()
        (root / 'shell').mkdir()
        key_rows = []
        for path in sorted((root / 'keys').glob('*.pub')):
            key_rows.append(path.read_text().rstrip('\r\n'))
        (root / 'authorized_keys').write_text('\n'.join(key_rows) + '\n')
        command = [sys.executable, '-B', str(root / 'daemon/daos_adbd.py'), '--listen', '127.0.0.1:0',
                   '--authorized-keys', str(root / 'authorized_keys'), '--sync-root', str(root / 'sync'),
                   '--cwd', str(root / 'shell'), '--ready-file', str(root / 'daemon-ready.json')]
        with (root / 'daemon-stderr.jsonl').open('xb') as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=log, close_fds=True, start_new_session=True)
        (root / 'daemon-process.json').write_text(json.dumps({'pid': child.pid, 'argv': command}, indent=2))
        deadline = time.monotonic() + 10
        while not (root / 'daemon-ready.json').exists():
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('FreeBSD ARM64 Python device daemon did not become ready')
            time.sleep(.05)
        ready = json.loads((root / 'daemon-ready.json').read_text())
        receipt.update(status='success', daemon_ready=ready, completed_at_unix=time.time(),
                       scope='kernel module loads/selects plus private loopback protocol endpoint; no physical USB host')
        save()
        print(json.dumps({'status': 'success', 'identity': identity, 'daemon_ready': ready,
                          'module_loaded': True, 'selected_template': 55813, 'usb_hardware_validated': False}))
    except BaseException as error:
        receipt.update(status='failed', error_type=type(error).__name__, error=str(error), completed_at_unix=time.time())
        save()
        raise


if __name__ == '__main__':
    main()

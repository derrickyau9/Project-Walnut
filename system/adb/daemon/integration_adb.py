#!/usr/bin/env python3
"""Exercise the device daemon with an actual official adb host, over loopback.

Uses an isolated foreground adb server and a dedicated development key. Never
contacts the normal port 5037, kills another server, or claims USB validation.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vacant_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_suite(adb, work, key, transport='loopback-tcp', *, endpoint=None, remote_sync_root=None,
              target_identity=None):
    work.mkdir(parents=True, exist_ok=False)
    (work / 'sync').mkdir()
    (work / 'shell').mkdir()
    home = work / 'host-home'
    (home / '.android').mkdir(parents=True, mode=0o700)
    shutil.copyfile(key, home / '.android/adbkey')
    (home / '.android/adbkey').chmod(0o600)
    shutil.copyfile(str(key) + '.pub', home / '.android/adbkey.pub')
    environment = dict(os.environ, HOME=str(home), ADB_VENDOR_KEYS=str(key),
                       ADB_MDNS_AUTO_CONNECT='', ADB_LOCAL_TRANSPORT_MAX_PORT='0')
    port = vacant_port()
    host_command = [str(adb), '-L', 'tcp:' + str(port),
                    '--one-device', 'DAOS-INTEGRATION-NO-USB', 'server', 'nodaemon']
    receipt = {'format': 'DaOS-ADB-official-host-integration-v1', 'status': 'running',
               'transport': transport, 'usb_hardware_validated': False,
               'freebsd_target_execution_validated': False,
               'private_key_material_recorded': False, 'started_at_unix': time.time(),
               'adb': {'path': str(adb), 'sha256': digest(adb)},
               'source_sha256': {p.name: digest(p) for p in Path(__file__).parent.glob('*.py')},
               'cases': [], 'work': str(work)}
    if endpoint is not None:
        if (not endpoint.startswith('127.0.0.1:') or not endpoint.rpartition(':')[2].isdecimal()
                or not remote_sync_root or not remote_sync_root.startswith('/')):
            raise ValueError('Remote validation requires an explicit loopback SSH endpoint and absolute private sync root')
        receipt['target_identity'] = target_identity
    result_file = work / 'integration-receipt.json'

    def save():
        result_file.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')

    def command(arguments, *, input=None, timeout=20):
        argv = [str(adb), '-P', str(port), *arguments]
        result = subprocess.run(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, timeout=timeout)
        record = {'argv': argv, 'returncode': result.returncode,
                  'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
                  'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
                  'stderr_sha256': hashlib.sha256(result.stderr).hexdigest()}
        if len(result.stdout) < 2048:
            record['stdout'] = result.stdout.decode('utf-8', errors='replace')
        if len(result.stderr) < 2048:
            record['stderr'] = result.stderr.decode('utf-8', errors='replace')
        receipt['cases'].append(record)
        save()
        return result

    daemon = server = None
    daemon_log = (work / 'device-stderr.jsonl').open('xb')
    server_log = (work / 'host-server.log').open('xb')
    try:
        if endpoint is None:
            daemon_command = [sys.executable, str(Path(__file__).with_name(
                'stdio_bridge.py' if transport == 'stdio-via-loopback' else 'daos_adbd.py'))]
            if transport != 'stdio-via-loopback':
                daemon_command += ['--listen', '127.0.0.1:0']
            daemon_command += ['--authorized-keys', str(key) + '.pub', '--sync-root', str(work / 'sync'),
                               '--cwd', str(work / 'shell'), '--ready-file', str(work / 'ready.json')]
            daemon = subprocess.Popen(daemon_command,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=daemon_log)
            deadline = time.monotonic() + 10
            while not (work / 'ready.json').exists():
                if daemon.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('device daemon did not become ready')
                time.sleep(.05)
            ready = json.loads((work / 'ready.json').read_text())
            serial = '127.0.0.1:' + str(ready['port'])
            receipt['authorized_public_fingerprints'] = ready['authorized_key_fingerprints']
            receipt['device'] = ready
        else:
            serial = endpoint
            receipt['device'] = {'transport': 'SSH-forwarded remote loopback TCP', 'endpoint': endpoint,
                                 'remote_private_sync_root': remote_sync_root, 'usb_hardware_validated': False}
        server = subprocess.Popen(host_command, stdin=subprocess.DEVNULL, stdout=server_log,
                                  stderr=subprocess.STDOUT, env=environment)
        deadline = time.monotonic() + 10
        while True:
            if server.poll() is not None:
                raise RuntimeError('isolated foreground adb server exited')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('isolated adb server did not listen')
                time.sleep(.05)
        version = subprocess.run([str(adb), 'version'], capture_output=True, text=True, check=True)
        receipt['adb']['version'] = version.stdout
        connected = command(['connect', serial])
        if connected.returncode:
            raise AssertionError('official adb connect failed')
        state = command(['-s', serial, 'get-state'])
        assert state.returncode == 0 and state.stdout.strip() == b'device', 'RSA AUTH did not authorize the host'
        features = command(['-s', serial, 'features'])
        assert features.returncode == 0 and b'shell_v2' in features.stdout
        separated = command(['-s', serial, 'shell', '-T', 'printf DAOS_STDOUT; printf DAOS_STDERR >&2; exit 7'])
        assert separated.returncode == 7 and separated.stdout == b'DAOS_STDOUT' and separated.stderr == b'DAOS_STDERR', 'shell_v2 output/exit mismatch'
        legacy = command(['-s', serial, 'shell', '-x', 'printf DAOS_LEGACY'])
        assert legacy.returncode == 0 and legacy.stdout == b'DAOS_LEGACY', 'legacy shell mismatch'
        payload = bytes(range(256)) * 1024
        echoed = command(['-s', serial, 'shell', '-T', 'cat'], input=payload)
        assert echoed.returncode == 0 and echoed.stdout == payload and not echoed.stderr, 'binary stdin/close-stdin mismatch'
        large = command(['-s', serial, 'shell', '-T', 'dd if=/dev/zero bs=65536 count=8 2>/dev/null'])
        assert large.returncode == 0 and large.stdout == bytes(524288), 'flow-controlled large output mismatch'
        terminal = command(['-s', serial, 'shell', '-tt', 'test -t 0 && test -t 1 && printf DAOS_PTY_OK; stty size'])
        assert terminal.returncode == 0 and b'DAOS_PTY_OK' in terminal.stdout, 'forced PTY was not a terminal'
        interactive = command(['-s', serial, 'shell', '-tt'], input=b'printf DAOS_INTERACTIVE_OK\\n\nexit 9\n')
        assert interactive.returncode == 9 and b'DAOS_INTERACTIVE_OK' in interactive.stdout, 'interactive shell exit mismatch'
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(command, ['-s', serial, 'shell', '-T', f'sleep 0.1; printf MULTIPLEX_{index}']) for index in range(4)]
            for index, future in enumerate(futures):
                value = future.result()
                assert value.returncode == 0 and value.stdout == f'MULTIPLEX_{index}'.encode(), 'multiplexing mismatch'
        source = work / 'push-source.bin'
        source.write_bytes(payload)
        pushed = command(['-s', serial, 'push', '-Z', str(source), '/transfer.bin'])
        assert pushed.returncode == 0, 'sync push failed'
        if endpoint is None:
            assert (work / 'sync/transfer.bin').read_bytes() == payload, 'sync push mismatch'
        else:
            copied = command(['-s', serial, 'shell', '-T', 'cat ' + shlex.quote(remote_sync_root.rstrip('/') + '/transfer.bin')])
            assert copied.returncode == 0 and copied.stdout == payload, 'target sync file differs from pushed bytes'
        pulled = command(['-s', serial, 'pull', '-Z', '/transfer.bin', str(work / 'pulled.bin')])
        assert pulled.returncode == 0 and (work / 'pulled.bin').read_bytes() == payload, 'sync pull mismatch'
        unsupported = command(['-s', serial, 'root'])
        assert unsupported.returncode != 0, 'unsupported root service was not rejected'
        cancellation = subprocess.Popen([str(adb), '-P', str(port), '-s', serial, 'shell', '-T',
                                          'echo $$; exec sleep 60'], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        try:
            child_line = cancellation.stdout.readline()
            assert child_line.strip().isdigit(), 'cancellation shell did not report its PID'
            child_pid = int(child_line)
            stop(cancellation)
            deadline = time.monotonic() + 5
            if endpoint is None:
                while Path('/proc', str(child_pid)).exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                assert not Path('/proc', str(child_pid)).exists(), 'disconnect left the shell process alive'
            else:
                while True:
                    probe = command(['-s', serial, 'shell', '-T', f'kill -0 {child_pid} 2>/dev/null'])
                    if probe.returncode != 0:
                        break
                    assert time.monotonic() < deadline, 'disconnect left the target shell process alive'
                    time.sleep(.1)
            receipt['disconnect_cleanup'] = {'child_pid': child_pid, 'reaped': True}
        finally:
            stop(cancellation)
        command(['disconnect', serial])
        # A separate, real adb server with a different private key must remain
        # unauthorized. The production daemon must never accept AUTH public
        # keys merely because the host sent one.
        unauthorized_home = work / 'unauthorized-home'
        (unauthorized_home / '.android').mkdir(parents=True, mode=0o700)
        unauthorized_key = unauthorized_home / '.android/adbkey'
        subprocess.run([str(adb), 'keygen', str(unauthorized_key)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unauthorized_key.chmod(0o600)
        bad_environment = dict(environment, HOME=str(unauthorized_home), ADB_VENDOR_KEYS=str(unauthorized_key))
        bad_port = vacant_port()
        with (work / 'unauthorized-host.log').open('xb') as bad_log:
            bad_server = subprocess.Popen([str(adb), '-L', 'tcp:' + str(bad_port),
                                           '--one-device', 'DAOS-INTEGRATION-NO-USB', 'server', 'nodaemon'],
                                          stdin=subprocess.DEVNULL, stdout=bad_log, stderr=subprocess.STDOUT,
                                          env=bad_environment)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if bad_server.poll() is not None:
                        raise RuntimeError('unauthorized test adb server exited')
                    try:
                        with socket.create_connection(('127.0.0.1', bad_port), timeout=.2):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('unauthorized adb server did not listen')
                        time.sleep(.05)
                bad_connect = subprocess.run([str(adb), '-P', str(bad_port), 'connect', serial],
                                             capture_output=True, env=bad_environment, timeout=15)
                denied = subprocess.run([str(adb), '-P', str(bad_port), '-s', serial,
                                         'shell', '-T', 'printf UNAUTHORIZED_MUST_NOT_RUN'],
                                        capture_output=True, env=bad_environment, timeout=15)
                assert denied.returncode != 0 and b'UNAUTHORIZED_MUST_NOT_RUN' not in denied.stdout
                receipt['unauthorized_host'] = {
                    'connect_returncode': bad_connect.returncode,
                    'connect_stdout': bad_connect.stdout.decode(errors='replace'),
                    'shell_returncode': denied.returncode,
                    'shell_stderr': denied.stderr.decode(errors='replace'),
                    'shell_rejected': True, 'public_key_was_not_auto_enrolled': True}
            finally:
                stop(bad_server)
        receipt.update(status='success', completed_at_unix=time.time(),
                       authenticated=True, shell_v2=True, legacy_shell=True, binary_stdin=True,
                       pty=True, interactive_pty=True, multiplex=True, sync_push_pull=True)
        if endpoint is not None:
            identity = target_identity or {}
            receipt['freebsd_target_execution_validated'] = (
                identity.get('system') == 'FreeBSD' and identity.get('machine') == 'arm64'
                and identity.get('architecture') == 'aarch64')
        save()
    except BaseException as error:
        receipt.update(status='failed', error_type=type(error).__name__, error=str(error),
                       completed_at_unix=time.time())
        save()
        raise
    finally:
        stop(server)
        stop(daemon)
        daemon_log.close()
        server_log.close()
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True, help='Dedicated development host key; never printed')
    parser.add_argument('--work', type=Path, required=True, help='A new private run directory on F-backed WSL')
    parser.add_argument('--transport', choices=('loopback-tcp', 'stdio-via-loopback', 'ssh-forwarded-loopback'), default='loopback-tcp')
    parser.add_argument('--endpoint', help='Existing remote daemon reached through an owned loopback SSH tunnel')
    parser.add_argument('--remote-sync-root', help='Absolute private target directory used by the remote daemon')
    parser.add_argument('--target-identity', type=Path, help='Previously verified target identity JSON')
    args = parser.parse_args()
    identity = json.loads(args.target_identity.read_text()) if args.target_identity else None
    receipt = run_suite(args.adb.resolve(strict=True), args.work.absolute(), args.key.resolve(strict=True), args.transport,
                        endpoint=args.endpoint, remote_sync_root=args.remote_sync_root, target_identity=identity)
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.work / 'integration-receipt.json'),
                      'cases': len(receipt['cases']), 'usb_hardware_validated': False}, indent=2))


if __name__ == '__main__':
    main()

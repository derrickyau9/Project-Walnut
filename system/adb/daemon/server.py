"""Authenticated ADB device endpoint. Default transport is binary stdio.

Network serving is available only through explicit --listen 127.0.0.1:PORT.
Authorized public keys must be provisioned before startup. A received AUTH
public key is never installed automatically. Shells run as the daemon's UID.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import signal
import socket
import sys
import threading
import time

from .auth import load_authorized_keys
from .protocol import Disconnected, MAX_PAYLOAD, ProtocolError, Transport, VERSION
from .shell_service import ShellService, parse_service
from .sync_service import SyncService

def developer_access():
    """Missing marker preserves the development image's existing ADB policy."""
    marker=Path('/var/DaOS/system/developer-mode')
    try:
        if marker.is_symlink() or marker.stat().st_size>32:return False
        return marker.read_text().strip()=='enabled'
    except FileNotFoundError:return True
    except OSError:return False

def service_allowed(destination):
    return developer_access() or destination in (
        'exec:/usr/local/bin/python3.12 -B /usr/local/libexec/DaOS-camera-link.py',
        'shell,v2,raw:/usr/local/bin/python3.12 -B /usr/local/libexec/DaOS-camera-link.py')


class Stream:
    def __init__(self, connection, local_id, remote_id):
        self.connection, self.local_id, self.remote_id = connection, local_id, remote_id
        self.closed = threading.Event()
        self.credit = threading.Event()
        self.credit.set()  # OPEN also grants the first outbound write credit.
        self.output_lock = threading.Lock()
        self.input_lock = threading.Lock()
        self.input_pending = False
        self.incoming = queue.Queue(maxsize=1)
        self.thread = None

    def write(self, data):
        with self.output_lock:
            for offset in range(0, len(data), self.connection.maxdata):
                if not self.credit.wait(30):
                    raise Disconnected('peer did not acknowledge stream data')
                if self.closed.is_set() or self.connection.transport.closed.is_set():
                    raise Disconnected('stream closed')
                self.credit.clear()
                self.connection.transport.send('WRTE', self.local_id, self.remote_id,
                                               data[offset:offset + self.connection.maxdata])

    def deliver(self, data):
        with self.input_lock:
            if self.input_pending:
                raise ProtocolError('peer sent a second WRTE without OKAY')
            self.input_pending = True
            self.incoming.put_nowait(data)

    def receive(self, timeout=.025):
        if self.closed.is_set():
            raise Disconnected('stream closed')
        try:
            value = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        with self.input_lock:
            self.input_pending = False
        self.connection.transport.send('OKAY', self.local_id, self.remote_id)
        return value

    def receive_nowait(self):
        return self.receive(timeout=0)

    def finish(self):
        if self.credit.wait(30) and not self.closed.is_set():
            self.connection.transport.send('CLSE', self.local_id, self.remote_id)
        self.close()

    def close(self):
        self.closed.set()
        self.credit.set()
        with self.connection.stream_lock:
            self.connection.streams.pop(self.local_id, None)


class Connection:
    def __init__(self, transport, keys, *, shell='/bin/sh', cwd='/', serial='DAOS-DEV', sync_root=None):
        self.transport, self.keys, self.shell, self.cwd = transport, keys, shell, cwd
        self.serial, self.sync_root = serial, sync_root
        self.authenticated = False
        self.version = 0
        self.maxdata = MAX_PAYLOAD
        self.token = None
        self.auth_attempts = 0
        self.streams, self.next_id = {}, 1
        self.stream_lock = threading.Lock()
        self.workers = []

    def log(self, event, **fields):
        print(json.dumps({'event': event, 'time': time.time(), **fields}, sort_keys=True),
              file=sys.stderr, flush=True)

    def challenge(self):
        self.auth_attempts += 1
        if self.auth_attempts > 16:
            raise ProtocolError('authentication attempt limit reached')
        self.token = secrets.token_bytes(20)
        self.transport.send('AUTH', 1, 0, self.token)

    def connected(self):
        self.authenticated = True
        self.token = None
        features = 'shell_v2'
        banner = ('device::ro.product.name=DaOS;ro.product.model=DaOS_BSD;'
                  'ro.product.device=daos;features=' + features + ';').encode('ascii') + b'\0'
        self.transport.send('CNXN', self.version, MAX_PAYLOAD, banner)
        self.log('authenticated', serial=self.serial)

    def open(self, remote_id, remote_arg, payload):
        if not remote_id or remote_arg != 0 or not payload.endswith(b'\0') or b'\0' in payload[:-1]:
            raise ProtocolError('invalid OPEN packet')
        try:
            destination = payload[:-1].decode('utf-8')
            if not service_allowed(destination):
                raise ValueError('Developer shell and raw filesystem access are disabled')
            if len(destination) > 4096:
                raise ValueError('service name too long')
            if destination == 'sync:' and self.sync_root is not None:
                options = None
            else:
                options = parse_service(destination)
        except (UnicodeError, ValueError):
            self.transport.send('CLSE', 0, remote_id)
            self.log('service_rejected', remote_id=remote_id)
            return
        with self.stream_lock:
            if len(self.streams) >= 16 or any(item.remote_id == remote_id for item in self.streams.values()):
                self.transport.send('CLSE', 0, remote_id)
                return
            local_id = self.next_id
            self.next_id = self.next_id % 0xfffffffe + 1
            stream = Stream(self, local_id, remote_id)
            self.streams[local_id] = stream
        service = (SyncService(stream, self.sync_root) if options is None else
                   ShellService(stream, options, self.shell, self.cwd))
        self.transport.send('OKAY', local_id, remote_id)

        def run():
            try:
                service.run()
            except (Disconnected, OSError, ValueError) as error:
                self.log('service_ended', stream=local_id, reason=str(error))
                if not self.transport.closed.is_set() and not stream.closed.is_set():
                    with contextlib.suppress(OSError, Disconnected):
                        self.transport.send('CLSE', local_id, remote_id)
            finally:
                if isinstance(service, ShellService):
                    service.cleanup()
                stream.close()

        worker = threading.Thread(target=run, name='daos-adb-service-' + str(local_id), daemon=True)
        stream.thread = worker
        self.workers = [item for item in self.workers if item.is_alive()]
        self.workers.append(worker)
        worker.start()

    def run(self):
        self.log('transport_opened', authentication_required=True)
        try:
            while not self.transport.closed.is_set():
                name, arg0, arg1, payload = self.transport.receive(self.version)
                if name == 'CNXN':
                    if self.version or arg0 < 0x01000000 or arg1 < 4096:
                        raise ProtocolError('unsupported or repeated CONNECT')
                    self.version, self.maxdata = min(arg0, VERSION), min(arg1, MAX_PAYLOAD)
                    self.challenge()
                    continue
                if not self.version:
                    continue
                if not self.authenticated:
                    if name != 'AUTH' or arg1 != 0:
                        raise ProtocolError('service requested before authentication')
                    if arg0 == 2 and any(key.verify(self.token, payload) for key in self.keys):
                        self.connected()
                    elif arg0 in (2, 3):
                        self.log('authorization_denied', received_public_key=arg0 == 3)
                        self.challenge()
                    else:
                        raise ProtocolError('unsupported authentication packet')
                    continue
                if name == 'OPEN':
                    self.open(arg0, arg1, payload)
                elif name in ('WRTE', 'OKAY', 'CLSE'):
                    with self.stream_lock:
                        stream = self.streams.get(arg1)
                    if stream is None:
                        continue
                    if arg0 != stream.remote_id:
                        raise ProtocolError('stream peer ID changed')
                    if name == 'WRTE':
                        stream.deliver(payload)
                    elif name == 'OKAY':
                        if payload:
                            raise ProtocolError('unexpected delayed-ack payload')
                        stream.credit.set()
                    else:
                        stream.close()
                else:
                    raise ProtocolError('unexpected command on authenticated transport')
        except (Disconnected, OSError, ProtocolError) as error:
            self.log('transport_closed', reason=str(error))
        finally:
            self.transport.close()
            with self.stream_lock:
                streams = list(self.streams.values())
            for stream in streams:
                stream.close()
            for worker in self.workers:
                worker.join(timeout=2)


def parse_listen(value):
    host, separator, port = value.rpartition(':')
    if host != '127.0.0.1' or not separator or not port.isdecimal() or not 0 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError('Test TCP listener must be explicit 127.0.0.1:PORT')
    return host, int(port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument('--stdio', action='store_true', help='Binary stdin/stdout transport (default)')
    transport.add_argument('--listen', type=parse_listen, help='Explicit loopback TCP test transport')
    parser.add_argument('--authorized-keys', type=Path, required=True)
    parser.add_argument('--shell', default='/bin/sh')
    parser.add_argument('--cwd', default='/')
    parser.add_argument('--serial', default='DAOS-BSD-DEV')
    parser.add_argument('--sync-root', type=Path, help='Enable sync v1 beneath this explicit filesystem root')
    parser.add_argument('--ready-file', type=Path, help='Write loopback listener address for test orchestration')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('Run the device daemon on a POSIX host or inside WSL')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', args.serial):
        parser.error('serial must be a plain identifier')
    if not Path(args.shell).is_absolute() or not os.access(args.shell, os.X_OK):
        parser.error('--shell must be an absolute executable path')
    if not Path(args.cwd).is_dir():
        parser.error('--cwd must be an existing directory')
    if args.sync_root is not None and not args.sync_root.is_dir():
        parser.error('--sync-root must be an existing directory')
    keys = load_authorized_keys(args.authorized_keys)
    options = dict(shell=args.shell, cwd=args.cwd, serial=args.serial, sync_root=args.sync_root)
    if args.listen is None:
        connection = Connection(Transport(0, 1), keys, **options)
        def close_stdio(_signal, _frame):
            connection.transport.close()
        signal.signal(signal.SIGTERM, close_stdio)
        signal.signal(signal.SIGINT, close_stdio)
        connection.run()
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(args.listen)
        server.listen(8)
        server.settimeout(.25)
        ready = {'transport': 'loopback-tcp-test', 'host': '127.0.0.1', 'port': server.getsockname()[1],
                 'pid': os.getpid(), 'usb_hardware_validated': False,
                 'authorized_key_fingerprints': [key.fingerprint for key in keys]}
        print(json.dumps({'event': 'listening', **ready}), file=sys.stderr, flush=True)
        if args.ready_file is not None:
            with args.ready_file.open('x', encoding='utf-8') as output:
                json.dump(ready, output)
        active = []
        stopping = threading.Event()
        def stop(_signal, _frame):
            stopping.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while not stopping.is_set():
                try:
                    peer, _ = server.accept()
                except socket.timeout:
                    continue
                if len([thread for _, thread in active if thread.is_alive()]) >= 8:
                    peer.close()
                    continue
                connection = Connection(Transport(peer.fileno(), peer.fileno(), peer), keys, **options)
                worker = threading.Thread(target=connection.run, daemon=True)
                active = [(item, thread) for item, thread in active if thread.is_alive()]
                active.append((connection, worker))
                worker.start()
        finally:
            for connection, worker in active:
                connection.transport.close()
            for connection, worker in active:
                worker.join(timeout=3)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('DaOS ADB device daemon: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

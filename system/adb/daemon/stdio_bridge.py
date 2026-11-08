#!/usr/bin/env python3
"""Loopback-only test adapter for the daemon's actual binary stdio transport.

This is a host integration fixture, not a USB implementation. The real USB
endpoint helper lives in system/adb/freebsd_usb and preserves USB transfers.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time


def exact(stream, count):
    result = bytearray()
    while len(result) < count:
        block = stream.read(count - len(result))
        if not block:
            raise EOFError
        result.extend(block)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorized-keys', required=True)
    parser.add_argument('--sync-root', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from daemon.auth import load_authorized_keys
    keys = load_authorized_keys(args.authorized_keys)
    stopping = threading.Event()
    active = []

    def stop(_signal, _frame):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def relay(peer):
        command = [sys.executable, str(Path(__file__).with_name('daos_adbd.py')), '--stdio',
                   '--authorized-keys', args.authorized_keys, '--sync-root', args.sync_root, '--cwd', args.cwd]
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
        counts = {'headers': 0, 'payloads': 0}

        def outgoing():
            try:
                while True:
                    header = exact(child.stdout, 24)
                    command, _, _, length, _, magic = struct.unpack('<6I', header)
                    if magic != command ^ 0xffffffff or length > 65536:
                        raise ValueError('invalid stdout ADB framing')
                    peer.sendall(header)
                    counts['headers'] += 1
                    if length:
                        peer.sendall(exact(child.stdout, length))
                        counts['payloads'] += 1
            except (OSError, EOFError, ValueError):
                with contextlib.suppress(OSError):
                    peer.shutdown(socket.SHUT_RDWR)

        worker = threading.Thread(target=outgoing, daemon=True)
        worker.start()
        try:
            while not stopping.is_set():
                readable, _, _ = select.select([peer], [], [], .1)
                if not readable:
                    continue
                block = peer.recv(8192)
                if not block:
                    break
                # Deliberately split headers and shell frames at awkward byte
                # boundaries. Pipes provide no USB transfer boundaries.
                for offset in range(0, len(block), 7):
                    view = memoryview(block[offset:offset + 7])
                    while view:
                        size = os.write(child.stdin.fileno(), view)
                        view = view[size:]
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=3)
            peer.close()
            child.stdout.close()
            worker.join(timeout=2)
            print(json.dumps({'event': 'stdio_adapter_connection_finished', 'child_pid': child.pid,
                              'exit_code': child.returncode, **counts, 'usb_hardware_validated': False}),
                  file=sys.stderr, flush=True)

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(8)
        listener.settimeout(.2)
        ready = {'transport': 'stdio-via-loopback-test-adapter', 'host': '127.0.0.1',
                 'port': listener.getsockname()[1], 'pid': os.getpid(), 'usb_hardware_validated': False,
                 'authorized_key_fingerprints': [key.fingerprint for key in keys]}
        with args.ready_file.open('x') as stream:
            json.dump(ready, stream)
        try:
            while not stopping.is_set():
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                active = [worker for worker in active if worker.is_alive()]
                if len(active) >= 8:
                    peer.close()
                    continue
                worker = threading.Thread(target=relay, args=(peer,), daemon=True)
                active.append(worker)
                worker.start()
        finally:
            stopping.set()
            for worker in active:
                worker.join(timeout=7)


if __name__ == '__main__':
    main()

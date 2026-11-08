"""ADB packet framing over POSIX descriptors (stdio or a loopback socket)."""
from __future__ import annotations

import os
import select
import struct
import threading
import time


VERSION = 0x01000001
MAX_PAYLOAD = 65536
COMMANDS = {word: int.from_bytes(word.encode('ascii'), 'little')
            for word in ('CNXN', 'AUTH', 'OPEN', 'OKAY', 'WRTE', 'CLSE')}
NAMES = {value: name for name, value in COMMANDS.items()}


class ProtocolError(ValueError):
    pass


class Disconnected(EOFError):
    pass


class Transport:
    def __init__(self, read_fd, write_fd, connection=None):
        self.read_fd, self.write_fd, self.connection = read_fd, write_fd, connection
        self.closed = threading.Event()
        self.write_lock = threading.Lock()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)

    def read_exact(self, count):
        data = bytearray()
        while len(data) < count:
            if self.closed.is_set():
                raise Disconnected('transport closed')
            readable, _, _ = select.select([self.read_fd], [], [], .25)
            if not readable:
                continue
            try:
                block = os.read(self.read_fd, count - len(data))
            except BlockingIOError:
                continue
            if not block:
                raise Disconnected('transport EOF')
            data.extend(block)
        return bytes(data)

    def _write_all(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 30
        while view:
            if self.closed.is_set():
                raise Disconnected('transport closed')
            _, writable, _ = select.select([], [self.write_fd], [], .25)
            if time.monotonic() > deadline:
                raise Disconnected('transport write deadline expired')
            if not writable:
                continue
            try:
                size = os.write(self.write_fd, view)
            except BlockingIOError:
                continue
            if size <= 0:
                raise Disconnected('transport write failed')
            view = view[size:]

    def send(self, name, arg0=0, arg1=0, payload=b''):
        command = COMMANDS[name]
        if len(payload) > MAX_PAYLOAD:
            raise ProtocolError('outbound packet exceeds device maxdata')
        # The v1 checksum is additive, despite historical protocol.md calling
        # it CRC32. Modern peers ignore it; calculating it supports both.
        header = struct.pack('<6I', command, arg0, arg1, len(payload), sum(payload) & 0xffffffff,
                             command ^ 0xffffffff)
        with self.write_lock:
            self._write_all(header)
            if payload:
                self._write_all(payload)

    def receive(self, version=0):
        header = self.read_exact(24)
        command, arg0, arg1, length, checksum, magic = struct.unpack('<6I', header)
        if command not in NAMES or magic != command ^ 0xffffffff or length > MAX_PAYLOAD:
            raise ProtocolError('invalid ADB header, command or length')
        payload = self.read_exact(length) if length else b''
        modern = version >= VERSION or (NAMES[command] == 'CNXN' and arg0 >= VERSION)
        if not modern and checksum != sum(payload) & 0xffffffff:
            raise ProtocolError('invalid legacy ADB payload checksum')
        return NAMES[command], arg0, arg1, payload

    def close(self):
        self.closed.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(2)
            except OSError:
                pass
            self.connection.close()

"""Uncompressed ADB sync v1 (STAT/LIST/SEND/RECV) under an explicit root."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import struct
import uuid

from .protocol import Disconnected, ProtocolError


class SyncService:
    def __init__(self, stream, root, max_bytes=256 * 1024 * 1024):
        self.stream, self.root = stream, Path(root).resolve(strict=True)
        self.maximum = max_bytes
        self.buffer = bytearray()

    def exact(self, count):
        while len(self.buffer) < count:
            data = self.stream.receive(timeout=.25)
            if data is not None:
                self.buffer.extend(data)
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def send(self, name, value=0, payload=b''):
        self.stream.write(name.encode('ascii') + struct.pack('<I', value) + payload)

    def path(self, raw):
        name = raw.decode('utf-8')
        if not name or '\x00' in name or '..' in Path(name).parts:
            raise ValueError('invalid sync pathname')
        result = self.root / name.lstrip('/')
        resolved = result.resolve(strict=False)
        if self.root != resolved and self.root not in resolved.parents:
            raise ValueError('sync pathname leaves configured root')
        if result.is_symlink():
            raise ValueError('sync refuses symbolic-link targets')
        return resolved

    def run(self):
        try:
            while not self.stream.closed.is_set():
                name, size = struct.unpack('<4sI', self.exact(8))
                if size > 4096:
                    raise ProtocolError('sync pathname/command is too large')
                if name == b'QUIT' and size == 0:
                    self.stream.finish()
                    return
                argument = self.exact(size)
                if name == b'STAT':
                    target = self.path(argument)
                    try:
                        info = target.stat()
                        values = info.st_mode, min(info.st_size, 0xffffffff), int(info.st_mtime) & 0xffffffff
                    except FileNotFoundError:
                        values = 0, 0, 0
                    self.stream.write(struct.pack('<4sIII', b'STAT', *values))
                elif name == b'LIST':
                    target = self.path(argument)
                    for child in sorted(target.iterdir()):
                        if child.is_symlink():
                            continue
                        info, encoded = child.stat(), child.name.encode('utf-8')
                        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                            continue
                        self.stream.write(struct.pack('<4sIIII', b'DENT', info.st_mode,
                                                      min(info.st_size, 0xffffffff), int(info.st_mtime) & 0xffffffff,
                                                      len(encoded)) + encoded)
                    self.stream.write(struct.pack('<4sIIII', b'DONE', 0, 0, 0, 0))
                elif name == b'RECV':
                    target = self.path(argument)
                    if not target.is_file() or target.stat().st_size > self.maximum:
                        raise ValueError('sync receives only bounded regular files')
                    with target.open('rb') as source:
                        for block in iter(lambda: source.read(65536), b''):
                            self.send('DATA', len(block), block)
                    self.send('DONE')
                elif name == b'SEND':
                    raw, delimiter, mode = argument.rpartition(b',')
                    if not delimiter:
                        raise ValueError('sync SEND lacks a file mode')
                    target = self.path(raw)
                    file_mode = int(mode) & 0o777
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name('.' + target.name + '.daos-' + uuid.uuid4().hex)
                    total = 0
                    try:
                        with temporary.open('xb') as output:
                            while True:
                                tag, length = struct.unpack('<4sI', self.exact(8))
                                if tag == b'DONE':
                                    timestamp = length
                                    break
                                if tag != b'DATA' or length > 65536 or total + length > self.maximum:
                                    raise ProtocolError('invalid or oversized sync DATA')
                                output.write(self.exact(length))
                                total += length
                            output.flush()
                            os.fsync(output.fileno())
                        os.chmod(temporary, file_mode)
                        os.utime(temporary, (timestamp, timestamp))
                        if target.is_symlink() or (target.exists() and not target.is_file()):
                            raise ValueError('sync destination changed type')
                        os.replace(temporary, target)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                    self.send('OKAY')
                else:
                    raise ValueError('unsupported sync command')
        except (ValueError, OSError) as error:
            if not self.stream.closed.is_set():
                data = str(error).encode('utf-8', errors='replace')[:1024]
                self.send('FAIL', len(data), data)
                self.stream.finish()

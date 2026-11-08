"""POSIX shell, shell_v2 framing, PTY resize and connection cleanup."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import tty

from .protocol import ProtocolError, Disconnected


def parse_service(destination):
    prefix, separator, command = destination.partition(':')
    if not separator:
        raise ValueError('shell destination requires a colon')
    if prefix == 'exec':
        return {'v2': False, 'pty': True, 'raw_pty': True, 'term': 'dumb', 'command': command}
    parts = prefix.split(',')
    if parts[0] != 'shell':
        raise ValueError('unsupported service')
    options = parts[1:]
    term = 'xterm-256color'
    for value in options:
        if value.startswith('TERM='):
            term = value[5:]
            if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,128}', term):
                raise ValueError('invalid terminal type')
        elif value not in ('v2', 'raw', 'pty'):
            raise ValueError('unsupported shell option')
    if 'raw' in options and 'pty' in options:
        raise ValueError('conflicting shell terminal options')
    v2 = 'v2' in options
    use_pty = 'pty' in options or (not v2) or (not command and 'raw' not in options)
    return {'v2': v2, 'pty': use_pty, 'raw_pty': not v2 and bool(command),
            'term': term, 'command': command}


class ShellService:
    def __init__(self, stream, options, shell, cwd):
        self.stream, self.options, self.shell, self.cwd = stream, options, shell, cwd
        self.process = None
        self.master = None
        self.child_stdin = None
        self.output_fds = {}
        self.pending = bytearray()
        self.incoming = bytearray()
        self.stdin_eof = False
        self.cleaned = False

    def spawn(self):
        environment = dict(os.environ)
        environment.update(TERM=self.options['term'], SHELL=self.shell,
                           PATH='/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:/usr/pkg/bin:/usr/pkg/sbin')
        if self.options['pty']:
            master, slave = pty.openpty()
            self.master = master
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
            if self.options['raw_pty']:
                tty.setraw(slave)
            helper = Path(__file__).with_name('pty_child.py')
            try:
                self.process = subprocess.Popen([sys.executable, str(helper), self.shell, self.options['command']],
                                                stdin=slave, stdout=slave, stderr=slave,
                                                cwd=self.cwd, env=environment, start_new_session=True,
                                                close_fds=True)
            finally:
                os.close(slave)
            self.child_stdin = master
            self.output_fds[master] = 1
            os.set_blocking(master, False)
        else:
            self.process = subprocess.Popen([self.shell, '-c', self.options['command']] if self.options['command'] else [self.shell],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            cwd=self.cwd, env=environment, start_new_session=True,
                                            close_fds=True)
            self.child_stdin = self.process.stdin.fileno()
            self.output_fds = {self.process.stdout.fileno(): 1, self.process.stderr.fileno(): 2}
            for descriptor in (self.child_stdin, *self.output_fds):
                os.set_blocking(descriptor, False)

    def output(self, kind, payload):
        if self.options['v2']:
            payload = struct.pack('<BI', kind, len(payload)) + payload
        self.stream.write(payload)

    def accept(self, payload):
        if not self.options['v2']:
            if not self.stdin_eof:
                self.pending.extend(payload)
            return
        self.incoming.extend(payload)
        while len(self.incoming) >= 5:
            kind, length = struct.unpack_from('<BI', self.incoming)
            if length > 1024 * 1024:
                raise ProtocolError('shell_v2 frame is too large')
            if len(self.incoming) < 5 + length:
                break
            data = bytes(self.incoming[5:5 + length])
            del self.incoming[:5 + length]
            if kind == 0:
                if not self.stdin_eof:
                    self.pending.extend(data)
            elif kind == 4 and length == 0:
                self.stdin_eof = True
            elif kind == 5:
                match = re.fullmatch(rb'(\d{1,5})x(\d{1,5}),(\d{1,5})x(\d{1,5})\x00?', data)
                if not match or any(int(value) > 65535 for value in match.groups()):
                    raise ProtocolError('invalid PTY window size')
                if self.master is not None:
                    fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', *map(int, match.groups())))
            else:
                raise ProtocolError('unsupported inbound shell_v2 frame')

    def close_stdin(self):
        if self.options['pty']:
            # A PTY cannot half-close: retaining it matches official adbd.
            return
        if self.child_stdin is not None:
            self.process.stdin.close()
            self.child_stdin = None

    def run(self):
        self.spawn()
        self.stream.connection.log('shell_started', stream=self.stream.local_id,
                                   pid=self.process.pid, pty=self.options['pty'], v2=self.options['v2'])
        try:
            while not self.stream.closed.is_set():
                if len(self.pending) < 256 * 1024:
                    block = self.stream.receive_nowait()
                    if block is not None:
                        self.accept(block)
                if self.stdin_eof and not self.pending:
                    self.close_stdin()
                if not self.output_fds and self.process.poll() is not None:
                    break
                writers = [self.child_stdin] if self.pending and self.child_stdin is not None else []
                readable, writable, _ = select.select(list(self.output_fds), writers, [], .025)
                if writable:
                    try:
                        size = os.write(self.child_stdin, self.pending)
                        del self.pending[:size]
                    except (BrokenPipeError, OSError) as error:
                        if isinstance(error, OSError) and error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            pass
                        else:
                            self.pending.clear()
                            self.close_stdin()
                for descriptor in readable:
                    try:
                        data = os.read(descriptor, 16384)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        if self.options['pty'] and error.errno == errno.EIO:
                            data = b''
                        else:
                            raise
                    if data:
                        self.output(self.output_fds[descriptor], data)
                    else:
                        self.output_fds.pop(descriptor, None)
            if not self.stream.closed.is_set():
                code = self.process.wait(timeout=5)
                code = code if code >= 0 else 128 - code
                if self.options['v2']:
                    self.output(3, bytes([code & 255]))
                self.stream.finish()
        finally:
            self.cleanup()

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        if self.process is not None:
            # The child owns a new session/process group; never signal a host
            # shell, adb server, or any unrelated process group.
            for sig, delay in ((signal.SIGHUP, .3), (signal.SIGTERM, .3), (signal.SIGKILL, .2)):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, sig)
                try:
                    self.process.wait(timeout=delay)
                except subprocess.TimeoutExpired:
                    pass
                # A shell can exit before another member of its session's
                # process group. Continue cleanup until that group is gone.
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    break
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()
            self.stream.connection.log('shell_reaped', stream=self.stream.local_id,
                                       pid=self.process.pid, returncode=self.process.poll())
        if self.master is not None:
            with contextlib.suppress(OSError):
                os.close(self.master)
            self.master = None

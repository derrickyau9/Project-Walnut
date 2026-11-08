"""Exec a shell with its inherited PTY as controlling terminal.

This small exec helper avoids preexec_fn in a multithreaded daemon.
"""
import fcntl
import os
import signal
import sys
import termios


def main():
    if len(sys.argv) != 3:
        raise SystemExit(2)
    shell, command = sys.argv[1:]
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    os.execvpe(shell, [shell, '-c', command] if command else [shell, '-i'], os.environ)


if __name__ == '__main__':
    main()

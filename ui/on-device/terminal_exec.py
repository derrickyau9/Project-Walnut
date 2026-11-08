"""Fresh-process PTY session setup; avoid preexec hooks in the Qt process."""
import fcntl
import os
import sys
import termios

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
args=['/bin/sh','-i']
if sys.platform.startswith('freebsd'):args+=['-o','emacs']
os.execve('/bin/sh',args,os.environ)

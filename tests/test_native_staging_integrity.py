"""Real Linux process probes for native staging identity/content integrity.

A test-only LD_PRELOAD fixture pauses after libc closes this capture's staging
file, before write_dng returns. No production fault API or camera code is changed.
Every gate, child process and compiler invocation has a finite time budget.
These tests concern synthetic DNG storage, never sensor or calibration behavior.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from camera.camerad.native_client import NativeClient
from camera.camerad.service import DEFAULTS
from camera.dng.inspect import inspect_dng


ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'complete', 'failed', 'cancelled', 'unknown'}
_INTERPOSER = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static _Atomic int gated = 0;

int fclose(FILE *stream) {
    int (*original)(FILE *) = (int (*)(FILE *))dlsym(RTLD_NEXT, "fclose");
    if (!original) _exit(124);
    const char *target = getenv("HF_STAGING_GATE_TARGET");
    const char *ready = getenv("HF_STAGING_GATE_READY");
    const char *release = getenv("HF_STAGING_GATE_RELEASE");
    int matches = 0;
    if (target && ready && release) {
        char descriptor[64], resolved[PATH_MAX + 1];
        int count = snprintf(descriptor, sizeof descriptor, "/proc/self/fd/%d", fileno(stream));
        if (count > 0 && count < (int)sizeof descriptor) {
            ssize_t length = readlink(descriptor, resolved, PATH_MAX);
            if (length > 0) {
                resolved[length] = '\0';
                matches = strcmp(resolved, target) == 0;
            }
        }
    }
    int result = original(stream);
    if (matches && result == 0 && atomic_exchange(&gated, 1) == 0) {
        int signal = open(ready, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
        if (signal < 0 || write(signal, "closed\n", 7) != 7) _exit(123);
        close(signal);
        struct timespec started, now, delay = {0, 10000000};
        if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) _exit(122);
        for (;;) {
            int gate = open(release, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
            if (gate >= 0) {
                struct stat info;
                char value = 0;
                int valid = fstat(gate, &info) == 0 && S_ISREG(info.st_mode) &&
                    read(gate, &value, 1) == 1 && value == '1';
                close(gate);
                if (valid) break;
            }
            if (clock_gettime(CLOCK_MONOTONIC, &now) != 0 || now.tv_sec - started.tv_sec >= 15)
                _exit(121);
            nanosleep(&delay, NULL);
        }
    }
    return result;
}
'''


@unittest.skipUnless(sys.platform == 'linux' and Path('/proc/self/fd').is_dir()
                     and shutil.which('cc'), 'requires Linux /proc, a C compiler and LD_PRELOAD')
class NativeStagingIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
        if configured:
            cls.executable = Path(configured).resolve()
            if not cls.executable.is_file():
                raise RuntimeError('HF_NATIVE_DAEMON is not a file')
        else:
            cls.executable = next((path for path in (
                ROOT / 'build/native/hf-camerad', ROOT / 'build/linux-native/hf-camerad')
                if path.is_file()), None)
            if cls.executable is None:
                raise unittest.SkipTest('set HF_NATIVE_DAEMON to a dynamically linked Linux native daemon')
        # A static target intentionally cannot be interposed. The normal host
        # daemon is dynamic; ARM64 static packages have separate target tests.
        with cls.executable.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:4] != b'\x7fELF' or header[4:6] not in (b'\x01\x01', b'\x02\x01'):
                raise unittest.SkipTest('staging interposition requires a little-endian Linux ELF executable')
            if header[4] == 2:
                offset = struct.unpack_from('<Q', header, 32)[0]
                entry_size, count = struct.unpack_from('<HH', header, 54)
            else:
                offset = struct.unpack_from('<I', header, 28)[0]
                entry_size, count = struct.unpack_from('<HH', header, 42)
            if entry_size < 4 or count > 1024:
                raise RuntimeError('native daemon has an invalid ELF program table')
            dynamic = False
            for index in range(count):
                stream.seek(offset + index * entry_size)
                raw = stream.read(4)
                if len(raw) != 4:
                    raise RuntimeError('native daemon has a truncated ELF program table')
                dynamic = dynamic or struct.unpack('<I', raw)[0] == 3
            if not dynamic:
                raise unittest.SkipTest('LD_PRELOAD requires a dynamic host daemon; static ARM64 targets cannot be interposed')
        cls.fixture = tempfile.TemporaryDirectory(prefix='hf-native-staging-fixture-')
        cls.addClassCleanup(cls.fixture.cleanup)
        directory = Path(cls.fixture.name)
        source = directory / 'gate.c'
        cls.interposer = directory / 'gate.so'
        source.write_text(_INTERPOSER, encoding='ascii')
        compiled = subprocess.run([shutil.which('cc'), '-std=c11', '-shared', '-fPIC', '-O2',
                                   '-Wall', '-Wextra', '-Werror', str(source), '-o',
                                   str(cls.interposer), '-ldl'], capture_output=True, timeout=30)
        if compiled.returncode:
            raise RuntimeError('staging interposer compilation failed: ' + compiled.stderr.decode('utf-8', 'replace'))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hf-native-staging-case-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.request_id = str(uuid.uuid4())
        self.data = self.root / 'camera-data'
        self.staging = self.data / 'photos' / (self.request_id + '.dng.partial')
        self.final = self.data / 'photos' / (self.request_id + '.dng')
        self.original = self.root / 'original-written.dng'
        self.ready = self.root / 'close.ready'
        self.release = self.root / 'close.release'
        self.client = None
        self.addCleanup(self._cleanup_process)
        self.client = NativeClient(self.data, command=[str(self.executable)], timeout_seconds=3,
            env={'LD_PRELOAD': str(self.interposer), 'HF_STAGING_GATE_TARGET': str(self.staging),
                 'HF_STAGING_GATE_READY': str(self.ready), 'HF_STAGING_GATE_RELEASE': str(self.release)})
        self.lease = self.client.acquire('staging-integrity-test')

    def _release(self):
        if not self.release.exists():
            with self.release.open('xb') as stream:
                stream.write(b'1')

    def _cleanup_process(self):
        self._release()
        if self.client is not None:
            self.client.abort()
            self.assertIsNotNone(self.client.process.poll(), 'test left its native child running')

    def _capture_closed_staging(self):
        request = dict(DEFAULTS, request_id=self.request_id, token=self.lease['token'],
                       control_epoch=self.lease['epoch'])
        self.client.capture(request)
        deadline = time.monotonic() + 8
        while not self.ready.exists() and time.monotonic() < deadline:
            if self.client.process.poll() is not None:
                self.fail('native daemon exited before staging-close gate: ' + self.client.stderr_tail)
            time.sleep(.01)
        self.assertTrue(self.ready.exists(), 'native staging fclose was not interposed within eight seconds')
        self.assertEqual(self.ready.read_bytes(), b'closed\n')
        self.assertTrue(self.staging.is_file())
        self.assertFalse(self.final.exists())
        self.assertEqual(inspect_dng(self.staging)['request_id'], self.request_id)
        return self.staging.read_bytes()

    def _terminal(self):
        self._release()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            record = self.client.query_capture(self.request_id)
            if record['status'] in TERMINAL and self.client.get_state()['camera_state'] != 'capturing':
                return record
            time.sleep(.01)
        self.fail('native staging integrity outcome did not become terminal within eight seconds')

    def _reject_tampering(self):
        record = self._terminal()
        self.assertIn(record['status'], {'failed', 'unknown'}, 'tampered staging was reported complete')
        self.assertIsNone(record['frame_id'])
        self.assertIsNone(record['file_path'])
        self.assertFalse(os.path.lexists(self.final), 'foreign or modified staging was published')
        return record

    def test_untampered_closed_staging_completes(self):
        expected = self._capture_closed_staging()
        record = self._terminal()
        self.assertEqual(record['status'], 'complete')
        self.assertEqual(self.final.read_bytes(), expected)
        self.assertFalse(os.path.lexists(self.staging))

    def test_same_size_replacement_is_preserved_and_not_published(self):
        expected = self._capture_closed_staging()
        self.staging.rename(self.original)
        foreign = b'foreign-invalid-dng'.ljust(len(expected), b'!')
        self.staging.write_bytes(foreign)
        self._reject_tampering()
        self.assertEqual(self.staging.read_bytes(), foreign, 'failure cleanup removed or changed a foreign file')
        self.assertEqual(self.original.read_bytes(), expected)

    def test_fifo_replacement_is_bounded_preserved_and_not_published(self):
        expected = self._capture_closed_staging()
        self.staging.rename(self.original)
        os.mkfifo(self.staging, 0o600)
        identity = self.staging.lstat()
        self._reject_tampering()
        current = self.staging.lstat()
        self.assertTrue(stat.S_ISFIFO(current.st_mode), 'failure cleanup removed a foreign FIFO')
        self.assertEqual((current.st_dev, current.st_ino), (identity.st_dev, identity.st_ino))
        self.assertEqual(self.original.read_bytes(), expected)

    def test_symlink_replacement_preserves_target_and_is_not_published(self):
        expected = self._capture_closed_staging()
        self.staging.rename(self.original)
        target = self.root / 'foreign-target.dng'
        foreign = b'foreign-symlink-target'.ljust(len(expected), b'?')
        target.write_bytes(foreign)
        self.staging.symlink_to(target)
        self._reject_tampering()
        self.assertTrue(self.staging.is_symlink(), 'failure cleanup removed a foreign symlink')
        self.assertEqual(self.staging.resolve(), target)
        self.assertEqual(target.read_bytes(), foreign)
        self.assertEqual(self.original.read_bytes(), expected)

    def test_in_place_modified_contents_are_not_published_or_removed(self):
        expected = self._capture_closed_staging()
        before = self.staging.stat()
        modified = b'BAD!' + expected[4:]
        with self.staging.open('r+b') as stream:
            stream.write(modified[:4])
            stream.flush()
            os.fsync(stream.fileno())
        self.assertEqual(self.staging.stat().st_ino, before.st_ino)
        self._reject_tampering()
        self.assertEqual(self.staging.read_bytes(), modified, 'failure cleanup removed externally modified evidence')

    def test_concurrent_final_is_preserved_without_overwrite(self):
        self._capture_closed_staging()
        foreign = b'foreign final file must survive capture publication'
        self.final.write_bytes(foreign)
        record = self._terminal()
        self.assertIn(record['status'], {'failed', 'unknown'})
        self.assertIsNone(record['file_path'])
        self.assertEqual(self.final.read_bytes(), foreign)


if __name__ == '__main__':
    unittest.main()

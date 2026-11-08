"""Host-only tests of boot sequencing and native package-finalization failures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('daos_ui_finalize', HERE / 'files/usr/libexec/daos/ui-finalize.py')
finalizer = importlib.util.module_from_spec(spec); spec.loader.exec_module(finalizer)
spec = importlib.util.spec_from_file_location('daos_ui_installer', HERE / 'install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)

class SourceXorgLayout(unittest.TestCase):
    def test_netbsd_drivers_directory_is_accepted_and_missing_driver_rejected(self):
        with tempfile.TemporaryDirectory(prefix='daos-xorg-layout-test-') as tmp:
            base = Path(tmp)
            paths = ['usr/X11R7/bin/Xorg', 'usr/X11R7/bin/xauth',
                     'usr/X11R7/lib/modules/drivers/wsfb_drv.so',
                     'usr/X11R7/lib/modules/drivers/kbd_drv.so',
                     'usr/X11R7/lib/modules/drivers/mouse_drv.so',
                     'usr/sbin/pkg_add', 'usr/sbin/pkg_info',
                     'usr/libexec/ld.elf_so', 'libexec/ld.elf_so']
            for name in paths:
                path = base / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            lock = {'source_world_requires': []}
            self.assertEqual(installer.source_world_missing(base, lock), [])
            (base / 'usr/X11R7/lib/modules/drivers/kbd_drv.so').unlink()
            self.assertEqual(installer.source_world_missing(base, lock),
                             ['/usr/X11R7/lib/modules/drivers/kbd_drv.so'])

class NetBsdArchitecture(unittest.TestCase):
    def modules(self):
        for filename, uid in (('ui-finalize.py', 0), ('camera-session.py', 0), ('camera-app.py', 1000)):
            spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), HERE / 'files/usr/libexec/daos' / filename)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            yield filename, uid, module

    def test_evbarm_kernel_with_aarch64_machine_arch_is_accepted(self):
        for filename, uid, module in self.modules():
            with self.subTest(entry=filename), mock.patch.object(module.platform, 'system', return_value='NetBSD'), \
                 mock.patch.object(module.platform, 'machine', return_value='evbarm'), \
                 mock.patch.object(module.os, 'geteuid', return_value=uid), \
                 mock.patch.object(module.subprocess, 'check_output', return_value='aarch64\n') as sysctl:
                self.assertEqual(module.require_native(), 'aarch64')
                sysctl.assert_called_once_with(['/sbin/sysctl', '-n', 'hw.machine_arch'], text=True, timeout=5)

    def test_evbarm_kernel_with_32_bit_machine_arch_is_rejected(self):
        for filename, uid, module in self.modules():
            with self.subTest(entry=filename), mock.patch.object(module.platform, 'system', return_value='NetBSD'), \
                 mock.patch.object(module.platform, 'machine', return_value='evbarm'), \
                 mock.patch.object(module.os, 'geteuid', return_value=uid), \
                 mock.patch.object(module.subprocess, 'check_output', return_value='earmv7hf\n'):
                with self.assertRaisesRegex(RuntimeError, 'aarch64'): module.require_native()

class Finalization(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='daos-ui-finalizer-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache, self.state = self.root / 'cache', self.root / 'state'
        self.cache.mkdir()
        self.file = self.root / 'usr/pkg/lib/example.so'
        self.file.parent.mkdir(parents=True); self.file.write_bytes(b'factory bytes')
        self.rows = []
        for name in ('dependency-1.0', 'application-2.0'):
            archive = self.cache / (name + '.tgz'); archive.write_bytes(name.encode())
            self.rows.append({'name': name, 'file': archive.name, 'bytes': archive.stat().st_size,
                              'sha512': hashlib.sha512(archive.read_bytes()).hexdigest()})
        self.lock = self.cache / 'runtime.lock.json'
        self.lock.write_text(json.dumps({'format': 'DaOS-NetBSD-binary-runtime-lock-v1',
                   'target': {'system': 'NetBSD', 'architecture': 'aarch64'},
                   'roots': ['application-2.0'], 'packages': self.rows}))
        self.baseline = self.root / 'payload.json'
        self.baseline.write_text(json.dumps({'format': 'DaOS-NetBSD-ui-payload-baseline-v1',
                    'runtime_lock_sha256': finalizer.sha(self.lock, 'sha256'),
                    'files': [{'path': 'usr/pkg/lib/example.so', 'type': 'file',
                               'sha256': finalizer.sha(self.file, 'sha256')}]}))
        self.calls = []

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    def finalize(self, runner=None):
        return finalizer.finalize(self.cache, self.state, self.root / 'pkgdb', self.baseline,
                                  run=runner or self.runner, payload_root=self.root)

    def test_installs_all_locked_packages_in_dependency_order(self):
        result = self.finalize()
        command, settings = self.calls[0]
        self.assertEqual(command[:4], ['/usr/sbin/pkg_add', '-U', '-K', str(self.root / 'pkgdb')])
        self.assertEqual(command[4:], [str(self.cache / row['file']) for row in self.rows])
        self.assertEqual(settings['env']['PKG_PATH'], str(self.cache))
        self.assertTrue(result['factory_payload_unchanged'])
        self.assertEqual(result['factory_payload_files_verified'], 1)

    def test_corrupt_archive_never_calls_pkg_add_or_commits_receipt(self):
        (self.cache / self.rows[0]['file']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'missing or changed'): self.finalize()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state / 'finalized.json').exists())

    def test_failed_install_can_retry_without_false_completion(self):
        def fail(command, **kwargs): raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError): self.finalize(fail)
        self.assertFalse((self.state / 'finalized.json').exists())
        self.assertTrue(self.finalize()['native_package_scripts_completed'])
        self.assertIn('-U', self.calls[0][0])

    def test_native_mutation_does_not_rewrite_factory_baseline(self):
        original = self.baseline.read_bytes()
        def mutate(command, **kwargs):
            self.runner(command, **kwargs)
            if command[0].endswith('pkg_add'): self.file.write_bytes(b'changed by post-install')
            return subprocess.CompletedProcess(command, 0)
        with self.assertRaisesRegex(RuntimeError, 'changed factory payload'): self.finalize(mutate)
        self.assertEqual(self.baseline.read_bytes(), original)
        self.assertFalse((self.state / 'finalized.json').exists())
        self.assertEqual(json.loads((self.state / 'payload-changes.json').read_text())['changed'],
                         ['usr/pkg/lib/example.so'])

    def test_resume_checks_database_and_never_reinstalls(self):
        self.finalize(); self.calls.clear()
        result = self.finalize()
        self.assertTrue(result['resumed'])
        self.assertTrue(all(command[0].endswith('pkg_info') for command, _ in self.calls))
        def absent(command, **kwargs): raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError): self.finalize(absent)

class RcSequencing(unittest.TestCase):
    def test_supervisor_start_returns_before_background_work_finishes(self):
        # Run the real rc function body under /bin/sh. Only absolute filesystem
        # locations and the Python executable are mapped into a private test root.
        with tempfile.TemporaryDirectory(prefix='daos-ui-rc-test-') as tmp:
            root = Path(tmp)
            for folder in ('var/run', 'var/log'): (root / folder).mkdir(parents=True)
            fake = root / 'python'
            fake.write_text('#!/bin/sh\necho $$ > ' + str(root / 'started') + '\nsleep 5\n')
            fake.chmod(0o755)
            text = (HERE / 'files/etc/rc.d/daos_ui').read_text()
            text = text.replace('/usr/pkg/bin/python3.12', str(fake)).replace('/var/', str(root / 'var') + '/')
            script = root / 'harness.sh'
            script.write_text('_rc_subr_loaded=:\nload_rc_config() { :; }\nrun_rc_command() { "$start_cmd"; }\n' + text)
            start = time.monotonic()
            process = subprocess.Popen(['/bin/sh', str(script)], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertLess(time.monotonic() - start, 2)
                pid = int((root / 'var/run/daos-ui.pid').read_text())
                os.kill(pid, 0)
            finally:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass

if __name__ == '__main__': unittest.main()

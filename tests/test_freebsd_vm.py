"""Host guard and owned-process tests; actual FreeBSD proof is separate evidence."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools import freebsd_vm as vm


class FreeBsdVmGuardTests(unittest.TestCase):
    def test_json_rejects_duplicates_nonfinite_and_overflow(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                vm._json(raw)
        self.assertEqual(vm._json('{"x":1}'), {'x': 1})

    def test_finite_bounds_reject_booleans_and_nan(self):
        for value in (True, False, float('nan'), float('inf'), -.1, 31, '5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                vm._number(value, 0, 30, 'test')
        self.assertEqual(vm._number(1.5, 0, 30, 'test'), 1.5)

    def test_seed_config_disables_passwords_before_daemon_and_limits_users(self):
        key = 'ssh-ed25519 AAAATEST hf-task-bootstrap'
        user, meta = vm.seed_data(key, '28b44e2b-febe-45d9-bb7b-434bda112840')
        text = user.decode('ascii')
        self.assertTrue(text.startswith('#cloud-config\n'))
        for required in ('ssh_pwauth: false', 'package_update: false', 'package_upgrade: false',
                         'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
                         'PermitRootLogin prohibit-password', 'AuthenticationMethods publickey',
                         'AllowUsers root camera', 'pw useradd camera -u 1000', 'pw usermod root -w no'):
            self.assertIn(required, text)
        self.assertLess(text.index('PasswordAuthentication no'), text.index('runcmd:'))
        self.assertNotIn('plain_text_passwd', text)
        self.assertEqual(text.count(key), 2)
        self.assertIn(b'instance-id: 28b44e2b-febe-45d9-bb7b-434bda112840\n', meta)

    def test_seed_inputs_cannot_inject_shell_or_yaml(self):
        for key in ('', 'ssh-ed25519 A\nmalicious', "ssh-ed25519 A'; reboot; '", 'ssh-rsa AAAA'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                vm.seed_data(key, '28b44e2b-febe-45d9-bb7b-434bda112840')
        with self.assertRaises(ValueError):
            vm.seed_data('ssh-ed25519 AAAA', 'not-uuid')

    def test_qemu_has_only_loopback_forward_no_shares_and_explicit_network_mode(self):
        directory = Path('/home/task/vm')
        metadata = {'ssh_port': 22241}
        with mock.patch.object(vm, '_metadata', return_value=(directory, metadata)), \
                mock.patch.object(vm, '_tool', return_value='/usr/bin/qemu-system-x86_64'):
            for restricted in (False, True):
                command = vm.qemu_command(directory, restricted=restricted)
                self.assertIn('q35,accel=kvm', command)
                self.assertEqual(command[command.index('-smp') + 1], '16')
                self.assertEqual(command[command.index('-m') + 1], '16384')
                self.assertEqual(command[command.index('-display') + 1], 'none')
                network = command[command.index('-netdev') + 1]
                self.assertIn('hostfwd=tcp:127.0.0.1:22241-:22', network)
                self.assertIn('restrict=' + ('on' if restricted else 'off'), network)
                self.assertNotIn('0.0.0.0', ' '.join(command))
                for forbidden in ('-virtfs', '-fsdev', '-daemonize', '-vnc', '-spice', '-smb'):
                    self.assertNotIn(forbidden, command)

    def test_qemu_resource_overrides_and_lower_bounds_preserve_vm_metadata_and_network(self):
        directory = Path('/home/task/vm')
        metadata = {'ssh_port': 22241, 'preserved': 'original prepared inputs'}
        original = dict(metadata)
        with mock.patch.object(vm, '_metadata', return_value=(directory, metadata)), \
                mock.patch.object(vm, '_tool', return_value='/usr/bin/qemu-system-x86_64'):
            for memory_mib, cpus in ((12288, 8), (2048, 1), (16384, 16)):
                with self.subTest(memory_mib=memory_mib, cpus=cpus):
                    command = vm.qemu_command(directory, restricted=True, memory_mib=memory_mib, cpus=cpus)
                    self.assertEqual(command[command.index('-m') + 1], str(memory_mib))
                    self.assertEqual(command[command.index('-smp') + 1], str(cpus))
                    network = command[command.index('-netdev') + 1]
                    self.assertIn('restrict=on', network)
                    self.assertIn('hostfwd=tcp:127.0.0.1:22241-:22', network)
                    self.assertEqual(metadata, original)

    def test_invalid_resources_fail_before_metadata_tools_or_process_launch(self):
        invalid = [('memory_mib', value) for value in (True, False, 2047, 16385, 2048.0, '12288', None)]
        invalid += [('cpus', value) for value in (True, False, 0, 17, 1.0, '8', None)]
        with mock.patch.object(vm, '_linux'), mock.patch.object(vm, '_metadata') as metadata, \
                mock.patch.object(vm, '_tool') as tool, mock.patch.object(vm, '_lock') as ownership, \
                mock.patch.object(vm.subprocess, 'Popen') as spawn:
            for method in (vm.qemu_command, vm.serve):
                for key, value in invalid:
                    with self.subTest(method=method.__name__, key=key, value=value), self.assertRaises(ValueError):
                        method('/unused', **{key: value})
            metadata.assert_not_called()
            tool.assert_not_called()
            ownership.assert_not_called()
            spawn.assert_not_called()

    def test_serve_cli_forwards_selected_resources_and_unchanged_defaults(self):
        for options, memory_mib, cpus in (([], 16384, 16),
                                         (['--memory-mib', '12288', '--cpus', '8'], 12288, 8)):
            with self.subTest(options=options), mock.patch.object(vm, 'serve') as serve, \
                    mock.patch.object(vm.signal, 'signal'):
                self.assertEqual(vm.main(['serve', '--work-dir', '/unused', *options]), 0)
                self.assertEqual(serve.call_args.kwargs['memory_mib'], memory_mib)
                self.assertEqual(serve.call_args.kwargs['cpus'], cpus)

    def test_serve_records_actual_override_command_in_launch_and_ready_without_mutating_inputs(self):
        class SimulatedReadyChild:
            pid = 12345
            returncode = 0
            def __init__(self):
                self.polls = 0
            def poll(self):
                self.polls += 1
                return None if self.polls == 1 else 0
        with tempfile.TemporaryDirectory(prefix='hf-freebsd-resources-') as temporary:
            directory = Path(temporary).resolve()
            metadata = {'ssh_port': 22241, 'base_sha256': 'a' * 64, 'seed_sha256': 'a' * 64,
                        'task_key_file': str(directory / 'task-key')}
            original = json.dumps(metadata, sort_keys=True).encode()
            (directory / 'vm.json').write_bytes(original)
            with mock.patch.object(vm, '_linux'), \
                    mock.patch.object(vm, '_metadata', return_value=(directory, metadata)), \
                    mock.patch.object(vm, '_tool', return_value='/usr/bin/qemu-system-x86_64'), \
                    mock.patch.object(vm.os, 'access', return_value=True), \
                    mock.patch.object(vm, '_lock', return_value=nullcontext()), \
                    mock.patch.object(vm, '_pin', return_value=nullcontext()), \
                    mock.patch.object(vm, '_digest', return_value='a' * 64), \
                    mock.patch.object(vm.socket, 'socket'), \
                    mock.patch.object(vm, 'ssh_command', return_value={'returncode': 0,
                        'stdout': 'FreeBSD\namd64\n0\nHF_TASK_SSH_READY\n'}), \
                    mock.patch.object(vm.subprocess, 'Popen', return_value=SimulatedReadyChild()) as spawn, \
                    mock.patch.object(vm, '_stop'), mock.patch.object(vm.time, 'sleep'), \
                    mock.patch('builtins.print'):
                vm.serve(directory, restricted=True, memory_mib=12288, cpus=8)
            boot, = directory.glob('boot-*')
            launch = json.loads((boot / 'launch.json').read_text())
            ready = json.loads((boot / 'ready.json').read_text())
            actual_command = spawn.call_args.args[0]
            self.assertEqual(launch['command'], actual_command)
            self.assertEqual(actual_command[actual_command.index('-m') + 1], '12288')
            self.assertEqual(actual_command[actual_command.index('-smp') + 1], '8')
            for receipt in (launch, ready):
                self.assertEqual(receipt['memory_mib'], 12288)
                self.assertEqual(receipt['cpus'], 8)
            self.assertEqual((directory / 'vm.json').read_bytes(), original)
            self.assertEqual(json.dumps(metadata, sort_keys=True).encode(), original)

    def test_ssh_rejects_empty_oversized_and_null_commands_before_spawn(self):
        with mock.patch.object(vm, '_linux'), mock.patch.object(vm, '_run') as run:
            for command in ('', '\0', 'x' * 65537, None):
                with self.subTest(command=str(command)[:8]), self.assertRaises(ValueError):
                    vm.ssh_command('/unused', command)
            run.assert_not_called()

    def test_guest_transport_paths_cannot_inject_commands_or_escape(self):
        for path in ('relative', '/', '/tmp/a b', '/tmp/../etc/key', '/tmp/./file',
                     '/tmp//file', '/tmp/file;reboot', '/tmp/file\nreboot', '/tmp/$(whoami)'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                vm._guest_path(path)
        self.assertEqual(vm._guest_path('/data/review/first.json'), '/data/review/first.json')

    def test_download_oversize_or_invalid_hash_never_creates_destination(self):
        with tempfile.TemporaryDirectory(prefix='hf-freebsd-download-') as temporary:
            destination = Path(temporary) / 'new'
            with mock.patch.object(vm, '_linux'), mock.patch.object(vm, 'ssh_command') as remote:
                for output in ('4097\n' + 'a' * 64 + '\n', '5\nnot-a-hash\n', '5\n' + 'a' * 64 + '\nextra\n'):
                    remote.return_value = {'returncode': 0, 'stdout': output}
                    with self.subTest(output=output[:10]), self.assertRaises(ValueError):
                        vm.copy_from('/unused', '/tmp/file', destination, max_bytes=4096)
                    self.assertFalse(destination.exists())
            destination.write_bytes(b'preserve')
            with mock.patch.object(vm, '_linux'), mock.patch.object(vm, 'ssh_command') as remote:
                with self.assertRaises(ValueError):
                    vm.copy_from('/unused', '/tmp/file', destination)
                remote.assert_not_called()
            self.assertEqual(destination.read_bytes(), b'preserve')

    def test_process_timeout_is_finite_and_reaps_owned_child(self):
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'time budget'):
            vm._run([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=.15)
        self.assertLess(time.monotonic() - started, 6)

    def test_process_output_bound_and_success(self):
        with self.assertRaisesRegex(RuntimeError, 'output exceeded'):
            vm._run([sys.executable, '-c', 'import sys;sys.stdout.write("x"*65536)'], maximum=1024)
        result = vm._run([sys.executable, '-c', 'print("owned-process")'])
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(result['stdout'].strip(), 'owned-process')

    @unittest.skipUnless(os.name == 'posix', 'POSIX ordinary-file and socket path guards')
    def test_directory_alias_delimiter_and_existing_destination_refused(self):
        with tempfile.TemporaryDirectory(prefix='hf-freebsd-guard-') as temporary:
            root = Path(temporary)
            existing = root / 'existing'; existing.mkdir()
            with self.assertRaises(ValueError):
                vm._directory(existing, new=True)
            alias = root / 'alias'; alias.symlink_to(existing, target_is_directory=True)
            with self.assertRaises(ValueError):
                vm._directory(alias)
            for name in ('bad,option', 'bad\nline'):
                destination = root / name
                with self.assertRaises(ValueError):
                    vm._directory(destination, new=True)
                self.assertFalse(destination.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX nofollow/nonblocking guards')
    def test_input_fifo_symlink_and_parent_alias_refused(self):
        with tempfile.TemporaryDirectory(prefix='hf-freebsd-pin-') as temporary:
            root = Path(temporary)
            regular = root / 'regular'; regular.write_bytes(b'keep')
            alias = root / 'alias'; alias.symlink_to(regular)
            fifo = root / 'fifo'; os.mkfifo(fifo)
            linked_parent = root / 'parent'; linked_parent.symlink_to(root, target_is_directory=True)
            for path in (alias, fifo, linked_parent / 'regular'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    vm._pin(path)
            self.assertEqual(vm._digest(regular), '6ca7ea2feefc88ecb5ed6356ed963f47dc9137f82526fdd25d618ea626d0803f')

    @unittest.skipUnless(sys.platform == 'linux', 'actual Linux flock ownership')
    def test_lifecycle_lock_blocks_other_owner_and_keeps_inode(self):
        with tempfile.TemporaryDirectory(prefix='hf-freebsd-lock-') as temporary:
            directory = Path(temporary)
            with vm._lock(directory):
                identity = (directory / '.vm.lock').stat().st_ino
                with self.assertRaisesRegex(RuntimeError, 'already owned'):
                    with vm._lock(directory, timeout=.05):
                        self.fail('split lifecycle owner')
            self.assertTrue((directory / '.vm.lock').is_file())
            with vm._lock(directory):
                self.assertEqual((directory / '.vm.lock').stat().st_ino, identity)


if __name__ == '__main__':
    unittest.main()

"""ARM task-VM contracts; actual FreeBSD guest execution is separate evidence."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from tools import freebsd_arm_vm as arm


_HEADER = bytes((0x7f, 0x45, 0x4c, 0x46, 2, 1, 1, 9)) + bytes(8) + bytes((2, 0, 183, 0))


def _proof(*, system='FreeBSD', machine='arm64', processor='aarch64',
           root='0', camera='1000', header=_HEADER, marker='HF_ARM_TASK_SSH_READY'):
    hexadecimal = ' '.join(f'{value:02x}' for value in header)
    return '\n'.join((system, machine, processor, root, camera, hexadecimal, marker)) + '\n'


class FreeBsdArmReadyTests(unittest.TestCase):
    def test_actual_arm64_identity_and_freebsd_elf_are_required_together(self):
        self.assertTrue(arm.READY_COMMAND.startswith('test ! -e /firstboot-reboot && '))
        value = arm.validate_ready(_proof())
        self.assertEqual(value, {'guest_system': 'FreeBSD', 'guest_machine': 'arm64', 'guest_processor': 'aarch64',
            'bootstrap_uid': 0, 'application_uid': 1000,
            'system_executable': {'path': '/bin/sh', 'format': 'ELF64', 'elf_machine': 183, 'elf_osabi': 9}})
        wrapped = _proof().replace(' '.join(f'{v:02x}' for v in _HEADER),
                                  ' '.join(f'{v:02x}' for v in _HEADER[:16]) + '\n  ' +
                                  ' '.join(f'{v:02x}' for v in _HEADER[16:]))
        self.assertEqual(arm.validate_ready(wrapped), value)

    def test_amd64_linux_wrong_processor_and_wrong_owners_are_refused(self):
        for fields in ({'system': 'Linux'}, {'system': 'FreeBSD-extra'},
                       {'machine': 'amd64'}, {'machine': 'aarch64'},
                       {'processor': 'amd64'}, {'processor': 'arm64'},
                       {'root': '1000'}, {'root': '00'}, {'camera': '0'}, {'camera': '1001'},
                       {'marker': 'HF_TASK_SSH_READY'}, {'marker': 'HF_ARM_TASK_SSH_READY extra'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                arm.validate_ready(_proof(**fields))

    def test_invalid_elf_class_endian_osabi_machine_and_truncation_are_refused(self):
        mutations = [(0, 0), (4, 1), (5, 2), (6, 0), (7, 0), (18, 62), (19, 1)]
        for index, replacement in mutations:
            header = bytearray(_HEADER)
            header[index] = replacement
            with self.subTest(index=index), self.assertRaises(ValueError):
                arm.validate_ready(_proof(header=bytes(header)))
        for header in (_HEADER[:19], _HEADER + b'\x00', b''):
            with self.subTest(length=len(header)), self.assertRaises(ValueError):
                arm.validate_ready(_proof(header=header))

    def test_bounded_proof_rejects_extra_missing_duplicate_and_nonhex_content(self):
        invalid = [_proof() + 'HF_ARM_TASK_SSH_READY\n', 'unexpected\n' + _proof(),
                   _proof().replace('1000\n', ''), _proof().replace('7f 45', 'gg 45'),
                   _proof().replace('7f 45', '7f45'), '', 'x' * 65537, None, b'FreeBSD\n']
        for value in invalid:
            with self.subTest(kind=type(value), prefix=str(value)[:24]), self.assertRaises(ValueError):
                arm.validate_ready(value)


class FreeBsdArmCommandTests(unittest.TestCase):
    def command(self, **options):
        directory = PurePosixPath('/home/task/private-arm-vm')
        metadata = {'ssh_port': 22242}
        with mock.patch.object(arm, '_metadata', return_value=(directory, metadata)), \
                mock.patch.object(arm.vm, '_tool', return_value='/usr/bin/qemu-system-aarch64'):
            return arm.qemu_command(directory, **options)

    def test_command_uses_tcg_arm64_private_uefi_and_bounded_resources(self):
        command = self.command()
        self.assertEqual(command[0], '/usr/bin/qemu-system-aarch64')
        self.assertEqual(command[command.index('-cpu') + 1], 'cortex-a72')
        self.assertEqual(command[command.index('-smp') + 1], '4')
        self.assertEqual(command[command.index('-m') + 1], '4096')
        self.assertIn('virt', command[command.index('-machine') + 1])
        self.assertIn('gic-version=3', command[command.index('-machine') + 1])
        self.assertTrue(('-accel' in command and command[command.index('-accel') + 1].split(',')[0] == 'tcg')
                        or 'accel=tcg' in command[command.index('-machine') + 1])
        self.assertEqual(command[command.index('-display') + 1], 'none')
        self.assertEqual(command[command.index('-monitor') + 1], 'none')
        self.assertEqual(command[command.index('-serial') + 1], 'stdio')
        self.assertNotIn('kvm', ' '.join(command))
        self.assertNotIn('host', command[command.index('-cpu') + 1])
        drives = [command[index + 1] for index, value in enumerate(command) if value == '-drive']
        firmware = [drive for drive in drives if 'if=pflash' in drive]
        self.assertEqual(len(firmware), 2)
        self.assertEqual(sum('readonly=on' in drive for drive in firmware), 1)
        for drive in firmware:
            self.assertIn('format=raw', drive)
            self.assertIn('/home/task/private-arm-vm/', drive)
            self.assertNotIn('/usr/share/', drive)
        self.assertTrue(any('guest.qcow2' in drive and 'format=qcow2' in drive for drive in drives))
        self.assertTrue(any('arm-seed.iso' in drive and 'readonly=on' in drive for drive in drives))
        devices = [command[index + 1] for index, value in enumerate(command) if value == '-device']
        self.assertTrue(any(value.startswith('virtio-scsi') for value in devices))
        self.assertTrue(any(value.startswith('scsi-cd') for value in devices))

    def test_network_forward_is_loopback_only_and_restriction_is_explicit(self):
        for restricted in (False, True):
            command = self.command(restricted=restricted)
            network = command[command.index('-netdev') + 1]
            self.assertIn('hostfwd=tcp:127.0.0.1:22242-:22', network)
            self.assertIn('restrict=' + ('on' if restricted else 'off'), network)
            text = ' '.join(command)
            self.assertNotIn('0.0.0.0', text)
            for forbidden in ('-virtfs', '-fsdev', '-daemonize', '-vnc', '-spice', '-smb'):
                self.assertNotIn(forbidden, command)

    def test_invalid_cpu_or_network_types_fail_before_spawning(self):
        for value in (True, False, 0, -1, 17, 4096, 1.5, '4', float('nan')):
            with self.subTest(cpus=value), self.assertRaises(ValueError):
                self.command(cpus=value)
        for value in (None, 0, 1, 'off'):
            with self.subTest(restricted=value), self.assertRaises(ValueError):
                self.command(restricted=value)

    def test_eight_cpu_choice_is_explicit_and_does_not_change_memory_or_network(self):
        command = self.command(cpus=8, restricted=True)
        self.assertEqual(command[command.index('-smp') + 1], '8')
        self.assertEqual(command[command.index('-m') + 1], '4096')
        self.assertIn('restrict=on', command[command.index('-netdev') + 1])


class FreeBsdArmPreparationTests(unittest.TestCase):
    def test_arm_seed_keeps_key_only_transport_uses_efi_console_and_distinct_hostname(self):
        instance = '28b44e2b-febe-45d9-bb7b-434bda112840'
        user, meta = arm.arm_seed_data('ssh-ed25519 AAAATEST hf-task-bootstrap', instance)
        self.assertIn(b'console="efi"', user)
        self.assertNotIn(b'comconsole', user)
        self.assertIn(b'local-hostname: daos-arm64-dev\n', meta)
        self.assertNotIn(b'daos-freebsd-dev', meta)
        for required in (b'ssh_pwauth: false', b'package_update: false', b'package_upgrade: false',
                         b'PermitRootLogin prohibit-password', b'PasswordAuthentication no',
                         b'KbdInteractiveAuthentication no', b'AuthenticationMethods publickey',
                         b'AllowUsers root camera', b'pw usermod root -w no'):
            self.assertIn(required, user)
        self.assertIn(('instance-id: ' + instance).encode(), meta)
        early_files, commands = user.split(b'runcmd:\n', 1)
        self.assertIn(b'write_files:\n', early_files)
        override_marker = b'  - path: /etc/rc.conf.local\n'
        self.assertEqual(early_files.count(override_marker), 1)
        override = early_files.split(override_marker, 1)[1].split(b'  - path:', 1)[0]
        self.assertIn(b'    append: true\n', override)
        self.assertIn(b'    content: |\n      firstboot_pkg_upgrade_enable="NO"\n', override)
        self.assertNotIn(b'firstboot_pkg_upgrade_enable', commands)

    def test_private_firmware_copies_and_arm_seed_are_created_without_launching_a_vm(self):
        # Small stand-ins exercise copying and metadata, not firmware execution.
        with tempfile.TemporaryDirectory(prefix='hf-arm-prepare-') as temporary:
            root = Path(temporary).resolve()
            code = root / 'installed-code.fd'; code.write_bytes(b'CODE' * 1024)
            variables = root / 'installed-vars.fd'; variables.write_bytes(b'VARS' * 1024)
            directory = root / 'private'
            instance = '28b44e2b-febe-45d9-bb7b-434bda112840'
            def generic_prepare(archive, expected, destination, *, port):
                self.assertEqual(destination, directory)
                self.assertEqual(port, 22242)
                directory.mkdir()
                (directory / 'task_ed25519.pub').write_text('ssh-ed25519 AAAATEST hf-task-bootstrap\n')
                (directory / 'vm.json').write_text('{"generic":true}\n')
                return {'instance_id': instance, 'base_sha256': 'b' * 64, 'seed_sha256': 'c' * 64}
            try:
                with mock.patch.object(arm, 'FIRMWARE_BYTES', 4096), \
                        mock.patch.object(arm.vm, '_linux'), mock.patch.object(arm.vm, '_tool'), \
                        mock.patch.object(arm.vm, '_directory', return_value=directory), \
                        mock.patch.object(arm.vm, 'prepare', side_effect=generic_prepare), \
                        mock.patch.object(arm.subprocess, 'Popen') as spawn:
                    value = arm.prepare(root / 'archive.xz', 'a' * 64, directory,
                                        firmware_code=code, firmware_vars=variables)
                    spawn.assert_not_called()
                self.assertEqual(value['format'], 'hf-freebsd-arm-task-vm-v1')
                self.assertEqual(value['brand'], 'DaOS')
                self.assertEqual(value['target_architecture'], 'aarch64')
                self.assertEqual(value['seed_file'], 'arm-seed.iso')
                self.assertFalse(value['secure_boot_validated'])
                self.assertFalse(value['physical_camera_validated'])
                self.assertEqual(json.loads((directory / 'arm-vm.json').read_text()), value)
                self.assertEqual(value['seed_sha256'], hashlib.sha256((directory / 'arm-seed.iso').read_bytes()).hexdigest())
                for filename, source in (('uefi-code.fd', code), ('uefi-vars.fd', variables)):
                    copy = directory / filename
                    self.assertEqual(copy.read_bytes(), source.read_bytes())
                    self.assertNotEqual(copy.stat().st_ino, source.stat().st_ino)
                    self.assertEqual(value['firmware'][filename]['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                (directory / 'uefi-vars.fd').write_bytes(b'changed guest variables')
                self.assertEqual(variables.read_bytes(), b'VARS' * 1024)
                self.assertEqual(code.read_bytes(), b'CODE' * 1024)
                if os.name == 'posix':
                    self.assertEqual((directory / 'uefi-code.fd').stat().st_mode & 0o777, 0o444)
                    self.assertEqual((directory / 'uefi-vars.fd').stat().st_mode & 0o777, 0o600)
            finally:
                # Windows chmod(0444) sets the file's read-only attribute.
                if (directory / 'uefi-code.fd').exists():
                    (directory / 'uefi-code.fd').chmod(0o600)

    def test_small_firmware_is_refused_before_generic_preparation_or_destination(self):
        with tempfile.TemporaryDirectory(prefix='hf-arm-firmware-') as temporary:
            directory = Path(temporary).resolve()
            code = directory / 'code.fd'; code.write_bytes(b'not a complete firmware image')
            variables = directory / 'vars.fd'; variables.write_bytes(b'small variables')
            destination = directory / 'new-vm'
            with mock.patch.object(arm.vm, '_linux'), mock.patch.object(arm.vm, '_tool'), \
                    mock.patch.object(arm.vm, 'prepare') as prepare:
                with self.assertRaises(ValueError):
                    arm.prepare(directory / 'archive.xz', 'a' * 64, destination,
                                firmware_code=code, firmware_vars=variables)
                prepare.assert_not_called()
            self.assertFalse(destination.exists())

    def test_same_firmware_input_is_refused_before_generic_preparation(self):
        path = Path('/installed/firmware.fd')
        with mock.patch.object(arm.vm, '_linux'), mock.patch.object(arm.vm, '_tool'), \
                mock.patch.object(arm, '_firmware', return_value=path), mock.patch.object(arm.vm, 'prepare') as prepare:
            with self.assertRaises(ValueError):
                arm.prepare('/archive', 'a' * 64, '/new', firmware_code=path, firmware_vars=path)
            prepare.assert_not_called()

    def test_metadata_refuses_amd64_substitution_hash_changes_and_network_mismatch(self):
        directory = Path('/home/task/private-arm-vm')
        valid = {'format': 'hf-freebsd-arm-task-vm-v1', 'prepared': True, 'brand': 'DaOS',
                 'guest_machine': 'arm64', 'target_architecture': 'aarch64', 'acceleration': 'tcg',
                 'ssh_port': 22242, 'generic_vm_metadata_sha256': 'a' * 64}
        with mock.patch.object(arm.vm, '_metadata', return_value=(directory, {'ssh_port': 22242})), \
                mock.patch.object(arm.vm, '_digest', return_value='a' * 64), \
                mock.patch.object(arm.vm, '_read_json', return_value=valid):
            self.assertEqual(arm._metadata(directory), (directory, valid))
            for changes in ({'format': 'hf-freebsd-task-vm-v1'}, {'prepared': 1}, {'brand': 'other'},
                            {'guest_machine': 'amd64'}, {'target_architecture': 'x86_64'},
                            {'acceleration': 'kvm'}, {'ssh_port': 22241},
                            {'generic_vm_metadata_sha256': 'b' * 64}):
                with self.subTest(changes=changes), \
                        mock.patch.object(arm.vm, '_read_json', return_value={**valid, **changes}), \
                        self.assertRaises(ValueError):
                    arm._metadata(directory)


if __name__ == '__main__':
    unittest.main()

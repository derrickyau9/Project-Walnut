"""Host-only VM guards: fake QMP peers, mocked supervisors, tiny qemu-img files.

These tests never invoke qemu-system, boot an image, or contact another VM.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

import vm


@contextlib.contextmanager
def fake_qmp(path, *, wrong_id=False, name=None, instance_uuid=None, invalid_greeting=False):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(3)
    errors = []

    def serve():
        try:
            connection, _ = server.accept()
            with connection, connection.makefile('rb') as stream:
                greeting = {} if invalid_greeting else {'QMP': {'version': {}, 'capabilities': []}}
                connection.sendall((json.dumps(greeting) + '\r\n').encode())
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    request = json.loads(line)
                    value = {}
                    if request['execute'] == 'query-name':
                        value = {'name': name}
                    elif request['execute'] == 'query-uuid':
                        value = {'UUID': instance_uuid}
                    if request['execute'] != 'qmp_capabilities':
                        connection.sendall(b'{"event":"TEST_EVENT"}\r\n')
                    response = {'id': 'wrong' if wrong_id else request['id'], 'return': value}
                    connection.sendall((json.dumps(response) + '\r\n').encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.close()
        thread.join(timeout=4)
        if thread.is_alive():
            raise AssertionError('Fake QMP peer did not shut down')
        if errors:
            raise errors[0]


class VMGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='daos-vm-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def image(self):
        image = self.root / 'release.img'
        image.write_bytes(b'host guard fixture' * 1024)
        evidence = {'format': 'DaOS-NetBSD-image-v1', 'status': 'assembled-not-boot-validated',
                    'image': {'path': str(image), 'size': image.stat().st_size, 'sha256': vm.sha(image)},
                    'profile': {'name': 'daos-test', 'target': {'machine': 'evbarm', 'architecture': 'aarch64'}},
                    'source_lock_sha256': '1' * 64, 'input_hash': '2' * 64,
                    'kernel': {'path': '/fixture/netbsd', 'sha256': '3' * 64, 'elf': {'machine': 'AArch64'}},
                    'base_stage_receipts': {name: '4' * 64 for name in ('tools', 'distribution', 'kernel')},
                    'target_boot_validated': False}
        vm.save(self.root / 'image-evidence.json', evidence)
        return image, evidence

    def record(self, *, prepared=False):
        directory = self.root / 'run'
        directory.mkdir(mode=0o700)
        image, _ = self.image()
        (directory / 'uefi-code.fd').write_bytes(b'test firmware')
        record = {'format': vm.RUN_FORMAT, 'status': 'prepared' if prepared else 'running',
                  'run_directory': str(directory), 'name': 'DaOS-BSD-source-123456789abc',
                  'uuid': str(uuid.uuid4()), 'qemu_pid': os.getpid(),
                  'qemu_process': vm.process_stamp(os.getpid()),
                  'qemu_executable': str(Path(sys.executable).resolve()), 'visible': False,
                  'raw_image': str(image), 'raw_stamp': vm.file_stamp(image),
                  'firmware_sha256': vm.sha(directory / 'uefi-code.fd'),
                  'argv': [os.fsdecode(word) for word in Path('/proc/self/cmdline').read_bytes().split(b'\0') if word]}
        (directory / 'qemu.pid').write_text(str(os.getpid()))
        vm.save(directory / 'vm.json', record)
        return directory, record

    def test_assembly_schema_and_image_hash_are_enforced(self):
        image, evidence = self.image()
        self.assertEqual(vm.validate_image(image)['raw_sha256'], evidence['image']['sha256'])
        image.write_bytes(b'changed' * 1024)
        with self.assertRaises(ValueError):
            vm.validate_image(image)

    def test_netbsd_input_pair_uses_keyboard_and_compatible_relative_mouse(self):
        directory,record=self.record()
        command=vm.qemu_command(directory,record)
        self.assertIn('usb-kbd,bus=usb.0',command)
        self.assertIn('usb-mouse,bus=usb.0',command)
        self.assertNotIn('usb-tablet,bus=usb.0',command)
        self.assertIn('ramfb',command)

    def test_incomplete_or_foreign_image_evidence_is_rejected(self):
        image, evidence = self.image()
        evidence['status'] = 'building'
        vm.save(self.root / 'image-evidence.json', evidence)
        with self.assertRaises(ValueError):
            vm.validate_image(image)
        evidence['status'] = 'assembled-not-boot-validated'
        evidence['profile']['target']['architecture'] = 'amd64'
        vm.save(self.root / 'image-evidence.json', evidence)
        with self.assertRaises(ValueError):
            vm.validate_image(image)

    def test_explicit_relocated_copy_preserves_original_evidence_and_checks_bytes(self):
        image, evidence = self.image()
        copied = self.root / 'published'
        copied.mkdir()
        artifact = copied / image.name
        receipt = self.root / 'image-evidence.json'
        original_evidence = receipt.read_bytes()
        shutil.copyfile(image, artifact)
        shutil.copyfile(receipt, copied / receipt.name)
        with self.assertRaisesRegex(ValueError, 'Image path differs'):
            vm.validate_image(artifact)
        validated = vm.validate_image(artifact, allow_relocated=True)
        self.assertEqual(validated['raw_sha256'], evidence['image']['sha256'])
        self.assertEqual(validated['raw_image'], str(artifact))
        self.assertEqual(validated['assembly_image_path'], str(image))
        self.assertTrue(validated['artifact_relocated'])
        self.assertEqual((copied / receipt.name).read_bytes(), original_evidence)
        changed = bytearray(artifact.read_bytes())
        changed[0] ^= 1
        artifact.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'differs from its assembly receipt'):
            vm.validate_image(artifact, allow_relocated=True)
    def test_default_image_follows_state_receipt(self):
        image, evidence = self.image()
        state_dir = self.root / 'workspace/state'
        state_dir.mkdir(parents=True)
        vm.save(state_dir / 'image.json', dict(evidence, receipt=str(self.root / 'image-evidence.json')))
        self.assertEqual(vm.image_path(None, workspace=state_dir.parent), image)
        state = vm.read(state_dir / 'image.json')
        state['image'] = dict(state['image'], path='/different/path.img')
        vm.save(state_dir / 'image.json', state)
        with self.assertRaises(ValueError):
            vm.image_path(None, workspace=state_dir.parent)

    def test_run_guards_have_no_side_effects(self):
        for value in ('/', '/usr/local/daos', str(self.root / 'bad,option'), str(self.root / ('x' * 100))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                vm.run_directory(value, new=True)
        target = self.root / 'actual'
        target.mkdir()
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            vm.run_directory(link / 'run', new=True)
        self.assertFalse((target / 'run').exists())

    def test_windows_paths_and_console_text_are_forwarded_without_shell_parsing(self):
        args = argparse.Namespace(action='console', distro='Debian',
                                  image=r'F:\Releases\DaOS latest.img', run_dir='/home/dy09/daos-vm',
                                  workspace='/home/dy09/daos-bsd-buildroot-ui',
                                  output=r'.\capture one.ppm', visible=True, text='help\r\n$(literal)')
        command = vm.wsl_command(args, source=r'F:\Project HasselFree\system\bsd-buildroot\vm.py', cwd=r'F:\Evidence')
        self.assertIn('/mnt/f/Releases/DaOS latest.img', command)
        self.assertIn('/mnt/f/Evidence/capture one.ppm', command)
        self.assertEqual(command[-1], args.text)
        self.assertIn('/home/dy09/daos-vm', command)
        with self.assertRaises(ValueError):
            vm.windows_path(r'F:drive-relative.img', cwd=r'F:\Evidence')

    def test_qmp_negotiation_correlates_ids_and_skips_events(self):
        path = self.root / 'qmp.sock'
        with fake_qmp(path, name='test'):
            client = vm.QMP(path, expected_pid=os.getpid())
            try:
                self.assertEqual(client.call('query-name'), {'name': 'test'})
            finally:
                client.close()

    def test_qmp_wrong_id_and_invalid_greeting_are_rejected(self):
        for invalid in ('id', 'greeting'):
            path = self.root / (invalid + '.sock')
            with fake_qmp(path, wrong_id=invalid == 'id', invalid_greeting=invalid == 'greeting'):
                with self.assertRaises(ValueError):
                    vm.QMP(path)

    def test_identity_checks_name_uuid_and_pid_starttime(self):
        directory, record = self.record()
        with fake_qmp(directory / 'qmp.sock', name=record['name'], instance_uuid=str(uuid.uuid4())):
            with self.assertRaisesRegex(ValueError, 'UUID'):
                vm.identity(directory)
        (directory / 'qmp.sock').unlink()
        with fake_qmp(directory / 'qmp.sock', name='another VM', instance_uuid=record['uuid']):
            with self.assertRaisesRegex(ValueError, 'name'):
                vm.identity(directory)
        record['qemu_process']['start_ticks'] += 1
        vm.save(directory / 'vm.json', record)
        with mock.patch.object(vm, 'QMP') as qmp:
            with self.assertRaisesRegex(ValueError, 'PID was reused'):
                vm.identity(directory)
            qmp.assert_not_called()

    def test_supervisor_records_failure_without_starting_another_process(self):
        directory, record = self.record(prepared=True)
        with mock.patch.object(vm.subprocess, 'Popen', side_effect=OSError('test launch failure')) as launch:
            with self.assertRaises(OSError):
                vm.supervise(argparse.Namespace(run_dir=directory))
            launch.assert_called_once()
        failed = vm.read(directory / 'vm.json')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['supervisor_pid'], os.getpid())
        self.assertTrue((directory / 'supervisor.lock').is_file())
        self.assertTrue((directory / 'qemu.log').is_file())
        with mock.patch.object(vm.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                vm.supervise(argparse.Namespace(run_dir=directory))
            launch.assert_not_called()

    def stopped_record(self):
        directory, record = self.record()
        record.update(vm.validate_image(Path(record['raw_image'])))
        (directory / 'disk.qcow2').write_bytes(b'preserved guest photos')
        (directory / 'uefi-vars.fd').write_bytes(b'preserved guest settings')
        (directory / 'qemu.log').write_text('previous qemu log')
        (directory / 'serial.log').write_text('previous guest serial')
        (directory / 'supervisor.log').write_text('previous supervisor log')
        (directory / 'supervisor.lock').write_text('previous supervisor pid')
        record.update(status='exited', qemu_exit_code=0, qemu_pid=2147483647,
                      qemu_img_executable='/fixture/qemu-img', boot_number=1,
                      persistent_state=vm.persistent_state(directory))
        vm.save(directory / 'vm.json', record)
        args = argparse.Namespace(run_dir=directory, image=Path(record['raw_image']),
                                  workspace=self.root, visible=True)
        return directory, record, args

    def test_resume_preserves_guest_disk_vars_and_archives_boot_evidence(self):
        directory, record, args = self.stopped_record()
        previous = (directory / 'vm.json').read_bytes()
        disk = (directory / 'disk.qcow2').read_bytes()
        variables = (directory / 'uefi-vars.fd').read_bytes()
        programs = {'qemu-system-aarch64': record['qemu_executable'], 'qemu-img': record['qemu_img_executable']}
        with mock.patch.object(vm, '_program', side_effect=lambda name: programs[name]), \
             mock.patch.object(vm, 'overlay_info', return_value={'format': 'qcow2'}), \
             mock.patch.object(vm.subprocess, 'Popen', return_value=argparse.Namespace(pid=999999)) as spawn, \
             mock.patch.object(vm, 'await_running', return_value={'resumed': True}):
            result = vm.resume(args)
        self.assertEqual(result, {'resumed': True})
        self.assertEqual((directory / 'disk.qcow2').read_bytes(), disk)
        self.assertEqual((directory / 'uefi-vars.fd').read_bytes(), variables)
        current = vm.read(directory / 'vm.json')
        self.assertEqual(current['boot_number'], 2)
        self.assertEqual(current['uuid'], record['uuid'])
        self.assertNotEqual(current['name'], record['name'])
        prior_record = Path(current['previous_boot_receipt'])
        self.assertEqual(prior_record.read_bytes(), previous)
        self.assertEqual((prior_record.parent / 'serial.log').read_text(), 'previous guest serial')
        self.assertEqual((prior_record.parent / 'qemu.log').read_text(), 'previous qemu log')
        self.assertEqual(vm.read(prior_record.parent / 'history.json')['status'], 'archived')
        self.assertEqual(current['status'], 'prepared')
        self.assertIn('_run', spawn.call_args.args[0])
        self.assertNotIn('qemu-system', spawn.call_args.args[0][0])

    def test_resume_rejects_changed_persistent_data_before_archiving(self):
        directory, record, args = self.stopped_record()
        original = (directory / 'vm.json').read_bytes()
        (directory / 'uefi-vars.fd').write_bytes(b'changed externally')
        programs = {'qemu-system-aarch64': record['qemu_executable'], 'qemu-img': record['qemu_img_executable']}
        with mock.patch.object(vm, '_program', side_effect=lambda name: programs[name]), \
             mock.patch.object(vm.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'shutdown receipt'):
                vm.resume(args)
            spawn.assert_not_called()
        self.assertEqual((directory / 'vm.json').read_bytes(), original)
        self.assertFalse((directory / 'boots').exists())

    def test_launch_refuses_an_old_image_and_keeps_existing_vm_untouched(self):
        directory, record, args = self.stopped_record()
        image = Path(record['raw_image'])
        image.write_bytes(b'a newer image')
        evidence = vm.read(image.parent / 'image-evidence.json')
        evidence['image'].update(size=image.stat().st_size, sha256=vm.sha(image))
        vm.save(image.parent / 'image-evidence.json', evidence)
        with mock.patch.object(vm.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'new --run-dir'):
                vm.launch(args)
            spawn.assert_not_called()
        self.assertEqual(vm.read(directory / 'vm.json')['boot_number'], 1)
        self.assertFalse((directory / 'boots').exists())

    def test_launch_returns_actual_status_for_an_existing_running_vm(self):
        directory, record = self.record()
        record.update(vm.validate_image(Path(record['raw_image'])))
        vm.save(directory / 'vm.json', record)
        args = argparse.Namespace(run_dir=directory, image=Path(record['raw_image']), workspace=self.root, visible=True)
        client = argparse.Namespace(call=mock.Mock(return_value={'running': True}), close=mock.Mock())
        with mock.patch.object(vm, 'identity', return_value=(record, client)), \
             mock.patch.object(vm.subprocess, 'Popen') as spawn, \
             contextlib.redirect_stdout(io.StringIO()):
            result = vm.launch(args)
        self.assertTrue(result['reused_running'])
        self.assertEqual(result['qemu'], {'running': True})
        spawn.assert_not_called()
        client.call.assert_called_once_with('query-status')
        client.close.assert_called_once()

    def test_launch_selects_fresh_start_only_for_missing_run_directory(self):
        args = argparse.Namespace(run_dir=self.root / 'new-vm')
        with mock.patch.object(vm, 'start', return_value={'fresh': True}) as start, \
             mock.patch.object(vm, 'resume') as resume:
            self.assertEqual(vm.launch(args), {'fresh': True})
            start.assert_called_once_with(args)
            resume.assert_not_called()

    def test_supervisor_clean_exit_records_persistent_state_without_real_qemu(self):
        directory, record = self.record(prepared=True)
        (directory / 'disk.qcow2').write_bytes(b'guest disk')
        (directory / 'uefi-vars.fd').write_bytes(b'guest nvram')
        process = argparse.Namespace(pid=999999, wait=lambda: 0)
        with mock.patch.object(vm.subprocess, 'Popen', return_value=process), \
             mock.patch.object(vm, 'process_stamp', return_value={'start_ticks': 1, 'executable': '/fixture/qemu'}):
            vm.supervise(argparse.Namespace(run_dir=directory))
        stopped = vm.read(directory / 'vm.json')
        self.assertEqual(stopped['status'], 'exited')
        self.assertEqual(stopped['qemu_exit_code'], 0)
        self.assertEqual(stopped['persistent_state'], vm.persistent_state(directory))

    @unittest.skipUnless(shutil.which('qemu-img') and Path('/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd').is_file(), 'QEMU image utility/AAVMF absent')
    def test_real_qemu_img_overlay_with_mock_supervisor_never_launches_qemu(self):
        image, _ = self.image()
        original = vm.sha(image)
        directory = self.root / 'new-vm'
        args = argparse.Namespace(run_dir=directory, image=image, workspace=self.root, visible=False)
        original_popen = subprocess.Popen
        launches = []

        def dispatch(command, *args, **kwargs):
            if '_run' in command:
                launches.append(command)
                state = vm.read(directory / 'vm.json')
                state.update(status='failed', error='test deliberately suppressed supervisor')
                vm.save(directory / 'vm.json', state)
                return argparse.Namespace(pid=123456)
            if 'qemu-system' in str(command[0]):
                raise AssertionError('A host-only test attempted to launch QEMU')
            return original_popen(command, *args, **kwargs)

        with mock.patch.object(vm.subprocess, 'Popen', side_effect=dispatch):
            with self.assertRaisesRegex(RuntimeError, 'did not remain running'):
                vm.start(args)
        self.assertEqual(len(launches), 1)
        self.assertEqual(vm.sha(image), original)
        state = vm.read(directory / 'vm.json')
        self.assertEqual(state['overlay']['format'], 'qcow2')
        self.assertEqual(state['overlay']['full-backing-filename'], str(image))
        self.assertEqual(state['status'], 'failed')


if __name__ == '__main__':
    unittest.main()

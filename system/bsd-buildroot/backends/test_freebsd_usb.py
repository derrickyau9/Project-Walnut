"""Host-only USB backend guards; never starts a VM or compiles a BSD base."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from backends import freebsd_usb as backend
from backends import freebsd_usb_guest as guest
import daos


PROFILE = backend.PROJECT / 'system/bsd-buildroot/targets/rpi4-aarch64-usb/profile.json'


class USBBackendGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='daos-usb-backend-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self):
        root = self.root
        source, stage, objects = root / 'source', root / 'stage', root / 'kernel-objects'
        anchors = ('Makefile', 'Makefile.inc1', 'sys/conf/newvers.sh', 'sys/arm64/conf/GENERIC')
        for name in anchors:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture ' + name)
        for name in ('bin/sh', 'sbin/init', 'lib/libc.so.7', 'boot/kernel/kernel'):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = bytearray(64)
            data[:8] = b'\x7fELF\x02\x01\x01\x09'
            struct.pack_into('<H', data, 18, 183)
            path.write_bytes(data)
        (stage / 'METALOG').write_text('fixture ownership metadata')
        for name in ('usr/include/sys/types.h', 'usr/include/sys/param.h'):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture SDK header')
        objects.mkdir()
        (objects / 'opt_global.h').write_text('fixture options')
        config = root / 'src.conf'
        config.write_text('WITHOUT_MAN=yes\n')
        kernel = root / 'DaOS'
        kernel.write_text('include GENERIC\nident DaOS\ndevice splash\n')
        archive = root / 'source.txz'
        archive.write_bytes(b'fixture pinned source archive')
        lock = {'commit': backend.COMMIT, 'archive_sha256': guest.sha(archive), 'archive_bytes': archive.stat().st_size}
        state = {'format': 'DaOS-freebsd-base-build-v2', 'status': 'completed',
                 'target': {'architecture': 'arm64'}, 'source': lock, 'source_lock_sha256': 'a' * 64,
                 'source_directory': str(source), 'stage_directory': str(stage),
                 'source_anchor_sha256': {name: guest.sha(source / name) for name in anchors},
                 'artifact_sha256': {name: guest.sha(stage / name) for name in ('bin/sh', 'sbin/init', 'lib/libc.so.7', 'boot/kernel/kernel', 'METALOG')},
                 'configuration_sha256': {'src.conf': guest.sha(config)},
                 'kernel_config': {'device_splash': True, 'path': str(kernel), 'sha256': guest.sha(kernel)}}
        receipt = root / 'base.json'
        receipt.write_text(json.dumps(state))
        request = {'base': {'receipt': str(receipt), 'receipt_sha256': guest.sha(receipt),
                           'source_root': str(source), 'stage': str(stage), 'src_conf': str(config), 'kernel_object': str(objects)},
                   'source': lock, 'source_archive': str(archive), 'source_lock_sha256': 'a' * 64,
                   'world_config_sha256': guest.sha(config), 'kernel_config_sha256': guest.sha(kernel)}
        return request, stage

    def verify(self, request):
        with mock.patch.object(guest.platform, 'system', return_value='FreeBSD'), \
             mock.patch.object(guest.platform, 'release', return_value='15.1-RELEASE-p6'):
            return guest.verify_base(request)

    def test_reuse_checks_actual_bytes_and_never_claims_compilation(self):
        request, stage = self.fixture()
        result = self.verify(request)
        self.assertEqual(result['status'], 'success')
        self.assertFalse(result['executed'])
        self.assertTrue(result['verification_executed'])
        self.assertTrue(result['reused'])
        self.assertFalse(result['world_recompiled'])
        with (stage / 'bin/sh').open('r+b') as stream:
            stream.seek(63)
            stream.write(b'X')
        with self.assertRaisesRegex(ValueError, 'anchor changed'):
            self.verify(request)

    def test_reuse_refuses_receipt_or_kernel_configuration_mismatch(self):
        request, stage = self.fixture()
        request['base']['receipt_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'receipt differs'):
            self.verify(request)
        request['base']['receipt_sha256'] = guest.sha(request['base']['receipt'])
        request['kernel_config_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'kernel configuration differs'):
            self.verify(request)

    def test_plan_has_no_side_effects_and_no_ssh(self):
        work = self.root / 'never-created'
        with mock.patch.object(backend, '_provider', side_effect=AssertionError('plan attempted SSH')):
            result = backend.plan(PROFILE, work)
        self.assertFalse(result['executed'])
        self.assertFalse(work.exists())
        self.assertNotIn('base', result['commands'])
        self.assertIn('--adb-bundle', result['commands']['image'])
        self.assertIn('WORLDTMP', result['source_SDK'])
        argv = result['commands']['usb']
        self.assertRegex(argv[argv.index('--archive-sha256') + 1], r'^[0-9a-f]{64}$')

    def test_explicit_rebuild_plan_calls_original_source_recipe(self):
        result = backend.plan(PROFILE, self.root / 'new-build', rebuild_base=True, jobs=8)
        argv = result['commands']['base']
        self.assertIn('--source-archive', argv)
        self.assertIn('/home/camera/src-p6.txz', argv)
        self.assertEqual(argv[argv.index('--jobs') + 1], '8')
        self.assertTrue(argv[1].endswith('/base-recipe/build_base.py'))
        self.assertFalse(result['executed'])

    def test_usb_source_archive_is_deterministic_and_contains_only_recipe_files(self):
        first = backend._usb_archive()
        self.assertEqual(first, backend._usb_archive())
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            self.assertEqual({p.name for p in archive}, {'freebsd_usb/' + n for n in backend.USB_FILES})
            for item in archive.getmembers():
                self.assertTrue(item.isfile())
                self.assertEqual(item.mtime, 1793836800)
                self.assertNotIn('adbkey', item.name)

    def test_guest_outputs_reject_live_os_and_image_work_escape(self):
        for path in ('/etc/adb', '/usr/local/bin/output', '/var/DaOS-test/../escape', 'relative'):
            with self.assertRaises(backend.common.BackendError):
                backend._guest_path(path, output=True)
        with self.assertRaisesRegex(backend.common.BackendError, 'configured private image'):
            backend.plan(PROFILE, self.root / 'work', guest_work_dir='/var/DaOS-another/image')

    def test_inspect_rpc_runs_its_real_serialization_without_a_vm(self):
        path = self.root / 'receipt.json'
        path.write_text('{"status":"completed"}')
        spec = backend._spec(PROFILE, self.root / 'workspace')
        def locally(_spec, code, timeout=90):
            return subprocess.check_output([sys.executable, '-c', code], text=True)
        with mock.patch.object(backend, '_ssh', side_effect=locally):
            inspected = backend._rpc(spec, 'inspect', {'files': {'receipt': str(path)}})
        self.assertEqual(inspected['receipt']['sha256'], guest.sha(path))
        self.assertEqual(inspected['receipt']['json']['status'], 'completed')

    def test_changed_artifact_cannot_reuse_successful_stage(self):
        spec = backend._spec(PROFILE, self.root / 'workspace')
        backend._init(spec)
        stored = {'status': 'success', 'input_hash': spec['input_hash'],
                  'outputs': {'helper': {'path': '/var/daos-builds/owned/helper', 'sha256': 'a' * 64}}}
        backend.common._write_json(spec['workspace'] / 'state/usb.json', stored)
        with mock.patch.object(backend, '_rpc', return_value={'helper': {'path': '/var/daos-builds/owned/helper', 'sha256': 'b' * 64}}):
            with self.assertRaisesRegex(backend.common.BackendError, 'artifact changed'):
                backend._cached(spec, 'usb')

    def test_uploaded_recipe_tamper_prevents_native_build(self):
        spec = backend._spec(PROFILE, self.root / 'workspace')
        actual = {
            'usb_archive': {'sha256': spec['usb_archive_sha256']},
            'usb_recipe': {'sha256': 'f' * 64},
        }
        with mock.patch.object(backend, '_rpc', return_value=actual):
            with self.assertRaisesRegex(backend.common.BackendError, 'recipe changed'):
                backend._verify_uploaded_inputs(spec)

    def test_sdk_drift_prevents_active_base_adoption(self):
        spec = backend._spec(PROFILE, self.root / 'workspace')
        configured = {'active_base': spec['profile']['base'],
                      'verification': {'SDK_header_sha256': {'usr/include/sys/types.h': 'a' * 64}}}
        current = {'SDK_header_sha256': {'usr/include/sys/types.h': 'b' * 64},
                   'kernel_object_options_sha256': 'c' * 64}
        with mock.patch.object(backend, '_verify_uploaded_inputs'), \
             mock.patch.object(backend, '_verify_base', return_value=current):
            with self.assertRaisesRegex(backend.common.BackendError, 'SDK/kernel object inputs changed'):
                backend._active(spec, configured)

    def test_unreachable_builder_error_names_existing_vm_serve_command(self):
        spec = backend._spec(PROFILE, self.root / 'workspace')
        provider = mock.Mock()
        provider.ssh_command.return_value = {'returncode': 255, 'stderr': 'Connection refused', 'stdout': ''}
        with mock.patch.object(backend, '_provider', return_value=provider):
            with self.assertRaisesRegex(backend.common.BackendError, r'freebsd_vm.py.? serve --work-dir'):
                backend._ssh(spec, 'print(1)')
        self.assertEqual(provider.ssh_command.call_args.kwargs['user'], 'root')

    def scratch_fixture(self, *, mount=True, available=16 * 1024 * 1024):
        root = self.root / 'scratch'
        root.mkdir()
        requested = 8 * 1024 * 1024
        request = {'root': str(root), 'required_free_bytes': requested}
        table = 'tmpfs ' + str(root) + ' tmpfs rw 0 0\n' if mount else '/dev/vtbd0p2 / ufs rw 0 0\n'
        stats = SimpleNamespace(f_bavail=available // 4096, f_frsize=4096, f_blocks=8192)
        return request, table, stats

    def check_scratch(self, request, table, stats):
        with mock.patch.object(guest.platform, 'system', return_value='FreeBSD'), \
             mock.patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(stdout=table)), \
             mock.patch.object(guest.os, 'statvfs', return_value=stats):
            return guest.verify_scratch(request)

    def test_unmounted_scratch_directory_cannot_fall_back_to_root_disk(self):
        request, table, stats = self.scratch_fixture(mount=False)
        with self.assertRaisesRegex(ValueError, 'not an actual tmpfs mount'):
            self.check_scratch(request, table, stats)
        self.assertEqual(list(Path(request['root']).iterdir()), [])

    def test_mounted_but_insufficient_scratch_space_is_rejected(self):
        request, table, stats = self.scratch_fixture(available=4 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, 'insufficient free space'):
            self.check_scratch(request, table, stats)

    def test_available_tmpfs_is_verified_without_image_mutation(self):
        request, table, stats = self.scratch_fixture()
        verified = self.check_scratch(request, table, stats)
        self.assertEqual(verified['status'], 'success')
        self.assertTrue(verified['mounted'])
        self.assertFalse(verified['image_mutation_executed'])
        self.assertEqual(verified['available_bytes'], 16 * 1024 * 1024)
        self.assertEqual(list(Path(request['root']).iterdir()), [])

    def test_image_scratch_failure_never_creates_stage_or_image(self):
        spec = backend._spec(PROFILE, self.root / 'workspace')
        backend._init(spec)
        def cached(_spec, name):
            return {'status': 'success'} if name == 'packages' else None
        with mock.patch.object(backend, '_load_spec', return_value=(spec, {})), \
             mock.patch.object(backend, '_active', return_value=spec['profile']['base']), \
             mock.patch.object(backend, '_cached', side_effect=cached), \
             mock.patch.object(backend, '_verify_project'), \
             mock.patch.object(backend, '_rpc', side_effect=ValueError('not an actual tmpfs mount')), \
             mock.patch.object(backend, '_stage') as execute:
            with self.assertRaisesRegex(ValueError, 'not an actual tmpfs mount'):
                backend.image(PROFILE, spec['workspace'])
            execute.assert_not_called()
        self.assertFalse((spec['workspace'] / 'state/image.json').exists())
        self.assertEqual(list((spec['workspace'] / 'receipts').iterdir()), [])

    def test_default_cli_profile_dispatches_usb_plan_with_separate_workspace(self):
        plan = mock.Mock(return_value={'executed': False})
        with mock.patch.object(sys, 'argv', ['daos.py', 'plan']), \
             mock.patch.object(daos, 'module', return_value=type('Backend', (), {'plan': plan})), \
             contextlib.redirect_stdout(io.StringIO()):
            daos.main()
        self.assertEqual(plan.call_args.args[1], daos.DEFAULT_USB_WORKSPACE)

    def test_explicit_netbsd_plan_keeps_its_own_workspace(self):
        plan = mock.Mock(return_value={'executed': False})
        with mock.patch.object(sys, 'argv', ['daos.py', 'plan', '--profile', 'rpi4-aarch64-ui']), \
             mock.patch.object(daos, 'module', return_value=type('Backend', (), {'plan': plan})), \
             contextlib.redirect_stdout(io.StringIO()):
            daos.main()
        self.assertEqual(plan.call_args.args[1], daos.DEFAULT_WORKSPACE)

    def test_usb_all_calls_real_stages_in_order_without_implicit_base_rebuild(self):
        work = self.root / 'workspace'
        provider = mock.Mock()
        provider._workspace.side_effect = Path
        for name in ('configure', 'build_base', 'build_usb', 'packages', 'image'):
            getattr(provider, name).return_value = {'operation': name, 'executed': name not in ('configure', 'build_base')}
        with mock.patch.object(sys, 'argv', ['daos.py', 'all', '--workspace', str(work)]), \
             mock.patch.object(daos, 'module', return_value=provider), \
             mock.patch.object(daos, 'doctor', return_value={'status': 'success'}), \
             contextlib.redirect_stdout(io.StringIO()):
            daos.main()
        stages = [call[0] for call in provider.mock_calls if call[0] != '_workspace']
        self.assertEqual(stages, ['configure', 'build_base', 'build_usb', 'packages', 'image'])
        self.assertFalse(provider.configure.call_args.kwargs['rebuild_base'])
        self.assertFalse(provider.build_base.call_args.kwargs['rebuild_base'])

    def test_usb_explicit_rebuild_requires_configured_policy(self):
        provider = mock.Mock()
        provider._workspace.side_effect = Path
        provider.build_base.return_value = {'executed': True}
        with mock.patch.object(sys, 'argv', ['daos.py', 'build', '--stage', 'base', '--rebuild-base',
                                           '--workspace', str(self.root / 'workspace')]), \
             mock.patch.object(daos, 'module', return_value=provider), \
             mock.patch.object(daos, 'doctor', return_value={'status': 'success'}), \
             contextlib.redirect_stdout(io.StringIO()):
            daos.main()
        self.assertTrue(provider.build_base.call_args.kwargs['rebuild_base'])
        provider.build_usb.assert_not_called()
        provider.image.assert_not_called()


if __name__ == '__main__':
    unittest.main()

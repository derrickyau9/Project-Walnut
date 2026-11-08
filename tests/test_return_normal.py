"""Return-to-normal FAT marker transaction using a private fake filesystem."""
from contextlib import nullcontext,redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

SOURCE=Path(__file__).resolve().parents[1]/'system/adb/recovery/return-normal.py'
spec=importlib.util.spec_from_file_location('return_normal',SOURCE)
normal=importlib.util.module_from_spec(spec);spec.loader.exec_module(normal)


class ReturnNormalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-return-normal-qa-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mount=self.root/'mount';self.saved=self.root/'fat-saved'
        self.config=self.root/'identity.json';self.config.write_text(json.dumps({'disk':'/dev/mmcsd0','disk_uuid':'disk-id','disk_bytes':1048576,'sector_size':512}))
        self.disk=self.root/'disk';self.disk.write_bytes(b'GPT fixture')
        self.mounted=False;self.marker=b'\1';self.alias_mount=False;self.bad_efi=False;self.calls=[]
        backend=SimpleNamespace(prove=Mock(),work=self.root/'work')
        self.backend=backend
        self.flash=SimpleNamespace(NativeBackend=lambda:backend,secure_path=lambda p:p,IDENTITY=self.config,
            lock=lambda _:nullcontext(),parse_gpt=self.gpt,command=self.command)
    def gpt(self,*args):return 'disk-id',{1:{'type':'wrong' if self.bad_efi else normal.EFI_GUID,'label':'DaOS-efi','uuid':'efi-id'}}
    def command(self,args):
        self.calls.append(args)
        if args[:3]==['/sbin/geom','label','status']:return 'gpt/DaOS-efi N/A mmcsd0p1\n'
        if args[:2]==['/sbin/mount','-p']:
            result='/dev/md0 / ufs ro 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'
            if self.alias_mount:result+='/dev/gpt/DaOS-efi /other msdosfs rw 0 0\n'
            if self.mounted:result+='/dev/mmcsd0p1 '+str(self.mount)+' msdosfs rw 0 0\n'
            return result
        if args[:2]==['/rescue/rescue','mount_msdosfs']:
            self.mounted=True
            if self.saved.exists():
                for item in self.saved.iterdir():shutil.move(str(item),str(self.mount/item.name))
                self.saved.rmdir()
            else:
                (self.mount/'DaOS-recovery').mkdir()
                if self.marker is not None:(self.mount/'DaOS-recovery/normal.running').write_bytes(self.marker)
                (self.mount/'unchanged-firmware').write_bytes(b'unchanged')
            return ''
        if args[:2]==['/rescue/rescue','umount']:
            self.mounted=False;self.saved.mkdir()
            for item in self.mount.iterdir():shutil.move(str(item),str(self.saved/item.name))
            return ''
        raise AssertionError(args)
    def run_transaction(self):
        actual_open=os.open
        def opened(path,flags,*args):return actual_open(self.disk if str(path)=='/dev/mmcsd0' else path,flags,*args)
        with patch.object(normal,'MOUNT',self.mount),patch.object(normal.os,'open',side_effect=opened),patch.object(normal.os,'sync',create=True),patch.object(normal.os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0),create=True):
            return normal.select_normal(self.flash)

    def test_clears_only_one_existing_byte_then_fsync_unmounts_without_rebooting_in_helper(self):
        result=self.run_transaction()
        self.assertEqual(result['status'],'normal-boot-selected');self.assertEqual(result['marker_bytes_written'],1)
        self.assertEqual((self.saved/'DaOS-recovery/normal.running').read_bytes(),b'\0')
        self.assertEqual((self.saved/'unchanged-firmware').read_bytes(),b'unchanged')
        self.assertFalse(self.mounted);self.assertFalse(self.mount.exists());self.assertEqual(self.backend.prove.call_count,3)

    def test_one_shot_without_crash_marker_returns_normal_without_rw_mount_or_file_write(self):
        self.marker=None
        with patch.object(normal.os,'write',side_effect=AssertionError('No marker must mean no write')):
            result=self.run_transaction()
        self.assertEqual(result['marker_bytes_written'],0);self.assertTrue(result['marker_absent'])
        self.assertTrue(result['reboot_requested']);self.assertFalse(self.mounted)
        self.assertFalse((self.saved/'DaOS-recovery/normal.running').exists())
        mounts=[call for call in self.calls if call[:2]==['/rescue/rescue','mount_msdosfs']]
        self.assertEqual(len(mounts),1);self.assertIn('ro',mounts[0])

    def test_native_msdos_mount_uses_separate_supported_ro_and_rw_options(self):
        self.run_transaction()
        mounts=[call for call in self.calls if call[:2]==['/rescue/rescue','mount_msdosfs']]
        self.assertEqual([call[call.index('-o')+1] for call in mounts],['ro','rw'])

    def test_mount_failure_preserves_captured_native_error_output(self):
        failure=subprocess.CalledProcessError(1,['mount_msdosfs'],output=b'mount option <ro,noatime> is unknown: Invalid argument')
        self.flash.command=Mock(side_effect=failure)
        with self.assertRaisesRegex(RuntimeError,'mount option .* is unknown'):
            normal.command(self.flash,['/rescue/rescue','mount_msdosfs','-o','ro'])

    def test_non_ram_environment_is_rejected_before_any_disk_or_mount_operation(self):
        self.backend.prove.side_effect=ValueError('not RAM root')
        with self.assertRaisesRegex(ValueError,'not RAM'):self.run_transaction()
        self.assertFalse(self.calls)

    def test_efi_wrong_identity_or_preexisting_alias_mount_refuses_writes(self):
        for mode in ('identity','mounted'):
            self.bad_efi=mode=='identity';self.alias_mount=mode=='mounted'
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.run_transaction()
            self.assertFalse(self.mount.exists());self.assertFalse(self.mounted)

    def test_missing_size_or_invalid_marker_cannot_clear_arbitrary_fat_file(self):
        for data in (b'',b'\1\1',b'x'):
            self.marker=data
            if self.saved.exists():shutil.rmtree(self.saved)
            with self.subTest(data=data),self.assertRaisesRegex(ValueError,'one-byte'):self.run_transaction()
            self.assertEqual((self.saved/'DaOS-recovery/normal.running').read_bytes(),data)
            self.assertFalse(self.mounted)

    def test_main_reboots_once_only_after_flushed_selection_receipt(self):
        output=io.StringIO();receipt={'status':'normal-boot-selected','marker_bytes_written':1,'reboot_requested':True}
        with patch.object(normal,'load_flash',return_value=self.flash),patch.object(normal,'select_normal',return_value=receipt),patch.object(normal,'check_quick_reboot') as gate,patch.object(normal.subprocess,'Popen',return_value=SimpleNamespace(pid=4321)) as spawn,redirect_stdout(output):normal.main()
        gate.assert_called_once_with(self.flash);spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0],['/rescue/sh','-c','/rescue/sleep 1; exec /rescue/reboot -qn'])
        self.assertTrue(spawn.call_args.kwargs['start_new_session']);self.assertTrue(spawn.call_args.kwargs['close_fds'])
        self.assertEqual(spawn.call_args.kwargs['stdout'],subprocess.DEVNULL);self.assertEqual(spawn.call_args.kwargs['stderr'],subprocess.DEVNULL)
        result=json.loads(output.getvalue());self.assertEqual(result['reboot_helper_pid'],4321);self.assertEqual(result['reboot_mode'],'detached-quick-protected-RAM')

    def test_quick_reboot_allows_readonly_physical_mounts_but_rejects_writable(self):
        for options,allowed in (('ro',True),('rw',False),('ro,rw',False)):
            table='/dev/md0 / ufs ro 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n/dev/mmcsd0p5 /photos ufs '+options+' 0 0\n'
            def command(flash,args):return '0\n' if args[-1]=='vm.swap_total' else table
            with self.subTest(options=options),patch.object(normal,'command',side_effect=command):
                if allowed:normal.check_quick_reboot(self.flash)
                else:
                    with self.assertRaises(ValueError):normal.check_quick_reboot(self.flash)

    def test_quick_reboot_gate_rejects_swap_and_never_schedules_helper_on_failure(self):
        with patch.object(normal,'command',side_effect=lambda flash,args:'4096\n' if args[-1]=='vm.swap_total' else '/dev/md0 / ufs ro 0 0\n'):
            with self.assertRaisesRegex(ValueError,'swap'):normal.check_quick_reboot(self.flash)
        with patch.object(normal,'load_flash',return_value=self.flash),patch.object(normal,'select_normal',return_value={}),patch.object(normal,'check_quick_reboot',side_effect=ValueError('writable physical FS')),patch.object(normal.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):normal.main()
        spawn.assert_not_called()


if __name__=='__main__':unittest.main()

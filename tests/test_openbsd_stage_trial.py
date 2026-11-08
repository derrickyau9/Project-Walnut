"""Private ordinary-file transaction QA; never constructs Native devices."""
from contextlib import contextmanager
import hashlib
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('newbsd_stage',ROOT/'system/openbsd/raspberrypi/stage_trial_from_ram.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)

class Sandbox:
    def __init__(self,base):
        self.base=base;self.work=base/'ram';self.work.mkdir(mode=0o700)
        self.efi=base/'efi';self.root=base/'root';self.efi.mkdir(mode=0o700);self.root.mkdir(mode=0o700)
        self.inputs=base/'inputs';self.inputs.mkdir(mode=0o700)
        self.mounts={};self.events=[];self.fail_event=None;self.failed=False;self.free=64*1024**2
        self.armed_wd=False;self.boot='sandbox-boot-1';self.reboot_count=0;self.write_partial=False;self.tamper_event=None;self.partial_bytes=0
    def guard(self,**kwargs):
        if self.mounts:raise ValueError('physical mount remains')
        if self.armed_wd:raise ValueError('watchdog armed')
        self.events.append('guard');return {'ram_md0':True,'partitions_checked':5,'watchdog_GET':0}
    def boot_id(self):return self.boot
    def input_guard(self,path):
        if path!=self.inputs:raise ValueError('inputs outside owned RAM fixture')
    def available(self,path):return self.free
    @contextmanager
    def mounted(self,role):
        if self.mounts:raise ValueError('two physical mounts')
        self.mounts[role]='ro';self.events.append('mount-ro:'+role)
        try:yield self.efi if role=='efi' else self.root
        finally:self.events.extend(['restore-ro:'+role,'unmount:'+role]);self.mounts.clear()
    def writable(self,role,path):
        if self.mounts!={role:'ro'}:raise ValueError('unexpected mount ownership')
        self.mounts[role]='rw';self.events.append('writable:'+role)
    def checkpoint(self,event):
        self.events.append(event)
        if self.tamper_event==event:
            (self.efi/'DaOS-nextbsd/bsd.rd').write_bytes(b'UNKNOWN_TAMPER')
        if self.fail_event==event and not self.failed:
            self.failed=True;raise OSError('injected transaction boundary failure')
    def write(self,file,data):
        if self.write_partial and data.startswith(b'OPAQUE_PRIVATE_IMAGE'):
            self.write_partial=False;file.write(data[:7]);file.flush();self.partial_bytes=file.tell()
            raise OSError('partial private image write')
        if file.write(data)!=len(data):raise OSError('short sandbox write')
    def detach_reboot(self):self.reboot_count+=1
    def sync_directory(self,path):stage.syncdir(path)

class TrialTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(os,'geteuid') or os.geteuid()!=0:self.skipTest('POSIX uid0 private fixture required')
        self.temp=tempfile.TemporaryDirectory(prefix='daos-newbsd-private-test-');self.base=Path(self.temp.name)
        self.runtime=Sandbox(self.base);r=self.runtime
        self.payloads={'BOOTAA64.EFI':b'EFI-PRIVATE-FIXTURE','bsd.rd':b'OPAQUE_PRIVATE_IMAGE'+b'x'*300,'d':b'DTB-FIXTURE'}
        self.original=b'nextboot_enable="NO" \n# original disabled fixture\n'
        self.patches=[patch.object(stage,'PAYLOADS',{name:(len(data),stage.digest(data)) for name,data in self.payloads.items()}),
                      patch.object(stage,'NEXT_SHA',stage.digest(self.original)),patch.object(stage.os,'sync',create=True)]
        bindings={'DISK_UUID':'fixture-uuid','ROOT_PINS':{'/boot/kernel/kernel':stage.digest(b'kernel')},
                  'EFI_PINS':{'boot.scr':stage.digest(b'frozen-script'),'EFI/BOOT/BOOTAA64.EFI':stage.digest(b'frozen-primary')}}
        self.patches.append(patch.object(stage,'BINDINGS',bindings))
        for value in self.patches:value.start()
        for name,data in self.payloads.items():self.put(r.inputs/name,data)
        for name,data in {'boot.scr':b'frozen-script','EFI/BOOT/BOOTAA64.EFI':b'frozen-primary',
                          'DaOS-recovery/normal.running':b'\1'}.items():self.put(r.efi/name,data)
        for name,data in {'boot/kernel/kernel':b'kernel','boot/nextboot.conf':self.original,
                          'etc/daos/recovery-watchdog.enabled':stage.POLICY_DATA}.items():self.put(r.root/name,data)
        (r.root/'etc/daos/recovery-watchdog.enabled').chmod(0o444)
        (r.root/'boot/nextboot.conf').chmod(0o644)
        self.before=self.snapshot()
    def tearDown(self):
        for value in reversed(getattr(self,'patches',[])):value.stop()
        if hasattr(self,'temp'):self.temp.cleanup()
    def put(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(data);path.chmod(0o600)
    def snapshot(self):
        return {str(path.relative_to(self.base)):(path.read_bytes(),path.stat().st_mode&0o777)
                for directory in (self.runtime.efi,self.runtime.root) for path in directory.rglob('*') if path.is_file()}
    def apply(self):return stage.Transaction(self.runtime).apply(self.runtime.inputs)
    def assert_restored(self):
        self.assertEqual(self.before,self.snapshot());self.assertEqual({},self.runtime.mounts)
    def test_plan_has_no_physical_file_mutation_or_reboot(self):
        result=stage.Transaction(self.runtime).plan(self.runtime.inputs)[2]
        self.assertEqual('planned',result['status']);self.assert_restored();self.assertEqual(0,self.runtime.reboot_count)
    def test_apply_order_and_roundtrip_after_ram_receipt_is_lost(self):
        record=self.apply();self.assertEqual('armed',record['status']);r=self.runtime
        self.assertEqual(stage.SELECTOR,(r.root/'boot/nextboot.conf').read_bytes())
        self.assertEqual(b'\0',(r.efi/'DaOS-recovery/normal.running').read_bytes())
        self.assertLess(r.events.index('fat-payloads-verified'),r.events.index('before-selector-publication'))
        self.assertLess(r.events.index('selector-published'),r.events.index('marker-cleared'))
        self.assertEqual(0,r.reboot_count)
        (r.work/'receipt.json').unlink()
        result=stage.Transaction(r).rollback()
        self.assertEqual('rolled-back-marker-one',result['status']);self.assert_restored()
    def test_consumed_exact_no_body_can_be_rolled_back(self):
        self.apply();(self.runtime.root/'boot/nextboot.conf').write_bytes(stage.CONSUMED)
        self.assertEqual('rolled-back-marker-one',stage.Transaction(self.runtime).rollback()['status']);self.assert_restored()
    def test_reboot_is_separate_and_requires_same_boot_complete_receipt(self):
        self.apply();self.assertEqual(0,self.runtime.reboot_count)
        result=stage.Transaction(self.runtime).reboot()
        self.assertTrue(result['reboot_requested']);self.assertEqual(1,self.runtime.reboot_count)
    def test_changed_boot_cannot_reuse_reboot_receipt(self):
        self.apply();self.runtime.boot='different-boot'
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).reboot()
        self.assertEqual(0,self.runtime.reboot_count)
    def test_watchdog_armed_refuses_before_mount_or_writes(self):
        self.runtime.armed_wd=True
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertEqual([],self.runtime.events);self.assert_restored()
    def test_bad_payload_hash_refuses_before_any_physical_mount(self):
        (self.runtime.inputs/'bsd.rd').write_bytes(b'changed')
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertEqual(['guard'],self.runtime.events);self.assert_restored()
    def test_changed_bytes_during_input_read_refuse_before_any_mount(self):
        original=Path.read_bytes;target=self.runtime.inputs/'bsd.rd'
        def read(path):return b'CHANGED_DURING_READ' if path==target else original(path)
        with patch.object(Path,'read_bytes',read):
            with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertEqual(['guard'],self.runtime.events);self.assert_restored()
    def test_insufficient_fat_space_refuses_without_writable_mount(self):
        self.runtime.free=1
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertFalse(any(e.startswith('writable') for e in self.runtime.events));self.assert_restored()
    def test_existing_foreign_namespace_is_not_overwritten(self):
        self.put(self.runtime.efi/'etc/boot.conf',b'foreign')
        expected=self.snapshot()
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertEqual(expected,self.snapshot())
    def test_root_pin_change_is_refused_before_mutation(self):
        (self.runtime.root/'boot/kernel/kernel').write_bytes(b'other')
        expected=self.snapshot()
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).plan(self.runtime.inputs)
        self.assertEqual(expected,self.snapshot())
    def test_partial_write_with_owned_unchanged_prefix_is_removed(self):
        self.runtime.write_partial=True
        record=self.apply();self.assertEqual('rolled-back-marker-one',record['status']);self.assertEqual(7,self.runtime.partial_bytes);self.assert_restored()
    def test_failure_after_fat_verification_rolls_back(self):
        self.runtime.fail_event='fat-payloads-verified'
        self.assertEqual('rolled-back-marker-one',self.apply()['status']);self.assert_restored()
    def test_failure_before_selector_publication_rolls_back(self):
        self.runtime.fail_event='before-selector-publication'
        self.assertEqual('rolled-back-marker-one',self.apply()['status']);self.assert_restored()
    def test_failure_after_selector_publication_rolls_back(self):
        self.runtime.fail_event='selector-published'
        self.assertEqual('rolled-back-marker-one',self.apply()['status']);self.assert_restored()
    def test_failure_after_marker_zero_restores_marker_one_and_selector(self):
        self.runtime.fail_event='marker-cleared'
        self.assertEqual('rolled-back-marker-one',self.apply()['status']);self.assert_restored()
    def test_unknown_payload_tamper_is_retained_with_incomplete_status(self):
        self.runtime.tamper_event='selector-published';self.runtime.fail_event='selector-published'
        record=self.apply();self.assertEqual('rollback-incomplete',record['status'])
        self.assertEqual(b'UNKNOWN_TAMPER',(self.runtime.efi/'DaOS-nextbsd/bsd.rd').read_bytes())
        self.assertEqual(self.original,(self.runtime.root/'boot/nextboot.conf').read_bytes())
        self.assertEqual(b'\1',(self.runtime.efi/'DaOS-recovery/normal.running').read_bytes())
    def test_unknown_selector_is_retained_but_marker_one_is_restored(self):
        self.apply();path=self.runtime.root/'boot/nextboot.conf';path.write_bytes(b'FOREIGN')
        self.assertEqual('rollback-incomplete',stage.Transaction(self.runtime).rollback()['status'])
        self.assertEqual(b'FOREIGN',path.read_bytes())
        self.assertEqual(b'\1',(self.runtime.efi/'DaOS-recovery/normal.running').read_bytes())
        self.assertEqual({},self.runtime.mounts)
    def test_persistent_backup_tamper_refuses_without_deleting_new_payloads(self):
        self.apply();self.put(self.runtime.root/'boot/DaOS-nextbsd/nextboot.original',b'FOREIGN')
        self.assertEqual('rollback-incomplete',stage.Transaction(self.runtime).rollback()['status'])
        self.assertTrue((self.runtime.efi/'DaOS-nextbsd/bsd.rd').exists())
        self.assertEqual(b'\1',(self.runtime.efi/'DaOS-recovery/normal.running').read_bytes())
    def test_reboot_rechecks_staged_payload_hash(self):
        self.apply();self.put(self.runtime.efi/'DaOS-nextbsd/bsd.rd',b'FOREIGN')
        with self.assertRaises(ValueError):stage.Transaction(self.runtime).reboot()
        self.assertEqual(0,self.runtime.reboot_count)
    def test_receipts_never_contain_private_image_bytes(self):
        self.apply()
        for path in [self.runtime.work/'receipt.json',self.runtime.root/'boot/DaOS-nextbsd/transaction.json']:
            self.assertNotIn(b'OPAQUE_PRIVATE_IMAGE',path.read_bytes())
        self.assertNotIn(b'OPAQUE_PRIVATE_IMAGE',stage.lua_source())

class NativePrimitiveTests(unittest.TestCase):
    def test_fat_initial_and_update_use_resident_crunched_applet_without_external_helpers(self):
        native=object.__new__(stage.Native);path=Path('/owned/efi');calls=[]
        native.command=lambda args:calls.append(list(map(str,args)))
        native.check_mounts=lambda *args:None
        initial=list(map(str,native.fat_mount_command(path,readonly=True)))
        self.assertEqual(['/rescue/rescue','mount_msdosfs'],initial[:2])
        native.writable('efi',path);native.readonly('efi',path)
        for command,mode in zip(calls,('noro','ro')):
            self.assertEqual(['/rescue/rescue','mount_msdosfs','-o','update','-o',mode],command[:6])
            self.assertEqual(['-u','0','-g','0','-m','0700','-M','0700',stage.BINDINGS['DISK']+'p1',str(path)],command[6:])
            self.assertTrue(all(',' not in argument for argument in command))
    def test_UFS_update_keeps_builtin_generic_mount_path(self):
        native=object.__new__(stage.Native);path=Path('/owned/root');calls=[]
        native.command=lambda args:calls.append(list(map(str,args)));native.check_mounts=lambda *args:None
        native.writable('root',path);native.readonly('root',path)
        self.assertEqual([['/rescue/mount','-u','-w',str(path)],['/rescue/mount','-u','-r',str(path)]],calls)
    def test_actual_kernel_disk_inventory_omits_md0(self):
        for value in ('mmcsd0','md0 mmcsd0','mmcsd0 md0'):
            stage.check_disk_inventory(value)
        for value in ('','md0','mmcsd0 da0','mmcsd0 md1','mmcsd0 mmcsd0','mmcsd0 sdda0'):
            with self.subTest(value=value),self.assertRaises(ValueError):stage.check_disk_inventory(value)
    def test_swap_nonzero_or_malformed_refuses_before_backend_or_mount(self):
        for value in ('1','4096','nan','','0x0','0 0','0\n1'):
            with self.subTest(value=value):
                native=object.__new__(stage.Native)
                native.command=lambda args:value
                class Backend:
                    def prove(self,role):raise AssertionError('provider proof must not run with swap')
                native.backend=Backend()
                with self.assertRaisesRegex(ValueError,'swap'):native.guard()
    def test_fat_directory_einval_is_scoped_and_other_errors_propagate(self):
        native=object.__new__(stage.Native);native.owned_mount=('efi',Path('/owned/efi'))
        seen=[];native.check_mounts=lambda *args:seen.append(args)
        with patch.object(stage,'syncdir',side_effect=OSError(errno.EINVAL,'directory unsupported')),patch.object(stage.os,'sync',create=True) as sync:
            native.sync_directory(Path('/owned/efi/DaOS-nextbsd'));self.assertEqual(1,sync.call_count)
            with self.assertRaises(OSError):native.sync_directory(Path('/owned/root/boot'))
        with patch.object(stage,'syncdir',side_effect=OSError(errno.EIO,'media failure')),patch.object(stage.os,'sync',create=True) as sync:
            with self.assertRaises(OSError):native.sync_directory(Path('/owned/efi/etc'))
            self.assertEqual(0,sync.call_count)
        self.assertEqual(('efi',Path('/owned/efi'),'rw'),seen[0])
    def test_mount_command_failure_after_real_mount_still_cleans(self):
        if not hasattr(os,'geteuid') or os.geteuid()!=0:self.skipTest('POSIX root fixture')
        with tempfile.TemporaryDirectory(prefix='daos-native-mount-mock-') as folder:
            native=object.__new__(stage.Native);native.work=Path(folder);native.owned_mount=None
            state={'mounted':False};events=[]
            native.rows=lambda:[['/dev/md0','/','ufs','rw','0','0']]+([['/dev/mmcsd0p1',str(native.work/'efi'),'msdosfs','ro','0','0']] if state['mounted'] else [])
            def command(args):
                if args[0]=='/rescue/rescue':
                    if 'update' in args:events.append('restore-ro');return ''
                    state['mounted']=True;events.append('mount-completed-then-error');raise OSError('transport failed after mount')
                if args[0]=='/rescue/mount':events.append('restore-ro');return ''
                if args[0]=='/rescue/umount':state['mounted']=False;events.append('unmount');return ''
                raise AssertionError(args)
            native.command=command
            with patch.object(stage,'BINDINGS',{'DISK':'/dev/mmcsd0'}):
                with self.assertRaises(OSError):
                    with native.mounted('efi'):self.fail('must not yield after failed mount command')
            self.assertFalse(state['mounted']);self.assertEqual(['mount-completed-then-error','restore-ro','unmount'],events)
    def test_duplicate_devfs_is_allowed_but_foreign_filesystem_refused(self):
        native=object.__new__(stage.Native)
        rows=[['/dev/md0','/','ufs','rw','0','0'],['devfs','/dev','devfs','rw','0','0'],['devfs','/dev','devfs','rw','0','0']]
        native.rows=lambda:rows
        native.check_mounts()
        rows.append(['/dev/mmcsd0p5','/photos','ufs','ro','0','0'])
        with self.assertRaises(ValueError):native.check_mounts()

if __name__=='__main__':unittest.main()

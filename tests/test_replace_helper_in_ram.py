"""Offline maintenance transactions on private files; native gates mocked."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'system/adb/recovery/replace_helper_in_ram.py'
spec=importlib.util.spec_from_file_location('replace_helper_ram',SOURCE)
ram=importlib.util.module_from_spec(spec);spec.loader.exec_module(ram)


def elf():
    data=bytearray(120);data[:8]=b'\x7fELF\x02\x01\x01\x09';struct.pack_into('<HHI',data,16,2,183,1)
    struct.pack_into('<Q',data,32,64);struct.pack_into('<HH',data,54,56,1);struct.pack_into('<I',data,64,1)
    return bytes(data)


class OfflineHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.work=self.base/'ram';(self.work/'incoming').mkdir(parents=True)
        self.candidate=self.work/'incoming/candidate.bin';self.candidate.write_bytes(elf())
        self.before=hashlib.sha256(b'old helper').hexdigest();self.after=hashlib.sha256(elf()).hexdigest()
        self.events=[];owner=self
        @contextmanager
        def lock(path):
            owner.events.append('lock')
            try:yield
            finally:owner.events.append('unlock')
        class Runtime:
            flash=SimpleNamespace(WORK=owner.work,secure_path=lambda p:Path(p),lock=lock)
            mounted=False
            def prove(self):
                owner.events.append('prove')
                if self.mounted:raise ValueError('Physical root still mounted')
                return {'path':Path('/dev/private-p2'),'identity':{'uuid':'fixed'}}
            def mount(self,provider,path):
                owner.events.append('mount-ro');self.mounted=True;self.path=path
                for name,data in {ram.HELPER:b'old helper','boot/kernel/kernel':b'kernel','sbin/init':b'init'}.items():
                    p=path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o755)
                owner.target=path/ram.HELPER
            def writable(self,provider,path):owner.events.append('mount-rw')
            def readonly(self,provider,path):owner.events.append('mount-ro-final')
            def unmount(self,path):owner.events.append('unmount');self.mounted=False
        self.runtime=Runtime()
        self.original_atomic=ram.atomic
        self.patches=[patch.object(ram,'KERNEL',hashlib.sha256(b'kernel').hexdigest()),
                      patch.object(ram,'INIT',hashlib.sha256(b'init').hexdigest()),
                      patch.object(ram,'unredirected',side_effect=lambda p:Path(p)),
                      patch.object(ram,'atomic',side_effect=self.write)]
        if os.name=='nt':self.patches.append(patch.object(ram.stat,'S_IMODE',return_value=0o755))
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def write(self,path,data,mode,token):path.write_bytes(data);path.chmod(mode)
    def apply(self):return ram.maintain(self.candidate,self.before,self.after,self.runtime)

    def test_offline_success_has_lock_and_both_backups_before_write_then_ro_unmount_postprove(self):
        result=self.apply()
        self.assertEqual(result['status'],'helper-replaced-offline')
        self.assertTrue(result['root_unmounted']);self.assertTrue(result['post_native_proof'])
        self.assertEqual(self.target.read_bytes(),elf())
        self.assertEqual(Path(result['ram_backup']).read_bytes(),b'old helper')
        self.assertEqual((self.runtime.path/result['root_backup_relative']).read_bytes(),b'old helper')
        self.assertLess(self.events.index('lock'),self.events.index('mount-ro'))
        self.assertEqual(self.events[-3:],['unmount','prove','unlock'])

    def test_wrong_candidate_refuses_before_lock_or_physical_mount(self):
        self.candidate.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.events,['prove'])

    def test_old_helper_pin_refuses_before_rw_and_always_unmounts(self):
        self.before='a'*64
        result=self.apply()
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('mount-rw',self.events);self.assertTrue(result['root_unmounted'])
        self.assertTrue(result['post_native_proof'])

    def test_failed_rw_remount_still_readonly_unmount_and_postproof(self):
        def failed(provider,path):self.events.append('mount-rw');raise OSError('injected remount error')
        self.runtime.writable=failed
        result=self.apply()
        self.assertEqual(self.target.read_bytes(),b'old helper')
        self.assertTrue(result['root_unmounted']);self.assertIn('mount-ro-final',self.events)

    def test_post_rename_failure_restores_old_before_unmount(self):
        def write(path,data,mode,token):
            self.write(path,data,mode,token)
            if path.as_posix().endswith(ram.HELPER) and data==elf():raise OSError('injected fsync failure after rename')
        with patch.object(ram,'atomic',side_effect=write):result=self.apply()
        self.assertEqual(result['status'],'failed-rolled-back')
        self.assertEqual(self.target.read_bytes(),b'old helper');self.assertTrue(result['root_unmounted'])

    def test_root_ro_failure_triggers_old_restore_and_second_ro_before_unmount(self):
        count=0
        def readonly(provider,path):
            nonlocal count
            count+=1
            if count==1:raise OSError('injected RO failure')
        self.runtime.readonly=readonly
        result=self.apply()
        self.assertEqual(count,2);self.assertEqual(self.target.read_bytes(),b'old helper')
        self.assertTrue(result['root_unmounted']);self.assertEqual(result['status'],'failed-rolled-back')

    def test_isolation_rejects_state_photos_or_foreign_filesystem(self):
        real=ram.Runtime(None,None)
        base='/dev/md0 / ufs ro 0 0\ndevfs /dev devfs rw 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'
        with patch.object(ram,'command',return_value=base):real.check_mounts()
        for extra in ('/dev/mmcsd0p3 /var ufs rw 0 0\n','/dev/mmcsd0p5 /photos ufs ro 0 0\n','/dev/da1p1 /other msdosfs ro 0 0\n'):
            with self.subTest(extra=extra),patch.object(ram,'command',return_value=base+extra),self.assertRaises(ValueError):real.check_mounts()

    def test_mount_validation_failure_attempts_owned_unmount(self):
        real=ram.Runtime(None,None);calls=[]
        with patch.object(ram,'command',side_effect=lambda args:calls.append(args) or ''),patch.object(real,'check_mounts',side_effect=ValueError('bad mounted source')):
            with self.assertRaises(ValueError):real.mount(Path('/dev/private-p2'),Path('/private-owned-root'))
        self.assertEqual(calls[-1],['/rescue/umount',str(Path('/private-owned-root'))])

    def test_successful_mount_then_validation_error_still_has_final_cleanup(self):
        def mount(provider,path):
            self.runtime.mounted=True
            raise ValueError('injected verification failure after physical mount')
        self.runtime.mount=mount
        result=self.apply()
        self.assertIn('unmount',self.events);self.assertIn('mount-ro-final',self.events)
        self.assertTrue(result['root_unmounted']);self.assertTrue(result['post_native_proof'])

    def test_failed_mount_command_with_changed_state_retains_cleanup_ownership(self):
        real=ram.Runtime(None,None);mount=Path('/private-owned-root');calls=[]
        def command(args):
            calls.append(args)
            if args==['/rescue/mount','-p']:return '/dev/private-p2 '+str(mount)+' ufs ro 0 0\n'
            raise OSError('injected command/first cleanup failure')
        with patch.object(ram,'command',side_effect=command):
            with self.assertRaises(OSError):real.mount(Path('/dev/private-p2'),mount)
        self.assertTrue(real.mounted)


if __name__=='__main__':unittest.main()

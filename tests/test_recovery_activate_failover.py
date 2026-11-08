"""Optional rescue activation exercises only private root/FAT file trees."""
import hashlib
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'system/adb/recovery'
sys.path.insert(0,str(SOURCE))
try:
    spec=importlib.util.spec_from_file_location('activate_failover',SOURCE/'activate_failover.py')
    activate=importlib.util.module_from_spec(spec);spec.loader.exec_module(activate)
finally:sys.path.pop(0)


class FailoverActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.root=self.base/'root';self.efi=self.base/'efi';self.bundle=self.base/'bundle';self.work=self.base/'work'
        self.root.mkdir();self.efi.mkdir();self.bundle.mkdir()
        for name,data in {'boot/kernel/kernel':b'stable kernel','boot/nextboot.conf':b'nextboot_enable="NO"\n',
            'boot/loader.conf.local':b'init_script="/usr/local/libexec/DaOS-recovery/early-init.sh"\n',
            'usr/local/libexec/DaOS-recovery/early-init.sh':b'early hook',
            'usr/local/libexec/DaOS-recovery/kernel-watchdog':b'static watchdog',
            'etc/rc.d/DaOS-rescue-clean':b'original clean script'}.items():self.put(self.root,name,data)
        self.put(self.efi,'EFI/BOOT/BOOTAA64.EFI',b'original EFI')
        self.put(self.efi,'config.txt',b'original firmware config')
        self.put(self.efi,'boot.scr',b'original selector')
        for name in activate.REQUIRED:self.put(self.bundle,name,('payload '+name).encode())
        identity={'format':'DaOS-recovery-identity-v1','disk':'/dev/mmcsd0','disk_uuid':'01234567-1234-1234-1234-0123456789ab','disk_bytes':99999,'sector_size':512,'partitions':{}}
        self.expected=activate.create_manifest(self.bundle,identity,activate.sha(self.efi/'EFI/BOOT/BOOTAA64.EFI'),activate.sha(self.efi/'config.txt'),activate.sha(self.root/'boot/kernel/kernel'))
        self.original=self.snapshot()
    def tearDown(self):self.tmp.cleanup()
    def put(self,base,name,data):
        p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o644)
    def snapshot(self):
        return {(area,p.relative_to(base).as_posix()):p.read_bytes() for area,base in [('root',self.root),('efi',self.efi)] for p in base.rglob('*') if p.is_file()}
    def apply(self,**kwargs):return activate.apply(self.root,self.efi,self.bundle,self.expected,self.work,**kwargs)

    def test_plan_has_no_writes_and_selector_is_last(self):
        r=activate.make_plan(self.root,self.efi,self.bundle,self.expected)
        self.assertEqual(self.snapshot(),self.original);self.assertFalse(self.work.exists())
        self.assertEqual((r['files'][-1]['area'],r['files'][-1]['path']),('efi','boot.scr'))
        self.assertFalse(r['otp_modified']);self.assertFalse(r['raw_disk_writes'])
        self.assertNotIn('gpio input',activate.boot_config.read_script_image(bytes.fromhex(r['files'][-1]['data_hex'])))

    @unittest.skipUnless(os.name=='posix','Transaction readback requires real POSIX executable modes')
    def test_apply_readback_and_rollback_restore_original_payloads_and_configuration(self):
        r=self.apply();self.assertEqual(r['status'],'installed-reboot-required')
        self.assertEqual((self.efi/'config.txt').read_bytes(),b'original firmware config')
        self.assertEqual((self.efi/'EFI/BOOT/BOOTAA64.EFI').read_bytes(),b'original EFI')
        self.assertEqual((self.efi/activate.MARKER).read_bytes(),b'\0')
        self.assertFalse((self.root/activate.POLICY).exists())
        activate.rollback(self.root,self.efi,self.work)
        self.assertEqual(self.snapshot(),self.original)

    @unittest.skipUnless(os.name=='posix','Transaction readback requires real POSIX executable and read-only modes')
    def test_watchdog_policy_only_published_after_selector_and_read_only(self):
        paths=[]
        def writer(path,data,mode):paths.append(path);activate.early.atomic(path,data,mode)
        self.apply(enable_watchdog=True,writer=writer)
        self.assertEqual(paths[-2:], [self.efi/'boot.scr',self.root/activate.POLICY])
        self.assertEqual((self.root/activate.POLICY).read_bytes(),activate.POLICY_DATA)
        self.assertEqual(stat.S_IMODE((self.root/activate.POLICY).stat().st_mode),0o444)

    def test_stage_failure_before_selector_restores_every_original_file(self):
        def writer(path,data,mode):
            if path==self.root/activate.CLEAN:raise OSError('Injected clean-hook write failure')
            activate.early.atomic(path,data,mode)
        with self.assertRaises(OSError):self.apply(writer=writer)
        self.assertEqual(self.snapshot(),self.original)
        self.assertEqual(json.loads((self.work/'deployment.json').read_text())['status'],'failed-rolled-back')

    @unittest.skipUnless(os.name=='posix','Reaching selector publication requires real POSIX executable modes')
    def test_failure_after_selector_publication_restores_selector_before_cleanup(self):
        restores=[];original=activate.early.atomic;original_fat=activate.fat_atomic;failed=False
        def record(path,data,*args,**kwargs):
            if failed and path==self.root/activate.CLEAN and data==b'original clean script':restores.append('clean')
            return original(path,data,*args,**kwargs)
        def record_fat(path,data,*args,**kwargs):
            if failed and path==self.efi/'boot.scr' and data==b'original selector':restores.append('selector')
            return original_fat(path,data,*args,**kwargs)
        def fail(path,data,mode):
            nonlocal failed
            original(path,data,mode)
            if path==self.efi/'boot.scr':failed=True;raise OSError('Fault after selector rename')
        with patch.object(activate.early,'atomic',side_effect=record),patch.object(activate,'fat_atomic',side_effect=record_fat):
            with self.assertRaises(OSError):self.apply(writer=fail)
        self.assertEqual(restores[0],'selector');self.assertEqual(self.snapshot(),self.original)

    @unittest.skipUnless(os.name=='posix','Transaction readback requires real POSIX executable modes')
    def test_external_change_refuses_rollback_before_selector_change(self):
        self.apply();selector=(self.efi/'boot.scr').read_bytes()
        (self.efi/'DaOS-recovery/loader.efi').write_bytes(b'external replacement')
        with self.assertRaises(ValueError):activate.rollback(self.root,self.efi,self.work)
        self.assertEqual((self.efi/'boot.scr').read_bytes(),selector)

    def test_primary_config_kernel_mismatch_or_active_trial_refuses_before_work(self):
        for base,name in ((self.efi,'config.txt'),(self.efi,'EFI/BOOT/BOOTAA64.EFI'),(self.root,'boot/kernel/kernel'),(self.root,'boot/nextboot.conf')):
            p=base/name;old=p.read_bytes();p.write_bytes(b'changed')
            try:
                with self.subTest(name=name),self.assertRaises(ValueError):self.apply()
                self.assertFalse(self.work.exists())
            finally:p.write_bytes(old)

    def test_global_efi_environment_cannot_be_added_to_payload(self):
        p=self.bundle/activate.MANIFEST;m=json.loads(p.read_text());m['files'].append({'path':'EFI/freebsd/loader.env','source':'DaOS-recovery/loader.lua','mode':0o644,'sha256':'f'*64,'bytes':1});p.write_text(json.dumps(m))
        with self.assertRaises(ValueError):activate.load_bundle(self.bundle,activate.sha(p))

    @unittest.skipIf(os.name=='nt','POSIX directory fsync behavior')
    def test_fat_directory_fsync_fallback_requires_verified_msdos_mount(self):
        real=activate.os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError(errno.EINVAL,'directory fsync unsupported')
            return real(fd)
        for kind in ('ufs','msdosfs'):
            path=self.efi/('probe-'+kind)
            with patch.object(activate.os,'fsync',side_effect=fsync),patch.object(activate,'command',return_value='dev '+str(self.efi)+' '+kind+' rw 0 0'),patch.object(activate.os,'sync') as sync:
                if kind=='ufs':
                    with self.assertRaises(OSError):activate.fat_atomic(path,b'payload')
                    sync.assert_not_called()
                else:
                    activate.fat_atomic(path,b'payload');sync.assert_called_once()
            self.assertEqual(path.read_bytes(),b'payload')

    def test_file_fsync_error_is_never_ignored_by_fat_writer(self):
        path=self.efi/'fsync-failure';path.write_bytes(b'original')
        with patch.object(activate.os,'fsync',side_effect=OSError(errno.EIO,'injected file fsync error')):
            with self.assertRaises(OSError):activate.fat_atomic(path,b'changed')
        self.assertEqual(path.read_bytes(),b'original')

    def test_existing_efi_mount_is_rejected_for_direct_label_and_chained_aliases(self):
        labels='gpt/DaOS-efi N/A mmcsd0p1\ngptid/efi-uuid N/A mmcsd0p1\nlabel/boot-alias N/A gptid/efi-uuid\n'
        identity={'disk':'/dev/mmcsd0'}
        for source in ('/dev/mmcsd0p1','/dev/gpt/DaOS-efi','/dev/gptid/efi-uuid','/dev/label/boot-alias'):
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'already mounted'):
                activate.require_efi_unmounted(identity,labels,source+' /existing-efi msdosfs ro 0 0\n')

    def test_unrelated_efi_mount_and_normal_root_state_mounts_are_accepted(self):
        labels='gpt/DaOS-efi N/A mmcsd0p1\ngpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'
        rows='/dev/gpt/DaOS-root / ufs ro 1 1\n/dev/gpt/DaOS-state /var ufs rw 2 2\n/dev/da1p1 /other-usb msdosfs rw 0 0\n'
        activate.require_efi_unmounted({'disk':'/dev/mmcsd0'},labels,rows)

    def test_mount_guard_rejects_ambiguous_alias_cycle_and_malformed_tables(self):
        cases=[('gpt/DaOS-efi N/A mmcsd0p1\ngpt/DaOS-efi N/A da1p1\n',''),
               ('gpt/DaOS-efi N/A label/loop\nlabel/loop N/A gpt/DaOS-efi\n',''),
               ('gpt/DaOS-efi N/A mmcsd0p1\n','/dev/mmcsd0p1 /bad msdosfs rw\n'),
               ('gpt/DaOS-efi N/A da1p1\n','')]
        for labels,rows in cases:
            with self.subTest(labels=labels,rows=rows),self.assertRaises(ValueError):
                activate.require_efi_unmounted({'disk':'/dev/mmcsd0'},labels,rows)

    @unittest.skipIf(os.name=='nt','POSIX symlink target guard')
    def test_redirected_fat_destination_is_rejected(self):
        outside=self.base/'outside';outside.mkdir();(self.efi/'DaOS-recovery').symlink_to(outside)
        with self.assertRaises(ValueError):self.apply()
        self.assertFalse(self.work.exists())


if __name__=='__main__':unittest.main()

"""Private files and injected mount failures only; never opens watchdog/media."""
import copy
import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('enable_watchdog',Path(__file__).with_name('enable_recovery_watchdog.py'))
enable=importlib.util.module_from_spec(spec);spec.loader.exec_module(enable)


class Fake:
    def __init__(self,path):self.policy=path;self.events=[];self.writable=False;self.fail_efi=False;self.fail_verify=0;self.ro_failures=0;self.replacement=False
    def verify(self):
        self.events.append('verify')
        if self.policy.exists() or self.fail_verify==len([e for e in self.events if e=='verify']):raise ValueError('Policy present or guard changed')
        return {'partitions_checked':5}
    def inspect_efi(self):
        self.events+=['efi-ro','efi-unmounted']
        if self.fail_efi:raise ValueError('Stale FAT v2 image')
        return {'marker':1}
    def remount(self,writable):
        self.events.append('root-rw' if writable else 'root-ro')
        if not writable and self.ro_failures:
            self.ro_failures-=1
            if self.replacement:
                self.policy.unlink();self.policy.write_bytes(b'external')
            raise OSError('Injected final read-only remount failure')
        self.writable=writable
    def assert_root(self,writable):
        if self.writable!=writable:raise ValueError('Wrong root mode')
    def receipt(self,record):self.events.append('receipt');return 'private-metadata-receipt.json'


class Guards(unittest.TestCase):
    labels='gpt/DaOS-root N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\ngpt/DaOS-efi N/A mmcsd0p1'
    mounts='/dev/gpt/DaOS-root / ufs ro 1 1\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n/dev/gpt/DaOS-state /var ufs rw,noatime 2 2\n/var/photos/update-source /var/DaOS/updates nullfs rw 0 0'
    def kenv_function(self,values=None,error=None):
        def query(action,name,buffer,size):
            self.assertEqual(action,0);self.assertEqual(size,129)
            if error is not None:ctypes.set_errno(error);return -1
            value=(values or {}).get(name)
            if value is None:ctypes.set_errno(errno.ENOENT);return -1
            data=value+b'\0';ctypes.memmove(buffer,data,min(size,len(data)));return min(size,len(data))
        return query
    def test_kenv_absent_enoent_is_accepted_with_exact_present_init_hook(self):
        function=self.kenv_function({b'init_script':b'/usr/local/libexec/DaOS-recovery/early-init.sh'})
        self.assertIsNone(enable.kenv_get('daos_recovery',function))
        enable.normal_environment(lambda name:enable.kenv_get(name,function))
    def test_kenv_found_yes_is_refused_and_init_hook_must_match(self):
        for values in ({b'daos_recovery':b'YES'},{b'init_script':b'/unexpected/hook'},{}):
            function=self.kenv_function(values)
            with self.assertRaises(ValueError):enable.normal_environment(lambda name:enable.kenv_get(name,function))
    def test_kenv_permission_unsupported_and_unknown_errors_fail_closed(self):
        for error in (errno.EPERM,errno.ENOSYS,errno.EACCES,errno.EINVAL,0,9999):
            with self.subTest(error=error),self.assertRaises(OSError):enable.kenv_get('daos_recovery',self.kenv_function(error=error))
    def test_kenv_empty_found_value_is_distinct_from_absence_and_truncation_refuses(self):
        self.assertEqual(enable.kenv_get('daos_recovery',self.kenv_function({b'daos_recovery':b''})), '')
        with self.assertRaises(ValueError):enable.kenv_get('daos_recovery',self.kenv_function({b'daos_recovery':b'Q'*129}))
        with self.assertRaises(ValueError):enable.kenv_get('daos_recovery',lambda *args:0)
    def test_normal_root_and_receipt_must_resolve_to_exact_partitions(self):
        enable.mount_guard(self.labels,self.mounts,receipt_path=Path('/var/DaOS/usb-recovery/watchdog-enable'))
        for text in (self.mounts.replace('ufs ro 1 1','ufs rw 1 1'),self.mounts.replace('/dev/gpt/DaOS-root','/dev/md0'),self.mounts+'\n/dev/gpt/DaOS-efi /boot/efi msdosfs ro 0 0'):
            with self.assertRaises(ValueError):enable.mount_guard(self.labels,text)
        with self.assertRaises(ValueError):enable.mount_guard(self.labels,self.mounts,receipt_path=Path('/var/DaOS/updates/watchdog-enable'))
    def test_alias_cycles_and_duplicate_names_are_rejected(self):
        for labels in (self.labels+'\ngpt/DaOS-root N/A mmcsd1p2','gpt/DaOS-root N/A gpt/cycle\ngpt/cycle N/A gpt/DaOS-root'):
            with self.assertRaises(ValueError):enable.mount_guard(labels,self.mounts)
    def test_every_partition_and_extra_partition_is_bound(self):
        enable.check_gpt(enable.DISK_UUID,enable.PARTITIONS)
        for index in range(1,6):
            parts=copy.deepcopy(enable.PARTITIONS);parts[index]['sectors']+=1
            with self.assertRaises(ValueError):enable.check_gpt(enable.DISK_UUID,parts)
        parts=copy.deepcopy(enable.PARTITIONS);parts[6]=parts[5]
        with self.assertRaises(ValueError):enable.check_gpt(enable.DISK_UUID,parts)
    def test_pinned_fat_files_and_sticky_marker_checked_before_enable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=root/'DaOS-recovery/root.ufs.gz';file.parent.mkdir();file.write_bytes(b'v3')
            marker=file.parent/'normal.running';marker.write_bytes(b'\1')
            pins={'DaOS-recovery/root.ufs.gz':enable.sha(file)}
            enable.check_efi_files(root,pins)
            file.write_bytes(b'old-v2')
            with self.assertRaises(ValueError):enable.check_efi_files(root,pins)
            file.write_bytes(b'v3');marker.write_bytes(b'\0')
            with self.assertRaises(ValueError):enable.check_efi_files(root,pins)
    def test_mount_success_followed_by_reported_failure_is_still_unmounted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state={'mount':None};events=[]
            def run(args):
                events.append(list(map(str,args)))
                if args[:2]==['/sbin/mount','-t']:
                    state['mount']=str(args[-1]);raise TimeoutError('Mount succeeded before caller timeout')
                if args==['/sbin/mount','-p']:
                    return '' if state['mount']is None else enable.DISK+'p1 '+state['mount']+' msdosfs ro 0 0'
                if args[0]=='/sbin/umount':state['mount']=None;return ''
                raise AssertionError(args)
            runtime=enable.Native.__new__(enable.Native);runtime.run=run;runtime.mounts=lambda **kw:None
            with patch.object(enable,'RAM',root),self.assertRaises(TimeoutError):runtime.inspect_efi()
            self.assertIsNone(state['mount']);self.assertTrue(any(args[0]=='/sbin/umount'for args in events));self.assertFalse(list(root.iterdir()))
    def test_cleanup_refuses_an_unexpected_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'private';path.mkdir();calls=[]
            def run(args):
                calls.append(args)
                if args==['/sbin/mount','-p']:return '/dev/mmcsd1p1 '+str(path)+' msdosfs ro 0 0'
                raise AssertionError('Must not unmount an unexpected provider')
            with self.assertRaisesRegex(ValueError,'unexpected mount'):enable.cleanup_efi(path,run)
            self.assertTrue(path.exists());self.assertFalse(any(args[0]=='/sbin/umount'for args in calls))


@unittest.skipUnless(os.name=='posix','POSIX policy hard-link/mode mechanics')
class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.policy=Path(self.tmp.name)/'recovery-watchdog.enabled';self.runtime=Fake(self.policy)
    def tearDown(self):self.tmp.cleanup()
    def test_plan_never_remounts_or_creates_policy(self):
        result=enable.execute('plan',self.runtime)
        self.assertEqual(result['status'],'planned');self.assertFalse(self.policy.exists());self.assertNotIn('root-rw',self.runtime.events)
    def test_enable_publishes_only_fixed_readonly_file_after_efi_unmount(self):
        result=enable.execute('enable',self.runtime)
        self.assertEqual(self.policy.read_bytes(),enable.DATA);self.assertEqual(stat.S_IMODE(self.policy.stat().st_mode),0o444);self.assertEqual(self.policy.stat().st_nlink,1)
        self.assertEqual(result['status'],'enabled-policy-only');self.assertFalse(result['watchdog_started']);self.assertFalse(self.runtime.writable)
        self.assertLess(self.runtime.events.index('efi-unmounted'),self.runtime.events.index('root-rw'));self.assertLess(self.runtime.events.index('root-ro'),self.runtime.events.index('receipt'))
        self.assertEqual(list(self.policy.parent.iterdir()),[self.policy])
    def test_failed_efi_or_changed_guard_never_opens_root_write_window(self):
        for scenario in ('efi','guard'):
            runtime=Fake(self.policy);runtime.fail_efi=scenario=='efi';runtime.fail_verify=2 if scenario=='guard' else 0
            with self.assertRaises(ValueError):enable.execute('enable',runtime)
            self.assertNotIn('root-rw',runtime.events);self.assertFalse(self.policy.exists())
    def test_existing_policy_is_never_replaced(self):
        self.policy.write_bytes(b'existing')
        with self.assertRaises(ValueError):enable.execute('enable',self.runtime)
        self.assertEqual(self.policy.read_bytes(),b'existing');self.assertNotIn('root-rw',self.runtime.events)
    def test_exclusive_publish_refuses_racing_external_file(self):
        original=os.link
        def race(source,destination):
            self.policy.write_bytes(b'external');return original(source,destination)
        with patch.object(enable.os,'link',race),self.assertRaises(RuntimeError):enable.execute('enable',self.runtime)
        self.assertEqual(self.policy.read_bytes(),b'external');self.assertFalse(self.runtime.writable)
    def test_publish_sync_failure_removes_only_own_file_and_restores_ro(self):
        actual=enable.syncdir;calls=[]
        def fail_once(path):
            calls.append(path)
            if len(calls)==1:raise OSError('Injected publication fsync failure')
            return actual(path)
        with patch.object(enable,'syncdir',fail_once),self.assertRaises(RuntimeError):enable.execute('enable',self.runtime)
        self.assertFalse(self.policy.exists());self.assertFalse(self.runtime.writable);self.assertFalse(list(self.policy.parent.iterdir()))
    def test_interrupt_immediately_after_link_still_removes_own_policy(self):
        actual=os.link
        def interrupted(source,destination):
            actual(source,destination);raise KeyboardInterrupt('Interrupted after exclusive publication')
        with patch.object(enable.os,'link',interrupted),self.assertRaises(RuntimeError):enable.execute('enable',self.runtime)
        self.assertFalse(self.policy.exists());self.assertFalse(self.runtime.writable);self.assertFalse(list(self.policy.parent.iterdir()))
    def test_failed_publication_and_failed_ro_still_retry_ro_without_policy(self):
        self.runtime.ro_failures=1
        with patch.object(enable.os,'link',side_effect=OSError('Injected link failure')),self.assertRaises(RuntimeError):enable.execute('enable',self.runtime)
        self.assertFalse(self.policy.exists());self.assertFalse(self.runtime.writable);self.assertEqual(self.runtime.events.count('root-ro'),2)
    def test_final_ro_failure_rolls_back_policy_and_retries_ro(self):
        self.runtime.ro_failures=1
        with self.assertRaisesRegex(RuntimeError,'read-only remount'):enable.execute('enable',self.runtime)
        self.assertFalse(self.policy.exists());self.assertFalse(self.runtime.writable)
        self.assertEqual(self.runtime.events.count('root-rw'),2);self.assertEqual(self.runtime.events.count('root-ro'),2)
    def test_rollback_refuses_an_external_replacement_and_still_restores_ro(self):
        self.runtime.ro_failures=1;self.runtime.replacement=True
        with self.assertRaisesRegex(RuntimeError,'Policy changed externally'):enable.execute('enable',self.runtime)
        self.assertEqual(self.policy.read_bytes(),b'external');self.assertFalse(self.runtime.writable)
    def test_receipt_failure_is_reported_after_successful_ro_restore(self):
        def fail(record):raise OSError('State metadata unavailable')
        self.runtime.receipt=fail;result=enable.execute('enable',self.runtime)
        self.assertEqual(result['status'],'enabled-policy-only');self.assertIsNone(result['receipt']);self.assertIn('metadata unavailable',result['receipt_error']);self.assertFalse(self.runtime.writable)


if __name__=='__main__':unittest.main()

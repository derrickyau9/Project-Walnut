"""One-shot USB rescue requests on temporary files; never reboot a machine."""
from contextlib import ExitStack,redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

SOURCE=Path(__file__).resolve().parents[1]/'system/adb/recovery/boot-rescue.py'
spec=importlib.util.spec_from_file_location('boot_rescue_request',SOURCE)
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)


class RescueRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-boot-rescue-qa-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=self.root/'payload';self.base.mkdir()
        self.ram=self.root/'ram';self.ram.mkdir()
        self.nextboot=self.root/'nextboot.conf';self.before=b'nextboot_enable="NO"\n# stable kernel retained\n';self.nextboot.write_bytes(self.before)
        self.files={}
        for name in ('kernel/kernel','kernel/usb_template.ko','kernel/daos_adb_template.ko','root.ufs.gz'):
            path=self.base/name;path.parent.mkdir(parents=True,exist_ok=True);data=('QA '+name).encode();path.write_bytes(data)
            self.files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        (self.base/'payload.json').write_text(json.dumps({'format':'DaOS-RAM-rescue-payload-v1','files':self.files}))
        self.mounts='tmpfs '+str(self.ram)+' tmpfs rw,mode=0700 0 0\n'
        self.validate=boot.validate

    def call(self,action,*,mounts=None,platform='freebsd15',run=None):
        reader=Mock(return_value=self.mounts if mounts is None else mounts)
        runner=run or Mock(return_value=SimpleNamespace(returncode=0))
        output=io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(boot,'RAM',self.ram));stack.enter_context(patch.object(boot,'NEXT',self.nextboot))
            stack.enter_context(patch.object(boot,'validate',side_effect=lambda:self.validate(self.base)))
            stack.enter_context(patch.object(boot.sys,'argv',['boot-rescue.py',action]))
            stack.enter_context(patch.object(boot.sys,'platform',platform))
            stack.enter_context(patch.object(boot.os,'geteuid',return_value=0,create=True))
            stack.enter_context(patch.object(boot.subprocess,'check_output',reader))
            stack.enter_context(patch.object(boot.subprocess,'run',runner))
            stack.enter_context(redirect_stdout(output))
            boot.main()
        return json.loads(output.getvalue()),reader,runner

    def test_arm_from_single_user_needs_no_var_and_preserves_original_selector_in_ram(self):
        self.assertFalse((self.root/'var').exists())
        result,reader,runner=self.call('arm')
        self.assertEqual(result,{'status':'RAM-rescue-nextboot-armed','reboot_requested':False})
        self.assertEqual((self.ram/'usb-rescue-request/nextboot.before').read_bytes(),self.before)
        self.assertEqual(self.nextboot.read_bytes(),boot.selector())
        self.assertEqual([c.args[0] for c in runner.call_args_list],[['/sbin/mount','-u','-w','/'],['/sbin/mount','-u','-r','/']])
        self.assertFalse((self.root/'var').exists())
        self.assertNotIn('/var/',SOURCE.read_text(encoding='utf-8'))

    def test_reboot_is_requested_exactly_once_after_selector_commit(self):
        calls=[]
        def runner(arguments,**kwargs):
            calls.append(arguments)
            if arguments[0]=='/sbin/shutdown':self.assertEqual(self.nextboot.read_bytes(),boot.selector())
            return SimpleNamespace(returncode=0)
        result,_,_=self.call('reboot',run=runner)
        self.assertTrue(result['reboot_requested'])
        self.assertEqual(calls[-1],['/sbin/shutdown','-r','now'])
        self.assertEqual(sum(c[0]=='/sbin/shutdown' for c in calls),1)

    def test_plan_validates_payload_without_mounts_backup_or_writes(self):
        result,reader,runner=self.call('plan',platform='linux')
        self.assertTrue(result['payload_verified']);reader.assert_not_called();runner.assert_not_called()
        self.assertEqual(self.nextboot.read_bytes(),self.before)
        self.assertFalse((self.ram/'usb-rescue-request').exists())

    def test_loader_selector_uses_uncompressed_basename_for_gzip_fallback(self):
        self.validate(self.base)
        self.assertTrue((self.base/'root.ufs.gz').exists());self.assertFalse((self.base/'root.ufs').exists())
        self.assertEqual(boot.SETTINGS['mfsroot_name'],'/boot/DaOS-recovery/root.ufs')
        self.assertIn(b'mfsroot_type="md_image"',boot.selector())
        self.assertNotIn(b'root.ufs.gz',boot.selector())

    def test_missing_non_tmpfs_or_readonly_ram_rejects_before_selector_write(self):
        cases=('', '/dev/da1 '+str(self.ram)+' ufs rw 0 0\n','tmpfs '+str(self.ram)+' tmpfs ro 0 0\n')
        for mounts in cases:
            runner=Mock()
            with self.subTest(mounts=mounts),self.assertRaisesRegex(ValueError,'RAM'):
                self.call('arm',mounts=mounts,run=runner)
            runner.assert_not_called();self.assertEqual(self.nextboot.read_bytes(),self.before)
            self.assertFalse((self.ram/'usb-rescue-request').exists())

    def test_pending_nextboot_refuses_before_mount_or_backup(self):
        pending=b'nextboot_enable="YES"\nkernel="other"\n';self.nextboot.write_bytes(pending)
        with patch.object(boot.subprocess,'run') as runner,self.assertRaisesRegex(ValueError,'pending'):self.call('arm',run=runner)
        runner.assert_not_called();self.assertEqual(self.nextboot.read_bytes(),pending)
        self.assertFalse((self.ram/'usb-rescue-request').exists())

    def test_tampered_payload_rejected_before_backup_or_any_mount_write(self):
        (self.base/'root.ufs.gz').write_bytes(b'bad')
        runner=Mock()
        with self.assertRaisesRegex(ValueError,'payload changed'):self.call('arm',run=runner)
        runner.assert_not_called();self.assertEqual(self.nextboot.read_bytes(),self.before)
        self.assertFalse((self.ram/'usb-rescue-request').exists())

    def test_non_freebsd_native_arm_is_rejected_before_backup_or_mount(self):
        runner=Mock()
        with self.assertRaisesRegex(ValueError,'Native FreeBSD'):self.call('arm',platform='linux',run=runner)
        runner.assert_not_called();self.assertEqual(self.nextboot.read_bytes(),self.before)


if __name__=='__main__':unittest.main()

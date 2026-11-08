"""Pure scratch-file safety checks; never select a kernel or touch a board."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('wifi005_transaction',ROOT/'system/freebsd/raspberrypi/deploy_wifi_005.py')
wifi=importlib.util.module_from_spec(spec);spec.loader.exec_module(wifi)

class WifiTransactionTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/DaOS_Camera_005/wifi-logic-qa';base.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temporary.name)
        (self.root/'boot').mkdir();(self.root/'etc').mkdir()
        self.nextboot=self.root/'boot/nextboot.conf';self.rc=self.root/'etc/rc.conf'
        self.nextboot.write_bytes(b'nextboot_enable="NO"\n# stable selector\n');self.rc.write_bytes(b'hostname="DaOS"\n')
        self.before=(self.nextboot.read_bytes(),self.rc.read_bytes())
    def tearDown(self):self.temporary.cleanup()
    def test_selector_is_published_after_complete_service_configuration(self):
        order=[];original=wifi.write_bytes
        def record(path,data,*args):order.append(path);original(path,data,*args)
        with patch.object(wifi,'write_bytes',record):wifi.arm_trial(self.root)
        self.assertEqual(order,[self.rc,self.nextboot]);self.assertIn(b'DaOS_wifi_driver_enable="NO"',self.rc.read_bytes())
        self.assertTrue(self.nextboot.read_bytes().startswith(b'nextboot_enable="YES"'))
    def test_deferred_trial_disables_auto_attach_but_publishes_one_shot_selector_last(self):
        self.rc.write_bytes(b'DaOS_wifi_driver_enable="YES"\nhostname="DaOS"\n')
        order=[];original=wifi.write_bytes
        def record(path,data,*args):order.append(path);original(path,data,*args)
        with patch.object(wifi,'write_bytes',record):wifi.arm_trial(self.root,defer_radio=True)
        self.assertEqual(order,[self.rc,self.nextboot])
        self.assertIn(b'DaOS_wifi_driver_enable="NO"',self.rc.read_bytes())
        self.assertNotIn(b'DaOS_wifi_driver_enable="YES"',self.rc.read_bytes())
        self.assertIn(b'DaOS_system_enable="YES"',self.rc.read_bytes())
        self.assertTrue(self.nextboot.read_bytes().startswith(b'nextboot_enable="YES"'))
        self.assertIn(('kernel="'+wifi.KERNEL+'"').encode(),self.nextboot.read_bytes())
    def test_defer_radio_is_rejected_outside_trial_before_platform_or_disk_operations(self):
        for action in ('stage','commit','rollback'):
            with self.subTest(action=action),patch.object(wifi.sys,'argv',['deploy_wifi_005.py',action,'--defer-radio']),patch.object(wifi,'native') as native,patch.object(wifi,'run') as commands:
                with self.assertRaises(SystemExit) as raised:wifi.main()
                self.assertEqual(raised.exception.code,2)
                native.assert_not_called();commands.assert_not_called()
    def test_unvalidated_trial_rejects_explicit_autoattach_before_any_write(self):
        with patch.object(wifi,'write_bytes') as writes:
            with self.assertRaisesRegex(ValueError,'must defer'):wifi.arm_trial(self.root,defer_radio=False)
        writes.assert_not_called();self.assertEqual((self.nextboot.read_bytes(),self.rc.read_bytes()),self.before)
    def test_cli_trial_without_defer_flag_still_disables_radio_and_records_it(self):
        work=self.root/'work';work.mkdir();country=self.root/'country';country.write_text('US')
        receipt={'format':'DaOS-wifi-005-trial-v1','payload_sha256':wifi.PAYLOAD_SHA,'status':'staged-default-unchanged','backups':{}}
        import json
        (work/'deployment.json').write_text(json.dumps(receipt));(work/'payload-manifest.json').write_text('{}')
        def rooted(value):
            mapping={'/':self.root,'/boot':self.root/'boot','/boot/nextboot.conf':self.nextboot,'/var/DaOS/system/wifi/country':country}
            return mapping.get(str(value),Path(value))
        def command(args):return '/boot/kernel/kernel' if args[-1]=='kern.bootfile' else ''
        with patch.object(wifi.sys,'argv',['deploy_wifi_005.py','trial','--fallback-observed']),patch.object(wifi,'WORK',work),patch.object(wifi,'Path',side_effect=rooted),patch.object(wifi,'native'),patch.object(wifi,'run',side_effect=command),patch.object(wifi,'sha',return_value=wifi.KERNEL_SHA),patch.object(wifi,'validate_payload'):
            wifi.main()
        saved=json.loads((work/'deployment.json').read_text())
        self.assertIs(saved['radio_attach_deferred'],True)
        self.assertEqual(saved['status'],'one-shot-armed-stable-default-retained')
        self.assertIn(b'DaOS_wifi_driver_enable="NO"',self.rc.read_bytes())
        self.assertNotIn(b'DaOS_wifi_driver_enable="YES"',self.rc.read_bytes())
        self.assertTrue(self.nextboot.read_bytes().startswith(b'nextboot_enable="YES"'))
    def test_commit_reenables_deferred_radio_before_publishing_default(self):
        loader=self.root/'boot/loader.conf.local';loader.write_bytes(b'kernel="kernel"\n')
        self.rc.write_bytes(b'DaOS_wifi_driver_enable="NO"\nhostname="DaOS"\n')
        order=[];original=wifi.write_bytes
        def record(path,data,*args):order.append(path);original(path,data,*args)
        with patch.object(wifi,'write_bytes',record):wifi.commit_defaults(self.root)
        self.assertEqual(order,[self.rc,loader]);self.assertIn(b'DaOS_wifi_driver_enable="YES"',self.rc.read_bytes())
        self.assertNotIn(b'DaOS_wifi_driver_enable="NO"',self.rc.read_bytes())
        self.assertIn(('kernel="'+wifi.KERNEL+'"').encode(),loader.read_bytes())
    def test_commit_failure_after_selector_publication_restores_selector_then_rc(self):
        loader=self.root/'boot/loader.conf.local';loader.write_bytes(b'kernel="kernel"\n')
        self.rc.write_bytes(b'DaOS_wifi_driver_enable="NO"\n')
        before=(loader.read_bytes(),self.rc.read_bytes());order=[];original=wifi.write_bytes;injected=False
        def fail_once(path,data,*args):
            nonlocal injected
            order.append(path);original(path,data,*args)
            if path==loader and not injected:injected=True;raise OSError('injected commit post-rename failure')
        with patch.object(wifi,'write_bytes',fail_once):
            with self.assertRaises(OSError):wifi.commit_defaults(self.root)
        self.assertEqual((loader.read_bytes(),self.rc.read_bytes()),before);self.assertEqual(order[-2:],[loader,self.rc])
    def test_failed_commit_restores_absence_of_original_selector(self):
        loader=self.root/'boot/loader.conf.local';before=self.rc.read_bytes();original=wifi.write_bytes;injected=False
        def fail_once(path,data,*args):
            nonlocal injected
            original(path,data,*args)
            if path==loader and not injected:injected=True;raise OSError('injected commit publication failure')
        with patch.object(wifi,'write_bytes',fail_once):
            with self.assertRaises(OSError):wifi.commit_defaults(self.root)
        self.assertFalse(loader.exists());self.assertEqual(self.rc.read_bytes(),before)
    def test_fault_after_selector_publication_restores_no_flag_then_original_rc(self):
        order=[];original=wifi.write_bytes;injected=False
        def fail_once(path,data,*args):
            nonlocal injected
            order.append(path);original(path,data,*args)
            if path==self.nextboot and not injected:injected=True;raise OSError('injected post-rename failure')
        with patch.object(wifi,'write_bytes',fail_once):
            with self.assertRaises(OSError):wifi.arm_trial(self.root)
        self.assertEqual((self.nextboot.read_bytes(),self.rc.read_bytes()),self.before)
        self.assertEqual(order[-2:],[self.nextboot,self.rc])
    def test_fault_before_service_write_never_leaves_armed_selector(self):
        original=wifi.write_bytes;injected=False
        def fail_once(path,data,*args):
            nonlocal injected
            if path==self.rc and not injected:injected=True;raise OSError('disk failure')
            original(path,data,*args)
        with patch.object(wifi,'write_bytes',fail_once):
            with self.assertRaises(OSError):wifi.arm_trial(self.root)
        self.assertEqual((self.nextboot.read_bytes(),self.rc.read_bytes()),self.before)
    def test_full_payload_gate_detects_changed_module_and_redirect(self):
        module=self.root/'boot'/wifi.KERNEL/'if_brcmfmac.ko';module.parent.mkdir();module.write_bytes(b'expected-module')
        os.chmod(module,0o644)
        manifest={'kernel_directory':wifi.KERNEL,'update_id':'wifi-005','files':[{'path':'root/boot/'+wifi.KERNEL+'/if_brcmfmac.ko','sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'mode':0o644}]}
        wifi.validate_payload(self.root,manifest)
        module.write_bytes(b'changed-module')
        with self.assertRaises(ValueError):wifi.validate_payload(self.root,manifest)
        manifest['files'][0]['path']='root/../escape'
        with self.assertRaises(ValueError):wifi.validate_payload(self.root,manifest)
    def staging_space(self,root_free,work_free,*,same_filesystem=False,backup_bytes=0):
        updates=self.root/'updates-volume';updates.mkdir(exist_ok=True)
        work=updates/'wifi-005'
        if backup_bytes:self.rc.write_bytes(b'x'*backup_bytes)
        manifest={'files':[{'path':'root/boot/'+wifi.KERNEL+'/kernel','bytes':90*1024**2}]}
        original=Path.stat
        def filesystem(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path in (self.root,updates):
                fields=list(value);fields[2]=11 if same_filesystem or path==self.root else 22
                return os.stat_result(fields)
            return value
        def available(path):
            self.assertIn(path,(self.root,updates))
            return SimpleNamespace(free=root_free if path==self.root else work_free)
        with patch.object(Path,'stat',autospec=True,side_effect=filesystem),patch.object(wifi.shutil,'disk_usage',side_effect=available):
            wifi.check_staging_space(self.root,work,manifest)
        self.assertFalse(work.exists())
    def test_budget_uses_update_mount_and_actual_payload_instead_of_200m_parent_gate(self):
        self.staging_space(165*1024**2,500*1024**2)
    def test_budget_rejects_insufficient_root_reserve(self):
        with self.assertRaisesRegex(ValueError,'root space'):
            self.staging_space(110*1024**2,500*1024**2)
    def test_budget_rejects_insufficient_update_mount(self):
        with self.assertRaisesRegex(ValueError,'update-cache'):
            self.staging_space(165*1024**2,110*1024**2)
    def test_budget_counts_incoming_and_installed_payload_on_shared_filesystem(self):
        with self.assertRaisesRegex(ValueError,'shared'):
            self.staging_space(165*1024**2,165*1024**2,same_filesystem=True)
        self.staging_space(230*1024**2,230*1024**2,same_filesystem=True)
    def test_budget_includes_files_that_will_be_backed_up(self):
        with self.assertRaisesRegex(ValueError,'update-cache'):
            self.staging_space(165*1024**2,124*1024**2,backup_bytes=3*1024**2)
    def test_bad_archive_is_rejected_before_work_creation_or_root_writes(self):
        archive=self.root/'bad.tar.gz';archive.write_bytes(b'invalid candidate')
        work=self.root/'new-work'
        with patch.object(wifi,'KERNEL_SHA','a'*64),patch.object(wifi,'PAYLOAD_SHA','b'*64),patch.object(wifi.sys,'argv',['deploy_wifi_005.py','stage','--archive',str(archive),'--stage-root',str(self.root),'--work-dir',str(work)]),patch.object(wifi,'run') as commands:
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):wifi.main()
        self.assertFalse(work.exists());commands.assert_not_called()
        self.assertEqual((self.nextboot.read_bytes(),self.rc.read_bytes()),self.before)
    def test_existing_work_directory_fails_before_payload_revalidation(self):
        archive=self.root/'bad.tar.gz';archive.write_bytes(b'invalid candidate')
        work=self.root/'old-work';work.mkdir();(work/'receipt').write_bytes(b'keep original')
        with patch.object(wifi.sys,'argv',['deploy_wifi_005.py','stage','--archive',str(archive),'--stage-root',str(self.root),'--work-dir',str(work)]),patch.object(wifi,'_read_payload') as inspect:
            with self.assertRaisesRegex(ValueError,'fresh private'):wifi.main()
        inspect.assert_not_called();self.assertEqual((work/'receipt').read_bytes(),b'keep original')
    def test_trial_and_committed_defaults_always_pin_platform_dma_off(self):
        self.assertEqual(wifi.BOOT['hw.bcm2835.sdhci.pio_mode'],'1')
        wifi.arm_trial(self.root,defer_radio=True)
        self.assertIn(b'hw.bcm2835.sdhci.pio_mode="1"',self.nextboot.read_bytes())
        wifi.commit_defaults(self.root)
        self.assertIn(b'hw.bcm2835.sdhci.pio_mode="1"',(self.root/'boot/loader.conf.local').read_bytes())
    def test_unbound_release_hashes_fail_before_reading_archive(self):
        with patch.object(wifi,'KERNEL_SHA','__KERNEL_SHA256_005__'),patch.object(wifi,'PAYLOAD_SHA','__PAYLOAD_SHA256_005__'),patch.object(wifi,'sha') as read:
            with self.assertRaisesRegex(ValueError,'not bound'):wifi._read_payload(self.root/'missing')
        read.assert_not_called()
    def test_running_trial_rejects_wrong_kernel_wrong_hash_or_platform_dma_enabled(self):
        cases=[('/boot/kernel/kernel','a'*64,'1'),('/boot/'+wifi.KERNEL+'/kernel','c'*64,'1'),('/boot/'+wifi.KERNEL+'/kernel','a'*64,'0')]
        for boot,digest,pio in cases:
            def result(args):return boot if args[-1]=='kern.bootfile' else pio
            with self.subTest(boot=boot,digest=digest,pio=pio),patch.object(wifi,'KERNEL_SHA','a'*64),patch.object(wifi,'PAYLOAD_SHA','b'*64),patch.object(wifi,'sha',return_value=digest),patch.object(wifi,'run',side_effect=result):
                with self.assertRaises(ValueError):wifi.require_running_trial()
    def test_running_trial_accepts_only_matching_kernel_hash_and_pio_one(self):
        def result(args):return '/boot/'+wifi.KERNEL+'/kernel' if args[-1]=='kern.bootfile' else '1'
        with patch.object(wifi,'KERNEL_SHA','a'*64),patch.object(wifi,'PAYLOAD_SHA','b'*64),patch.object(wifi,'sha',return_value='a'*64),patch.object(wifi,'run',side_effect=result):wifi.require_running_trial()
    def test_commit_receipt_requires_all_checks_and_current_candidate_identity(self):
        with patch.object(wifi,'KERNEL_SHA','a'*64),patch.object(wifi,'PAYLOAD_SHA','b'*64):
            report={'format':'DaOS-wifi-005-validation-v1','kernel_sha256':'a'*64,'payload_sha256':'b'*64,'passed':True,'errors':[],'boot_time':'boot005','completed_at':1000,'checks':dict.fromkeys(wifi.CHECKS,True)}
            wifi.validate_commit(report,boot_time='boot005',now=1001)
            report['checks']['platform_dma_disabled']=False
            with self.assertRaises(ValueError):wifi.validate_commit(report,boot_time='boot005',now=1001)
            report['checks']['platform_dma_disabled']=True;report['format']='DaOS-wifi-004-validation-v1'
            with self.assertRaises(ValueError):wifi.validate_commit(report,boot_time='boot005',now=1001)
    @unittest.skipIf(os.name=='nt','POSIX executable permission semantics')
    def test_rollback_restores_executable_mode(self):
        work=self.root/'work';source=work/'backup/usr/local/libexec/DaOS-wifi-load.py';source.parent.mkdir(parents=True)
        source.write_bytes(b'#!/usr/local/bin/python3.12\n');target=self.root/'usr/local/libexec/DaOS-wifi-load.py';target.parent.mkdir(parents=True);target.write_bytes(b'candidate')
        receipt={'backups':{'usr/local/libexec/DaOS-wifi-load.py':{'sha256':wifi.sha(source),'mode':0o755}}}
        wifi.restore(self.root,receipt,work)
        self.assertEqual(target.read_bytes(),source.read_bytes());self.assertEqual(target.stat().st_mode&0o777,0o755)
    def test_atomic_write_failure_removes_its_own_temporary(self):
        with patch.object(wifi.os,'replace',side_effect=OSError('rename rejected')):
            with self.assertRaises(OSError):wifi.write_bytes(self.rc,b'candidate')
        self.assertFalse(self.rc.with_name(self.rc.name+'.005.tmp').exists());self.assertEqual(self.rc.read_bytes(),self.before[1])

if __name__=='__main__':unittest.main()

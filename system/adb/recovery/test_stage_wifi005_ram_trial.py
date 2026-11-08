"""Private tree transactions only; no native mounts, SD, watchdog or boot."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('wifi005_ram_stage',Path(__file__).with_name('stage_wifi005_ram_trial.py'))
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)


class Fake:
    def __init__(self,base):
        self.root=base/'root';self.source=base/'ram-input';self.work=base/'state'/'trial';self.trial='a'*32
        self.root.mkdir();self.source.mkdir();self.work.parent.mkdir()
        (self.root/'boot').mkdir();self.before=b'nextboot_enable="NO" \nkernel="stable-unused"\n'
        (self.root/'boot/nextboot.conf').write_bytes(self.before);os.chmod(self.root/'boot/nextboot.conf',0o644)
        self.inputs={}
        for index,name in enumerate(('kernel','usb_template.ko','daos_adb_template.ko')):
            header=bytearray(64);header[:6]=b'\x7fELF\x02\x01';struct.pack_into('<HH',header,16,2 if index==0 else 3,183)
            data=bytes(header)+name.encode();(self.source/name).write_bytes(data)
            self.inputs[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'elf_type':2 if index==0 else 3}
        self.events=[];self.writable=False;self.verify_calls=0;self.fail_verify=0;self.ro_failures=0
    def secure(self,path):
        if any(p.is_symlink()for p in (path,*path.parents)):raise ValueError('Redirected private fixture')
        return path
    def verify(self):
        self.events.append('verify');self.verify_calls+=1
        if self.verify_calls==self.fail_verify:raise ValueError('Injected production guard failure')
        return {'stable':True,'watchdog_timeout_sbt':8*(1<<32)}
    def inspect_efi(self):self.events+=['efi-ro','efi-unmounted'];return {'marker':1,'fatv3':True}
    def current_payload(self):self.events.append('RAM-and-DTBO-pinned')
    def validate_inputs(self):return stage.validate_sources(self.source,self.inputs,self.secure)
    def selector_state(self):
        path=self.root/'boot/nextboot.conf';return path.read_bytes(),stat.S_IMODE(path.stat().st_mode)
    def state_location(self):self.events.append('bound-state')
    def prepare_work(self):self.work.mkdir(mode=0o700)
    def load_work(self):return json.loads((self.work/'receipt.json').read_text())
    def check_space(self):return stage.space_report(50*1024**2,100*1024**2,sum(entry['bytes']for entry in self.inputs.values()))
    def remount(self,writable):
        self.events.append('root-rw'if writable else'root-ro')
        if not writable and self.ro_failures:self.ro_failures-=1;raise OSError('Injected final RO failure')
        self.writable=writable
    def assert_root(self,writable):
        if self.writable!=writable:raise ValueError('Unexpected root access mode')


class Bindings(unittest.TestCase):
    def test_selector_binds_only_new_candidate_ram_and_explicit_usb_preloads(self):
        text=stage.selector('a'*32).decode()
        for line in ('kernel="kernel.daos-wifi-005-ramprobe"','module_path="/boot/kernel.daos-wifi-005-ramprobe"',
                     'bootfile="/boot/kernel.daos-wifi-005-ramprobe/kernel"','kernel_path="/boot/kernel.daos-wifi-005-ramprobe"',
                     'kernels_autodetect="NO"',
                     'usb_template_name="/boot/kernel.daos-wifi-005-ramprobe/usb_template.ko"','daos_adb_template_name="/boot/kernel.daos-wifi-005-ramprobe/daos_adb_template.ko"',
                     'init_script="/usr/local/libexec/DaOS-recovery/early-init.sh"','daos_recovery="YES"','daos_wifi005_no_rf="YES"',
                     'vfs.root.mountfrom="ufs:/dev/md0"','hw.bcm2835.sdhci.pio_mode="1"','sdio_load="NO"','if_brcmfmac_load="NO"'):
            self.assertIn(line+'\n',text)
        self.assertIn(stage.RAM_SHA,text);self.assertIn(stage.INPUTS['kernel']['sha256'],text)
        self.assertNotIn(';',text)
    def test_space_boundary_reports_ordinary_availability_and_reserve_separately(self):
        total=19682936;needed=total+6*1024**2
        report=stage.space_report(27688960,234184704,total)
        self.assertEqual(report['required_available_bytes'],needed)
        self.assertEqual(stage.space_report(needed,needed,total)['rollback_margin_bytes'],6*1024**2)
        with self.assertRaises(ValueError):stage.space_report(needed-1,234184704,total)
    def test_only_exact_owned_active_or_consumed_selector_matches(self):
        data=stage.selector('a'*32);consumed=data.replace(b'nextboot_enable="YES"',b'nextboot_enable="NO" ',1)
        self.assertTrue(stage.ours(data,data));self.assertTrue(stage.ours(consumed,data))
        self.assertFalse(stage.ours(consumed+b'kernel="external"\n',data))
        self.assertFalse(stage.cleared(b'nextboot_enable="NO"\nnextboot_enable="YES"\n'))


@unittest.skipUnless(os.name=='posix','POSIX file modes/hard links')
class Transactions(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.runtime=Fake(Path(self.tmp.name))
    def tearDown(self):self.tmp.cleanup()
    def test_plan_is_readonly_and_creates_no_receipt_or_candidate(self):
        result=stage.plan(self.runtime)
        self.assertEqual(result['status'],'planned');self.assertEqual(self.runtime.selector_state()[0],self.runtime.before)
        self.assertFalse(self.runtime.work.exists());self.assertFalse((self.runtime.root/stage.DIRECTORY).exists());self.assertNotIn('root-rw',self.runtime.events)
    def test_apply_writes_three_verified_files_then_selector_and_returns_ro(self):
        events=[];copy=stage.copy_candidate;atomic=stage.atomic
        def capture_copy(source,path,entry):events.append(path.name);return copy(source,path,entry)
        def capture_atomic(path,data,*args,**kwargs):
            if path==self.runtime.root/'boot/nextboot.conf':events.append('nextboot')
            return atomic(path,data,*args,**kwargs)
        with patch.object(stage,'copy_candidate',capture_copy),patch.object(stage,'atomic',capture_atomic):record=stage.apply(self.runtime)
        self.assertEqual(events,['kernel','usb_template.ko','daos_adb_template.ko','nextboot'])
        self.assertEqual(record['status'],'armed-no-reboot');self.assertFalse(record['reboot_performed']);self.assertFalse(self.runtime.writable)
        self.assertEqual((self.runtime.work/'nextboot.before').read_bytes(),self.runtime.before)
        for name,entry in self.runtime.inputs.items():
            path=self.runtime.root/stage.DIRECTORY/name;self.assertEqual(stage.sha(path),entry['sha256']);self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o644)
    def test_clear_restores_backup_keeps_candidate_and_accepts_consumed_flag(self):
        stage.apply(self.runtime);path=self.runtime.root/'boot/nextboot.conf';path.write_bytes(path.read_bytes().replace(b'nextboot_enable="YES"',b'nextboot_enable="NO" ',1))
        result=stage.clear_nextboot(self.runtime)
        self.assertEqual(result['status'],'cleared-no-reboot');self.assertEqual(path.read_bytes(),self.runtime.before);self.assertFalse(self.runtime.writable)
        self.assertEqual(len(list((self.runtime.root/stage.DIRECTORY).iterdir())),3)
    def test_input_hash_extra_file_or_existing_target_prevents_rw(self):
        (self.runtime.source/'kernel').write_bytes(b'tampered')
        with self.assertRaises(ValueError):stage.apply(self.runtime)
        self.assertNotIn('root-rw',self.runtime.events)
    def test_extra_radio_input_is_refused(self):
        (self.runtime.source/'if_brcmfmac.ko').write_bytes(b'RF')
        with self.assertRaises(ValueError):stage.plan(self.runtime)
    def test_after_plan_selector_change_is_not_adopted_as_backup(self):
        original=self.runtime.selector_state;calls=[]
        def changed():
            calls.append(1)
            if len(calls)==2:(self.runtime.root/'boot/nextboot.conf').write_bytes(b'nextboot_enable="YES"\nexternal=1\n')
            return original()
        self.runtime.selector_state=changed
        with self.assertRaisesRegex(ValueError,'after planning'):stage.apply(self.runtime)
        self.assertFalse(self.runtime.work.exists());self.assertNotIn('root-rw',self.runtime.events)
    def test_guard_change_after_prepared_receipt_does_not_open_root_rw(self):
        self.runtime.fail_verify=3
        with self.assertRaises(RuntimeError):stage.apply(self.runtime)
        self.assertNotIn('root-rw',self.runtime.events);self.assertFalse(self.runtime.writable);self.assertEqual(self.runtime.selector_state()[0],self.runtime.before)
    def test_failure_after_selector_rename_restores_selector_before_removing_files(self):
        atomic=stage.atomic
        def interrupted(path,data,*args,**kwargs):
            result=atomic(path,data,*args,**kwargs)
            if path==self.runtime.root/'boot/nextboot.conf' and data.startswith(b'nextboot_enable="YES"'):raise OSError('Interrupted after selector rename')
            return result
        with patch.object(stage,'atomic',interrupted),self.assertRaises(RuntimeError):stage.apply(self.runtime)
        self.assertEqual(self.runtime.selector_state()[0],self.runtime.before);self.assertFalse((self.runtime.root/stage.DIRECTORY).exists());self.assertFalse(self.runtime.writable)
        self.assertTrue(self.runtime.load_work()['rollback_complete'])
    def test_final_root_ro_failure_rolls_back_selector_and_files(self):
        self.runtime.ro_failures=1
        with self.assertRaisesRegex(RuntimeError,'final RO'):stage.apply(self.runtime)
        self.assertEqual(self.runtime.selector_state()[0],self.runtime.before);self.assertFalse((self.runtime.root/stage.DIRECTORY).exists());self.assertFalse(self.runtime.writable)
    def test_partial_mismatched_copy_is_retained_and_reported_incomplete(self):
        def partial(source,path,entry):path.write_bytes(b'partial');raise OSError('Partial write failed')
        with patch.object(stage,'copy_candidate',partial),self.assertRaises(RuntimeError):stage.apply(self.runtime)
        record=self.runtime.load_work();self.assertEqual(record['status'],'failed-rollback-incomplete');self.assertFalse(record['rollback_complete']);self.assertTrue(record['selector_restored'])
        self.assertEqual(self.runtime.selector_state()[0],self.runtime.before);self.assertFalse(self.runtime.writable);self.assertEqual((self.runtime.root/stage.DIRECTORY/'kernel').read_bytes(),b'partial')
    def test_external_selector_change_is_preserved_and_candidate_not_removed(self):
        copy=stage.copy_candidate
        def external(source,path,entry):
            copy(source,path,entry)
            if path.name=='daos_adb_template.ko':(self.runtime.root/'boot/nextboot.conf').write_bytes(b'nextboot_enable="YES"\nkernel="external"\n')
        with patch.object(stage,'copy_candidate',external),self.assertRaises(RuntimeError):stage.apply(self.runtime)
        self.assertIn(b'external',self.runtime.selector_state()[0]);self.assertEqual(len(list((self.runtime.root/stage.DIRECTORY).iterdir())),3);self.assertFalse(self.runtime.load_work()['rollback_complete']);self.assertFalse(self.runtime.writable)
    def test_corrupt_backup_or_external_selector_refuses_clear_before_rw(self):
        stage.apply(self.runtime);self.runtime.events=[];(self.runtime.work/'nextboot.before').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):stage.clear_nextboot(self.runtime)
        self.assertNotIn('root-rw',self.runtime.events)


if __name__=='__main__':unittest.main()

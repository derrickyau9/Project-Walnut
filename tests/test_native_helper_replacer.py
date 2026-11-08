"""One-use updater failure tests: private files and mocked native gates only."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import struct
import tempfile
import unittest
from types import SimpleNamespace

FILE=Path(__file__).resolve().parents[1]/'build/DaOS_USB_Recovery_001/replace-native-helper-over-usb.py'
if not FILE.is_file():
    raise unittest.SkipTest('Historical local updater fixture is absent; maintained RAM-only recovery lives in system/adb/recovery')
module_spec=importlib.util.spec_from_file_location('helper_replacer',FILE)
updater=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(updater)


def executable():
    data=bytearray(120);data[:8]=b'\x7fELF\x02\x01\x01\x09'
    struct.pack_into('<HHI',data,16,2,183,1)
    struct.pack_into('<Q',data,32,64);struct.pack_into('<HH',data,54,56,1)
    struct.pack_into('<I',data,64,1)
    return bytes(data)


class HelperReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.old=b'original helper';self.new=executable()
        self.target=self.base/'helper';self.target.write_bytes(self.old)
        self.upload=self.base/'candidate';self.upload.write_bytes(self.new)
        self.work=self.base/'persistent-backup'
        self.spec={'token':'1'*32,'upload':str(self.upload),'backup':str(self.work),
                   'before_sha256':hashlib.sha256(self.old).hexdigest(),
                   'after_sha256':hashlib.sha256(self.new).hexdigest(),'after_bytes':len(self.new)}
        self.ns={'__name__':'mock_remote'}
        exec(updater.REMOTE+'\n'+updater.importlib_source_elf(),self.ns)
        self.ns['TARGET']=self.target
        # Privileged filesystem/identity proof is replaced only for this fixture.
        self.ns['plain']=lambda p:Path(p)
        self.ns['prove']=lambda spec:{'root_read_only':True,'dual_gpt_verified':True,'backup_on_bound_state':True}
        self.mount_commands=[]
        self.ns['run']=lambda args:self.mount_commands.append(args)
        self.mount_modes=[];self.ns['mounts']=lambda mode,work:self.mount_modes.append(mode)
        self.ns['atomic']=self.write
        if os.name=='nt':self.ns['stat']=SimpleNamespace(S_IMODE=lambda mode:0o755)
        self.ns['persist']=lambda work,r,spec:(work/'receipt.json').write_text(json.dumps(r))
    def tearDown(self):self.temp.cleanup()
    def write(self,path,data,mode,token,on_replace=None):
        path.write_bytes(data);path.chmod(mode)
        if on_replace:on_replace()
    def apply(self):return self.ns['replace'](copy.deepcopy(self.spec))

    def test_success_persistent_backup_precedes_root_write_and_root_restored(self):
        def run(args):
            if '-w' in args:
                self.assertEqual((self.work/'daos-usb-adb.before').read_bytes(),self.old)
                self.assertEqual(json.loads((self.work/'receipt.json').read_text())['status'],'backed-up-before-root-remount')
            self.mount_commands.append(args)
        self.ns['run']=run
        result=self.apply()
        self.assertEqual(result['status'],'helper-replaced');self.assertTrue(result['root_read_only'])
        self.assertEqual(self.target.read_bytes(),self.new)
        self.assertEqual(self.mount_commands,[['/sbin/mount','-u','-w','/'],['/sbin/mount','-u','-r','/']])
        self.assertFalse(result['reboot_requested']);self.assertFalse(result['helper_restarted'])

    def test_failed_remount_w_still_attempts_remount_ro(self):
        def run(args):
            self.mount_commands.append(args)
            if '-w' in args:raise OSError('mount-w failed after possible flag change')
        self.ns['run']=run
        result=self.apply()
        self.assertEqual(self.target.read_bytes(),self.old)
        self.assertEqual(self.mount_commands[-1],['/sbin/mount','-u','-r','/'])
        self.assertTrue(result['root_read_only']);self.assertEqual(result['status'],'failed-before-publication')

    def test_failure_after_rename_rolls_back_exact_old_and_restores_ro(self):
        def write(path,data,mode,token,on_replace=None):
            self.write(path,data,mode,token,on_replace)
            if path==self.target and data==self.new:raise OSError('injected directory fsync failure after rename')
        self.ns['atomic']=write
        result=self.apply()
        self.assertEqual(result['status'],'failed-rolled-back')
        self.assertEqual(self.target.read_bytes(),self.old);self.assertTrue(result['root_read_only'])

    def test_candidate_write_failure_before_publish_keeps_old(self):
        def write(path,data,mode,token,on_replace=None):
            if path==self.target:raise OSError('injected candidate write failure')
            self.write(path,data,mode,token,on_replace)
        self.ns['atomic']=write
        result=self.apply()
        self.assertEqual(result['status'],'failed-before-publication')
        self.assertEqual(self.target.read_bytes(),self.old);self.assertTrue(result['root_read_only'])

    def test_rollback_failure_is_reported_and_ro_still_restored(self):
        def write(path,data,mode,token,on_replace=None):
            if path==self.target and data==self.old:raise OSError('rollback I/O error')
            self.write(path,data,mode,token,on_replace)
            if path==self.target:raise OSError('post-publication error')
        self.ns['atomic']=write
        result=self.apply()
        self.assertEqual(result['status'],'failed-rollback-refused');self.assertTrue(result['root_read_only'])
        self.assertIn('rollback I/O error',result['rollback_error'])
        self.assertEqual((self.work/'daos-usb-adb.before').read_bytes(),self.old)

    def test_readonly_restore_failure_never_reports_success(self):
        def run(args):
            self.mount_commands.append(args)
            if '-r' in args:raise OSError('read-only remount rejected')
        self.ns['run']=run
        result=self.apply()
        self.assertEqual(result['status'],'failed-root-readonly-restore');self.assertFalse(result['root_read_only'])
        self.assertIn('root_restore_error',json.loads((self.work/'receipt.json').read_text()))
        self.assertEqual(self.target.read_bytes(),self.old)

    def test_final_receipt_failure_rolls_back_old_and_never_reports_success(self):
        writes=0
        def persist(work,receipt,spec):
            nonlocal writes
            writes+=1
            if writes>=2:raise OSError('persistent receipt I/O error')
            (work/'receipt.json').write_text(json.dumps(receipt))
        self.ns['persist']=persist
        result=self.apply()
        self.assertEqual(result['status'],'failed-finalization-rolled-back')
        self.assertTrue(result['root_read_only']);self.assertIn('receipt_error',result)
        self.assertEqual(self.target.read_bytes(),self.old)

    def test_transient_final_ro_failure_restores_old_and_second_ro_is_verified(self):
        ro_calls=0
        def run(args):
            nonlocal ro_calls
            self.mount_commands.append(args)
            if '-r' in args:
                ro_calls+=1
                if ro_calls==1:raise OSError('first root-ro remount fails')
        self.ns['run']=run
        result=self.apply()
        self.assertEqual(result['status'],'failed-finalization-rolled-back')
        self.assertTrue(result['root_read_only']);self.assertEqual(ro_calls,2)
        self.assertEqual(self.target.read_bytes(),self.old)

    def test_wrong_upload_rejected_before_backup_or_remount(self):
        self.upload.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'uploaded candidate'):self.apply()
        self.assertFalse(self.work.exists());self.assertEqual(self.mount_commands,[])

    def test_existing_backup_directory_never_reused(self):
        self.work.mkdir()
        with self.assertRaisesRegex(ValueError,'Fresh persistent'):self.apply()
        self.assertEqual(self.mount_commands,[]);self.assertEqual(self.target.read_bytes(),self.old)

    def test_state_gate_rejects_nullfs_photos_foreign_root_and_rw_root(self):
        actual={};exec(updater.REMOTE+'\n'+updater.importlib_source_elf(),actual)
        good=[['/dev/mmcsd0p2','/','ufs','ro','1','1'],['/dev/mmcsd0p3','/var','ufs','rw','2','2'],['tmpfs','/tmp','tmpfs','rw','0','0']]
        work='/var/DaOS/usb-recovery/helper-'+'1'*32
        for change in ('good','nullfs','foreign','rwroot'):
            rows=copy.deepcopy(good)
            if change=='nullfs':rows.append(['/photos/updates','/var/DaOS/usb-recovery','nullfs','rw','0','0'])
            if change=='foreign':rows[0][0]='/dev/md0'
            if change=='rwroot':rows[0][3]='rw'
            actual['table']=lambda:(rows,lambda source:source)
            if change=='good':actual['mounts']('ro',work)
            else:
                with self.subTest(change=change),self.assertRaises(ValueError):actual['mounts']('ro',work)

    def test_arm64_elf_and_initial_service_limit(self):
        updater.elf(self.new)
        for offset,value in ((7,0),(18,62),(4,1)):
            data=bytearray(self.new);data[offset]=value
            with self.subTest(offset=offset),self.assertRaises(ValueError):updater.elf(data)
        evidence=FILE.parent/'physical-acceptance-1ab37e7ad43d431c92427c62d1ca4cb8/actual-full-gpt-identity.json'
        initial=updater.initial_guard(json.loads(evidence.read_text())['partitions'])
        compile(initial,'initial-readonly-preupload-guard','exec')
        service='shell,v2,raw:'+' '.join(shlex.quote(v) for v in ('/usr/local/bin/python3.12','-I','-B','-c',initial))
        self.assertLessEqual(len(service.encode()),4096)


if __name__=='__main__':unittest.main()

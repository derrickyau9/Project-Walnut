"""Recovery installer transactions on private roots; never remount or open SD."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import stat
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('recovery_install',HERE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-recovery-install-')
        self.base=Path(self.temp.name);self.root=self.base/'root';self.root.mkdir()
        self.bundle=self.base/'bundle';self.bundle.mkdir();self.work=self.base/'work'
        self.init=self.root/'sbin/init';self.init.parent.mkdir();self.init.write_bytes(b'original static init');self.init.chmod(0o755)
        loader=self.root/installer.LOADER;loader.parent.mkdir();loader.write_bytes(b'# existing USB identity\nfoo="bar"\n')
        self.before={str(p.relative_to(self.root)):p.read_bytes()for p in self.root.rglob('*')if p.is_file()}
        binary=bytearray(120);binary[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',binary,18,183)
        struct.pack_into('<Q',binary,32,64);struct.pack_into('<HH',binary,54,56,1);struct.pack_into('<I',binary,64,1)
        entries=[]
        for number,name in enumerate(sorted(installer.REQUIRED)):
            data=bytes(binary) if name=='sbin/init' else ('recovery '+name+'\n').encode()
            source='payload-'+str(number);(self.bundle/source).write_bytes(data)
            entries.append({'source':source,'path':name,'bytes':len(data),'sha256':installer.digest(data),'mode':0o755 if name=='sbin/init' or name.endswith('.sh') or '/rc.d/' in name else 0o644})
        self.manifest={'format':installer.FORMAT,'version':installer.VERSION,
                       'base_init_sha256':installer.sha(self.init),'files':entries}
        self.write_manifest()

    def tearDown(self):self.temp.cleanup()

    def write_manifest(self):
        p=self.bundle/'manifest.json';p.write_text(json.dumps(self.manifest))
        self.expected=installer.sha(p)

    def unchanged(self):
        for name,data in self.before.items():self.assertEqual((self.root/name).read_bytes(),data,name)

    def test_plan_is_read_only_and_places_activation_last(self):
        plan=installer.make_plan(self.root,self.bundle,self.expected)
        self.assertEqual(plan['files'][0]['path'],'sbin/init')
        self.assertEqual(plan['files'][-1]['path'],installer.LOADER)
        self.assertFalse(self.work.exists());self.assertFalse((self.root/'DaOS-recovery-ram').exists());self.unchanged()

    def test_complete_apply_and_rollback_restore_originals_and_absence(self):
        result=installer.apply(self.root,self.bundle,self.expected,self.work)
        self.assertEqual(result['status'],'installed-reboot-required')
        self.assertIn(('init_script="'+installer.HOOK+'"').encode(),(self.root/installer.LOADER).read_bytes())
        self.assertIn(b'foo="bar"',(self.root/installer.LOADER).read_bytes())
        self.assertTrue((self.root/'DaOS-recovery-ram').is_dir())
        result=installer.rollback(self.root,self.work)
        self.assertEqual(result['status'],'rolled-back');self.unchanged()
        self.assertFalse((self.root/'usr/local/libexec/DaOS-recovery/early_adb.py').exists())
        self.assertFalse((self.root/'DaOS-recovery-ram').exists())

    def test_fault_after_init_and_before_selector_restores_every_original(self):
        count=0
        def fail(path,data,mode):
            nonlocal count
            count+=1
            installer.atomic(path,data,mode)
            if count==2:raise OSError('injected after-write fault')
        with self.assertRaises(OSError):installer.apply(self.root,self.bundle,self.expected,self.work,fail)
        self.unchanged()
        receipt=json.loads((self.work/'deployment.json').read_text())
        self.assertEqual(receipt['status'],'failed-rolled-back')

    def test_fault_after_selector_publication_rolls_selector_back_first(self):
        def fail(path,data,mode):
            installer.atomic(path,data,mode)
            if path==self.root/installer.LOADER:raise OSError('post-publication fault')
        with self.assertRaises(OSError):installer.apply(self.root,self.bundle,self.expected,self.work,fail)
        self.unchanged()

    def test_bad_input_hash_fails_before_any_work_or_root_changes(self):
        p=self.bundle/self.manifest['files'][0]['source'];p.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):installer.apply(self.root,self.bundle,self.expected,self.work)
        self.assertFalse(self.work.exists());self.unchanged()

    def test_payload_mutation_during_install_is_detected_and_rolled_back(self):
        changed=False
        def mutate(path,data,mode):
            nonlocal changed
            installer.atomic(path,data,mode)
            if not changed:
                changed=True
                entry=next(e for e in self.manifest['files']if e['path']!='sbin/init')
                (self.bundle/entry['source']).write_bytes(b'modified after preflight')
        with self.assertRaisesRegex(ValueError,'Payload changed'):
            installer.apply(self.root,self.bundle,self.expected,self.work,mutate)
        self.unchanged()

    def test_dynamic_or_wrong_architecture_init_is_rejected(self):
        entry=next(e for e in self.manifest['files']if e['path']=='sbin/init')
        p=self.bundle/entry['source'];original=p.read_bytes()
        for offset,value in ((18,62),(64,3),(64,2)):
            data=bytearray(original)
            struct.pack_into('<H' if offset==18 else '<I',data,offset,value)
            p.write_bytes(data);entry['sha256']=installer.digest(data);self.write_manifest()
            with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):
                installer.apply(self.root,self.bundle,self.expected,self.work)
        self.assertFalse(self.work.exists());self.unchanged()

    def test_original_init_identity_cannot_be_rebaselined_after_install(self):
        installer.apply(self.root,self.bundle,self.expected,self.work)
        with self.assertRaisesRegex(ValueError,'baseline'):installer.make_plan(self.root,self.bundle,self.expected)
        installer.rollback(self.root,self.work);self.unchanged()

    @unittest.skipIf(os.name=='nt','POSIX symlink enforcement tested in WSL/FreeBSD')
    def test_redirected_destination_parent_is_rejected_before_work_creation(self):
        external=self.base/'outside';external.mkdir();(self.root/'usr').symlink_to(external,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Redirected'):installer.apply(self.root,self.bundle,self.expected,self.work)
        self.assertFalse(self.work.exists());self.assertEqual(list(external.iterdir()),[])

    @unittest.skipIf(os.name=='nt','POSIX symlink enforcement tested in WSL/FreeBSD')
    def test_redirected_bundle_source_is_rejected_even_if_content_matches(self):
        entry=self.manifest['files'][0];source=self.bundle/entry['source']
        outside=self.base/'source';outside.write_bytes(source.read_bytes());source.unlink();source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Redirected'):installer.apply(self.root,self.bundle,self.expected,self.work)
        self.assertFalse(self.work.exists());self.unchanged()

    def test_rollback_refuses_corrupt_backup_before_restoring_anything(self):
        installer.apply(self.root,self.bundle,self.expected,self.work)
        (self.work/'backup/sbin/init').write_bytes(b'bad backup')
        current=self.init.read_bytes();selector=(self.root/installer.LOADER).read_bytes()
        with self.assertRaisesRegex(ValueError,'backup changed'):installer.rollback(self.root,self.work)
        self.assertEqual(self.init.read_bytes(),current);self.assertEqual((self.root/installer.LOADER).read_bytes(),selector)

    def test_rollback_preserves_external_file_edits(self):
        installer.apply(self.root,self.bundle,self.expected,self.work)
        p=self.root/'usr/local/libexec/DaOS-recovery/early_adb.py';p.write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError,'externally changed'):installer.rollback(self.root,self.work)
        self.assertEqual(p.read_bytes(),b'user edit')

    def test_manifest_cannot_install_arbitrary_file_or_override_loader(self):
        for name in ('etc/passwd',installer.LOADER,'../outside'):
            self.manifest['files'][0]['path']=name;self.write_manifest()
            with self.subTest(name=name),self.assertRaises(ValueError):installer.make_plan(self.root,self.bundle,self.expected)

    def test_unrelated_existing_init_script_is_not_silently_replaced(self):
        (self.root/installer.LOADER).write_text('init_script="/old/boot-hook"\n')
        with self.assertRaisesRegex(ValueError,'reconciled'):installer.make_plan(self.root,self.bundle,self.expected)

    def test_live_mode_requires_freebsd_root_and_explicit_switch(self):
        with patch.object(installer.sys,'argv',['install.py','plan','--root','/','--bundle',str(self.bundle),'--sha256',self.expected]):
            with self.assertRaisesRegex(ValueError,'--live'):installer.main()

    @unittest.skipIf(os.name=='nt','POSIX root spellings')
    def test_dotdot_root_spelling_cannot_bypass_live_guard(self):
        self.assertEqual(installer.unredirected('/tmp/..'),Path('/'))
        with patch.object(installer.sys,'argv',['install.py','plan','--root','/tmp/..','--bundle',str(self.bundle),'--sha256',self.expected]):
            with self.assertRaisesRegex(ValueError,'--live'):installer.main()

    @unittest.skipUnless(hasattr(os,'chflags') and hasattr(stat,'SF_IMMUTABLE'),'Native BSD flags required')
    def test_native_schg_is_preserved_on_apply_and_rollback(self):
        flags=stat.SF_IMMUTABLE|getattr(stat,'UF_NODUMP',0)
        os.chflags(self.init,flags)
        try:
            installer.apply(self.root,self.bundle,self.expected,self.work)
            self.assertEqual(self.init.stat().st_flags,flags)
            installer.rollback(self.root,self.work)
            self.unchanged();self.assertEqual(self.init.stat().st_flags,flags)
        finally:os.chflags(self.init,0)

    @unittest.skipUnless(hasattr(os,'chflags') and hasattr(stat,'SF_IMMUTABLE'),'Native BSD flags required')
    def test_native_schg_restored_if_rename_fails_after_clear(self):
        flags=stat.SF_IMMUTABLE|getattr(stat,'UF_NODUMP',0)
        os.chflags(self.init,flags)
        try:
            with patch.object(installer.os,'replace',side_effect=PermissionError('injected rename failure')):
                with self.assertRaises(PermissionError):installer.atomic(self.init,b'new init',0o755)
            self.unchanged();self.assertEqual(self.init.stat().st_flags,flags)
            self.assertFalse(self.init.with_name('init.recovery-tmp').exists())
        finally:os.chflags(self.init,0)

    @unittest.skipUnless(hasattr(os,'chflags') and hasattr(stat,'SF_IMMUTABLE'),'Native BSD flags required')
    def test_securelevel_denied_clear_leaves_original_untouched(self):
        os.chflags(self.init,stat.SF_IMMUTABLE)
        try:
            with patch.object(installer.os,'chflags',side_effect=PermissionError('securelevel denies clear')),patch.object(installer.os,'replace') as replace:
                with self.assertRaises(PermissionError):installer.atomic(self.init,b'new init',0o755)
            replace.assert_not_called();self.unchanged()
            self.assertEqual(self.init.stat().st_flags,stat.SF_IMMUTABLE)
        finally:os.chflags(self.init,0)


if __name__=='__main__':unittest.main()

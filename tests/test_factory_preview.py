"""Private fixture checks for factory builder failure boundaries; no disk I/O."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'system/freebsd/raspberrypi/build_factory_preview.py'
spec=importlib.util.spec_from_file_location('factory_preview_under_test',SOURCE)
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class FactoryPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.work=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()

    def test_empty_mountpoint_refuses_existing_content(self):
        target=self.work/'root';target.mkdir();(target/'keep').write_text('owned elsewhere')
        with self.assertRaises(ValueError):builder.empty_mountpoint(target)
        self.assertEqual((target/'keep').read_text(),'owned elsewhere')

    def test_bound_helper_hash_failure_prevents_import(self):
        marker=self.work/'executed';source=self.work/'helper.py'
        source.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\n')
        with self.assertRaises(ValueError):builder.load_bound_module(source,'0'*64,'untrusted_fixture')
        self.assertFalse(marker.exists())

    def test_private_key_scan_distinguishes_complete_body_from_binary_marker(self):
        body=base64.b64encode(b'public synthetic fixture, not a credential '*3)
        (self.work/'fixture.pem').write_bytes(b'-----BEGIN PRIVATE KEY-----\n'+body+b'\n-----END PRIVATE KEY-----\n')
        (self.work/'magic.bin').write_bytes(b'-----BEGIN PRIVATE KEY-----\x00format token\x00-----END PRIVATE KEY-----')
        result=builder.privacy_scan(self.work)
        findings={entry['path']:entry for entry in result['private_key_pem_files']}
        self.assertEqual(findings['fixture.pem']['complete_base64_private_key_blocks'],1)
        self.assertEqual(findings['magic.bin']['complete_base64_private_key_blocks'],0)
        self.assertFalse(result['secret_contents_exported'])

    def fixture_runner(self,provider='/dev/md7p2',unmount_succeeds=True):
        state={'mounted':True,'commands':[]}
        target=self.work/'root'
        def run(command,timeout=60,check=True):
            state['commands'].append(command)
            result={'command':command,'returncode':0,'stdout':'','stderr':''}
            if command==['/sbin/mount','-p']:
                result['stdout']=provider+' '+str(target)+' ufs rw 0 0\n' if state['mounted'] else ''
            elif command[0]=='/sbin/umount':
                if unmount_succeeds:state['mounted']=False
                else:result['returncode']=1;result['stderr']='fixture EBUSY'
            elif command[:2]==['/sbin/mdconfig','-d']:
                self.assertFalse(state['mounted'],'must not detach an attached filesystem')
            else:raise AssertionError(command)
            return result
        return target,state,run

    def test_cleanup_discovers_mount_even_when_command_did_not_report_success(self):
        target,state,run=self.fixture_runner()
        with patch.object(builder,'run',run):
            builder.release_owned('md7',[('/dev/md7p2',target)],self.work,'mount-timeout')
        self.assertIn(['/sbin/umount',str(target)],state['commands'])
        self.assertIn(['/sbin/mdconfig','-d','-u','md7'],state['commands'])
        self.assertTrue(json.loads((self.work/'mount-timeout-cleanup-001.json').read_text())['released'])

    def test_cleanup_never_detaches_after_busy_unmount(self):
        target,state,run=self.fixture_runner(unmount_succeeds=False)
        with patch.object(builder,'run',run),self.assertRaises(RuntimeError):
            builder.release_owned('md7',[('/dev/md7p2',target)],self.work,'busy')
        self.assertFalse(any(command[0]=='/sbin/mdconfig' for command in state['commands']))
        self.assertFalse(json.loads((self.work/'busy-cleanup-001.json').read_text())['released'])

    def test_cleanup_refuses_foreign_provider(self):
        target,state,run=self.fixture_runner(provider='/dev/not-owned')
        with patch.object(builder,'run',run),self.assertRaises(RuntimeError):
            builder.release_owned('md7',[('/dev/md7p2',target)],self.work,'foreign')
        self.assertFalse(any(command[0] in ('/sbin/mdconfig','/sbin/umount') for command in state['commands']))

    def test_cleanup_preserves_original_exception_and_records_leak(self):
        target,state,run=self.fixture_runner(unmount_succeeds=False)
        with patch.object(builder,'run',run),self.assertRaisesRegex(ValueError,'original build failure'):
            try:raise ValueError('original build failure')
            finally:builder.release_owned('md7',[('/dev/md7p2',target)],self.work,'original')
        self.assertTrue((self.work/'original-cleanup-001.json').is_file())

    def profile_args(self,profile=None):
        path=self.work/'profile.json'
        path.write_text(json.dumps(builder.WALNUT_PROFILE if profile is None else profile),encoding='utf8')
        return SimpleNamespace(input_profile=path,input_profile_sha256=builder.sha(path))

    def test_bound_walnut_profile_retains_both_source_hashes_and_fixed_geometry(self):
        args=self.profile_args();profile=builder.input_profile(args)
        self.assertEqual(profile,builder.WALNUT_PROFILE)
        self.assertEqual(profile['raw_bytes'],3254820864)
        self.assertTrue(profile['source_core_preinstalled'])
        args.input_profile.write_bytes(args.input_profile.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'SHA256 changed'):builder.input_profile(args)

    def test_profile_cannot_authorize_other_inputs_paths_or_application_versions(self):
        for field,value in [('source_xz_sha256','0'*64),('source_raw_sha256','0'*64),('raw_bytes',1),
                            ('image_basename','../foreign.img'),('export_basename','/foreign.img.xz'),
                            ('application_version','camera-experience-007'),('source_core_preinstalled',1)]:
            profile=dict(builder.WALNUT_PROFILE);profile[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):builder.input_profile(self.profile_args(profile))

    def test_profile_requires_hash_and_rejects_duplicate_fields(self):
        args=self.profile_args();args.input_profile_sha256=None
        with self.assertRaises(ValueError):builder.input_profile(args)
        args.input_profile.write_text('{"format":"unused",'+json.dumps(builder.WALNUT_PROFILE)[1:])
        args.input_profile_sha256=builder.sha(args.input_profile)
        with self.assertRaisesRegex(ValueError,'Duplicate'):builder.input_profile(args)

    def test_work_requires_matching_profile_and_uses_bound_image_name(self):
        args=self.profile_args();args.work=self.work/'walnut-preview-002';args.work.mkdir()
        image=args.work/builder.WALNUT_PROFILE['image_basename'];image.write_bytes(b'fixture!')
        with patch.object(builder,'WORK_PARENT',self.work),patch.object(builder,'RAW_BYTES',8):
            with self.assertRaisesRegex(ValueError,'Prepared input profile'):builder.owned_work(args)
            builder.save(args.work/'input-profile.json',builder.WALNUT_PROFILE)
            self.assertEqual(builder.owned_work(args),(args.work,image))
            (args.work/'input-profile.json').write_text(json.dumps(builder.LEGACY_PROFILE))
            with self.assertRaisesRegex(ValueError,'Work input profile changed'):builder.owned_work(args)

    def test_inherited_core_cannot_enter_legacy_reinstallation(self):
        args=self.profile_args()
        with patch.object(builder,'owned_work') as own,self.assertRaisesRegex(ValueError,'do not reinstall'):
            builder.update_core(args)
        own.assert_not_called()

    def test_inherited_core_checks_actual_files_flags_and_predecessor_metadata(self):
        root=self.work/'root';names=builder.CORE_PATHS|{'usr/local/libexec/daos-usb-adb','boot/kernel/kernel','etc/fstab'}
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('fixture '+name).encode())
        expected={name:builder.sha(root/name) for name in names};metadata=root/'usr/share/DaOS';metadata.mkdir(parents=True)
        builder.save(metadata/'factory-preview.json',{'format':'DaOS-factory-preview-profile-v1','application_version':'camera-experience-005','core_files':expected})
        builder.save(metadata/'factory-preview-app-manifest.json',{'version':'camera-experience-005'})
        original_stat=Path.stat;original_link=Path.is_symlink;original_readlink=builder.os.readlink
        links={root/'etc'/name:'/DaOS-identity/'+name for name in ('hostid','machine-id')}
        flags={'value':0x20000}
        def stat_with_fixture_flags(path,*args,**kwargs):
            if path==root/'sbin/init':return SimpleNamespace(st_flags=flags['value'])
            return original_stat(path,*args,**kwargs)
        with patch.object(builder,'CORE_INIT',expected['sbin/init']),patch.object(builder,'HELPER',expected['usr/local/libexec/daos-usb-adb']),patch.object(builder,'KERNEL',expected['boot/kernel/kernel']), \
             patch.object(builder.stat,'SF_IMMUTABLE',0x20000,create=True),patch.object(Path,'stat',stat_with_fixture_flags), \
             patch.object(Path,'is_symlink',lambda path:path in links or original_link(path)), \
             patch.object(builder.os,'readlink',side_effect=lambda path,*a,**kw:links[path] if path in links else original_readlink(path,*a,**kw)):
            receipt=builder.inherited_core(root,builder.WALNUT_PROFILE)
            self.assertEqual(receipt['core_files'],expected)
            self.assertEqual(receipt['predecessor_metadata']['factory-preview.json']['sha256'],builder.sha(metadata/'factory-preview.json'))
            flags['value']=0
            with self.assertRaisesRegex(ValueError,'immutable init'):builder.inherited_core(root,builder.WALNUT_PROFILE)
            flags['value']=0x20000
            (root/'boot/kernel/kernel').write_bytes(b'tampered kernel')
            with self.assertRaisesRegex(ValueError,'Inherited core verification'):builder.inherited_core(root,builder.WALNUT_PROFILE)

    def test_metadata_preserves_exact_predecessor_bytes_and_publishes_new_current(self):
        path=self.work/'factory-preview.json';old=b'{ "historical": true }\n';path.write_bytes(old)
        history=self.work/'history'
        with patch.object(builder,'sync_directory') as sync:
            receipt=builder.publish_metadata(path,{'project_name':'Project Walnut'},history,old)
        self.assertEqual(json.loads(path.read_text()),{'project_name':'Project Walnut'})
        self.assertEqual((history/receipt['history_file']).read_bytes(),old)
        self.assertEqual(receipt['previous_sha256'],hashlib.sha256(old).hexdigest())
        self.assertEqual(receipt['current_sha256'],builder.sha(path))
        self.assertEqual(sync.call_count,2)

    def test_metadata_rename_failure_preserves_current_and_history(self):
        path=self.work/'factory-preview.json';old=b'{"old":true}\n';path.write_bytes(old);history=self.work/'history'
        with patch.object(builder,'sync_directory'),patch.object(builder.os,'replace',side_effect=OSError('injected rename')):
            with self.assertRaisesRegex(OSError,'injected rename'):builder.publish_metadata(path,{'new':True},history,old)
        self.assertEqual(path.read_bytes(),old)
        self.assertEqual([p.read_bytes() for p in history.iterdir()],[old])
        self.assertFalse(list(self.work.glob('*.tmp')))

    def test_changed_current_or_historical_metadata_is_never_overwritten(self):
        path=self.work/'factory-preview.json';old=b'{"old":true}\n';path.write_bytes(b'external');history=self.work/'history'
        with self.assertRaisesRegex(ValueError,'Predecessor metadata changed'):builder.publish_metadata(path,{},history,old)
        self.assertFalse(history.exists());self.assertEqual(path.read_bytes(),b'external')
        path.write_bytes(old);history.mkdir()
        backup=history/(path.stem+'-'+hashlib.sha256(old).hexdigest()+'.json');backup.write_bytes(b'external history')
        with self.assertRaisesRegex(ValueError,'Metadata history changed'):builder.publish_metadata(path,{},history,old)
        self.assertEqual(path.read_bytes(),old);self.assertEqual(backup.read_bytes(),b'external history')

if __name__=='__main__':unittest.main()

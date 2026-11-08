"""Private fixture checks for factory builder failure boundaries; no disk I/O."""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
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

if __name__=='__main__':unittest.main()

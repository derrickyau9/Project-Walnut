"""Pending metadata is not a completed runtime or a payload checksum claim."""
import argparse,copy,importlib,json,os
from pathlib import Path
import sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
IMAGE=Path(__file__).resolve().parents[1]/'system/freebsd/image'
sys.path.insert(0,str(IMAGE))
exporter=importlib.import_module('export_runtime_metadata')
collector=importlib.import_module('collect_runtime')


class ExportTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','native metadata function imports POSIX account modules')
    def test_file_query_is_limited_to_validated_selected_packages(self):
        failed=SimpleNamespace(returncode=1,stdout='',stderr='bounded failure')
        with patch.object(exporter.subprocess,'run',return_value=failed) as run:
            with self.assertRaises(RuntimeError):exporter.declared_candidates({'one':{},'two':{}},['two','one'])
        command=run.call_args.args[0]
        self.assertEqual(['query','-e'],command[1:3])
        self.assertEqual('%n = "one" || %n = "two"',command[3]);self.assertNotIn('-a',command)
        with patch.object(exporter.subprocess,'run') as run:
            with self.assertRaises(ValueError):exporter.declared_candidates({},['bad" || true'])
            run.assert_not_called()

    def test_batched_lists_include_empty_and_deduplicate(self):
        with patch.object(exporter,'query',return_value='one\tlib\none\tlib\ntwo\t\n'):
            self.assertEqual({'one':['lib'],'two':[]},exporter.package_lists('%dn',{'one':{},'two':{}}))
        with patch.object(exporter,'query',return_value='unknown\tx\n'):
            with self.assertRaises(ValueError):exporter.package_lists('%dn',{'one':{}})

    def test_non_freebsd_refuses_before_metadata_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'DaOS-runtime-export'
            args=argparse.Namespace(architecture='arm64',output=str(output))
            with patch.object(exporter.platform,'system',return_value='Linux'),patch.object(exporter,'inventory') as inventory:
                with self.assertRaises(ValueError):exporter.export(args)
                inventory.assert_not_called()
            self.assertFalse(output.exists())

    def test_provisional_checkpoint_is_create_only_and_cannot_claim_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'checkpoint.json'
            record={'format':'DaOS-freebsd-runtime-provisional-v1','completed_runtime':False,
                    'status':'payload-hashed-awaiting-elf','entries':[{'sha256':'a'*64}]}
            self.assertEqual(64,len(collector.write_provisional(target,record)))
            self.assertEqual(record,json.loads(target.read_text()))
            with self.assertRaises(FileExistsError):collector.write_provisional(target,record)
            with self.assertRaises(ValueError):collector.write_provisional(Path(tmp)/'bad',dict(record,completed_runtime=True))

    @unittest.skipUnless(os.name=='posix','source export models POSIX ownership')
    def test_export_checks_source_packages_but_does_not_hash_or_promote_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);payload=root/'DaOS-runtime-old'/'payload';target=payload/'usr/local/bin/app'
            target.parent.mkdir(parents=True);target.write_bytes(b'content');target.chmod(0o644)
            payload.parent.chmod(0o700);payload.chmod(0o755)
            output=root/'DaOS-runtime-metadata'
            args=argparse.Namespace(architecture='arm64',profile='camera-runtime-v1',package=['one'],payload=str(payload),output=str(output))
            package={'name':'one','version':'1','origin':'test/one','abi':'FreeBSD:15:aarch64','repository':'test','package_checksum':'declared-package'}
            row={'path':'usr/local/bin/app','type':'file','bytes':7,'mode':0o644,'uid':0,'gid':0,'packages':['one'],'declared_checksum':'2$pending'}
            with patch.object(exporter,'require_freebsd'),patch.object(exporter.platform,'system',return_value='FreeBSD'),\
                 patch.object(exporter.platform,'release',return_value='15.1-RELEASE-p6'),\
                 patch.object(exporter.platform,'machine',return_value='arm64'),\
                 patch.object(exporter,'inventory',side_effect=[{'one':copy.deepcopy(package)},{'one':copy.deepcopy(package)}]),\
                 patch.object(exporter,'package_lists',side_effect=[{'one':[]},{'one':['BSD']}]),\
                 patch.object(exporter,'declared_candidates',return_value=[row]),\
                 patch.object(exporter,'base_manifest',return_value=[]),\
                 patch.object(exporter,'loader_directories',return_value=([],[])),\
                 patch.object(exporter.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b'')) as run:
                result=exporter.export(args)
            self.assertTrue(result['installed_checksums_verified'])
            self.assertFalse(result['payload_content_verified']);self.assertFalse(result['completed_runtime'])
            self.assertNotIn('sha256',result['entries'][0])
            self.assertEqual('arm64',result['source_host']['machine'])
            self.assertTrue((output/'provisional.json').is_file())
            self.assertFalse((output/'runtime.json').exists());self.assertFalse((output/'payload').exists())
            self.assertEqual(b'content',target.read_bytes())
            self.assertEqual(['/usr/local/sbin/pkg','check','-s','one'],run.call_args.args[0])

    def test_failed_source_checksum_check_creates_no_pending_or_completed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'DaOS-runtime-failed'
            args=argparse.Namespace(architecture='arm64',profile='camera-runtime-v1',package=['one'],payload='unused',output=str(output))
            packages={'one':{'abi':'FreeBSD:15:aarch64'}}
            with patch.object(exporter,'require_freebsd'),patch.object(exporter.platform,'release',return_value='15.1'),\
                 patch.object(exporter,'disable_core_dumps'),\
                 patch.object(exporter,'inventory',return_value=packages),\
                 patch.object(exporter,'package_lists',return_value={'one':[]}),\
                 patch.object(exporter,'declared_candidates') as candidates,\
                 patch.object(exporter.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=b'',stderr=b'bad checksum')):
                with self.assertRaisesRegex(ValueError,'pkg check'):exporter.export(args)
                candidates.assert_not_called()
            self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()

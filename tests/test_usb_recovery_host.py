"""Exercise host recovery transfer against a file-backed fake ADB endpoint."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
host=load('usb_recovery_host',ROOT/'system/adb/recovery/host.py')
flash=load('usb_recovery_device',ROOT/'system/adb/recovery/flash.py')


class FakeUSB:
    def __init__(self,root):
        self.root=root;self.target=root/'root.img';self.target.write_bytes(b'O'*4096)
        self.backend=flash.FileBackend(root,{'root':self.target})
        self.identity={'disk_uuid':'disk-uuid','uuid':'partition-uuid','start_lba':40,'sectors':8,'index':2}
        self.prepares=0;self.uploads=[];self.applies=[];self.cleanups=[];self.probes=0
        self.lost_reply=False;self.torn=False;self.bad_receipt=False;self.changed_identity=False

    def probe(self,role,offset,count):
        self.probes+=1
        result=flash.probe(role,offset,count,self.backend);result['target_identity']=dict(self.identity)
        if self.changed_identity and self.probes>1:result['target_identity']['disk_uuid']='different-card'
        return result

    def prepare(self):self.prepares+=1
    def upload(self,source,target):
        self.uploads.append((str(source),target));shutil.copyfile(source,self.root/Path(target).name)
    def apply(self,path):
        request=json.loads((self.root/Path(path).name).read_text());self.applies.append(copy.deepcopy(request))
        if self.torn:
            self.torn=False;e=request['extents'][0]
            with self.target.open('r+b') as f:
                f.seek(e['target_offset']);f.write((self.root/request['image']).read_bytes()[:512])
            raise ConnectionError('USB reset during first extent')
        result=flash.execute(request,self.backend,apply=True);result['target_identity']=dict(self.identity)
        if self.lost_reply:
            self.lost_reply=False;raise ConnectionError('USB disconnected after completed write, reply lost')
        if self.bad_receipt:result['request_sha256']='f'*64
        return result
    def cleanup(self,paths):
        self.cleanups.append(list(paths))
        for name in paths:(self.root/Path(name).name).unlink(missing_ok=True)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-host-qa-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);device=self.root/'device';device.mkdir();self.usb=FakeUSB(device)
        self.image=self.root/'image.bin';self.image.write_bytes(b'A'*1024+b'B'*1024+b'C'*1024+b'D'*1024)
        self.journal=self.root/'journal'
        self.chunk=patch.object(host,'CHUNK',1024);self.chunk.start();self.addCleanup(self.chunk.stop)
    def write(self,resume=False):return host.write_image(self.usb,'root',self.image,self.journal,resume=resume,progress=lambda _:None)
    def events(self):return [json.loads(p.read_text()) for p in sorted((self.journal/'events').glob('*.json'))]

    def test_full_image_transfers_only_bounded_chunks_and_checks_receipts(self):
        result=self.write()
        self.assertEqual(result['status'],'complete');self.assertEqual(result['verified_bytes'],4096)
        self.assertEqual(self.usb.target.read_bytes(),self.image.read_bytes())
        self.assertEqual(len(self.usb.applies),4)
        self.assertTrue(all(r['image_bytes']==1024 for r in self.usb.applies))
        self.assertEqual(self.events()[-1]['event'],'complete')

    def test_wrong_full_image_size_rejected_before_prepare_upload_or_apply(self):
        self.image.write_bytes(b'X'*1024)
        with self.assertRaisesRegex(ValueError,'exactly match'):self.write()
        self.assertEqual(self.usb.prepares,0);self.assertFalse(self.usb.uploads);self.assertFalse(self.usb.applies)
        self.assertFalse(self.journal.exists())

    def test_normal_second_run_reuses_same_image_and_skips_all_completed_chunks(self):
        self.write();manifest=(self.journal/'manifest.json').read_bytes();events=[p.read_bytes() for p in sorted((self.journal/'events').glob('*.json'))]
        self.usb.applies.clear();self.usb.uploads.clear()
        result=self.write(resume=True)
        self.assertEqual(result['verified_bytes'],4096);self.assertFalse(self.usb.applies);self.assertFalse(self.usb.uploads)
        self.assertEqual((self.journal/'manifest.json').read_bytes(),manifest)
        self.assertEqual([p.read_bytes() for p in sorted((self.journal/'events').glob('*.json'))][:len(events)],events)

    def test_lost_reply_stops_without_retry_then_explicit_resume_probes_and_skips(self):
        self.usb.lost_reply=True
        with self.assertRaisesRegex(ConnectionError,'reply lost'):self.write()
        self.assertEqual(len(self.usb.applies),1)
        self.assertEqual(self.events()[-1]['event'],'interrupted')
        self.write(resume=True)
        self.assertEqual(len(self.usb.applies),4)
        self.assertTrue(any(e['event']=='verified-already-present' and e['offset']==0 for e in self.events()))
        self.assertEqual(self.usb.target.read_bytes(),self.image.read_bytes())

    def test_explicit_resume_replans_torn_chunk_from_new_observed_preimage(self):
        self.usb.torn=True
        with self.assertRaises(ConnectionError):self.write()
        first=self.usb.applies[0]
        self.write(resume=True)
        second=self.usb.applies[1]
        self.assertNotEqual(first['operation'],second['operation'])
        self.assertNotEqual(first['extents'][0]['before_sha256'],second['extents'][0]['before_sha256'])
        self.assertEqual(self.usb.target.read_bytes(),self.image.read_bytes())

    def test_changed_source_cannot_resume_original_manifest(self):
        self.write();self.image.write_bytes(b'Z'*4096);self.usb.applies.clear()
        with self.assertRaisesRegex(ValueError,'Resume image'):self.write(resume=True)
        self.assertFalse(self.usb.applies)

    def test_changed_card_during_transfer_stops_before_upload(self):
        self.usb.changed_identity=True
        with self.assertRaisesRegex(ValueError,'identity changed'):self.write()
        self.assertFalse(self.usb.uploads);self.assertFalse(self.usb.applies)

    def test_wrong_receipt_never_records_complete_or_automatically_retries(self):
        self.usb.bad_receipt=True
        with self.assertRaisesRegex(ValueError,'exact completed'):self.write()
        self.assertEqual(len(self.usb.applies),1)
        self.assertEqual(self.events()[-1]['event'],'interrupted')
        self.assertFalse(any(e['event']=='complete' for e in self.events()))

    def test_source_mutation_after_hash_is_caught_before_first_upload(self):
        original_probe=self.usb.probe
        def probe(*args):
            result=original_probe(*args)
            if self.usb.probes==1:self.image.write_bytes(b'Z'*4096)
            return result
        with patch.object(self.usb,'probe',side_effect=probe):
            with self.assertRaisesRegex(ValueError,'chunk changed'):self.write()
        self.assertFalse(self.usb.uploads);self.assertFalse(self.usb.applies)

    def test_upload_normalizes_remote_permissions_after_windows_push(self):
        usb=object.__new__(host.USB)
        with patch.object(usb,'run') as run,patch.object(usb,'remote') as remote:
            usb.upload(self.image,'/DaOS-recovery-ram/flash/incoming/test.bin')
        run.assert_called_once_with('push',str(self.image),'/DaOS-recovery-ram/flash/incoming/test.bin',timeout=180)
        remote.assert_called_once_with(['/bin/chmod','600','/DaOS-recovery-ram/flash/incoming/test.bin'])

    def test_untrusted_probe_response_is_rejected_before_prepare(self):
        for field,value in [('role','state'),('target_offset',512),('before_sha256','bad'),('target_identity',{}),('partition_bytes',True)]:
            original=self.usb.probe
            def probe(*args,field=field,value=value):
                r=original(*args);r[field]=value;return r
            with self.subTest(field=field),patch.object(self.usb,'probe',side_effect=probe),self.assertRaises(ValueError):self.write()
        self.assertEqual(self.usb.prepares,0)

    def test_cli_restricts_roles_and_defaults_to_dedicated_server(self):
        self.assertEqual(host.PORT,5053);self.assertEqual(host.REMOTE,'/usr/local/libexec/DaOS-recovery/flash.py')
        with self.assertRaisesRegex(ValueError,'root/state'):host.write_image(self.usb,'photos',self.image,self.journal)
        with self.assertRaisesRegex(ValueError,'exact DaOS USB serial'):
            host.USB(self.root,'127.0.0.1:5555')

    def test_rescue_accepts_flushed_arming_receipt_before_expected_disconnect_without_retry(self):
        usb=object.__new__(host.USB)
        usb.connector=SimpleNamespace(adb_call=lambda *args,**kw:None)
        usb.adb=Path('adb.exe');usb.port=5053;usb.environment={};usb.selector=['-t','12']
        response=SimpleNamespace(returncode=1,stdout='{"status":"RAM-rescue-nextboot-armed","reboot_requested":true}\nShutdown NOW!\n',stderr='error: closed\n')
        with patch.object(usb,'check') as check,patch.object(usb.connector,'adb_call',return_value=response) as call:
            result=usb.rescue()
        check.assert_called_once();call.assert_called_once()
        self.assertFalse(result['request_retried']);self.assertFalse(result['reboot_confirmed'])
        self.assertIn('/usr/local/libexec/DaOS-recovery/boot-rescue.py reboot',call.call_args.args[-1])

    def test_rescue_without_receipt_is_uncertain_and_never_retried(self):
        usb=object.__new__(host.USB)
        usb.connector=SimpleNamespace(adb_call=lambda *args,**kw:None)
        usb.adb=Path('adb.exe');usb.port=5053;usb.environment={};usb.selector=['-t','12']
        response=SimpleNamespace(returncode=1,stdout='',stderr='device offline')
        with patch.object(usb,'check'),patch.object(usb.connector,'adb_call',return_value=response) as call:
            with self.assertRaisesRegex(ConnectionError,'not retried'):usb.rescue()
        call.assert_called_once()

    def test_normal_uploads_small_utility_and_never_retries_after_disconnect(self):
        usb=object.__new__(host.USB);usb.adb=Path('adb.exe');usb.port=5053;usb.environment={};usb.selector=['-t','12']
        usb.connector=SimpleNamespace(adb_call=lambda *args,**kw:None)
        response=SimpleNamespace(returncode=1,stdout='{"status":"normal-boot-selected","marker_bytes_written":1,"reboot_requested":true}\n',stderr='closed')
        with patch.object(usb,'probe',return_value={}),patch.object(host,'validate_probe'),patch.object(usb,'prepare'),patch.object(usb,'upload') as upload,patch.object(usb,'check'),patch.object(usb.connector,'adb_call',return_value=response) as call:
            result=usb.normal()
        upload.assert_called_once_with(host.RETURN_NORMAL,host.RAM+'/incoming/return-normal.py')
        call.assert_called_once();self.assertFalse(result['request_retried']);self.assertFalse(result['reboot_confirmed'])

    def test_normal_accepts_zero_marker_writes_only_when_absence_is_explicitly_confirmed(self):
        for absent,accepted in ((True,True),(False,False),(None,False)):
            usb=object.__new__(host.USB);usb.adb=Path('adb.exe');usb.port=5053;usb.environment={};usb.selector=['-t','12']
            usb.connector=SimpleNamespace(adb_call=lambda *args,**kw:None)
            value={'status':'normal-boot-selected','marker_bytes_written':0,'reboot_requested':True,'marker_absent':absent}
            response=SimpleNamespace(returncode=1,stdout=json.dumps(value)+'\n',stderr='closed')
            with self.subTest(absent=absent),patch.object(usb,'probe',return_value={}),patch.object(host,'validate_probe'),patch.object(usb,'prepare'),patch.object(usb,'upload'),patch.object(usb,'check'),patch.object(usb.connector,'adb_call',return_value=response):
                if accepted:self.assertEqual(usb.normal()['marker_bytes_written'],0)
                else:
                    with self.assertRaisesRegex(ConnectionError,'No normal-boot'):usb.normal()


if __name__=='__main__':unittest.main()

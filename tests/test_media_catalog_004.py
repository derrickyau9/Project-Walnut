"""004 library authority, whole-library catalog and durable media jobs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PyQt5.QtGui import QGuiApplication,QImage
from PyQt5.QtTest import QTest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
from camera.camerad.service import CameraService
from camera.camerad.native_client import NativeClient
from media_catalog import MediaCatalog,CatalogError
from task_service import TaskService,TaskCancelled
from media_workspace import PhotosModel,MediaImageProvider


class LibraryAuthorityTests(unittest.TestCase):
    def exercise(self,factory):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service=factory(root);library=service.describe_library()['library_id']
            lease=service.acquire('library-test');photo=str(uuid.uuid4())
            request=dict(request_id=photo,token=lease['token'],control_epoch=lease['epoch'],sensor_mode='sim-small',requested_exposure_us=10000,requested_analogue_gain=1.,output_format='dng')
            record=service.capture(request);end=time.monotonic()+5
            while record['status'] not in {'complete','failed','cancelled','unknown'}:
                if time.monotonic()>end:self.fail('fixture capture timed out')
                time.sleep(.01);record=service.query_capture(photo)
            self.assertEqual('complete',record['status'])
            while service.get_state()['camera_state']!='idle':time.sleep(.005)
            service.release(lease['token'],lease['epoch'])
            original=root/'photos'/(photo+'.dng');digest=hashlib.sha256(original.read_bytes()).hexdigest()
            assets=service.list_library_assets();self.assertEqual(photo,assets['items'][0]['photo_uuid'])
            imported=service.import_library_asset(str(original))
            self.assertNotEqual(photo,imported['photo_uuid']);self.assertEqual(photo,imported['original_request_uuid'])
            self.assertEqual('import',imported['origin_kind'])
            with service.open_library_asset(imported['photo_uuid']) as stream:self.assertEqual(digest,hashlib.file_digest(stream,'sha256').hexdigest())
            self.assertEqual(2,len(service.list_library_changes()['items']))
            service.set_library_asset_flags(photo,protected=True,trashed=False)
            skipped=service.purge_library_asset(photo,str(uuid.uuid4()),'PERMANENTLY_DELETE')
            self.assertIn('skipped',skipped['purge_result']);self.assertTrue(original.exists())
            service.set_library_asset_flags(photo,protected=False,trashed=True)
            with service.open_capture(photo):
                skipped=service.purge_library_asset(photo,str(uuid.uuid4()),'PERMANENTLY_DELETE')
                self.assertEqual('skipped_in_use',skipped['purge_result'])
            purged=service.purge_library_asset(photo,str(uuid.uuid4()),'PERMANENTLY_DELETE')
            self.assertEqual('purged',purged['state']);self.assertFalse(original.exists())
            self.assertEqual('complete',service.query_capture(photo)['status'])
            service.close();service=factory(root)
            self.assertEqual(library,service.describe_library()['library_id'])
            self.assertEqual('purged',service.get_library_asset(photo)['state'])
            self.assertEqual('complete',service.query_capture(photo)['status'])
            self.assertEqual(1,len(service.list_captures()['items']))
            self.assertEqual(2,len(service.list_library_assets()['items']))
            service.close()
    def test_reference_lifecycle_import_pins_purge_and_restart(self):self.exercise(CameraService)
    def test_native_lifecycle_import_pins_purge_and_restart(self):
        binary=ROOT/'build/DaOS_Camera_004/native-media/Release/hf-camerad.exe'
        if not binary.is_file():self.skipTest('004 native authority has not been built')
        self.exercise(lambda root:NativeClient(root,command=[str(binary)]))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.catalog=MediaCatalog(self.root)
        self.library=str(uuid.uuid4());self.catalog.active_library=self.library
        for index in range(125):
            photo=str(uuid.uuid4());asset={'photo_uuid':photo,'original_request_uuid':photo,'origin_kind':'capture','state':'active','change_seq':index+1,'width':128,'height':96,'media_type':'image/x-adobe-dng','extension':'.dng','filename':'photo-%03d.dng'%index,'timestamp':1000000+index,'byte_count':25000,'sha256':hashlib.sha256(str(index).encode()).hexdigest()}
            self.catalog.upsert_asset(self.library,asset)
    def tearDown(self):self.temp.cleanup()
    def test_whole_library_pages_filters_snapshot_and_revision_conflict(self):
        pages=[];cursor=''
        while True:
            page=self.catalog.page(self.library,cursor=cursor,limit=48);pages.extend(page['items']);cursor=page['next_cursor']
            if not cursor:break
        self.assertEqual(125,len(pages));self.assertEqual(125,len({r['photo_uuid'] for r in pages}))
        first,last=pages[0],pages[-1]
        self.catalog.mutate(self.library,last['photo_uuid'],{'rating':5})
        self.assertEqual(last['photo_uuid'],self.catalog.page(self.library,{'filter':'rated'})['items'][0]['photo_uuid'])
        selection=self.catalog.create_selection(self.library,{'filter':'all'})
        self.assertEqual(125,selection['count'])
        self.catalog.mutate(self.library,first['photo_uuid'],{'edits':dict(first['edits'],exposure=1)})
        frozen=self.catalog.selection_rows(selection['selection_id'],0,1)[0][1]
        self.assertEqual(0,frozen['edit_revision'])
        with self.assertRaises(CatalogError):self.catalog.mutate(self.library,first['photo_uuid'],{'rating':3},expected_revision=0)
        self.assertEqual(2,len(self.catalog.history(self.library,first['photo_uuid'])))
        self.catalog.flush_outbox();self.assertTrue((self.catalog.sidecars/self.library/(first['photo_uuid']+'.daos.json')).is_file())
    def test_portable_metadata_has_no_paths_or_identity_and_snapshot_backup(self):
        photo=self.catalog.page(self.library)['items'][0]
        self.catalog.mutate(self.library,photo['photo_uuid'],{'rating':4})
        document=self.catalog.exportPortableMetadata();serialized=json.dumps(document)
        self.assertNotIn(self.library,serialized);self.assertNotIn(photo['photo_uuid'],serialized);self.assertNotIn(str(self.root),serialized)
        self.assertEqual(125,len(document['items']))
        result=self.catalog.restorePortableMetadata(document);self.assertEqual([],result['conflicts'])
        backup=self.root/'snapshot.sqlite3';self.catalog.backup(str(backup));self.assertTrue(backup.is_file())

    def test_portable_restore_is_atomic_strict_and_crash_ledger_idempotent(self):
        first,second=self.catalog.page(self.library)['items'][:2]
        self.catalog.mutate(self.library,second['photo_uuid'],{'rating':2})
        def item(photo,rating):return {'sha256':photo['fingerprint'],'rating':rating,'protected':False,'trashed':False,'edits':photo['edits']}
        document={'format':'daos.portable-photo-metadata.v1','items':[item(first,5),item(second,4)]}
        result=self.catalog.restorePortableMetadata(document,operation_id=uuid.uuid4().hex)
        self.assertFalse(result['valid']);self.assertEqual(0,result['applied'])
        self.assertEqual(0,self.catalog.photo(self.library,first['photo_uuid'])['rating'])
        document['items'][1]['edits']=dict(second['edits'],wb=[.05,1,1])
        with self.assertRaises(CatalogError):self.catalog.restorePortableMetadata(document)
        self.assertEqual(0,self.catalog.photo(self.library,first['photo_uuid'])['rating'])
        document['items']=[item(first,5)];operation=uuid.uuid4().hex
        self.assertTrue(MediaCatalog.validatePortableDocument(document)['valid'])
        self.assertTrue(self.catalog.validatePortableMetadata(document)['valid'])
        self.assertEqual(1,self.catalog.restorePortableMetadata(document,operation_id=operation)['applied'])
        self.assertTrue(self.catalog.portableRestoreCommitted(operation))
        self.assertTrue(self.catalog.restorePortableMetadata(document,operation_id=operation)['already_committed'])
        document['items'][0]['rating']=4
        with self.assertRaises(CatalogError):self.catalog.restorePortableMetadata(document,operation_id=operation)
        self.assertEqual(5,self.catalog.photo(self.library,first['photo_uuid'])['rating'])
        for invalid in ([[0,0],[.5,.8],[.75,.7],[1,1]],[[0,.1],[1,1]],[[0,0]]+[[i/32,i/32]for i in range(1,33)]):
            with self.assertRaises(CatalogError):MediaCatalog.validateEditParameters({'curve':invalid})

    def test_ten_thousand_rows_bounded_model_provider_and_disk_cache(self):
        app=QGuiApplication.instance() or QGuiApplication([])
        with self.catalog.db(write=True)as db:
            template=dict(db.execute('SELECT * FROM photos LIMIT 1').fetchone());columns=[key for key in template if key!='seq']
            for index in range(10000-125):
                row=dict(template);photo=str(uuid.uuid4());descriptor=json.loads(row['data_json']);descriptor.update(photo_uuid=photo,original_request_uuid=photo,filename='large-%05d.dng'%index,timestamp=2000000+index)
                row.update(photo_uuid=photo,data_json=json.dumps(descriptor),captured_at=2000000+index,filename=descriptor['filename'])
                db.execute('INSERT INTO photos('+','.join(columns)+') VALUES('+','.join('?'for key in columns)+')',[row[key]for key in columns])
        model=PhotosModel();cursor='';count=0;maximum=0
        while True:
            page=self.catalog.page(self.library,cursor=cursor);model.replace(page['items']);maximum=max(maximum,model.rowCount());count+=model.rowCount();cursor=page['next_cursor']
            if not cursor:break
        self.assertEqual(10000,count);self.assertEqual(48,maximum)
        snapshot=self.catalog.create_selection(self.library);self.assertEqual(10000,snapshot['count']);self.assertLessEqual(len(self.catalog.selection_rows(snapshot['selection_id'],0,17)),17)
        provider=MediaImageProvider();image=QImage(1024,1024,QImage.Format_RGB32);image.fill(0xff778899)
        for index in range(40):provider.publish('tile',image)
        memory=sum(i.sizeInBytes()for i in provider.images.values());self.assertLessEqual(memory,24*1024*1024);self.assertLessEqual(len(provider.images),96)
        asset=self.catalog.page(self.library)['items'][0]
        for index in range(12):
            path=self.catalog.cache/('cache-%02d.jpg'%index);path.write_bytes(b'x'*4096)
            bounds=self.catalog.touch_derivative(str(index),asset,'thumb',path,max_bytes=16384,max_items=8)
        self.assertLessEqual(bounds['bytes'],16384);self.assertLessEqual(bounds['items'],8)
        report=ROOT/'build/DaOS_Camera_004/library-bounds.json';report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps({'indexed':10000,'model_maximum_rows':maximum,'selection_rows_requested':17,'provider_peak_after_40_tiles_bytes':memory,'provider_limit_bytes':24*1024*1024,'disk_cache_default_bytes':256*1024*1024,'disk_cache_default_items':2048},indent=2))


class DurableTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance()or QGuiApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.client=CameraService(self.root/'camera')
        lease=self.client.acquire('media-job-test');self.photo=str(uuid.uuid4())
        record=self.client.capture(dict(request_id=self.photo,token=lease['token'],control_epoch=lease['epoch'],sensor_mode='sim-small',requested_exposure_us=10000,requested_analogue_gain=1.,output_format='dng'))
        while record['status']not in {'complete','failed'}:time.sleep(.01);record=self.client.query_capture(self.photo)
        while self.client.get_state()['camera_state']!='idle':time.sleep(.005)
        self.client.release(lease['token'],lease['epoch']);self.catalog=MediaCatalog(self.root/'state');self.catalog.sync(self.client)
        self.asset=self.catalog.page(self.catalog.active_library)['items'][0];self.tasks=TaskService(self.catalog,lambda:self.client,enabled=False)
        self.destination=self.root/'export';self.destination.mkdir()
    def tearDown(self):self.tasks.close();self.client.close();self.temp.cleanup()
    def job(self,identity):
        with self.catalog.db()as db:return dict(db.execute('SELECT * FROM jobs WHERE job_id=?',(identity,)).fetchone())
    def execute(self,kind='export_original',destination=None):
        identity=self.tasks.enqueue(kind,{'options':{},'destination':str(destination or self.destination)},[self.asset]);return identity,self.tasks._execute(self.job(identity))
    def test_owned_publish_resume_after_crash_does_not_reopen_original(self):
        class Crash(BaseException):pass
        identity=self.tasks.enqueue('export_original',{'options':{},'destination':str(self.destination)},[self.asset])
        self.tasks._after_publish=lambda job,path:(_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):self.tasks._execute(self.job(identity))
        output=self.destination/(self.photo+'.dng');self.assertTrue(output.is_file());digest=hashlib.sha256(output.read_bytes()).hexdigest()
        self.tasks.close();self.tasks=TaskService(self.catalog,lambda:self.client)
        self.assertEqual('paused',self.job(identity)['status'])
        with patch.object(self.client,'open_library_asset',side_effect=AssertionError('published source must not recopy')):
            self.tasks.resume(identity);end=time.monotonic()+5
            while self.job(identity)['status']not in {'complete','failed'}and time.monotonic()<end:QTest.qWait(10)
        self.assertEqual('complete',self.job(identity)['status']);self.assertEqual(digest,hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertTrue(output.with_name(output.name+'.daos.json').exists());self.assertEqual('complete',self.client.query_capture(self.photo)['status'])
    def test_ordinary_same_name_even_identical_is_failed_and_disk_full_never_succeeds(self):
        source=self.client.root/'photos'/(self.photo+'.dng');target=self.destination/source.name;target.write_bytes(source.read_bytes())
        identity,result=self.execute();self.assertEqual('failed',result['status']);self.assertEqual('failed',self.job(identity)['status'])
        target.unlink()
        with patch('task_service.tempfile.NamedTemporaryFile',side_effect=OSError(28,'No space left on device')):
            identity,result=self.execute()
        self.assertEqual('failed',result['status']);self.assertFalse(target.exists());self.assertTrue(source.exists())
    def test_native_develop_publish_crash_recovers_owned_output_without_reprocessing(self):
        if not(ROOT/'build/DaOS_Camera_004/processing/worker-build/Release/hf-imager.exe').is_file():self.skipTest('native image worker not built')
        class Crash(BaseException):pass
        identity=self.tasks.enqueue('export_jpeg',{'options':{},'destination':str(self.destination)},[self.asset])
        self.tasks._after_publish=lambda job,path:(_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):self.tasks._execute(self.job(identity))
        output=self.destination/(self.photo+'.jpg');self.assertTrue(output.is_file());digest=hashlib.sha256(output.read_bytes()).hexdigest()
        self.tasks.close();self.tasks=TaskService(self.catalog,lambda:self.client)
        with patch('image_processing.run_job',side_effect=AssertionError('native processor must not rerun published frozen output')):
            self.tasks.resume(identity);end=time.monotonic()+5
            while self.job(identity)['status']not in {'complete','failed'}and time.monotonic()<end:QTest.qWait(10)
        self.assertEqual('complete',self.job(identity)['status']);self.assertEqual(digest,hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertTrue(output.with_name(output.name+'.daos-manifest.json').exists())
    def test_publication_cancel_cleans_only_owned_temporary_and_admission_is_closed(self):
        identity=self.tasks.enqueue('export_original',{'options':{},'destination':str(self.destination)},[self.asset])
        staged=self.catalog.root/'job-staging'/identity/self.photo/'attempt'/'output.dng';staged.parent.mkdir(parents=True);staged.write_bytes(b'x'*2000000)
        final=self.destination/'cancel.dng';publication=self.tasks._publication(staged,final)
        receipt={'publication_stage':'prepared','result':{},'publications':[publication],'input':self.asset,'options':{}}
        self.tasks._save_prepared(identity,self.photo,receipt);checks=0
        def check(job):
            nonlocal checks
            checks+=1
            if checks>=3:raise TaskCancelled()
        with patch.object(self.tasks,'_check',side_effect=check):
            with self.assertRaises(TaskCancelled):self.tasks._publish_prepared(identity,self.asset,{},receipt)
        self.assertFalse(final.exists());self.assertEqual([],list(self.destination.glob('.daos-*')));self.assertTrue(staged.exists())
        self.assertFalse(self.tasks.quiescent());self.tasks.setAdmissionBlocked(True,'维护测试')
        with self.assertRaisesRegex(CatalogError,'维护测试'):self.tasks.enqueue('export_original',{},[self.asset])
        with self.assertRaisesRegex(CatalogError,'维护测试'):self.tasks.submit_processing('analyze',{'sources':[self.asset]},str(self.destination/'a.json'))
        self.tasks.setAdmissionBlocked(False);self.assertIsInstance(self.tasks.enqueue('export_original',{},[self.asset]),str)


if __name__=='__main__':unittest.main()

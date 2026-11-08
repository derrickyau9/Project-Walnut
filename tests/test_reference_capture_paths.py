"""Observed namespace replacement cannot write/clean foreign photo storage."""
from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest import mock
import uuid

from camera.camerad.service import CameraError,CameraService,DEFAULTS,TERMINAL
from camera.camerad import service as implementation


class ReferenceCapturePathTests(unittest.TestCase):
    def link(self,destination,path):
        try: path.symlink_to(destination,target_is_directory=True)
        except OSError as exc: self.skipTest('directory symlink unavailable: '+str(exc))

    def wait(self,service,rid):
        end=time.monotonic()+5
        while time.monotonic()<end:
            record=service.query_capture(rid)
            if record['status'] in TERMINAL and service.active is None: return record
            time.sleep(.01)
        self.fail('reference capture exceeded bounded test deadline')

    def test_startup_rejects_linked_photo_directory_before_journal_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; data.mkdir(); outside=root/'foreign'; outside.mkdir()
            sentinel=outside/'owner.txt'; sentinel.write_bytes(b'owner content')
            self.link(outside,data/'photos')
            with self.assertRaises(CameraError) as failure: CameraService(data)
            self.assertEqual(failure.exception.code,'storage_error')
            self.assertFalse((data/'journal.sqlite3').exists())
            self.assertEqual(sentinel.read_bytes(),b'owner content')
            self.assertEqual(sorted(p.name for p in outside.iterdir()),['owner.txt'])

    def test_redirect_after_admission_never_creates_an_outside_photo_or_cleans_foreign_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; outside=root/'foreign'; outside.mkdir()
            service=CameraService(data); lease=service.acquire('path-test'); rid=str(uuid.uuid4())
            try:
                service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch'],fault='delay'))
                (data/'photos').rename(data/'original-photos'); self.link(outside,data/'photos')
                sentinel=outside/'owner.txt'; sentinel.write_bytes(b'owner content')
                record=self.wait(service,rid)
                self.assertEqual(record['status'],'failed',record)
                self.assertIsNone(record['file_path'])
                self.assertEqual(sorted(p.name for p in outside.iterdir()),['owner.txt'])
                self.assertEqual(sentinel.read_bytes(),b'owner content')
                with self.assertRaises(CameraError) as denied:
                    service.capture(dict(DEFAULTS,request_id=str(uuid.uuid4()),token=lease['token'],control_epoch=lease['epoch']))
                self.assertEqual(denied.exception.code,'storage_error')
            finally:
                if service.active is not None: self.wait(service,rid)
                service.close()

    def test_post_publication_replacement_stays_unknown_and_preserves_both_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; outside=root/'foreign'; outside.mkdir()
            # Establish symlink capability before a capture thread uses it.
            probe=root/'probe'; self.link(outside,probe); probe.unlink()
            service=CameraService(data); lease=service.acquire('late-path-test'); rid=str(uuid.uuid4())
            publish=implementation._publish_capture_file
            def redirect(temporary,final):
                publish(temporary,final)
                (data/'photos').rename(data/'original-photos')
                (data/'photos').symlink_to(outside,target_is_directory=True)
                sentinel=outside/temporary.parent.name; sentinel.mkdir()
                (sentinel/'owner.txt').write_bytes(b'foreign staging')
            try:
                with mock.patch.object(implementation,'_publish_capture_file',redirect):
                    service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                    record=self.wait(service,rid)
                self.assertEqual(record['status'],'unknown',record)
                self.assertIsNone(record['file_path'])
                self.assertTrue((data/'original-photos'/(rid+'.dng')).is_file())
                self.assertFalse(list(outside.glob('*.dng')))
                self.assertEqual(next(outside.glob('*/owner.txt')).read_bytes(),b'foreign staging')
            finally:
                if service.active is not None: self.wait(service,rid)
                service.close()

    def test_staging_directory_replacement_during_generation_cannot_write_foreign_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; outside=root/'foreign'; outside.mkdir()
            probe=root/'probe'; self.link(outside,probe); probe.unlink()
            service=CameraService(data); lease=service.acquire('stage-test'); rid=str(uuid.uuid4())
            generate=implementation.generate_bayer
            def redirect(*args,**kwargs):
                staging=next((data/'photos').glob('.capture-*'))
                staging.rename(staging.with_name(staging.name+'-original'))
                staging.symlink_to(outside,target_is_directory=True)
                return generate(*args,**kwargs)
            try:
                with mock.patch.object(implementation,'generate_bayer',redirect):
                    service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                    self.assertEqual(self.wait(service,rid)['status'],'failed')
                self.assertEqual(list(outside.iterdir()),[])
                self.assertFalse(list((data/'photos').glob('*.dng')))
            finally:
                if service.active is not None: self.wait(service,rid)
                service.close()

    def test_replaced_produced_file_never_becomes_a_completed_photograph(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'camera'; service=CameraService(data); lease=service.acquire('file-test'); rid=str(uuid.uuid4())
            check=service._check_capture_staging
            replaced=[]
            def replace(staging,identity,temporary=None,snapshot=None):
                # Inject after the writer's handle closes; Windows correctly
                # refuses renaming an open Python file, unlike POSIX.
                if temporary is not None and snapshot is not None and not replaced:
                    temporary.rename(temporary.with_name('original-produced.dng'))
                    temporary.write_bytes(b'not a valid DNG')
                    replaced.append(True)
                return check(staging,identity,temporary,snapshot)
            try:
                with mock.patch.object(service,'_check_capture_staging',side_effect=replace):
                    service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                    record=self.wait(service,rid)
                self.assertEqual(record['status'],'failed',record)
                self.assertEqual(replaced,[True])
                self.assertIsNone(record['file_path'])
                self.assertFalse(list((data/'photos').glob('*.dng')))
            finally:
                if service.active is not None: self.wait(service,rid)
                service.close()

    def test_in_place_pixel_change_with_restored_timestamp_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'camera'; service=CameraService(data); lease=service.acquire('bytes-test'); rid=str(uuid.uuid4())
            digest=CameraService._capture_digest; modified=[]; tampered=[]
            def tamper(stream,maximum):
                result=digest(stream,maximum)
                if not modified:
                    modified.append(True); info=os.fstat(stream.fileno())
                    stream.seek(-2,os.SEEK_END); value=stream.read(2)
                    stream.seek(-2,os.SEEK_END); stream.write(bytes([value[0]^1,value[1]])); stream.flush()
                    os.utime(stream.name,ns=(info.st_atime_ns,info.st_mtime_ns)); stream.seek(0)
                    tampered.append(True)
                return result
            try:
                with mock.patch.object(CameraService,'_capture_digest',side_effect=tamper):
                    service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                    record=self.wait(service,rid)
                self.assertEqual(record['status'],'failed',record)
                self.assertEqual(tampered,[True])
                self.assertIsNone(record['file_path'])
                self.assertFalse(list((data/'photos').glob('*.dng')))
            finally:
                if service.active is not None: self.wait(service,rid)
                service.close()


if __name__=='__main__': unittest.main()

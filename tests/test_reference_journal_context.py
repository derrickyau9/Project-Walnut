"""Valid bindings cannot conceal corrupted capture/request/status context."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid

from camera.calibration import create_binding,create_uncalibrated_package
from camera.camerad.service import CameraError,CameraService,DEFAULTS,TERMINAL


class ReferenceJournalContextTests(unittest.TestCase):
    def capture(self,path):
        service=CameraService(path);lease=service.acquire('journal-test');rid=str(uuid.uuid4())
        service.bind_calibration(lease['token'],lease['epoch'],create_binding(create_uncalibrated_package('HF-Sim','none','1')))
        service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
        end=time.monotonic()+5
        while service.active is not None and time.monotonic()<end:time.sleep(.01)
        self.assertEqual(service.query_capture(rid)['status'],'complete')
        return service,lease,rid

    def damage(self,row,kind):
        request=json.loads(row[0]);record=json.loads(row[1]);status=row[2]
        if kind=='identity':record['request_id']=str(uuid.uuid4())
        elif kind=='actual':record['actual_exposure_us']=0
        elif kind=='status':record['status']='accepted'
        elif kind=='request':request['requested_exposure_us']+=100
        elif kind=='legacy-calibration':
            for field in ('capture_record_version','calibration_binding','hardware_calibrated','pixel_corrections_applied'):
                record.pop(field,None)
            record['calibration_id']='physical-measured-calibration'
        return json.dumps(request),json.dumps(record),status

    def test_live_query_fences_context_mismatch_without_rewrite_or_new_capture(self):
        for kind in ('identity','actual','status','request','legacy-calibration'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                service,lease,rid=self.capture(tmp)
                try:
                    row=service.db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?',(rid,)).fetchone()
                    damaged=self.damage(row,kind)
                    service.db.execute('UPDATE captures SET request_json=?,record_json=?,status=? WHERE request_id=?',(*damaged,rid));service.db.commit()
                    before=hashlib.sha256((service.photos/(rid+'.dng')).read_bytes()).hexdigest()
                    with self.assertRaises(CameraError) as failed:service.query_capture(rid)
                    self.assertEqual(failed.exception.code,'journal_error')
                    with self.assertRaises(CameraError):service.capture(dict(DEFAULTS,request_id=str(uuid.uuid4()),token=lease['token'],control_epoch=lease['epoch']))
                    self.assertEqual(tuple(service.db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?',(rid,)).fetchone()),damaged)
                    self.assertEqual(before,hashlib.sha256((service.photos/(rid+'.dng')).read_bytes()).hexdigest())
                    self.assertEqual(len(list(service.photos.glob('*.dng'))),1)
                finally:service.close()

    def test_restart_refuses_context_mismatch_before_epoch_recovery_changes_and_releases_lock(self):
        for kind in ('identity','actual','status','request','legacy-calibration'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                service,_,rid=self.capture(tmp);service.close();database=Path(tmp)/'journal.sqlite3'
                with closing(sqlite3.connect(database)) as db,db:
                    original=tuple(db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?',(rid,)).fetchone())
                    damaged=self.damage(original,kind)
                    db.execute('UPDATE captures SET request_json=?,record_json=?,status=? WHERE request_id=?',(*damaged,rid))
                    meta=dict(db.execute('SELECT key,value FROM meta'))
                with self.assertRaises(CameraError) as failed:CameraService(tmp)
                self.assertEqual(failed.exception.code,'journal_error')
                with closing(sqlite3.connect(database)) as db,db:
                    self.assertEqual(dict(db.execute('SELECT key,value FROM meta')),meta)
                    self.assertEqual(tuple(db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?',(rid,)).fetchone()),damaged)
                    db.execute('UPDATE captures SET request_json=?,record_json=?,status=? WHERE request_id=?',(*original,rid))
                reopened=CameraService(tmp)
                try:self.assertEqual(reopened.query_capture(rid)['status'],'complete')
                finally:reopened.close()


if __name__=='__main__':unittest.main()

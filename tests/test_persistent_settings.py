"""Actual reference settings commits, process death, corruption and fencing."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from camera.camerad.service import CameraError,CameraService,DEFAULTS,SETTINGS_FORMAT,TERMINAL


class PersistentReferenceSettingsTests(unittest.TestCase):
    def test_settings_survive_abrupt_process_exit_and_explicit_capture_does_not_replace_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            code='import os,sys; from camera.camerad.service import CameraService; s=CameraService(sys.argv[1]); l=s.acquire("settings"); s.configure(l["token"],l["epoch"],{"requested_exposure_us":22300,"requested_analogue_gain":2.5}); os._exit(0)'
            process=subprocess.run([sys.executable,'-B','-c',code,tmp],capture_output=True,text=True,timeout=20)
            self.assertEqual(process.returncode,0,process.stderr)
            service=CameraService(tmp); lease=service.acquire('capture'); rid=str(uuid.uuid4())
            try:
                self.assertEqual(service.get_state()['settings']['requested_exposure_us'],22300)
                self.assertEqual(service.get_state()['settings']['requested_analogue_gain'],2.5)
                service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                deadline=time.monotonic()+10
                while service.active is not None and time.monotonic()<deadline: time.sleep(.01)
                self.assertEqual(service.query_capture(rid)['actual_exposure_us'],10000)
            finally: service.close()
            service=CameraService(tmp)
            try:
                self.assertEqual(service.get_state()['settings']['requested_exposure_us'],22300)
                self.assertEqual(len(list(service.photos.glob('*.dng'))),1)
            finally: service.close()

    def test_old_journal_without_either_setting_key_migrates_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=Path(tmp)/'journal.sqlite3'
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.executemany('INSERT INTO meta VALUES(?,?)',[('authority','python'),('epoch','7')])
            service=CameraService(tmp)
            try:
                self.assertEqual(service.get_state()['settings'],DEFAULTS)
                self.assertEqual(service.db.execute("SELECT value FROM meta WHERE key='settings_format'").fetchone()[0],SETTINGS_FORMAT)
            finally: service.close()

    def test_invalid_or_partially_missing_persisted_settings_are_preserved_and_release_lock(self):
        samples=[None,'{bad','{"format":"hf-device-settings-v1","format":"other","settings":{}}',
            json.dumps({'format':'future','settings':DEFAULTS}),
            json.dumps({'format':SETTINGS_FORMAT,'settings':dict(DEFAULTS,sensor_mode=[])}),
            json.dumps({'format':SETTINGS_FORMAT,'settings':dict(DEFAULTS,requested_exposure_us=True)}),
            json.dumps({'format':SETTINGS_FORMAT,'settings':dict(DEFAULTS,requested_analogue_gain=1.0)}).replace('1.0','1e999'),
            ' '*16385]
        for index,raw in enumerate(samples):
            with self.subTest(case=index),tempfile.TemporaryDirectory() as tmp:
                service=CameraService(tmp); service.close(); path=Path(tmp)/'journal.sqlite3'
                with closing(sqlite3.connect(path)) as db, db:
                    if raw is None: db.execute("DELETE FROM meta WHERE key='settings'")
                    else: db.execute("UPDATE meta SET value=? WHERE key='settings'",(raw,))
                    before=dict(db.execute('SELECT key,value FROM meta'))
                with self.assertRaises(CameraError) as failed: CameraService(tmp)
                self.assertEqual(failed.exception.code,'journal_error')
                with closing(sqlite3.connect(path)) as db, db:
                    self.assertEqual(dict(db.execute('SELECT key,value FROM meta')),before)
                    db.execute("INSERT OR REPLACE INTO meta VALUES('settings',?)",(CameraService._settings_document(DEFAULTS),))
                repaired=CameraService(tmp)
                repaired.close()

    def test_failed_or_ambiguous_commit_fences_capture_without_claiming_new_memory(self):
        for after_commit in (False,True):
            with self.subTest(after_commit=after_commit),tempfile.TemporaryDirectory() as tmp:
                service=CameraService(tmp); lease=service.acquire('settings'); real=service.db
                class Failure:
                    def __getattr__(self,name): return getattr(real,name)
                    def commit(self):
                        if after_commit: real.commit()
                        raise sqlite3.OperationalError('injected commit acknowledgement failure')
                service.db=Failure()
                with self.assertRaises(CameraError): service.configure(lease['token'],lease['epoch'],{'requested_exposure_us':23400})
                self.assertEqual(service.settings,DEFAULTS)
                self.assertFalse(service.get_state()['journal_healthy'])
                with self.assertRaises(CameraError): service.capture(dict(DEFAULTS,request_id=str(uuid.uuid4()),token=lease['token'],control_epoch=lease['epoch']))
                service.db=real;service.close()
                restarted=CameraService(tmp)
                try:
                    self.assertEqual(restarted.settings['requested_exposure_us'],23400 if after_commit else 10000)
                    self.assertEqual(list(restarted.photos.glob('*.dng')),[])
                finally: restarted.close()


if __name__=='__main__':unittest.main()

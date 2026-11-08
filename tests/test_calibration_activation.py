"""Real authority activation/pinning; synthetic statistics never alter RAW."""
import copy
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
import uuid

from camera.calibration import create_synthetic_package,create_binding,CalibrationError
from camera.camerad.service import CameraError,CameraService,DEFAULTS,TERMINAL
from camera.dng.inspect import inspect_dng
from simulation.bayer import generate_bayer

ROOT=Path(__file__).resolve().parents[1]


class ReferenceCalibrationActivationTests(unittest.TestCase):
    authority='python'

    def open(self,path):
        if self.authority=='native':
            from camera.camerad.native_client import NativeClient
            return NativeClient(path,command=[os.environ['HF_NATIVE_DAEMON']],timeout_seconds=15)
        return CameraService(path)

    def fixture(self,version='1'):
        return create_binding(create_synthetic_package('HF-Sim','software-statistics',version,width=128,height=96))

    def wait(self,service,rid):
        end=time.monotonic()+10
        while time.monotonic()<end:
            record=service.query_capture(rid)
            if record['status'] in TERMINAL:
                # The reference clears its worker just after the final commit.
                if self.authority=='python' and service.active is not None: time.sleep(.01);continue
                return record
            time.sleep(.01)
        self.fail('capture did not settle')

    def test_bound_capture_pins_original_snapshot_through_selection_restart_and_id_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=self.open(tmp); lease=service.acquire('calibration-test'); rid=str(uuid.uuid4())
            binding=self.fixture(); original=copy.deepcopy(binding)
            request=dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch'])
            try:
                selected=service.bind_calibration(lease['token'],lease['epoch'],binding)
                self.assertEqual(selected['calibration_id'],binding['binding_id'])
                self.assertIs(selected['hardware_calibrated'],False)
                binding['package_payload']+='tampered caller object'
                service.capture(request); record=self.wait(service,rid)
                self.assertEqual(record['status'],'complete',record)
                self.assertEqual(record['capture_record_version'],2)
                self.assertEqual(record['calibration_binding'],original)
                self.assertIs(record['pixel_corrections_applied'],False)
                path=Path(record['file_path']); old=path.stat().st_mtime_ns,path.read_bytes()
                self.assertIn(original['binding_id'].encode(),old[1])
                structure=inspect_dng(path)
                pixels=[]
                for offset,length in zip(structure['strip_offsets'],structure['strip_byte_counts']):
                    pixels.extend(struct.unpack('<'+'H'*(length//2),old[1][offset:offset+length]))
                self.assertEqual(pixels,list(generate_bayer(128,96)))
                service.clear_calibration(lease['token'],lease['epoch'])
                self.assertEqual(service.capture(request)['calibration_binding'],original)
                newer=self.fixture('2');service.bind_calibration(lease['token'],lease['epoch'],newer)
                self.assertEqual(old,(path.stat().st_mtime_ns,path.read_bytes()))
            finally:service.close()
            service=self.open(tmp)
            try:
                self.assertEqual(service.get_state()['calibration']['binding_id'],newer['binding_id'])
                self.assertEqual(service.query_capture(rid)['calibration_binding'],original)
                self.assertEqual(len(list((Path(tmp)/'photos').glob('*.dng'))),1)
            finally:service.close()

    def test_activation_requires_lease_and_idle_capture_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=self.open(tmp);lease=service.acquire('owner');binding=self.fixture();rid=str(uuid.uuid4())
            try:
                with self.assertRaises(CameraError):service.bind_calibration('stale',lease['epoch'],binding)
                request=dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch'],fault='delay')
                service.capture(request)
                for operation in (lambda:service.bind_calibration(lease['token'],lease['epoch'],binding),
                                  lambda:service.clear_calibration(lease['token'],lease['epoch'])):
                    with self.assertRaises(CameraError) as failed:operation()
                    self.assertEqual(failed.exception.code,'camera_busy')
                record=self.wait(service,rid)
                self.assertEqual(record['calibration_id'],'synthetic-uncalibrated')
                self.assertNotIn('capture_record_version',record)
                bad=self.fixture();bad['hardware_calibrated']=True
                with self.assertRaises((CameraError,CalibrationError)):service.bind_calibration(lease['token'],lease['epoch'],bad)
                self.assertIsNone(service.get_state()['calibration']['binding_id'])
            finally:
                self.wait(service,rid);service.close()

    def test_damaged_active_snapshot_cannot_reset_on_restart_and_releases_authority_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=self.open(tmp);lease=service.acquire('owner');binding=self.fixture()
            service.bind_calibration(lease['token'],lease['epoch'],binding);service.close()
            database=Path(tmp)/'journal.sqlite3'
            with closing(sqlite3.connect(database)) as db,db:
                value=db.execute("SELECT value FROM meta WHERE key='calibration_binding'").fetchone()[0]
                damaged=value.replace(binding['package_checksum'],'sha256:'+'0'*64)
                db.execute("UPDATE meta SET value=? WHERE key='calibration_binding'",(damaged,))
                before=dict(db.execute('SELECT key,value FROM meta'))
            with self.assertRaises(CameraError):self.open(tmp)
            with closing(sqlite3.connect(database)) as db,db:
                self.assertEqual(dict(db.execute('SELECT key,value FROM meta')),before)
                db.execute("UPDATE meta SET value=? WHERE key='calibration_binding'",(value,))
            repaired=self.open(tmp)
            try:self.assertEqual(repaired.get_state()['calibration']['binding_id'],binding['binding_id'])
            finally:repaired.close()

    @unittest.skipUnless(importlib.util.find_spec('jsonschema'),'optional jsonschema validator')
    def test_bound_record_is_explicit_v2_and_does_not_silently_change_v1_schema(self):
        import jsonschema
        from referencing import Registry,Resource
        directory=ROOT/'daos/camera-contracts'
        legacy=json.loads((directory/'v1/capture-record.schema.json').read_text())
        bound=json.loads((directory/'v2/capture-record.schema.json').read_text())
        artifact=json.loads((directory/'v2/calibration-binding.schema.json').read_text())
        registry=Registry().with_resource(artifact['$id'],Resource.from_contents(artifact))
        validator=jsonschema.Draft202012Validator(bound,registry=registry)
        with tempfile.TemporaryDirectory() as tmp:
            service=self.open(tmp);lease=service.acquire('schema');binding=self.fixture();rid=str(uuid.uuid4())
            try:
                service.bind_calibration(lease['token'],lease['epoch'],binding)
                service.capture(dict(DEFAULTS,request_id=rid,token=lease['token'],control_epoch=lease['epoch']))
                record=self.wait(service,rid);validator.validate(record)
                self.assertTrue(list(jsonschema.Draft202012Validator(legacy).iter_errors(record)))
                forged=copy.deepcopy(record);forged['hardware_calibrated']=True
                self.assertTrue(list(validator.iter_errors(forged)))
            finally:service.close()


@unittest.skipUnless(os.environ.get('HF_NATIVE_DAEMON'),'explicit R9 native authority required')
class NativeCalibrationActivationTests(ReferenceCalibrationActivationTests):
    authority='native'


if __name__=='__main__':unittest.main()

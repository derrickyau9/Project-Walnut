"""Actual offline CLI sessions; read/export never take or replay a photograph."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]


class OfflineCameraCliTests(unittest.TestCase):
    authority='python'

    @classmethod
    def setUpClass(cls):
        if cls.authority!='native': return
        configured=os.environ.get('HF_NATIVE_DAEMON') or os.environ.get('HF_NATIVE_CAMERAD')
        filename='hf-camerad.exe' if os.name=='nt' else 'hf-camerad'
        cls.native_daemon=configured or next((str(p) for p in (ROOT/'build/native/Release'/filename,ROOT/'build/native'/filename) if p.is_file()),None)
        if cls.native_daemon is None: raise unittest.SkipTest('build the native authority or set HF_NATIVE_DAEMON')

    def cli(self,data,*args,without_workflow=False):
        command=[sys.executable,'-B','-m','camera.camerad']
        if without_workflow:
            # A real import blocker establishes independence from automation.
            code='import importlib.abc,runpy,sys\nclass NoWorkflow(importlib.abc.MetaPathFinder):\n def find_spec(self,name,*args):\n  if name=="daos" or name.startswith("daos."): raise ImportError("automation deliberately unavailable")\nsys.meta_path.insert(0,NoWorkflow()); sys.argv=["camera.camerad",*sys.argv[1:]]; runpy.run_module("camera.camerad",run_name="__main__")'
            command=[sys.executable,'-B','-c',code]
        command+=['--data-dir',str(data),'--authority',self.authority]
        if self.authority=='native': command+=['--native-daemon',self.native_daemon]
        return subprocess.run([*command,*args],cwd=ROOT,capture_output=True,text=True,timeout=30)

    def result(self,data,*args,without_workflow=False,code=0):
        reply=self.cli(data,*args,without_workflow=without_workflow)
        self.assertEqual(reply.returncode,code,reply.stdout+reply.stderr)
        return json.loads(reply.stdout.splitlines()[-1])

    def capture(self,data,rid):
        return self.result(data,'capture','--request-id',rid,'--exposure-us','12300',without_workflow=True)

    def test_capture_review_and_export_survive_cli_restarts_without_workflows_or_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; ids=sorted(str(uuid.uuid4()) for _ in range(2))
            for rid in ids: self.assertEqual(self.capture(data,rid)['status'],'complete')
            files={p.name:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in (data/'photos').glob('*.dng')}
            caps=self.result(data,'capabilities',without_workflow=True)
            self.assertTrue(caps['simulated'])
            status=self.result(data,'status',without_workflow=True)
            self.assertIsNone(status['control'])
            page=self.result(data,'list','--limit','1',without_workflow=True)
            self.assertEqual(len(page['items']),1)
            older=self.result(data,'list','--limit','1','--cursor',page['next_cursor'],without_workflow=True)
            self.assertEqual({page['items'][0]['request_id'],older['items'][0]['request_id']},set(ids))
            rid=ids[0]; asset=self.result(data,'asset',rid,without_workflow=True)
            destination=root/'export.dng'
            exported=self.result(data,'export',rid,'--output',str(destination),without_workflow=True)
            self.assertEqual(exported['byte_count'],asset['byte_count'])
            self.assertEqual(destination.read_bytes(),(data/'photos'/(rid+'.dng')).read_bytes())
            self.assertEqual(files,{p.name:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in (data/'photos').glob('*.dng')})
            self.assertIsNone(self.result(data,'status')['control'])

    def test_existing_export_and_missing_or_corrupt_source_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'camera'; rid=str(uuid.uuid4()); self.capture(data,rid)
            destination=root/'existing.dng'; destination.write_bytes(b'owner photograph')
            self.assertEqual(self.result(data,'export',rid,'--output',str(destination),code=2)['error']['code'],'export_conflict')
            self.assertEqual(destination.read_bytes(),b'owner photograph')
            missing=root/'missing.dng'
            self.result(data,'export',str(uuid.uuid4()),'--output',str(missing),code=2)
            self.assertFalse(missing.exists())
            source=data/'photos'/(rid+'.dng'); source.write_bytes(b'corrupt retained capture')
            corrupt=root/'corrupt-copy.dng'
            self.assertEqual(self.result(data,'export',rid,'--output',str(corrupt),code=2)['error']['code'],'capture_unavailable')
            self.assertFalse(corrupt.exists())
            self.assertEqual(len(list((data/'photos').glob('*.dng'))),1)


class BasicCameraBackendSelectionTests(unittest.TestCase):
    def test_invalid_backend_selection_cannot_silently_fall_back_or_create_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'untouched'
            reply=subprocess.run([sys.executable,'-B','-m','camera.camerad','--data-dir',str(data),
                '--native-daemon','unavailable-authority','capture'],cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertEqual(reply.returncode,2)
            self.assertIn('--native-daemon requires --authority native',reply.stderr)
            self.assertFalse(data.exists())


class NativeOfflineCameraCliTests(OfflineCameraCliTests):
    authority='native'


if __name__=='__main__': unittest.main()

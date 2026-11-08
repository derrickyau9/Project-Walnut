"""Offline release/operation checks, with no USB or native device actions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
import uuid
from tools.deliver_camera_004 import INSTALL_PS,RUNNER_PY

ROOT=Path(__file__).resolve().parents[1]
runner=types.ModuleType('operation_runner_qa');exec(compile(RUNNER_PY,'run_camera_update.py','exec'),runner.__dict__)

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/DaOS_Camera_004/delivery-logic-qa';base.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temporary.name)
        self.deployment=self.root/'camera-experience-004/deployment.json';self.deployment.parent.mkdir()
    def tearDown(self):self.temporary.cleanup()
    def prior(self,status):
        self.deployment.write_text(json.dumps({'format':'DaOS-camera-app-v1','version':'camera-experience-004','status':status}))
    def execute(self,action,code):
        operation=uuid.uuid4().hex
        result=runner.execute(action,operation,'1'*64,base=self.root,runner=lambda args,check:types.SimpleNamespace(returncode=code))
        self.assertEqual(operation,result['operation']);self.assertEqual(result,json.loads((self.root/('camera-experience-004-operation-'+operation+'.json')).read_text()))
        return result
    def test_old_ready_does_not_hide_a_new_failed_install(self):
        self.prior('ready');result=self.execute('Install',1);self.assertEqual('failed',result['status'])
    def test_old_rollback_does_not_hide_a_new_failed_rollback(self):
        self.prior('rolled-back');result=self.execute('Rollback',1);self.assertEqual('failed',result['status'])
    def test_matching_terminal_receipt_requires_successful_current_process(self):
        for action,status in [('Install','ready'),('Rollback','rolled-back')]:
            self.prior(status);result=self.execute(action,0)
            self.assertEqual(('complete',status,0),(result['status'],result['deployment_status'],result['exit_code']))
        self.prior('ready');self.assertEqual('failed',self.execute('Rollback',0)['status'])
    def test_operation_identity_cannot_be_reused(self):
        self.prior('ready');identity=uuid.uuid4().hex
        runner.execute('Install',identity,'1'*64,base=self.root,runner=lambda *args,**kwargs:types.SimpleNamespace(returncode=0))
        with self.assertRaises(ValueError):runner.execute('Install',identity,'1'*64,base=self.root)
    def bundle(self):
        archive=self.root/'camera-experience-004.tar.gz';archive.write_bytes(b'offline fixture')
        (self.root/'Install.ps1').write_text(INSTALL_PS,encoding='utf-8')
        (self.root/'release.json').write_text(json.dumps({'application_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
        self.deployment.unlink(missing_ok=True);self.deployment.parent.rmdir()
        lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name for path in sorted(self.root.iterdir()) if path.is_file()]
        (self.root/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n')
    def check(self):
        environment=dict(os.environ,PSModulePath=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/WindowsPowerShell/v1.0/Modules'))
        return subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'Install.ps1'),'-Action','Check'],cwd=ROOT,capture_output=True,text=True,timeout=15,env=environment)
    @unittest.skipUnless(os.name=='nt','Actual Windows PowerShell Check path')
    def test_check_is_relative_to_package_and_validates_all_listed_bytes(self):
        self.bundle();result=self.check();self.assertEqual(0,result.returncode,result.stderr)
        (self.root/'camera-experience-004.tar.gz').write_bytes(b'corrupted');self.assertNotEqual(0,self.check().returncode)
    @unittest.skipUnless(os.name=='nt','Actual Windows PowerShell Check path')
    def test_check_rejects_escape_malformed_and_duplicate_lines(self):
        self.bundle();sums=self.root/'SHA256SUMS.txt';original=sums.read_text()
        for suffix in ['0'*64+'  ../escape\n','malformed checksum\n',original.splitlines()[0]+'\n']:
            sums.write_text(original+suffix);self.assertNotEqual(0,self.check().returncode)
    @unittest.skipUnless(os.name=='nt','Actual Windows PowerShell Check path')
    def test_check_rejects_unlisted_files(self):
        self.bundle();(self.root/'unlisted.py').write_text('unexpected')
        self.assertNotEqual(0,self.check().returncode)

if __name__=='__main__':unittest.main()

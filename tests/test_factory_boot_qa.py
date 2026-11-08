"""Optional test executables must be rejected before any QA VM is launched."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('factory_boot_qa_under_test',ROOT/'tools/factory_boot_qa.py')
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)

class BootQaInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        (self.root/'final-boot-qa.json').write_text(json.dumps({'application':{'version':'camera-experience-006'}}))
        self.worker=self.root/'fixture-elf'
        data=bytearray(64);data[:8]=b'\x7fELF\x02\x01\x01\x09';data[18:20]=(183).to_bytes(2,'little')
        self.worker.write_bytes(data);self.sha=hashlib.sha256(data).hexdigest()

    def rejected(self,arguments,message):
        command=['factory_boot_qa.py','--inputs',str(self.root),'--record','final-boot-qa.json','--out',str(self.root/'new-vm')]+arguments
        with patch.object(sys,'argv',command),patch.object(sys,'platform','linux'),patch.object(qa.os,'geteuid',return_value=1000,create=True),patch.object(qa.subprocess,'Popen',side_effect=AssertionError('A VM must not start for a refused input')),self.assertRaisesRegex(ValueError,message):
            qa.main()
        self.assertFalse((self.root/'new-vm').exists())

    def test_missing_worker_hash_pair_refused_before_output_creation(self):
        self.rejected(['--native-worker',str(self.worker)],'supplied together')

    def test_changed_worker_bytes_refused_before_vm(self):
        self.worker.write_bytes(self.worker.read_bytes()+b'changed')
        self.rejected(['--native-worker',str(self.worker),'--native-worker-sha256',self.sha],'pinned FreeBSD ARM64 ELF')

    def test_matching_hash_for_wrong_architecture_does_not_authorize_execution(self):
        data=bytearray(self.worker.read_bytes());data[18:20]=(62).to_bytes(2,'little');self.worker.write_bytes(data)
        self.rejected(['--native-worker',str(self.worker),'--native-worker-sha256',hashlib.sha256(data).hexdigest()],'pinned FreeBSD ARM64 ELF')

    def test_walnut_capture_probe_refuses_old_application_context(self):
        (self.root/'final-boot-qa.json').write_text(json.dumps({'application':{'version':'camera-experience-005'}}))
        self.rejected(['--native-worker',str(self.worker),'--native-worker-sha256',self.sha],'requires the 006')

if __name__=='__main__':unittest.main()

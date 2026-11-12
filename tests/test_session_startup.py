"""Deferred identity audit must report actual full hashes, never partial hashes."""
import hashlib
import importlib.util
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

PATH=pathlib.Path(__file__).resolve().parents[1]/'system/freebsd/image/overlay/usr/local/libexec/DaOS-camera-session.py'
spec=importlib.util.spec_from_file_location('session_startup_test',PATH)
session=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{name:Mock() for name in ('fcntl','grp','pwd')} if sys.platform=='win32' else {}):spec.loader.exec_module(session)


class SessionStartupTests(unittest.TestCase):
    def test_audit_does_no_io_until_started_and_emits_complete_digests_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'kernel';raw=b'kernel-fixture'*100000;path.write_bytes(raw)
            calls=[]
            with patch.object(session,'event',side_effect=lambda name,**kw:calls.append((name,kw))):
                audit=session.IdentityAudit({'system':'fixture'},{'kernel_sha256':path})
                self.assertIsNone(audit.thread);self.assertEqual(calls,[])
                audit.start();thread=audit.thread;audit.start();self.assertIs(audit.thread,thread)
                thread.join(3);self.assertFalse(thread.is_alive());audit.close()
            self.assertEqual(calls,[('base-identity',{'system':'fixture','kernel_sha256':hashlib.sha256(raw).hexdigest()})])

    def test_missing_input_and_cancelled_reads_never_claim_a_digest(self):
        for cancelled in (False,True):
            with self.subTest(cancelled=cancelled),tempfile.TemporaryDirectory() as directory:
                path=pathlib.Path(directory)/'kernel'
                if cancelled:path.write_bytes(b'unchanged')
                calls=[]
                with patch.object(session,'event',side_effect=lambda name,**kw:calls.append((name,kw))):
                    audit=session.IdentityAudit({}, {'kernel_sha256':path})
                    if cancelled:audit.close()
                    audit.start();audit.thread.join(3)
                    self.assertFalse(audit.thread.is_alive());audit.close()
                self.assertEqual(calls[0][0],'base-identity-incomplete')
                self.assertNotIn('kernel_sha256',calls[0][1])

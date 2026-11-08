"""Walnut maker branding must preserve the existing RAW/metadata contract."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from camera.dng.hf_dng import write_dng
from camera.dng.inspect import inspect_dng

REQUEST='00000000-0000-0000-0000-000000000001'
NAMESPACE='org.hasslefree.hf-sim.capture.v1'  # Deliberately retained technical ABI.
# Public synthetic 4x4 fixture produced by frozen app005 writer SHA8969aef7... .
# This is test data, not a user's capture; its original bytes must remain valid.
LEGACY_RAW=base64.b64decode(
 'SUkqAAgAAAAgAP4ABAABAAAAAAAAAAABBAABAAAABAAAAAEBBAABAAAABAAAAAIBAwABAAAAEAAAAAMBAwABAAAAAQAAAAYBAwABAAAAI4AAAA4BAgCdAAAAjgEAAA8BAgALAAAALAIAABABAgAHAAAAOAIAABEBBAABAAAA4gIAABIBAwABAAAAAQAAABUBAwABAAAAAQAAABYBBAABAAAABAAAABcBBAABAAAAIAAAABwBAwABAAAAAQAAAI2CAwACAAAAAgACAI6CAQAEAAAAAAEBAmmHBAABAAAAyAIAABLGAQAEAAAAAQQAABPGAQAEAAAAAQEAABTGAgAHAAAAQAIAABbGAQADAAAAAAECABfGAwABAAAAAQAAABnGAwACAAAAAQABABrGBAABAAAAQAAAAB3GBAABAAAA/z8AAB/GBAACAAAASAIAACDGBAACAAAAUAIAACHGCgAJAAAAWAIAACjGBQADAAAAoAIAAFrGAwABAAAAFQAAAI3GBAAEAAAAuAIAAAAAAAB7Im5hbWVzcGFjZSI6Im9yZy5oYXNzbGVmcmVlLmhmLXNpbS5jYXB0dXJlLnYxIiwic2ltdWxhdGVkIjp0cnVlLCJjYXB0dXJlIjp7ImFjdHVhbF9leHBvc3VyZV91cyI6MTAwMDAsInJlcXVlc3RfaWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDEifX0AAEhhc3NsZUZyZWUAAEhGLVNpbQAASEYtU2ltAAAAAAAAAAAAAAQAAAAEAAAAAQAAAAEAAAAAAAAAAQAAAAAAAAABAAAAAAAAAAEAAAABAAAAAQAAAAAAAAABAAAAAAAAAAEAAAAAAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAAAAAAAAAAAEAAAABAAAAAEAmoIFAAEAAADaAgAAAAAAAAEAAABkAAAAQABkAMgALAFAAGQAyAAsAUAAZADIACwBQABkAMgALAE=')

def ascii_tag(raw,tag):
    """Independent minimal TIFF ASCII tag reader for these controlled fixtures."""
    start=struct.unpack_from('<I',raw,4)[0]
    count=struct.unpack_from('<H',raw,start)[0]
    matches=[]
    for index in range(count):
        at=start+2+index*12
        number,kind,length,value=struct.unpack_from('<HHII',raw,at)
        if number==tag:
            if kind!=2:raise AssertionError('Expected ASCII TIFF field')
            data=raw[at+8:at+12][:length] if length<=4 else raw[value:value+length]
            matches.append(data.rstrip(b'\0').decode('ascii'))
    if len(matches)!=1:raise AssertionError('Missing or duplicate TIFF field')
    return matches[0]

class ProjectBrandDngTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'new.dng'
        write_dng(self.path,4,4,[64,100,200,300]*4,{'request_id':REQUEST,'actual_exposure_us':10000})

    def test_new_default_maker_is_project_walnut(self):
        self.assertEqual(ascii_tag(self.path.read_bytes(),271),'Project Walnut')
        self.assertEqual(ascii_tag(self.path.read_bytes(),272),'HF-Sim')

    def test_new_brand_retains_exact_technical_envelope(self):
        description=json.loads(ascii_tag(self.path.read_bytes(),270))
        self.assertEqual(description,{'namespace':NAMESPACE,'simulated':True,
            'capture':{'actual_exposure_us':10000,'request_id':REQUEST}})
        self.assertEqual(inspect_dng(self.path)['metadata_state'],'valid')

    def test_frozen_old_raw_is_readable_without_any_byte_or_time_change(self):
        self.assertEqual(hashlib.sha256(LEGACY_RAW).hexdigest(),'464bc7d371f13e6a4f50834f7989cec3f0ba6f7a43bc41ee8e02321765de469c')
        old=Path(self.temporary.name)/'old-app005.dng';old.write_bytes(LEGACY_RAW)
        before=(old.read_bytes(),old.stat().st_mtime_ns)
        result=inspect_dng(old)
        self.assertEqual(result['metadata_state'],'valid');self.assertEqual(result['request_id'],REQUEST)
        self.assertEqual((old.read_bytes(),old.stat().st_mtime_ns),before)
        fresh=inspect_dng(self.path)
        a=result['strip_offsets'][0];b=fresh['strip_offsets'][0]
        self.assertEqual(LEGACY_RAW[a:a+result['strip_byte_counts'][0]],self.path.read_bytes()[b:b+fresh['strip_byte_counts'][0]])

    def test_brand_change_does_not_trust_foreign_capture_namespace(self):
        raw=self.path.read_bytes();foreign='org.foreign.'+'x'*(len(NAMESPACE)-len('org.foreign.'))
        self.assertEqual(raw.count(NAMESPACE.encode('ascii')),1)
        raw=raw.replace(NAMESPACE.encode('ascii'),foreign.encode('ascii'))
        self.path.write_bytes(raw);before=(self.path.read_bytes(),self.path.stat().st_mtime_ns)
        result=inspect_dng(self.path)
        self.assertEqual(result['metadata_state'],'invalid');self.assertIsNone(result['request_id'])
        self.assertEqual((self.path.read_bytes(),self.path.stat().st_mtime_ns),before)

if __name__=='__main__':unittest.main()

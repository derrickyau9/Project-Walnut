"""The optional USB helper is pinned before the native image builder writes."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ram_builder',ROOT/'system/adb/recovery/build_ram_rescue.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def executable(machine=183):
    data=bytearray(120);data[:8]=b'\x7fELF\x02\x01\x01\x09'
    struct.pack_into('<HHI',data,16,2,machine,1)
    return bytes(data)


class RamBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.helper=self.base/'native-helper';self.data=executable();self.helper.write_bytes(self.data)
        self.digest=hashlib.sha256(self.data).hexdigest()
    def tearDown(self):self.temp.cleanup()

    def test_omitted_override_preserves_source_helper(self):
        self.assertIsNone(builder.usb_helper_override(None,None))

    def test_helper_and_digest_must_be_paired(self):
        for path,digest in ((self.helper,None),(None,self.digest)):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'together'):
                builder.usb_helper_override(path,digest)

    def test_bad_pair_stops_before_build_output_or_native_mount(self):
        args=SimpleNamespace(usb_helper=self.helper,usb_helper_sha256=None,output=self.base/'output')
        with patch.object(builder.sys,'platform','freebsd15'),patch.object(builder.os,'geteuid',return_value=0,create=True),patch.object(builder,'run') as native:
            with self.assertRaisesRegex(ValueError,'together'):builder.build(args)
        self.assertFalse(args.output.exists());native.assert_not_called()

    def test_hash_mismatch_or_non_arm64_native_executable_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
            builder.usb_helper_override(self.helper,'0'*64)
        self.helper.write_bytes(executable(machine=62))
        with self.assertRaisesRegex(ValueError,'ARM64'):
            builder.usb_helper_override(self.helper,hashlib.sha256(self.helper.read_bytes()).hexdigest())

    def test_source_and_parent_symlinks_are_rejected(self):
        link=self.base/'link';directory=self.base/'directory-link'
        try:link.symlink_to(self.helper);directory.symlink_to(self.base,target_is_directory=True)
        except OSError:self.skipTest('Host does not permit test symlinks')
        for path in (link,directory/self.helper.name):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'Redirected'):
                builder.usb_helper_override(path,self.digest)

    def test_bound_bytes_are_snapshotted_against_later_source_changes(self):
        bound=builder.usb_helper_override(self.helper,self.digest)
        self.helper.write_bytes(b'changed after preflight')
        self.assertEqual(bound,self.data)
        self.assertEqual(hashlib.sha256(bound).hexdigest(),self.digest)


if __name__=='__main__':unittest.main()

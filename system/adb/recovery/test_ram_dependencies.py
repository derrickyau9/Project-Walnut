"""Image command closure checks; catch a missing /sbin alias before makefs."""
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ram_dependency_builder',HERE/'build_ram_rescue.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


@unittest.skipIf(os.name=='nt','Image absolute symlink semantics require POSIX QA')
class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-rescue-command-closure-');self.root=Path(self.temp.name)
        data=bytearray(20);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,183)
        data.extend(b'sh\0kldstat\0mount\0sysctl\0umount\0reboot\0sleep\0')
        for name in ('rescue/rescue','usr/local/bin/python3.12','usr/sbin/daemon','usr/local/libexec/daos-usb-adb'):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o755)
        (self.root/'sbin').mkdir()
        self.scripts={'runtime.py':"command(['/sbin/kldstat','-q','-m','usb_template'])"}
    def tearDown(self):self.temp.cleanup()

    def test_missing_sbin_kldstat_alias_fails_before_image_build(self):
        with patch.object(builder,'run',return_value=''):
            with self.assertRaisesRegex(ValueError,'/sbin/kldstat'):builder.runtime_preflight(self.root,self.scripts)

    def test_absolute_alias_resolves_inside_image_and_passes_when_present(self):
        (self.root/'sbin/kldstat').symlink_to('/rescue/rescue')
        with patch.object(builder,'run',return_value=''):
            report=builder.runtime_preflight(self.root,self.scripts)
        self.assertTrue(report['passed']);self.assertEqual(report['paths']['/sbin/kldstat']['resolved'],'rescue/rescue')

    def test_host_bin_sh_cannot_satisfy_a_missing_image_target(self):
        (self.root/'sbin/kldstat').symlink_to('/bin/sh')
        with patch.object(builder,'run',return_value=''):
            with self.assertRaisesRegex(ValueError,'/sbin/kldstat'):builder.runtime_preflight(self.root,self.scripts)

    def test_missing_dynamic_library_or_wrong_elf_architecture_fails(self):
        (self.root/'sbin/kldstat').symlink_to('/rescue/rescue')
        with patch.object(builder,'run',return_value=' (NEEDED) Shared library: [libmissing.so.1]'):
            with self.assertRaisesRegex(ValueError,'libmissing'):builder.runtime_preflight(self.root,self.scripts)
        p=self.root/'usr/local/bin/python3.12';data=bytearray(p.read_bytes());struct.pack_into('<H',data,18,62);p.write_bytes(data)
        with patch.object(builder,'run',return_value=''):
            with self.assertRaisesRegex(ValueError,'Non-ARM64'):builder.runtime_preflight(self.root,self.scripts)

    def test_direct_rescue_multicall_argument_must_exist(self):
        scripts={'runtime.py':"command(['/rescue/rescue','missing_applet'])"}
        with patch.object(builder,'run',return_value=''):
            with self.assertRaisesRegex(ValueError,'missing_applet'):builder.runtime_preflight(self.root,scripts)


if __name__=='__main__':unittest.main()

"""Virtual-target archive/ELF/serial safety; actual guest boot is separate evidence."""
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import json
import subprocess
import sys

from tools import qemu_target as target


def elf(machine=183, interpreter=False):
    data=bytearray(120); data[:6]=b'\x7fELF\x02\x01'
    data[18:20]=machine.to_bytes(2,'little'); data[32:40]=(64).to_bytes(8,'little')
    data[54:56]=(56).to_bytes(2,'little'); data[56:58]=(1).to_bytes(2,'little')
    data[64:68]=(3 if interpreter else 1).to_bytes(4,'little')
    return bytes(data)


def serial(data=b'12345678'):
    return ('HF_QEMU_INIT pid=1 machine=aarch64 release=test synthetic-only\n'
            'HF_QEMU_NATIVE_TESTS_OK\nHF_QEMU_NOREPLACE_OK\nHF_QEMU_RPC_RESTART_OK\n'
            f'HF_QEMU_DNG_BEGIN {len(data)}\nHF_QEMU_DNG {data.hex()}\n'
            'HF_QEMU_DNG_END\nHF_QEMU_RESULT PASS synthetic-only\n').encode()


class QemuTargetTests(unittest.TestCase):
    def test_wrong_arch_dynamic_and_truncated_elf_rejected_before_build(self):
        target.arm64_elf(elf())
        for value in [elf(62),elf(interpreter=True),elf()[:100],b'not ELF']:
            with self.assertRaises(ValueError): target.arm64_elf(value)
        with tempfile.TemporaryDirectory() as tmp:
            native=Path(tmp)/'native'; native.mkdir()
            (native/'hf-core-tests').write_bytes(elf(62))
            output=Path(tmp)/'guest'
            with patch.object(target.subprocess,'run') as run:
                with self.assertRaises(ValueError): target.build(output,native)
                run.assert_not_called()
            self.assertFalse(output.exists())

    def test_newc_archive_is_deterministic_and_rejects_aliases(self):
        item=('init',b'example',stat.S_IFREG|0o755,0,0)
        expected=target.cpio([item])
        self.assertTrue(expected.startswith(b'070701'))
        self.assertIn(b'TRAILER!!!\0',expected)
        self.assertEqual(target.cpio([item]),expected)
        for name in ['../init','/init','bin/../init','bin//init','bin/./init','TRAILER!!!','in\0it']:
            with self.assertRaises(ValueError): target.cpio([(name,*item[1:])])
        with self.assertRaises(ValueError): target.cpio([item,item])
        with self.assertRaises(ValueError): target.cpio([('link',b'init',stat.S_IFLNK|0o777,0,0)])

    def test_guest_serial_requires_complete_unique_success_and_exact_transfer(self):
        data,_=target.parse_serial(serial())
        self.assertEqual(data,b'12345678')
        for value in [serial().replace(b'NOREPLACE_OK',b'NOPE'), serial()+b'HF_QEMU_RESULT FAIL\n',
                      serial()+b'HF_QEMU_RESULT PASS synthetic-only\n',
                      serial().replace(b'BEGIN 8',b'BEGIN 9'),
                      serial().replace(b'DNG 3132',b'DNG zz32'),
                      serial().replace(b'HF_QEMU_DNG_END',b'not the end')]:
            with self.assertRaises(ValueError): target.parse_serial(value)
        with self.assertRaises(ValueError): target.parse_serial(b'x'*(4*1024**2+1))

    def test_guest_boot_refuses_changed_archive_before_starting_process(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/'build-evidence.json').write_text(json.dumps({'initramfs_sha256':'bad'}))
            (directory/'initramfs.cpio.gz').write_bytes(b'changed')
            package=directory/'kernel'; image=package/'extracted/boot/image'
            image.parent.mkdir(parents=True); image.write_bytes(b'kernel')
            (package/'kernel.deb').write_bytes(b'package')
            (package/'kernel-manifest.json').write_text(json.dumps({'kernel_relative_path':'extracted/boot/image',
                'kernel_sha256':target.sha(b'kernel'),'sha256':target.sha(b'package')}))
            with patch.object(target.subprocess,'Popen') as start:
                with self.assertRaisesRegex(ValueError,'changed'): target.boot(image,directory)
                start.assert_not_called()

    def test_guest_boot_refuses_kernel_evidence_mismatch_before_start(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); image=root/'extracted/boot/image'; image.parent.mkdir(parents=True)
            image.write_bytes(b'foreign kernel'); (root/'kernel.deb').write_bytes(b'package')
            (root/'kernel-manifest.json').write_text(json.dumps({'kernel_relative_path':'extracted/boot/image',
                'kernel_sha256':target.sha(b'expected kernel'),'sha256':target.sha(b'package')}))
            with patch.object(target.subprocess,'Popen') as start:
                with self.assertRaisesRegex(ValueError,'kernel differs'): target.boot(image,root/'guest')
                start.assert_not_called()

    def test_repeated_guest_run_preserves_existing_artifact_without_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); existing=directory/'guest.dng'; existing.write_bytes(b'first run')
            with patch.object(target.subprocess,'Popen') as start:
                with self.assertRaisesRegex(ValueError,'output exists'): target.boot(directory/'kernel',directory)
                start.assert_not_called()
            self.assertEqual(existing.read_bytes(),b'first run')

    def test_timeout_stops_owned_real_child_and_closes_serial_pipe(self):
        from tools.arm64_build import source_inputs
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); package=root/'kernel'; image=package/'extracted/boot/image'
            image.parent.mkdir(parents=True); image.write_bytes(b'fixture kernel')
            (package/'kernel.deb').write_bytes(b'fixture package')
            (package/'kernel-manifest.json').write_text(json.dumps({'kernel_relative_path':'extracted/boot/image',
                'kernel_sha256':target.sha(image.read_bytes()),'sha256':target.sha(b'fixture package')}))
            guest=root/'guest'; guest.mkdir(); packed=b'fixture initramfs'
            (guest/'initramfs.cpio.gz').write_bytes(packed)
            inputs={name:target.sha((target.ROOT/name).read_bytes()) for name in
                ('system/qemu-virt/init.c','system/qemu-virt/rpc.c','tools/qemu_target.py')}
            (guest/'build-evidence.json').write_text(json.dumps({'initramfs_sha256':target.sha(packed),
                'guest_input_sha256':inputs,'native_input_sha256':source_inputs(target.ROOT)}))
            real_start=subprocess.Popen; children=[]
            def start(_command,**kwargs):
                child=real_start([sys.executable,'-c','import time; time.sleep(5)'],**kwargs)
                children.append(child); return child
            with patch.object(target.subprocess,'Popen',side_effect=start):
                with self.assertRaisesRegex(RuntimeError,'timed out'):
                    target.boot(image,guest,timeout=.1)
            self.assertEqual(len(children),1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue((guest/'serial.log').is_file())
            self.assertFalse((guest/'boot-evidence.json').exists())


if __name__=='__main__': unittest.main()

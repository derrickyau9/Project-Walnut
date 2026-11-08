"""Boot selector branch execution with harmless U-Boot command substitutes."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recovery_boot_config',ROOT/'system/adb/recovery/boot_config.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)


class BootConfigTests(unittest.TestCase):
    def test_ram_rescue_sets_init_hook_before_kernel_boot(self):
        script=boot.recovery_lua()
        hook='loader.setenv("init_script", "/usr/local/libexec/DaOS-recovery/early-init.sh")'
        self.assertIn(hook,script)
        self.assertLess(script.index(hook),script.index('checked("boot")'))

    def test_ram_image_loader_requests_implicit_gzip_decompression(self):
        commands=[line.strip() for line in boot.recovery_lua().splitlines() if line.startswith('checked(')]
        self.assertIn('checked("load -t md_image /DaOS-recovery/root.ufs")',commands)
        self.assertNotIn('checked("load -t md_image /DaOS-recovery/root.ufs.gz")',commands)

    def test_legacy_script_header_roundtrip_and_corruption_rejected(self):
        script=boot.manual_script()
        blob=boot.script_image(script)
        self.assertEqual(boot.read_script_image(blob),script)
        for offset in (0,4,8,12,64,70,len(blob)-1):
            changed=bytearray(blob);changed[offset]^=1
            with self.subTest(offset=offset),self.assertRaises(ValueError):boot.read_script_image(changed)

    def test_invalid_devices_cannot_inject_commands_into_uboot(self):
        for interface,device in (('mmc; reset','0:1'),('mmc','0:1; reset'),('usb','../disk'),('usb','0:0')):
            with self.subTest(interface=interface,device=device),self.assertRaises(ValueError):boot.manual_script(interface,device)
        with self.assertRaises(ValueError):boot.gpio_selector(gpio=8)

    def test_loader_patch_rejects_other_source_before_creating_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'main.c';source.write_bytes(b'not pinned source')
            output=root/'output'
            with self.assertRaises(ValueError):boot.generate(output,source)
            self.assertFalse(output.exists())

    @unittest.skipUnless(os.environ.get('DAOS_FREEBSD_LOADER_SOURCE'),'Set exact pinned source path for source patch QA')
    def test_patch_pins_efi_device_before_primary_root_search(self):
        source=Path(os.environ['DAOS_FREEBSD_LOADER_SOURCE']).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),boot.LOADER_SOURCE_SHA)
        changed=boot.patch_loader_source(source)
        self.assertEqual(changed.count(boot.MARKER),1)
        self.assertIn(b'efiblk_get_pdinfo_by_handle(boot_img->DeviceHandle)',changed)
        self.assertLess(changed.index(boot.MARKER),changed.index(boot.PATCH_ANCHOR))
        self.assertEqual(changed.replace(boot.PATCH_INSERT,b'',1),source)

    @unittest.skipIf(os.name=='nt','Execute script branches under POSIX shell')
    def test_gpio_low_selects_recovery_high_keeps_original_loader(self):
        low=self.execute(boot.gpio_selector(),pin=0)
        self.assertIn('ARGS:daos_recovery=YES loader_lua=/DaOS-recovery/loader.lua',low.stdout)
        self.assertIn('/DaOS-recovery/loader.efi',low.stdout)
        self.assertNotIn('/EFI/BOOT/BOOTAA64.EFI',low.stdout)
        high=self.execute(boot.gpio_selector(),pin=1)
        self.assertIn('ARGS:\n',high.stdout)
        self.assertIn('/EFI/BOOT/BOOTAA64.EFI',high.stdout)
        self.assertNotIn('/DaOS-recovery/loader.efi',high.stdout)

    @unittest.skipIf(os.name=='nt','Execute script branches under POSIX shell')
    def test_rescue_load_failure_never_falls_back_to_damaged_primary(self):
        for script in (boot.manual_script(),boot.gpio_selector()):
            result=self.execute(script,pin=0,load_result=1)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('DAOS_RESCUE_BOOT_FAILED',result.stdout)
            self.assertNotIn('BOOTEFI:',result.stdout)
            self.assertNotIn('/EFI/BOOT/BOOTAA64.EFI',result.stdout)

    @unittest.skipIf(os.name=='nt','Execute script branches under POSIX shell')
    def test_armed_selector_marks_and_verifies_before_every_normal_boot(self):
        for marker in ('missing','0'):
            with self.subTest(marker=marker):
                p=self.execute_armed(marker)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertIn('BOOT:/EFI/BOOT/BOOTAA64.EFI:',p.stdout)
                self.assertLess(p.stdout.index('MARKER-WRITE:1'),p.stdout.index('BOOT:'))
                self.assertIn('MARKER-READ:1',p.stdout)

    @unittest.skipIf(os.name=='nt','Execute script branches under POSIX shell')
    def test_unclean_reset_malformed_marker_or_gpio_enters_rescue_without_rearming(self):
        for marker,pin in (('1',1),('2',1),('long0',1),('missing',0)):
            with self.subTest(marker=marker,pin=pin):
                p=self.execute_armed(marker,pin=pin)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertIn('BOOT:/DaOS-recovery/loader.efi:daos_recovery=YES',p.stdout)
                self.assertNotIn('MARKER-WRITE:',p.stdout)
                self.assertNotIn('/EFI/BOOT/BOOTAA64.EFI',p.stdout)

    @unittest.skipIf(os.name=='nt','Execute script branches under POSIX shell')
    def test_failed_marker_write_readback_or_mismatch_never_transfers_normal(self):
        for mode in ('write-failed','readback-failed','readback-mismatch'):
            with self.subTest(mode=mode):
                p=self.execute_armed('0',mode=mode)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertIn('BOOT:/DaOS-recovery/loader.efi:daos_recovery=YES',p.stdout)
                self.assertNotIn('/EFI/BOOT/BOOTAA64.EFI',p.stdout)

    def execute_armed(self,marker,pin=1,mode='ok'):
        commands='''
kernel_addr_r=0x80000
marker=DAOS_MARKER
mode=DAOS_MODE
read_count=0
setenv() { name=$1; shift; eval "$name=\\"\\$*\\""; }
gpio() { return DAOS_PIN; }
write_byte() { memory=$2; }
fatload() {
    read_count=$((read_count+1))
    [ "$marker" = missing ] && return 1
    [ "$read_count" -gt 1 ] && [ "$mode" = readback-failed ] && return 1
    filesize=1
    memory=$marker
    [ "$marker" = long0 ] && memory=0 && filesize=2
    [ "$read_count" -gt 1 ] && [ "$mode" = readback-mismatch ] && memory=0
    printf 'MARKER-READ:%s\\n' "$memory"
    return 0
}
fatwrite() {
    [ "$mode" = write-failed ] && return 1
    marker=$memory
    printf 'MARKER-WRITE:%s\\n' "$marker"
}
itest_byte() { if [ "$2" = '!=' ]; then [ "$memory" != "$3" ]; else [ "$memory" = "$3" ]; fi; }
load() { loaded=$4; }
bootefi() { printf 'BOOT:%s:%s\\n' "$loaded" "$bootargs"; exit 0; }
sleep() { exit 99; }
'''.replace('DAOS_MARKER',marker).replace('DAOS_MODE',mode).replace('DAOS_PIN',str(pin))
        script=boot.armed_failover().replace('mw.b','write_byte').replace('itest.b','itest_byte')
        return subprocess.run(['/bin/sh','-c',commands+script],capture_output=True,text=True,timeout=5)

    def execute(self,script,pin=0,load_result=0):
        commands='''
kernel_addr_r=0x80000
setenv() { shift; printf 'ARGS:%s\\n' "$*"; }
gpio() { return DAOS_PIN; }
load() { printf 'LOAD:%s\\n' "$*"; return DAOS_LOAD_RESULT; }
bootefi() { printf 'BOOTEFI:%s\\n' "$*"; }
sleep() { exit 99; }
'''.replace('DAOS_PIN',str(pin)).replace('DAOS_LOAD_RESULT',str(load_result))
        return subprocess.run(['/bin/sh','-c',commands+script],capture_output=True,text=True,timeout=5)


if __name__=='__main__':unittest.main()

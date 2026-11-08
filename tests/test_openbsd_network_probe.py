"""Exercise the shell gates using private command fixtures, never a network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'system/openbsd/raspberrypi/network_probe.sh'
NONCE='a'*32


@unittest.skipUnless(os.name=='posix','actual POSIX shell fixtures')
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.marker=self.root/'marker';self.marker.write_text(NONCE+'\n')
        self.mounts=self.root/'mounts';self.mounts.write_text('/dev/rd0a on / type ffs (local)\n')
        self.script=SOURCE.read_text().replace('/etc/daos/probe-nonce',str(self.marker))
        commands={
            '/bin/arch':'echo OpenBSD.aarch64',
            '/sbin/mount':'cat "'+str(self.mounts)+'"',
            '/sbin/sysctl':'case "$*" in "-n kern.ostype") :;; "-n hw.machine") echo arm64;; *) echo "hw.model=QEMU fixture";; esac',
            '/bin/df':'echo "RAM fixture"',
            '/sbin/ifconfig':'printf "%s\\n" "vio0: flags=UP,RUNNING" "status: active" "wpakey MUST_NOT_APPEAR" "inet 10.0.2.15"',
            '/sbin/route':'echo "10.0.2/24 link#1 vio0"',
            '/sbin/dmesg':'echo "root on rd0a swap on rd0b"'}
        for i,(name,body)in enumerate(commands.items()):
            path=self.root/('command'+str(i));path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o700)
            self.script=self.script.replace(name,str(path))
        self.path=self.root/'probe.sh';self.path.write_text(self.script)
    def tearDown(self):self.temp.cleanup()
    def run_probe(self,nonce=NONCE,interface='vio0'):
        return subprocess.run(['/bin/sh',str(self.path),nonce,interface],capture_output=True,text=True,timeout=3)
    def test_valid_ram_logs_and_redacts_key_bearing_lines(self):
        result=self.run_probe();self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('DAOS_OPENBSD_NETWORK_LOGS_END '+NONCE,result.stdout)
        self.assertIn('status: active',result.stdout);self.assertNotIn('MUST_NOT_APPEAR',result.stdout)
    def test_any_physical_or_extra_mount_is_refused_before_logs(self):
        for row in ('/dev/sd0a on / type ffs (local)','/dev/rd0a on / type ffs (local)\n/dev/sd0i on /mnt type msdos (local)','/dev/rd0a on / type nfs (local)'):
            self.mounts.write_text(row+'\n');result=self.run_probe()
            self.assertNotEqual(result.returncode,0);self.assertNotIn('LOGS_BEGIN',result.stdout)
    def test_wrong_nonce_and_unexpected_interface_are_refused(self):
        for nonce,interface in (('b'*32,'vio0'),('a'*31,'vio0'),('g'*32,'vio0'),(NONCE,'bwfm1'),(NONCE,'vio0;reboot')):
            with self.subTest(nonce=nonce,interface=interface):self.assertNotEqual(self.run_probe(nonce,interface).returncode,0)
    def test_redirected_marker_is_refused(self):
        actual=self.root/'actual';self.marker.rename(actual);self.marker.symlink_to(actual)
        self.assertNotEqual(self.run_probe().returncode,0)
    def test_mount_change_during_readonly_collection_prevents_end_marker(self):
        self.path.write_text(self.script.replace('--- IPv4 routes ---', '--- IPv4 routes ---')
                             .replace("echo '--- dmesg (first 600 lines) ---'",'echo "/dev/sd0a on / type ffs (local)" > "'+str(self.mounts)+'"'))
        result=self.run_probe();self.assertNotEqual(result.returncode,0)
        self.assertIn('mount topology changed',result.stderr);self.assertNotIn('LOGS_END',result.stdout)
    def test_original_source_has_valid_posix_shell_syntax(self):
        result=subprocess.run(['/bin/sh','-n',str(SOURCE)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_wrong_OS_or_arch_is_refused_before_logs(self):
        for arch in ('NetBSD.aarch64','OpenBSD.amd64','arm64',''):
            with self.subTest(arch=arch):
                (self.root/'command0').write_text('#!/bin/sh\necho "'+arch+'"\n')
                result=self.run_probe();self.assertNotEqual(result.returncode,0)
                self.assertNotIn('LOGS_BEGIN',result.stdout)
    def test_stock_SMALL_missing_ostype_and_printf_are_supported(self):
        # Native ARM64 RAMDISK returns empty kern.ostype and has no printf.
        source=SOURCE.read_text()
        self.assertNotIn('printf ',source);self.assertNotIn('kern.ostype',source)
        self.assertEqual(self.run_probe().returncode,0)


if __name__=='__main__':unittest.main()

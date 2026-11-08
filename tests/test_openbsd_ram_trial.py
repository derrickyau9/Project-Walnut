"""Private POSIX-shell fixtures: no radio, daemon, device or filesystem action."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'system/openbsd/raspberrypi/ram_init_trial.sh'
NONCE='c'*32

@unittest.skipUnless(os.name=='posix','requires private POSIX process groups')
class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bin=self.root/'commands';self.bin.mkdir()
        self.calls=self.root/'calls';self.console=self.root/'console';self.console.touch()
        self.mounts=self.root/'mounts';self.mounts.write_text('root_device on / type ffs (local, read-only)\n')
        self.dmesg=self.root/'dmesg';self.dmesg.write_text('OpenBSD 8.0 (RAMDISK) #113: fixture\nroot on rd0a swap on rd0b dump on rd0b\n"daos,openbsd-dtb-001" at mainbus0 not configured\n')
        for name in ('etc/daos','etc/ssh','var/run','var/log','dev'):(self.root/name).mkdir(parents=True,exist_ok=True)
        for name,data in {'etc/daos/probe-nonce':NONCE,'etc/daos/trial-kind':'OPENBSD8_PI4_RAM_WIFI_ONLY',
            'etc/daos/test-ssid':'PUBLIC TEST AP','etc/daos/test-psk':'public-test-fixture-password',
            'etc/ssh/authorized_keys':'PUBLIC FIXTURE ONLY','etc/ssh/ssh_host_ed25519_key':'NOT A KEY - MOCK ONLY'}.items():
            p=self.root/name;p.write_text(data+'\n');p.chmod(0o600)
        (self.root/'dev/bpf').symlink_to('/dev/null')
        self.wrap('arch','echo OpenBSD.aarch64')
        self.wrap('mount','if [ "$#" -gt 0 ]; then echo "/dev/rd0a on / type ffs (local)" > "'+str(self.mounts)+'"; else /bin/cat "'+str(self.mounts)+'"; fi')
        self.wrap('dmesg','/bin/cat "'+str(self.dmesg)+'"')
        self.wrap('sysctl','case "$*" in "-n hw.machine") echo arm64;; "-n hw.product") echo "Raspberry Pi 4 Model B Rev 1.4";; esac')
        self.wrap('sshd','[ "$1" != -t ] || exit 0\necho "Server listening on 0.0.0.0 port 22.\r"\nexec /bin/sleep 30')
        self.wrap('resolvd','exit 0')
        self.wrap('dhcpleased','exec /bin/sleep 30')
        self.wrap('sleep','/bin/sleep 0.02')
        self.wrap('reboot','echo REBOOT_REQUESTED >> "'+str(self.calls)+'"')
        self.ifcalls=self.root/'ifconfig.jsonl'
        path=self.bin/'ifconfig';path.write_text('#!/usr/bin/python3\nimport json,sys\nwith open('+repr(str(self.ifcalls))+',"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nif sys.argv[1:]==["bse0"]:sys.exit(1)\n');path.chmod(0o700)
        self.script=SOURCE.read_text().replace('PATH=/sbin:/bin:/usr/bin:/usr/sbin:/','PATH='+str(self.bin)+':/usr/bin:/bin')
        for name in ('/etc/daos','/etc/ssh','/var/run','/var/log','/var/db','/dev/bpf'):
            self.script=self.script.replace(name,str(self.root/name.lstrip('/')))
        self.script=self.script.replace('>/dev/console','>>'+str(self.console))
        for name in ('/bin/arch','/usr/sbin/sshd','/sbin/resolvd','/sbin/dhcpleased'):
            self.script=self.script.replace(name,str(self.bin/Path(name).name))
        self.path=self.root/'trial.sh';self.path.write_text(self.script);self.groups=[]
    def wrap(self,name,body):
        p=self.bin/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o700)
    def tearDown(self):
        for group in self.groups:
            try:os.killpg(group,signal.SIGKILL)
            except ProcessLookupError:pass
        self.tmp.cleanup()
    def run_trial(self):
        p=subprocess.Popen(['/bin/sh',str(self.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        self.groups.append(p.pid);stdout,stderr=p.communicate(timeout=3)
        time.sleep(.04)
        return p.returncode,stdout.decode(),stderr.decode()
    def ifargs(self):return [] if not self.ifcalls.exists() else [json.loads(x) for x in self.ifcalls.read_text().splitlines()]
    def test_only_resident_root_single_private_AP_request_and_key_listener(self):
        rc,stdout,stderr=self.run_trial();self.assertEqual(rc,0,stderr+self.console.read_text())
        calls=self.ifargs();self.assertEqual(sum('wpakey' in x for x in calls),1)
        self.assertIn(['bwfm0','nwid','PUBLIC TEST AP','wpakey','public-test-fixture-password','up'],calls)
        self.assertIn(['bwfm0','inet','autoconf'],calls)
        self.assertIn('ACCESS_REQUESTED',self.console.read_text());self.assertNotIn('public-test-fixture-password',stdout+stderr+self.console.read_text())
    def test_physical_or_extra_mount_refused_before_deadline_or_interface(self):
        for row in ('/dev/sd0a on / type ffs (local)','/dev/rd0a on / type ffs (local)\n/dev/sd0i on /mnt type msdos (local)'):
            self.mounts.write_text(row+'\n');rc,_,_=self.run_trial();self.assertNotEqual(rc,0)
            self.assertFalse((self.root/'var/run/daos-openbsd-deadline.pid').exists());self.assertEqual(self.ifargs(),[])
    def test_wrong_FDT_refused_before_opaque_inputs_or_interface(self):
        self.dmesg.write_text('OpenBSD 8.0 (RAMDISK) #113: fixture\nroot on rd0a swap on rd0b dump on rd0b\n');rc,_,_=self.run_trial()
        self.assertNotEqual(rc,0);self.assertEqual(self.ifargs(),[])
    def test_prior_boot_root_and_FDT_proofs_cannot_satisfy_current_boot(self):
        old='OpenBSD 8.0 (RAMDISK) #112: old\nroot on rd0a swap on rd0b dump on rd0b\n"daos,openbsd-dtb-001" at mainbus0 not configured\n'
        for current in ('OpenBSD 8.0 (RAMDISK) #113: new\nroot on rd0a swap on rd0b dump on rd0b\n',
                        'OpenBSD 8.0 (GENERIC) #113: new\nroot on sd0a swap on sd0b dump on sd0b\n"daos,openbsd-dtb-001" at mainbus0 not configured\n'):
            with self.subTest(current=current):
                self.dmesg.write_text(old+current);rc,_,_=self.run_trial();self.assertNotEqual(rc,0);self.assertEqual(self.ifargs(),[])
    def test_no_current_banner_refused_before_root_update(self):
        self.dmesg.write_text('root on rd0a swap on rd0b dump on rd0b\n"daos,openbsd-dtb-001" at mainbus0 not configured\n')
        rc,_,_=self.run_trial();self.assertNotEqual(rc,0)
        self.assertIn('root_device',self.mounts.read_text());self.assertEqual(self.ifargs(),[])
    def test_missing_BPF_refused_before_interface(self):
        (self.root/'dev/bpf').unlink();rc,_,_=self.run_trial();self.assertNotEqual(rc,0);self.assertEqual(self.ifargs(),[])
    def test_empty_or_symlink_opaque_input_refused(self):
        p=self.root/'etc/daos/test-psk';p.unlink();p.symlink_to(self.root/'etc/daos/test-ssid')
        rc,_,_=self.run_trial();self.assertNotEqual(rc,0);self.assertEqual(self.ifargs(),[])
    def test_secret_characters_are_data_and_never_evaluated(self):
        text='$(touch '+str(self.root/'SHOULD_NOT_EXIST')+'); value'
        (self.root/'etc/daos/test-psk').write_text(text)
        rc,stdout,stderr=self.run_trial();self.assertEqual(rc,0,stderr)
        self.assertTrue(any(text in call for call in self.ifargs()));self.assertNotIn(text,stdout+stderr+self.console.read_text())
        self.assertFalse((self.root/'SHOULD_NOT_EXIST').exists())
    def test_wired_DHCP_still_requested_if_WiFi_absent_or_configuration_fails(self):
        wrapper=self.bin/'ifconfig'
        original=wrapper.read_text()
        for failed in ('absent','configuration'):
            with self.subTest(failed=failed):
                self.ifcalls.unlink(missing_ok=True)
                (self.root/'var/run/daos-trial-started').unlink(missing_ok=True)
                wrapper.write_text(original.replace('if sys.argv[1:]==["bse0"]:sys.exit(1)',
                    'if '+('sys.argv[1:]==["bwfm0"]' if failed=='absent' else '"wpakey" in sys.argv')+':sys.exit(1)'))
                rc,_,stderr=self.run_trial();self.assertEqual(rc,0,stderr)
                calls=self.ifargs();self.assertIn(['bse0','inet','autoconf','up'],calls)
                self.assertNotIn(['bwfm0','inet','autoconf'],calls)
                self.assertIn('wired=DHCP_requested',self.console.read_text())
    def test_no_source_install_mount_flash_or_deadline_extension_path(self):
        source=SOURCE.read_text();self.assertEqual(source.count('sleep 120'),1)
        self.assertIn('reboot -nq',source);self.assertNotIn('fatwrite',source);self.assertNotIn('mount_msdos',source)
        self.assertNotIn('ssh-keygen',source);self.assertNotIn('eval ',source)
        self.assertEqual(subprocess.run(['/bin/sh','-n',str(SOURCE)]).returncode,0)
if __name__=='__main__':unittest.main()

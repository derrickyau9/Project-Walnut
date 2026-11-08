"""Private POSIX QA only: no fido, SD provider, module load or RF access.

Native gates/commands are explicitly replaced with private files and fakes.
Two tests exercise real owned child-process SIGSTOP/PID lifetime on POSIX.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
RADIO_SOURCE=ROOT/'system/freebsd/raspberrypi/wifi005_ram_radio.py'
if os.name!='nt':
    import fcntl
    spec=importlib.util.spec_from_file_location('private_wifi005_radio',RADIO_SOURCE)
    radio=importlib.util.module_from_spec(spec);spec.loader.exec_module(radio)
else:radio=None


class Clock:
    def __init__(self,now=100.):self.now=now
    def read(self):return self.now
    def sleep(self,seconds):self.now+=seconds


@unittest.skipIf(radio is None,'POSIX process/flock private QA')
class RadioPrivateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wifi005-radio-private-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.ram=self.base/'ram';self.ram.mkdir(mode=0o700)
        self.nonce='1'*32;self.state=self.ram/('wifi005-radio-'+self.nonce);self.state.mkdir(mode=0o700)
        self.run=self.base/'private-run';self.run.mkdir(mode=0o700)
        self.assets=self.ram/('wifi005-rf-assets-'+self.nonce);self.assets.mkdir(mode=0o700)
        (self.assets/radio.WATCHDOG).write_bytes(b'private fake executable');(self.assets/radio.WATCHDOG).chmod(0o500)
        self.commands=[];owner=self
        class Refused(ValueError):pass
        class Runner:
            def __init__(self,seconds=30):pass
            def run(self,args,**kwargs):owner.commands.append(args);return 0,b''
            def text(self,args,**kwargs):
                owner.commands.append(args)
                if args[:2]==['/rescue/mount','-p']:return 0,'/dev/md0 / ufs ro 0 0\n'
                if args[:2]==['/sbin/mdconfig','-l']:return 0,'md0 preload 384M /private/root.ufs'
                if args==['/sbin/sysctl','-n','vm.swap_total']:return 0,'0'
                if args==['/sbin/sysctl','-n','kern.bootfile']:return 0,'/boot/kernel.daos-wifi-005-ramprobe/kernel'
                if args==['/sbin/sysctl','-n','net.wlan.devices']:return 0,'brcmfmac0'
                if args==['/sbin/sysctl','-n','dev.brcmfmac.0.country']:return 0,'US'
                if args==['/rescue/dmesg','-a']:return 0,'brcmfmac0: firmware: private-test-build'
                raise AssertionError('Unexpected native command '+str(args))
        self.c=SimpleNamespace(RAM=self.ram,BOOTFILE='/boot/kernel.daos-wifi-005-ramprobe/kernel',Runner=Runner,Refused=Refused,
            trusted=lambda path,**kw:Path(path),pinned=lambda *args:{},
            isolation=lambda rows:rows,mounts=lambda data:[line.split() for line in data.splitlines()],
            collect=Mock(return_value={'passed':True,'card_provider_reads':False}),
            dmesg_evidence=lambda text:{},native_kenv=lambda key:{'daos_wifi005_trial_nonce':self.nonce,'daos_recovery':'YES','hw.bcm2835.sdhci.pio_mode':'1','daos_wifi005_no_rf':'YES'}[key])
        self.clock=Clock();self.lockfd=None
    def tearDown(self):
        if self.lockfd is not None:os.close(self.lockfd);self.lockfd=None
    def ready(self):
        path=self.state/'owner.lock';self.lockfd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);fcntl.flock(self.lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ready={'format':radio.FORMAT,'nonce':self.nonce,'origin':100.,'deadline':220.,'owner_pid':12345,'feeder_pid':23456,
               'watchdog_identity':radio.file_identity(self.assets/radio.WATCHDOG),'owner_lock_identity':radio.file_identity(path),'armed_ack':'ARMED 23456 '+self.nonce,'ready_monotonic':100.,'hardware_seconds':8}
        (self.state/'ready.json').write_text(json.dumps(ready));(self.state/'ready.json').chmod(0o600)
        (self.run/'watchdog.armed').write_text(ready['armed_ack']+'\n');(self.run/'watchdog.armed').chmod(0o600)
        radio.heartbeat(self.run/'heartbeat',self.nonce,self.clock.now)
        return ready
    def verify(self):
        with patch.object(radio,'watchdog_timeout',return_value=8*(1<<32)):
            return radio.verify_ready(self.c,self.state,self.run,self.nonce,clock=self.clock.read)
    def mutate_ready(self,**changes):
        path=self.state/'ready.json';data=json.loads(path.read_text());data.update(changes);path.write_text(json.dumps(data))
    def attach(self,popen=None):
        loader=Mock();loader.poll.return_value=0
        real_verify=radio.verify_ready
        def sleep(seconds):
            self.clock.sleep(seconds)
            radio.heartbeat(self.run/'heartbeat',self.nonce,self.clock.now)
        with (patch.object(radio,'paths',return_value=(self.state,self.run)),patch.object(radio,'asset_check'),
             patch.object(radio,'watchdog_timeout',return_value=8*(1<<32)),patch.object(radio.time,'monotonic',side_effect=self.clock.read),
             patch.object(radio.time,'sleep',side_effect=sleep),
             patch.object(radio,'verify_ready',side_effect=lambda c,s,r,n:real_verify(c,s,r,n,clock=self.clock.read)),
             patch.object(radio.subprocess,'Popen',side_effect=popen or (lambda *a,**k:loader))):
            return radio.attach(self.c,self.base/'boot-artifacts',self.assets,self.nonce,'US')

    def test_fixed_deadline_never_extends_and_stops_only_direct_child(self):
        child=Mock();beats=[]
        radio.deadline_loop(child,220.,lambda now:beats.append(now),clock=self.clock.read,sleep=self.clock.sleep)
        self.assertEqual(self.clock.now,220.);self.assertTrue(beats);self.assertLess(max(beats),220.)
        child.send_signal.assert_called_once_with(signal.SIGSTOP);child.poll.assert_not_called();child.wait.assert_not_called()

    def test_plan_is_readonly_and_never_starts_watchdog_or_loader(self):
        future_state=self.base/'unprepared-state';future_run=self.base/'unprepared-run'
        with (patch.object(radio,'paths',return_value=(future_state,future_run)),patch.object(radio,'binary_gate') as aliases,
              patch.object(radio,'asset_check'),patch.object(radio,'watchdog_timeout',return_value=0),
              patch.object(radio.subprocess,'Popen',side_effect=AssertionError('plan must not start a process'))):
            result=radio.plan(self.c,self.base/'boot-artifacts',self.assets,self.nonce)
        aliases.assert_called_once_with(self.c)
        self.assertEqual(result['status'],'planned-no-radio');self.assertEqual(result['rf_operations'],0)
        self.assertFalse(future_state.exists());self.assertFalse(future_run.exists())

    @unittest.skipUnless(hasattr(os,'geteuid') and os.geteuid()==0,'Alias owner assertion needs private root-owned fixtures')
    def test_crunched_binary_alias_gate_accepts_exact_and_refuses_wrong_target(self):
        private=self.base/'rescue';private.mkdir();binary=private/'rescue';binary.write_bytes(b'private no-execution fixture');binary.chmod(0o500)
        for name in ('kldload','kenv','mount'):(private/name).symlink_to('rescue')
        realpath=Path
        def remap(value):return realpath(str(value).replace('/rescue/',str(private)+'/',1))
        with patch.object(radio,'Path',side_effect=remap):radio.binary_gate(self.c)
        other=private/'other';other.write_bytes(b'other');other.chmod(0o500)
        (private/'kldload').unlink();(private/'kldload').symlink_to('other')
        with patch.object(radio,'Path',side_effect=remap),self.assertRaisesRegex(ValueError,'alias missing'):radio.binary_gate(self.c)

    def test_expired_deadline_stops_child_without_another_heartbeat(self):
        child=Mock();beat=Mock();self.clock.now=250.
        radio.deadline_loop(child,220.,beat,clock=self.clock.read,sleep=self.clock.sleep)
        beat.assert_not_called();child.send_signal.assert_called_once_with(signal.SIGSTOP)

    def test_healthy_ready_requires_real_busy_lock_fresh_nonce_ack_and_hardware8(self):
        expected=self.ready();self.assertEqual(self.verify(),expected)

    def test_deadline_extension_wrong_nonce_future_or_expired_ticket_rejected(self):
        original=self.ready()
        for changes in ({'deadline':221.},{'nonce':'2'*32},{'origin':101.,'deadline':221.},{'origin':-20.,'deadline':100.}):
            self.mutate_ready(**original);self.mutate_ready(**changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.verify()

    def test_owner_death_unlock_rejects_even_fresh_heartbeat_and_valid_ack(self):
        self.ready();os.close(self.lockfd);self.lockfd=None
        with self.assertRaisesRegex(ValueError,'no longer holds'):self.verify()

    def test_replaced_owner_lock_inode_rejected(self):
        self.ready();path=self.state/'owner.lock';path.unlink();path.write_bytes(b'new lock');path.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'lock changed'):self.verify()

    def test_stale_future_or_wrong_nonce_heartbeat_rejected(self):
        self.ready()
        for text in (self.nonce+' 97\n',self.nonce+' 101\n','2'*32+' 100\n',self.nonce+' nan\n'):
            (self.run/'heartbeat').write_text(text)
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'heartbeat'):self.verify()

    def test_wrong_pid_or_nonce_armed_ack_rejected(self):
        self.ready()
        for ack in ('ARMED 999 '+self.nonce,'ARMED 23456 '+'2'*32):
            (self.run/'watchdog.armed').write_text(ack)
            with self.subTest(ack=ack),self.assertRaisesRegex(ValueError,'acknowledgement'):self.verify()

    def test_disarm_protocol_injection_refused(self):
        self.ready()
        for name in ('watchdog.stop','watchdog.disarmed','result'):
            path=self.run/name;path.write_text('injected')
            try:
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'disarm'):self.verify()
            finally:path.unlink()

    def test_unarmed_or_wrong_hardware_timeout_rejected(self):
        self.ready()
        for value in (0,4*(1<<32),9*(1<<32)):
            with self.subTest(value=value),patch.object(radio,'watchdog_timeout',return_value=value),self.assertRaisesRegex(ValueError,'eight seconds'):
                radio.verify_ready(self.c,self.state,self.run,self.nonce,clock=self.clock.read)

    def test_successful_attach_claims_once_and_only_ram_status_commands_after_rf(self):
        self.ready();result=self.attach()
        self.assertEqual(result['status'],'attached-candidate-reset-pending');self.assertEqual(result['rf_operations'],1)
        self.assertGreaterEqual(result['candidate_stable_seconds'],5);self.assertFalse(result['wifi_repaired'])
        self.assertFalse(result['watchdog_disarmed']);self.assertFalse(result['fat_marker_cleared']);self.assertFalse(result['physical_card_io'])
        self.assertFalse(result['scan_or_association_requested']);self.assertTrue((self.state/'attach.started').exists())
        allowed={'/rescue/kenv','/rescue/mount','/rescue/dmesg','/sbin/sysctl'}
        self.assertTrue(all(args[0] in allowed for args in self.commands))
        self.assertFalse(any(any(token in ' '.join(args) for token in ('diskinfo','mmcsd','sdda','gpart','normal','clear-FAT')) for args in self.commands))
        with self.assertRaises(FileExistsError):self.attach()

    def test_attach_loader_failure_keeps_attempt_claim_and_does_not_disarm(self):
        self.ready()
        def failed(*args,**kwargs):raise OSError('private load failure')
        with self.assertRaisesRegex(OSError,'load failure'):self.attach(failed)
        self.assertTrue((self.state/'attach.started').exists())
        self.assertFalse(any((self.run/name).exists() for name in ('watchdog.stop','watchdog.disarmed','result')))

    def test_attach_refuses_changed_feeder_inode_before_country_or_loader(self):
        self.ready();path=self.assets/radio.WATCHDOG;path.unlink();path.write_bytes(b'private fake executable');path.chmod(0o500)
        with self.assertRaisesRegex(ValueError,'inode changed'):self.attach()
        self.assertFalse((self.state/'attach.started').exists());self.assertEqual(self.commands,[])

    def test_attach_refuses_less_than60s_remaining(self):
        self.ready();self.clock.now=170.;radio.heartbeat(self.run/'heartbeat',self.nonce,170.)
        with self.assertRaisesRegex(ValueError,'Insufficient'):self.attach()
        self.assertFalse((self.state/'attach.started').exists())

    def test_kernel_error_returns_reset_pending_without_disarm_or_sd(self):
        self.ready()
        def bad(text):raise self.c.Refused('private SDIO status error')
        self.c.dmesg_evidence=bad
        result=self.attach()
        self.assertEqual(result['status'],'kernel-error-reset-pending');self.assertFalse(result['watchdog_disarmed'])

    def test_exclusive_receipt_never_overwrites_existing_attempt(self):
        path=self.state/'attach.started';radio.save_new(path,{'nonce':self.nonce})
        with self.assertRaises(FileExistsError):radio.save_new(path,{'nonce':'2'*32})
        self.assertEqual(json.loads(path.read_text())['nonce'],self.nonce);self.assertEqual(path.stat().st_nlink,1)

    def test_coordinator_owns_child_without_polling_after_immediate_armed_ack(self):
        launch={'nonce':self.nonce,'origin':100.,'deadline':220.};radio.save_new(self.state/'launch.json',launch)
        child=Mock();child.pid=444;child.poll.side_effect=AssertionError('must not reap already ARMED child')
        def popen(args,**kwargs):
            self.assertEqual(args,[str(self.assets/radio.WATCHDOG),str(self.run),self.nonce])
            self.assertIn('preexec_fn',kwargs)
            (self.run/'watchdog.armed').write_text('ARMED 444 '+self.nonce+'\n');return child
        class Reset(Exception):pass
        def finish(owned,deadline,beat):
            self.assertIs(owned,child);self.assertEqual(deadline,220.);self.clock.now=220.;owned.send_signal(signal.SIGSTOP)
        with (patch.object(radio,'paths',return_value=(self.state,self.run)),patch.object(radio,'asset_check'),
             patch.object(radio,'watchdog_timeout',side_effect=[0,8*(1<<32)]),patch.object(radio.subprocess,'Popen',side_effect=popen),
             patch.object(radio.time,'monotonic',side_effect=self.clock.read),patch.object(radio,'deadline_loop',side_effect=finish),
             patch.object(radio.time,'sleep',side_effect=Reset)):
            with self.assertRaises(Reset):radio.coordinate(self.c,self.assets,self.nonce)
        ready=json.loads((self.state/'ready.json').read_text());self.assertEqual(ready['feeder_pid'],444)
        child.poll.assert_not_called();self.assertEqual(child.send_signal.call_args_list[0].args,(signal.SIGSTOP,))
        self.assertTrue((self.state/'deadline-fired.json').exists());self.assertFalse((self.run/'watchdog.stop').exists())

    def test_coordinator_startup_ack_failure_stops_own_child_without_disarm(self):
        radio.save_new(self.state/'launch.json',{'nonce':self.nonce,'origin':100.,'deadline':220.})
        child=Mock();child.pid=444;child.poll.return_value=69
        with (patch.object(radio,'paths',return_value=(self.state,self.run)),patch.object(radio,'asset_check'),
             patch.object(radio,'watchdog_timeout',return_value=0),patch.object(radio.subprocess,'Popen',return_value=child),
             patch.object(radio.time,'monotonic',side_effect=self.clock.read)):
            with self.assertRaisesRegex(ValueError,'acknowledgement unavailable'):radio.coordinate(self.c,self.assets,self.nonce)
        child.send_signal.assert_called_once_with(signal.SIGSTOP);self.assertFalse((self.state/'ready.json').exists())
        self.assertFalse(any((self.run/name).exists() for name in ('watchdog.stop','watchdog.disarmed','result')))


@unittest.skipIf(radio is None,'POSIX owned child-process private QA')
class OwnedChildProcessTests(unittest.TestCase):
    def test_real_owned_child_is_stopped_and_keeps_pid_until_parent_reaps(self):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            radio.deadline_loop(child,0,lambda now:None,clock=lambda:0,sleep=lambda seconds:None)
            pid,status=os.waitpid(child.pid,os.WUNTRACED)
            self.assertEqual(pid,child.pid);self.assertTrue(os.WIFSTOPPED(status));self.assertEqual(os.WSTOPSIG(status),signal.SIGSTOP)
        finally:
            child.send_signal(signal.SIGCONT);child.terminate();child.wait(timeout=5)

    def test_already_reaped_owned_child_never_signals_reused_pid(self):
        child=subprocess.Popen([sys.executable,'-c','pass']);child.wait(timeout=5)
        with patch.object(subprocess.os,'kill',side_effect=AssertionError('must not signal already reaped PID')):
            radio.deadline_loop(child,0,lambda now:None,clock=lambda:0,sleep=lambda seconds:None)


if __name__=='__main__':unittest.main()

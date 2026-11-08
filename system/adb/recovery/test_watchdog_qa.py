"""Exercise watchdog QA controller gates with private files and fake hardware."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('watchdog_qa',HERE/'qa_watchdog_ram.py')
qa=importlib.util.module_from_spec(spec)
if os.name!='nt':spec.loader.exec_module(qa)


class FakeRuntime:
    def __init__(self):
        self.now=100.0;self.armed=False;self.events=[];self.selector={'disk_uuid':'fixed','marker':1,'selector_sha256':'a'*64}
    def clock(self):return self.now
    def sleep(self,seconds):self.now+=seconds
    def boot(self):return 'same-boot-tuple'
    def query(self):return 8*qa.SBT_1S if self.armed else 0
    def verify(self):self.events.append('verify');return {'protected':True}
    def marker(self):self.events.append('read-FAT-RO-and-unmount');return self.selector
    def arm(self):self.events.append('arm');self.armed=True;return {'pid':42,'arm_output':'ARMED pid=42'}
    def disarm(self):self.events.append('disarm');self.armed=False
    def stop(self):self.events.append('SIGSTOP-owned-child')


@unittest.skipIf(os.name=='nt','POSIX fcntl controller; use WSL/FreeBSD')
class WatchdogQATests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='watchdog-qa-controller-');self.root=Path(self.temp.name)
        self.rows='/dev/md0 / ufs rw 0 0\ndevfs /dev devfs rw 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'
    def tearDown(self):self.temp.cleanup()

    def test_gettimeout_exact_freebsd15_readonly_ioctl_and_buffer(self):
        self.assertEqual(qa.WDIOC_GETTIMEOUT,0x40085736)
        def ioctl(fd,number,data,mutate):
            self.assertEqual((fd,number,len(data),mutate),(123,0x40085736,8,True));data[:]=struct.pack('=q',8*qa.SBT_1S)
        with patch.object(qa.os,'open',return_value=123) as opened,patch.object(qa.os,'close'),patch.object(qa.fcntl,'ioctl',side_effect=ioctl):
            self.assertEqual(qa.query_timeout(),8*qa.SBT_1S)
        self.assertEqual(opened.call_args.args[1]&os.O_ACCMODE,os.O_RDONLY)

    def test_requires_resident_rw_ram_and_no_swap(self):
        qa.safety_mounts(self.rows,'md0 preload 384M','0')
        for rows,md,swap in ((self.rows,'md0 vnode /file','0'),(self.rows,'md0 preload','1'),(self.rows.replace('ufs rw','ufs ro'),'md0 preload','0')):
            with self.subTest(md=md,swap=swap),self.assertRaises(ValueError):qa.safety_mounts(rows,md,swap)

    def test_any_other_ufs_fat_or_nullfs_mount_blocks_reset(self):
        for kind in ('ufs','msdosfs','nullfs'):
            for mode in ('ro','rw'):
                with self.subTest(kind=kind,mode=mode),self.assertRaises(ValueError):
                    qa.safety_mounts(self.rows+'/dev/card /media '+kind+' '+mode+' 0 0\n','md0 preload','0')

    def test_transient_policy_restores_guard_and_absence_even_on_error(self):
        for failure in (False,True):
            policy=self.root/('policy-'+str(failure));calls=[]
            def run(args):calls.append(args);return 'YES' if args[1]=='-q' else ''
            try:
                with qa.ram_policy(run,path=policy):
                    self.assertEqual(policy.read_bytes(),qa.POLICY_BYTES);self.assertEqual(policy.stat().st_mode&0o777,0o444)
                    if failure:raise ValueError('injected launch failure')
            except ValueError:
                if not failure:raise
            self.assertFalse(policy.exists());self.assertEqual(calls[-1],['/rescue/kenv','daos_recovery=YES'])

    def test_plan_reads_fat_but_never_arms(self):
        runtime=FakeRuntime()
        with contextlib.redirect_stdout(io.StringIO()):record=qa.run_phase('plan',runtime,self.root/'plan.json')
        self.assertEqual(record['status'],'planned-no-watchdog-arm');self.assertNotIn('arm',runtime.events)
        self.assertIn('read-FAT-RO-and-unmount',runtime.events)

    def test_disarm_requires_full_twelve_second_observation(self):
        runtime=FakeRuntime()
        with contextlib.redirect_stdout(io.StringIO()):record=qa.run_phase('disarm',runtime,self.root/'disarm.json')
        self.assertEqual(record['status'],'disarm-observation-passed');self.assertGreaterEqual(record['observed_seconds'],12)
        self.assertFalse(runtime.armed);self.assertEqual(runtime.events.count('arm'),1)

    def test_fault_refuses_missing_stale_or_other_boot_disarm_receipt_before_arming(self):
        runtime=FakeRuntime()
        for previous in ({},{'format':qa.FORMAT,'status':'disarm-observation-passed','boot_id':'other','binary_sha256':qa.BINARY_SHA}):
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(ValueError):qa.run_phase('fault',runtime,self.root/'bad.json',previous)
        self.assertNotIn('arm',runtime.events)

    def test_fault_timeout_resumes_disarms_and_saves_reset_nonce_for_host(self):
        runtime=FakeRuntime()
        previous={'format':qa.FORMAT,'status':'disarm-observation-passed','boot_id':runtime.boot(),'binary_sha256':qa.BINARY_SHA,'selector':runtime.selector,'completed_monotonic':runtime.clock()}
        with contextlib.redirect_stdout(io.StringIO()):record=qa.run_phase('fault',runtime,self.root/'fault.json',previous)
        self.assertEqual(record['status'],'fault-no-reset-failed')
        self.assertIn('SIGSTOP-owned-child',runtime.events);self.assertFalse(runtime.armed)
        self.assertGreaterEqual(runtime.now-100,27)
        nonce=record['reset_nonce'];self.assertEqual(Path(nonce['path']).read_text().strip(),nonce['value'])
        self.assertTrue(record['host_must_verify_new_transport'])

    def test_rearming_during_disarm_observation_is_not_a_pass(self):
        runtime=FakeRuntime();runtime.query=lambda:1
        with self.assertRaises(ValueError):qa.observe_disarmed(runtime,runtime.boot())

    def test_fat_marker_is_inspected_readonly_and_unmounted_on_success_or_failure(self):
        for marker in (b'\1',b'\0'):
            work=self.root/('work-'+str(marker[0]));work.mkdir();disk=work/'disk';disk.write_bytes(b'fake GPT read input')
            identity=work/'identity.json';identity.write_text(json.dumps({'disk':str(disk),'disk_bytes':24,'sector_size':512,'disk_uuid':'fixed'}))
            flash=SimpleNamespace(IDENTITY=identity,secure_path=lambda p:Path(p),parse_gpt=lambda *args:('fixed',{1:{'type':qa.EFI_GUID,'label':'DaOS-efi','uuid':'efi-fixed'}}))
            runtime=qa.Runtime.__new__(qa.Runtime);runtime.flash=flash;runtime.selector_sha=qa.hashlib.sha256(b'selector').hexdigest();runtime.verify=Mock()
            current={};calls=[]
            def run(args):
                calls.append(list(map(str,args)))
                if len(args)>1 and args[1]=='mount_msdosfs':
                    self.assertEqual(args[2:4],['-o','ro']);mount=Path(args[-1]);current['mount']=mount
                    (mount/'DaOS-recovery').mkdir();(mount/'DaOS-recovery/normal.running').write_bytes(marker);(mount/'boot.scr').write_bytes(b'selector');return ''
                if args==['/rescue/mount','-p']:
                    return str(disk)+'p1 '+str(current['mount'])+' msdosfs ro 0 0'
                if len(args)>1 and args[1]=='umount':
                    mount=Path(args[-1]);shutil.rmtree(mount/'DaOS-recovery');(mount/'boot.scr').unlink();return ''
                raise AssertionError(args)
            with patch.object(qa,'WORK',work),patch.object(qa,'command',side_effect=run):
                if marker==b'\1':self.assertEqual(runtime.marker()['marker'],1)
                else:
                    with self.assertRaises(ValueError):runtime.marker()
            self.assertEqual(calls[-1][1],'umount');self.assertFalse(current['mount'].exists())


if __name__=='__main__':unittest.main()

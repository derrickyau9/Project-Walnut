"""No hardware operations: exercise the mandatory pre-RF protection gates."""
import errno,hashlib,importlib.util,io,json,os,struct,sys,tempfile,unittest
from contextlib import ExitStack,redirect_stdout,redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'system/freebsd/raspberrypi'
spec=importlib.util.spec_from_file_location('deploy_wifi_005',SOURCE/'deploy_wifi_005.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
old=sys.modules.get('deploy_wifi_005');sys.modules['deploy_wifi_005']=deploy
try:
    spec=importlib.util.spec_from_file_location('wifi005_diagnostic',SOURCE/'diagnose_wifi_005.py')
    diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)
finally:
    if old is None:sys.modules.pop('deploy_wifi_005',None)
    else:sys.modules['deploy_wifi_005']=old

class ReadonlyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/DaOS_WiFi_005_Diagnostic/logic-qa';base.mkdir(parents=True,exist_ok=True)
        follow=patch.object(diag.os,'O_NOFOLLOW',getattr(diag.os,'O_NOFOLLOW',0x100000),create=True);follow.start();self.addCleanup(follow.stop)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temp.name)
        self.rows=[{'source':dev,'target':target,'type':'ufs','options':['ro','noatime']} for target,dev in diag.EXPECTED.items()]
        self.rows+=[{'source':'tmpfs','target':'/tmp','type':'tmpfs','options':['rw']}]
        self.rows+=[{'source':'/var/DaOS/camera/photos/.owned/apps','target':target,'type':'nullfs','options':['rw']} for target in diag.ALIASES]
        self.ram=Path(diag.RAM_PREFIX+'a'*32)
        self.probes=[(Path('/opt/hasslefree') if target=='/' else Path(target))/('.DaOS-wifi005-write-probe-'+'a'*32) for target in tuple(diag.EXPECTED)+diag.ALIASES]
    def tearDown(self):self.temp.cleanup()
    def usb_pid_call(self,*,early=True,status=0,data=None,receipt_mode=0o600,dead=False,untrusted=False,stderr=''):
        early_path=self.root/'early_adb.py';receipt_path=self.root/'early.json';ordinary_path=self.root/'ordinary.pid'
        if early:early_path.write_text('trusted fixture')
        else:early_path.unlink(missing_ok=True)
        supervisor=Mock();supervisor.stat.return_value=SimpleNamespace(st_mode=0o755,st_nlink=1)
        receipt=Mock();receipt.stat.return_value=SimpleNamespace(st_mode=receipt_mode,st_nlink=1,st_size=200)
        receipt.read_text.return_value=json.dumps(data if data is not None else {'format':'DaOS-early-recovery-adb-v1','auth_required':True,'pid':1234})
        ordinary=Mock();ordinary.read_text.return_value='4567\n'
        def own(path):
            if Path(path)==early_path:return supervisor
            if Path(path)==receipt_path:
                if untrusted:raise ValueError('redirected or non-root-owned receipt')
                return receipt
            if Path(path)==ordinary_path:return ordinary
            raise AssertionError(path)
        self.usb_owned=Mock(side_effect=own)
        self.usb_command=Mock(return_value=SimpleNamespace(returncode=status,stderr=stderr))
        self.usb_kill=Mock(side_effect=ProcessLookupError('dead owner') if dead else None)
        self.usb_normal_path=ordinary_path
        with ExitStack() as stack:
            stack.enter_context(patch.object(diag,'EARLY_USB_SUPERVISOR',early_path))
            stack.enter_context(patch.object(diag,'EARLY_USB_RECEIPT',receipt_path))
            stack.enter_context(patch.object(diag,'NORMAL_USB_PID',ordinary_path))
            stack.enter_context(patch.object(diag,'owned',self.usb_owned))
            stack.enter_context(patch.object(diag,'command',self.usb_command))
            stack.enter_context(patch.object(diag.os,'kill',self.usb_kill))
            return diag.usb_session_pid()
    def test_usb_uses_live_authenticated_early_owner_instead_of_var_pid(self):
        self.assertEqual(self.usb_pid_call(),1234)
        self.usb_kill.assert_called_once_with(1234,0)
        self.assertNotIn(self.usb_normal_path,[call.args[0] for call in self.usb_owned.call_args_list])
        self.assertEqual(self.usb_command.call_args.args[0][-1],'status')
    def test_usb_falls_back_to_ordinary_owner_only_without_early_owner(self):
        for early,status in ((False,0),(True,1)):
            with self.subTest(early=early,status=status):
                self.assertEqual(self.usb_pid_call(early=early,status=status),4567)
                self.usb_kill.assert_called_once_with(4567,0)
                if not early:self.usb_command.assert_not_called()
    def test_usb_rejects_wrong_early_receipt_format_auth_or_pid_without_fallback(self):
        original={'format':'DaOS-early-recovery-adb-v1','auth_required':True,'pid':1234}
        for field,value in (('format','wrong'),('auth_required',False),('auth_required','true'),('pid',True),('pid',1),('pid','1234')):
            data=dict(original);data[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.usb_pid_call(data=data)
            self.usb_kill.assert_not_called()
            self.assertNotIn(self.usb_normal_path,[call.args[0] for call in self.usb_owned.call_args_list])
    def test_usb_rejects_untrusted_writable_or_dead_early_owner(self):
        for options,error in (({'untrusted':True},ValueError),({'receipt_mode':0o666},ValueError),({'dead':True},ProcessLookupError)):
            with self.subTest(options=options),self.assertRaises(error):self.usb_pid_call(**options)
            self.assertNotIn(self.usb_normal_path,[call.args[0] for call in self.usb_owned.call_args_list])
    def test_usb_early_status_error_is_not_a_signal_to_trust_stale_var_pid(self):
        for status,stderr in ((2,''),(1,'invalid RAM mount')):
            with self.subTest(status=status,stderr=stderr),self.assertRaisesRegex(ValueError,'could not be verified'):
                self.usb_pid_call(status=status,stderr=stderr)
            self.usb_kill.assert_not_called()
            self.assertNotIn(self.usb_normal_path,[call.args[0] for call in self.usb_owned.call_args_list])
    def mount_text(self,rows=None):
        return ''.join(row['source']+' '+row['target']+' '+row['type']+' '+','.join(row['options'])+' 0 0\n' for row in (self.rows if rows is None else rows))
    def test_readonly_lower_mounts_accept_rw_nullfs_only_with_separate_write_probes(self):
        found=diag.physical_mounts(diag.parse_mounts(self.mount_text()),protected=True)
        self.assertEqual(set(found),set(diag.EXPECTED))
    def test_missing_mislabeled_or_duplicate_physical_provider_is_rejected(self):
        for transform in ('missing','label','duplicate'):
            rows=json.loads(json.dumps(self.rows))
            if transform=='missing':rows.pop(0)
            elif transform=='label':rows[1]['source']='/dev/gpt/OtherState'
            else:rows.append(dict(rows[0]))
            with self.subTest(transform=transform),self.assertRaises(ValueError):diag.physical_mounts(rows,protected=True)
    def test_any_writable_physical_provider_blocks_protected_state(self):
        for target in diag.EXPECTED:
            rows=json.loads(json.dumps(self.rows))
            next(v for v in rows if v['target']==target)['options']=['rw']
            with self.subTest(target=target),self.assertRaises(ValueError):diag.physical_mounts(rows,protected=True)
    def test_unknown_writable_physical_filesystem_is_rejected(self):
        rows=self.rows+[{'source':'/dev/da0p1','target':'/boot/efi','type':'msdosfs','options':['rw']}]
        with self.assertRaises(ValueError):diag.physical_mounts(rows,protected=False)
    def test_ram_requires_actual_tmpfs_not_only_tmp_prefix(self):
        diag.require_tmpfs(self.ram,self.rows)
        rows=[v for v in self.rows if v['target']!='/tmp']
        with self.assertRaises(ValueError):diag.require_tmpfs(self.ram,rows)
        rows=self.rows+[{'source':'/dev/da0p2','target':self.ram.as_posix(),'type':'ufs','options':['rw']}]
        with self.assertRaises(ValueError):diag.require_tmpfs(self.ram,rows)
    def test_protection_forces_children_then_state_and_probes_every_alias(self):
        calls=[]
        def run(args):calls.append([str(v) for v in args]);return SimpleNamespace(stdout=self.mount_text())
        with patch.object(diag.os,'statvfs',create=True,return_value=SimpleNamespace(f_flag=diag.READONLY_FLAG)),patch.object(diag,'blocked_write',return_value=errno.EROFS) as probe:
            result=diag.protect(self.ram,{},self.probes,run)
        self.assertEqual([v[-1] for v in calls[:3]],['/var/DaOS/camera/photos','/var/DaOS/calibration','/var'])
        self.assertTrue(all(v[1:4]==['-u','-r','-f'] for v in calls[:3]))
        self.assertEqual(probe.call_count,7);self.assertEqual(set(result),set(map(str,self.probes)))
    def test_missing_alias_probe_is_rejected_before_any_mount_change(self):
        run=Mock()
        with self.assertRaises(ValueError):diag.protect(self.ram,{},self.probes[:-1],run)
        run.assert_not_called()
    def test_mount_table_ro_without_statvfs_ro_is_not_accepted(self):
        run=Mock(return_value=SimpleNamespace(stdout=self.mount_text()))
        with patch.object(diag.os,'statvfs',create=True,return_value=SimpleNamespace(f_flag=0)),patch.object(diag,'blocked_write') as probe:
            with self.assertRaises(ValueError):diag.protect(self.ram,{},self.probes,run)
        probe.assert_not_called()
    def test_write_denial_accepts_only_erofs_or_enxio(self):
        for code in (errno.EROFS,errno.ENXIO):
            with self.subTest(code=code),patch.object(diag.os,'open',side_effect=OSError(code,'blocked')):
                self.assertEqual(diag.blocked_write(self.root/'probe'),code)
        for code in (errno.EACCES,errno.ENOENT):
            with self.subTest(code=code),patch.object(diag.os,'open',side_effect=OSError(code,'unproven')):
                with self.assertRaises(ValueError):diag.blocked_write(self.root/'probe')
    def test_successful_write_probe_blocks_rf_and_removes_only_its_own_empty_file(self):
        path=self.root/'own-empty-probe';path.write_bytes(b'')
        with patch.object(diag.os,'open',return_value=123),patch.object(diag.os,'close'):
            with self.assertRaisesRegex(ValueError,'succeeded'):diag.blocked_write(path)
        self.assertFalse(path.exists())
    def elf(self,*,machine=183,interpreter=False):
        data=bytearray(120);data[:6]=b'\x7fELF\x02\x01'
        struct.pack_into('<H',data,18,machine);struct.pack_into('<Q',data,32,64);struct.pack_into('<HH',data,54,56,1)
        struct.pack_into('<I',data,64,3 if interpreter else 1)
        path=self.root/'tool';path.write_bytes(data);return path
    def test_recovery_binary_must_be_static_arm64(self):
        self.assertEqual(len(diag.static_arm64(self.elf())),120)
        for path in (self.elf(machine=62),):
            with self.assertRaises(ValueError):diag.static_arm64(path)
        with self.assertRaises(ValueError):diag.static_arm64(self.elf(interpreter=True))
    def radio_command(self,*,loaded=False,devices='',actor=False,swap='0',missing=None):
        def run(args,**kwargs):
            args=list(map(str,args));last=args[-1]
            if 'net.wlan.devices' in args:return SimpleNamespace(returncode=0,stdout=devices)
            if 'vm.swap_total' in args:return SimpleNamespace(returncode=0,stdout=swap)
            if 'pgrep' in Path(args[0]).name:return SimpleNamespace(returncode=0 if actor else 1,stdout='')
            if '-m' in args:return SimpleNamespace(returncode=1 if last==missing else 0,stdout='')
            return SimpleNamespace(returncode=0 if loaded else 1,stdout='')
        return run
    def test_active_radio_loader_swap_or_missing_dependency_blocks_diagnostic(self):
        for options in ({'loaded':True},{'devices':'brcmfmac0'},{'actor':True},{'swap':'4096'},{'missing':'firmware'}):
            with self.subTest(options=options),patch.object(diag,'command',side_effect=self.radio_command(**options)):
                with self.assertRaises(ValueError):diag.radio_idle(self.ram)
    def test_radio_idle_requires_resident_dependencies_and_zero_swap(self):
        with patch.object(diag,'command',side_effect=self.radio_command()) as run:diag.radio_idle(self.ram)
        self.assertEqual({v.args[0][-1] for v in run.call_args_list if '-m' in v.args[0]},{'pci','firmware','wlan','sdiob'})
    def test_unknown_capture_result_refuses_radio(self):
        connection=Mock();connection.execute.return_value=[('complete',9),('unknown',1)]
        with patch.object(diag.sqlite3,'connect',return_value=connection):
            with self.assertRaisesRegex(ValueError,'unknown'):diag.idle_statuses()
        connection.close.assert_called_once()
    def test_unbound_watchdog_prevents_any_preparation_or_rf(self):
        with patch.object(diag,'require_running_trial'),patch.object(diag.os,'geteuid',create=True,return_value=0),patch.object(diag.os,'uname',create=True,return_value=SimpleNamespace(machine='arm64')),patch.object(diag,'WATCHDOG_SHA','__UNBOUND__'),patch.object(diag,'command') as run:
            with self.assertRaisesRegex(ValueError,'not bound'):diag.start(self.root/'archive',self.root/'dog')
        run.assert_not_called()
    def test_plan_is_read_only_and_reports_no_rw_restore(self):
        with patch.object(diag,'command') as run,patch.object(diag.sys,'argv',['diagnose_wifi_005.py','plan']),redirect_stdout(io.StringIO()) as out:diag.main()
        plan=json.loads(out.getvalue());self.assertFalse(plan['rw_restore_supported']);self.assertFalse(plan['physical_wifi_validated']);run.assert_not_called()
    def test_no_bypass_cli_flags_are_accepted(self):
        with patch.object(diag.sys,'argv',['diagnose_wifi_005.py','start','--unsafe']),patch.object(diag,'start') as start,redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):diag.main()
        start.assert_not_called()
    def test_embedded_watchdog_source_and_bound_static_artifact_identity(self):
        self.assertEqual(hashlib.sha256(diag.WATCHDOG_SOURCE.encode()).hexdigest(),'8ec798d6d045e9d827d5bd544837b0555dfa57cd42ebd3cd16565fc87fd4816a')
        self.assertEqual(diag.WATCHDOG_SHA,'b852a5db1804b5237e5b97d66e0bd309eda976af58dfd44ce67629c22dd991bc')
    def test_owned_prep_recovery_uses_synced_reboot_unless_every_physical_provider_is_ro(self):
        for readonly in (False,True):
            ram=self.root/('ram-'+('b' if readonly else 'c')*32);ram.mkdir();(ram/'rescue').write_bytes(b'owned static rescue fixture')
            receipt={'format':diag.FORMAT,'nonce':ram.name[-32:],'kernel_sha256':diag.KERNEL_SHA,'payload_sha256':diag.PAYLOAD_SHA,'boot_time':'boot005','ram_hashes':{'rescue':diag.digest(ram/'rescue')}}
            (ram/'receipt.json').write_text(json.dumps(receipt))
            rows=json.loads(json.dumps(self.rows))
            if not readonly:next(row for row in rows if row['target']=='/var')['options']=['rw']
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if args[-1]=='-p':return SimpleNamespace(stdout=self.mount_text(rows))
                if args[-1]=='kern.boottime':return SimpleNamespace(stdout='boot005')
                if args[-1]=='kern.bootfile':return SimpleNamespace(stdout='/boot/'+diag.KERNEL+'/kernel')
                return SimpleNamespace(stdout='')
            with self.subTest(readonly=readonly),patch.object(diag,'RAM_PREFIX',str(self.root/'ram-')),patch.object(diag,'require_tmpfs'),patch.object(diag,'owned',side_effect=lambda p,**kw:Path(p)),patch.object(diag.os,'geteuid',create=True,return_value=0),patch.object(diag,'command',side_effect=run),redirect_stdout(io.StringIO()):diag.reboot_stable(ram)
            self.assertEqual(calls[-1],[ram/'reboot']+(['-qn'] if readonly else []))
            self.assertFalse(any('-w' in list(map(str,call)) for call in calls))
    def test_collector_failure_signals_cannot_prevent_reboot_and_success_never_enables_writes(self):
        self.assertIn("trap '' HUP INT TERM PIPE",diag.COLLECTOR)
        self.assertNotIn('watchdog.stop',diag.COLLECTOR)
        self.assertNotIn("'-w'",diag.COLLECTOR)
        self.assertIn('PROTECTED_READY SD_REMAINS_READ_ONLY',diag.COLLECTOR)

if __name__=='__main__':unittest.main()

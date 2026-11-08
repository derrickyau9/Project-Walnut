"""Early recovery tests use private files and mocked USB tools, never hardware."""
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('early_adb',HERE/'early_adb.py')
early=importlib.util.module_from_spec(spec)
if os.name!='nt':spec.loader.exec_module(early)


@unittest.skipIf(os.name=='nt','Requires POSIX shell/flock; execute under WSL or FreeBSD')
class EarlyADBTests(unittest.TestCase):
    def test_config_cannot_select_network_transport_or_arbitrary_key_runtime(self):
        for field,value in (('authorized_keys','/tmp/untrusted_keys'),('helper','/bin/sh'),('listen','0.0.0.0:5555')):
            config=dict(early.DEFAULT_CONFIG);config[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):early.helper_command(config)

    def test_helper_uses_fixed_usb_auth_entry_without_var_dependencies(self):
        with patch.object(early,'owned',side_effect=lambda p,**kw:Path(p)):
            command=early.helper_command(dict(early.DEFAULT_CONFIG))
        self.assertEqual(command[:7],['/usr/local/libexec/daos-usb-adb','--vid','0xffff','--pid','0xda05','--wait','--'])
        self.assertEqual(command[7:],['/usr/local/bin/python3.12','-I','-B','/usr/local/libexec/DaOS-recovery/early_daemon.py'])
        self.assertNotIn('/var',repr(command))

    def test_ram_must_be_writable_dedicated_tmpfs(self):
        rows=['tmpfs /DaOS-recovery-ram tmpfs rw 0 0\n',
              '/dev/gpt/DaOS-state /DaOS-recovery-ram ufs rw 0 0\n',
              'tmpfs /DaOS-recovery-ram tmpfs ro 0 0\n',
              'tmpfs /tmp tmpfs rw 0 0\n']
        info=Mock(st_mode=0o40700)
        with patch.object(early,'owned'),patch.object(Path,'stat',return_value=info):
            early.validate_ram(Mock(return_value=rows[0]))
            for row in rows[1:]:
                with self.subTest(row=row),self.assertRaises(ValueError):early.validate_ram(Mock(return_value=row))

    def test_symlinked_or_group_writable_recovery_files_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='early-adb-paths-') as d:
            p=Path(d)/'target';p.write_text('test');p.chmod(0o666)
            link=Path(d)/'link';link.symlink_to(p)
            for name in (p,link):
                with self.subTest(path=name),self.assertRaises(ValueError):early.owned(name)

    def test_status_uses_flock_instead_of_a_stale_pid(self):
        with patch.object(early,'validate_ram'),patch.object(early,'lock_state',side_effect=BlockingIOError):
            self.assertTrue(early.running())
        with patch.object(early,'validate_ram'),patch.object(early,'lock_state',return_value=123),patch.object(early.os,'close') as close:
            self.assertFalse(early.running());close.assert_called_once_with(123)

    def test_normal_supervisor_yields_before_opening_var_logs(self):
        source=HERE.parent/'integration/files/usr/local/libexec/DaOS-usb-adb-session.py'
        spec=importlib.util.spec_from_file_location('normal_usb_for_early_qa',source)
        normal=importlib.util.module_from_spec(spec);spec.loader.exec_module(normal)
        with patch.object(normal,'early_recovery_running',return_value=True),patch.object(normal,'RotatingFileHandler') as logger:
            self.assertEqual(normal.main(),0)
        logger.assert_not_called()

    def test_recovery_entry_retains_auth_and_accepts_no_injected_transport_args(self):
        spec=importlib.util.spec_from_file_location('early_daemon_for_qa',HERE/'early_daemon.py')
        entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
        server=Mock();server.developer_access=Mock(return_value=False)
        original_auth=Mock();server.Connection.authenticate=original_auth
        fake_spec=Mock();fake_spec.loader.exec_module=Mock()
        with patch.object(entry.importlib.util,'spec_from_file_location',return_value=fake_spec),patch.object(entry.importlib.util,'module_from_spec',return_value=Mock()),patch.object(entry.importlib,'import_module',return_value=server),patch.object(sys,'argv',['early_daemon.py','--listen','0.0.0.0:5555','--authorized-keys','/tmp/untrusted']):
            def capture():
                self.assertEqual(sys.argv[1:],['--stdio','--authorized-keys','/etc/daos/adb_keys','--shell','/rescue/sh','--cwd','/','--sync-root','/'])
                self.assertIs(server.Connection.authenticate,original_auth)
                self.assertTrue(server.developer_access())
            server.main.side_effect=capture
            entry.main()
        server.main.assert_called_once()

    def test_template_registration_precedes_selection(self):
        runner=Mock(return_value=Mock(returncode=1));reader=Mock(side_effect=['/boot/kernel/kernel\n','-1\n'])
        with patch.object(Path,'is_file',return_value=True),patch.object(early,'owned'):
            early.configure_usb(runner,reader)
        mutations=[list(v.args[0]) for v in runner.call_args_list if v.args[0][0]!='/sbin/kldstat']
        self.assertEqual(mutations,
                         [['/sbin/kldload','-n','/boot/kernel/usb_template.ko'],
                          ['/sbin/kldload','-n','/boot/kernel/daos_adb_template.ko'],
                          ['/sbin/sysctl','hw.usb.template=55813']])

    def test_preloaded_modules_and_selected_template_do_not_mutate_active_usb(self):
        runner=Mock(return_value=Mock(returncode=0));reader=Mock(side_effect=['/boot/DaOS-recovery/kernel/kernel\n','55813\n'])
        with patch.object(early,'owned') as owned:
            early.configure_usb(runner,reader)
        owned.assert_not_called()
        self.assertEqual([v.args[0] for v in runner.call_args_list],
                         [['/sbin/kldstat','-q','-m','usb_template'],['/sbin/kldstat','-q','-m','daos_adb_template']])

    def test_version_only_template_is_detected_by_resident_file_basename(self):
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            return Mock(returncode=1 if command[-2:]==['-m','usb_template'] else 0)
        reader=Mock(side_effect=['/boot/DaOS-recovery/kernel/kernel\n','55813\n'])
        with patch.object(early,'owned') as owned:early.configure_usb(run,reader)
        owned.assert_not_called()
        self.assertIn(['/sbin/kldstat','-q','-n','usb_template.ko'],calls)
        self.assertTrue(all(v[0]=='/sbin/kldstat'for v in calls))

    def test_ram_root_missing_modules_load_from_explicit_local_paths(self):
        runner=Mock(return_value=Mock(returncode=1));reader=Mock(side_effect=['/boot/DaOS-recovery/kernel/kernel\n','55813\n'])
        with patch.object(early,'owned'):
            early.configure_usb(runner,reader)
        loads=[v.args[0]for v in runner.call_args_list if v.args[0][0]=='/sbin/kldload']
        self.assertEqual(loads,[['/sbin/kldload','-n','/boot/kernel/usb_template.ko'],['/sbin/kldload','-n','/boot/modules/daos_adb_template.ko']])

    def test_owned_supervisor_alone_is_never_reported_as_adb_ready(self):
        with tempfile.TemporaryDirectory(prefix='early-health-') as d:
            ram=Path(d)
            report=early.HealthReporter(ram,ram/'console')
            report.update('helper-waiting-host',helper_pid=123)
            with patch.object(early,'RAM',ram),patch.object(early,'running',return_value=True),patch.object(early,'owned',side_effect=lambda p,**kw:Path(p)):
                state=early.health()
        self.assertTrue(state['supervisor_alive']);self.assertFalse(state['ready'])
        self.assertFalse(state['host_configured']);self.assertFalse(state['adb_authenticated'])

    def test_authentication_and_disconnect_control_readiness(self):
        with tempfile.TemporaryDirectory(prefix='early-health-events-') as d:
            ram=Path(d);report=early.HealthReporter(ram,ram/'console')
            with patch.object(early,'RAM',ram),patch.object(early,'running',return_value=True),patch.object(early,'owned',side_effect=lambda p,**kw:Path(p)):
                report.observe('daos-usb-adb: serving peripheral ugen0.2 (native USB bulk)')
                self.assertTrue(early.health()['host_configured']);self.assertFalse(early.health()['ready'])
                report.observe('{"event":"transport_opened","authentication_required":true}')
                self.assertFalse(early.health()['ready'])
                report.observe('{"event":"authenticated","serial":"DAOS-BSD-DEV"}')
                self.assertTrue(early.health()['ready'])
                report.observe('{"event":"transport_closed","reason":"host disconnected"}')
                self.assertFalse(early.health()['ready']);self.assertFalse(early.health()['adb_authenticated'])

    def test_dead_owner_cannot_leave_stale_authenticated_health(self):
        with tempfile.TemporaryDirectory(prefix='early-health-stale-') as d:
            ram=Path(d);report=early.HealthReporter(ram,ram/'console')
            report.observe('{"event":"authenticated"}')
            with patch.object(early,'RAM',ram),patch.object(early,'running',return_value=False),patch.object(early,'owned',side_effect=lambda p,**kw:Path(p)):
                state=early.health()
        self.assertFalse(state['ready']);self.assertFalse(state['adb_authenticated']);self.assertFalse(state['host_configured'])

    def test_setup_failure_records_actual_stderr_and_bounds_console_retries(self):
        with tempfile.TemporaryDirectory(prefix='early-health-errors-') as d:
            ram=Path(d);report=early.HealthReporter(ram,ram/'console',clock=lambda:100.0)
            error=subprocess.CalledProcessError(1,['/sbin/kldload','/missing/module'],stderr=b'ENOENT: module missing')
            for _ in range(10):report.error('setup-failed',error)
            snapshot=json.loads((ram/'health.json').read_text());console=(ram/'console').read_text()
        self.assertEqual(snapshot['last_error']['returncode'],1)
        self.assertIn('ENOENT',snapshot['last_error']['stderr'])
        self.assertEqual(console.count('phase=setup-failed'),1)
        self.assertIn('ENOENT',console);self.assertFalse(snapshot['adb_authenticated'])

    def test_monitor_reassembles_fragmented_auth_messages_and_clears_exit_state(self):
        early.STOP.clear()
        with tempfile.TemporaryDirectory(prefix='early-health-monitor-') as d:
            ram=Path(d);report=early.HealthReporter(ram,ram/'console')
            code='import os,time;os.write(2,b"daos-usb-adb: serving peripheral ugen0.2 (native USB bulk)\\n");os.write(2,b\'{"event":"auth\');time.sleep(.02);os.write(2,b\'enticated"}\\n\');time.sleep(.02)'
            logger=logging.getLogger('early-monitor-qa');logger.addHandler(logging.NullHandler())
            early.monitor([sys.executable,'-c',code],logger,report=report)
            state=json.loads((ram/'health.json').read_text());console=(ram/'console').read_text()
        self.assertIn('phase=adb-authenticated',console)
        self.assertEqual(state['phase'],'helper-exited');self.assertFalse(state['adb_authenticated'])

    def test_ram_rc_distinguishes_supervisor_from_authenticated_usb(self):
        spec=importlib.util.spec_from_file_location('ram_builder_health_qa',HERE/'build_ram_rescue.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        rc=builder.rescue_rc().decode()
        self.assertNotIn('DAOS_RAM_RESCUE_READY',rc)
        self.assertIn('DAOS_RAM_SUPERVISOR_ALIVE',rc)
        self.assertIn('early_adb.py health',rc)
        self.assertIn('DAOS_RAM_ADB_AUTHENTICATED',rc)

    def run_hook(self,mode):
        with tempfile.TemporaryDirectory(prefix='early-adb-hook-') as directory:
            root=Path(directory);ram=root/'ram';ram.mkdir();(root/'mode').write_text(mode)
            applet=root/'tool'
            applet.write_text('''#!'''+sys.executable+'''
from pathlib import Path
import json,sys
r=Path(__file__).resolve().parent
n=Path(sys.argv[0]).name
args=sys.argv[1:]
with (r/'calls').open('a') as f:f.write(json.dumps([n,*args])+'\\n')
mode=(r/'mode').read_text()
if n=='mount' and args==['-p']:
 if mode!='fresh':print('devfs /dev devfs rw 0 0')
 if mode in ('mounted','running'):print('tmpfs '+str(r/'ram')+' tmpfs rw 0 0')
 if mode=='wrong-ram':print('/dev/da0 '+str(r/'ram')+' ufs rw 0 0')
elif n=='python':sys.exit(0 if mode=='running' else 1)
''')
            applet.chmod(0o755)
            for name in ('mount','daemon','python'):(root/name).symlink_to(applet)
            supervisor=root/'supervisor.py';supervisor.write_text('fixture')
            script=(HERE/'early-init.sh').read_text().replace('RAM=/DaOS-recovery-ram','RAM='+str(ram)).replace('PYTHON=/usr/local/bin/python3.12','PYTHON='+str(root/'python')).replace('SUPERVISOR=/usr/local/libexec/DaOS-recovery/early_adb.py','SUPERVISOR='+str(supervisor)).replace('/rescue/mount',str(root/'mount')).replace('/usr/sbin/daemon',str(root/'daemon'))
            result=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True,timeout=5)
            calls=[json.loads(v) for v in (root/'calls').read_text().splitlines()]
        self.assertEqual(result.returncode,0,result.stderr)
        return calls,result

    def test_hook_mounts_devfs_then_private_ram_then_launches_without_var(self):
        calls,_=self.run_hook('fresh')
        self.assertEqual(calls[0],['mount','-p'])
        self.assertEqual(calls[1],['mount','-t','devfs','devfs','/dev'])
        self.assertEqual(calls[2][:6],['mount','-t','tmpfs','-o','size=32m,mode=0700','tmpfs'])
        self.assertEqual(calls[-1][0],'daemon')
        self.assertNotIn('/var',repr(calls))

    def test_hook_is_idempotent_with_live_owner(self):
        calls,_=self.run_hook('running')
        self.assertEqual([c[0]for c in calls],['mount','python'])

    def test_hook_restarts_after_killed_owner_without_remounting(self):
        calls,_=self.run_hook('mounted')
        self.assertEqual([c[0]for c in calls],['mount','python','daemon'])

    def test_hook_rejects_persistent_ram_path_without_blocking_local_boot(self):
        calls,result=self.run_hook('wrong-ram')
        self.assertEqual([c[0]for c in calls],['mount'])
        self.assertIn('not tmpfs',result.stderr)


if __name__=='__main__':unittest.main()

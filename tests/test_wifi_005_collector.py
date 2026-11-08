"""Execute the RAM collector with simulated devices; never touch hardware."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'system/freebsd/raspberrypi'
sys.path.insert(0, str(SOURCE))
try:
    spec = importlib.util.spec_from_file_location('collector_diag', SOURCE/'diagnose_wifi_005.py')
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
finally:
    sys.path.pop(0)

APPLET = r'''#!/usr/bin/python3
import hashlib, os, sys, time
from pathlib import Path
root=Path(__file__).resolve().parent
name=Path(sys.argv[0]).name
args=sys.argv[1:]
mode=(root/'mode').read_text()
if name=='cat':
    for name in args:
        p=Path(name)
        if mode=='log-read-error' and p.name.startswith('chunk-'):sys.exit(1)
        if p.exists():sys.stdout.buffer.write(p.read_bytes())
elif name=='dd':
    fields=dict(v.split('=',1) for v in args)
    if fields['count']=='16':
        counter=root/'sample-count'
        n=int(counter.read_text())+1 if counter.exists() else 1
        counter.write_text(str(n))
        if mode=='baseline-error' or (n==2 and mode=='post-error'):sys.exit(1)
        data=b'b' if n==2 and mode=='corruption' else b'a'
        Path(fields['of']).write_bytes(data*1048576)
        print(('65536' if mode=='short-read' else '1048576')+' bytes transferred in 0.1 secs',file=sys.stderr)
elif name=='md5':
    print(hashlib.md5(Path(args[-1]).read_bytes()).hexdigest())
elif name=='dmesg':
    counter=root/'sample-count'
    if mode=='late-error' and counter.exists() and counter.read_text()=='2':print('mmcsd0: I/O error')
elif name=='kldload':
    (root/'attached').write_text('yes')
    if mode=='attach-error':sys.exit(1)
elif name=='sysctl':
    print('brcmfmac0' if args[-1]=='net.wlan.devices' else 'US')
elif name=='sleep':time.sleep(.005)
elif name=='reboot':(root/'rebooted').write_text(' '.join(args))
elif name=='tail':
    p=Path(args[-1]);sys.stdout.buffer.write(p.read_bytes()[-3800:])
elif name!='kenv':raise RuntimeError('Unexpected applet '+name)
'''


@unittest.skipIf(os.name == 'nt', 'Collector executes a POSIX shell; run under WSL/FreeBSD')
class CollectorTests(unittest.TestCase):
    def run_collector(self, mode):
        with tempfile.TemporaryDirectory(prefix='wifi005-collector-') as directory:
            root=Path(directory)
            (root/'mode').write_text(mode)
            applet=root/'applet'
            applet.write_text(APPLET.replace('#!/usr/bin/python3', '#!'+sys.executable))
            applet.chmod(0o755)
            for name in ('cat','dd','md5','dmesg','kldload','sysctl','sleep','reboot','tail','kenv'):
                (root/name).symlink_to(applet)
            script=diag.COLLECTOR.replace('__RAM__',str(root)).replace('__DUMPS__','test.dumpregs').replace('__NONCE__','a'*32)
            p=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True,timeout=15)
            result=(root/'result').read_text().strip()
            reboot=(root/'rebooted').read_text()
            attached=(root/'attached').exists()
            samples=int((root/'sample-count').read_text())
        self.assertEqual(reboot,'-qn')
        return p, result, attached, samples

    def test_success_requires_a_second_identical_sample_after_attach(self):
        p,result,attached,samples=self.run_collector('ok')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(result,'protected-ready')
        self.assertTrue(attached)
        self.assertEqual(samples,2)
        self.assertIn('ROOT_SAMPLE_MATCH_AFTER_RADIO_ATTACH',p.stdout)

    def test_baseline_failure_or_short_read_prevents_radio_attachment(self):
        for mode in ('baseline-error','short-read'):
            with self.subTest(mode=mode):
                p,result,attached,_=self.run_collector(mode)
                self.assertEqual(p.returncode,10)
                self.assertEqual(result,'failed')
                self.assertFalse(attached)

    def test_post_attach_read_error_corruption_or_late_kernel_error_fails(self):
        for mode in ('post-error','corruption','late-error'):
            with self.subTest(mode=mode):
                p,result,attached,samples=self.run_collector(mode)
                self.assertEqual(p.returncode,10,p.stderr)
                self.assertEqual(result,'failed')
                self.assertTrue(attached)
                self.assertEqual(samples,2)
                self.assertNotIn('PROTECTED_READY SD_REMAINS_READ_ONLY',p.stdout)
                if mode=='corruption':
                    self.assertIn('ROOT_SAMPLE_MISMATCH_AFTER_RADIO_ATTACH',p.stdout)

    def test_driver_failure_or_unreadable_kernel_log_cannot_report_success(self):
        for mode in ('attach-error','log-read-error'):
            with self.subTest(mode=mode):
                p,result,attached,samples=self.run_collector(mode)
                self.assertEqual(p.returncode,10,p.stdout)
                self.assertEqual(result,'failed')
                self.assertTrue(attached)
                self.assertEqual(samples,1)


if __name__ == '__main__':
    unittest.main()

"""Run against a fresh, unpacked QA tree inside the existing FreeBSD builder."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT/'system/freebsd/raspberrypi'
sys.path.insert(0, str(SOURCE))
import deploy_wifi_005 as deploy
import diagnose_wifi_005 as diag


def main():
    assert sys.platform.startswith('freebsd')
    report={'platform':sys.platform,'machine':os.uname().machine,
            'physical_wifi_validated':False,'sd_bytes_written':0}
    result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests','-p','test_wifi_005*.py','-v'],cwd=ROOT,capture_output=True,text=True,timeout=120)
    (ROOT/'tests.stdout').write_text(result.stdout)
    (ROOT/'tests.stderr').write_text(result.stderr)
    report['unit_tests_returncode']=result.returncode
    assert result.returncode==0,result.stderr
    scratch=ROOT/'scratch-root';scratch.mkdir()
    before={'boot/nextboot.conf':b'nextboot_enable="NO"\n',
            'boot/loader.conf.local':b'# stable\nkernel="kernel"\n',
            'etc/rc.conf':b'DaOS_wifi_driver_enable="NO"\n',
            'etc/resolv.conf':b'# unchanged\n'}
    for name,data in before.items():
        p=scratch/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    work=ROOT/'stage-work'
    args=[sys.executable,'-B',str(SOURCE/'deploy_wifi_005.py'),'stage','--archive',str(ROOT/'wifi-update-005.tar.gz'),'--stage-root',str(scratch),'--work-dir',str(work)]
    stage=subprocess.run(args,capture_output=True,text=True,timeout=120)
    (ROOT/'stage-result.json').write_text(json.dumps({'returncode':stage.returncode,'stdout':stage.stdout,'stderr':stage.stderr},indent=2))
    assert stage.returncode==0,stage.stderr
    manifest=json.loads((work/'payload-manifest.json').read_text())
    deploy.validate_payload(scratch,manifest)
    assert all((scratch/n).read_bytes()==d for n,d in before.items())
    report['native_staging_files_verified']=len(manifest['files'])
    report['stable_selectors_unchanged']=True
    receipt=json.loads((work/'deployment.json').read_text())
    deploy.restore(scratch,receipt,work)
    assert all((scratch/n).read_bytes()==d for n,d in before.items())
    report['scratch_rollback_verified']=True

    # Exercise the actual FreeBSD rescue sh/dd/md5/cat, on a private file.
    ram=ROOT/'rescue-sample';ram.mkdir()
    for name in ('sh','dd','md5','cat'):(ram/name).symlink_to('/rescue/'+name)
    fixture=ram/'root-fixture'
    data=b'DaOS QA root block'*65536
    data=data[:1048576]
    with fixture.open('wb') as f:f.seek(256*1024**2);f.write(data)
    start=diag.COLLECTOR.index('sample_root() {')
    end=diag.COLLECTOR.index('\nbaseline=$(sample_root)',start)
    function=diag.COLLECTOR[start:end].replace('/dev/gpt/DaOS-root',str(fixture))
    script='R='+str(ram)+'\n'+function+'\nsample_root\n'
    first=subprocess.run(['/rescue/sh','-c',script],capture_output=True,text=True,timeout=10)
    assert first.returncode==0,first.stderr
    assert first.stdout.strip()==hashlib.md5(data).hexdigest()
    full_log=(ram/'sample-read.log').read_text()
    with fixture.open('r+b') as f:f.truncate(256*1024**2+65536)
    short=subprocess.run(['/rescue/sh','-c',script],capture_output=True,text=True,timeout=10)
    assert short.returncode!=0 and not short.stdout
    report['native_rescue_full_read_verified']=True
    report['native_rescue_short_read_rejected']=True
    report['native_dd_full_read_output']=full_log
    report['native_dd_short_read_output']=(ram/'sample-read.log').read_text()
    report['diagnostic_sha256']=hashlib.sha256((SOURCE/'diagnose_wifi_005.py').read_bytes()).hexdigest()
    report['passed']=True
    (ROOT/'native-qa.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()

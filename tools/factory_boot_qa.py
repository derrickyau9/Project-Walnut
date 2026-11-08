#!/usr/bin/env python3
"""Boot only a derived factory QA copy under isolated ARM64 QEMU.

This tests init and volatile identity integration. It has no NIC, USB gadget,
physical card, camera, or DSI hardware and makes no claim to test those devices.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()

def main():
    if sys.platform!='linux' or os.geteuid()==0:raise ValueError('Non-root isolated Linux QA host required')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--image',type=Path,help='Reuse an already decompressed, hash-bound ordinary QA file')
    parser.add_argument('--record',default='core-boot-qa.json',choices=('core-boot-qa.json','final-boot-qa.json'))
    args=parser.parse_args()
    inputs=args.inputs.resolve(strict=True);record=json.loads((inputs/args.record).read_text())
    compressed=inputs/record.get('gzip_file','core-boot-qa.img.gz')
    if compressed.is_symlink() or sha(compressed)!=record['gzip_sha256']:raise ValueError('Bound QA archive mismatch')
    out=args.out
    if out.exists() or out.is_symlink() or out.parent.resolve(strict=True)!=out.parent:raise ValueError('New private QA directory required')
    out.mkdir(mode=0o700);image=out/'factory-core-qa.img'
    if args.image:
        image=args.image
        if image.is_symlink() or not image.is_file() or image.resolve(strict=True)!=image or image.stat().st_size!=record['raw_bytes'] or sha(image)!=record['raw_sha256']:
            raise ValueError('Reused QA image identity mismatch')
    else:
        h=hashlib.sha256();length=0
        with gzip.open(compressed,'rb') as source,image.open('xb') as dest:
            for chunk in iter(lambda:source.read(1024**2),b''):
                length+=len(chunk)
                if length>record['raw_bytes']:raise ValueError('QA decompression exceeded bound')
                h.update(chunk);dest.write(chunk)
        if length!=record['raw_bytes'] or h.hexdigest()!=record['raw_sha256']:raise ValueError('Bound QA image mismatch')
    vars_path=out/'AAVMF_VARS.fd'
    shutil.copyfile('/usr/share/AAVMF/AAVMF_VARS.fd',vars_path)
    socket_path=Path('/tmp/daos-factory-qa-'+str(os.getpid())+'.sock')
    if socket_path.exists():raise ValueError('Owned serial socket unexpectedly exists')
    command=['qemu-system-aarch64','-machine','virt,gic-version=3','-accel','tcg,thread=multi','-cpu','cortex-a72',
        '-smp','2','-m','2048','-display','none','-serial','unix:'+str(socket_path)+',server=on,wait=off',
        '-monitor','none','-no-reboot','-nic','none',
        '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd',
        '-drive','if=pflash,format=raw,file='+str(vars_path),
        '-drive','if=none,id=factory,file='+str(image)+',format=raw,snapshot=on',
        '-device','virtio-blk-pci,drive=factory']
    expected=record['core_files']
    script='''import hashlib,json,os,pathlib,re,stat,subprocess
expected=EXPECTED
def run(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=15)
 return {'returncode':p.returncode,'stdout':p.stdout[-4096:],'stderr':p.stderr[-4096:]}
r={}
r['core_hashes']={n:hashlib.sha256(pathlib.Path('/'+n).read_bytes()).hexdigest() for n in expected}
r['hostid_start']=run(['/bin/sh','/etc/rc.d/hostid','start'])
r['hostid_save']=run(['/bin/sh','/etc/rc.d/hostid_save','start'])
r['mounts']=run(['/sbin/mount','-p'])
r['identity_mode']=stat.S_IMODE(os.stat('/DaOS-identity').st_mode)
r['usb_private_mode']=stat.S_IMODE(os.stat('/DaOS-recovery-ram').st_mode)
public=run(['/usr/bin/su','-m','camera','-c','/bin/cat /etc/machine-id'])
r['camera_can_read_machine_id']=public['returncode']==0 and bool(re.fullmatch('[0-9a-f]{32}\\n?',public['stdout']))
r['hostid_valid']=bool(re.fullmatch('[0-9a-f-]{36}\\n?',pathlib.Path('/etc/hostid').read_text()))
r['identity_links']={n:os.readlink('/etc/'+n) for n in ('hostid','machine-id')}
r['supervisor_status']=run(['/usr/local/bin/python3.12','-I','-B','/usr/local/libexec/DaOS-recovery/early_adb.py','status'])['returncode']
r['kernel']=run(['/sbin/sysctl','-n','kern.bootfile'])
application=APPLICATION
if application:
 manifest_path=pathlib.Path('/usr/share/DaOS/factory-preview-app-manifest.json')
 manifest=json.loads(manifest_path.read_text())
 release=pathlib.Path('/opt/hasslefree/releases')/application['version']
 r['app_manifest_matches']=hashlib.sha256(manifest_path.read_bytes()).hexdigest()==application['manifest_file_sha256']
 r['app_files_match']=all(hashlib.sha256((release/(e['path'][len('opt/hasslefree/'):] if e['path'].startswith('opt/hasslefree/') else 'support/camera-app-installer.py')).read_bytes()).hexdigest()==e['sha256'] for e in manifest['files'])
 from PyQt5.QtCore import QCoreApplication,QTranslator
 qt=QCoreApplication([])
 expected_translations=TRANSLATIONS
 r['native_qt_translations']={}
 r['native_qt_details']={}
 for code,values in expected_translations.items():
  translator=QTranslator(qt)
  loaded=translator.load(str(release/'ui/on-device/locales'/(code+'.qm')))
  translated=[translator.translate('ImageTools',key) for key in TRANSLATION_KEYS]
  r['native_qt_translations'][code]=loaded and translated==values
  r['native_qt_details'][code]={'loaded':loaded,'values':translated}
 worker=run([str(release/'bin/hf-imager'),'--capabilities'])
 r['native_image_worker']=worker['returncode']==0 and json.loads(worker['stdout'])['opencv']=='4.12.0'
 data=pathlib.Path('/DaOS-recovery-ram/factory-qa-native');data.mkdir(mode=0o700)
 requests='{"id":1,"method":"capabilities","params":{}}\\n{"id":2,"method":"close","params":{}}\\n'
 authority=subprocess.run([str(release/'bin/hf-camerad'),'--data-dir',str(data)],input=requests,capture_output=True,text=True,timeout=15)
 replies=[json.loads(line) for line in authority.stdout.splitlines()]
 r['native_camera_authority']=authority.returncode==0 and len(replies)==2 and replies[0].get('result',{}).get('api_version')=='1.1' and replies[0]['result'].get('simulated') is True
print('QA_FACTORY_JSON '+json.dumps(r,sort_keys=True),flush=True)
'''.replace('EXPECTED',json.dumps(expected,indent=1)).replace('APPLICATION',repr(record.get('application'))).replace('TRANSLATIONS',ascii({'en_US':['Method','WB temp.'],'zh_CN':['处理方式','白平衡色温']})).replace('TRANSLATION_KEYS',ascii(['处理方式','白平衡色温']))
    payload=("env LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib:/usr/local/lib/qt5 /usr/local/bin/python3.12 -I -B <<'DAOS_QA_PY'\n"+script+"\nDAOS_QA_PY\n").encode()
    if not payload.isascii():raise ValueError('Serial QA code must use ASCII Unicode escapes')
    received=bytearray();started=time.monotonic();sent=False;entered=False;answer=None;process=None;connection=None
    send_offset=0;next_send=0
    report={'format':'DaOS-factory-native-ARM64-init-QA-v1','command':command,'qa_raw_sha256':record['raw_sha256'],
        'candidate_sha256':record['candidate_sha256'],'physical_hardware_tested':False,'usb_transport_tested':False,
        'application_gui_tested':False,'qa_console_override_only':True,'serial_input_rate_bytes_per_second':800}
    try:
        with (out/'qemu-stderr.log').open('xb') as error:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=error,stderr=subprocess.STDOUT)
            while time.monotonic()-started<180:
                if process.poll() is not None:raise RuntimeError('QA virtual machine exited before assertions')
                if connection is None:
                    try:
                        connection=socket.socket(socket.AF_UNIX);connection.settimeout(.5);connection.connect(str(socket_path));connection.setblocking(False)
                    except OSError:
                        if connection:connection.close()
                        connection=None;time.sleep(.05);continue
                try:chunk=connection.recv(65536)
                except BlockingIOError:chunk=b''
                if chunk:received.extend(chunk)
                if len(received)>8*1024**2:raise ValueError('QA serial output exceeded bound')
                text=received.decode('utf-8','replace')
                if not entered and 'Enter full pathname of shell' in text:
                    connection.sendall(b'\n');entered=True
                if entered and not sent and re.search(r'(^|\n)# ',text):
                    sent=True
                if sent and send_offset<len(payload) and time.monotonic()>=next_send:
                    try:send_offset+=connection.send(payload[send_offset:send_offset+8])
                    except BlockingIOError:pass
                    next_send=time.monotonic()+.01
                matches=re.findall(r'(?:^|\n)QA_FACTORY_JSON (\{[^\r\n]+\})\r?\n',text)
                if matches:answer=json.loads(matches[-1]);break
                time.sleep(.005)
            if answer is None:raise RuntimeError('Bounded ARM64 boot did not return QA assertions')
            rows=[line.split() for line in answer['mounts']['stdout'].splitlines()]
            def mounted(target,kind,mode):
                found=[r for r in rows if len(r)==6 and r[1]==target]
                return len(found)==1 and found[0][2]==kind and mode in found[0][3].split(',')
            checks={'single_user_before_rc':entered,'exact_core_files':answer['core_hashes']==expected,
                'root_readonly':mounted('/','ufs','ro'),'public_identity_tmpfs':mounted('/DaOS-identity','tmpfs','rw'),
                'private_usb_tmpfs':mounted('/DaOS-recovery-ram','tmpfs','rw'),
                'identity_0755':answer['identity_mode']==0o755,'usb_0700':answer['usb_private_mode']==0o700,
                'hostid_start_success':answer['hostid_start']['returncode']==0,
                'hostid_save_success':answer['hostid_save']['returncode']==0 and not answer['hostid_save']['stderr'],
                'valid_generated_hostid':answer['hostid_valid'],'unprivileged_machine_id_readable':answer['camera_can_read_machine_id'],
                'identity_symlinks':answer['identity_links']=={'hostid':'/DaOS-identity/hostid','machine-id':'/DaOS-identity/machine-id'},
                'early_supervisor_running':answer['supervisor_status']==0}
            if record.get('application'):
                checks.update(final_application_manifest=answer['app_manifest_matches'],final_application_files=answer['app_files_match'],
                    native_qt_en_US=answer['native_qt_translations']['en_US'],native_qt_zh_CN=answer['native_qt_translations']['zh_CN'],
                    native_image_worker=answer['native_image_worker'],native_camera_authority=answer['native_camera_authority'])
            report.update(checks=checks,assertions=answer,passed=all(checks.values()))
    except BaseException as exc:
        report.update(passed=False,error={'type':type(exc).__name__,'message':str(exc)})
    finally:
        if connection:connection.close()
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        socket_path.unlink(missing_ok=True)
        (out/'serial.log').write_bytes(received)
        report.update(seconds=time.monotonic()-started,qemu_returncode=process.returncode if process else None,
            serial_sha256=hashlib.sha256(received).hexdigest(),input_qa_image_unchanged=sha(image)==record['raw_sha256'])
        (out/'qa.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('command','assertions')}),flush=True)
    if not report['passed'] or not report['input_qa_image_unchanged']:raise SystemExit(1)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Boot only a derived factory QA copy under isolated ARM64 QEMU.

This tests init and volatile identity integration. It has no NIC, USB gadget,
physical card, camera, or DSI hardware and makes no claim to test those devices.
"""
import argparse
import ast
import base64
import gzip
import hashlib
import inspect
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

def probe_import_identity(release, scratch):
    """Exercise the actual sibling loader and unindexed sidecar fallback in RAM."""
    import sys
    sys.path.insert(0,str(release))
    sys.path.insert(0,str(release/'ui/on-device'))
    import media_catalog
    from apps_controller import _companion
    from media_library import MediaStore
    companion=_companion('media_catalog')
    workspace=_companion('media_workspace')
    results={'same_module':companion is media_catalog,
             'same_CatalogError':companion.CatalogError is media_catalog.CatalogError is workspace.CatalogError}
    catalog=companion.MediaCatalog(scratch)
    catalog.active_library='00000000-0000-0000-0000-000000000001'
    photo='00000000-0000-0000-0000-000000000002'
    legacy=MediaStore(scratch/'legacy');legacy.write(photo,{'rating':3})
    saved=legacy.root/(photo+'.daos.json')
    before=(saved.read_bytes(),saved.stat().st_mtime_ns)
    try:catalog.sidecar(catalog.active_library,photo)
    except workspace.CatalogError:results['photo_not_yet_indexed']=True
    else:results['photo_not_yet_indexed']=False
    store=workspace.CatalogSidecarStore(legacy,catalog,lambda:None)
    results['legacy_rating_fallback']=store.read(photo)['rating']==3
    results['legacy_bytes_and_mtime_unchanged']=before==(saved.read_bytes(),saved.stat().st_mtime_ns)
    missing=store.read('00000000-0000-0000-0000-000000000003')
    results['missing_sidecar_defaults']=missing['rating']==0 and missing['edits']==media_catalog.DEFAULT_EDITS
    class BrokenCatalog:
        active_library=catalog.active_library
        def sidecar(self,*args):raise OSError('private QA catalog failure')
    try:workspace.CatalogSidecarStore(legacy,BrokenCatalog(),lambda:None).read(photo)
    except OSError:results['unexpected_errors_propagate']=True
    else:results['unexpected_errors_propagate']=False
    return results

def main():
    if sys.platform!='linux' or os.geteuid()==0:raise ValueError('Non-root isolated Linux QA host required')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--image',type=Path,help='Reuse an already decompressed, hash-bound ordinary QA file')
    parser.add_argument('--record',default='core-boot-qa.json')
    parser.add_argument('--native-worker',type=Path,help='Optional SHA-bound ARM64 synthetic test worker, uploaded only to private guest RAM')
    parser.add_argument('--native-worker-sha256')
    parser.add_argument('--image-jobs',action='store_true',help='Exercise all three demo JPEGs through the real native run_job adapter in private RAM')
    parser.add_argument('--require-level',action='store_true',help='Require and exercise the actual display-only level controller, with private JSON fixtures and no IMU claim')
    parser.add_argument('--require-import-identity',action='store_true',help='Require the actual sibling-module identity and unindexed legacy sidecar fallback in private RAM')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.json',args.record) or '..' in args.record:raise ValueError('QA receipt must be a simple basename')
    inputs=args.inputs.resolve(strict=True);record=json.loads((inputs/args.record).read_text())
    if args.image_jobs and record.get('application',{}).get('version')!='camera-experience-006':raise ValueError('Demo image-job QA requires the 006 application')
    if args.require_import_identity and record.get('application',{}).get('version')!='camera-experience-006':raise ValueError('Import identity QA requires the 006 application')
    native_worker=None
    if (args.native_worker is None)!=(args.native_worker_sha256 is None):raise ValueError('Test worker path and SHA256 must be supplied together')
    if args.native_worker is not None:
        path=args.native_worker
        if path.is_symlink() or not path.is_file() or not 64<=path.stat().st_size<=256*1024 or not re.fullmatch('[0-9a-f]{64}',args.native_worker_sha256):raise ValueError('Bound ordinary test worker required')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=args.native_worker_sha256 or data[:8]!=b'\x7fELF\x02\x01\x01\x09' or int.from_bytes(data[18:20],'little')!=183:raise ValueError('Test worker must be the pinned FreeBSD ARM64 ELF')
        if record.get('application',{}).get('version')!='camera-experience-006':raise ValueError('Walnut maker QA requires the 006 application')
        encoded=base64.b64encode(gzip.compress(data,mtime=0)).decode('ascii')
        native_worker={'sha256':args.native_worker_sha256,'bytes':len(data),'gzip_base64':[encoded[n:n+64] for n in range(0,len(encoded),64)]}
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
IMPORT_IDENTITY_FUNCTION
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
 from PyQt5.QtCore import QCoreApplication,QTranslator,QUrl
 from PyQt5.QtQml import QQmlComponent,QQmlEngine
 qt=QCoreApplication([])
 expected_translations=TRANSLATIONS
 r['native_qt_translations']={}
 r['native_qt_details']={}
 for code,values in expected_translations.items():
  translator=QTranslator(qt)
  loaded=translator.load(str(release/'ui/on-device/locales'/(code+'.qm')))
  qt.installTranslator(translator)
  engine=QQmlEngine(qt)
  component=QQmlComponent(engine)
  keys=TRANSLATION_KEYS
  qml_source='import QtQml 2.15'+chr(10)+'QtObject { property string first: qsTranslate("ImageTools", '+json.dumps(keys[0])+'); property string second: qsTranslate("ImageTools", '+json.dumps(keys[1])+') }'
  component.setData(qml_source.encode('ascii'),QUrl())
  obj=component.create()
  translated=[obj.property('first'),obj.property('second')] if obj is not None else []
  r['native_qt_translations'][code]=loaded and translated==values
  r['native_qt_details'][code]={'loaded':loaded,'values':translated,'errors':[e.toString() for e in component.errors()]}
  qt.removeTranslator(translator)
 worker=run([str(release/'bin/hf-imager'),'--capabilities'])
 r['native_image_worker']=worker['returncode']==0 and json.loads(worker['stdout'])['opencv']=='4.12.0'
 data=pathlib.Path('/DaOS-recovery-ram/factory-qa-native');data.mkdir(mode=0o700)
 requests='{"id":1,"method":"capabilities","params":{}}\\n{"id":2,"method":"close","params":{}}\\n'
 authority=subprocess.run([str(release/'bin/hf-camerad'),'--data-dir',str(data)],input=requests,capture_output=True,text=True,timeout=15)
 replies=[json.loads(line) for line in authority.stdout.splitlines()]
 r['native_camera_authority']=authority.returncode==0 and len(replies)==2 and replies[0].get('result',{}).get('api_version')=='1.1' and replies[0]['result'].get('simulated') is True
 if REQUIRE_IMPORT_IDENTITY:
  try:r['import_identity_states']=probe_import_identity(release,pathlib.Path('/DaOS-recovery-ram/factory-qa-import-identity'))
  except Exception as error:r['import_identity_states']={'error':type(error).__name__,'message':str(error)[:300]}
 if REQUIRE_LEVEL:
  import sys
  sys.path.insert(0,str(release/'ui/on-device'))
  from level_controller import LevelController,read_bounded
  state=pathlib.Path('/DaOS-recovery-ram/factory-qa-level');state.mkdir(mode=0o700)
  attitude=state/'attitude.json';clock=[12_000_000_000]
  level=LevelController(state,attitude_file=attitude,clock_ns=lambda:clock[0],poll_ms=0)
  states={'default_disabled':not level.enabled and not level.available and level.rollDeg is None}
  level.setEnabled(True,persist=False)
  states['unavailable_without_fake_zero']=not level.available and level.rollDeg is None and level.pitchDeg is None
  attitude.write_text(json.dumps({'format':'Project-Walnut-attitude-v1','valid':True,'monotonic_ns':clock[0],'roll_deg':2.5,'pitch_deg':-1.5}))
  level.refresh();states['fresh_file_values']=level.available and not level.isDemo and level.rollDeg==2.5 and level.pitchDeg==-1.5
  clock[0]+=500_000_001;level.refresh();states['stale_clears_angles']=level.state=='stale' and level.rollDeg is None and level.pitchDeg is None
  level.setDemoAngles(4,-3);clock[0]+=60_000_000_000;level.refresh()
  states['explicit_DEMO_nonexpiring']=level.isDemo and level.available and level.state=='demo' and level.rollDeg==4 and level.pitchDeg==-3
  level.clearDemo();states['clear_DEMO_restores_stale_real_source']=not level.isDemo and not level.available and level.rollDeg is None
  fifo=state/'fifo';os.mkfifo(fifo)
  try:read_bounded(fifo)
  except (ValueError,OSError):states['special_file_refused']=True
  else:states['special_file_refused']=False
  states['no_demo_or_shell_preferences_persisted']=not (state/'level-preferences.json').exists() and not (state/'system-preferences.json').exists()
  level.close();r['level_controller_states']=states
  r['level_fixture_scope']='Private local JSON/fake clock and explicit DEMO; no physical IMU'
 demo=[e for e in manifest['files'] if '/ui/on-device/demo-images/' in e['path'] and e['path'].lower().endswith(('.jpg','.jpeg'))]
 if demo:
  from PyQt5.QtGui import QImage
  r['native_demo_JPEGs']={e['path'].rsplit('/',1)[-1]:not QImage(str(release/e['path'][len('opt/hasslefree/'):])).isNull() for e in demo}
 if IMAGE_JOBS:
  import importlib.util,threading
  mounts=run(['/sbin/mount','-p'])['stdout']
  if any(len(line.split())==6 and line.split()[1]=='/tmp' for line in mounts.splitlines()):raise ValueError('Unexpected existing QA scratch mount')
  mounted=run(['/rescue/mount','-t','tmpfs','-o','size=192m,mode=0700','tmpfs','/tmp'])
  if mounted['returncode']!=0:raise ValueError('Private image-job tmpfs unavailable')
  spec=importlib.util.spec_from_file_location('walnut_actual_image_processing',release/'ui/on-device/image_processing.py')
  processor=importlib.util.module_from_spec(spec);spec.loader.exec_module(processor)
  class FixtureAsset:
   def __init__(self,path):self.path=path
   def open_library_asset(self,identity):
    source=self.path.open('rb');source.media_type='image/jpeg';return source
  r['native_JPEG_jobs']={}
  sources_json=release/'ui/on-device/demo-images/SOURCES.json';sources_before=hashlib.sha256(sources_json.read_bytes()).hexdigest()
  for entry in demo:
   source=release/entry['path'][len('opt/hasslefree/'):];name=source.name
   before=(hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_mtime_ns)
   destination=pathlib.Path('/tmp')/('walnut-'+name)
   cancel=threading.Event();timer=threading.Timer(35,cancel.set);timer.start()
   try:
    result=processor.run_job('develop_jpeg',FixtureAsset(source),None,{'sources':[{'photo_uuid':name,'sha256':entry['sha256'],'byte_count':entry['bytes']}],'max_edge':640,'quality':90},str(destination),cancel_event=cancel,work_dir='/tmp/walnut-image-jobs')
   except Exception as error:
    r['native_JPEG_jobs'][name]={'passed':False,'error':type(error).__name__,'message':str(error)[:300]}
    continue
   finally:timer.cancel()
   image=QImage(str(destination));sample=image.scaled(32,32)
   levels=[sample.pixelColor(x,y).lightness() for x in range(sample.width()) for y in range(sample.height())]
   dimensions=not image.isNull() and 0<image.width()<=640 and 0<image.height()<=640 and image.width()==result['width'] and image.height()==result['height']
   nonblank=bool(levels) and max(levels)-min(levels)>=8
   unchanged=before==(hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_mtime_ns)
   provenance=result['input_sources'][0]['sha256']==entry['sha256'] and result['sha256']==hashlib.sha256(destination.read_bytes()).hexdigest()
   r['native_JPEG_jobs'][name]={'passed':dimensions and nonblank and unchanged and provenance,'width':image.width(),'height':image.height(),'nonblank':nonblank,'source_bytes_and_mtime_unchanged':unchanged,'provenance_SHA_matches':provenance,'output_bytes':destination.stat().st_size,'peak_rss_bytes':result.get('peak_rss_bytes'),'external_task_sidecar_publication_tested':False}
  r['demo_SOURCES_json_unchanged']=hashlib.sha256(sources_json.read_bytes()).hexdigest()==sources_before
worker_spec=NATIVE_WORKER
if worker_spec:
 import base64,gzip,importlib.util
 binary=gzip.decompress(base64.b64decode(''.join(worker_spec['gzip_base64']),validate=True))
 if len(binary)!=worker_spec['bytes'] or hashlib.sha256(binary).hexdigest()!=worker_spec['sha256']:raise ValueError('RAM test worker identity mismatch')
 worker_path=pathlib.Path('/DaOS-recovery-ram/factory-qa-native-worker')
 with worker_path.open('xb') as output:output.write(binary)
 os.chmod(worker_path,0o500)
 capture=pathlib.Path('/DaOS-recovery-ram/factory-qa-walnut.dng')
 generated=run([str(worker_path),'--output',str(capture),'--width','128','--height','96','--exposure-us','12500','--gain','1.25','--scene','scene','--request-id','00000000-0000-0000-0000-000000000006','--sensor-id','HF-Sim','--calibration-id','synthetic-uncalibrated'])
 if generated['returncode']!=0:raise ValueError('Native RAM capture failed')
 raw=capture.read_bytes();import struct
 ifd=struct.unpack_from('<I',raw,4)[0];count=struct.unpack_from('<H',raw,ifd)[0]
 ascii_fields={}
 for index in range(count):
  at=ifd+2+12*index;tag,kind,length,value=struct.unpack_from('<HHII',raw,at)
  if tag in (270,271) and kind==2:
   payload=raw[at+8:at+12][:length] if length<=4 else raw[value:value+length]
   ascii_fields[tag]=payload.rstrip(bytes([0])).decode('ascii')
 r['native_RAW_maker']=ascii_fields.get(271)=='Project Walnut'
 r['native_RAW_namespace']=json.loads(ascii_fields[270])['namespace']=='org.hasslefree.hf-sim.capture.v1'
 source=release/'camera/dng/inspect.py';spec=importlib.util.spec_from_file_location('walnut_actual_app_reader',source)
 reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
 before=(capture.read_bytes(),capture.stat().st_mtime_ns);inspected=reader.inspect_dng(capture)
 r['native_RAW_readonly_reader']=inspected['metadata_state']=='valid' and inspected['request_id']=='00000000-0000-0000-0000-000000000006' and before==(capture.read_bytes(),capture.stat().st_mtime_ns)
print('QA_FACTORY_JSON '+json.dumps(r,sort_keys=True),flush=True)
'''.replace('IMPORT_IDENTITY_FUNCTION',inspect.getsource(probe_import_identity)).replace('EXPECTED',json.dumps(expected,indent=1)).replace('APPLICATION',repr(record.get('application'))).replace('TRANSLATIONS',ascii({'en_US':['Method','WB temp.'],'zh_CN':['处理方式','白平衡色温']})).replace('TRANSLATION_KEYS',ascii(['处理方式','白平衡色温'])).replace('NATIVE_WORKER',json.dumps(native_worker,indent=1) if native_worker else 'None').replace('IMAGE_JOBS',repr(args.image_jobs)).replace('REQUIRE_LEVEL',repr(args.require_level)).replace('REQUIRE_IMPORT_IDENTITY',repr(args.require_import_identity))
    ast.parse(script)
    payload=("env LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib:/usr/local/lib/qt5 /usr/local/bin/python3.12 -I -B <<'DAOS_QA_PY'\n"+script+"\nDAOS_QA_PY\n").encode()
    if not payload.isascii():raise ValueError('Serial QA code must use ASCII Unicode escapes')
    received=bytearray();started=time.monotonic();sent=False;entered=False;answer=None;process=None;connection=None
    send_offset=0;next_send=0
    report={'format':'DaOS-factory-native-ARM64-init-QA-v1','command':command,'qa_raw_sha256':record['raw_sha256'],
        'candidate_sha256':record['candidate_sha256'],'physical_hardware_tested':False,'usb_transport_tested':False,
        'application_gui_tested':False,'qa_console_override_only':True,'serial_input_rate_bytes_per_second':800,
        'boot_deadline_seconds':240 if args.image_jobs else 180,'JPEG_source_adapter':'private fixture mapping known UI demo assets; no physical camera library' if args.image_jobs else None}
    try:
        with (out/'qemu-stderr.log').open('xb') as error:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=error,stderr=subprocess.STDOUT)
            while time.monotonic()-started<report['boot_deadline_seconds']:
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
                if 'native_demo_JPEGs' in answer:checks['native_demo_JPEG_decode']=len(answer['native_demo_JPEGs'])==3 and all(answer['native_demo_JPEGs'].values())
                if args.image_jobs:checks['native_JPEG_render_jobs']=len(answer['native_JPEG_jobs'])==3 and all(result['passed'] for result in answer['native_JPEG_jobs'].values()) and answer['demo_SOURCES_json_unchanged']
                if args.require_level:checks['level_UI_controller_states']=len(answer['level_controller_states'])==8 and all(answer['level_controller_states'].values())
                if args.require_import_identity:checks['sibling_module_identity_and_unindexed_sidecar_fallback']=len(answer['import_identity_states'])==7 and all(answer['import_identity_states'].values())
            if native_worker:
                checks.update(native_RAW_maker=answer['native_RAW_maker'],native_RAW_namespace=answer['native_RAW_namespace'],native_RAW_readonly_reader=answer['native_RAW_readonly_reader'])
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

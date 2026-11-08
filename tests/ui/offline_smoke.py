"""Real command-line capture and saved-plan replay check, separate from Qt.

The caller supplies a tested native executable. Reports identify the actual
Python host; this helper never labels x86 execution as ARM or physical capture.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
NO_AUTOMATION='import importlib.abc,runpy,sys\nclass NoAutomation(importlib.abc.MetaPathFinder):\n def find_spec(self,name,*args):\n  if name=="daos" or name.startswith("daos."): raise ImportError("automation unavailable for basic photography")\nsys.meta_path.insert(0,NoAutomation()); sys.argv=["camera.camerad",*sys.argv[1:]]; runpy.run_module("camera.camerad",run_name="__main__")'


def sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(65536),b''): result.update(block)
    return result.hexdigest()


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())


def _rows(path):
    with sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True) as db:
        return db.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()


def run(native,output,phase,*,timeout=30):
    native=Path(native).resolve(strict=True); output=Path(output).absolute()
    if phase not in {'first','resume'}: raise ValueError('unsupported offline phase')
    if phase=='first':
        output.mkdir(parents=True,exist_ok=False)
        saved={'format':'hf-offline-smoke-state-v2','request_id':str(uuid.uuid4()),'job_id':'offline-'+uuid.uuid4().hex}
        write_new(output/'identity.json',saved)
        previous=None
    else:
        saved=json.loads((output/'identity.json').read_text())
        previous=json.loads((output/'first.json').read_text())
        if saved['format']!='hf-offline-smoke-state-v2' or previous.get('passed') is not True:
            raise RuntimeError('missing prior offline identity/evidence')
    rid=saved['request_id']
    if str(uuid.UUID(rid))!=rid: raise RuntimeError('invalid saved offline UUID')
    counter=0
    def command(argv):
        nonlocal counter
        counter+=1
        result=subprocess.run([sys.executable,'-B',*argv],cwd=ROOT,capture_output=True,text=True,timeout=timeout,
                              env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1'))
        if len(result.stdout.encode())+len(result.stderr.encode())>1024**2:
            raise RuntimeError('offline command result exceeds bound')
        if result.returncode:
            raise RuntimeError('offline command failed: '+str(result.returncode)+' '+result.stderr[-2000:]+result.stdout[-2000:])
        # Workflow commands emit indented JSON; basic capture emits identity then
        # one final record. Decode the whole object first, then the final line.
        try: return json.loads(result.stdout)
        except json.JSONDecodeError: return json.loads(result.stdout.splitlines()[-1])
    basic=['-c',NO_AUTOMATION,'--data-dir',str(output/'basic'),'--authority','native','--native-daemon',str(native)]
    if phase=='first':
        from camera.calibration import create_binding,create_synthetic_package
        binding=create_binding(create_synthetic_package('HF-Sim','target-software-statistics','1',width=128,height=96))
        write_new(output/'binding.json',binding)
        command([*basic,'configure','--exposure-us','11700','--gain','1','--mode','sim-small'])
        command([*basic,'bind-calibration',str(output/'binding.json')])
        captured=command([*basic,'capture','--request-id',rid])
        if captured.get('status')!='complete': raise RuntimeError('basic offline capture did not complete')
    else:
        from camera.calibration import read_binding
        binding=read_binding(output/'binding.json')
    status=command([*basic,'status'])
    if (status['settings']['requested_exposure_us']!=11700 or status.get('settings_persistent') is not True
            or status['calibration']['binding_id']!=binding['binding_id']):
        raise RuntimeError('persisted device settings or calibration selection changed')
    queried=command([*basic,'query',rid])
    if (queried.get('status')!='complete' or queried.get('actual_exposure_us')!=11700
            or queried.get('capture_record_version')!=2 or queried.get('calibration_binding')!=binding
            or queried.get('hardware_calibrated') is not False or queried.get('pixel_corrections_applied') is not False):
        raise RuntimeError('offline query did not preserve actual exposure/request')
    page=command([*basic,'list']); asset=command([*basic,'asset',rid])
    if len(page['items'])!=1 or page['items'][0]['request_id']!=rid:
        raise RuntimeError('offline retained list differs from original request')
    exported=output/(phase+'-basic.dng')
    command([*basic,'export',rid,'--output',str(exported)])
    source=output/'basic/photos'/(rid+'.dng'); source_hash=sha(source)
    if sha(exported)!=source_hash or exported.stat().st_size!=asset['byte_count']:
        raise RuntimeError('offline export differs from original DNG')
    if _rows(output/'basic/journal.sqlite3')!=[(rid,'complete')]:
        raise RuntimeError('offline read/export replayed or changed a capture')
    workflow=['-m','daos.capability_runtime','--data-dir',str(output/'workflow'),
              '--authority','native','--native-daemon',str(native)]
    result=command([*workflow,'run','--job',saved['job_id'],'--exposures-us','7000','13000']) if phase=='first' else command([*workflow,'resume','--job',saved['job_id']])
    if result.get('status')!='completed': raise RuntimeError('offline saved workflow did not complete')
    photo_dir=output/'workflow/simulator/photos'
    photos={path.name:sha(path) for path in sorted(photo_dir.glob('*.dng'))}
    rows=_rows(output/'workflow/simulator/journal.sqlite3')
    if len(rows)!=2 or any(status!='complete' for _,status in rows) or len(photos)!=2:
        raise RuntimeError('offline workflow capture count or completion is inconsistent')
    if previous and (previous['basic']['request_id']!=rid or previous['basic']['dng_sha256']!=source_hash
                     or previous['workflow']['photos']!=photos or previous['workflow']['job_id']!=saved['job_id']):
        raise RuntimeError('offline reboot changed identity or replayed a saved capture')
    report={'format':'hf-offline-camera-smoke-v2','phase':phase,'passed':True,'simulated':True,
            'machine':platform.machine(),'python':platform.python_version(),'commands':counter,
            'basic':{'request_id':rid,'journal_rows':1,'actual_exposure_us':11700,'dng_sha256':source_hash,
                     'export_sha256':sha(exported),'automation_import_blocked':True,
                     'capture_record_version':2,'calibration_id':binding['binding_id'],'package_checksum':binding['package_checksum'],
                     'settings_persistent':True,'hardware_calibrated':False,'pixel_corrections_applied':False},
            'workflow':{'job_id':saved['job_id'],'status':'completed','journal_rows':2,'photos':photos,
                        'resumed_saved_plan':phase=='resume'},'physical_camera_validated':False}
    write_new(output/(phase+'.json'),report)
    return report

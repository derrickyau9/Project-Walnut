"""Bounded adapter to the versioned native image worker; never captures a frame.

run_job is a synchronous BACKGROUND API for the persistent task service. Every
source is a verified authority stream, copied without modification to a private
job directory. There is one native worker in flight across all callers.
"""
from collections import deque
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

KINDS = ('develop_jpeg','develop_tiff16','analyze','hdr','exposure_fusion','focus_stack',
         'denoise_stack','panorama','astro_stack','darkflat','negative')
PROCESSOR_VERSION = 'daos-imager-1.0/opencv-4.12.0'
_GATE = threading.BoundedSemaphore(1)


class ProcessingError(RuntimeError): pass
class ProcessingCancelled(ProcessingError): pass


def worker_path():
    def usable(candidate):
        try:
            if not candidate.is_file():return False
            with candidate.open('rb') as stream:header=stream.read(64)
            if os.name=='nt':return header[:2]==b'MZ'
            machine=183 if platform.machine().lower() in ('aarch64','arm64') else 62
            abi=9 if platform.system()=='FreeBSD' else 0
            return header[:7]==b'\x7fELF\x02\x01\x01' and int.from_bytes(header[18:20],'little')==machine and header[7] in ((abi,) if abi else (0,3))
        except OSError:return False
    here = Path(__file__).resolve()
    if platform.system()=='FreeBSD' and (here.parents[2]/'release-ready.json').is_file():
        candidate=here.parents[2]/'bin/hf-imager'
        return candidate if usable(candidate) else None
    configured = os.environ.get('DAOS_IMAGE_WORKER')
    if configured:
        candidate = Path(configured)
        return candidate if usable(candidate) else None
    candidates = [here.parents[2]/'bin/hf-imager', Path('/usr/local/bin/hf-imager'),
                  here.parents[2]/'build/DaOS_Camera_004/processing/worker-build/Release/hf-imager.exe',
                  here.parents[2]/'build/DaOS_Camera_004/processing/worker-build/hf-imager']
    return next((p for p in candidates if usable(p)), None)


def _directory(value):
    path = Path(value).absolute()
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink(): raise ProcessingError('Image job directories cannot be symbolic links')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir(): raise ProcessingError('Image jobs need an ordinary directory')
    return path


def _source(client, reference, original):
    photo = reference.get('photo_uuid')
    if photo and callable(getattr(client, 'open_library_asset', None)):
        return client.open_library_asset(photo)
    identity = reference.get('original_request_uuid') or reference.get('request_id') or original
    if not identity: raise ProcessingError('A verified library asset identity is required')
    return client.open_capture(identity)


def _prepare(client, reference, original, path, cancel, progress):
    with _source(client, reference, original) as source:
        try:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode): raise ProcessingError('Image source must be a regular file')
            expected = before.st_size
        except (AttributeError, OSError):
            before = None; expected = reference.get('byte_count')
        if expected is not None and (not isinstance(expected,int) or not 0 < expected <= 512*1024*1024):
            raise ProcessingError('Image source exceeds the 512 MiB bound')
        source.seek(0); digest = hashlib.sha256(); count = 0
        with path.open('xb') as output:
            while True:
                if cancel.is_set(): raise ProcessingCancelled('Image task cancelled')
                if shutil.disk_usage(path.parent).free < 64*1024*1024:
                    raise ProcessingError('Insufficient free scratch space')
                block = source.read(1024*1024)
                if not block: break
                count += len(block)
                if count > 512*1024*1024: raise ProcessingError('Image source exceeds size bound')
                digest.update(block); output.write(block)
                if progress: progress({'stage':'prepare','bytes_done':count,'bytes_total':expected})
            output.flush(); os.fsync(output.fileno())
        if before is not None:
            after = os.fstat(source.fileno())
            fields = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
            if fields(before) != fields(after): raise ProcessingError('Original image changed during preparation')
        if expected is not None and count != expected: raise ProcessingError('Original image was truncated')
        expected_digest=reference.get('sha256') or reference.get('fingerprint')
        if isinstance(expected_digest,dict):expected_digest=expected_digest.get('sha256')
        if expected_digest and expected_digest!=digest.hexdigest():
            raise ProcessingError('Original image fingerprint changed; the saved asset was not replaced')
        structure = getattr(source,'retained_structure',{}) or {}
        record = dict(reference, path=str(path), sha256=digest.hexdigest(), byte_count=count)
        for key in ('black_level','white_level','cfa','width','height'):
            if key in structure: record[key] = structure[key]
        record.setdefault('original_request_uuid', getattr(source,'library_original_request_uuid',original))
        record.setdefault('media_type',getattr(source,'media_type',''))
        if 'exposure_us' not in record:
            exposure=reference.get('actual_exposure_us',reference.get('requested_exposure_us'))
            if exposure is not None:record['exposure_us']=exposure
        return record


def run_job(kind, client, original_request_uuid, params, destination,
            progress=None, cancel_event=None, work_dir=None):
    """Execute one image task in a worker process, with bounded source copying.

    params.sources is a list of photo references for multi-input operations.
    max_edge=320/1024 explicitly creates a derivative; region=[x,y,w,h] preserves
    original pixel coordinates. No implicit full-resolution downscale occurs.
    progress receives small dictionaries, never pixels. Cancellation is checked
    during queue wait, copying and each native tile, with kill as a last resort.
    """
    if kind not in KINDS: raise ProcessingError('Unsupported image task kind')
    executable = worker_path()
    if executable is None: raise ProcessingError('Native OpenCV 4.12 image worker is not installed')
    params = dict(params or {})
    if isinstance(params.get('edits'),dict): params = dict(params,**params.pop('edits'))
    cancel = cancel_event or threading.Event()
    while not _GATE.acquire(timeout=.05):
        if cancel.is_set(): raise ProcessingCancelled('Image task cancelled in queue')
    process = None; job_dir = None; error_thread=None; copied=[]; stop = threading.Event(); stderr = deque(maxlen=30)
    identity = uuid.uuid4().hex
    try:
        if cancel.is_set(): raise ProcessingCancelled('Image task cancelled')
        base = _directory(work_dir or (Path(tempfile.gettempdir())/'daos-image-jobs'))
        job_dir = base/('image-'+identity); job_dir.mkdir(mode=0o700)
        references = params.pop('sources',None) or [{'original_request_uuid':original_request_uuid}]
        if not isinstance(references,list) or not 1 <= len(references) <= 24:
            raise ProcessingError('Image processing requires 1 to 24 explicit sources')
        inputs=[]
        for index, reference in enumerate(references):
            if not isinstance(reference,dict): raise ProcessingError('Invalid image source reference')
            path=job_dir/('input-'+str(index)+'.bin');copied.append(path)
            inputs.append(_prepare(client,reference,original_request_uuid,path,cancel,progress))
        cancel_path=job_dir/'cancel'
        request={'format':'daos.image-job.v1','job_id':identity,'kind':kind,'inputs':inputs,
                 'params':params,'scratch':str(job_dir),'cancel_path':str(cancel_path)}
        if destination:
            destination=Path(destination).absolute()
            if destination.exists() or destination.is_symlink():raise ProcessingError('Choose a new output filename')
            request['destination']=str(destination)
            suffix=destination.suffix.lower()
            if suffix=='.png':params['output_format']='png'
            elif suffix in ('.tif','.tiff'):params['output_format']='tiff16'
        elif kind!='analyze':raise ProcessingError('Choose a new local image destination')
        environment=os.environ.copy()
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,cwd=job_dir,env=environment,
                                 creationflags=flags)
        def read_errors():
            for line in iter(process.stderr.readline,b''):
                stderr.append(line[:2000].decode('utf-8',errors='replace').rstrip())
        error_thread=threading.Thread(target=read_errors,daemon=True);error_thread.start()
        def monitor_cancel():
            since=None
            while not stop.wait(.05):
                if cancel.is_set():
                    if since is None:
                        since=time.monotonic();cancel_path.touch(exist_ok=True)
                    elif time.monotonic()-since>8 and process.poll() is None:
                        process.kill();return
        threading.Thread(target=monitor_cancel,daemon=True).start()
        process.stdin.write((json.dumps(request,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8'));process.stdin.close()
        result=None;error=None
        while True:
            line=process.stdout.readline(1024*1024+1)
            if not line:break
            if len(line)>1024*1024:process.kill();raise ProcessingError('Image worker reply exceeds bound')
            event=json.loads(line)
            if event.get('event')=='progress' and progress:progress(event)
            elif event.get('event')=='result':result=event['result']
            elif event.get('event')=='error':error=event
        code=process.wait(timeout=10)
        if error and error.get('code')=='cancelled' or cancel.is_set() and result is None:raise ProcessingCancelled('Image task cancelled')
        if code or error or not isinstance(result,dict):
            raise ProcessingError((error or {}).get('message') or 'Native image worker failed: '+'; '.join(stderr))
        result['original_request_uuid']=original_request_uuid
        result['processor_version']=result.get('processor',PROCESSOR_VERSION)
        result['input_sources']=[{key:record.get(key) for key in
                                 ('photo_uuid','original_request_uuid','sha256','byte_count')}
                                for record in inputs]
        if kind=='analyze' and inputs[0].get('cfa')=='RGGB':
            from raw_analysis import raw_histogram
            try:result.setdefault('analysis',{})['raw']=raw_histogram(inputs[0]['path'],cancel=cancel,progress=progress)
            except InterruptedError as error:raise ProcessingCancelled(str(error)) from error
        if result.get('outputs'):
            first=result['outputs'][0]
            result.update({key:first[key] for key in ('width','height','byte_count','sha256') if key in first})
            result['destination']=first.get('path','')
        return result
    finally:
        stop.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if error_thread:error_thread.join(timeout=1)
        if process is not None:
            for pipe in (process.stdin,process.stdout,process.stderr):
                if pipe is not None and not pipe.closed:
                    try:pipe.close()
                    except OSError:pass
        for path in copied:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        if job_dir:
            for path in job_dir.glob('*.rgb16'):
                try:path.unlink()
                except OSError:pass
            try:(job_dir/'cancel').unlink(missing_ok=True);job_dir.rmdir()
            except OSError:pass
        _GATE.release()


from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot


class ImageProcessingService(QObject):
    """Lightweight GUI facade. The persistent TaskService owns actual execution."""
    changed=pyqtSignal()
    jobRequested=pyqtSignal(str,object,str)

    def __init__(self,state_dir=None,parent=None):
        if isinstance(state_dir,QObject):parent,state_dir=state_dir,None
        super().__init__(parent)
        self._message='';self._busy=False;self._progress=0.;self._submitter=None

    @pyqtProperty(bool,notify=changed)
    def available(self):return worker_path() is not None
    @pyqtProperty(str,notify=changed)
    def workerPath(self):return str(worker_path() or '')
    @pyqtProperty(str,constant=True)
    def version(self):return PROCESSOR_VERSION
    @pyqtProperty('QVariantList',constant=True)
    def algorithms(self):
        labels={'develop_jpeg':'原尺寸 JPEG','develop_tiff16':'16 位 TIFF','analyze':'图像分析',
                'hdr':'HDR 辐射融合','exposure_fusion':'曝光融合','focus_stack':'焦点堆栈',
                'denoise_stack':'多帧降噪','panorama':'全景拼接','astro_stack':'星空对齐堆栈',
                'darkflat':'暗场 / 平场校正','negative':'负片反转'}
        return [dict(id=kind,label=labels[kind],minInputs=2 if kind in KINDS[3:10] else 1) for kind in KINDS]
    @pyqtProperty(bool,notify=changed)
    def busy(self):return self._busy
    @pyqtProperty(float,notify=changed)
    def progress(self):return self._progress
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message or ('本机原生处理已就绪' if self.available else '原生图像工作器尚未安装')
    def setSubmitter(self,function):self._submitter=function
    @pyqtSlot(str,str,str,result=bool)
    def submitJob(self,kind,params_json,destination):
        try:
            params=json.loads(params_json or '{}')
            if kind not in KINDS or not isinstance(params,dict):raise ProcessingError('无效的图像处理任务')
            if not self.available:raise ProcessingError('原生图像工作器尚未安装')
            if self._submitter:self._submitter(kind,params,destination)
            else:self.jobRequested.emit(kind,params,destination)
            self._message='任务已提交到后台队列';self.changed.emit();return True
        except (ValueError,ProcessingError) as error:
            self._message=str(error);self.changed.emit();return False

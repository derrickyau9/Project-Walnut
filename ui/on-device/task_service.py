"""Persistent bounded background jobs shared by media and application tools."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import deque

from PyQt5.QtCore import QObject,QTimer,pyqtProperty,pyqtSignal,pyqtSlot

from media_catalog import _json,CatalogError


class TaskPaused(Exception):pass
class TaskCancelled(Exception):pass


class TaskService(QObject):
    changed=pyqtSignal()
    completed=pyqtSignal(str,object)
    _snapshotReady=pyqtSignal(object)
    _notice=pyqtSignal(str)
    _jobDone=pyqtSignal(str,object)
    _detailReady=pyqtSignal(str)

    def __init__(self,catalog,client_getter,parent=None,*,enabled=True):
        super().__init__(parent);self.catalog=catalog;self.client_getter=client_getter
        self._closed=False;self._enabled=enabled;self._jobs=[];self._message='';self._snapshot_pending=False
        self._active='';self._cancel=threading.Event();self._wake=threading.Event();self._handlers={}
        self._desired_requests={}
        self._detail=''
        self._admission_blocked=False;self._admission_reason='系统维护中'
        self._commands=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-task-metadata');self._command_count=0;self._command_lock=threading.Lock()
        self._snapshotReady.connect(self._accept_snapshot);self._notice.connect(self._set_notice);self._jobDone.connect(self._finished)
        self._detailReady.connect(self._accept_detail)
        self._timer=QTimer(self);self._timer.setInterval(500);self._timer.timeout.connect(self.refresh)
        self._worker=None
        if enabled:
            with catalog.db(write=True) as db:db.execute("UPDATE jobs SET status='paused',desired='pause',stage='进程已中断，可继续原任务' WHERE status IN ('running','queued','pausing')")
            self._worker=threading.Thread(target=self._loop,name='daos-persistent-jobs',daemon=True);self._worker.start();self._timer.start();self.refresh()

    @pyqtProperty('QVariantList',notify=changed)
    def jobs(self):return list(self._jobs)
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message
    @pyqtProperty(str,notify=changed)
    def activeJobId(self):return self._active
    @pyqtProperty(str,notify=changed)
    def detailText(self):return self._detail
    @pyqtProperty(int,notify=changed)
    def pendingCount(self):return sum(row['status'] in {'queued','running','paused','pausing'} for row in self._jobs)

    def register_handler(self,kind,function):self._handlers[kind]=function;self._wake.set()
    def setAdmissionBlocked(self,blocked,reason='系统维护中'):
        # Serialize against the catalog writer: after this returns no accepted
        # request may still commit behind the maintenance quiescence check.
        with self.catalog._lock:
            self._admission_blocked=bool(blocked);self._admission_reason=str(reason)
        self.changed.emit()
    def quiescent(self):
        with self._command_lock:
            if self._command_count:return False
        if self._active:return False
        with self.catalog.db() as db:return db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','pausing') LIMIT 1").fetchone()is None
    def _admit(self):
        if self._admission_blocked:raise CatalogError(self._admission_reason)
    @pyqtSlot(str)
    def inspectJob(self,job_id):
        def read():
            with self.catalog.db() as db:
                row=db.execute('SELECT job_id,kind,status,stage,error,done,total,progress FROM jobs WHERE job_id=?',(job_id,)).fetchone()
                items=db.execute('SELECT ordinal,photo_uuid,status,error,result_json FROM job_items WHERE job_id=? ORDER BY ordinal LIMIT 100',(job_id,)).fetchall()
            if not row:return
            document=dict(row);document['items']=[{'ordinal':r['ordinal'],'photo_uuid':r['photo_uuid'],'status':r['status'],'error':r['error'],'result':json.loads(r['result_json']) if r['result_json'] else None} for r in items]
            if not self._closed:self._detailReady.emit(json.dumps(document,ensure_ascii=False,indent=2))
        self._command(read)
    @pyqtSlot(str)
    def _accept_detail(self,value):self._detail=value;self.changed.emit()

    def _command(self,function):
        with self._command_lock:
            if self._closed or self._command_count>=16:self._message='任务操作队列已满，请稍候';self.changed.emit();return False
            self._command_count+=1
        future=self._commands.submit(function)
        def done(result):
            try:result.result()
            except Exception as exc:
                if not self._closed:self._notice.emit(str(exc))
            finally:
                with self._command_lock:self._command_count-=1
                self._wake.set()
        future.add_done_callback(done);return True

    def enqueue(self,kind,payload,items=None,*,job_id=None):
        """Persist acceptance synchronously; call only from a background service."""
        self._admit();job_id=job_id or str(uuid.uuid4());now=time.time();items=items or []
        encoded=_json(payload)
        with self.catalog.db(write=True) as db:
            self._admit()
            previous=db.execute('SELECT kind,payload FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if previous:
                if previous['kind']!=kind or previous['payload']!=encoded:raise CatalogError('job UUID belongs to another payload')
                return job_id
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=64:raise CatalogError('任务队列已满（64）')
            db.execute('INSERT INTO jobs(job_id,kind,status,desired,payload,total,created,updated) VALUES(?,?,\'queued\',\'run\',?,?,?,?)',(job_id,kind,encoded,len(items),now,now))
            for index,item in enumerate(items):
                db.execute('INSERT INTO job_items(job_id,ordinal,photo_uuid,input_json) VALUES(?,?,?,?)',(job_id,index+1,item.get('photo_uuid',''),_json(item)))
            self._event(db,job_id,'accepted',{'count':len(items)})
        self._wake.set();return job_id

    def submit_selection(self,kind,selection_id,options,destination,*,job_id=None):
        self._admit();job_id=job_id or str(uuid.uuid4());payload={'selection_id':selection_id,'options':options,'destination':destination}
        # Materialize selection inputs in SQLite, not a GUI/worker RAM list.
        with self.catalog.db(write=True) as db:
            self._admit()
            previous=db.execute('SELECT kind,payload FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if previous:
                if previous['kind']!=kind or previous['payload']!=_json(payload):raise CatalogError('job UUID conflict')
                return job_id
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=64:raise CatalogError('任务队列已满（64）')
            db.execute("INSERT INTO jobs(job_id,kind,status,desired,payload,created,updated) VALUES(?,?,'queued','run',?,?,?)",(job_id,kind,_json(payload),time.time(),time.time()))
            db.execute('INSERT OR IGNORE INTO job_items(job_id,ordinal,photo_uuid,input_json) SELECT ?,ordinal,photo_uuid,snapshot FROM selection_items WHERE selection_id=?',(job_id,selection_id))
            count=db.execute('SELECT count(*) FROM job_items WHERE job_id=?',(job_id,)).fetchone()[0]
            if not count:raise CatalogError('选择为空，未创建任务')
            db.execute('UPDATE jobs SET total=? WHERE job_id=?',(count,job_id))
            self._event(db,job_id,'accepted',{'count':count})
        self._wake.set();return job_id

    @pyqtSlot(str,str,str,str,result=str)
    def enqueueSelection(self,kind,selection_id,options_json,destination):
        if self._admission_blocked:self._message=self._admission_reason;self.changed.emit();return ''
        job_id=str(uuid.uuid4())
        self._command(lambda:self.submit_selection(kind,selection_id,json.loads(options_json or '{}'),destination,job_id=job_id))
        return job_id

    def submit_processing(self,kind,params,destination):
        """ImageProcessingService submitter; current selected sources supplied by facade."""
        self._admit();sources=params.get('sources',[])
        if not 1<=len(sources)<=16:raise CatalogError('图像计算需选择 1–16 张照片')
        job_id=str(uuid.uuid4())
        if not self._command(lambda:self.enqueue(kind,{'options':params,'destination':destination},sources,job_id=job_id)):raise CatalogError('任务操作队列已满，请稍候')
        return job_id

    @pyqtSlot(str)
    def pause(self,job_id):self._control(job_id,'pause')
    @pyqtSlot(str)
    def resume(self,job_id):self._control(job_id,'run')
    @pyqtSlot(str)
    def cancel(self,job_id):self._control(job_id,'cancel')
    def _control(self,job_id,desired):
        if desired=='run' and self._admission_blocked:self._message=self._admission_reason;self.changed.emit();return
        if desired=='run' and job_id==self._active:return
        self._desired_requests[job_id]=desired
        if job_id==self._active:self._cancel.set()
        def work():
            with self.catalog.db(write=True) as db:
                if desired=='run':self._admit()
                row=db.execute('SELECT status FROM jobs WHERE job_id=?',(job_id,)).fetchone()
                if not row:raise CatalogError('unknown task')
                if row['status']=='complete':return
                status='queued' if desired=='run' else ('cancelled' if desired=='cancel' and job_id!=self._active else 'paused' if job_id!=self._active else 'pausing')
                db.execute('UPDATE jobs SET desired=?,status=?,error=NULL,updated=? WHERE job_id=?',(desired,status,time.time(),job_id));self._event(db,job_id,'control',{'desired':desired})
                if desired=='run':db.execute("UPDATE job_items SET status='queued',error=NULL WHERE job_id=? AND status IN ('failed','running')",(job_id,))
        self._command(work)

    @pyqtSlot()
    def refresh(self):
        if not self._enabled or self._snapshot_pending or self._closed:return
        self._snapshot_pending=True
        def read():
            try:
                with self.catalog.db() as db:rows=db.execute('SELECT job_id,kind,status,desired,done,total,progress,stage,error,created,updated FROM jobs ORDER BY created DESC LIMIT 100').fetchall()
                result=[dict(row) for row in rows]
            except Exception:result=None
            if not self._closed:
                try:self._snapshotReady.emit(result)
                except RuntimeError:pass
        self._commands.submit(read)
    @pyqtSlot(object)
    def _accept_snapshot(self,rows):
        self._snapshot_pending=False
        if rows is not None:self._jobs=rows
        self.changed.emit()
    @pyqtSlot(str)
    def _set_notice(self,message):self._message=message;self.changed.emit()
    @pyqtSlot(str,object)
    def _finished(self,job_id,result):self.refresh();self.completed.emit(job_id,result)

    def _event(self,db,job_id,kind,detail):db.execute('INSERT INTO job_events(job_id,kind,detail,created) VALUES(?,?,?,?)',(job_id,kind,_json(detail),time.time()))
    def events(self,job_id,after=0,limit=100):
        with self.catalog.db() as db:rows=db.execute('SELECT * FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?',(job_id,after,min(100,limit))).fetchall()
        return [dict(row,detail=json.loads(row['detail'])) for row in rows]

    def _state(self,job_id,**changes):
        changes['updated']=time.time()
        with self.catalog.db(write=True) as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in changes)+' WHERE job_id=?',(*changes.values(),job_id))

    def _check(self,job_id):
        if self._closed:raise TaskPaused()
        requested=self._desired_requests.get(job_id)
        if requested=='pause':raise TaskPaused()
        if requested=='cancel':raise TaskCancelled()
        with self.catalog.db() as db:row=db.execute('SELECT desired FROM jobs WHERE job_id=?',(job_id,)).fetchone()
        if row['desired']=='pause':raise TaskPaused()
        if row['desired']=='cancel':raise TaskCancelled()

    def _loop(self):
        while not self._closed:
            self._wake.wait(.5);self._wake.clear()
            if self._closed:break
            try:
                with self.catalog.db() as db:row=db.execute("SELECT * FROM jobs WHERE status='queued' AND desired='run' ORDER BY created LIMIT 1").fetchone()
                if not row:continue
                self._active=row['job_id'];self._cancel=threading.Event()
                result=self._execute(dict(row))
                if not self._closed:self._jobDone.emit(row['job_id'],result)
            except Exception as exc:
                if self._active:
                    try:self._state(self._active,status='failed',error=str(exc))
                    except Exception:pass
                if not self._closed:self._notice.emit('任务失败：'+str(exc))
            finally:self._active=''

    def _execute(self,job):
        job_id=job['job_id'];kind=job['kind'];payload=json.loads(job['payload']);options=payload.get('options',{})
        self._state(job_id,status='running',stage='准备输入');client=self.client_getter()
        if kind in self._handlers:
            result=self._handlers[kind](payload,self._cancel,lambda **values:self._state(job_id,**values))
            self._state(job_id,status='complete',progress=1,stage='完成');return result
        if client is None:raise CatalogError('相机资料库尚未连接')
        if payload.get('library_id') and client.describe_library()['library_id']!=payload['library_id']:raise CatalogError('任务等待原资料库，当前连接已改变')
        results=deque(maxlen=20);skipped=0;multi=kind in {'hdr','exposure_fusion','focus_stack','denoise_stack','panorama','astro_stack','darkflat'}
        try:
            if multi:
                with self.catalog.db() as db:
                    count=db.execute('SELECT count(*) FROM job_items WHERE job_id=?',(job_id,)).fetchone()[0]
                    if not 2<=count<=16:raise CatalogError('多图任务需要 2–16 张照片')
                    inputs=[json.loads(r[0]) for r in db.execute('SELECT input_json FROM job_items WHERE job_id=? ORDER BY ordinal LIMIT 16',(job_id,)).fetchall()]
                if not 2<=len(inputs)<=16:raise CatalogError('多图任务需要 2–16 张照片')
                result=self._process(job_id,kind,client,inputs[0],dict(options,sources=inputs),payload.get('destination'))
                receipt=self._load_prepared(job_id,inputs[0]['photo_uuid'])
                with self.catalog.db(write=True) as db:db.execute("UPDATE job_items SET status='complete',result_json=? WHERE job_id=?",(_json(result),job_id))
                self._cleanup_publication(job_id,receipt)
                self._state(job_id,done=len(inputs),progress=1);results=[result]
            else:
                while True:
                    self._check(job_id)
                    with self.catalog.db() as db:row=db.execute("SELECT * FROM job_items WHERE job_id=? AND status IN ('queued','running') ORDER BY ordinal LIMIT 1",(job_id,)).fetchone()
                    if row is None:break
                    item=json.loads(row['input_json']);ordinal=row['ordinal'];self._state(job_id,stage='第 '+str(ordinal)+' 项')
                    with self.catalog.db(write=True) as db:db.execute("UPDATE job_items SET status='running',error=NULL WHERE job_id=? AND ordinal=?",(job_id,ordinal))
                    try:
                        result=self._item(job_id,kind,client,item,options,payload.get('destination',''),ordinal)
                        receipt=self._load_prepared(job_id,item['photo_uuid'])
                        with self.catalog.db(write=True) as db:db.execute("UPDATE job_items SET status='complete',result_json=? WHERE job_id=? AND ordinal=?",(_json(result),job_id,ordinal))
                        self._cleanup_publication(job_id,receipt)
                        results.append(result)
                        if result.get('skipped') or str(result.get('purge_result','')).startswith('skipped'):skipped+=1
                    except (TaskPaused,TaskCancelled):raise
                    except Exception as exc:
                        if self._cancel.is_set():self._check(job_id)
                        with self.catalog.db(write=True) as db:db.execute("UPDATE job_items SET status='failed',error=? WHERE job_id=? AND ordinal=?",(str(exc),job_id,ordinal))
                    with self.catalog.db() as db:done=db.execute("SELECT count(*) FROM job_items WHERE job_id=? AND status IN ('complete','failed')",(job_id,)).fetchone()[0]
                    self._state(job_id,done=done,progress=done/max(1,job['total']))
            with self.catalog.db() as db:failed=db.execute("SELECT count(*) FROM job_items WHERE job_id=? AND status='failed'",(job_id,)).fetchone()[0]
            self._state(job_id,status='failed' if failed else 'complete',progress=1,stage=('完成 · 跳过 '+str(skipped)+' 项') if not failed else str(failed)+' 项失败',error=str(failed)+' 项失败' if failed else None)
            return {'job_id':job_id,'status':'failed' if failed else 'complete','skipped':skipped,'results':list(results)[-20:]}
        except (TaskPaused,TaskCancelled) as exc:
            status='paused' if isinstance(exc,TaskPaused) else 'cancelled'
            self._state(job_id,status=status,desired='pause' if status=='paused' else 'cancel',stage='已暂停' if status=='paused' else '已取消')
            return {'job_id':job_id,'status':status}
        except Exception:
            if self._cancel.is_set():
                with self.catalog.db() as db:desired=db.execute('SELECT desired FROM jobs WHERE job_id=?',(job_id,)).fetchone()[0]
                status='paused' if desired=='pause' or self._closed else 'cancelled'
                self._state(job_id,status=status,stage='中断后可继续' if status=='paused' else '已取消');return {'job_id':job_id,'status':status}
            raise

    def _item(self,job_id,kind,client,item,options,destination,ordinal):
        photo=item['photo_uuid'];library=item.get('library_id',self.catalog.active_library)
        if kind!='import' and library and client.describe_library()['library_id']!=library:raise CatalogError('原资料库未连接，未在另一资料库执行操作')
        if kind=='metadata':
            changes=dict(options.get('changes',{}));current=self.catalog.sidecar(library,photo)
            if options.get('preserve_geometry') and 'edits'in changes:
                edits=dict(changes['edits']);existing=current.get('edits',{})
                edits.update(rotation=existing.get('rotation',0),crop=existing.get('crop'));changes['edits']=edits
            merged=dict(current,**changes)
            indexed=self.catalog.photo(library,photo)
            if item.get('metadata_revision') is not None and indexed['metadata_revision']!=item['metadata_revision']:raise CatalogError('metadata revision conflict')
            if 'protected' in changes or 'trashed_at' in changes:
                asset=client.get_library_asset(photo)
                if 'protected'not in changes and asset['protected'] and merged.get('trashed_at')is not None:raise CatalogError('照片已受保护')
                client.set_library_asset_flags(photo,protected=merged.get('protected',False),trashed=merged.get('trashed_at') is not None,expected_change_seq=asset['change_seq'])
            result=self.catalog.mutate(library,photo,changes,expected_revision=item.get('metadata_revision'));self.catalog.flush_outbox();return result
        if kind=='purge':
            current=self.catalog.photo(library,photo)
            if current['protected'] or not current['trashed']:return {'photo_uuid':photo,'skipped':'protected_or_not_trashed'}
            asset=client.get_library_asset(photo)
            if asset['protected']:return {'photo_uuid':photo,'skipped':'authority_protected'}
            client.set_library_asset_flags(photo,protected=False,trashed=True,expected_change_seq=asset['change_seq'])
            operation=str(uuid.uuid5(uuid.UUID(job_id),photo));result=client.purge_library_asset(photo,operation,options.get('confirmation'))
            self.catalog.upsert_asset(library,result);return result
        if kind=='import':
            result=client.import_library_asset(item['source'],photo,progress=lambda done,total:self._progress(job_id,done/max(1,total),'复制导入'),cancel_event=self._cancel)
            self.catalog.upsert_asset(result['library_id'],result);return result
        if kind in {'export_original','export_dng'}:
            extension=item.get('extension','.dng');requested=Path(destination)
            if requested.is_dir():directory=self._directory(destination);target=directory/(photo+extension)
            else:
                if not requested.is_absolute() or not requested.parent.is_dir():raise CatalogError('请选择绝对导出路径')
                directory=requested.parent;target=requested
            prepared=self._load_prepared(job_id,photo)
            if prepared is None:
                if target.exists() or target.is_symlink():raise CatalogError('目标文件已存在，未覆盖或计为成功')
                stage=self.catalog.root/'job-staging'/job_id/photo/uuid.uuid4().hex;stage.mkdir(parents=True,exist_ok=True)
                staged=stage/target.name
                with client.open_library_asset(photo) as source:result=self._copy(source,staged,job_id)
                expected=item.get('fingerprint')or item.get('sha256')
                if isinstance(expected,dict):expected=expected.get('sha256')
                if expected and result['sha256']!=expected:raise CatalogError('原件校验和与冻结选择不符，未发布导出')
                sidecar=stage/(target.name+'.daos.json');self._new_json(sidecar,{'format':'daos.exported-photo.v1','job_id':job_id,'sha256':result['sha256'],'metadata':item})
                result=dict(result,photo_uuid=photo,input_sources=[{'photo_uuid':photo,'original_request_uuid':item.get('original_request_uuid',photo),'sha256':result['sha256'],'byte_count':result['byte_count']}],outputs=[{'path':str(staged),'sha256':result['sha256'],'byte_count':result['byte_count']}])
                publications=[dict(result['outputs'][0],staged_path=str(staged),path=str(target)),self._publication(sidecar,target.with_name(sidecar.name),include=False)]
                prepared={'publication_stage':'prepared','result':result,'publications':publications,'input':item,'options':options}
                self._save_prepared(job_id,photo,prepared)
            return self._publish_prepared(job_id,item,options,prepared)
        return self._process(job_id,{'export_jpeg':'develop_jpeg','export_tiff':'develop_tiff16'}.get(kind,kind),client,item,options,destination)

    def _directory(self,value):
        path=Path(value)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():raise CatalogError('请选择已有的绝对导出目录')
        return path

    def _copy(self,source,target,job_id):
        digest=hashlib.sha256();temporary=None;before=os.fstat(source.fileno())
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.'+job_id+'-',delete=False) as output:
                temporary=Path(output.name)
                while True:
                    self._check(job_id);block=source.read(1024*1024)
                    if not block:break
                    output.write(block);digest.update(block)
                output.flush();os.fsync(output.fileno())
            after=os.fstat(source.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise CatalogError('导出源在读取期间改变')
            if target.exists():
                raise CatalogError('目标同名文件已存在，未覆盖')
            os.link(temporary,target)
            return {'destination':str(target),'sha256':digest.hexdigest(),'byte_count':temporary.stat().st_size}
        finally:
            if temporary:temporary.unlink(missing_ok=True)

    def _new_json(self,path,value):
        encoded=(_json(value)+'\n').encode()
        if path.exists():
            if path.read_bytes()!=encoded:raise CatalogError('旁车同名冲突，未覆盖')
            return
        with path.open('xb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())

    def _process(self,job_id,kind,client,item,options,destination):
        processing=importlib.import_module('image_processing');params=dict(item.get('edits',{}),**options)
        params.setdefault('sources',[item]);params['photo_uuid']=item['photo_uuid']
        if not destination:
            directory=self.catalog.root/'exports';directory.mkdir(exist_ok=True);destination=str(directory/(job_id+('.tif' if kind=='develop_tiff16' else '.json' if kind=='analyze' else '.jpg')))
        elif Path(destination).is_dir():destination=str(Path(destination)/(item['photo_uuid']+('.tif' if kind=='develop_tiff16' else '.json' if kind=='analyze' else '.jpg')))
        target=Path(destination)
        if not target.is_absolute() or not target.parent.is_dir():raise CatalogError('请选择已有目录中的绝对输出路径')
        prepared=self._load_prepared(job_id,item['photo_uuid'])
        if prepared is None:
            if target.exists() or target.is_symlink():raise CatalogError('目标文件已存在，未覆盖或计为成功')
            stage=self.catalog.root/'job-staging'/job_id/item['photo_uuid']/uuid.uuid4().hex;stage.mkdir(parents=True,exist_ok=True)
            staged=stage/target.name
            result=processing.run_job(kind,client,item.get('original_request_uuid',item['photo_uuid']),params,str(staged),
            progress=lambda value:self._progress(job_id,value.get('progress',0) if isinstance(value,dict) else float(value),value.get('stage','图像处理') if isinstance(value,dict) else '图像处理'),cancel_event=self._cancel,work_dir=str(self.catalog.root/'processing-scratch'))
            publications=[]
            for output in result.get('outputs',[]):
                source=Path(output['path']);name=target.name if len(result.get('outputs',[]))==1 and kind!='analyze' else target.stem+'-'+source.name
                publications.append(dict(output,staged_path=str(source),path=str(target.parent/name)))
            prepared={'publication_stage':'prepared','result':result,'publications':publications,'input':item,'options':options}
            manifest=stage/(target.name+'.daos-manifest.json')
            self._new_json(manifest,{'format':'daos.export-job.v1','job_id':job_id,'input':item,'options':options,'outputs':[{k:v for k,v in out.items() if k!='staged_path'}for out in publications],'analysis':result.get('analysis')})
            prepared['publications'].append(self._publication(manifest,target.with_name(manifest.name),include=False))
            self._save_prepared(job_id,item['photo_uuid'],prepared)
        return self._publish_prepared(job_id,item,options,prepared)

    def _load_prepared(self,job_id,photo):
        with self.catalog.db() as db:row=db.execute('SELECT result_json FROM job_items WHERE job_id=? AND photo_uuid=? ORDER BY ordinal LIMIT 1',(job_id,photo)).fetchone()
        value=json.loads(row[0]) if row and row[0] else None
        return value if isinstance(value,dict) and value.get('publication_stage')=='prepared' else None

    def _save_prepared(self,job_id,photo,value):
        with self.catalog.db(write=True) as db:db.execute('UPDATE job_items SET result_json=? WHERE job_id=? AND photo_uuid=?',(_json(value),job_id,photo))

    def _publication(self,source,target,include=True):
        with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        return {'staged_path':str(source),'path':str(target),'sha256':digest,'byte_count':source.stat().st_size,'include':include}

    def _cleanup_publication(self,job_id,receipt):
        if not receipt:return
        owned=(self.catalog.root/'job-staging'/job_id).resolve()
        scratch=(self.catalog.root/'processing-scratch').resolve()
        for output in receipt.get('publications',[]):
            temporary=Path(output['publication_temp']) if output.get('publication_temp') else None
            if temporary and temporary.name.startswith('.daos-'+job_id+'-'):
                try:
                    if temporary.resolve().parent==Path(output['path']).resolve().parent and self._file_identity(temporary)==output.get('publication_identity'):temporary.unlink()
                except Exception:pass
            staged=Path(output['staged_path'])
            try:
                path=staged.resolve()
                if (owned in path.parents or scratch in path.parents) and not staged.is_symlink():
                    with staged.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    if digest==output['sha256']:staged.unlink()
            except Exception:pass

    def _file_identity(self,path):
        if path.is_symlink():raise CatalogError('输出路径是链接，未接受')
        info=path.stat()
        return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]

    def _publish_prepared(self,job_id,item,options,prepared):
        if _json(prepared['input'])!=_json(item) or _json(prepared['options'])!=_json(options):raise CatalogError('已准备输出与冻结的输入/编辑版本不符')
        for output in prepared['publications']:
            self._check(job_id);final=Path(output['path']);marker=output.get('publication_identity')
            if final.exists() or final.is_symlink():
                if marker is None or self._file_identity(final)!=marker:raise CatalogError('同名输出并非此任务发布，未覆盖或计为成功')
            else:
                temporary=Path(output['publication_temp']) if output.get('publication_temp') else None
                if temporary is None or not temporary.exists():
                    recorded=False
                    try:
                        with Path(output['staged_path']).open('rb') as source:
                            with tempfile.NamedTemporaryFile(dir=final.parent,prefix='.daos-'+job_id+'-',delete=False) as target:
                                temporary=Path(target.name);digest=hashlib.sha256()
                                while True:
                                    self._check(job_id);block=source.read(1024*1024)
                                    if not block:break
                                    target.write(block);digest.update(block)
                                target.flush();os.fsync(target.fileno())
                        if digest.hexdigest()!=output['sha256']:raise CatalogError('待发布输出校验失败')
                        output['publication_temp']=str(temporary);output['publication_identity']=self._file_identity(temporary)
                        self._save_prepared(job_id,item['photo_uuid'],prepared);recorded=True
                    finally:
                        if temporary is not None and not recorded:temporary.unlink(missing_ok=True)
                elif self._file_identity(temporary)!=marker:raise CatalogError('待发布文件身份改变，未接受')
                try:os.link(temporary,final)
                except OSError:
                    if os.name=='nt' and not final.exists():os.rename(temporary,final)
                    else:raise
                if os.name!='nt':
                    descriptor=os.open(final.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                    try:os.fsync(descriptor)
                    finally:os.close(descriptor)
                hook=getattr(self,'_after_publish',None)
                if hook:hook(job_id,final)
            with final.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=output['sha256'] or self._file_identity(final)!=output['publication_identity']:raise CatalogError('已发布输出身份/校验和不符')
        result=prepared['result']
        private={'staged_path','publication_temp','publication_identity','include'}
        final_outputs=[{k:v for k,v in output.items() if k not in private} for output in prepared['publications'] if output.get('include',True)]
        result=dict(result,outputs=final_outputs)
        if result.get('analysis'):result['analysis']=dict(result['analysis'],outputs=final_outputs)
        if final_outputs:result['destination']=final_outputs[0]['path']
        for source in result.get('input_sources',[]):
            if source.get('sha256') and source.get('photo_uuid'):self.catalog.record_fingerprint(item['library_id'],source['photo_uuid'],source['sha256'])
        return result

    def _progress(self,job_id,value,stage):
        if time.monotonic()-getattr(self,'_last_progress',0)<.25:return
        self._last_progress=time.monotonic();self._state(job_id,progress=max(0,min(1,float(value))),stage=str(stage)[:160])

    def close(self):
        if self._closed:return
        self._closed=True;self._cancel.set();self._wake.set();self._timer.stop()
        if self._worker:self._worker.join(timeout=12)
        self._commands.shutdown(wait=True,cancel_futures=True)

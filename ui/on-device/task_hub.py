"""One task surface over durable media, original-UUID workflows and transfers."""
from concurrent.futures import ThreadPoolExecutor
import json,time
from PyQt5.QtCore import QObject,QTimer,pyqtProperty,pyqtSignal,pyqtSlot

class TaskHub(QObject):
    changed=pyqtSignal()
    _transfersReady=pyqtSignal(object)
    _notice=pyqtSignal(str)
    def __init__(self,media,workflow,link,parent=None):
        super().__init__(parent);self.media=media;self.workflow=workflow;self.link=link
        self._transfers=[];self._detail='';self._message='';self._closed=False;self._pending=False;self._selected='';self._workflow_action=None;self._seen={}
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-task-hub')
        self._transfersReady.connect(self._accept)
        self._notice.connect(self._set_notice)
        if media:media.changed.connect(self._media_changed)
        workflow.changed.connect(self._workflow_changed)
        self._timer=QTimer(self);self._timer.setInterval(750);self._timer.timeout.connect(self.refresh);self._timer.start();self.refresh()
    @pyqtProperty('QVariantList',notify=changed)
    def jobs(self):
        result=list(self.media.jobs) if self.media else []
        for row in self.workflow.jobs:
            current=row['job_id']==self.workflow.jobId
            result.append(dict(job_id='workflow:'+row['job_id'],kind='workflow',status={'completed':'complete','pending':'queued'}.get(row['status'],row['status']),
                progress=self.workflow.progress if current else 0,done=self.workflow.completed if current else row.get('completed',0),
                total=self.workflow.total if current else row.get('total',0),stage='原拍摄UUID逐项核实',error=self.workflow.errorText if current else row.get('error',''),created=self._seen.setdefault(row['job_id'],time.time()),source='workflow'))
        for row in self._transfers:
            result.append(dict(job_id='transfer:'+row['id'],kind='transfer',status={'ready':'queued','transferring':'running','awaiting_commit':'awaiting_commit'}.get(row['status'],row['status']),
                progress=row['offset']/row['bytes'] if row['bytes'] else 0,done=row['offset'],total=row['bytes'],stage='字节传输 / 接收端 SHA256 确认',error='',created=row['created'],source='transfer'))
        return sorted(result,key=lambda row:(row['status'] not in ('complete','cancelled','failed'),float(row.get('updated') or row.get('created') or 0)),reverse=True)[:160]
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message or (self.media.message if self.media else '')
    @pyqtProperty(str,notify=changed)
    def detailText(self):return self._detail
    @pyqtProperty(str,notify=changed)
    def activeJobId(self):return 'workflow:'+self.workflow.jobId if self.workflow.active else self.media.activeJobId if self.media else ''
    @pyqtProperty(int,notify=changed)
    def pendingCount(self):return sum(row['status'] not in ('complete','cancelled','failed') for row in self.jobs)
    def inspect_snapshot(self,identity):
        row=next((item for item in self.jobs if item['job_id']==identity),None)
        if row is None:raise ValueError('任务不存在')
        result=dict(row)
        if identity.startswith('workflow:') and identity[9:]==self.workflow.jobId:result['steps']=self.workflow.steps
        if identity.startswith('transfer:'):result['note']='传输由原客户端继续；只有接收端完整SHA256匹配后才显示完成'
        return result
    @pyqtSlot(str)
    def inspectJob(self,identity):
        self._selected=identity
        if identity.startswith(('workflow:','transfer:')):
            if identity.startswith('workflow:'):self.workflow.selectJob(identity[9:])
            self._detail=json.dumps(self.inspect_snapshot(identity),ensure_ascii=False,indent=2);self.changed.emit()
        elif self.media:self.media.inspectJob(identity)
    def _media_changed(self):
        if not self._selected.startswith(('workflow:','transfer:')) and self.media:self._detail=self.media.detailText
        self.changed.emit()
    def _workflow_changed(self):
        identities={row['job_id'] for row in self.workflow.jobs}
        self._seen={identity:value for identity,value in self._seen.items() if identity in identities}
        pending=self._workflow_action
        if pending and self.workflow.jobId==pending[0] and self.workflow._record.get('plan'):
            self._workflow_action=None
            try:getattr(self.workflow,pending[1])()
            except (ValueError,KeyError) as error:self._message='流程控制未执行：'+str(error)
        if self._selected.startswith('workflow:'):
            try:self._detail=json.dumps(self.inspect_snapshot(self._selected),ensure_ascii=False,indent=2)
            except ValueError:pass
        self.changed.emit()
    @pyqtSlot(str)
    def pause(self,identity):self._control(identity,'pause')
    @pyqtSlot(str)
    def resume(self,identity):self._control(identity,'resume')
    @pyqtSlot(str)
    def cancel(self,identity):self._control(identity,'cancel')
    def _control(self,identity,action):
        try:
            if identity.startswith('workflow:'):
                actual=identity[9:]
                if self.workflow.active:
                    if self.workflow.jobId!=actual:raise ValueError('另一个流程仍在运行')
                    getattr(self.workflow,action)()
                elif action=='pause':self._message='该流程当前未运行'
                else:
                    self._workflow_action=(actual,action);self.workflow.selectJob(actual);self._workflow_changed()
            elif identity.startswith('transfer:'):
                self._message='正在请求传输'+{'pause':'暂停','resume':'继续','cancel':'取消'}[action]
                future=self._executor.submit(self.link.control_transfer,identity[9:],action)
                def done(task):
                    try:task.result();message='传输已'+{'pause':'暂停','resume':'准备继续，请由原客户端恢复下载','cancel':'取消'}[action]
                    except Exception as error:message='传输操作未完成：'+str(error)
                    if not self._closed:self._notice.emit(message)
                future.add_done_callback(done)
            elif self.media:getattr(self.media,action)(identity)
        except (ValueError,OSError) as error:self._message='任务操作未完成：'+str(error)
        self.changed.emit()
    @pyqtSlot()
    def refresh(self):
        if self._closed:return
        if self.media:self.media.refresh()
        self.workflow.refresh()
        if self._pending:return
        self._pending=True
        def read():
            with self.link._db() as db:return [dict(row) for row in db.execute('SELECT * FROM transfers ORDER BY created DESC LIMIT 60')]
        future=self._executor.submit(read)
        def done(task):
            try:rows=task.result()
            except Exception:rows=[]
            if not self._closed:self._transfersReady.emit(rows)
        future.add_done_callback(done)
    @pyqtSlot(object)
    def _accept(self,rows):self._pending=False;self._transfers=rows;self.changed.emit()
    @pyqtSlot(str)
    def _set_notice(self,message):self._message=message;self.refresh();self.changed.emit()
    def close(self):
        if self._closed:return
        self._closed=True;self._timer.stop();self._executor.shutdown(wait=True,cancel_futures=True)

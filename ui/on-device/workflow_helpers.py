"""Qt bridge for existing journaled workflows using the UI authority handoff."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from daos.capability_runtime.bracket import BracketRunner, WorkflowError
from daos.capability_runtime.manager import WorkflowManager


class CameraWorkflow(QObject):
    changed = pyqtSignal()
    _snapshotReady = pyqtSignal(object, object)

    def __init__(self, camera, data_dir, parent=None):
        super().__init__(parent)
        self._camera = camera
        self._journal = Path(data_dir) / 'apps' / 'workflows.sqlite3'
        self._manager = None; self._runner = None
        self._job = ''; self._jobs = []; self._record = {}; self._error = ''
        self._desired = 'run'; self._cancel = threading.Event(); self._lock = threading.RLock()
        self._closed = False; self._pending = False
        self._supports = callable(getattr(camera, 'runExclusiveWorkflow', None))
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daos-workflow-journal')
        self._snapshotReady.connect(self._on_snapshot)
        finished = getattr(camera, 'workflowFinished', None)
        if finished is not None: finished.connect(self._finished)
        camera_changed = getattr(camera, 'changed', None)
        if camera_changed is not None: camera_changed.connect(self.changed.emit)
        self._timer = QTimer(self); self._timer.setInterval(400); self._timer.timeout.connect(self.refresh)
        if self._supports: self._timer.start(); self.refresh()

    @pyqtProperty(bool, notify=changed)
    def available(self): return self._manager is not None and callable(getattr(self._camera, 'runExclusiveWorkflow', None))
    @pyqtProperty(bool, notify=changed)
    def active(self): return bool(getattr(self._camera, 'workflowActive', False))
    def _camera_ready(self, reconcile=False):
        query=getattr(self._camera,'canRunExclusiveWorkflow',None)
        return (not self._closed and self.available and not self.active and callable(query)
                and bool(query(self._original_request_id() if reconcile else None)))
    @pyqtProperty(bool, notify=changed)
    def canStart(self): return self._camera_ready()
    @pyqtProperty(bool, notify=changed)
    def canResume(self): return self.status in {'paused','unknown'} and self._camera_ready(reconcile=True)
    @pyqtProperty(bool, notify=changed)
    def canCancel(self): return not self._closed and self.available and (self.active or self.canResume)
    @pyqtProperty(str, notify=changed)
    def jobId(self): return self._job
    @pyqtProperty(str, notify=changed)
    def status(self): return self._record.get('status', 'ready' if self.available else 'unavailable')
    @pyqtProperty(str, notify=changed)
    def errorText(self): return self._error or self._record.get('error') or ''
    @pyqtProperty('QVariantList', notify=changed)
    def jobs(self): return list(self._jobs)
    @pyqtProperty('QVariantList', notify=changed)
    def steps(self):
        return [{'step': row['step'] + 1, 'requestId': row['request_id'], 'status': row['status'],
                 'filePath': (row.get('result') or {}).get('file_path', '')}
                for row in self._record.get('steps', [])]
    @pyqtProperty(int, notify=changed)
    def total(self): return len(self._record.get('plan', {}).get('exposures_us', []))
    @pyqtProperty(int, notify=changed)
    def completed(self): return sum(row['status'] == 'completed' for row in self._record.get('steps', []))
    @pyqtProperty(float, notify=changed)
    def progress(self): return self.completed / self.total if self.total else 0.0
    @pyqtProperty(str, notify=changed)
    def desired(self): return self._desired
    @pyqtProperty(str, notify=changed)
    def summaryText(self):
        names = {'completed':'已保存','failed':'失败','cancelled':'已取消','unknown':'待核实',
                 'submitting':'提交中','accepted':'已接收','capturing':'拍摄中'}
        lines = []
        for row in self._record.get('steps', []):
            result = row.get('result') or {}
            exposure = result.get('actual_exposure_us')
            settings = ''
            if type(exposure) is int and exposure > 0:
                settings = ('%g s' % (exposure / 1000000) if exposure >= 1000000 else '1/%g s' % (1000000 / exposure))
            gain = result.get('actual_analogue_gain')
            if type(gain) in (int, float): settings += ' · %gx' % gain
            lines.append('第 %d 张    %s%s' % (row['step'] + 1, names.get(row['status'], row['status']), '\n' + settings if settings else ''))
        return '\n\n'.join(lines) if lines else '选择计划并执行，结果将在这里逐张显示。'

    @pyqtProperty(str, notify=changed)
    def resultText(self):
        rows = self.steps
        states = {'completed':'已保存','running':'执行中','pending':'待执行','accepted':'已接收','capturing':'拍摄中',
                  'submitting':'提交中','cancelled':'已取消','failed':'失败','unknown':'待核实','paused':'已暂停','ready':'就绪'}
        detail = '\n'.join('第 ' + str(row['step']) + ' 张 · ' + states.get(row['status'], row['status']) + '\n' + row['requestId']
                           + ('\n' + row['filePath'] if row['filePath'] else '') for row in rows)
        return (str(self.completed) + '/' + str(self.total) + ' 已保存 · ' + states.get(self.status, self.status)
                + ('\n' + self.errorText if self.errorText else '') + ('\n\n' + detail if detail else ''))

    def _inspect(self):
        if self._manager is None:
            self._manager = WorkflowManager(None, self._journal)
        rows = self._manager.list_jobs()
        identity = self._job or (rows[0]['job_id'] if rows else '')
        record = self._manager.inspect(identity) if identity else {}
        return {'jobs': rows, 'job_id': identity, 'record': record}

    @pyqtSlot()
    def refresh(self):
        if self._closed or self._pending or not self._supports: return
        self._pending = True
        future = self._executor.submit(self._inspect)
        def done(completed):
            try: result, error = completed.result(), None
            except Exception as exc: result, error = None, str(exc)
            if not self._closed:
                try: self._snapshotReady.emit(result, error)
                except RuntimeError: pass  # QObject owner may already be gone.
        future.add_done_callback(done)

    @pyqtSlot(object, object)
    def _on_snapshot(self, value, error):
        self._pending = False
        if error: self._error = str(error)
        elif value:
            self._jobs = value['jobs']
            # A prior inspect must not overwrite a newly started/selected job.
            if not self._job or value['job_id'] == self._job:
                self._job = value['job_id']; self._record = value['record']
        self.changed.emit()

    @pyqtSlot(str)
    def selectJob(self, identity):
        if self.active: return
        if any(row['job_id'] == identity for row in self._jobs):
            self._job = identity; self._record = {}; self._error = ''; self.refresh(); self.changed.emit()

    def start(self, plan):
        if not self.canStart:
            self._error = 'Camera is unavailable or already running a workflow'; self.changed.emit(); return False
        previous = self._job, self._record
        self._job = 'camera-' + uuid.uuid4().hex
        self._record = {'job_id': self._job, 'plan': plan, 'status': 'pending', 'steps': []}
        accepted=self._launch(plan, 'run')
        if not accepted:
            self._job,self._record=previous;self.changed.emit()
        return accepted

    def _original_request_id(self):
        unresolved = [row for row in self._record.get('steps', []) if row['status'] not in {'completed','failed','cancelled'}]
        return self._record.get('unknown_request_id') or (unresolved[-1]['request_id'] if unresolved else '')

    def _launch(self, plan, desired, *, reconcile=False):
        if not self._camera_ready(reconcile=reconcile):
            self._error = 'Finish the current camera operation before starting a workflow'
            self.changed.emit();return False
        identity = self._job
        previous_desired,previous_cancel=self._desired,self._cancel
        self._desired = desired; self._error = ''; self._cancel = threading.Event()
        if desired == 'cancel': self._cancel.set()
        cancel = self._cancel
        def execute(client):
            runner = BracketRunner(client, self._journal)
            with runner.execution():
                runner.prepare(identity, plan)
                with self._lock:
                    self._runner = runner
                    runner.set_desired(identity, self._desired)
                try:
                    result = runner._run(identity, plan, cancel)
                    steps = result.get('steps', [])
                    result['latest_request_id'] = steps[-1]['request_id'] if steps else ''
                    unresolved = [row for row in steps if row['status'] not in {'completed', 'failed', 'cancelled'}]
                    result['unknown_request_id'] = unresolved[-1]['request_id'] if unresolved else ''
                    return result
                finally:
                    with self._lock: self._runner = None
        original_id = self._original_request_id()
        if reconcile and original_id:
            accepted = self._camera.runExclusiveWorkflow(execute, reconcile_request_id=original_id)
        else:
            accepted = self._camera.runExclusiveWorkflow(execute)
        if not accepted:
            self._error = 'Finish the current camera operation before starting a workflow'
            self._desired,self._cancel=previous_desired,previous_cancel
        self.changed.emit()
        return bool(accepted)

    @pyqtSlot()
    def pause(self): self._control('pause')
    @pyqtSlot(result=bool)
    def cancel(self):
        if self._closed:return False
        if self.active:
            self._cancel.set(); self._control('cancel');return True
        elif self.status in {'paused', 'unknown'}:
            return self._launch(self._record['plan'], 'cancel', reconcile=True)
        return False
    @pyqtSlot(result=bool)
    def resume(self):
        if not self.active and self.status in {'paused', 'unknown'}:
            return self._launch(self._record['plan'], 'run', reconcile=True)
        return False

    def _control(self, desired):
        if not self.active: return
        self._desired = desired
        identity = self._job
        def persist():
            with self._lock:
                runner = self._runner
                if runner is not None: runner.set_desired(identity, desired)
        self._executor.submit(persist)
        self.changed.emit()

    @pyqtSlot(object, object)
    def _finished(self, result, error):
        if error: self._error = str(error)
        if isinstance(result, dict) and result.get('job_id') == self._job:
            self._record = result
        self.refresh(); self.changed.emit()

    def close(self):
        if self._closed: return
        if self.active:
            self._cancel.set(); self._desired = 'cancel'
            with self._lock:
                if self._runner is not None:
                    self._runner.set_desired(self._job, 'cancel')
        self._closed = True; self._timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)

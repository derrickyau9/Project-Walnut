"""Action-correlated bounded performance traces, including long GUI stalls."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import uuid
from PyQt5.QtCore import QObject,QEvent,QTimer,Qt,pyqtProperty,pyqtSignal,pyqtSlot


def percentile(values,fraction):
    values=sorted(values)
    return round(values[min(len(values)-1,int((len(values)-1)*fraction))],2) if values else None

def peak_memory_bytes():
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            class Memory(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
            value=Memory();value.cb=ctypes.sizeof(value)
            get=ctypes.windll.psapi.GetProcessMemoryInfo;get.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
            current=ctypes.windll.kernel32.GetCurrentProcess;current.restype=wintypes.HANDLE
            if get(current(),ctypes.byref(value),value.cb):return value.PeakWorkingSetSize
        else:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    except (OSError,AttributeError,ImportError):pass
    return None


class PerformanceRecorder(QObject):
    changed=pyqtSignal()
    _saved=pyqtSignal(str)
    def __init__(self,destination=None,parent=None,*,clock=time.perf_counter_ns):
        super().__init__(parent)
        self.destination=Path(destination) if destination else None
        self.clock=clock
        self.frames=deque(maxlen=1800);self.inputs=deque(maxlen=512)
        self.preview=deque(maxlen=300);self.preview_latency=deque(maxlen=300)
        self.preview_intervals=deque(maxlen=600);self._preview_active=True;self._last_preview=None
        self.heartbeat_delays=deque(maxlen=1800);self.completed_actions=deque(maxlen=256)
        self._actions={};self._pending_inputs=deque(maxlen=128);self._activities=set()
        self._last_frame=self._last_heartbeat=None
        self._swaps=self._idle_gaps=self._long_stalls=0;self._maximum_stall=0.
        self._save_error='';self._save_pending=False;self._closed=False
        self._key_override=None
        self._writer=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-performance')
        self._saved.connect(self._save_finished)
        self._timer=QTimer(self);self._timer.setInterval(1000);self._timer.timeout.connect(self.changed);self._timer.start()
        self._heartbeat=QTimer(self);self._heartbeat.setTimerType(Qt.PreciseTimer)
        self._heartbeat.setInterval(50);self._heartbeat.timeout.connect(self._pulse);self._heartbeat.start()

    def eventFilter(self,watched,event):
        kind=event.type();now=self.clock()
        if kind==QEvent.KeyRelease:self._key_override=None
        if kind in (QEvent.ShortcutOverride,QEvent.KeyPress):
            identity=(event.key(),int(event.modifiers()))
            if kind==QEvent.ShortcutOverride:
                # Qt Quick shortcuts can consume a key before KeyPress reaches
                # application filters. Count the override, without key content.
                if self._key_override and self._key_override[:2]==identity and now-self._key_override[2]<500_000:return False
                self._key_override=(*identity,now)
            elif self._key_override and self._key_override[:2]==identity and now-self._key_override[2]<10_000_000:
                self._key_override=None;return False
        if kind in (QEvent.MouseButtonPress,QEvent.TouchBegin,QEvent.KeyPress,QEvent.ShortcutOverride):
            # No typed text, passwords, clipboard contents or key names in traces.
            identity=self.beginAction('input',(watched.objectName() or type(watched).__name__)[:80])
            self._pending_inputs.append(identity)
        return False

    @pyqtSlot(str,str,result=str)
    def beginAction(self,kind,control):
        identity=uuid.uuid4().hex
        if len(self._actions)>=128:self.finish(next(iter(self._actions)),'expired')
        self._actions[identity]={'id':identity,'kind':kind[:40],'control':control[:80],
          'start_ns':self.clock(),'stages':[],'first_visible_ms':None}
        return identity

    @pyqtSlot(str,str)
    def stage(self,identity,name):
        action=self._actions.get(identity)
        if action is not None and len(action['stages'])<16:
            action['stages'].append({'name':name[:60],'ms':round((self.clock()-action['start_ns'])/1e6,3)})

    @pyqtSlot(str)
    def firstVisible(self,identity):
        action=self._actions.get(identity)
        if action is not None and action['first_visible_ms'] is None:
            action['first_visible_ms']=(self.clock()-action['start_ns'])/1e6
            self.inputs.append(action['first_visible_ms'])

    @pyqtSlot(str,str)
    def finish(self,identity,result):
        action=self._actions.pop(identity,None)
        if action is None:return
        action=dict(action);action['duration_ms']=round((self.clock()-action.pop('start_ns'))/1e6,3)
        action['result']=result[:80];self.completed_actions.append(action)

    @pyqtSlot(str,bool)
    def setActivity(self,name,active):
        if active:self._activities.add(name[:80])
        else:self._activities.discard(name[:80])

    @pyqtSlot()
    def frameSwapped(self):
        now=self.clock();self._swaps+=1
        if self._last_frame is not None:
            interval=(now-self._last_frame)/1e6
            # Static scenes need no swap. Active gaps, however long, stay visible.
            if self._activities or self._pending_inputs or interval<100:
                self.frames.append(interval)
                if interval>=250:self._record_stall(interval)
            else:self._idle_gaps+=1
        self._last_frame=now
        while self._pending_inputs:
            identity=self._pending_inputs.popleft();self.firstVisible(identity);self.finish(identity,'visible-frame')

    def _record_stall(self,milliseconds):
        self._long_stalls+=1;self._maximum_stall=max(self._maximum_stall,milliseconds)

    def _pulse(self):
        now=self.clock()
        if self._last_heartbeat is not None:
            delay=max(0.,(now-self._last_heartbeat)/1e6-50.)
            self.heartbeat_delays.append(delay)
            if delay>=250:self._record_stall(delay)
        self._last_heartbeat=now
        for identity,action in list(self._actions.items()):
            if now-action['start_ns']>30_000_000_000:self.finish(identity,'timeout')

    @pyqtSlot(bool)
    def setPreviewActive(self,active):
        active=bool(active)
        if active!=self._preview_active:self._last_preview=None
        self._preview_active=active;self.setActivity('live-preview',active)

    def previewPresented(self,produced_ns):
        now=self.clock();self.preview.append(now);self.preview_latency.append(max(0.,(now-produced_ns)/1e6))
        if self._preview_active:
            if self._last_preview is not None:self.preview_intervals.append((now-self._last_preview)/1e6)
            self._last_preview=now

    @pyqtProperty('QVariantMap',notify=changed)
    def metrics(self):
        fps=(len(self.preview_intervals)*1000/sum(self.preview_intervals) if self.preview_intervals and sum(self.preview_intervals)>0 else None)
        return {'scope':'application actions/GUI heartbeat/swaps; not sensor-to-panel latency',
          'frameSamples':len(self.frames),'frameP95Ms':percentile(self.frames,.95),'frameP99Ms':percentile(self.frames,.99),
          'frameMaxMs':max(self.frames,default=None),'inputSamples':len(self.inputs),'inputP95Ms':percentile(self.inputs,.95),
          'inputP99Ms':percentile(self.inputs,.99),'swapCount':self._swaps,'idleSwapGaps':self._idle_gaps,
          'longStallEvents':self._long_stalls,'maximumStallMs':round(self._maximum_stall,2),
          'heartbeatP99Ms':percentile(self.heartbeat_delays,.99),'previewFps':round(fps,2) if fps else None,'previewIntervalMaxMs':max(self.preview_intervals,default=None),
          'previewWorkerP95Ms':percentile(self.preview_latency,.95),'actionsInFlight':len(self._actions),
          'actionsCompleted':len(self.completed_actions),'uiPeakMemoryBytes':peak_memory_bytes(),'saveError':self._save_error,'savePending':self._save_pending}

    @pyqtProperty('QVariantList',notify=changed)
    def actions(self):return list(self.completed_actions)[-32:]

    def _write(self,document):
        self.destination.parent.mkdir(parents=True,exist_ok=True);temporary=self.destination.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(document,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.destination)

    @pyqtSlot(result=bool)
    def save(self):
        if self.destination is None or self._save_pending or self._closed:return False
        self._save_pending=True;self.changed.emit()
        document={'format':'daos.performance.v2','recorded_at':time.time(),**self.metrics,'savePending':False,'actions':list(self.completed_actions)}
        future=self._writer.submit(self._write,document)
        def done(result):
            try:result.result();error=''
            except Exception as exc:error=str(exc)
            if not self._closed:self._saved.emit(error)
        future.add_done_callback(done);return True

    @pyqtSlot(str)
    def _save_finished(self,error):
        self._save_error=error;self._save_pending=False;self.changed.emit()

    def close(self):
        if self._closed:return
        self._closed=True;self._timer.stop();self._heartbeat.stop()
        if self.destination is not None:
            document={'format':'daos.performance.v2','recorded_at':time.time(),**self.metrics,'savePending':False,'actions':list(self.completed_actions)}
            try:self._writer.submit(self._write,document).result()
            except Exception as error:self._save_error=str(error)
        self._writer.shutdown(wait=True);self._save_pending=False

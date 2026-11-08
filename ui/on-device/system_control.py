"""Asynchronous user settings, portable backups, update inspection and idle UI."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
from pathlib import Path
import platform
import re
import socket
import time
import uuid
from zoneinfo import ZoneInfo

from PyQt5.QtCore import QObject,QEvent,QTimer,pyqtProperty,pyqtSignal,pyqtSlot

PREFERENCES={'language':'zh_CN','timezone':'America/Los_Angeles','idle_seconds':300,'developer_mode':True}


from system_archive import (atomic as _atomic,safe_path as _safe_directory,create_backup,inspect_backup,inspect_update,\
    restore_documents,recover_restore_transactions,validate_preferences,strict_json,BACKUP_FILES)

def service_request(operation,**fields):
    if not hasattr(socket,'AF_UNIX'):raise OSError('此平台没有原生系统服务套接字')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(35);connection.connect('/var/run/DaOS-system/control.sock')
        connection.sendall(json.dumps(dict(op=operation,**fields)).encode()+b'\n')
        data=bytearray()
        while b'\n' not in data:
            block=connection.recv(4096)
            if not block:raise OSError('系统服务提前关闭')
            data.extend(block)
            if len(data)>262144:raise ValueError('系统回复超过限制')
    value=json.loads(data.split(b'\n',1)[0])
    if value.get('error'):raise ValueError(value['error'])
    return value



class SystemController(QObject):
    changed=pyqtSignal()
    finished=pyqtSignal(str,object,str)
    restored=pyqtSignal()
    powerScheduled=pyqtSignal()

    def __init__(self,data_root,camera=None,parent=None,language=None):
        super().__init__(parent)
        self.root=Path(data_root).absolute();self.camera=camera
        self._preferences=dict(PREFERENCES);self._state={};self._busy=False;self._closed=False
        self._message='';self._error='';self._last_input=time.monotonic();self._asleep=False
        self._backup=None;self._update={};self._catalog=None
        self._tasks=None;self._maintenance='';self._operation='';self._restart_required=False;self._restore_fault=False;self._restore_info={}
        self._inspection_revision={'backup':0,'update':0}
        self._admission_owned=False
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-system')
        self.finished.connect(self._apply)
        try:
            recovery=recover_restore_transactions(self.root)
            if recovery:self._restart_required=True;self._message='上次配置恢复已核对，需要重新载入界面'
        except Exception as error:self._restore_fault=True;self._restart_required=True;self._error='配置恢复记录需核对：'+str(error)
        try:
            path=self.root/'system-preferences.json'
            if path.exists():
                if path.is_symlink() or path.stat().st_size>16384:raise ValueError('系统设置无效')
                value=json.loads(path.read_text(encoding='utf-8'))
                self._validate(value);self._preferences=value
        except (OSError,ValueError,KeyError,TypeError) as error:self._error=str(error)
        if language is not None:
            # Startup override is a small local preference transaction, before
            # asynchronous maintenance/status work is admitted.
            from localization import validate_language
            selected=validate_language(language)
            values=self._validate(dict(self._preferences,language=selected))
            _atomic(self.root/'system-preferences.json',json.dumps(values,ensure_ascii=False).encode())
            self._preferences=values
        self._timer=QTimer(self);self._timer.setInterval(1000);self._timer.timeout.connect(self._idle);self._timer.start()

    def attachCatalog(self,catalog):self._catalog=catalog
    def attachTasks(self,tasks):self._tasks=tasks

    @staticmethod
    def _validate(value):
        return validate_preferences(value)

    @pyqtProperty(bool,notify=changed)
    def busy(self):return self._busy
    @pyqtProperty(str,notify=changed)
    def message(self):return self._error or self._message
    @pyqtProperty('QVariantMap',notify=changed)
    def preferences(self):return dict(self._preferences)
    @pyqtProperty('QVariantMap',notify=changed)
    def state(self):return dict(self._state)
    @pyqtProperty('QVariantMap',notify=changed)
    def updateInfo(self):return dict(self._update)
    @pyqtProperty(bool,notify=changed)
    def screenAsleep(self):return self._asleep
    @pyqtProperty(str,notify=changed)
    def clockText(self):return datetime.datetime.now(ZoneInfo(self._preferences['timezone'])).strftime('%Y-%m-%d %H:%M:%S')
    @pyqtProperty('QVariantList',constant=True)
    def timezones(self):return ['America/Los_Angeles','America/New_York','UTC','Asia/Shanghai','Asia/Tokyo','Europe/London','Europe/Berlin']
    @pyqtProperty('QVariantMap',notify=changed)
    def backupInfo(self):return dict(self._backup[0]) if self._backup else {}
    @pyqtProperty(bool,notify=changed)
    def restartRequired(self):return self._restart_required
    @pyqtProperty(str,notify=changed)
    def operation(self):return self._operation if self._busy else ''
    @pyqtProperty(bool,notify=changed)
    def nativeReady(self):return self._state.get('native') is True and self._state.get('service_ready') is True
    @pyqtProperty('QVariantMap',notify=changed)
    def restoreInfo(self):return dict(self._restore_info)

    @pyqtSlot(result=bool)
    def markReloaded(self):
        if self._restore_fault:return False
        self._restart_required=False
        self._message='配置已恢复并重新载入；C1 参数只入档，不会自动应用。'
        self.changed.emit();return True

    @pyqtSlot()
    def reloadConfiguration(self):
        if self._restart_required and not self._restore_fault:self.restored.emit()

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.MouseButtonPress,QEvent.TouchBegin,QEvent.KeyPress):
            self._last_input=time.monotonic()
            if self._asleep:
                self._asleep=False;self.changed.emit();return True
        return False

    def _idle(self):
        seconds=self._preferences['idle_seconds']
        busy=self._busy or bool(self.camera and (self.camera.busy or self.camera.settingsPending))
        if seconds and not busy and time.monotonic()-self._last_input>=seconds and not self._asleep:
            self._asleep=True
        self.changed.emit()

    @pyqtSlot()
    def wake(self):self._last_input=time.monotonic();self._asleep=False;self.changed.emit()

    def _submit(self,kind,function):
        if self._busy or self._closed:return False
        self._busy=True;self._operation=kind;self._error='';self._message='正在处理…';self.changed.emit()
        future=self._executor.submit(function)
        def done(task):
            try:value,error=task.result(),''
            except Exception as exc:value,error=None,str(exc)
            if not self._closed:self.finished.emit(kind,value,error)
        future.add_done_callback(done);return True

    def _end_guard(self):
        if self.camera and self._maintenance:self.camera.endMaintenance(self._maintenance)
        self._maintenance=''
        if self._admission_owned and self._tasks and hasattr(self._tasks,'setAdmissionBlocked'):self._tasks.setAdmissionBlocked(False)
        self._admission_owned=False

    def _guarded_submit(self,kind,function,reason,allow_unknown=False):
        if self._busy or self._closed:return False
        if self._tasks and hasattr(self._tasks,'setAdmissionBlocked'):
            self._tasks.setAdmissionBlocked(True,reason)
            self._admission_owned=True
            try:ready=self._tasks.quiescent()
            except Exception as error:
                self._end_guard();self._error='任务状态无法核实：'+str(error);self.changed.emit();return False
            if not ready:
                self._end_guard();self._error='请先暂停或完成后台任务，再执行系统维护。';self.changed.emit();return False
        if self.camera:
            self._maintenance=self.camera.beginMaintenance(reason,allow_unknown)
            if not self._maintenance:
                self._end_guard();self._error='请先等待拍摄、参数确认和临时 Fn 结束。';self.changed.emit();return False
        if not self._submit(kind,function):self._end_guard();return False
        return True

    @pyqtSlot(str,object,str)
    def _apply(self,kind,value,error):
        self._busy=False;self._error=error
        try:
            if error:
                if kind=='status':self._state={'system':platform.system(),'native':platform.system()=='FreeBSD','service_ready':False,'backlight_supported':False,'power_supported':False}
                if kind=='inspect-backup':self._backup=None
                if kind=='inspect-update':self._update={}
                if kind in ('restore','recover') and (self.root/'apps/restore-transactions').exists() and any((self.root/'apps/restore-transactions').iterdir()):
                    self._restore_fault=True;self._restart_required=True
                return
            if kind=='preferences':
                self._preferences=self._validate(value['preferences']);self._message='系统偏好已保存'
                if value.get('warning'):self._error=value['warning']
            elif kind=='status':
                self._state=value;self._message='系统状态已刷新'
                # The native persistent access flag is authoritative even if a
                # prior UI preference-file write was interrupted.
                if value.get('native') is True and type(value.get('developer_mode')) is bool:
                    self._preferences=dict(self._preferences,developer_mode=value['developer_mode'])
            elif kind=='inspect-backup':
                if value['revision']!=self._inspection_revision['backup']:self._message='路径已更改，请重新检查';return
                self._backup=value['payload'];self._message='备份已校验；确认恢复后才应用' if self._backup[0].get('restorable') else '备份有效，但媒体库未就绪或元数据冲突，未恢复任何配置'
            elif kind in ('restore','recover'):
                self._restore_fault=False;self._restart_required=True;self._restore_info=dict(value)
                if value.get('preferences'):self._preferences=self._validate(value['preferences'])
                self._message='配置文件已恢复，请重新载入或重开应用；拍摄参数保持当前。'
                self.restored.emit()
            elif kind=='inspect-update':
                if value['revision']!=self._inspection_revision['update']:self._message='更新路径或摘要已更改，请重新检查';return
                self._update=value['payload'];self._message='更新清单、文件与整体摘要检查通过'
            elif kind=='power':self._message='系统操作已安排';self.powerScheduled.emit()
            elif kind=='system':
                for key in ('backlight_percent','timezone','clock_utc'):
                    if key in value:self._state[key]=value[key]
                self._message=value.get('message','系统操作已读回确认')
            else:self._message=str(value.get('message') or value.get('path') or '操作完成') if isinstance(value,dict) else '操作完成'
        except Exception as failure:
            self._error='操作结果无法确认：'+str(failure)
            if kind in ('restore','recover'):self._restart_required=True
        finally:
            self._end_guard();self.changed.emit()

    @pyqtSlot(str,'QVariant')
    def setPreference(self,key,value):
        values=dict(self._preferences);values[key]=value
        def write():
            self._validate(values)
            native_developer=key=='developer_mode' and platform.system()=='FreeBSD'
            if native_developer:
                result=service_request('system.developer-mode',enabled=values[key])
                if result.get('developer_mode') is not values[key]:raise ValueError('开发模式未读回确认')
            try:_atomic(self.root/'system-preferences.json',json.dumps(values,ensure_ascii=False).encode())
            except OSError as error:
                if not native_developer:raise
                # Access has already changed and was read back. Reflect that
                # fact; never show an enabled terminal after native disable.
                return dict(preferences=values,warning='原生开发权限已生效，界面偏好文件未保存：'+str(error))
            return dict(preferences=values)
        self._submit('preferences',write)

    @pyqtSlot()
    def refresh(self):
        if platform.system()=='FreeBSD':
            def status():
                result=service_request('system.status')
                if not isinstance(result,dict) or result.get('native') is not True:raise ValueError('原生系统状态未确认')
                return dict(result,service_ready=True)
            self._submit('status',status)
        else:self._state={'system':platform.system(),'native':False,'service_ready':False,'backlight_supported':False,'power_supported':False};self.changed.emit()

    @pyqtSlot(str)
    def createBackup(self,destination):self._submit('backup',lambda:create_backup(self.root,destination,self._catalog))
    @pyqtSlot(result=bool)
    def createDefaultBackup(self):
        target=self.root/'apps/exports'/('DaOS-backup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.tar.gz')
        return self._submit('backup',lambda:create_backup(self.root,target,self._catalog))
    @pyqtSlot(str)
    def inspectBackup(self,path):
        if self._busy:return
        self.clearInspection('backup');revision=self._inspection_revision['backup']
        def inspect():
            manifest,documents=inspect_backup(path)
            preview={'restorable':True,'sourcePath':str(_safe_directory(path))}
            if 'portable-media.json' in documents:
                if self._catalog is None:preview.update(restorable=False,reason='媒体库尚未就绪，无法恢复元数据')
                else:
                    result=self._catalog.validatePortableMetadata(strict_json(documents['portable-media.json']))
                    preview.update(media=result,restorable=result.get('valid',not result.get('conflicts')))
            return dict(revision=revision,payload=(dict(manifest,**preview),documents))
        self._submit('inspect-backup',inspect)

    @pyqtSlot(str)
    def clearInspection(self,kind):
        if kind not in self._inspection_revision:return
        self._inspection_revision[kind]+=1
        if kind=='backup':self._backup=None
        else:self._update={}
        self.changed.emit()
    @pyqtSlot()
    def restoreBackup(self):
        if not self._backup or not self._backup[0].get('restorable',True):return
        manifest,documents=self._backup
        preferences=dict(self._preferences)
        def restore():
            result=restore_documents(self.root,documents,self._catalog,developer_mode=preferences['developer_mode'])
            path=self.root/'system-preferences.json'
            result['preferences']=self._validate(strict_json(path.read_bytes())) if path.exists() else preferences
            return result
        self._guarded_submit('restore',restore,'正在恢复配置')

    @pyqtSlot()
    def recoverPendingConfiguration(self):
        def recover():
            result=recover_restore_transactions(self.root,self._catalog)
            path=self.root/'system-preferences.json'
            return dict(recovered=result,preferences=self._validate(strict_json(path.read_bytes())) if path.exists() else dict(self._preferences))
        self._guarded_submit('recover',recover,'正在核对恢复记录')

    @pyqtSlot(str,str)
    def inspectUpdate(self,path,expected_hash):
        if self._busy:return
        self.clearInspection('update');revision=self._inspection_revision['update']
        self._submit('inspect-update',lambda:dict(revision=revision,payload=inspect_update(path,expected_hash)))

    @pyqtSlot(str)
    def systemAction(self,action):
        if action not in ('reboot','shutdown','sync-time','set-timezone','activate-update','rollback-update'):return
        if platform.system()!='FreeBSD':self._error='此操作需要运行在 DaOS BSD 设备上';self.changed.emit();return
        fields={}
        if action=='set-timezone':fields['timezone']=self._preferences['timezone']
        if action=='activate-update':
            if not self._update:self._error='请先检查更新文件';self.changed.emit();return
            fields.update(path=self._update['path'],sha256=self._update['sha256'])
        power=action in ('reboot','shutdown','activate-update','rollback-update')
        def execute():
            result=service_request('system.'+action,**fields)
            if not isinstance(result,dict) or power and result.get('scheduled') is not True:raise ValueError('系统操作未确认安排')
            if action=='set-timezone' and result.get('timezone')!=fields['timezone']:raise ValueError('系统时区未读回确认')
            if action=='sync-time':
                actual=datetime.datetime.fromisoformat(str(result.get('clock_utc','')).replace('Z','+00:00'))
                if actual.tzinfo is None:raise ValueError('网络同步后未读回系统时间')
            return result
        self._guarded_submit('power' if power else 'system',execute,'正在安排系统操作',action in ('reboot','shutdown'))

    @pyqtSlot(str)
    def setUtcTime(self,value):
        if platform.system()!='FreeBSD':self._error='手动系统时间只能在 DaOS BSD 设备设置';self.changed.emit();return
        try:
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):raise ValueError('请使用 YYYY-MM-DDTHH:MM:SSZ UTC 格式')
            requested=datetime.datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        except ValueError as error:self._error=str(error);self.changed.emit();return
        def execute():
            result=service_request('system.set-time',utc=value)
            actual=datetime.datetime.fromisoformat(str(result.get('clock_utc','')).replace('Z','+00:00'))
            if actual.tzinfo is None or abs((actual-requested).total_seconds())>5:raise ValueError('系统时间回读不一致')
            return result
        self._guarded_submit('system',execute,'正在设置系统时间')

    @pyqtSlot(int)
    def setBacklight(self,brightness):
        if not self._state.get('backlight_supported'):return
        if type(brightness) is not int or not 1<=brightness<=100:return
        def execute():
            result=service_request('system.set-backlight',value=brightness)
            if type(result.get('backlight_percent')) is not int or not 1<=result['backlight_percent']<=100:raise ValueError('背光未读回确认')
            return result
        self._submit('system',execute)

    def close(self):
        if self._closed:return
        self._closed=True;self._timer.stop();self._executor.shutdown(wait=True,cancel_futures=True);self._end_guard()

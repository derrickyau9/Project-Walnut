"""Versioned shooting profiles and local controls; capture UUIDs stay in camera.

C1-C3 explicitly apply supported parameters through the camera's acknowledged
settings API. Import is passive; it never applies parameters or submits a shot.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import time
import uuid
from parameter_state import validate_patch


ACTIONS = (
    ('exposure', '快门速度', '曝光时长由相机确认后生效。', True),
    ('gain', '模拟增益', '未经 ISO 标定的模拟增益，不等同于 ISO。', True),
    ('grid', '构图网格', '切换本地三分构图辅助线。', True),
    ('preview', '实时取景', '暂停或恢复当前取景画面。', True),
    ('clean_view', '纯净取景', '收起左侧任务栏，完整观察构图。', True),
    ('photos', '照片回放', '查看已经保存的照片。', True),
    ('my_menu', '我的菜单', '常用摄影工具的个人收藏。', True),
    ('wifi', 'Wi-Fi', '查看真实无线状态和连接控制。', True),
    ('terminal', '终端', '打开可使用软键盘的本地终端。', True),
    ('recipes', '拍摄配方', '创建、检查和导出可移植摄影配方。', True),
    ('diagnostics', '状态诊断', '检查相机状态及原始拍摄请求。', True),
    ('system', '系统设置', '系统运行状态和本地偏好。', True),
    ('theme', '显示主题', '切换黑白灰不透明深色或浅色界面。', True),
    ('workspace', '自定义控制', '配置 Q、我的菜单和 C1–C3 工作台。', True),
    ('tasks', '任务中心', '检查、暂停和取消当前任务。', True),
    ('image_tools', '影像工具', '对已选照片执行真实像素分析和派生处理。', True),
    ('system_control', '系统管理', '时间、开发模式、配置备份与受控更新。', True),
    ('white_balance', '白平衡', '当前后端尚未开放真实白平衡控制。', False),
    ('focus', '对焦 / AF', '尚未连接镜头对焦或自动对焦控制。', False),
    ('iso', '标定 ISO', '模拟增益尚未完成 ISO 标定。', False),
    ('aperture', '光圈', '尚未连接电子镜头光圈控制。', False),
    ('video', '视频录制', '当前后端尚未开放视频编码与录制管线。', False),
    ('histogram', '直方图', '成像管线尚未提供经过验证的场景直方图。', False),
    ('peaking', '峰值对焦', '尚未连接经过验证的峰值对焦图像管线。', False),
)
ACTION_IDS = {item[0] for item in ACTIONS}
INPUT_ACTION_IDS = {'grid', 'clean_view', 'exposure', 'gain', 'my_menu'}
HOLD_IDS = {'hold_c1','hold_c2','hold_c3'}
PHOTO_SLOTS = ['exposure', 'gain', 'grid', 'preview', 'photos', 'my_menu', 'wifi', 'terminal', 'recipes', 'diagnostics', 'theme', 'workspace']
VIDEO_SLOTS = ['video', 'exposure', 'gain', 'white_balance', 'focus', 'grid', 'preview', 'photos', 'wifi', 'terminal', 'diagnostics', 'workspace']


def default_layout():
    return {'mode': 'photo', 'slots': {'photo': list(PHOTO_SLOTS), 'video': list(VIDEO_SLOTS)},
            'my_menu': ['exposure', 'gain', 'wifi', 'terminal'], 'taskbar_auto_hide': True,
            'inputs': {'fn1': 'grid', 'fn2': 'my_menu', 'dial': 'exposure'},
            'reduced_motion': False, 'display': {'grid_enabled': False, 'theme': 'dark'}}


def validate_layout(value):
    # One-time migration of the initial local workspace draft. Shared imports
    # are checked for the full schema separately and never gain extra fields.
    legacy_fields = {'mode', 'slots', 'my_menu', 'taskbar_auto_hide', 'inputs'}
    if isinstance(value, dict) and set(value) == legacy_fields:
        value = dict(value, reduced_motion=False, display={'grid_enabled':False,'theme':'dark'})
    if not isinstance(value, dict) or set(value) != {'mode', 'slots', 'my_menu', 'taskbar_auto_hide', 'inputs', 'reduced_motion', 'display'}:
        raise ValueError('Invalid workspace fields.')
    if value['mode'] not in ('photo', 'video') or type(value['taskbar_auto_hide']) is not bool:
        raise ValueError('Invalid workspace mode or taskbar preference.')
    slots = value['slots']
    if not isinstance(slots, dict) or set(slots) != {'photo', 'video'}:
        raise ValueError('Both photo and video Q layouts are required.')
    for items in slots.values():
        if not isinstance(items, list) or len(items) != 12 or any(type(i) is not str or i not in ACTION_IDS for i in items):
            raise ValueError('Each Q layout requires twelve known actions.')
    pins = value['my_menu']
    if not isinstance(pins, list) or len(pins) > 20 or len(pins) != len(set(pins)) or any(type(i) is not str or i not in ACTION_IDS for i in pins):
        raise ValueError('Invalid My Menu actions.')
    inputs = value['inputs']
    if not isinstance(inputs, dict) or set(inputs) != {'fn1', 'fn2', 'dial'}:
        raise ValueError('Invalid input bindings.')
    if any(type(inputs[i]) is not str or inputs[i] not in INPUT_ACTION_IDS | HOLD_IDS for i in ('fn1', 'fn2')) or inputs['dial'] not in ('exposure', 'gain'):
        raise ValueError('Unsupported input binding.')
    if type(value['reduced_motion']) is not bool or not isinstance(value['display'], dict) or set(value['display']) != {'grid_enabled', 'theme'} or type(value['display']['grid_enabled']) is not bool or value['display']['theme'] not in ('dark', 'light'):
        raise ValueError('Invalid workspace display preferences.')
    return copy.deepcopy(value)


def validate_profile(value):
    if not isinstance(value,dict) or set(value) != {'ui','camera','compatibility'}: raise ValueError('工作台槽格式无效。')
    if not isinstance(value['ui'],dict) or set(value['ui']) != set(default_layout()):raise ValueError('v2 工作台界面字段不完整。')
    ui=validate_layout(value['ui'])
    camera=None if value['camera'] is None else validate_patch(value['camera'],full=True)
    compatibility=value['compatibility']
    if (not isinstance(compatibility,dict) or set(compatibility) != {'device','settings_schema'}
            or not isinstance(compatibility['device'],str) or len(compatibility['device'])>120
            or compatibility['settings_schema'] != 'hf-device-settings-v1'): raise ValueError('相机兼容性字段无效。')
    return dict(ui=ui,camera=camera,compatibility=dict(compatibility))


def migrate_profile(value):
    return dict(ui=validate_layout(value),camera=None,compatibility={'device':'','settings_schema':'hf-device-settings-v1'})


def validate_state_document(value):
    """Strict non-I/O backup validator; returns a canonical v2 document."""
    if (not isinstance(value,dict) or set(value) != {'version','layout','profiles','active_profile'}
            or type(value['version']) is not int or value['version'] not in (1,2)):
        raise ValueError('未知工作台状态格式。')
    layout=validate_layout(value['layout'])
    profiles=value['profiles']
    if not isinstance(profiles,dict) or any(key not in ('C1','C2','C3') for key in profiles):raise ValueError('工作台只支持 C1–C3。')
    profiles={key:(migrate_profile(item) if value['version']==1 else validate_profile(item)) for key,item in profiles.items()}
    active=value['active_profile']
    if type(active) is not str or (active and active not in profiles):raise ValueError('活动工作台不存在。')
    return dict(version=2,layout=layout,profiles=profiles,active_profile=active)


def validate_shared(value):
    if not isinstance(value, dict) or set(value) != {'schema', 'version', 'layout', 'profiles'} or value['schema'] != 'daos-shooting-workspace' or type(value['version']) is not int or value['version'] not in (1,2):
        raise ValueError('仅接受版本化工作台配置；禁止控制租约、拍摄请求或系统命令。')
    expected_fields = set(default_layout())
    if not isinstance(value['layout'],dict) or set(value['layout']) != expected_fields:
        raise ValueError('分享配置缺少完整的界面字段。')
    layout = validate_layout(value['layout'])
    if not isinstance(value['profiles'], dict) or any(key not in ('C1', 'C2', 'C3') for key in value['profiles']):
        raise ValueError('导入文件只能包含 C1–C3 工作台。')
    if value['version'] == 1 and any(not isinstance(item,dict) or set(item) != expected_fields for item in value['profiles'].values()):
        raise ValueError('分享配置中的 C1–C3 字段不完整。')
    profiles = {key: (migrate_profile(item) if value['version'] == 1 else validate_profile(item)) for key, item in value['profiles'].items()}
    return layout, profiles


def checked_file_path(value, *, create_parent=False):
    if type(value) is not str or len(value) > 4096 or not Path(value).is_absolute() or Path(value).suffix.lower() != '.json':
        raise ValueError('请选择 .json 文件的完整绝对路径。')
    path = Path(value)
    if create_parent: path.parent.mkdir(parents=True, exist_ok=True)
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or not stat.S_ISDIR(info.st_mode):
            raise ValueError('配置路径不能包含符号链接或重解析点。')
    return path


class WorkspaceState:
    """Strict schema, bounded reads and atomic local persistence."""
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'shooting-workspace.json'
        self.layout = default_layout()
        self.profiles = {}
        self.active_profile = ''
        self.error = ''
        self._blocked = False
        self._parent_identity = None
        try:
            self._check_directory()
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if not stat.S_ISREG(info.st_mode) or self.path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or info.st_size > 65536:
                    raise ValueError('Workspace file must be a regular file of at most 64 KiB.')
                with self.path.open('rb') as handle:
                    raw = handle.read(65537)
                if len(raw) > 65536:
                    raise ValueError('Workspace file exceeds 64 KiB.')
                data = validate_state_document(json.loads(raw.decode('utf-8')))
                if not isinstance(data, dict) or set(data) != {'version', 'layout', 'profiles', 'active_profile'} or type(data['version']) is not int or data['version'] not in (1,2):
                    raise ValueError('Unknown workspace schema.')
                self.layout = validate_layout(data['layout'])
                if not isinstance(data['profiles'], dict) or any(i not in ('C1', 'C2', 'C3') for i in data['profiles']):
                    raise ValueError('Only C1-C3 profile slots are supported.')
                self.profiles = {key: (migrate_profile(item) if data['version'] == 1 else validate_profile(item)) for key, item in data['profiles'].items()}
                if data['active_profile'] not in ('', 'C1', 'C2', 'C3'):
                    raise ValueError('Invalid active workspace.')
                self.active_profile = data['active_profile']
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            self.layout = default_layout()
            self.profiles = {}
            self.active_profile = ''
            self.error = 'Workspace preferences could not be loaded: ' + str(error)
            self._blocked = True  # Preserve an unreadable file for recovery.

    def _check_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for parent in (self.path.parent, *self.path.parent.parents):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or not stat.S_ISDIR(info.st_mode):
                raise ValueError('Workspace directories must not be links or reparse points.')
        info = self.path.parent.stat()
        identity = (info.st_dev, info.st_ino)
        if self._parent_identity is not None and identity != self._parent_identity:
            raise ValueError('Workspace directory changed; restart before editing.')
        self._parent_identity = identity

    def save(self, layout=None, profiles=None, active=None):
        if self._blocked:
            return False
        pending = self.path.with_name('.shooting-workspace-' + uuid.uuid4().hex + '.tmp')
        try:
            self._check_directory()
            next_layout = validate_layout(self.layout if layout is None else layout)
            next_profiles = {key:validate_profile(item) for key,item in (self.profiles if profiles is None else profiles).items()}
            next_active = self.active_profile if active is None else active
            if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
                raise ValueError('Workspace destination is no longer a regular file.')
            document = {'version': 2, 'layout': next_layout, 'profiles': next_profiles, 'active_profile': next_active}
            with pending.open('x', encoding='utf-8') as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(',', ':'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, self.path)
            self.layout, self.profiles, self.active_profile = next_layout, next_profiles, next_active
            self.error = ''
            return True
        except (OSError, ValueError, TypeError) as error:
            self.error = 'Workspace changes were not saved: ' + str(error)
            return False
        finally:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass


try:
    from PyQt5.QtCore import QObject, QCoreApplication, QEvent, Qt, pyqtProperty, pyqtSignal, pyqtSlot
except ImportError:
    class ShootingWorkspace:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('ShootingWorkspace requires PyQt5; WorkspaceState does not.')
else:
    class ShootingWorkspace(QObject):
        changed = pyqtSignal()
        actionRequested = pyqtSignal(str)
        adjustRequested = pyqtSignal(str, int)
        profileApplied = pyqtSignal('QVariantMap')
        momentaryRequested = pyqtSignal(str, bool)
        parameterChanged = pyqtSignal()

        def __init__(self, data_dir, parent=None):
            super().__init__(parent)
            self._state = WorkspaceState(data_dir)
            self._capabilities = {}
            self._settings = {}
            self._message = self._state.error
            self._input_enabled = False
            self._held = {}
            self._camera = None
            self._pending_profile = None
            self._quick_active = False
            self._selected_parameter = 'exposure'
            self._developer_mode=True
            application = QCoreApplication.instance()
            if application is not None: application.installEventFilter(self)

        def attachCamera(self,camera):
            if self._camera is camera: return
            self._camera=camera
            camera.changed.connect(self._camera_updated)
            camera.settingsSettled.connect(self._profile_settled)
            camera.settingsFailed.connect(self._profile_failed)
            self.parameterChanged.emit()

        @pyqtSlot(result=bool)
        def reloadFromDisk(self):
            if self._held or self._pending_profile:
                self._message='请先结束临时控制或工作台载入，再重新载入配置。';self.changed.emit();return False
            state=WorkspaceState(self._state.path.parent)
            if state.error:self._message=state.error;self.changed.emit();return False
            self._state=state
            self._message='工作台配置已重新载入；拍摄参数保持当前。'
            self.changed.emit();self.parameterChanged.emit();return True

        def _camera_updated(self):
            self.parameterChanged.emit()
            if self._pending_profile and self._camera.connectionState == 'disconnected':
                self._pending_profile=None
                self._message='工作台参数未确认，重新连接后请核对并再次载入。';self.changed.emit()

        def _commit_profile_ui(self,name,profile):
            ok=self._state.save(layout=profile['ui'],active=name)
            self._message=name+' 已载入，真实参数已确认。' if ok else '拍摄参数已确认，但界面配置未保存：'+self._state.error
            if ok: self.profileApplied.emit(dict(profile['ui']['display']))
            self.changed.emit()
            return ok

        def _profile_settled(self,revision,confirmed):
            if not self._pending_profile or revision != self._pending_profile[1]: return
            name,_,profile=self._pending_profile
            self._pending_profile=None
            if self._camera.settingsView['desiredRevision'] != revision:
                self._message='手动调整已保留，工作台载入转为自定义；输入映射保持当前。'
                self.changed.emit();return
            if dict(confirmed) == profile['camera']:
                self._commit_profile_ui(name,profile)

        def _profile_failed(self,revision,reason):
            if self._pending_profile and revision <= self._pending_profile[1]:
                self._pending_profile=None
                self._message='工作台参数未确认：'+str(reason)
                self.changed.emit()

        def eventFilter(self, watched, event):
            if event.type() in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
                for key in list(self._held): self.endInput(key)
            if self._quick_active and event.type() in (QEvent.KeyPress,QEvent.KeyRelease) and event.key() in (Qt.Key_Up,Qt.Key_Down):
                if event.type() == QEvent.KeyPress:self.dispatchInput('dial',1 if event.key()==Qt.Key_Up else -1)
                return True
            if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() in (Qt.Key_F1, Qt.Key_F2):
                key = 'fn1' if event.key() == Qt.Key_F1 else 'fn2'
                if event.type() == QEvent.KeyRelease and key in self._held:
                    if not event.isAutoRepeat(): self.endInput(key)
                    return True
                if event.type() == QEvent.KeyPress and self._input_enabled:
                    if not event.isAutoRepeat(): self.beginInput(key)
                    return True
            return super().eventFilter(watched, event)

        def _info(self, action_id):
            item = next((i for i in ACTIONS if i[0] == action_id), None)
            if item is None:
                return {'id': action_id, 'title': 'Unavailable', 'available': False, 'reason': 'Unknown action.'}
            enabled, reason = item[3], item[2]
            if action_id == 'terminal' and not self._developer_mode:
                enabled,reason=False,'开发模式已关闭，请在系统管理开启后使用终端。'
            # Only implemented routes can be enabled. A capability flag cannot
            # manufacture a recording, AF or histogram implementation.
            override = self._capabilities.get(action_id)
            if isinstance(override, dict) and override.get('available') is False:
                enabled, reason = False, str(override.get('reason') or reason)[:240]
            return {'id': item[0], 'title': item[1], 'available': enabled, 'reason': reason,
                    'pinned': action_id in self._state.layout['my_menu']}

        def _edit(self, edit):
            value = copy.deepcopy(self._state.layout)
            edit(value)
            if value == self._state.layout:return True
            ok = self._state.save(layout=value)
            self._message = '' if ok else self._state.error
            self.changed.emit()
            return ok

        @pyqtProperty(str, notify=changed)
        def mode(self): return self._state.layout['mode']
        @pyqtProperty(str, notify=changed)
        def activeProfile(self): return self._state.active_profile
        @pyqtProperty(str, notify=changed)
        def message(self): return self._message
        @pyqtProperty(bool, notify=changed)
        def taskbarAutoHide(self): return self._state.layout['taskbar_auto_hide']
        @pyqtProperty(bool, notify=changed)
        def reducedMotion(self): return self._state.layout['reduced_motion']
        @pyqtProperty('QVariantList', constant=True)
        def presets(self):
            return [{'id': key, 'title': title} for key, title in (('daily','日常'),('street','街拍'),('portrait','人像'),('landscape','风光'),('astro','天文'),('video','视频'))]
        @pyqtProperty('QVariantList', notify=changed)
        def quickSlots(self): return [dict(self._info(i), slot=n) for n, i in enumerate(self._state.layout['slots'][self.mode])]
        @pyqtProperty('QVariantList', notify=changed)
        def catalog(self): return [self._info(i[0]) for i in ACTIONS]
        @pyqtProperty('QVariantList', notify=changed)
        def inputActions(self):
            return [self._info(i[0]) for i in ACTIONS if i[0] in INPUT_ACTION_IDS]+[
                {'id':'hold_'+key.lower(),'title':'按住临时 '+key,'available':bool(self._state.profiles.get(key,{}).get('camera')),'reason':'先保存该工作台的真实参数。'} for key in ('C1','C2','C3')]
        @pyqtProperty('QVariantList', notify=changed)
        def myMenu(self): return [self._info(i) for i in self._state.layout['my_menu']]
        @pyqtProperty('QVariantList', notify=changed)
        def profiles(self): return [{'id':key,'saved':key in self._state.profiles,'active':key == self.activeProfile,
                                    'parametersSaved':bool(self._state.profiles.get(key,{}).get('camera'))} for key in ('C1','C2','C3')]
        @pyqtProperty('QVariantMap',notify=parameterChanged)
        def parameterView(self): return self._camera.settingsView if self._camera else {'desired':{},'confirmed':{},'phase':'disconnected','canEdit':False}
        @pyqtProperty(str,notify=parameterChanged)
        def selectedParameter(self): return self._selected_parameter
        @pyqtProperty(bool,notify=parameterChanged)
        def profileDirty(self):
            saved=self._state.profiles.get(self.activeProfile,{})
            if not saved:return False
            layout=copy.deepcopy(self._state.layout)
            if type(self._settings.get('grid_enabled')) is bool:layout['display']['grid_enabled']=self._settings['grid_enabled']
            if self._settings.get('theme') in ('dark','light'):layout['display']['theme']=self._settings['theme']
            return saved['ui'] != layout or bool(saved.get('camera') and saved['camera'] != self.parameterView.get('desired'))
        @pyqtProperty('QVariantMap', notify=changed)
        def inputBindings(self): return dict(self._state.layout['inputs'])

        @pyqtSlot('QVariantMap')
        def setCapabilities(self, value):
            value=dict(value)
            if value != self._capabilities:
                self._capabilities=value;self.changed.emit()
        @pyqtSlot(bool)
        def setDeveloperMode(self,enabled):
            if self._developer_mode != bool(enabled):self._developer_mode=bool(enabled);self.changed.emit()
        @pyqtSlot('QVariantMap')
        def setCurrentSettings(self, value):
            before=(self._settings.get('grid_enabled'),self._settings.get('theme'))
            self._settings.update(dict(value))
            if before != (self._settings.get('grid_enabled'),self._settings.get('theme')):self.parameterChanged.emit()
        @pyqtSlot(str, result='QVariantMap')
        def actionInfo(self, action_id): return self._info(action_id)
        @pyqtSlot(str, result=bool)
        def setMode(self, mode):
            return self._edit(lambda value: value.update(mode=mode)) if mode in ('photo', 'video') else False
        @pyqtSlot(int, str, result=bool)
        def setSlot(self, index, action_id):
            if index not in range(12) or action_id not in ACTION_IDS: return False
            return self._edit(lambda value: value['slots'][value['mode']].__setitem__(index, action_id))
        @pyqtSlot(str, result=bool)
        def toggleMyMenu(self, action_id):
            if action_id not in ACTION_IDS: return False
            def edit(value):
                pins = value['my_menu']
                pins.remove(action_id) if action_id in pins else pins.append(action_id)
            return self._edit(edit)
        @pyqtSlot(bool, result=bool)
        def setTaskbarAutoHide(self, enabled): return self._edit(lambda value: value.update(taskbar_auto_hide=bool(enabled)))
        @pyqtSlot(bool, result=bool)
        def setReducedMotion(self, enabled): return self._edit(lambda value: value.update(reduced_motion=bool(enabled)))
        @pyqtSlot(str, str, result=bool)
        def setInput(self, key, action_id):
            if key not in ('fn1', 'fn2', 'dial') or action_id not in (('exposure', 'gain') if key == 'dial' else INPUT_ACTION_IDS | HOLD_IDS): return False
            return self._edit(lambda value: value['inputs'].__setitem__(key, action_id))
        @pyqtSlot(str, result=bool)
        def saveProfile(self, name):
            if name not in ('C1', 'C2', 'C3'): return False
            view=self.parameterView
            if (not self._camera or view.get('phase') != 'stable' or view.get('isTemporary') or self._camera.busy
                    or not view.get('confirmed')):
                self._message='请等待相机就绪、参数确认及临时 Fn 释放后，再保存真实拍摄参数。';self.changed.emit();return False
            profiles = copy.deepcopy(self._state.profiles)
            layout = copy.deepcopy(self._state.layout)
            if type(self._settings.get('grid_enabled')) is bool: layout['display']['grid_enabled'] = self._settings['grid_enabled']
            if self._settings.get('theme') in ('dark', 'light'): layout['display']['theme'] = self._settings['theme']
            profiles[name] = dict(ui=layout,camera=validate_patch(dict(view['confirmed']),full=True),
                                  compatibility={'device':str(self._capabilities.get('device',''))[:120],'settings_schema':'hf-device-settings-v1'})
            ok = self._state.save(profiles=profiles, active=name)
            self._message = name + ' 已保存：真实快门、增益、图像模式和界面配置。' if ok else self._state.error
            self.changed.emit()
            return ok
        @pyqtSlot(str, result=bool)
        def loadProfile(self, name):
            if name not in self._state.profiles:
                self._message = name + ' 尚未保存。'
                self.changed.emit()
                return False
            profile=copy.deepcopy(self._state.profiles[name])
            if profile['camera'] is None:
                ok=self._state.save(layout=profile['ui'],active=name)
                self._message='旧 '+name+' 仅含界面；请重新保存以记忆真实参数。' if ok else self._state.error
                if ok:self.profileApplied.emit(dict(profile['ui']['display']))
                self.changed.emit();return ok
            if not self._camera or not self.parameterView.get('canEdit') or self.parameterView.get('isTemporary'):
                self._message='请先连接相机、核实拍摄结果并松开临时 Fn。';self.changed.emit();return False
            if profile['compatibility']['device'] and profile['compatibility']['device'] != self._capabilities.get('device'):
                self._message='该工作台属于不同相机，拍摄参数未应用。';self.changed.emit();return False
            revision=self._camera.setSettings(profile['camera'],'profile:'+name)
            if revision < 0:return False
            if not self._camera.settingsPending and self._camera.confirmedSettings == profile['camera']:
                return self._commit_profile_ui(name,profile)
            self._pending_profile=(name,revision,profile)
            self._message='正在载入 '+name+'；等待整组拍摄参数确认。';self.changed.emit();return True
        @pyqtSlot(str, result=bool)
        def applyPreset(self, name):
            if name not in {i['id'] for i in self.presets}: return False
            layout = default_layout()
            for key in ('my_menu','inputs','taskbar_auto_hide','reduced_motion'):
                layout[key] = copy.deepcopy(self._state.layout[key])
            if name == 'street':
                layout['slots']['photo'] = ['exposure','gain','clean_view','grid','preview','photos','my_menu','wifi','recipes','diagnostics','theme','workspace']
            elif name == 'portrait':
                layout['slots']['photo'] = ['exposure','gain','focus','white_balance','aperture','grid','photos','my_menu','preview','theme','diagnostics','workspace']
            elif name == 'landscape':
                layout['display']['grid_enabled'] = True
                layout['slots']['photo'] = ['exposure','gain','grid','histogram','peaking','preview','recipes','photos','my_menu','wifi','diagnostics','workspace']
            elif name == 'astro':
                layout['slots']['photo'] = ['exposure','gain','preview','clean_view','recipes','focus','grid','photos','terminal','diagnostics','theme','workspace']
            elif name == 'video': layout['mode'] = 'video'
            if self._settings.get('theme') in ('light','dark'): layout['display']['theme'] = self._settings['theme']
            ok = self._state.save(layout=layout, active='')
            self._message = '已应用界面模板。对焦、白平衡和视频能力仍以当前后端为准；曝光保持不变。' if ok else self._state.error
            if ok: self.profileApplied.emit(dict(layout['display']))
            self.changed.emit()
            return ok
        @pyqtSlot(str, result=bool)
        def exportWorkspace(self, destination):
            try:
                path = checked_file_path(destination, create_parent=True)
                document = {'schema':'daos-shooting-workspace', 'version':2,
                            'layout':copy.deepcopy(self._state.layout), 'profiles':copy.deepcopy(self._state.profiles)}
                validate_shared(document)
                raw = json.dumps(document, ensure_ascii=False, separators=(',',':')).encode('utf-8')
                if len(raw) > 65536: raise ValueError('配置超过 64 KiB 限制。')
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0)
                with os.fdopen(os.open(path,flags,0o600),'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._message = '配置已导出：' + str(path)
                ok = True
            except (OSError,ValueError,TypeError) as error:
                self._message = '配置未导出（已有文件不会覆盖）：' + str(error)
                ok = False
            self.changed.emit()
            return ok
        @pyqtSlot(result=bool)
        def exportDefault(self):
            name = 'DaOS-workspace-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json'
            return self.exportWorkspace(str((self._state.path.parent/'apps'/'exports'/name).absolute()))
        @pyqtSlot(str, result=bool)
        def importWorkspace(self, source):
            try:
                path = checked_file_path(source)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400 or info.st_size > 65536:
                    raise ValueError('配置必须为不超过 64 KiB 的普通文件。')
                flags = os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0)
                with os.fdopen(os.open(path,flags),'rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev,opened.st_ino) != (info.st_dev,info.st_ino): raise ValueError('读取配置时文件发生变化。')
                    raw = handle.read(65537)
                if len(raw) > 65536: raise ValueError('配置超过 64 KiB 限制。')
                layout, profiles = validate_shared(json.loads(raw.decode('utf-8')))
                ok = self._state.save(layout=layout,profiles=profiles,active='')
                if not ok: raise ValueError(self._state.error)
                unavailable = sorted({self._info(action)['title'] for values in layout['slots'].values() for action in values if not self._info(action)['available']})
                self._message = '已导入工作台，曝光保持不变。' + ('当前不可用：' + '、'.join(unavailable) if unavailable else '')
                self.profileApplied.emit(dict(layout['display']))
            except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError) as error:
                self._message = '配置未导入：' + str(error)
                ok = False
            self.changed.emit()
            return ok
        @pyqtSlot(str, result=bool)
        def triggerAction(self, action_id):
            info = self._info(action_id)
            if not info['available']:
                self._message = info['title'] + ': ' + info['reason']
                self.changed.emit()
                return False
            self._message = ''
            self.changed.emit()
            self.actionRequested.emit(action_id)
            return True
        @pyqtSlot(str, int)
        def dispatchInput(self, key, delta):
            action = self._selected_parameter if key == 'dial' and self._quick_active else self._state.layout['inputs'].get(key)
            if key == 'dial' and delta and action:
                if self._camera:self._camera.nudgeSettings(action,delta,'dial')
                else:self.adjustRequested.emit(action,delta)
            elif action:
                self.triggerAction(action)

        @pyqtSlot(bool)
        def setInputEnabled(self, enabled): self._input_enabled = bool(enabled)
        @pyqtSlot(bool)
        def setQuickActive(self,active):self._quick_active=bool(active)
        @pyqtSlot(str)
        def selectQuickParameter(self,action):
            if action in ('exposure','gain'):
                self._selected_parameter=action;self.parameterChanged.emit()
        @pyqtSlot(str,int)
        def nudgeParameter(self,action,ticks):
            if action in ('exposure','gain') and self._camera:
                self._selected_parameter=action
                self._camera.nudgeSettings(action,ticks,'quick')
        @pyqtSlot(str)
        def beginInput(self, key):
            if key not in ('fn1', 'fn2') or key in self._held or self._held: return
            action = self._state.layout['inputs'][key]
            if action in HOLD_IDS:
                profile=self._state.profiles.get(action[5:].upper(),{})
                if not self._camera or not profile.get('camera'):
                    self._message='请先保存该工作台的真实拍摄参数。';self.changed.emit();return
                if profile['compatibility']['device'] and profile['compatibility']['device'] != self._capabilities.get('device'):
                    self._message='该临时工作台属于不同相机，参数未应用。';self.changed.emit();return
                token=self._camera.beginTemporary(profile['camera'],key)
                if token:self._held[key]={'action':action,'token':token}
                return
            if not self._info(action)['available']: return
            self._held[key] = action
            self.momentaryRequested.emit(action, True)
        @pyqtSlot(str)
        def endInput(self, key):
            action = self._held.pop(key, None)
            if isinstance(action,dict):
                if self._camera:self._camera.endTemporary(action['token'])
            elif action: self.momentaryRequested.emit(action, False)

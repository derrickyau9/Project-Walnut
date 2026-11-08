"""Local DaOS camera navigation and display preferences.

This shell never submits camera commands. Exposure, gain, capture identity,
device ownership and saved media remain the camera authority's responsibility.
Only three UI preferences are stored here. POSIX operations use a pinned parent
directory descriptor and O_NOFOLLOW; Windows additionally rejects reparse points
and checks file/directory identities, but does not claim hostile concurrent
directory-replacement resistance equivalent to POSIX dir_fd operations.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import stat
import uuid


MAX_PREFERENCES_BYTES = 8192
DEFAULT_PREFERENCES = {
    'grid_enabled': False,
    'theme': 'light',
    'preview_brightness': 100,
}
_APP_DEFINITIONS = (
    ('camera', 'Camera', '相机', 'CAM', 'photography'),
    ('photos', 'Photos', '相册', 'RAW', 'photography'),
    ('recipes', 'Recipes', '拍摄配方', 'SEQ', 'photography'),
    ('settings', 'Settings', '相机设置', 'SET', 'system'),
    ('devices', 'Devices', '设备', 'DEV', 'system'),
    ('diagnostics', 'Diagnostics', '诊断', 'LOG', 'system'),
    ('system', 'System', '系统', 'SYS', 'system'),
)
_APP_IDS = frozenset(item[0] for item in _APP_DEFINITIONS)
_GROUP_DEFINITIONS = (
    ('exposure', 'Exposure', '曝光', 'Exposure and gain are applied by the camera authority.'),
    ('image', 'Image and RAW', '图像与 RAW', 'Inspect the active sensor mode and Bayer/DNG capabilities.'),
    ('display', 'Display', '显示', 'Local framing grid, theme and simulated preview brightness.'),
    ('storage', 'Storage', '存储', 'Committed photographs and their retained DNG files.'),
    ('system', 'System', '系统', 'Runtime identity, local preferences and device health.'),
)
_GROUP_IDS = frozenset(item[0] for item in _GROUP_DEFINITIONS)


class PreferenceError(ValueError):
    """A local preference file could not be read or committed safely."""


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _identity(info):
    # Windows lstat and handle fstat can expose different ctime interpretations
    # after an atomic replacement. The stable file ID, size and mtime still bind
    # the same file; POSIX additionally supplies its reliable inode-change time.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            getattr(info, 'st_ctime_ns', 0) if os.name != 'nt' else 0)


def _valid_preferences(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_PREFERENCES):
        raise PreferenceError('Invalid local display preference fields.')
    if type(value['grid_enabled']) is not bool:
        raise PreferenceError('Framing grid must be true or false.')
    if type(value['theme']) is not str or value['theme'] not in ('light', 'dark'):
        raise PreferenceError('Theme must be light or dark.')
    brightness = value['preview_brightness']
    if type(brightness) is not int or not 25 <= brightness <= 100:
        raise PreferenceError('Preview brightness must be an integer from 25 to 100.')
    return dict(value)


def _decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreferenceError('Duplicate local preference field.')
            result[key] = value
        return result

    def constant(_):
        raise PreferenceError('Non-finite local preference value.')

    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=unique,
                              parse_constant=constant)
        if (not isinstance(document, dict) or set(document) != {'schema_version', 'preferences'}
                or type(document['schema_version']) is not int or document['schema_version'] != 1):
            raise PreferenceError('Invalid local preference schema.')
        return _valid_preferences(document['preferences'])
    except (UnicodeError, json.JSONDecodeError, TypeError, KeyError, RecursionError) as error:
        raise PreferenceError('Invalid local preference document.') from error


class _PreferenceStore:
    def __init__(self, path):
        self.path = Path(os.path.abspath(os.fspath(path)))
        if not self.path.name or self.path.name in ('.', '..'):
            raise PreferenceError('A dedicated local preference filename is required.')
        self.parent = self.path.parent
        self.parent_identity = None
        self.expected = None
        self.error = ''
        self.unsafe = False
        self.corrupt = None
        self.last_backup_name = ''
        self._dir_fd = (os.name == 'posix' and hasattr(os, 'O_NOFOLLOW')
                        and hasattr(os, 'O_DIRECTORY') and os.open in os.supports_dir_fd)

    def _check_parent(self, *, create=False):
        for parent in (*reversed(self.parent.parents), self.parent):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                if not create:
                    raise PreferenceError('Local preference directory disappeared.')
                try:
                    parent.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = parent.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                raise PreferenceError('Local preference directories must not be links or reparse points.')
        current = self.parent.lstat()
        pair = (current.st_dev, current.st_ino)
        if self.parent_identity is None:
            self.parent_identity = pair
        elif pair != self.parent_identity:
            raise PreferenceError('Local preference directory changed; reload before editing.')

    @contextmanager
    def _directory(self, *, create=False):
        self._check_parent(create=create)
        descriptor = None
        try:
            if self._dir_fd:
                descriptor = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                     | getattr(os, 'O_CLOEXEC', 0))
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != self.parent_identity:
                    raise PreferenceError('Local preference directory changed while opening it.')
            yield descriptor
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _stat(self, descriptor, name):
        try:
            info = (os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if descriptor is not None else (self.parent/name).lstat())
        except FileNotFoundError:
            return None
        if _linked(info) or not stat.S_ISREG(info.st_mode):
            raise PreferenceError('The local preference file must be a regular file, not a link.')
        return info

    def _open(self, descriptor, name, flags, mode=0o600):
        flags |= getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0)
        if descriptor is not None:
            return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=descriptor)
        return os.open(self.parent/name, flags, mode)

    def load(self):
        try:
            with self._directory(create=True) as directory:
                before = self._stat(directory, self.path.name)
                if before is None:
                    return dict(DEFAULT_PREFERENCES)
                self.expected = _identity(before)
                stream = self._open(directory, self.path.name, os.O_RDONLY)
                try:
                    opened = os.fstat(stream)
                    if not stat.S_ISREG(opened.st_mode) or _identity(opened) != self.expected:
                        raise PreferenceError('Local preference file changed while opening it.')
                    if opened.st_size > MAX_PREFERENCES_BYTES:
                        raise PreferenceError('Local preference file is too large; it was left untouched.')
                    data = bytearray()
                    while len(data) <= MAX_PREFERENCES_BYTES:
                        part = os.read(stream, MAX_PREFERENCES_BYTES + 1 - len(data))
                        if not part:
                            break
                        data.extend(part)
                    if len(data) > MAX_PREFERENCES_BYTES or _identity(os.fstat(stream)) != self.expected:
                        raise PreferenceError('Local preference file changed or exceeded its size bound.')
                finally:
                    os.close(stream)
            try:
                return _decode(bytes(data))
            except PreferenceError as error:
                self.corrupt = bytes(data)
                self.error = str(error) + ' Defaults are in use; the original file is preserved. Reset explicitly to replace it.'
                return dict(DEFAULT_PREFERENCES)
        except (OSError, PreferenceError) as error:
            self.unsafe = True
            self.error = str(error)
            return dict(DEFAULT_PREFERENCES)

    def _check_current(self, directory):
        info = self._stat(directory, self.path.name)
        current = _identity(info) if info is not None else None
        if current != self.expected:
            raise PreferenceError('Local preferences changed outside this session; reload before editing.')

    def _write_new(self, directory, name, data):
        stream = self._open(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(stream, remaining)
                if written <= 0:
                    raise OSError('Could not finish writing local preferences.')
                remaining = remaining[written:]
            os.fsync(stream)
        finally:
            os.close(stream)

    def _unlink(self, directory, name):
        try:
            if directory is not None:
                os.unlink(name, dir_fd=directory)
            else:
                (self.parent/name).unlink()
        except FileNotFoundError:
            pass

    def save(self, preferences, *, reset=False):
        if self.unsafe:
            raise PreferenceError(self.error or 'Local preference storage is unavailable.')
        if self.corrupt is not None and not reset:
            raise PreferenceError(self.error)
        preferences = _valid_preferences(preferences)
        data = (json.dumps({'schema_version': 1, 'preferences': preferences},
                           sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        if len(data) > MAX_PREFERENCES_BYTES:
            raise PreferenceError('Local preferences exceed their size bound.')
        temporary = '.shell-preferences-' + uuid.uuid4().hex + '.tmp'
        with self._directory() as directory:
            self._check_current(directory)
            if reset and self.corrupt is not None:
                backup = 'shell-preferences-invalid-' + uuid.uuid4().hex + '.bak'
                self._write_new(directory, backup, self.corrupt)
                self.last_backup_name = backup
                if directory is not None:
                    os.fsync(directory)
            try:
                self._write_new(directory, temporary, data)
                self._check_parent()
                self._check_current(directory)
                if directory is not None:
                    os.replace(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                else:
                    os.replace(self.parent/temporary, self.path)
                info = self._stat(directory, self.path.name)
                self.expected = _identity(info)
            finally:
                self._unlink(directory, temporary)
        self.corrupt = None
        self.error = ''


class ShellState:
    """Pure-Python app navigation; no camera authority or image data dependency."""
    def __init__(self, state_path=None, available_apps=None):
        if available_apps is None:
            self._available = set(_APP_IDS)
        elif not isinstance(available_apps, (set, frozenset, list, tuple)):
            raise ValueError('Available applications must be an explicit collection.')
        else:
            self._available = set(available_apps)
            if 'camera' not in self._available or not self._available <= _APP_IDS:
                raise ValueError('Application selection must include Camera and contain known routes only.')
        self._current_app = 'camera'
        self._current_group = 'exposure'
        self._menu_open = True
        self._history = []
        self._error = ''
        self._store = _PreferenceStore(state_path) if state_path is not None else None
        self._preferences = self._store.load() if self._store is not None else dict(DEFAULT_PREFERENCES)

    @property
    def current_app(self): return self._current_app

    @property
    def current_group(self): return self._current_group

    @property
    def menu_open(self): return self._menu_open

    @property
    def can_go_back(self): return bool(self._history) or self._current_app != 'camera'

    @property
    def preferences(self): return dict(self._preferences)

    @property
    def preferences_persistent(self):
        return self._store is not None and not self._store.unsafe and self._store.corrupt is None and not self._store.error

    @property
    def error_text(self):
        return self._error or (self._store.error if self._store is not None else '')

    @property
    def preference_backup_name(self):
        return self._store.last_backup_name if self._store is not None else ''

    @property
    def apps(self):
        return [{'id': app_id, 'title': title, 'label': label, 'shortLabel': short,
                 'section': section, 'enabled': app_id in self._available,
                 'reason': '' if app_id in self._available else 'This application is unavailable in this shell profile.'}
                for app_id, title, label, short, section in _APP_DEFINITIONS]

    @property
    def settings_groups(self):
        return [{'id': group, 'title': title, 'label': label, 'description': description}
                for group, title, label, description in _GROUP_DEFINITIONS]

    def _fail(self, message):
        self._error = message
        return False

    def _remember(self):
        self._history.append((self._current_app, self._current_group))
        self._history = self._history[-16:]

    def navigate(self, app_id):
        if type(app_id) is not str or app_id not in _APP_IDS:
            return self._fail('Unknown DaOS camera application.')
        if app_id not in self._available:
            return self._fail('This DaOS camera application is unavailable in the current profile.')
        if app_id != self._current_app:
            self._remember()
            self._current_app = app_id
        self._error = ''
        return True

    def back(self):
        self._error = ''
        while self._history:
            app_id, group = self._history.pop()
            if app_id in self._available:
                self._current_app, self._current_group = app_id, group
                return True
        if self._current_app != 'camera':
            self._current_app = 'camera'
            return True
        return False

    def toggle_menu(self):
        self._menu_open = not self._menu_open
        return self._menu_open

    def select_group(self, group_id):
        if type(group_id) is not str or group_id not in _GROUP_IDS:
            return self._fail('Unknown camera setting group.')
        if 'settings' not in self._available:
            return self._fail('Camera settings are unavailable in this shell profile.')
        if self._current_app != 'settings' or group_id != self._current_group:
            self._remember()
            self._current_app, self._current_group = 'settings', group_id
        self._error = ''
        return True

    def set_preference(self, key, value):
        if type(key) is not str or key not in DEFAULT_PREFERENCES:
            return self._fail('Unknown local display preference.')
        candidate = dict(self._preferences)
        candidate[key] = value
        try:
            _valid_preferences(candidate)
            if self._store is not None:
                self._store.save(candidate)
        except (OSError, PreferenceError) as error:
            return self._fail(str(error))
        self._preferences = candidate
        self._error = ''
        return True

    def reset_preferences(self):
        candidate = dict(DEFAULT_PREFERENCES)
        try:
            if self._store is not None:
                self._store.save(candidate, reset=True)
        except (OSError, PreferenceError) as error:
            return self._fail(str(error))
        self._preferences = candidate
        self._error = ''
        return True


try:
    from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot
except ImportError:
    class ShellController:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('ShellController requires the optional PyQt5 runtime; ShellState does not.')
else:
    class ShellController(QObject):
        """Qt5 adapter exposed as the read-only-property `cameraShell` context."""
        changed = pyqtSignal()

        def __init__(self, state_path=None, parent=None, *, available_apps=None):
            super().__init__(parent)
            self._state = ShellState(state_path, available_apps)

        @pyqtProperty('QVariantList', notify=changed)
        def apps(self): return self._state.apps

        @pyqtProperty('QVariantList', notify=changed)
        def settingsGroups(self): return self._state.settings_groups

        @pyqtProperty(str, notify=changed)
        def currentApp(self): return self._state.current_app

        @pyqtProperty(str, notify=changed)
        def appTitle(self):
            return next(item['title'] for item in self._state.apps if item['id'] == self._state.current_app)

        @pyqtProperty(str, notify=changed)
        def currentGroup(self): return self._state.current_group

        @pyqtProperty(bool, notify=changed)
        def menuOpen(self): return self._state.menu_open

        @pyqtProperty(bool, notify=changed)
        def canGoBack(self): return self._state.can_go_back

        @pyqtProperty('QVariantMap', notify=changed)
        def preferences(self): return self._state.preferences

        @pyqtProperty(bool, notify=changed)
        def gridEnabled(self): return self._state.preferences['grid_enabled']

        @pyqtProperty(str, notify=changed)
        def theme(self): return self._state.preferences['theme']

        @pyqtProperty(int, notify=changed)
        def previewBrightness(self): return self._state.preferences['preview_brightness']

        @pyqtProperty(bool, notify=changed)
        def preferencesPersistent(self): return self._state.preferences_persistent

        @pyqtProperty(str, notify=changed)
        def errorText(self): return self._state.error_text

        @pyqtProperty(str, notify=changed)
        def preferenceBackupName(self): return self._state.preference_backup_name

        def _call(self, method, *args):
            result = getattr(self._state, method)(*args)
            self.changed.emit()
            return result

        @pyqtSlot(str, result=bool)
        def navigate(self, app_id): return self._call('navigate', app_id)

        @pyqtSlot(result=bool)
        def back(self): return self._call('back')

        @pyqtSlot(result=bool)
        def toggleMenu(self): return self._call('toggle_menu')

        @pyqtSlot(str, result=bool)
        def selectGroup(self, group_id): return self._call('select_group', group_id)

        @pyqtSlot(str, 'QVariant', result=bool)
        def setPreference(self, key, value):
            # QML stores Number values as doubles, including Math.round results.
            # Normalize only integral display-brightness numbers at this bridge;
            # the pure model remains strict about integer values and its bounds.
            if (key == 'preview_brightness' and type(value) is float
                    and math.isfinite(value) and value.is_integer()):
                value = int(value)
            return self._call('set_preference', key, value)

        @pyqtSlot(result=bool)
        def resetPreferences(self): return self._call('reset_preferences')

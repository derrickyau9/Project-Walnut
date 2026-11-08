"""Local recipes, explicit journaled workflow execution, media and diagnostics.

Saving a recipe never captures. Only an explicit Run uses CameraController's
exclusive authority handoff and the existing resumable workflow engine.
"""
from __future__ import annotations

import json
import importlib.util
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid

from PyQt5.QtCore import QObject, QT_VERSION_STR, PYQT_VERSION_STR, pyqtProperty, pyqtSignal, pyqtSlot

from daos.capability_runtime.recipes import FORMAT, MAX_RECIPE_BYTES, RecipeError, dumps_recipe, loads_recipe, normalize_recipe

MAX_RECIPES = 100
MAX_DIRECTORY_ENTRIES = 512
MAX_REPORT_BYTES = 65536
_RECIPE_ID = re.compile(r'[0-9a-f]{32}\Z')
_RECIPE_FILE = re.compile(r'([0-9a-f]{32})\.recipe\.json\Z')
_FILE_ERRORS = (OSError, ValueError, TypeError, RecipeError)


def _companion(name):
    """Load sibling helpers even when a host imports this file by absolute path."""
    key = 'daos_ondevice_' + name
    if key not in sys.modules:
        path = Path(__file__).with_name(name + '.py')
        existing = sys.modules.get(name)
        try:
            same_sibling = existing is not None and Path(existing.__file__).resolve() == path.resolve()
        except (AttributeError, OSError, TypeError, ValueError):
            same_sibling = False
        if same_sibling:
            sys.modules[key] = existing
            return existing
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        sys.modules.setdefault(name, module)
        spec.loader.exec_module(module)
    return sys.modules[key]


class AppFileError(ValueError):
    pass


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _checked_directory(path, *, create=False):
    """Reject links/reparse points and nondirectories, including ancestors."""
    if os.fspath(path).startswith(('\\\\', '//')):
        raise AppFileError('choose a local filesystem directory, not a network path')
    path = Path(os.path.abspath(os.fspath(path)))
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if not create:
                raise AppFileError('destination parent must already exist')
            item.mkdir(mode=0o700)
            info = item.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise AppFileError('app directories must be ordinary directories, without symbolic links')
    return path


def _identity(path):
    info = path.lstat()
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise AppFileError('app directory identity changed')
    return info.st_dev, info.st_ino


def _read_recipe(path):
    """Bounded no-follow leaf read; portable recipe parser rejects extra fields."""
    before = path.lstat()
    if _is_link(before) or not stat.S_ISREG(before.st_mode):
        raise AppFileError('recipe must be an ordinary file')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)):
            raise AppFileError('recipe changed while opening')
        raw = stream.read(MAX_RECIPE_BYTES + 1)
    return loads_recipe(raw)


def _publish_new(parent, filename, data):
    """Sync a private temporary file and atomically publish without replacing."""
    parent = _checked_directory(parent)
    identity = _identity(parent)
    temporary = None
    directory_fd = None
    try:
        if os.name != 'nt':
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            opened = os.fstat(directory_fd)
            if (opened.st_dev, opened.st_ino) != identity:
                raise AppFileError('destination directory changed while opening')
            candidate = '.DaOS-app-' + uuid.uuid4().hex + '.tmp'
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
            temporary = candidate
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if _identity(parent) != identity:
                raise AppFileError('destination directory changed before publication')
            os.link(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
            os.fsync(directory_fd)
        else:
            with tempfile.NamedTemporaryFile(dir=parent, prefix='.DaOS-app-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if _identity(parent) != identity:
                raise AppFileError('destination directory changed before publication')
            os.link(temporary, parent/filename)
        if _identity(parent) != identity:
            raise AppFileError('destination directory changed during publication; inspect the existing file')
    except FileExistsError as error:
        raise AppFileError('destination already exists; choose a new file') from error
    finally:
        if temporary is not None:
            if directory_fd is not None:
                os.unlink(temporary, dir_fd=directory_fd)
            else:
                temporary.unlink(missing_ok=True)
        if directory_fd is not None:
            os.close(directory_fd)


def _export_target(destination):
    if not isinstance(destination, str) or len(destination) > 2048 or any(ord(c) < 32 for c in destination):
        raise AppFileError('choose an absolute new local .json file')
    if destination.startswith(('\\\\', '//')):
        raise AppFileError('choose a local filesystem destination, not a network path')
    path = Path(destination)
    if (not path.is_absolute() or path.suffix != '.json' or '..' in path.parts
            or path.name.endswith((' ', '.')) or ':' in path.name or not path.stem):
        raise AppFileError('choose an absolute new local .json file')
    _checked_directory(path.parent)
    return path


class CameraAppsController(QObject):
    """Qt bridge to small useful apps, independent of realtime camera calls."""
    changed = pyqtSignal()

    def __init__(self, camera_controller, data_dir, parent=None):
        super().__init__(parent)
        self._camera = camera_controller
        self._data_root = Path(os.path.abspath(os.fspath(data_dir)))
        self._root = self._data_root/'apps'
        self._recipes_dir = self._root/'recipes'
        self._exports_dir = self._root/'exports'
        self._recipes = []
        self._selected = ''
        self._selected_json = ''
        self._message = 'Create a portable recipe. Saving does not take a photograph.'
        self._message_scope = 'recipes'
        self._error = ''
        self._directory_error = ''
        try:
            for directory in (self._root, self._recipes_dir, self._exports_dir):
                _checked_directory(directory, create=True)
        except (OSError, ValueError, TypeError) as error:
            self._directory_error = str(error)
            self._error = 'Local app storage is unavailable: ' + str(error)
        self._diagnostics = {}
        self._closed = False
        self._media = _companion('media_library').MediaLibrary(camera_controller, self._data_root, self)
        self._workflow = _companion('workflow_helpers').CameraWorkflow(camera_controller, self._data_root, self)
        self._catalog = self._tasks = None
        self._task_hub = None
        if not self._directory_error and callable(getattr(camera_controller,'runExclusiveWorkflow',None)):
            self._catalog = _companion('media_catalog').MediaCatalog(self._data_root,legacy_reader=self._media._store.read,
                client_getter=lambda:getattr(self._camera,'_client',None))
            self._tasks = _companion('task_service').TaskService(self._catalog,lambda:getattr(self._camera,'_client',None),self)
            workspace = _companion('media_workspace')
            self._media._store = workspace.CatalogSidecarStore(self._media._store,self._catalog,lambda:getattr(self._camera,'_client',None))
            self._media._gallery = workspace.GalleryWorkspace(camera_controller,self._catalog,self._tasks,self)
        shutdown = getattr(camera_controller, 'shutdownStarted', None)
        if shutdown is not None: shutdown.connect(self.close)
        signal = getattr(camera_controller, 'changed', None)
        if signal is not None:
            signal.connect(self._camera_changed)
        self.refreshRecipes()
        self.refreshDiagnostics()

    @pyqtProperty(QObject, constant=True)
    def media(self): return self._media

    @pyqtProperty(QObject, constant=True)
    def workflow(self): return self._workflow

    @property
    def catalog(self):return self._catalog

    @pyqtProperty(QObject,notify=changed)
    def tasks(self):return self._task_hub or self._tasks
    def attachTaskHub(self,hub):self._task_hub=hub;self.changed.emit()

    def attachProcessing(self,processing):
        if self._tasks is None:return
        def submit(kind,params,destination):
            gallery=self._media.gallery
            sources=params.get('sources') or gallery.processing_inputs()
            if not sources:raise AppFileError('请先在照片库选择照片')
            return self._tasks.submit_processing(kind,dict(params,sources=sources),destination)
        processing.setSubmitter(submit)

    @pyqtSlot()
    def runSelectedRecipe(self):
        self._message_scope = 'recipes'
        try:
            if not self._selected_json:
                raise AppFileError('select a saved recipe first')
            value = loads_recipe(self._selected_json)
            if self._workflow.start(value['plan']):
                self._error = ''
                self._message = 'Running ' + value['name'] + '. Original request UUIDs are journaled for every shot.'
                self.changed.emit()
            else:
                self._fail('Workflow was not started', self._workflow.errorText)
        except _FILE_ERRORS as error:
            self._fail('Workflow was not started', error)

    @pyqtSlot(str, str, int, int)
    def saveCaptureRecipe(self, name, kind, count, interval_ms):
        """Touch-friendly plans based on current exposure; no capture on save."""
        self._message_scope = 'recipes'
        try:
            self._check_store()
            if len(self._recipes) >= MAX_RECIPES:
                raise AppFileError('at most 100 recipes are supported')
            if kind not in {'bracket', 'interval', 'dark', 'flat'} or not 1 <= count <= 100:
                raise AppFileError('choose a supported sequence and 1 to 100 shots')
            exposure = int(self._camera.exposureUs)
            exposures = ([max(100, exposure // 2), exposure, min(30000000, exposure * 2)]
                         if kind == 'bracket' else [exposure] * count)
            plan = {'kind': kind, 'exposures_us': exposures, 'sensor_mode': self._camera.sensorMode,
                    'requested_analogue_gain': self._camera.analogueGain, 'output_format': 'dng'}
            if kind == 'interval': plan['interval_ms'] = interval_ms
            recipe = normalize_recipe({'format': FORMAT, 'name': name, 'plan': plan})
            identity = uuid.uuid4().hex
            _publish_new(self._recipes_dir, identity + '.recipe.json', dumps_recipe(recipe).encode('utf-8'))
            self.refreshRecipes(); self.selectRecipe(identity)
            self._message = 'Plan saved. Select Run to take photographs.'; self.changed.emit()
        except (*_FILE_ERRORS, AttributeError) as error:
            self._fail('Recipe was not saved', error)

    def close(self):
        if self._closed: return
        self._closed = True
        if self._tasks:self._tasks.close()
        if self._media.gallery:self._media.gallery.close()
        self._workflow.close(); self._media.close()

    @pyqtProperty('QVariantList', notify=changed)
    def recipes(self): return [dict(row, exposuresUs=list(row['exposuresUs'])) for row in self._recipes]

    @pyqtProperty(str, notify=changed)
    def selectedRecipeId(self): return self._selected

    @pyqtProperty(str, notify=changed)
    def selectedRecipeJson(self): return self._selected_json

    @pyqtProperty(str, notify=changed)
    def message(self): return self._message

    @pyqtProperty(str, notify=changed)
    def messageScope(self): return self._message_scope

    @pyqtProperty(str, notify=changed)
    def errorText(self): return self._error

    @pyqtProperty(str, notify=changed)
    def exportDirectory(self): return str(self._exports_dir)

    @pyqtProperty('QVariantList', notify=changed)
    def diagnosticRows(self):
        camera = self._diagnostics.get('camera', {})
        return [
            self._row('Connection', camera.get('connection', 'unknown'), 'success' if camera.get('connection') == 'ready' else 'warning'),
            self._row('Capture outcome', camera.get('capture_status', 'unknown')),
            self._row('Original request ID', camera.get('request_id') or 'No capture submitted'),
            self._row('Requested exposure', str(camera.get('requested_exposure_us', 'unknown')) + ' us'),
            self._row('Requested gain', str(camera.get('requested_analogue_gain', 'unknown')) + 'x'),
            self._row('Saved DNG', camera.get('dng_path') or 'No committed photograph selected'),
            self._row('Device telemetry', 'Not measured; HF-Sim software only', 'warning'),
        ]

    @pyqtProperty('QVariantList', notify=changed)
    def systemRows(self):
        runtime = self._diagnostics.get('runtime', {})
        storage = self._diagnostics.get('storage', {})
        return [self._row('System', runtime.get('system', 'unknown') + ' ' + runtime.get('release', '')),
                self._row('Runtime architecture', runtime.get('machine', 'unknown')),
                self._row('Python / Qt', runtime.get('python', '?') + ' / ' + runtime.get('Qt', '?')),
                self._row('Data filesystem free', self._bytes_label(storage.get('free_bytes'))),
                self._row('Data location', str(self._data_root)),
                self._row('Updates', 'BSD A/B update integration is unavailable', 'warning')]

    @pyqtProperty('QVariantList', notify=changed)
    def deviceRows(self):
        camera = self._diagnostics.get('camera', {})
        return [self._row('Authority', camera.get('backend', 'unknown')),
                self._row('Image source', 'HF-Sim synthetic Bayer', 'warning'),
                self._row('Selected mode', camera.get('sensor_mode', 'unknown')),
                self._row('RAW output', 'DNG; 14-bit synthetic values in 16-bit containers'),
                self._row('Physical sensor', 'Not connected / not validated', 'warning'),
                self._row('Lens / HUMI / MCU', 'Hardware interfaces unavailable', 'warning')]

    @pyqtProperty('QVariantList', notify=changed)
    def storageRows(self):
        storage = self._diagnostics.get('storage', {})
        return [self._row('Data filesystem total', self._bytes_label(storage.get('total_bytes'))),
                self._row('Data filesystem free', self._bytes_label(storage.get('free_bytes'))),
                self._row('Local app data', str(self._root)),
                self._row('Portable recipes', str(len(self._recipes))),
                self._row('Export folder', str(self._exports_dir)),
                self._row('Storage validation', 'Capacity only; media speed and power-loss behavior are untested', 'warning')]

    @staticmethod
    def _row(label, value, tone='muted'):
        return {'label': label, 'value': str(value), 'tone': tone}

    @staticmethod
    def _bytes_label(value):
        return ('Not available' if type(value) is not int else f'{value / 1024**3:.2f} GiB')

    def _fail(self, operation, error):
        self._error = operation + ': ' + str(error)
        self._message = ''
        self.changed.emit()

    def _check_store(self):
        if self._directory_error:
            raise AppFileError(self._directory_error)
        _checked_directory(self._recipes_dir)

    @pyqtSlot()
    def refreshRecipes(self):
        self._message_scope = 'recipes'
        try:
            self._check_store()
            rows = []; skipped = 0
            identity = _identity(self._recipes_dir)
            for count, path in enumerate(self._recipes_dir.iterdir(), 1):
                if count > MAX_DIRECTORY_ENTRIES:
                    raise AppFileError('recipe directory contains too many entries')
                match = _RECIPE_FILE.fullmatch(path.name)
                if not match:
                    continue
                if len(rows) >= MAX_RECIPES:
                    raise AppFileError('at most 100 recipes are supported in this local editor')
                try:
                    value = _read_recipe(path)
                except _FILE_ERRORS:
                    skipped += 1
                    continue
                plan = value['plan']
                rows.append({'id': match[1], 'name': value['name'], 'kind': plan.get('kind', 'bracket'),
                             'exposureCount': len(plan['exposures_us']), 'exposuresUs': plan['exposures_us'],
                             'sensorMode': plan['sensor_mode'], 'analogueGain': plan['requested_analogue_gain']})
            if _identity(self._recipes_dir) != identity:
                raise AppFileError('recipe directory changed while reading')
            self._recipes = sorted(rows, key=lambda row: (row['name'].casefold(), row['id']))
            self._error = ''
            if skipped:
                self._message = f'Skipped {skipped} invalid or unsafe recipe file(s).'
            elif not rows:
                self._message = 'No saved recipes. Create a bracket to keep or share offline.'
            else:
                self._message = f'{len(rows)} portable recipe(s). Select a plan, then Run.'
            if self._selected and self._selected not in {row['id'] for row in rows}:
                self._selected = self._selected_json = ''
            self.changed.emit()
        except _FILE_ERRORS as error:
            self._fail('Recipe list unavailable', error)

    @pyqtSlot(str, str)
    def saveBracketRecipe(self, name, exposure_csv):
        self._message_scope = 'recipes'
        try:
            self._check_store()
            if len(self._recipes) >= MAX_RECIPES:
                raise AppFileError('at most 100 recipes are supported in this local editor')
            if not isinstance(exposure_csv, str) or len(exposure_csv) > 4096:
                raise AppFileError('enter 1 to 100 comma-separated integer exposures in microseconds')
            parts = [part.strip() for part in exposure_csv.split(',')]
            if not 1 <= len(parts) <= 100 or any(not re.fullmatch(r'[0-9]{1,8}', part) for part in parts):
                raise AppFileError('enter 1 to 100 comma-separated integer exposures in microseconds')
            recipe = normalize_recipe({'format': FORMAT, 'name': name,
                'plan': {'kind': 'bracket', 'exposures_us': [int(part) for part in parts],
                         'sensor_mode': self._camera.sensorMode,
                         'requested_analogue_gain': self._camera.analogueGain, 'output_format': 'dng'}})
            encoded = dumps_recipe(recipe).encode('utf-8')
            identity = uuid.uuid4().hex
            _publish_new(self._recipes_dir, identity + '.recipe.json', encoded)
            self.refreshRecipes()
            self.selectRecipe(identity)
            if not self._error:
                self._message = 'Recipe saved locally. Saving did not submit a capture.'
                self.changed.emit()
        except (*_FILE_ERRORS, AttributeError) as error:
            self._fail('Recipe was not saved', error)

    @pyqtSlot(str)
    def selectRecipe(self, identity):
        self._message_scope = 'recipes'
        try:
            self._check_store()
            if not isinstance(identity, str) or not _RECIPE_ID.fullmatch(identity):
                raise AppFileError('select a valid local recipe ID')
            directory_identity = _identity(self._recipes_dir)
            recipe = _read_recipe(self._recipes_dir/(identity + '.recipe.json'))
            if _identity(self._recipes_dir) != directory_identity:
                raise AppFileError('recipe directory changed while reading')
            self._selected = identity
            self._selected_json = dumps_recipe(recipe)
            self._error = ''
            self._message = 'Plan selected. Run explicitly starts the journaled sequence.'
            self.changed.emit()
        except _FILE_ERRORS as error:
            self._fail('Recipe could not be selected', error)

    @pyqtSlot(str)
    def exportRecipe(self, destination):
        self._message_scope = 'recipes'
        try:
            if not self._selected or not self._selected_json:
                raise AppFileError('select a saved recipe first')
            # Export the selected immutable UI snapshot, validated again. No IDs,
            # camera leases, journal rows or credentials enter this format.
            encoded = dumps_recipe(loads_recipe(self._selected_json)).encode('utf-8')
            path = _export_target(destination)
            _publish_new(path.parent, path.name, encoded)
            self._error = ''
            self._message = 'Portable recipe exported to ' + str(path)
            self.changed.emit()
        except _FILE_ERRORS as error:
            self._fail('Recipe export failed', error)

    def _camera_changed(self):
        # This is a snapshot of existing UI state, not another camera API call.
        self.refreshDiagnostics()

    @pyqtSlot()
    def refreshDiagnostics(self):
        camera = self._camera
        requested_gain = getattr(camera, 'analogueGain', None)
        if isinstance(requested_gain, bool) or not isinstance(requested_gain, (int, float)) or not math.isfinite(requested_gain):
            requested_gain = None
        exposure = getattr(camera, 'exposureUs', None)
        if type(exposure) is not int:
            exposure = None
        storage = {'scope': 'data-directory filesystem; not card throughput or media health'}
        try:
            _checked_directory(self._data_root)
            usage = shutil.disk_usage(self._data_root)
            storage.update(total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free)
        except (OSError, ValueError):
            storage['unavailable'] = True
        self._diagnostics = {
            'format': 'hasslefree.device-diagnostics.v1',
            'generated_at_unix_ms': int(time.time() * 1000),
            'synthetic': True, 'physical_hardware_validated': False,
            'camera': {'backend': str(getattr(camera, 'backendLabel', 'unknown'))[:512],
                       'connection': str(getattr(camera, 'connectionState', 'unknown'))[:64],
                       'capture_status': str(getattr(camera, 'captureState', 'unknown'))[:64],
                       'request_id': str(getattr(camera, 'lastRequestId', ''))[:128],
                       'requested_exposure_us': exposure,
                       'requested_analogue_gain': requested_gain,
                       'sensor_mode': str(getattr(camera, 'sensorMode', 'unknown'))[:64],
                       'dng_path': str(getattr(camera, 'dngPath', ''))[:2048],
                       'parameter_scope': 'requested UI state; actual per-frame values remain in capture evidence'},
            'runtime': {'system': platform.system(), 'release': platform.release(),
                        'machine': platform.machine(), 'python': platform.python_version(),
                        'Qt': QT_VERSION_STR, 'PyQt': PYQT_VERSION_STR},
            'storage': storage,
            'limits': ['No physical sensor, lens, FPGA or MCU telemetry is measured.',
                       'This diagnostics snapshot reads UI state only; explicitly started workflows acquire camera control separately.',
                       'BSD A/B updating and hardware recovery remain unimplemented.']
        }
        self.changed.emit()

    @pyqtSlot(str)
    def exportDiagnostics(self, destination):
        self._message_scope = 'diagnostics'
        try:
            self.refreshDiagnostics()
            encoded = (json.dumps(self._diagnostics, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
            if len(encoded) > MAX_REPORT_BYTES:
                raise AppFileError('diagnostic report exceeds its size bound')
            path = _export_target(destination)
            _publish_new(path.parent, path.name, encoded)
            self._error = ''
            self._message = 'Read-only diagnostic snapshot exported to ' + str(path)
            self.changed.emit()
        except _FILE_ERRORS as error:
            self._fail('Diagnostics export failed', error)

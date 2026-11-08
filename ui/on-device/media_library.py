"""Reversible photo organization and versioned, non-destructive sidecars.

The capture authority owns original DNG files. This module never renames,
rewrites or deletes them. Trash is a reversible library flag, not disk erasure.
"""
from __future__ import annotations

import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import importlib.util
import math
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
import uuid

from PyQt5.QtCore import QObject, QBuffer, QByteArray, QIODevice, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage

FORMAT = 'daos.photo-sidecar.v1'
DEFAULT_EDITS = {'exposure': 0.0, 'contrast': 1.0, 'saturation': 1.0, 'rotation': 0, 'crop': None}


def _pipeline():
    if 'image_pipeline' not in sys.modules:
        spec = importlib.util.spec_from_file_location('image_pipeline', Path(__file__).with_name('image_pipeline.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules['image_pipeline'] = module; spec.loader.exec_module(module)
    return sys.modules['image_pipeline']


def _photo_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('a canonical capture UUID is required')
    return value


class MediaStore:
    """Small atomic per-photo sidecars; corrupt existing records stay visible."""
    def __init__(self, root):
        self._lock = threading.RLock()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('media sidecars need an ordinary directory')

    def read(self, identity):
        path = self.root / (_photo_id(identity) + '.daos.json')
        value = {'format': FORMAT, 'request_id': identity, 'rating': 0,
                 'protected': False, 'trashed_at': None, 'edits': dict(DEFAULT_EDITS)}
        try:
            if path.is_symlink():
                raise ValueError('sidecar must not be a symbolic link')
            with path.open('rb') as stream:
                raw = stream.read(32769)
            if len(raw) > 32768:
                raise ValueError('sidecar is too large')
            saved = json.loads(raw)
        except FileNotFoundError:
            return value
        if (not isinstance(saved, dict) or saved.get('format') != FORMAT
                or saved.get('request_id') != identity
                or type(saved.get('rating')) is not int or not 0 <= saved['rating'] <= 5
                or type(saved.get('protected')) is not bool
                or (saved.get('trashed_at') is not None and type(saved['trashed_at']) not in (int, float))):
            raise ValueError('invalid sidecar; original photo and sidecar were preserved')
        edits = saved.get('edits')
        if not isinstance(edits, dict) or set(edits) - set(DEFAULT_EDITS) or not {'exposure','contrast','saturation'} <= set(edits):
            raise ValueError('unsupported edit sidecar')
        for name, bounds in (('exposure', (-5, 5)), ('contrast', (.5, 2)), ('saturation', (0, 2))):
            number = edits[name]
            if type(number) not in (int, float) or not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
                raise ValueError('invalid sidecar adjustment')
        edits = dict(DEFAULT_EDITS, **edits)
        if edits['rotation'] not in (0, 90, 180, 270):
            raise ValueError('invalid sidecar rotation')
        crop = edits['crop']
        if crop is not None and (not isinstance(crop, list) or len(crop) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in crop)
                or not (0 <= crop[0] < 1 and 0 <= crop[1] < 1 and crop[2] > 0 and crop[3] > 0
                        and crop[0] + crop[2] <= 1.000001 and crop[1] + crop[3] <= 1.000001)):
            raise ValueError('invalid sidecar crop')
        saved['edits'] = edits
        return saved

    def write(self, identity, changes):
        with self._lock:
            return self._write(identity, changes)

    def _write(self, identity, changes):
        value = self.read(identity)
        value.update(changes)
        value['updated_at_unix_ms'] = int(time.time() * 1000)
        payload = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix='.sidecar-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.root / (_photo_id(identity) + '.daos.json'))
            if os.name != 'nt':
                descriptor = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value


def render_adjustments(url, edits):
    """Apply a display-referred edit to the bounded review, never the RAW."""
    if not url.startswith('data:image/png;base64,'):
        return ''
    image = QImage.fromData(base64.b64decode(url.partition(',')[2], validate=True))
    if image.isNull():
        raise ValueError('review image could not be decoded')
    image = image.scaled(960, 640, 1, 1) if image.width() > 960 or image.height() > 640 else image
    image = _pipeline().apply_review_edits(image, edits)
    packet = QByteArray(); device = QBuffer(packet); device.open(QIODevice.WriteOnly)
    if not image.save(device, 'PNG'):
        raise ValueError('adjusted preview could not be encoded')
    return 'data:image/png;base64,' + base64.b64encode(bytes(packet)).decode('ascii')


class MediaLibrary(QObject):
    changed = pyqtSignal()
    _rendered = pyqtSignal(int, str, str)
    _exported = pyqtSignal(object, str)
    _sidecarsRead = pyqtSignal(object, object)
    _writeFinished = pyqtSignal(str, object, str, str)

    def __init__(self, camera, data_dir, parent=None):
        super().__init__(parent)
        self._camera = camera
        self._gallery = None
        self._store = None
        self._filter = 'all'
        self._selected = ''; self._record = {}; self._items = []; self._error = ''; self._message = ''
        self._records = {}; self._pending_edits = {}
        self._confirmed_records = {}; self._failed_reads = set(); self._reading = False
        self._write_pending = False; self._export_pending = False
        try: self._store = MediaStore(Path(data_dir) / 'apps' / 'media')
        except (OSError, ValueError) as error: self._error = 'Media sidecars unavailable: ' + str(error)
        self._preview_url = ''; self._base_url = ''; self._generation = 0; self._closed = False
        self._render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daos-media-edit')
        self._reader_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daos-media-read')
        self._writer_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daos-media-write')
        self._render_pending = False
        self._render_timer = QTimer(self); self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120); self._render_timer.timeout.connect(self._render)
        self._rendered.connect(self._on_rendered)
        self._exported.connect(self._on_exported)
        self._sidecarsRead.connect(self._on_sidecars_read)
        self._writeFinished.connect(self._on_write_finished)
        self._edit_save_timer = QTimer(self); self._edit_save_timer.setSingleShot(True)
        self._edit_save_timer.setInterval(250); self._edit_save_timer.timeout.connect(self._save_pending_edits)
        camera.changed.connect(self.refresh)
        self.refresh()

    @pyqtProperty('QVariantList', notify=changed)
    def items(self): return list(self._items)
    @pyqtProperty(QObject,notify=changed)
    def gallery(self):return self._gallery
    @property
    def imageProvider(self):return self._gallery.imageProvider if self._gallery else None
    @property
    def catalog(self):return self._gallery.catalog if self._gallery else None
    @pyqtProperty(bool, notify=changed)
    def ready(self): return self._store is not None and self._selected in self._records and self._selected not in self._failed_reads
    @pyqtProperty(bool, notify=changed)
    def busy(self): return self._write_pending
    @pyqtProperty(bool, notify=changed)
    def exporting(self): return self._export_pending
    @pyqtProperty(str, notify=changed)
    def filterName(self): return self._filter
    @pyqtProperty(str, notify=changed)
    def selectedId(self): return self._selected
    @pyqtProperty(int, notify=changed)
    def rating(self): return self._record.get('rating', 0)
    @pyqtProperty(bool, notify=changed)
    def protected(self): return self._record.get('protected', False)
    @pyqtProperty(bool, notify=changed)
    def trashed(self): return self._record.get('trashed_at') is not None
    @pyqtProperty('QVariantMap', notify=changed)
    def selectedEdits(self): return dict(self._record.get('edits', DEFAULT_EDITS))
    @pyqtProperty(str, notify=changed)
    def previewUrl(self): return self._preview_url
    @pyqtProperty(str, notify=changed)
    def message(self): return self._error or self._message
    @pyqtProperty(str, notify=changed)
    def archiveText(self):
        return ('Capture UUID: ' + self._selected + '\n\n' + str(getattr(self._camera, 'selectedCaptureInfo', ''))
                + '\n\nDaOS sidecar (original DNG retained):\n' + json.dumps(self._record, ensure_ascii=False, indent=2))

    @pyqtSlot()
    def refresh(self):
        if self._closed: return
        previous = self._selected
        self._selected = str(getattr(self._camera, 'selectedCaptureId', ''))
        try:
            if self._store is None:
                self._items = list(getattr(self._camera, 'libraryItems', [])); self.changed.emit(); return
            self._record = self._records.get(self._selected, {})
            missing = {self._selected} if self._selected and self._selected not in self._records else set()
            rows = []
            for row in getattr(self._camera, 'libraryItems', []):
                try:
                    identity = row['request_id']
                    if identity not in self._records: missing.add(identity)
                    sidecar = self._records.get(identity, {'rating':0,'protected':False,'trashed_at':None})
                    trash = sidecar.get('trashed_at') is not None
                    if ((self._filter == 'trash') != trash
                            or self._filter == 'rated' and sidecar['rating'] < 1
                            or self._filter == 'protected' and not sidecar['protected']):
                        continue
                    rows.append(dict(row, rating=sidecar['rating'], protected=sidecar['protected'], trashed=trash))
                except (OSError, ValueError, TypeError) as error:
                    self._error = 'Sidecar unavailable: ' + str(error)
            self._items = rows
            self._queue_reads(sorted(missing - self._failed_reads)[:51])
        except (OSError, ValueError, TypeError) as error:
            self._record = {}; self._error = str(error)
        url = str(getattr(self._camera, 'reviewUrl', ''))
        if previous != self._selected or url != self._base_url:
            self._base_url = url; self._preview_url = ''; self._generation += 1
            self._render_timer.start()
        self.changed.emit()

    def _queue_reads(self, identities):
        if not identities or self._reading or self._closed: return
        self._reading = True
        def read():
            values, errors = {}, {}
            for identity in identities:
                try: values[identity] = self._store.read(identity)
                except Exception as error: errors[identity] = str(error)
            return values, errors
        future = self._reader_executor.submit(read)
        def done(completed):
            values, errors = completed.result()
            if not self._closed:
                try: self._sidecarsRead.emit(values, errors)
                except RuntimeError: pass
        future.add_done_callback(done)

    @pyqtSlot(object, object)
    def _on_sidecars_read(self, values, errors):
        self._reading = False
        for identity, value in values.items():
            if identity not in self._records:
                self._records[identity] = copy.deepcopy(value)
                self._confirmed_records[identity] = copy.deepcopy(value)
        if errors:
            self._failed_reads.update(errors)
            self._error = '旁车读取失败，原文件已保留：' + next(iter(errors.values()))
        self.refresh()
        if self._selected in values:
            self._generation += 1; self._render_timer.start()

    @pyqtSlot(str)
    def setFilter(self, value):
        if value in {'all', 'rated', 'protected', 'trash'}:
            self._filter = value; self.refresh()

    def _update(self, changes, message):
        try:
            if self._store is None: raise ValueError('media sidecar storage is unavailable')
            if not self.ready: raise ValueError('照片旁车尚未加载完成')
            if self._write_pending: raise ValueError('正在保存，请等待确认')
            self._start_write(self._selected, changes, message)
            return True
        except (OSError, ValueError, TypeError) as error:
            self._error = 'Change was not saved: ' + str(error); self.changed.emit()
            return False

    def _start_write(self, identity, changes, message):
        self._write_pending = True; self._error = ''; self._message = '正在保存，请稍候…'; self.changed.emit()
        future = self._writer_executor.submit(self._store.write, identity, copy.deepcopy(changes))
        def done(completed):
            try: value, error = completed.result(), ''
            except Exception as exc: value, error = None, str(exc)
            if not self._closed:
                try: self._writeFinished.emit(identity, value, error, message)
                except RuntimeError: pass
        future.add_done_callback(done)

    @pyqtSlot(str, object, str, str)
    def _on_write_finished(self, identity, value, error, message):
        self._write_pending = False
        if error:
            self._error = '保存失败，已保留原状态：' + error
            self._pending_edits.pop(identity, None)
            if identity in self._confirmed_records: self._records[identity] = copy.deepcopy(self._confirmed_records[identity])
            self._generation += 1; self._preview_url = ''; self._render_timer.start()
        else:
            self._confirmed_records[identity] = copy.deepcopy(value)
            if identity in self._pending_edits: value['edits'] = dict(self._pending_edits[identity])
            self._records[identity] = value; self._message = message
        self.refresh()
        self._save_pending_edits()

    @pyqtSlot(int)
    def setRating(self, value):
        if 0 <= value <= 5: self._update({'rating': value}, 'Rating saved')
    @pyqtSlot(bool)
    def setProtected(self, value): self._update({'protected': value}, 'Protection saved')
    @pyqtSlot()
    def moveToTrash(self):
        if self.protected:
            self._error = 'Protected photo: unlock it before moving to trash'; self.changed.emit(); return
        self._update({'trashed_at': int(time.time() * 1000)}, 'Moved to library trash. Original DNG retained; restore at any time.')
    @pyqtSlot()
    def restore(self): self._update({'trashed_at': None}, 'Photo restored')
    @pyqtSlot(str, float)
    def setEdit(self, name, value):
        bounds = {'exposure': (-5, 5), 'contrast': (.5, 2), 'saturation': (0, 2)}
        if name not in bounds or not math.isfinite(value) or not bounds[name][0] <= value <= bounds[name][1]: return
        if not self._selected or self._store is None: return
        edits = self.selectedEdits; edits[name] = round(value, 3)
        self._stage_edits(edits)

    def _stage_edits(self, edits):
        if not self.ready or self._closed: return
        if self._selected not in self._pending_edits and len(self._pending_edits) >= 8:
            self._error = '编辑保存队列已满，请稍候'; self.changed.emit(); return
        self._record['edits'] = edits
        self._records[self._selected] = self._record
        self._pending_edits[self._selected] = dict(edits)
        self._message = '正在保存预览编辑旁车；RAW 像素不变。'; self._error = ''
        self._generation += 1; self._render_timer.start(); self._edit_save_timer.start(); self.changed.emit()

    def _save_pending_edits(self):
        if self._write_pending or not self._pending_edits or self._closed: return
        identity = next(iter(self._pending_edits)); edits = self._pending_edits.pop(identity)
        self._start_write(identity, {'edits': edits}, '预览编辑已保存到旁车；RAW 像素不变。')

    @pyqtSlot()
    def rotate(self):
        edits = self.selectedEdits; edits['rotation'] = (edits['rotation'] + 90) % 360
        self._stage_edits(edits)

    @pyqtSlot(str)
    def setCrop(self, ratio):
        edits = self.selectedEdits
        if ratio == 'original': edits['crop'] = None
        else:
            ratios = {'1:1': 1, '3:2': 1.5, '4:3': 4/3}
            width = getattr(self._camera, 'reviewWidth', 0); height = getattr(self._camera, 'reviewHeight', 0)
            if ratio not in ratios or not width or not height: return
            current = width / height; target = ratios[ratio]
            w = min(1., target / current); h = min(1., current / target)
            edits['crop'] = [(1-w)/2, (1-h)/2, w, h]
        self._stage_edits(edits)

    @pyqtSlot(str)
    def applyLook(self, name):
        presets = {'Natural': (1.,1.), 'Mono': (1.05,0.), 'Punchy': (1.25,1.15), 'Soft': (.85,.85)}
        if name not in presets: return
        edits = self.selectedEdits
        edits['contrast'], edits['saturation'] = presets[name]
        self._stage_edits(edits)
    @pyqtSlot()
    def resetEdits(self):
        self._stage_edits(dict(DEFAULT_EDITS))
        self._preview_url = ''; self.changed.emit()

    @pyqtSlot(str)
    def exportSidecar(self, destination):
        if not self.ready or self._export_pending: return
        value = copy.deepcopy(self._record)
        def write():
            path = Path(destination)
            if not path.is_absolute() or path.suffix.lower() != '.json':
                raise ValueError('choose a new absolute .json filename')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            return {'kind':'sidecar','destination':str(path)}
        self._start_export(write)

    @pyqtSlot(str)
    def exportJpeg(self, destination):
        if self._export_pending: return
        identity = self._selected; edits = self.selectedEdits
        client = getattr(self._camera, '_client', None)
        if not identity or client is None:
            self._error = 'Select a saved photograph on a connected authority'; self.changed.emit(); return
        self._start_export(lambda: _pipeline().export_review_jpeg(client, identity, destination, edits))

    def _start_export(self, function):
        self._export_pending = True
        self._error = ''; self._message = '正在导出副本…'; self.changed.emit()
        future = self._render_executor.submit(function)
        def done(completed):
            try: result, error = completed.result(), ''
            except Exception as exc: result, error = None, str(exc)
            if not self._closed: self._exported.emit(result, error)
        future.add_done_callback(done)

    @pyqtSlot(object, str)
    def _on_exported(self, result, error):
        self._export_pending = False
        self._error = 'JPEG export failed: ' + error if error else ''
        if result:
            self._message = ('旁车已导出：' + result['destination'] if result.get('kind') == 'sidecar' else
                             'SDR 样张已保存 · ' + str(result['width']) + '×' + str(result['height']) + ' · ' + result['destination'])
        self.changed.emit()

    def _render(self):
        if self._closed or self._render_pending or not self._base_url: return
        edits = self.selectedEdits
        if edits == DEFAULT_EDITS:
            self._preview_url = ''; self.changed.emit(); return
        self._render_pending = True
        generation = self._generation; url = self._base_url
        future = self._render_executor.submit(render_adjustments, url, edits)
        def complete(done):
            try: result, error = done.result(), ''
            except Exception as exc: result, error = '', str(exc)
            if not self._closed: self._rendered.emit(generation, result, error)
        future.add_done_callback(complete)

    @pyqtSlot(int, str, str)
    def _on_rendered(self, generation, url, error):
        self._render_pending = False
        if generation == self._generation:
            self._preview_url = url
            if error: self._error = 'Preview adjustment: ' + error
            self.changed.emit()
        else: self._render_timer.start()

    def close(self):
        if self._closed: return
        self._closed = True; self._render_timer.stop(); self._edit_save_timer.stop()
        # At most eight latest edits plus one in-flight operation. Closing does
        # not wait on SD I/O in the GUI thread; the executor drains these writes.
        for identity, edits in self._pending_edits.items():
            self._writer_executor.submit(self._store.write, identity, {'edits': edits})
        self._pending_edits = {}
        self._reader_executor.shutdown(wait=True, cancel_futures=True)
        self._writer_executor.shutdown(wait=True, cancel_futures=False)
        self._render_executor.shutdown(wait=True, cancel_futures=False)

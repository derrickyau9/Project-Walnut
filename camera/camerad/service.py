"""HF-Sim control service. This is not a driver or a hardware camera daemon."""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid

from simulation.bayer import generate_bayer
from camera.dng.hf_dng import write_dng
from camera.pipeline.preview import PreviewStream
from camera.camerad.native_backend import NativeWorker, NativeBackendError
from camera.camerad.library import LibraryAuthorityMixin

MODES = {"sim-small": (128, 96), "sim-full": (6064, 4040)}
DEFAULTS = {"sensor_mode": "sim-small", "requested_exposure_us": 10000,
            "requested_analogue_gain": 1.0, "output_format": "dng", "simulation_scene": "scene"}
TERMINAL = {"complete", "failed", "cancelled", "unknown"}
SETTINGS_FORMAT = 'hf-device-settings-v1'
CALIBRATION_FORMAT = 'hf-active-calibration-v1'


class CameraError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _publish_capture_file(temporary, final):
    """Publish without replacing an entry that appeared after admission."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        move = kernel.MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(temporary), str(final), 0x00000008):  # MOVEFILE_WRITE_THROUGH, no replacement
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        # Same-filesystem hard link is an atomic create-only publication.
        os.link(temporary, final)
        os.unlink(temporary)
        directory = os.open(final.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _lock_directory(root):
    """Prevent two service instances from granting control from one journal."""
    try:
        f = open(root / '.service.lock', 'a+b')
    except OSError as exc:
        code = 'already_running' if getattr(exc, 'winerror', None) in (32, 33) else 'storage_error'
        raise CameraError(code, 'cannot open camera directory lock') from exc
    try:
        if os.name == 'nt':
            import msvcrt
            f.seek(0)
            if not f.read(1):
                f.write(b'0')
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        f.close()
        raise CameraError('already_running', 'another service owns this data directory') from exc
    return f


class CameraService(LibraryAuthorityMixin):
    """Single-process simulated service with a durable, queryable request journal.

    Restart marks unfinished captures unknown: a crash after file rename may have
    occurred before the completion record was committed. No automatic replay.
    """
    def __init__(self, data_dir, lease_seconds=30, *, backend='python', native_worker=None):
        if backend not in ('python', 'native'):
            raise ValueError('backend must be python or native')
        if backend == 'native' and native_worker is None:
            raise ValueError('native backend requires an explicit native worker executable')
        self.backend = backend
        self.native_worker = NativeWorker([str(native_worker)]) if backend == 'native' else None
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 3600:
            raise ValueError('lease_seconds must be greater than zero and no more than 3600')
        self.lease_seconds = float(lease_seconds)
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.photos = self.root / "photos"
        try:
            self.photos.mkdir(exist_ok=True)
            self._capture_directory_identity = self._check_capture_directories()
        except OSError as exc:
            raise CameraError('storage_error', 'capture directories are unavailable') from exc
        self._lock_file = _lock_directory(self.root)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.root / "journal.sqlite3"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS captures (request_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL, record_json TEXT NOT NULL, status TEXT NOT NULL);
        """)
        self.db.commit()
        authority = self.db.execute("SELECT value FROM meta WHERE key='authority'").fetchone()
        if authority and authority[0] != 'python':
            self.db.close()
            self._lock_file.close()
            raise CameraError('journal_authority_mismatch', 'journal belongs to another authority; use a separate data directory')
        self.control = None
        self.control_expires_at = None
        try:
            self.settings, migrate_settings = self._load_settings()
            self.calibration_binding, migrate_calibration = self._load_calibration()
        except BaseException:
            self.db.close()
            self._lock_file.close()
            raise
        self.active = None
        self.pipeline_generation = 0
        self.connected = True
        self.journal_fault = None
        self.uncertain = {}
        self.cancel = threading.Event()
        self.preview = PreviewStream()
        try:
            with self.lock:
                row = self.db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
                self.epoch = (int(row[0]) if row else 0) + 1
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('epoch', ?)", (str(self.epoch),))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('authority', 'python')")
                if migrate_settings:
                    self.db.execute("INSERT INTO meta VALUES ('settings', ?)", (self._settings_document(self.settings),))
                    self.db.execute("INSERT INTO meta VALUES ('settings_format', ?)", (SETTINGS_FORMAT,))
                if migrate_calibration:
                    self.db.execute("INSERT INTO meta VALUES ('calibration_binding', ?)", (self._calibration_document(None),))
                    self.db.execute("INSERT INTO meta VALUES ('calibration_binding_format', ?)", (CALIBRATION_FORMAT,))
                for stored in self.db.execute('SELECT request_id,request_json,record_json,status FROM captures'):
                    self._read_record(stored['record_json'],stored['request_id'],stored['status'],stored['request_json'])
                for row in self.db.execute("SELECT request_id,request_json,record_json,status FROM captures WHERE status IN ('accepted','capturing')").fetchall():
                    record = self._read_record(row['record_json'],row['request_id'],row['status'],row['request_json'])
                    record.update(status='unknown', error='service restarted before completion was confirmed')
                    self._save(record)
                self.db.commit()
        except BaseException:
            try: self.db.rollback()
            finally:
                self.db.close(); self._lock_file.close()
            raise
        self._init_library()

    @staticmethod
    def _settings_document(settings):
        return json.dumps({'format':SETTINGS_FORMAT,'settings':settings},sort_keys=True,separators=(',',':'),allow_nan=False)

    @staticmethod
    def _state_json(raw,limit):
        if not isinstance(raw,str) or len(raw.encode('utf-8'))>limit: raise ValueError('state exceeds bound')
        def unique(pairs):
            result={}
            for key,item in pairs:
                if key in result: raise ValueError('duplicate state field')
                result[key]=item
            return result
        def finite(raw):
            number=float(raw)
            if not math.isfinite(number): raise ValueError('nonfinite state')
            return number
        def constant(_): raise ValueError('nonfinite state')
        return json.loads(raw,object_pairs_hook=unique,parse_constant=constant,parse_float=finite)

    def _load_settings(self):
        """Only a genuinely absent legacy pair may acquire default settings."""
        try:
            value=self.db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
            marker=self.db.execute("SELECT value FROM meta WHERE key='settings_format'").fetchone()
            if value is None and marker is None: return dict(DEFAULTS),True
            if value is None or marker is None or marker[0]!=SETTINGS_FORMAT:
                raise ValueError('incomplete or unsupported device settings')
            document=self._state_json(value[0],16384)
            if (not isinstance(document,dict) or set(document)!={'format','settings'} or document['format']!=SETTINGS_FORMAT
                    or not isinstance(document['settings'],dict) or set(document['settings'])!=set(DEFAULTS)):
                raise ValueError('unsupported device settings envelope')
            return self._validate(document['settings']),False
        except (sqlite3.Error,CameraError,ValueError,TypeError,OverflowError,RecursionError,UnicodeError) as exc:
            raise CameraError('journal_error','persisted device settings are invalid; preserved for inspection') from exc

    @staticmethod
    def _calibration_document(binding):
        return json.dumps({'format':CALIBRATION_FORMAT,'binding':binding},sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)

    def _load_calibration(self):
        from camera.calibration import validate_binding,CalibrationError
        from camera.calibration.binding import MAX_BINDING_BYTES
        try:
            value=self.db.execute("SELECT value FROM meta WHERE key='calibration_binding'").fetchone()
            marker=self.db.execute("SELECT value FROM meta WHERE key='calibration_binding_format'").fetchone()
            if value is None and marker is None: return None,True
            if value is None or marker is None or marker[0]!=CALIBRATION_FORMAT:
                raise ValueError('incomplete active calibration state')
            document=self._state_json(value[0],MAX_BINDING_BYTES+128)
            if not isinstance(document,dict) or set(document)!={'format','binding'} or document['format']!=CALIBRATION_FORMAT:
                raise ValueError('unsupported active calibration state')
            binding=document['binding']
            return (validate_binding(binding) if binding is not None else None),False
        except (sqlite3.Error,CalibrationError,ValueError,TypeError,OverflowError,RecursionError,UnicodeError) as exc:
            raise CameraError('journal_error','persisted calibration binding is invalid; preserved for inspection') from exc

    @staticmethod
    def _calibration_state(binding):
        return {'sensor_id':'HF-Sim','binding_id':binding['binding_id'] if binding else None,
                'calibration_id':binding['binding_id'] if binding else 'synthetic-uncalibrated',
                'package_checksum':binding['package_checksum'] if binding else None,
                'hardware_calibrated':False,'pixel_corrections_applied':False}

    def _record_binding(self,record):
        from camera.camerad.calibration_context import validate_capture_calibration_context
        try:
            return validate_capture_calibration_context(record)
        except (ValueError,TypeError) as exc:
            self.journal_fault='bound capture calibration snapshot is invalid'
            raise CameraError('journal_error','bound capture calibration snapshot is invalid; no replay or fallback') from exc

    def _read_record(self,raw,request_id=None,row_status=None,request_payload=None):
        try:
            value=self._state_json(raw,1024*1024)
            if not isinstance(value,dict): raise ValueError('capture must be an object')
            rid=value.get('request_id')
            if (not isinstance(rid,str) or str(uuid.UUID(rid))!=rid or value.get('capture_id')!=rid
                    or request_id is not None and rid!=request_id or value.get('sensor_id')!='HF-Sim'
                    or value.get('status') not in TERMINAL|{'accepted','capturing'}
                    or row_status is not None and value.get('status')!=row_status):
                raise ValueError('capture identity/status differs from journal context')
            settings=self._validate(value)
            if 'capture_record_version' not in value and value.get('calibration_id')!='synthetic-uncalibrated':
                raise ValueError('legacy simulator capture has an unsupported calibration identity')
            if value.get('backend','python') not in {'python','native'}: raise ValueError('unknown capture backend')
            if value.get('fault') not in (None,'delay','storage_full','disconnect','corrupt_frame'):
                raise ValueError('invalid stored fault')
            deadline=value.get('deadline_unix_ms')
            if deadline is not None and (type(deadline) is not int or deadline<0): raise ValueError('invalid stored deadline')
            if request_payload is not None:
                payload=self._state_json(request_payload,65536)
                if (not isinstance(payload,dict) or set(payload)-set(DEFAULTS)-{'fault','deadline_unix_ms'}
                        or self._validate(payload)!=settings or payload.get('fault')!=value.get('fault')
                        or payload.get('deadline_unix_ms')!=deadline):
                    raise ValueError('capture differs from its immutable request')
            outcomes={'actual_exposure_us','actual_analogue_gain','frame_id','timestamp','file_path','error'}
            if not outcomes.issubset(value): raise ValueError('capture outcome fields are missing')
            exposure=value['actual_exposure_us'];gain=value['actual_analogue_gain'];stamp=value['timestamp'];path=value['file_path']
            if exposure is not None and (type(exposure) is not int or exposure!=settings['requested_exposure_us']):
                raise ValueError('simulated actual exposure does not match request')
            if gain is not None and (type(gain) not in (int,float) or not math.isfinite(gain) or gain!=settings['requested_analogue_gain']):
                raise ValueError('simulated actual gain does not match request')
            if value['frame_id'] is not None and value['frame_id']!=rid: raise ValueError('frame identity differs')
            if stamp is not None and (type(stamp) not in (int,float) or not 0<stamp<=1e20 or not math.isfinite(stamp)):
                raise ValueError('capture timestamp is invalid')
            if path is not None and (not isinstance(path,str) or not path or len(path)>32768 or '\0' in path):
                raise ValueError('capture file metadata is invalid')
            if value['error'] is not None and not isinstance(value['error'],str): raise ValueError('capture error is invalid')
            if value['status']=='complete' and (any(value[key] is None for key in outcomes-{'error'}) or value['error'] is not None):
                raise ValueError('capture completion is missing consistent outcome metadata')
            return self._record_binding(value)
        except (CameraError,ValueError,TypeError,OverflowError,RecursionError,UnicodeError) as exc:
            self.journal_fault='capture journal JSON is invalid'
            raise CameraError('journal_error','capture journal JSON is invalid; preserved for inspection') from exc

    def _select_calibration(self,token,epoch,binding,*,clear=False):
        from camera.calibration import validate_binding,CalibrationError
        with self.lock:
            self._authorize(token,epoch)
            if binding is None and not clear: raise CameraError('invalid_binding','binding must be an artifact; clear calibration explicitly')
            if not self.connected: raise CameraError('device_disconnected','reconnect the simulated device explicitly')
            if self.active: raise CameraError('camera_busy','capture in progress')
            try: verified=validate_binding(binding) if binding is not None else None
            except CalibrationError as exc: raise CameraError(exc.code,str(exc)) from exc
            try:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('calibration_binding', ?)",(self._calibration_document(verified),))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('calibration_binding_format', ?)",(CALIBRATION_FORMAT,))
                self.db.commit()
            except sqlite3.Error as exc:
                self.journal_fault=str(exc)
                try: self.db.rollback()
                except sqlite3.Error: pass
                raise CameraError('journal_error','calibration selection commit failed; restart and inspect persisted state') from exc
            self.calibration_binding=verified
            return self._calibration_state(verified)

    def bind_calibration(self,token,epoch,binding):
        return self._select_calibration(token,epoch,binding)

    def clear_calibration(self,token,epoch):
        return self._select_calibration(token,epoch,None,clear=True)

    def _check_capture_directories(self):
        """Reject redirected/replaced capture directories at I/O boundaries.

        This detects observed namespace changes; it is not an atomic sandbox
        against a hostile process racing the same user's directory operations.
        """
        try:
            identities=[]
            for path in (self.root, self.photos):
                info=path.lstat()
                if (not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
                        or path.resolve(strict=True)!=path):
                    raise OSError('capture directory was redirected')
                identities.append((info.st_dev,info.st_ino))
            identity=tuple(identities)
            if hasattr(self,'_capture_directory_identity') and identity!=self._capture_directory_identity:
                raise OSError('capture directory was replaced')
            return identity
        except (OSError,RuntimeError) as exc:
            raise CameraError('storage_error','capture directory identity changed or is unavailable') from exc

    def _check_capture_staging(self, staging, identity, temporary=None, snapshot=None):
        self._check_capture_directories()
        try:
            info=staging.lstat()
            if (not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
                    or (info.st_dev,info.st_ino)!=identity or staging.resolve(strict=True)!=staging):
                raise OSError('capture staging directory changed')
            if temporary is not None:
                info=temporary.lstat()
                current=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
                if (not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
                        or temporary.resolve(strict=True)!=temporary or snapshot is not None and current!=snapshot):
                    raise OSError('produced capture file changed')
                return current
        except (OSError,RuntimeError) as exc:
            raise CameraError('storage_error','capture staging identity changed or is unavailable') from exc

    @staticmethod
    def _capture_digest(stream, maximum):
        digest=hashlib.sha256(); count=0; stream.seek(0)
        while True:
            block=stream.read(min(65536,maximum+1-count))
            if not block: break
            count+=len(block)
            if count>maximum: raise CameraError('invalid_frame','capture file exceeds its mode bound')
            digest.update(block)
        stream.seek(0)
        return digest.hexdigest()

    @staticmethod
    def capabilities():
        return {"api_version": "1.1", "device": "HF-Sim", "simulated": True,
                "capture_record_versions": [1,2], "settings_persistent": True,
                "device_settings":{"persistent":True,"format":SETTINGS_FORMAT,"capture_changes_baseline":False},
                "calibration_binding": {"supported":True,"binding_schema_version":1,"max_request_bytes":65536,
                                        "hardware_calibrated":False,"pixel_corrections_applied":False},
                "modes": [{"id": k, "width": v[0], "height": v[1], "bit_depth": 14, "cfa": "RGGB"} for k, v in MODES.items()],
                "exposure_us": {"min": 100, "max": 30000000}, "analogue_gain": {"min": 1.0, "max": 16.0},
                "output_formats": ["dng"], "simulation_scenes": ["scene", "dark", "flat"],
                "faults": ["delay", "storage_full", "disconnect", "corrupt_frame"],
                "retained_captures": {"listing": True, "max_page_size": 50, "asset_metadata": True,
                                      "binary_export": True, "thumbnail": True,
                                      "thumbnail_source": "retained-dng", "thumbnail_calibrated": False}}

    def _save(self, record):
        self.db.execute("UPDATE captures SET status=?, record_json=? WHERE request_id=?",
                        (record['status'], json.dumps(record, sort_keys=True), record['request_id']))
        if getattr(self, '_library_ready', False): self._library_capture(record)

    def _expire_control(self):
        if self.control and time.monotonic() >= self.control_expires_at:
            self.control = None
            self.control_expires_at = None

    def _authorize(self, token, epoch):
        if self.journal_fault:
            raise CameraError('journal_error', 'journal unavailable; restart and reconcile pending captures')
        self._expire_control()
        if not self.control or not isinstance(token, str) or not secrets.compare_digest(token, self.control['token']) or epoch != self.control['epoch']:
            raise CameraError('control_denied', 'control token or epoch is invalid')

    def acquire(self, owner):
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
            raise CameraError('invalid_request', 'owner must be a nonempty string up to 128 characters')
        with self.lock:
            if self.journal_fault:
                raise CameraError('journal_error', 'journal unavailable; restart and reconcile pending captures')
            self._expire_control()
            if self.control:
                raise CameraError('control_busy', 'control is already held')
            next_epoch = self.epoch + 1
            try:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('epoch', ?)", (str(next_epoch),))
                self.db.commit()
            except sqlite3.Error as exc:
                # A failed commit may have reached storage; fence the entire service
                # until restart reloads the authoritative persisted epoch.
                self.journal_fault = str(exc)
                try:
                    self.db.rollback()
                except sqlite3.Error:
                    pass
                raise CameraError('journal_error', 'could not persist control epoch; restart required') from exc
            self.epoch = next_epoch
            self.control = {'owner': owner, 'epoch': self.epoch, 'token': secrets.token_urlsafe(32)}
            self.control_expires_at = time.monotonic() + self.lease_seconds
            return {**self.control, 'lease_seconds': self.lease_seconds}

    def heartbeat(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            self.control_expires_at = time.monotonic() + self.lease_seconds
            return {'owner': self.control['owner'], 'epoch': self.control['epoch'],
                    'lease_seconds': self.lease_seconds}

    def release(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            self.control = None
            self.control_expires_at = None
            return {'released': True}

    @staticmethod
    def _validate(settings):
        mode = settings.get('sensor_mode')
        exposure = settings.get('requested_exposure_us')
        gain = settings.get('requested_analogue_gain')
        if not isinstance(mode,str) or mode not in MODES or settings.get('output_format') != 'dng':
            raise CameraError('invalid_request', 'unsupported sensor mode or output format')
        if isinstance(exposure, bool) or not isinstance(exposure, int) or not 100 <= exposure <= 30000000:
            raise CameraError('invalid_request', 'exposure must be an integer from 100 to 30000000 us')
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not 1 <= gain <= 16:
            raise CameraError('invalid_request', 'gain must be between 1 and 16')
        scene = settings.get('simulation_scene', 'scene')
        if scene not in ('scene', 'dark', 'flat'):
            raise CameraError('invalid_request', 'unsupported simulated scene')
        return {**{k: settings[k] for k in DEFAULTS if k != 'simulation_scene'}, 'simulation_scene': scene}

    def configure(self, token, epoch, settings):
        with self.lock:
            self._authorize(token, epoch)
            if not self.connected:
                raise CameraError('device_disconnected', 'simulated device is disconnected; reconnect explicitly')
            if self.active:
                raise CameraError('camera_busy', 'capture in progress')
            if not isinstance(settings, dict) or any(k not in DEFAULTS for k in settings):
                raise CameraError('invalid_request', 'settings contains unsupported fields')
            merged = dict(self.settings)
            merged.update(settings)
            validated=self._validate(merged)
            try:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('settings', ?)", (self._settings_document(validated),))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('settings_format', ?)", (SETTINGS_FORMAT,))
                self.db.commit()
            except sqlite3.Error as exc:
                self.journal_fault=str(exc)
                try: self.db.rollback()
                except sqlite3.Error: pass
                raise CameraError('journal_error','device settings commit failed; restart and inspect persisted settings') from exc
            self.settings = validated
            return dict(self.settings)

    def get_state(self):
        with self.lock:
            self._expire_control()
            row = self.db.execute('SELECT request_id,request_json,record_json,status FROM captures ORDER BY rowid DESC LIMIT 1').fetchone()
            return {'connected': self.connected, 'simulated': True,
                    'backend': self.backend, 'authority': 'python',
                    'camera_state': 'capturing' if self.active else 'idle',
                    'journal_healthy': self.journal_fault is None,
                    'control': ({'owner': self.control['owner'], 'epoch': self.control['epoch']} if self.control else None),
                    'settings': dict(self.settings), 'settings_persistent':True,'settings_format':SETTINGS_FORMAT,
                    'calibration':self._calibration_state(self.calibration_binding),
                    'last_capture': self._read_record(row['record_json'],row['request_id'],row['status'],row['request_json']) if row else None,
                    'preview': self.preview.state(), 'pipeline_generation': self.pipeline_generation}

    def start_preview(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            if not self.connected:
                raise CameraError('device_disconnected', 'simulated device is disconnected; reconnect explicitly')
            return self.preview.start()

    def stop_preview(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            return self.preview.stop()

    def reconnect(self, token, epoch):
        with self.lock:
            self._authorize(token, epoch)
            if self.active:
                raise CameraError('camera_busy', 'wait for the active capture to finish')
            self.connected = True
            self.pipeline_generation += 1
            return {'connected': True, 'simulated': True}

    def query_capture(self, request_id):
        with self.lock:
            if request_id in self.uncertain:
                return self._read_record(json.dumps(self.uncertain[request_id],allow_nan=False),request_id)
            row = self.db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?', (request_id,)).fetchone()
            return self._read_record(row['record_json'],request_id,row['status'],row['request_json']) if row else None

    def list_captures(self, limit=20, cursor=None):
        from camera.camerad.retained import canonical_id, dimensions_for_record, summary
        if type(limit) is not int or not 1 <= limit <= 50:
            raise CameraError('invalid_request', 'limit must be an integer from 1 to 50')
        if cursor is not None:
            cursor = canonical_id(cursor)
        with self.lock:
            before = None
            if cursor is not None:
                row = self.db.execute("SELECT rowid FROM captures WHERE request_id=? AND status='complete'", (cursor,)).fetchone()
                if row is None:
                    raise CameraError('invalid_request', 'cursor must name a completed capture')
                before = row[0]
            rows = self.db.execute(
                "SELECT request_id,request_json,record_json,status FROM captures WHERE status='complete'" +
                (" AND rowid<?" if before is not None else "") + " ORDER BY rowid DESC LIMIT ?",
                (before, limit + 1) if before is not None else (limit + 1,)).fetchall()
            items = []
            for row in rows[:limit]:
                record = self._read_record(row['record_json'],row['request_id'],row['status'],row['request_json'])
                if not isinstance(record, dict) or record.get('request_id') != row[0]:
                    raise CameraError('capture_unavailable', 'retained capture identity does not match the journal key')
                items.append(summary(record, *dimensions_for_record(record)))
            return {'items': items, 'next_cursor': items[-1]['request_id'] if len(rows) > limit else None,
                    'limit': limit, 'order': 'newest_first'}

    def _retained_record(self, request_id):
        from camera.camerad.retained import canonical_id
        request_id = canonical_id(request_id)
        record = self.query_capture(request_id)
        if record is not None and (not isinstance(record, dict) or record.get('request_id') != request_id):
            raise CameraError('capture_unavailable', 'retained capture identity does not match the journal key')
        return record

    def get_capture_asset(self, request_id):
        from camera.camerad.retained import asset_metadata
        return asset_metadata(self.root, self._retained_record(request_id))

    def open_capture(self, request_id):
        self.get_capture_asset(request_id)
        return self.open_library_asset(request_id)

    def capture_thumbnail(self, request_id):
        from camera.camerad.retained import thumbnail_retained
        return thumbnail_retained(self.root, self._retained_record(request_id))

    def export_capture(self, request_id, destination):
        from camera.camerad.retained import export_retained
        return export_retained(self.root, self._retained_record(request_id), destination)

    def capture(self, request):
        if not isinstance(request, dict):
            raise CameraError('invalid_request', 'request must be an object')
        with self.lock:
            allowed = set(DEFAULTS) | {'request_id', 'token', 'control_epoch', 'fault', 'deadline_unix_ms'}
            if any(k not in allowed for k in request):
                raise CameraError('invalid_request', 'capture contains unsupported fields')
            self._authorize(request.get('token'), request.get('control_epoch'))
            rid = request.get('request_id')
            try:
                if not isinstance(rid, str) or str(uuid.UUID(rid)) != rid:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise CameraError('invalid_request', 'request_id must be a lowercase canonical UUID')
            data = self._validate(request)
            fault = request.get('fault')
            if fault not in (None, 'delay', 'storage_full', 'disconnect', 'corrupt_frame'):
                raise CameraError('invalid_request', 'unknown fault')
            deadline = request.get('deadline_unix_ms')
            if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, int)):
                raise CameraError('invalid_request', 'deadline_unix_ms must be an integer')
            data.update(fault=fault, deadline_unix_ms=deadline)
            payload = json.dumps(data, sort_keys=True, separators=(',', ':'))
            row = self.db.execute('SELECT request_json,record_json,status FROM captures WHERE request_id=?', (rid,)).fetchone()
            if row:
                if row['request_json'] != payload:
                    raise CameraError('request_conflict', 'request_id already exists with different parameters')
                return self._read_record(row['record_json'],rid,row['status'],row['request_json'])
            if not self.connected:
                raise CameraError('device_disconnected', 'simulated device is disconnected; reconnect explicitly')
            if self.active:
                raise CameraError('camera_busy', 'capture in progress')
            self._check_capture_directories()
            if (self.photos / (rid + '.dng')).exists() or (self.photos / (rid + '.dng.partial')).exists():
                raise CameraError('request_conflict', 'output for this request already exists; inspect it before proceeding')
            if deadline is not None and deadline <= int(time.time() * 1000):
                raise CameraError('deadline_expired', 'deadline already expired')
            record = {'request_id': rid, 'capture_id': rid, 'status': 'accepted', **data,
                      'backend': self.backend,
                      'actual_exposure_us': None, 'actual_analogue_gain': None, 'frame_id': None,
                      'timestamp': None, 'sensor_id': 'HF-Sim', 'calibration_id': 'synthetic-uncalibrated',
                      'file_path': None, 'error': None}
            if self.calibration_binding is not None:
                from camera.calibration import validate_binding,CalibrationError
                try: snapshot=validate_binding(self.calibration_binding)
                except CalibrationError as exc:
                    self.journal_fault='active calibration snapshot is invalid'
                    raise CameraError('journal_error','active calibration snapshot is invalid; no capture or fallback') from exc
                record.update(capture_record_version=2,calibration_binding=snapshot,calibration_id=snapshot['binding_id'],
                              hardware_calibrated=False,pixel_corrections_applied=False)
            try:
                self.db.execute('INSERT INTO captures VALUES (?,?,?,?)', (rid, payload, json.dumps(record), 'accepted'))
                self.db.commit()  # Must precede initiating capture.
            except sqlite3.Error as exc:
                # Never initiate work after an uncertain journal commit. Restart
                # reloads any record that might have reached stable storage.
                self.journal_fault = str(exc)
                try:
                    self.db.rollback()
                except sqlite3.Error:
                    pass
                raise CameraError('journal_error', 'capture journal write failed; restart and query this request ID') from exc
            self.active = rid
            self.pipeline_generation += 1
            self.cancel = threading.Event()
            worker = threading.Thread(target=self._run, args=(rid, dict(record)), daemon=True)
            try:
                worker.start()
            except Exception as exc:
                if worker.is_alive():
                    # A worker that did start is authoritative; do not schedule another.
                    return record
                record.update(status='failed', error='capture worker could not start: ' + str(exc))
                try:
                    self._save(record)
                    self.db.commit()
                except sqlite3.Error as journal_exc:
                    try:
                        self.db.rollback()
                    except sqlite3.Error:
                        pass
                    record.update(status='unknown', error='journal failure; restart and reconcile')
                    self.uncertain[rid] = record
                    self.journal_fault = str(journal_exc)
                finally:
                    self.active = None
                    self.pipeline_generation += 1
                raise CameraError('worker_start_failed', record['error']) from exc
            return record

    def cancel_capture(self, token, epoch, request_id):
        with self.lock:
            self._authorize(token, epoch)
            record = self.query_capture(request_id)
            if not record:
                raise CameraError('not_found', 'capture request not found')
            if self.active == request_id and record['status'] not in TERMINAL:
                self.cancel.set()
                return {'requested': True, 'request_id': request_id}
            return {'requested': False, 'request_id': request_id, 'status': record['status']}

    def _run(self, rid, record):
        staging = None
        staging_identity = None
        temp = None
        publication_attempted = False
        final = self.photos / (rid + '.dng')
        try:
            with self.lock:
                record = self.query_capture(rid)
                record['status'] = 'capturing'
                self._save(record)
                self.db.commit()
            # Delay is an injected simulated readout delay, not sensor timing.
            if record['fault'] == 'delay' and self.cancel.wait(1.0):
                raise CameraError('cancelled', 'capture cancelled')
            if self.cancel.is_set():
                raise CameraError('cancelled', 'capture cancelled')
            if record['deadline_unix_ms'] is not None and int(time.time() * 1000) > record['deadline_unix_ms']:
                raise CameraError('deadline_expired', 'capture deadline expired')
            if record['fault'] == 'disconnect':
                with self.lock:
                    self.connected = False
                    self.pipeline_generation += 1
                raise CameraError('disconnect', 'injected simulated device disconnect')
            if record['fault'] in ('storage_full', 'corrupt_frame'):
                raise CameraError(record['fault'], 'injected ' + record['fault'])
            width, height = MODES[record['sensor_mode']]
            self._check_capture_directories()
            # This request owns a newly created private staging directory. A
            # conflicting public .partial path is never truncated or removed.
            staging = Path(tempfile.mkdtemp(prefix='.capture-' + rid + '-', dir=self.photos))
            staging_info = staging.lstat()
            staging_identity = (staging_info.st_dev, staging_info.st_ino)
            temp = staging / 'frame.dng.partial'
            evidence = None
            produced_digest = None
            if self.native_worker:
                self._check_capture_staging(staging,staging_identity)
                try:
                    evidence = self.native_worker.capture(temp, width, height, record, self.cancel)
                except NativeBackendError as exc:
                    raise CameraError(exc.code, str(exc)) from exc
                produced = self._check_capture_staging(staging,staging_identity,temp)
            else:
                pixels = generate_bayer(width, height, record['requested_exposure_us'], record['requested_analogue_gain'], scene=record['simulation_scene'])
                self._check_capture_staging(staging,staging_identity)
                with temp.open('x+b') as output:
                    write_dng(output, width, height, pixels, metadata={
                        'requested_exposure_us': record['requested_exposure_us'],
                        'actual_exposure_us': record['requested_exposure_us'],
                        'requested_analogue_gain': record['requested_analogue_gain'],
                        'actual_analogue_gain': record['requested_analogue_gain'],
                        'request_id': rid, 'capture_id': rid, 'frame_id': rid,
                        'sensor_id': record['sensor_id'], 'calibration_id': record['calibration_id'],
                    })
                    output.flush()
                    info=os.fstat(output.fileno())
                    produced=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
                    produced_digest=self._capture_digest(output,width*height*2+65536)
            self._check_capture_staging(staging,staging_identity,temp,produced)
            with open(temp, 'r+b') as f:
                info=os.fstat(f.fileno())
                if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=produced:
                    raise CameraError('storage_error','capture file changed while opening')
                from camera.dng.inspect import inspect_dng
                structure=inspect_dng(f)
                if (structure['width'],structure['height'],structure['bits_per_sample'],structure['cfa'],
                        structure['request_id'],structure['metadata_state'])!=(width,height,16,'RGGB',rid,'valid'):
                    raise CameraError('invalid_frame','produced DNG does not match the accepted capture')
                digest=self._capture_digest(f,width*height*2+65536)
                if produced_digest is not None and digest!=produced_digest:
                    raise CameraError('invalid_frame','produced capture bytes changed')
                produced_digest=digest
                os.fsync(f.fileno())
            with self.lock:
                if self.cancel.is_set():
                    raise CameraError('cancelled', 'capture cancelled before commit')
                if record['deadline_unix_ms'] is not None and int(time.time() * 1000) > record['deadline_unix_ms']:
                    raise CameraError('deadline_expired', 'capture deadline expired before commit')
                self._check_capture_staging(staging,staging_identity,temp,produced)
                publication_attempted = True
                _publish_capture_file(temp, final)
                self._check_capture_staging(staging,staging_identity,final,produced)
                with final.open('rb') as output:
                    info=os.fstat(output.fileno())
                    if ((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=produced
                            or self._capture_digest(output,width*height*2+65536)!=produced_digest):
                        raise CameraError('invalid_frame','published capture identity or bytes changed')
                record.update(status='complete', actual_exposure_us=evidence['actual_exposure_us'] if evidence else record['requested_exposure_us'],
                              actual_analogue_gain=evidence['actual_analogue_gain'] if evidence else record['requested_analogue_gain'],
                              frame_id=evidence['frame_id'] if evidence else rid,
                              timestamp=time.time(), file_path=str(final),
                              backend=self.backend)
                if evidence:
                    record['native_evidence'] = evidence
                self._save(record)
                self.db.commit()
        except Exception as exc:
            with self.lock:
                try:
                    # Discard any uncommitted completion or phase update first.
                    self.db.rollback()
                    record = self.query_capture(rid)
                    # A failure after rename cannot establish whether a file was committed.
                    status = 'unknown' if publication_attempted else ('cancelled' if isinstance(exc, CameraError) and exc.code == 'cancelled' else 'failed')
                    record.update(status=status, error=str(exc))
                    self._save(record)
                    self.db.commit()
                except sqlite3.Error as journal_exc:
                    try:
                        self.db.rollback()
                    except sqlite3.Error:
                        pass
                    record.update(status='unknown', error='journal failure; restart and reconcile')
                    self.uncertain[rid] = record
                    self.journal_fault = str(journal_exc)
        finally:
            try:
                if staging is not None:
                    # A replacement namespace is foreign. Preserve both its
                    # contents and any orphaned original staging for inspection.
                    try:
                        self._check_capture_directories()
                        info=staging.lstat()
                        if (stat.S_ISDIR(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400
                                and (info.st_dev,info.st_ino)==staging_identity):
                            shutil.rmtree(staging)
                    except (CameraError,OSError):
                        pass
            finally:
                with self.lock:
                    self.active = None
                    self.pipeline_generation += 1

    def close(self):
        with self.lock:
            if self.active:
                raise CameraError('camera_busy', 'wait for capture completion before closing')
            self.db.close()
            self._lock_file.close()

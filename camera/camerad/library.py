"""Versioned, authority-owned media assets; imports never create exposures.

The library records asset lifecycle separately from immutable capture outcomes.
Only an authority performs permanent removal, after a durable purge intent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_assets(
 photo_uuid TEXT PRIMARY KEY,raw_uuid TEXT NOT NULL,origin TEXT NOT NULL,
 metadata TEXT NOT NULL,relative_path TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'active',
 protected INTEGER NOT NULL DEFAULT 0,trashed INTEGER NOT NULL DEFAULT 0,purge_operation TEXT);
CREATE TABLE IF NOT EXISTS library_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,photo_uuid TEXT NOT NULL,kind TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS library_change_photo ON library_changes(photo_uuid,seq);
CREATE TRIGGER IF NOT EXISTS library_asset_insert AFTER INSERT ON library_assets BEGIN
 INSERT INTO library_changes(photo_uuid,kind) VALUES(new.photo_uuid,'upsert'); END;
CREATE TRIGGER IF NOT EXISTS library_asset_update AFTER UPDATE ON library_assets BEGIN
 INSERT INTO library_changes(photo_uuid,kind) VALUES(new.photo_uuid,new.state); END;
"""
MAX_IMPORT_BYTES = 512 * 1024 * 1024
PURGE_CONFIRMATION = 'PERMANENTLY_DELETE'


def error(code, message):
    from .service import CameraError
    return CameraError(code, message)


def identity(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise error('invalid_request', 'canonical media UUID required')
    return value


def canonical_asset_path(root, descriptor):
    root = Path(root)
    origin = descriptor['origin_kind']
    directory = root / ('photos' if origin == 'capture' else 'library-imports')
    extension = descriptor.get('extension', '.dng')
    if extension not in {'.dng','.jpg','.png','.tif'}: raise error('capture_unavailable', 'unsupported media extension')
    path = directory / (identity(descriptor['photo_uuid']) + extension)
    if directory.is_symlink() or not directory.is_dir() or path.is_symlink():
        raise error('capture_unavailable', 'media path is redirected')
    if not stat.S_ISREG(path.stat().st_mode) or path.resolve(strict=True) != path:
        raise error('capture_unavailable', 'media is not a canonical regular file')
    return path


class PinnedStream:
    def __init__(self, stream, client, pin, descriptor):
        self._stream, self._client, self._pin = stream, client, pin
        self.library_asset_uuid = descriptor['photo_uuid']
        self.library_original_request_uuid = descriptor['original_request_uuid']
        self.library_descriptor = descriptor
        self.retained_structure = descriptor.get('structure', {})
        info = os.fstat(stream.fileno())
        self.retained_snapshot = (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    def __getattr__(self, name): return getattr(self._stream, name)
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self):
        if self._stream.closed: return
        try: self._stream.close()
        finally:
            try: self._client.library_rpc('unpin', {'pin':self._pin})
            except Exception: pass


class LibraryClientMixin:
    def describe_library(self): return self.library_rpc('info', {})
    def list_library_assets(self, limit=50, after=0, upper=None):
        return self.library_rpc('list', {'limit':limit,'after':after,'upper':upper})
    def list_library_changes(self, after=0, limit=50):
        return self.library_rpc('changes', {'limit':limit,'after':after})
    def get_library_asset(self, photo_uuid): return self.library_rpc('get', {'photo_uuid':identity(photo_uuid)})
    def set_library_asset_flags(self, photo_uuid, *, protected, trashed, expected_change_seq=None):
        return self.library_rpc('flags', {'photo_uuid':identity(photo_uuid),'protected':bool(protected),'trashed':bool(trashed),'expected_change_seq':expected_change_seq})
    def purge_library_asset(self, photo_uuid, operation_id, confirmation):
        return self.library_rpc('purge', {'photo_uuid':identity(photo_uuid),'operation_id':identity(operation_id),'confirmation':confirmation})
    def open_library_asset(self, photo_uuid):
        descriptor = self.get_library_asset(photo_uuid)
        if descriptor['state'] != 'active': raise error('capture_unavailable', 'asset is not available')
        pin = self.library_rpc('pin', {'photo_uuid':photo_uuid})['pin']
        stream = None
        try:
            path = canonical_asset_path(self.root, descriptor)
            if os.name == 'nt':
                from .retained import _open_windows
                stream = _open_windows(path)
            else:
                stream = os.fdopen(os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0)), 'rb')
            initial = os.fstat(stream.fileno())
            if initial.st_size != descriptor['byte_count']: raise error('capture_unavailable','asset size changed')
            if descriptor['media_type'] == 'image/x-adobe-dng':
                from camera.dng.inspect import inspect_dng
                structure = inspect_dng(stream)
                if ((structure['width'],structure['height']) != (descriptor['width'],descriptor['height'])
                        or structure.get('request_id') not in (None,descriptor['original_request_uuid'])):
                    raise error('capture_unavailable','asset structure changed')
                descriptor = dict(descriptor,structure=structure)
            stream.seek(0)
            return PinnedStream(stream,self,pin,descriptor)
        except BaseException:
            if stream: stream.close()
            self.library_rpc('unpin',{'pin':pin})
            raise
    def import_library_asset(self, source, photo_uuid=None, *, progress=None, cancel_event=None):
        photo_uuid = identity(photo_uuid or str(uuid.uuid4()))
        try:
            existing = self.get_library_asset(photo_uuid)
            if existing['origin_kind'] == 'import': return existing
            raise error('request_conflict','import UUID already belongs to a capture')
        except Exception as exc:
            if getattr(exc,'code',None) not in {'not_found','capture_unavailable'}: raise
        descriptor = stage_import(self.root, source, photo_uuid, progress=progress, cancel_event=cancel_event)
        return self.library_rpc('register_import', {'descriptor':descriptor})


def stage_import(root, source, photo_uuid, *, progress=None, cancel_event=None):
    """Copy a supported file to a new owned asset, without changing its bytes."""
    from camera.dng.inspect import inspect_dng
    photo_uuid = identity(photo_uuid)
    source = Path(source)
    if not source.is_absolute() or source.is_symlink() or not source.is_file(): raise error('invalid_request','choose an ordinary absolute source file')
    before = source.stat()
    if not 8 <= before.st_size <= MAX_IMPORT_BYTES: raise error('invalid_request','import exceeds 512 MiB')
    with source.open('rb') as stream:
        prefix = stream.read(16); stream.seek(0)
        structure = {};orientation=0
        if prefix[:2] in (b'II',b'MM'):
            try:
                structure = inspect_dng(stream)
                media_type, extension = 'image/x-adobe-dng','.dng'
                width,height = structure['width'],structure['height']
            except ValueError:
                from PyQt5.QtGui import QImageReader
                reader = QImageReader(str(source)); size=reader.size()
                if not size.isValid(): raise error('invalid_request','unsupported TIFF/DNG')
                width,height=size.width(),size.height(); media_type,extension='image/tiff','.tif'
                orientation=int(reader.transformation())
        else:
            from PyQt5.QtGui import QImageReader
            reader=QImageReader(str(source)); size=reader.size()
            kind=bytes(reader.format()).lower()
            mapping={b'jpeg':('image/jpeg','.jpg'),b'jpg':('image/jpeg','.jpg'),b'png':('image/png','.png')}
            if not size.isValid() or kind not in mapping: raise error('invalid_request','supported imports: DNG, JPEG, PNG, TIFF')
            width,height=size.width(),size.height(); media_type,extension=mapping[kind]
            orientation=int(reader.transformation())
        if not 1 <= width*height <= 64000000: raise error('invalid_request','image exceeds 64 megapixels')
        directory=Path(root)/'library-imports'; directory.mkdir(exist_ok=True)
        if directory.is_symlink(): raise error('storage_error','import directory is redirected')
        final=directory/(photo_uuid+extension)
        temporary=None; digest=hashlib.sha256(); total=0
        try:
            stream.seek(0)
            with tempfile.NamedTemporaryFile(dir=directory,prefix='.'+photo_uuid+'-',suffix='.partial',delete=False) as output:
                temporary=Path(output.name)
                while True:
                    if cancel_event is not None and cancel_event.is_set(): raise error('cancelled','import cancelled')
                    block=stream.read(1024*1024)
                    if not block: break
                    output.write(block);digest.update(block);total+=len(block)
                    if progress: progress(total,before.st_size)
                output.flush();os.fsync(output.fileno())
            after=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise error('capture_unavailable','import source changed during copy')
            if final.exists():
                with final.open('rb') as check:
                    existing=hashlib.file_digest(check,'sha256').hexdigest()
                if existing!=digest.hexdigest(): raise error('request_conflict','existing import has different content')
            else: os.link(temporary,final)
            if os.name!='nt':
                fd=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                try: os.fsync(fd)
                finally: os.close(fd)
            return {'photo_uuid':photo_uuid,'original_request_uuid':structure.get('request_id') or photo_uuid,
                    'origin_kind':'import','media_type':media_type,'extension':extension,'width':width,'height':height,
                    'orientation':orientation,'display_width':height if orientation&4 else width,'display_height':width if orientation&4 else height,
                    'byte_count':total,'sha256':digest.hexdigest(),'structure':structure,
                    'timestamp':before.st_mtime,'filename':source.name,'simulated':structure.get('metadata_state')=='valid'}
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)


class LibraryAuthorityMixin(LibraryClientMixin):
    def _init_library(self):
        self._library_pins={};self._library_ready=False
        with self.lock:
            self.db.executescript(SCHEMA)
            row=self.db.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
            self._library_id=row[0] if row else str(uuid.uuid4())
            identity(self._library_id)
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('library_id',?)",(self._library_id,))
            for row in self.db.execute("SELECT record_json FROM captures WHERE status='complete'").fetchall():
                self._library_capture(json.loads(row[0]))
            self.db.commit();self._library_ready=True
            pending=self.db.execute("SELECT photo_uuid,purge_operation FROM library_assets WHERE state='purge_pending'").fetchall()
            for row in pending:
                try:self._library_purge(row[0],row[1],PURGE_CONFIRMATION,recover=True)
                except Exception:pass  # Asset intent remains pending; capture history is untouched.

    def _library_capture(self, record):
        if record.get('status')!='complete': return
        from .retained import summary,dimensions_for_record
        photo=record['request_id']
        if self.db.execute('SELECT 1 FROM library_assets WHERE photo_uuid=?',(photo,)).fetchone(): return
        meta=summary(record,*dimensions_for_record(record))
        path=self.photos/(photo+'.dng')
        meta.update(photo_uuid=photo,original_request_uuid=photo,origin_kind='capture',media_type='image/x-adobe-dng',extension='.dng',byte_count=path.stat().st_size if path.exists() else 0)
        self.db.execute('INSERT INTO library_assets(photo_uuid,raw_uuid,origin,metadata,relative_path) VALUES(?,?,?,?,?)',
                        (photo,photo,'capture',json.dumps(meta,sort_keys=True),'photos/'+photo+'.dng'))

    def _library_asset(self, photo):
        row=self.db.execute('SELECT * FROM library_assets WHERE photo_uuid=?',(identity(photo),)).fetchone()
        if row is None: raise error('not_found','library photo not found')
        result=json.loads(row['metadata'])
        seq=self.db.execute('SELECT max(seq) FROM library_changes WHERE photo_uuid=?',(photo,)).fetchone()[0]
        result.update(library_id=self._library_id,state=row['state'],protected=bool(row['protected']),trashed=bool(row['trashed']),change_seq=seq)
        return result

    def library_rpc(self, operation, params):
        with self.lock:
            try:
                if operation=='info':
                    seq=self.db.execute('SELECT coalesce(max(seq),0) FROM library_changes').fetchone()[0]
                    high=self.db.execute('SELECT coalesce(max(rowid),0) FROM library_assets').fetchone()[0]
                    return {'library_id':self._library_id,'schema_version':1,'change_seq':seq,'asset_high_water':high}
                if operation in ('list','changes'):
                    limit=params.get('limit',50);after=params.get('after',0)
                    if type(limit)is not int or not 1<=limit<=50 or type(after)is not int or after<0: raise error('invalid_request','bounded library page required')
                    if operation=='list':
                        upper=params.get('upper')
                        rows=self.db.execute('SELECT rowid,photo_uuid FROM library_assets WHERE rowid>?'+(' AND rowid<=?' if upper is not None else '')+' ORDER BY rowid LIMIT ?',
                            (after,upper,limit+1) if upper is not None else (after,limit+1)).fetchall()
                        return {'library_id':self._library_id,'items':[self._library_asset(r[1]) for r in rows[:limit]],'next_after':rows[min(len(rows),limit)-1][0] if rows else after,'has_more':len(rows)>limit}
                    rows=self.db.execute('SELECT seq,photo_uuid,kind FROM library_changes WHERE seq>? ORDER BY seq LIMIT ?',(after,limit+1)).fetchall()
                    return {'library_id':self._library_id,'items':[{'seq':r[0],'kind':r[2],'asset':self._library_asset(r[1])} for r in rows[:limit]],'next_after':rows[min(len(rows),limit)-1][0] if rows else after,'has_more':len(rows)>limit}
                photo=identity(params.get('photo_uuid')) if operation not in ('unpin','register_import') else None
                if operation=='get': return self._library_asset(photo)
                if operation=='register_import':
                    descriptor=params['descriptor'];photo=identity(descriptor['photo_uuid']);identity(descriptor['original_request_uuid'])
                    path=canonical_asset_path(self.root,descriptor)
                    if descriptor.get('origin_kind')!='import' or path.stat().st_size!=descriptor['byte_count']: raise error('invalid_request','import descriptor mismatch')
                    existing=self.db.execute('SELECT metadata FROM library_assets WHERE photo_uuid=?',(photo,)).fetchone()
                    if existing and json.loads(existing[0]).get('sha256')!=descriptor.get('sha256'): raise error('request_conflict','import UUID conflict')
                    self.db.execute('INSERT OR IGNORE INTO library_assets(photo_uuid,raw_uuid,origin,metadata,relative_path) VALUES(?,?,?,?,?)',
                        (photo,descriptor['original_request_uuid'],'import',json.dumps(descriptor,sort_keys=True),'library-imports/'+photo+descriptor['extension']))
                    self.db.commit();return self._library_asset(photo)
                if operation=='flags':
                    asset=self._library_asset(photo)
                    if asset['state']!='active': raise error('capture_unavailable','asset is not active')
                    if params.get('expected_change_seq')is not None and params['expected_change_seq']!=asset['change_seq']:raise error('request_conflict','asset guard revision changed')
                    if type(params.get('protected'))is not bool or type(params.get('trashed'))is not bool: raise error('invalid_request','boolean flags required')
                    if params['protected'] and params['trashed']: raise error('protected','protected photo cannot enter trash')
                    self.db.execute('UPDATE library_assets SET protected=?,trashed=? WHERE photo_uuid=?',(int(params['protected']),int(params['trashed']),photo));self.db.commit()
                    return self._library_asset(photo)
                if operation=='pin':
                    if self._library_asset(photo)['state']!='active': raise error('capture_unavailable','asset is unavailable')
                    if len(self._library_pins)>=64:raise error('camera_busy','asset reader limit reached')
                    token=str(uuid.uuid4());self._library_pins[token]=photo
                    return {'pin':token,'photo_uuid':photo}
                if operation=='unpin': self._library_pins.pop(params.get('pin'),None);return {'released':True}
                if operation=='purge': return self._library_purge(photo,identity(params.get('operation_id')),params.get('confirmation'))
                raise error('method_not_found','unsupported library method')
            except BaseException:
                self.db.rollback();raise

    def _library_purge(self, photo, operation_id, confirmation, recover=False):
        if confirmation!=PURGE_CONFIRMATION: raise error('confirmation_required','explicit permanent-delete confirmation required')
        asset=self._library_asset(photo)
        if asset['state']=='purged': return dict(asset,purge_result='already_purged')
        if asset['protected'] or not asset['trashed']: return dict(asset,purge_result='skipped_protected_or_not_trashed')
        if photo in self._library_pins.values() or self.active==photo: return dict(asset,purge_result='skipped_in_use')
        self.db.execute("UPDATE library_assets SET state='purge_pending',purge_operation=? WHERE photo_uuid=?",(operation_id,photo));self.db.commit()
        try:
            path=canonical_asset_path(self.root,asset);path.unlink()
            if os.name!='nt':
                fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                try: os.fsync(fd)
                finally: os.close(fd)
        except FileNotFoundError: pass
        except OSError:
            self.db.execute("UPDATE library_assets SET state='active' WHERE photo_uuid=?",(photo,));self.db.commit()
            return dict(self._library_asset(photo),purge_result='skipped_in_use_or_io')
        self.db.execute("UPDATE library_assets SET state='purged' WHERE photo_uuid=?",(photo,));self.db.commit()
        return dict(self._library_asset(photo),purge_result='purged')

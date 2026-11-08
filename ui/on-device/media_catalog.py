"""Persistent whole-library index, immutable edits and sidecar outbox.

All methods are blocking service methods intended for worker threads. Qt views
receive bounded pages only. Source RAW and capture journals are never written.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

SCHEMA_VERSION=1
DEFAULT_EDITS={'exposure':0.,'contrast':1.,'saturation':1.,'rotation':0,'crop':None}


class CatalogError(ValueError): pass


def canonical(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise CatalogError('invalid asset UUID')
    return value


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


class MediaCatalog:
    def __init__(self,data_dir,*,legacy_reader=None,client_getter=None):
        self.root=Path(data_dir)/'apps';self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'media-catalog.sqlite3';self.sidecars=self.root/'sidecars'
        self.cache=self.root/'media-cache';self.cache.mkdir(exist_ok=True)
        self.legacy_reader=legacy_reader;self.client_getter=client_getter
        self.active_library='';self._lock=threading.RLock()
        with self.db(write=True) as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,SCHEMA_VERSION):raise CatalogError('unsupported media catalog version; existing data preserved')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sources(library_id TEXT PRIMARY KEY,scan_after INTEGER NOT NULL DEFAULT 0,scan_upper INTEGER NOT NULL DEFAULT 0,change_after INTEGER NOT NULL DEFAULT 0,scan_complete INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS photos(seq INTEGER PRIMARY KEY AUTOINCREMENT,library_id TEXT NOT NULL,photo_uuid TEXT NOT NULL,source_seq INTEGER NOT NULL,data_json TEXT NOT NULL,asset_state TEXT NOT NULL,captured_at REAL NOT NULL,filename TEXT NOT NULL,rating INTEGER NOT NULL DEFAULT 0,protected INTEGER NOT NULL DEFAULT 0,trashed INTEGER NOT NULL DEFAULT 0,metadata_rev INTEGER NOT NULL DEFAULT 0,edit_rev INTEGER NOT NULL DEFAULT 0,sidecar_json TEXT NOT NULL,fingerprint TEXT,UNIQUE(library_id,photo_uuid));
            CREATE INDEX IF NOT EXISTS photo_order ON photos(library_id,captured_at DESC,seq DESC);
            CREATE INDEX IF NOT EXISTS photo_filter ON photos(library_id,trashed,protected,rating);
            CREATE INDEX IF NOT EXISTS photo_fingerprint ON photos(fingerprint);
            CREATE TABLE IF NOT EXISTS edit_revisions(library_id TEXT NOT NULL,photo_uuid TEXT NOT NULL,revision INTEGER NOT NULL,parent_revision INTEGER,params TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(library_id,photo_uuid,revision));
            CREATE TABLE IF NOT EXISTS sidecar_outbox(library_id TEXT NOT NULL,photo_uuid TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',error TEXT,PRIMARY KEY(library_id,photo_uuid));
            CREATE TABLE IF NOT EXISTS selections(selection_id TEXT PRIMARY KEY,library_id TEXT NOT NULL,query_json TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS selection_items(selection_id TEXT NOT NULL,ordinal INTEGER NOT NULL,photo_uuid TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(selection_id,photo_uuid));
            CREATE INDEX IF NOT EXISTS selection_order ON selection_items(selection_id,ordinal);
            CREATE TABLE IF NOT EXISTS derivatives(cache_key TEXT PRIMARY KEY,library_id TEXT NOT NULL,photo_uuid TEXT NOT NULL,edit_rev INTEGER NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,byte_count INTEGER NOT NULL,touched REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,desired TEXT NOT NULL DEFAULT 'run',payload TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,progress REAL NOT NULL DEFAULT 0,stage TEXT NOT NULL DEFAULT '',error TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS job_items(job_id TEXT NOT NULL,ordinal INTEGER NOT NULL,photo_uuid TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',input_json TEXT NOT NULL,result_json TEXT,error TEXT,PRIMARY KEY(job_id,ordinal));
            CREATE TABLE IF NOT EXISTS job_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS portable_restores(operation_id TEXT PRIMARY KEY,document_sha256 TEXT NOT NULL);
            ''')
            db.execute('PRAGMA user_version='+str(SCHEMA_VERSION))

    @contextmanager
    def db(self,*,write=False):
        lock=self._lock if write else None
        if lock:lock.acquire()
        connection=sqlite3.connect(self.path,timeout=1);connection.row_factory=sqlite3.Row
        try:
            connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA synchronous=FULL');connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA cache_size=-4096')
            with connection:yield connection
        finally:
            connection.close()
            if lock:lock.release()

    def source(self,library):
        with self.db() as db:
            row=db.execute('SELECT * FROM sources WHERE library_id=?',(library,)).fetchone()
            return dict(row) if row else None

    def sync(self,client,max_pages=2):
        """Bounded checkpointed backfill + durable commit changefeed."""
        info=client.describe_library();library=canonical(info['library_id']);self.active_library=library
        with self.db(write=True) as db:
            db.execute('INSERT OR IGNORE INTO sources(library_id,scan_upper,change_after) VALUES(?,?,?)',(library,info['asset_high_water'],info['change_seq']))
        state=self.source(library);pages=0;changed=0
        while pages<max_pages:
            if not state['scan_complete']:
                page=client.list_library_assets(50,state['scan_after'],state['scan_upper'])
                for asset in page['items']:self.upsert_asset(library,asset);changed+=1
                with self.db(write=True) as db:
                    db.execute('UPDATE sources SET scan_after=?,scan_complete=?,updated=? WHERE library_id=?',(page['next_after'],not page['has_more'],time.time(),library))
            else:
                page=client.list_library_changes(state['change_after'],50)
                for event in page['items']:self.upsert_asset(library,event['asset']);changed+=1
                with self.db(write=True) as db:db.execute('UPDATE sources SET change_after=?,updated=? WHERE library_id=?',(page['next_after'],time.time(),library))
            pages+=1;state=self.source(library)
            if state['scan_complete'] and not page['has_more']:break
        return {'library_id':library,'changed':changed,'scan_complete':bool(state['scan_complete']),'more':not state['scan_complete'] or page['has_more'],'count':self.count(library,{'filter':'all','include_purged':True})}

    def upsert_asset(self,library,asset):
        photo=canonical(asset['photo_uuid']);canonical(asset['original_request_uuid'])
        sidecar={'format':'daos.photo-sidecar.v1','request_id':photo,'rating':0,'protected':bool(asset.get('protected')),'trashed_at':time.time()*1000 if asset.get('trashed') else None,'edits':dict(DEFAULT_EDITS)}
        if self.legacy_reader:
            try:sidecar=self.legacy_reader(photo)
            except (OSError,ValueError):pass
        with self.db(write=True) as db:
            old=db.execute('SELECT source_seq FROM photos WHERE library_id=? AND photo_uuid=?',(library,photo)).fetchone()
            if old and old[0]>asset['change_seq']:return
            if old:
                db.execute('UPDATE photos SET source_seq=?,data_json=?,asset_state=?,fingerprint=coalesce(fingerprint,?) WHERE library_id=? AND photo_uuid=?',
                           (asset['change_seq'],_json(asset),asset['state'],asset.get('sha256'),library,photo))
            else:
                db.execute('INSERT INTO photos(library_id,photo_uuid,source_seq,data_json,asset_state,captured_at,filename,rating,protected,trashed,sidecar_json,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (library,photo,asset['change_seq'],_json(asset),asset['state'],asset.get('timestamp') or time.time(),str(asset.get('filename',photo)),sidecar['rating'],int(sidecar['protected']),sidecar.get('trashed_at') is not None,_json(sidecar),asset.get('sha256')))
                db.execute('INSERT OR IGNORE INTO edit_revisions VALUES(?,?,0,NULL,?,?)',(library,photo,_json(sidecar.get('edits',DEFAULT_EDITS)),time.time()))

    def _where(self,library,query):
        clauses=['library_id=?'];args=[library];kind=query.get('filter','all')
        if not query.get('include_purged'):clauses.append("asset_state!='purged'")
        clauses.append('trashed=1' if kind=='trash' else 'trashed=0')
        if kind=='rated':clauses.append('rating>=?');args.append(int(query.get('minimum_rating',1)))
        if kind=='protected':clauses.append('protected=1')
        text=str(query.get('search',''))[:256]
        if text:clauses.append("filename LIKE ? ESCAPE '\\'");args.append('%'+text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        if query.get('date_from') is not None:clauses.append('captured_at>=?');args.append(float(query['date_from']))
        if query.get('date_to') is not None:clauses.append('captured_at<?');args.append(float(query['date_to']))
        return ' AND '.join(clauses),args

    def _row(self,row):
        item=json.loads(row['data_json']);sidecar=json.loads(row['sidecar_json'])
        item.update(request_id=row['photo_uuid'],photo_uuid=row['photo_uuid'],library_id=row['library_id'],rating=row['rating'],protected=bool(row['protected']),trashed=bool(row['trashed']),metadata_revision=row['metadata_rev'],edit_revision=row['edit_rev'],edits=sidecar.get('edits',DEFAULT_EDITS),fingerprint=row['fingerprint'])
        return item

    def page(self,library,query=None,cursor='',limit=48):
        if not 1<=limit<=60:raise CatalogError('page limit must be 1..60')
        query=query or {};where,args=self._where(library,query);sort=query.get('sort','newest')
        asc=sort=='oldest';operator='>' if asc else '<';order='ASC' if asc else 'DESC'
        query_key=hashlib.sha256(_json([library,query]).encode()).hexdigest()[:16]
        if cursor:
            value=json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if value['query']!=query_key:raise CatalogError('cursor belongs to another query')
            if sort=='rating':where+=' AND (rating,captured_at,seq)<(?,?,?)';args.extend(value['last'])
            else:where+=' AND (captured_at,seq)'+operator+'(?,?)';args.extend(value['last'])
        order_by='rating DESC,captured_at DESC,seq DESC' if sort=='rating' else 'captured_at '+order+',seq '+order
        with self.db() as db:rows=db.execute('SELECT * FROM photos WHERE '+where+' ORDER BY '+order_by+' LIMIT ?',(*args,limit+1)).fetchall()
        result=[self._row(row) for row in rows[:limit]];next_cursor=''
        if len(rows)>limit:
            row=rows[limit-1];last=[row['rating'],row['captured_at'],row['seq']] if sort=='rating' else [row['captured_at'],row['seq']]
            next_cursor=base64.urlsafe_b64encode(_json({'query':query_key,'last':last}).encode()).decode()
        return {'items':result,'next_cursor':next_cursor,'count':self.count(library,query),'library_id':library}

    def count(self,library,query=None):
        where,args=self._where(library,query or {})
        with self.db() as db:return db.execute('SELECT count(*) FROM photos WHERE '+where,args).fetchone()[0]

    def photo(self,library,photo):
        with self.db() as db:row=db.execute('SELECT * FROM photos WHERE library_id=? AND photo_uuid=?',(library,canonical(photo))).fetchone()
        if not row:raise CatalogError('photo is not indexed')
        return self._row(row)

    def sidecar(self,library,photo):
        with self.db() as db:row=db.execute('SELECT sidecar_json FROM photos WHERE library_id=? AND photo_uuid=?',(library,photo)).fetchone()
        if not row:raise CatalogError('photo is not indexed')
        return json.loads(row[0])

    def mutate(self,library,photo,changes,expected_revision=None):
        with self.db(write=True) as db:
            row=db.execute('SELECT * FROM photos WHERE library_id=? AND photo_uuid=?',(library,canonical(photo))).fetchone()
            if not row:raise CatalogError('photo is not indexed')
            if expected_revision is not None and row['metadata_rev']!=expected_revision:raise CatalogError('metadata revision conflict')
            value=json.loads(row['sidecar_json']);value.update(copy.deepcopy(changes));revision=row['metadata_rev']+1;edit_rev=row['edit_rev']
            if value.get('protected') and value.get('trashed_at') is not None:raise CatalogError('protected photo cannot be trashed')
            if 'edits' in changes:
                edit_rev=db.execute('SELECT coalesce(max(revision),0)+1 FROM edit_revisions WHERE library_id=? AND photo_uuid=?',(library,photo)).fetchone()[0]
                db.execute('INSERT INTO edit_revisions VALUES(?,?,?,?,?,?)',(library,photo,edit_rev,row['edit_rev'],_json(value['edits']),time.time()))
            value.update(library_id=library,metadata_revision=revision,edit_revision=edit_rev,updated_at_unix_ms=int(time.time()*1000))
            db.execute('UPDATE photos SET rating=?,protected=?,trashed=?,metadata_rev=?,edit_rev=?,sidecar_json=? WHERE library_id=? AND photo_uuid=?',
                       (int(value.get('rating',0)),bool(value.get('protected')),value.get('trashed_at') is not None,revision,edit_rev,_json(value),library,photo))
            portable={'format':'daos.photo-sidecar.v2','photo_uuid':photo,'library_id':library,'metadata_revision':revision,'edit_revision':edit_rev,'fingerprint':row['fingerprint'],'metadata':value}
            db.execute("INSERT INTO sidecar_outbox VALUES(?,?,?,?, 'pending',NULL) ON CONFLICT(library_id,photo_uuid) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,status='pending',error=NULL",(library,photo,revision,_json(portable)))
        return value

    def flush_outbox(self,limit=32):
        with self.db() as db:rows=db.execute("SELECT * FROM sidecar_outbox WHERE status!='complete' LIMIT ?",(limit,)).fetchall()
        completed=0
        for row in rows:
            directory=self.sidecars/row['library_id'];directory.mkdir(parents=True,exist_ok=True)
            path=directory/(row['photo_uuid']+'.daos.json');temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=directory,prefix='.outbox-',delete=False) as stream:
                    temporary=Path(stream.name);stream.write((row['payload']+'\n').encode());stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,path)
                if os.name!='nt':
                    fd=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                    try:os.fsync(fd)
                    finally:os.close(fd)
                with self.db(write=True) as db:db.execute("UPDATE sidecar_outbox SET status='complete',error=NULL WHERE library_id=? AND photo_uuid=? AND revision=?",(row['library_id'],row['photo_uuid'],row['revision']))
                completed+=1
            except OSError as exc:
                with self.db(write=True) as db:db.execute("UPDATE sidecar_outbox SET status='pending',error=? WHERE library_id=? AND photo_uuid=?",(str(exc),row['library_id'],row['photo_uuid']))
            finally:
                if temporary:temporary.unlink(missing_ok=True)
        return completed

    def history(self,library,photo,before=None,limit=100):
        if not 1<=limit<=100:raise CatalogError('history page limit must be 1..100')
        with self.db() as db:rows=db.execute('SELECT revision,parent_revision,params,created FROM edit_revisions WHERE library_id=? AND photo_uuid=? AND revision<? ORDER BY revision DESC LIMIT ?',(library,photo,before if before is not None else 9223372036854775807,limit)).fetchall()
        return [dict(revision=r[0],parent_revision=r[1],edits=json.loads(r[2]),created=r[3]) for r in rows]

    def touch_derivative(self,key,asset,kind,path,*,max_bytes=256*1024*1024,max_items=2048):
        """Bound the disposable disk cache independently of original assets."""
        path=Path(path);resolved=path.resolve();owned=self.cache.resolve()
        if path.is_symlink()or resolved.parent!=owned:raise CatalogError('预览缓存路径已重定向')
        size=path.stat().st_size
        with self.db(write=True) as db:
            db.execute('INSERT INTO derivatives VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET touched=excluded.touched,byte_count=excluded.byte_count',(key,asset['library_id'],asset['photo_uuid'],asset.get('edit_revision',0),kind,str(path),size,time.time()))
            count,total=db.execute('SELECT count(*),coalesce(sum(byte_count),0) FROM derivatives').fetchone()
            while count>max_items or total>max_bytes:
                rows=db.execute('SELECT cache_key,path,byte_count FROM derivatives ORDER BY touched LIMIT 64').fetchall()
                if not rows:break
                for row in rows:
                    candidate=Path(row['path'])
                    if not candidate.is_symlink()and candidate.resolve().parent==owned:candidate.unlink(missing_ok=True)
                    db.execute('DELETE FROM derivatives WHERE cache_key=?',(row['cache_key'],));count-=1;total-=row['byte_count']
                    if count<=max_items and total<=max_bytes:break
        return {'items':count,'bytes':total,'max_items':max_items,'max_bytes':max_bytes}

    def set_history(self,library,photo,revision):
        with self.db(write=True) as db:
            row=db.execute('SELECT params FROM edit_revisions WHERE library_id=? AND photo_uuid=? AND revision=?',(library,photo,revision)).fetchone()
            if not row:raise CatalogError('edit revision not found')
            current=db.execute('SELECT * FROM photos WHERE library_id=? AND photo_uuid=?',(library,photo)).fetchone()
            value=json.loads(current['sidecar_json']);metadata_rev=current['metadata_rev']+1
            value.update(edits=json.loads(row[0]),edit_revision=revision,metadata_revision=metadata_rev)
            db.execute('UPDATE photos SET edit_rev=?,metadata_rev=?,sidecar_json=? WHERE library_id=? AND photo_uuid=?',(revision,metadata_rev,_json(value),library,photo))
            portable={'format':'daos.photo-sidecar.v2','photo_uuid':photo,'library_id':library,'metadata_revision':metadata_rev,'edit_revision':revision,'fingerprint':current['fingerprint'],'metadata':value}
            db.execute("INSERT INTO sidecar_outbox VALUES(?,?,?,?,'pending',NULL) ON CONFLICT(library_id,photo_uuid) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,status='pending',error=NULL",(library,photo,metadata_rev,_json(portable)))
        return value

    def navigate_history(self,library,photo,direction):
        with self.db() as db:
            current=db.execute('SELECT edit_rev FROM photos WHERE library_id=? AND photo_uuid=?',(library,photo)).fetchone()[0]
            if direction=='undo':row=db.execute('SELECT parent_revision FROM edit_revisions WHERE library_id=? AND photo_uuid=? AND revision=?',(library,photo,current)).fetchone()
            else:row=db.execute('SELECT revision FROM edit_revisions WHERE library_id=? AND photo_uuid=? AND parent_revision=? ORDER BY revision DESC LIMIT 1',(library,photo,current)).fetchone()
        if row and row[0] is not None:return self.set_history(library,photo,row[0])
        return self.sidecar(library,photo)

    def create_selection(self,library,query=None,photo_ids=None):
        selection=str(uuid.uuid4());query=query or {}
        with self.db(write=True) as db:
            db.execute('INSERT INTO selections VALUES(?,?,?,?)',(selection,library,_json(query),time.time()))
            where,args=self._where(library,query)
            if photo_ids is not None:
                if len(photo_ids)>10000:raise CatalogError('explicit selection exceeds 10000')
                where+=' AND photo_uuid IN ('+','.join('?' for _ in photo_ids)+')' if photo_ids else ' AND 0'
                args.extend(photo_ids)
            cursor=db.execute('SELECT * FROM photos WHERE '+where+' ORDER BY captured_at DESC,seq DESC',args)
            count=0
            for count,row in enumerate(cursor,1):db.execute('INSERT INTO selection_items VALUES(?,?,?,?)',(selection,count,row['photo_uuid'],_json(self._row(row))))
        return {'selection_id':selection,'count':count}

    def selection_rows(self,selection,after=0,limit=50):
        with self.db() as db:rows=db.execute('SELECT ordinal,snapshot FROM selection_items WHERE selection_id=? AND ordinal>? ORDER BY ordinal LIMIT ?',(selection,after,limit)).fetchall()
        return [(row[0],json.loads(row[1])) for row in rows]

    def record_fingerprint(self,library,photo,digest):
        if len(digest)!=64:raise CatalogError('invalid SHA256')
        with self.db(write=True) as db:db.execute('UPDATE photos SET fingerprint=? WHERE library_id=? AND photo_uuid=?',(digest,library,photo))

    def backup(self,destination):
        path=Path(destination)
        if not path.is_absolute() or path.exists():raise CatalogError('choose a new absolute backup filename')
        source=sqlite3.connect(self.path);target=sqlite3.connect(path)
        try:source.backup(target,pages=128)
        finally:target.close();source.close()
        return {'path':str(path),'byte_count':path.stat().st_size}

    def _ensure_fingerprints(self):
        if not self.client_getter:return
        client=self.client_getter()
        if client is None:return
        library=client.describe_library()['library_id']
        after=0
        while True:
            with self.db() as db:rows=db.execute("SELECT seq,photo_uuid FROM photos WHERE library_id=? AND fingerprint IS NULL AND asset_state='active' AND seq>? ORDER BY seq LIMIT 20",(library,after)).fetchall()
            if not rows:break
            for row in rows:
                try:
                    with client.open_library_asset(row['photo_uuid']) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    self.record_fingerprint(library,row['photo_uuid'],digest)
                except Exception:continue
            after=rows[-1]['seq']

    def exportPortableMetadata(self):
        self._ensure_fingerprints()
        with self.db() as db:rows=db.execute('SELECT fingerprint,sidecar_json FROM photos WHERE asset_state!=\'purged\'').fetchall()
        items=[];missing=0
        for row in rows:
            if not row[0]:missing+=1;continue
            value=json.loads(row[1]);items.append({'sha256':row[0],'rating':value.get('rating',0),'protected':value.get('protected',False),'trashed':value.get('trashed_at') is not None,'edits':value.get('edits',DEFAULT_EDITS)})
        return {'format':'daos.portable-photo-metadata.v1','items':items,'unavailable_fingerprints':missing}

    @staticmethod
    def _portable_items(document):
        if (not isinstance(document,dict) or document.get('format')!='daos.portable-photo-metadata.v1'
                or set(document)-{'format','items','unavailable_fingerprints'}
                or type(document.get('unavailable_fingerprints',0))is not int or not 0<=document.get('unavailable_fingerprints',0)<=100000
                or not isinstance(document.get('items'),list) or len(document['items'])>100000
                or len(_json(document).encode())>16*1024*1024):raise CatalogError('invalid portable metadata')
        items={};duplicates=[]
        for item in document['items']:
            if not isinstance(item,dict) or set(item)-{'sha256','rating','protected','trashed','edits'}:raise CatalogError('invalid portable photo fields')
            digest=item.get('sha256','')
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise CatalogError('invalid metadata fingerprint')
            if (type(item.get('rating'))is not int or not 0<=item['rating']<=5
                    or type(item.get('protected'))is not bool or type(item.get('trashed'))is not bool
                    or item['protected'] and item['trashed']):raise CatalogError('invalid portable photo flags')
            edits=item.get('edits')
            if not isinstance(edits,dict) or len(_json(edits).encode())>16384:raise CatalogError('invalid portable edits')
            bounds={'exposure':(-10,10),'exposure_ev':(-10,10),'contrast':(0,3),'saturation':(0,3),'temperature':(2000,12000),'tint':(-1,1),'noise':(0,1),'sharpen':(0,3)}
            if set(edits)-set(bounds)-{'rotation','crop','curve','wb'}:raise CatalogError('unsupported portable edit parameters')
            for key,value in edits.items():
                if key in bounds and (type(value)not in(int,float) or not math.isfinite(value) or not bounds[key][0]<=value<=bounds[key][1]):raise CatalogError('invalid portable adjustment')
            if type(edits.get('rotation',0))is not int or edits.get('rotation',0)not in(0,90,180,270):raise CatalogError('invalid rotation')
            crop=edits.get('crop')
            if crop is not None and (not isinstance(crop,list) or len(crop)!=4 or any(type(v)not in(int,float) or not math.isfinite(v) for v in crop)
                    or not(0<=crop[0]<1 and 0<=crop[1]<1 and crop[2]>0 and crop[3]>0 and crop[0]+crop[2]<=1.000001 and crop[1]+crop[3]<=1.000001)):raise CatalogError('invalid crop')
            if 'wb'in edits and (not isinstance(edits['wb'],list) or len(edits['wb'])!=3 or any(type(v)not in(int,float)or not math.isfinite(v)or not .1<=v<=8 for v in edits['wb'])):raise CatalogError('invalid white balance')
            if 'curve'in edits:
                curve=edits['curve']
                if not isinstance(curve,list)or not 2<=len(curve)<=32 or any(not isinstance(point,list)or len(point)!=2 or any(type(v)not in(int,float)or not math.isfinite(v)or not 0<=v<=1 for v in point)for point in curve)or any(a[0]>=b[0]or a[1]>b[1]for a,b in zip(curve,curve[1:]))or curve[0]!=[0,0]or curve[-1]!=[1,1]:raise CatalogError('invalid tone curve')
            if digest in items and _json(items[digest])!=_json(item):duplicates.append({'sha256':digest,'reason':'backup has different metadata for identical RAW bytes'})
            else:items[digest]=item
        return list(items.values()),duplicates

    @staticmethod
    def validatePortableDocument(document):
        items,duplicates=MediaCatalog._portable_items(document)
        if duplicates:raise CatalogError('backup has conflicting metadata for identical fingerprints')
        return {'valid':True,'item_count':len(items)}

    @staticmethod
    def validateEditParameters(edits):
        MediaCatalog.validatePortableDocument({'format':'daos.portable-photo-metadata.v1','items':[{'sha256':'0'*64,'rating':0,'protected':False,'trashed':False,'edits':edits}]})
        return copy.deepcopy(edits)

    def _portable_plan(self,db,items,duplicates):
        conflicts=list(duplicates[:100]);conflict_count=len(duplicates);unmatched=0;needed=0
        for item in items:
            rows=db.execute("SELECT metadata_rev,sidecar_json FROM photos WHERE fingerprint=? AND asset_state!='purged'",(item['sha256'],)).fetchall()
            if not rows:unmatched+=1;continue
            for row in rows:
                value=json.loads(row['sidecar_json'])
                same=(value.get('rating',0)==item['rating'] and bool(value.get('protected'))==item['protected']
                      and (value.get('trashed_at')is not None)==item['trashed'] and value.get('edits',DEFAULT_EDITS)==item['edits'])
                if row['metadata_rev'] and not same:
                    conflict_count+=1
                    if len(conflicts)<100:conflicts.append({'sha256':item['sha256'],'reason':'existing metadata differs'})
                elif not same:needed+=1
        return {'valid':conflict_count==0,'conflicts':conflicts,'conflict_count':conflict_count,'unmatched':unmatched,'would_apply':needed}

    def validatePortableMetadata(self,document):
        items,duplicates=self._portable_items(document)
        with self.db() as db:return self._portable_plan(db,items,duplicates)

    def portableRestoreCommitted(self,operation_id):
        try:operation=uuid.UUID(operation_id).hex
        except (ValueError,TypeError,AttributeError):return False
        with self.db() as db:return db.execute('SELECT 1 FROM portable_restores WHERE operation_id=?',(operation,)).fetchone()is not None

    def restorePortableMetadata(self,document,operation_id=None):
        """Strict preflight + one transaction: any conflict changes no metadata."""
        items,duplicates=self._portable_items(document);self._ensure_fingerprints();applied=0
        operation=uuid.UUID(operation_id).hex if operation_id is not None else None
        digest=hashlib.sha256(_json(document).encode()).hexdigest()
        with self.db(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            if operation:
                previous=db.execute('SELECT document_sha256 FROM portable_restores WHERE operation_id=?',(operation,)).fetchone()
                if previous:
                    if previous[0]!=digest:raise CatalogError('restore operation UUID has another document')
                    return {'valid':True,'applied':0,'conflicts':[],'unmatched':0,'already_committed':True}
            plan=self._portable_plan(db,items,duplicates)
            if not plan['valid']:return dict(plan,applied=0)
            for item in items:
                rows=db.execute("SELECT * FROM photos WHERE fingerprint=? AND asset_state!='purged'",(item['sha256'],)).fetchall()
                for row in rows:
                    value=json.loads(row['sidecar_json'])
                    same=(value.get('rating',0)==item['rating'] and bool(value.get('protected'))==item['protected'] and (value.get('trashed_at')is not None)==item['trashed'] and value.get('edits',DEFAULT_EDITS)==item['edits'])
                    if same:continue
                    revision=row['metadata_rev']+1;edit_rev=row['edit_rev']
                    if value.get('edits',DEFAULT_EDITS)!=item['edits']:
                        edit_rev=db.execute('SELECT coalesce(max(revision),0)+1 FROM edit_revisions WHERE library_id=? AND photo_uuid=?',(row['library_id'],row['photo_uuid'])).fetchone()[0]
                        db.execute('INSERT INTO edit_revisions VALUES(?,?,?,?,?,?)',(row['library_id'],row['photo_uuid'],edit_rev,row['edit_rev'],_json(item['edits']),time.time()))
                    value.update(rating=item['rating'],protected=item['protected'],trashed_at=value.get('trashed_at')or int(time.time()*1000) if item['trashed'] else None,
                                 edits=item['edits'],metadata_revision=revision,edit_revision=edit_rev)
                    db.execute('UPDATE photos SET rating=?,protected=?,trashed=?,metadata_rev=?,edit_rev=?,sidecar_json=? WHERE library_id=? AND photo_uuid=?',(item['rating'],item['protected'],item['trashed'],revision,edit_rev,_json(value),row['library_id'],row['photo_uuid']))
                    payload={'format':'daos.photo-sidecar.v2','photo_uuid':row['photo_uuid'],'library_id':row['library_id'],'metadata_revision':revision,'edit_revision':edit_rev,'fingerprint':row['fingerprint'],'metadata':value}
                    db.execute("INSERT INTO sidecar_outbox VALUES(?,?,?,?,'pending',NULL) ON CONFLICT(library_id,photo_uuid) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,status='pending',error=NULL",(row['library_id'],row['photo_uuid'],revision,_json(payload)))
                    applied+=1
            if operation:db.execute('INSERT INTO portable_restores VALUES(?,?)',(operation,digest))
        # Outbox publication is recoverable; it is outside the metadata commit.
        publication_error=''
        try:self.flush_outbox()
        except Exception as exc:publication_error=str(exc)
        return dict(plan,applied=applied,sidecar_sync_pending=bool(publication_error),outbox_error=publication_error)

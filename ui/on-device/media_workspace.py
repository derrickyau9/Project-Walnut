"""Whole-library Qt facade. Every disk, database and decode runs off the GUI."""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import QObject,QAbstractListModel,QModelIndex,Qt,QTimer,pyqtProperty,pyqtSignal,pyqtSlot
from PyQt5.QtGui import QImage
from PyQt5.QtQuick import QQuickImageProvider

from media_catalog import CatalogError,_json


class MediaImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image);self._lock=threading.Lock();self.images=OrderedDict();self.generation=0
    def publish(self,channel,image):
        with self._lock:
            self.generation+=1;key=channel+'/'+str(self.generation);self.images[key]=QImage(image)
            while len(self.images)>96 or sum(i.sizeInBytes() for i in self.images.values())>24*1024*1024:self.images.popitem(last=False)
        return 'image://daosmedia/'+key
    def requestImage(self,identifier,requestedSize):
        with self._lock:image=QImage(self.images.get(identifier,QImage()))
        return image,image.size()


class PhotosModel(QAbstractListModel):
    Role=Qt.UserRole+1
    def __init__(self,parent=None):super().__init__(parent);self.rows=[]
    def roleNames(self):return {self.Role:b'item'}
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def data(self,index,role=Qt.DisplayRole):return self.rows[index.row()] if index.isValid() and role==self.Role else None
    def replace(self,rows):
        if len(rows)>60:raise CatalogError('图库模型仅接受至多 60 行的分页')
        self.beginResetModel();self.rows=list(rows);self.endResetModel()
    def patch(self,photo,changes):
        for index,row in enumerate(self.rows):
            if row['photo_uuid']==photo:row.update(changes);self.dataChanged.emit(self.index(index),self.index(index),[self.Role]);break


class CatalogSidecarStore:
    """Canonical catalog edits; legacy JSON is a compatible portable mirror."""
    def __init__(self,legacy,catalog,client_getter):self.legacy=legacy;self.root=legacy.root;self.catalog=catalog;self.client_getter=client_getter
    def read(self,photo):
        try:return self.catalog.sidecar(self.catalog.active_library,photo)
        except CatalogError:return self.legacy.read(photo)
    def write(self,photo,changes):
        library=self.catalog.active_library
        try:current=self.catalog.sidecar(library,photo)
        except CatalogError:
            result=self.legacy.write(photo,changes)
            client=self.client_getter()
            if not library or client is None:return result
            try:self.catalog.upsert_asset(library,client.get_library_asset(photo));current=self.catalog.sidecar(library,photo)
            except Exception:return result
        merged=dict(current,**changes);client=self.client_getter()
        if client and ('protected'in changes or 'trashed_at'in changes):
            asset=client.get_library_asset(photo)
            if 'protected'not in changes and asset['protected'] and merged.get('trashed_at')is not None:raise CatalogError('照片已受保护，不能移入回收站')
            client.set_library_asset_flags(photo,protected=bool(merged.get('protected')),trashed=merged.get('trashed_at')is not None,expected_change_seq=asset['change_seq'])
        result=self.catalog.mutate(library,photo,changes)
        try:
            old=self.root/(photo+'.daos.json');backup=self.catalog.root/'legacy-media-backup'/library
            if old.exists():
                backup.mkdir(parents=True,exist_ok=True);target=backup/old.name
                if not target.exists():
                    with old.open('rb') as src,target.open('xb') as dst:dst.write(src.read(32769));dst.flush();os.fsync(dst.fileno())
            self.legacy.write(photo,changes)
        except OSError:result['sidecar_sync_pending']=True
        self.catalog.flush_outbox();return result


class GalleryWorkspace(QObject):
    changed=pyqtSignal()
    _result=pyqtSignal(str,object,object)
    _image=pyqtSignal(str,str,object,object)

    def __init__(self,camera,catalog,tasks,parent=None):
        super().__init__(parent);self.camera=camera;self.catalog=catalog;self.tasks=tasks
        self.imageProvider=MediaImageProvider();self._model=PhotosModel(self);self._items=[];self._rows={}
        self._available=False;self._busy=False;self._message='正在连接资料库';self._closed=False
        self._filter='all';self._search='';self._sort='newest';self._next='';self._cursor='';self._total=0
        self._library='';self._current={};self._history=[];self._selection='';self._selection_count=0;self._multi=False
        self._selection_inputs=[]
        self._pending_ui_edits={};self._edit_inflight=False;self._analysis={}
        self._review='';self._tile='';self._tile_size=(0,0);self._compare='';self._compare_asset={};self._compare_size=(0,0)
        self._generation=0;self._metadata_pending=0;self._sync_pending=False;self._page_pending=False
        self._sync_again=False;self._seen_capture=''
        self._browse_path='';self._browse_entries=[];self._mutations=set();self._date_from=self._date_to=None
        self._styles=[];self._wb_pending=False
        self._metadata=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-gallery-catalog')
        self._images=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-gallery-preview')
        self._image_pending=False;self._wanted=OrderedDict();self._image_generation=0
        self._image_requests={}
        self._image_cancel=threading.Event()
        self._result.connect(self._accept);self._image.connect(self._accept_image)
        camera.changed.connect(self._camera_changed);tasks.completed.connect(self._task_completed)
        self._timer=QTimer(self);self._timer.setInterval(1800);self._timer.timeout.connect(self.sync);self._timer.start()
        self._edit_timer=QTimer(self);self._edit_timer.setSingleShot(True);self._edit_timer.setInterval(200);self._edit_timer.timeout.connect(self._flush_ui_edits)
        self._camera_changed()
        self.refreshStyles()

    @pyqtProperty(bool,notify=changed)
    def available(self):return self._available
    @pyqtProperty(bool,notify=changed)
    def busy(self):return self._busy or self._page_pending or self._metadata_pending>0
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message
    @pyqtProperty(QObject,constant=True)
    def model(self):return self._model
    @pyqtProperty('QVariantList',notify=changed)
    def items(self):return list(self._items)
    @pyqtProperty(int,notify=changed)
    def totalCount(self):return self._total
    @pyqtProperty(str,notify=changed)
    def nextCursor(self):return self._next
    @pyqtProperty(str,notify=changed)
    def filterName(self):return self._filter
    @pyqtProperty(int,notify=changed)
    def selectionCount(self):return self._selection_count
    @pyqtProperty(bool,notify=changed)
    def multiSelect(self):return self._multi
    @pyqtProperty(str,notify=changed)
    def currentId(self):return self._current.get('photo_uuid','')
    @pyqtProperty('QVariantMap',notify=changed)
    def current(self):return dict(self._current)
    @pyqtProperty(str,notify=changed)
    def reviewUrl(self):return self._review
    @pyqtProperty(int,notify=changed)
    def reviewWidth(self):return self._current.get('display_width',self._current.get('width',0))
    @pyqtProperty(int,notify=changed)
    def reviewHeight(self):return self._current.get('display_height',self._current.get('height',0))
    @pyqtProperty(str,notify=changed)
    def tileUrl(self):return self._tile
    @pyqtProperty(int,notify=changed)
    def tileWidth(self):return self._tile_size[0]
    @pyqtProperty(int,notify=changed)
    def tileHeight(self):return self._tile_size[1]
    @pyqtProperty(str,notify=changed)
    def compareUrl(self):return self._compare
    @pyqtProperty(int,notify=changed)
    def compareWidth(self):return self._compare_size[0]
    @pyqtProperty(int,notify=changed)
    def compareHeight(self):return self._compare_size[1]
    @pyqtProperty('QVariantList',notify=changed)
    def editHistory(self):return list(self._history)
    @pyqtProperty('QVariantMap',notify=changed)
    def edits(self):return dict(self._current.get('edits',{}))
    @pyqtProperty('QVariantList',notify=changed)
    def selectedSources(self):return self.processing_inputs() if len(self._selection_inputs)<=16 else []
    @pyqtProperty('QVariantMap',notify=changed)
    def analysis(self):return dict(self._analysis)
    @pyqtProperty('QVariantList',notify=changed)
    def styles(self):return list(self._styles)
    @pyqtProperty(str,notify=changed)
    def archiveText(self):return json.dumps(self._current,ensure_ascii=False,indent=2)
    @pyqtProperty(str,notify=changed)
    def browsePath(self):return self._browse_path
    @pyqtProperty('QVariantList',notify=changed)
    def browseEntries(self):return list(self._browse_entries)

    def _submit(self,kind,function):
        if self._closed or self._metadata_pending>=16:self._message='图库操作队列已满，请稍候';self.changed.emit();return False
        self._metadata_pending+=1
        future=self._metadata.submit(function)
        def done(result):
            try:value,error=result.result(),None
            except Exception as exc:value,error=None,str(exc)
            if not self._closed:self._result.emit(kind,value,error)
        future.add_done_callback(done);return True

    def _camera_changed(self):
        if not self._available and getattr(self.camera,'connectionState','')=='ready':self.sync()
        elif getattr(self.camera,'captureState','')=='complete' and getattr(self.camera,'lastRequestId','')!=self._seen_capture:
            self._seen_capture=getattr(self.camera,'lastRequestId','');self.sync()

    @pyqtSlot()
    def sync(self):
        if self._closed:return
        if self._sync_pending:self._sync_again=True;return
        client=getattr(self.camera,'_client',None)
        if client is None:return
        self._sync_pending=True
        self._submit('sync',lambda:self.catalog.sync(client))

    def _query(self):return {'filter':self._filter,'search':self._search,'sort':self._sort,'date_from':self._date_from,'date_to':self._date_to}
    @pyqtSlot()
    def refresh(self):self._cursor='';self._load_page();self.sync();self.refreshStyles()
    def _load_page(self):
        if not self._library or self._page_pending:return
        self._page_pending=True;library=self._library;query=self._query();cursor=self._cursor
        def work():
            result=self.catalog.page(library,query,cursor)
            if self._selection:
                ids=[row['photo_uuid'] for row in result['items']]
                with self.catalog.db() as db:selected={r[0] for r in db.execute('SELECT photo_uuid FROM selection_items WHERE selection_id=? AND photo_uuid IN ('+','.join('?' for _ in ids)+')',(self._selection,*ids))} if ids else set()
            else:selected=set()
            for row in result['items']:row.update(thumbUrl='',selected=row['photo_uuid'] in selected)
            result['query']=query;result['cursor']=cursor;return result
        self._submit('page',work)
    @pyqtSlot()
    def nextPage(self):
        if self._next:self._cursor=self._next;self._load_page()
    @pyqtSlot(str)
    def setFilter(self,value):
        if value in {'all','rated','protected','trash'}:self._filter=value;self._cursor='';self._load_page();self.changed.emit()
    @pyqtSlot(str)
    def setSearch(self,value):self._search=value[:256];self._cursor='';self._load_page()
    @pyqtSlot(str)
    def setSort(self,value):
        if value in {'newest','oldest','rating'}:self._sort=value;self._cursor='';self._load_page()
    @pyqtSlot(float,float)
    def setDateRange(self,start,end):self._date_from=start if start>=0 else None;self._date_to=end if end>=0 else None;self._cursor='';self._load_page()
    @pyqtSlot(bool)
    def setMultiSelect(self,value):self._multi=value;self.changed.emit()

    @pyqtSlot(str)
    def selectPhoto(self,photo):
        if self._multi:self.toggleSelection(photo);return
        self._load_details(photo)
    def _load_details(self,photo):
        self._flush_ui_edits()
        self._generation+=1;generation=self._generation;self._review=self._tile='';self._compare=''
        def load():return {'photo':self.catalog.photo(self._library,photo),'history':self.catalog.history(self._library,photo),'generation':generation}
        self._submit('select',load)

    def _selection_task(self,all_query=False,toggle=None,clear=False):
        library=self._library;query=self._query()
        def work():
            if clear:return {'selection_id':'','count':0}
            if all_query or not self._selection:
                result=self.catalog.create_selection(library,query,photo_ids=None if all_query else [toggle] if toggle else [self.currentId])
            else:
                with self.catalog.db(write=True) as db:
                    old=db.execute('SELECT ordinal FROM selection_items WHERE selection_id=? AND photo_uuid=?',(self._selection,toggle)).fetchone()
                    if old:db.execute('DELETE FROM selection_items WHERE selection_id=? AND photo_uuid=?',(self._selection,toggle))
                    else:
                        ordinal=db.execute('SELECT coalesce(max(ordinal),0)+1 FROM selection_items WHERE selection_id=?',(self._selection,)).fetchone()[0]
                        row=db.execute('SELECT * FROM photos WHERE library_id=? AND photo_uuid=?',(library,toggle)).fetchone()
                        db.execute('INSERT INTO selection_items VALUES(?,?,?,?)',(self._selection,ordinal,toggle,_json(self.catalog._row(row))))
                    count=db.execute('SELECT count(*) FROM selection_items WHERE selection_id=?',(self._selection,)).fetchone()[0]
                result={'selection_id':self._selection,'count':count}
            result['inputs']=[r[1] for r in self.catalog.selection_rows(result['selection_id'],0,17)] if result['selection_id'] else []
            return result
        self._submit('selection',work)
    @pyqtSlot(str)
    def toggleSelection(self,photo):self._selection_task(toggle=photo)
    @pyqtSlot()
    def selectAll(self):self._multi=True;self._selection_task(all_query=True)
    @pyqtSlot()
    def clearSelection(self):self._selection_task(clear=True)

    def _batch(self,kind,options,destination=''):
        library=self._library;current=self.currentId;selection=self._selection;query=self._query()
        job_id=str(uuid.uuid4());mutation=kind in {'metadata','purge'}
        if mutation:
            if self._busy:return
            self._mutations.add(job_id);self._busy=True;self.changed.emit()
        def work():
            try:
                chosen=selection
                if not chosen:
                    if not current:raise CatalogError('请选择照片或全选筛选结果')
                    chosen=self.catalog.create_selection(library,query,photo_ids=[current])['selection_id']
                self.tasks.submit_selection(kind,chosen,options,destination,job_id=job_id)
                return {'job_id':job_id,'mutation':mutation}
            except Exception as exc:return {'job_id':job_id,'rejected':str(exc)}
        if not self._submit('queued',work):self._mutations.discard(job_id);self._busy=bool(self._mutations)
    @pyqtSlot(int)
    def setRating(self,value):
        if 0<=value<=5:self._batch('metadata',{'changes':{'rating':value}})
    @pyqtSlot(bool)
    def setProtected(self,value):self._batch('metadata',{'changes':{'protected':value}})
    @pyqtSlot()
    def trash(self):self._batch('metadata',{'changes':{'trashed_at':int(time.time()*1000)}})
    @pyqtSlot()
    def restore(self):self._batch('metadata',{'changes':{'trashed_at':None}})
    @pyqtSlot(str)
    def emptyTrash(self,confirmation):
        if confirmation!='PERMANENTLY_DELETE':self._message='永久清空需要明确确认';self.changed.emit();return
        if self._busy:return
        library=self._library;job_id=str(uuid.uuid4());self._mutations.add(job_id);self._busy=True;self.changed.emit()
        def work():
            try:
                selection=self.catalog.create_selection(library,{'filter':'trash'})
                self.tasks.submit_selection('purge',selection['selection_id'],{'confirmation':confirmation},'',job_id=job_id)
                return {'job_id':job_id,'mutation':True}
            except Exception as exc:return {'job_id':job_id,'rejected':str(exc)}
        if not self._submit('queued',work):self._mutations.discard(job_id);self._busy=bool(self._mutations)
    @pyqtSlot(str,str)
    def exportSelection(self,format,directory):self._batch({'original':'export_original','jpeg':'export_jpeg','tiff':'export_tiff'}.get(format,format),{},directory)
    @pyqtSlot(str)
    def importPaths(self,paths):
        sources=[p.strip() for p in paths.splitlines() if p.strip()]
        if not 1<=len(sources)<=1000:self._message='请输入 1–1000 个绝对文件路径，每行一个';self.changed.emit();return
        job=str(uuid.uuid4());items=[{'photo_uuid':str(uuid.uuid5(uuid.UUID(job),str(index))),'source':source} for index,source in enumerate(sources)]
        library=self._library
        self._submit('queued',lambda:self.tasks.enqueue('import',{'library_id':library},items,job_id=job))

    @pyqtSlot(str)
    def browseDirectory(self,value):
        def work():
            path=Path(value) if value else Path('F:/') if os.name=='nt' else next((Path(p) for p in ('/media','/mnt','/') if Path(p).is_dir()),Path('/'))
            path=path.absolute()
            if not path.is_dir() or path.is_symlink():raise CatalogError('请选择普通目录')
            entries=[]
            with os.scandir(path) as iterator:
                for entry in iterator:
                    if len(entries)>=512:break
                    if entry.is_symlink():continue
                    is_dir=entry.is_dir(follow_symlinks=False)
                    if is_dir or Path(entry.name).suffix.lower() in {'.dng','.jpg','.jpeg','.png','.tif','.tiff'}:
                        entries.append({'name':entry.name,'path':str(path/entry.name),'isDirectory':is_dir})
            entries.sort(key=lambda item:(not item['isDirectory'],item['name'].casefold()))
            return {'path':str(path),'entries':entries}
        self._submit('browse',work)
    @pyqtSlot()
    def browseUp(self):self.browseDirectory(str(Path(self._browse_path).parent))

    def _edit(self,photo,changes,revision):
        library=self._library;generation=self._generation
        def work():
            value=self.catalog.mutate(library,photo,changes,revision);self.catalog.flush_outbox()
            return {'photo':self.catalog.photo(library,photo),'history':self.catalog.history(library,photo),'generation':generation}
        self._edit_inflight=True
        if not self._submit('edited',work):self._edit_inflight=False;return False
        return True
    @pyqtSlot(str,float)
    def setEdit(self,name,value):
        edits=self.edits;edits[name]=value
        if name=='exposure':edits.pop('exposure_ev',None)
        self._stage_ui_edits(edits)
    def _stage_ui_edits(self,edits):
        if not self.currentId:return
        edits=self.catalog.validateEditParameters(edits)
        if 'exposure_ev'in edits:
            edits['exposure']=edits.pop('exposure_ev')
        if self.currentId not in self._pending_ui_edits and len(self._pending_ui_edits)>=8:self._message='编辑队列已满，请稍候';self.changed.emit();return
        self._current['edits']=edits;self._pending_ui_edits[self.currentId]={'edits':copy.deepcopy(edits),'revision':self._current['metadata_revision']};self._edit_timer.start();self.changed.emit()
    def _flush_ui_edits(self):
        if self._edit_inflight or not self._pending_ui_edits:return
        photo=next(iter(self._pending_ui_edits));value=self._pending_ui_edits.pop(photo)
        if not self._edit(photo,{'edits':value['edits']},value['revision']):
            self._pending_ui_edits[photo]=value;self._edit_timer.start()
    @pyqtSlot()
    def rotate(self):edits=self.edits;edits['rotation']=(edits.get('rotation',0)+90)%360;self._stage_ui_edits(edits)
    @pyqtSlot(str)
    def setCrop(self,value):
        edits=self.edits
        if value=='original':edits['crop']=None
        else:
            ratio={'1:1':1,'3:2':1.5,'4:3':4/3}.get(value)
            if not ratio or not self.reviewHeight:return
            current=self.reviewWidth/self.reviewHeight;w=min(1,ratio/current);h=min(1,current/ratio);edits['crop']=[(1-w)/2,(1-h)/2,w,h]
        self._stage_ui_edits(edits)
    @pyqtSlot(int)
    def selectHistory(self,revision):
        photo=self.currentId;library=self._library
        if photo:self._submit('history',lambda:self.catalog.set_history(library,photo,revision))
    @pyqtSlot(int)
    def historyPage(self,before):
        photo=self.currentId;library=self._library;generation=self._generation
        if photo:self._submit('history_page',lambda:{'generation':generation,'items':self.catalog.history(library,photo,before if before>=0 else None)})
    @pyqtSlot()
    def undo(self):
        photo=self.currentId;library=self._library
        if photo:self._submit('history',lambda:self.catalog.navigate_history(library,photo,'undo'))
    @pyqtSlot()
    def redo(self):
        photo=self.currentId;library=self._library
        if photo:self._submit('history',lambda:self.catalog.navigate_history(library,photo,'redo'))

    @pyqtSlot(int,float)
    def setWhiteBalanceChannel(self,channel,value):
        if channel not in (0,1,2)or not .1<=value<=8:return
        edits=self.edits;gains=list(edits.get('wb',[1.,1.,1.]));gains[channel]=round(value,3)
        edits['wb']=gains;edits.pop('temperature',None);edits.pop('tint',None);self._stage_ui_edits(edits)

    @pyqtSlot(int,float)
    def setCurvePoint(self,index,value):
        if index not in (1,2,3):return
        edits=self.edits;curve=copy.deepcopy(edits.get('curve',[[0,0],[.25,.25],[.5,.5],[.75,.75],[1,1]]))
        if len(curve)!=5:curve=[[0,0],[.25,.25],[.5,.5],[.75,.75],[1,1]]
        curve[index][1]=round(max(curve[index-1][1],min(curve[index+1][1],value)),3)
        edits['curve']=curve;self._stage_ui_edits(edits)
    @pyqtSlot()
    def resetCurve(self):
        edits=self.edits;edits['curve']=[[0,0],[.25,.25],[.5,.5],[.75,.75],[1,1]];self._stage_ui_edits(edits)

    @pyqtSlot(float,float)
    def pickWhiteBalance(self,u,v):
        if not self.currentId or not 0<=u<=1 or not 0<=v<=1 or self._wb_pending:return
        asset=copy.deepcopy(self._current);generation=self._generation;edits=self.edits;rotation=edits.get('rotation',0)
        if rotation==90:u,v=v,1-u
        elif rotation==180:u,v=1-u,1-v
        elif rotation==270:u,v=1-v,u
        crop=edits.get('crop')or[0,0,1,1];u=crop[0]+u*crop[2];v=crop[1]+v*crop[3]
        width=self.reviewWidth;height=self.reviewHeight;rw=min(9,width);rh=min(9,height)
        region=[max(0,min(width-rw,int(u*width)-rw//2)),max(0,min(height-rh,int(v*height)-rh//2)),rw,rh]
        self._wb_pending=True;self._message='正在取未编辑的线性 RGB 灰点（未标定）…';self.changed.emit()
        self._wanted=OrderedDict((key,value)for key,value in self._wanted.items()if not key.startswith('thumb:'))
        def sample():
            processing=importlib.import_module('image_processing');path=self.catalog.cache/('wb-'+uuid.uuid4().hex+'.tif')
            params={'sources':[asset],'photo_uuid':asset['photo_uuid'],'region':region,'max_edge':0,'linear_tiff':True,'wb':[1.,1.,1.],'exposure':0.,'contrast':1.,'saturation':1.,'rotation':0,'crop':None,'noise':0.,'sharpen':0.}
            try:
                processing.run_job('develop_tiff16',getattr(self.camera,'_client',None),asset['original_request_uuid'],params,str(path),cancel_event=self._image_cancel,work_dir=str(self.catalog.root/'processing-scratch'))
                image=QImage(str(path));values=[]
                for y in range(image.height()):
                    for x in range(image.width()):
                        color=image.pixelColor(x,y);rgb=(color.redF(),color.greenF(),color.blueF())
                        if all(.005<value<.995 for value in rgb):values.append(rgb)
                if not values:raise CatalogError('所选灰点过暗或已溢出，请选择另一中性区域')
                r,g,b=[sum(row[channel]for row in values)/len(values)for channel in range(3)]
                return {'photo_uuid':asset['photo_uuid'],'generation':generation,'wb':[max(.1,min(8,g/r)),1.,max(.1,min(8,g/b))]}
            finally:path.unlink(missing_ok=True)
        if not self._submit('white_balance',sample):self._wb_pending=False

    @pyqtSlot()
    def refreshStyles(self):
        def load():
            styles=importlib.import_module('photo_styles');document=styles.load(self.catalog.root/'photo-styles.json')
            return [dict(style,builtin=True)for style in styles.BUILTINS]+[dict(style,builtin=False)for style in document['styles']]
        self._submit('styles',load)
    @pyqtSlot()
    def reloadStyles(self):self.refreshStyles()
    @pyqtSlot(str)
    def saveStyle(self,name):
        params={key:copy.deepcopy(value)for key,value in self.edits.items()if key not in {'rotation','crop'}}
        if not self.currentId:self._message='请先选择照片';self.changed.emit();return
        def save():
            styles=importlib.import_module('photo_styles');path=self.catalog.root/'photo-styles.json';document=styles.load(path)
            document['styles'].append({'id':str(uuid.uuid4()),'name':name,'params':params});styles.save(path,document)
            return True
        self._submit('style_saved',save)
    @pyqtSlot(str)
    def deleteStyle(self,identity):
        def remove():
            styles=importlib.import_module('photo_styles');path=self.catalog.root/'photo-styles.json';document=styles.load(path)
            document['styles']=[style for style in document['styles']if style['id']!=identity];styles.save(path,document);return True
        self._submit('style_saved',remove)
    @pyqtSlot(str)
    def applyStyle(self,identity):
        style=next((style for style in self._styles if style['id']==identity),None)
        if not style:return
        if self._selection_count:self._batch('metadata',{'changes':{'edits':copy.deepcopy(style['params'])},'preserve_geometry':True})
        else:
            params=copy.deepcopy(style['params']);params.update(rotation=self.edits.get('rotation',0),crop=self.edits.get('crop'))
            self._stage_ui_edits(params)

    def processing_inputs(self):
        if self._selection:
            if len(self._selection_inputs)>16:raise CatalogError('图像计算最多选 16 张，请缩小选择')
            return list(self._selection_inputs)
        return [dict(self._current)] if self.currentId else []

    @pyqtSlot(str)
    def setCompare(self,photo):
        asset=self._rows.get(photo) or next((row for row in self._selection_inputs if row['photo_uuid']==photo),None)
        if asset:
            self._compare_asset=asset;self._compare_size=(asset.get('display_width',asset['width']),asset.get('display_height',asset['height']))
            self._request_image('compare',asset,region=[0,0,min(640,self._compare_size[0]),min(480,self._compare_size[1])])
    @pyqtSlot(int,int,int,int)
    def loadTile(self,x,y,width,height):
        if self.currentId:self._request_image('tile',self._current,region=[x,y,min(1024,width),min(768,height)])
    @pyqtSlot(int,int,int,int)
    def loadCompareTile(self,x,y,width,height):
        if self._compare_asset:self._request_image('compare',self._compare_asset,region=[x,y,min(1024,width),min(768,height)])

    def _request_image(self,kind,asset,region=None):
        key=kind+':'+asset['photo_uuid'];self._image_generation+=1
        token=(self._generation,self._image_generation,asset['library_id']);self._image_requests[key]=token
        # Retain request identities only for the bounded pending queue/current
        # model, so visiting thousands of pages cannot grow a GUI cache.
        keep={kind+':'+row for kind in ('thumb','review','tile','compare')for row in self._rows}
        for row in (self.currentId,self._compare_asset.get('photo_uuid','')):
            if row:keep.update(kind+':'+row for kind in ('review','tile','compare'))
        keep.update(self._wanted);keep.add(key)
        for previous in list(self._image_requests):
            if previous not in keep:self._image_requests.pop(previous,None)
        self._wanted[key]=(kind,copy.deepcopy(asset),region,token)
        while len(self._wanted)>24:self._wanted.popitem(last=False)
        self._dispatch_image()
    @pyqtSlot(str)
    def requestThumbnail(self,photo):
        if photo in self._rows and not self._rows[photo].get('thumbUrl'):self._request_image('thumb',self._rows[photo])
    def _dispatch_image(self):
        if self._image_pending or not self._wanted or self._closed:return
        # Current review/tiles always outrank thumbnail prefetch.
        key=next((k for k in self._wanted if not k.startswith('thumb:')),next(iter(self._wanted)))
        if key.startswith('thumb:')and(self._wb_pending or self.tasks.activeJobId):return
        kind,asset,region,generation=self._wanted.pop(key);self._image_pending=True
        future=self._images.submit(self._render_image,kind,asset,region)
        def done(result):
            try:image=result.result()
            except Exception as exc:image=str(exc)
            if not self._closed:self._image.emit(kind,asset['photo_uuid'],generation,image)
        future.add_done_callback(done)
    def _render_image(self,kind,asset,region):
        client=getattr(self.camera,'_client',None)
        if client is None:raise CatalogError('资料库未连接')
        if client.describe_library()['library_id']!=asset['library_id']:raise CatalogError('正在等待原资料库，未读取另一资料库')
        max_edge=256 if kind=='thumb' else 1024
        params=dict(asset.get('edits',{}),photo_uuid=asset['photo_uuid'],max_edge=max_edge,sources=[asset])
        if region is not None:params['region']=region;params['max_edge']=0;params['rotation']=0;params['crop']=None
        digest=hashlib.sha256(_json([asset['library_id'],asset['photo_uuid'],asset.get('edit_revision',0),kind,params]).encode()).hexdigest()
        target=self.catalog.cache/(digest+('.tif' if region is not None else '.jpg'))
        if not target.exists():
            try:
                processing=importlib.import_module('image_processing')
                result=processing.run_job('develop_tiff16' if region is not None else 'develop_jpeg',client,asset['original_request_uuid'],params,str(target),cancel_event=self._image_cancel,work_dir=str(self.catalog.root/'processing-scratch'))
                for source in result.get('input_sources',[]):
                    if source.get('sha256'):self.catalog.record_fingerprint(asset['library_id'],source['photo_uuid'],source['sha256'])
            except Exception:
                if self._image_cancel.is_set():raise
                if asset.get('media_type')!='image/x-adobe-dng':raise
                from image_pipeline import render_retained
                class Bound:
                    def open_capture(self,request):return client.open_library_asset(asset['photo_uuid'])
                packet=render_retained(Bound(),asset['original_request_uuid'],region=tuple(region) if region else None,max_size=(max_edge,max_edge))
                image=packet['image']
                if not image.save(str(target),'TIFF' if region is not None else 'JPEG'):raise CatalogError('无法写入预览缓存')
        image=QImage(str(target))
        if image.isNull():raise CatalogError('图像解码失败')
        self.catalog.touch_derivative(digest,asset,kind,target)
        return image

    @pyqtSlot(str,object,object)
    def _accept(self,kind,value,error):
        self._metadata_pending=max(0,self._metadata_pending-1)
        if kind=='sync':self._sync_pending=False
        if kind=='page':self._page_pending=False
        if error:
            if kind=='edited':self._edit_inflight=False
            if kind=='queued':self._busy=bool(self._mutations)
            if kind=='white_balance':self._wb_pending=False;self._dispatch_image()
            self._message=str(error);self.changed.emit();return
        if kind=='sync':
            if self._library and self._library!=value['library_id']:
                self._current={};self._review=self._tile=self._compare='';self._selection='';self._selection_count=0;self._selection_inputs=[];self._generation+=1
            self._available=True;self._library=value['library_id'];self._message=('资料库已同步 · ' if value['scan_complete'] else '正在索引 · ')+str(value['count'])+' 张'
            if value['changed'] or not self._items:self._load_page()
            if value['more'] or self._sync_again:self._sync_again=False;QTimer.singleShot(0,self.sync)
        elif kind=='page':
            if value['query']!=self._query() or value['cursor']!=self._cursor:self._load_page();return
            self._items=value['items'];self._rows={r['photo_uuid']:r for r in self._items};self._total=value['count'];self._next=value['next_cursor'];self._model.replace(self._items)
            self._wanted=OrderedDict((k,v) for k,v in self._wanted.items() if not k.startswith('thumb:'))
            for asset in self._items[:12]:self._request_image('thumb',asset)
        elif kind in {'select','edited'}:
            if kind=='edited':self._edit_inflight=False
            photo=value['photo']['photo_uuid']
            if photo in self._pending_ui_edits:self._pending_ui_edits[photo]['revision']=value['photo']['metadata_revision']
            if kind=='edited' and photo in self._rows:
                self._rows[photo].update(value['photo']);self._model.patch(photo,value['photo']);self._request_image('thumb',value['photo'])
            if value['generation']!=self._generation:
                self._flush_ui_edits();return
            self._current=value['photo'];self._history=value['history'];self._request_image('review',self._current)
            if self.currentId in self._pending_ui_edits:self._current['edits']=copy.deepcopy(self._pending_ui_edits[self.currentId]['edits'])
            self._flush_ui_edits()
        elif kind=='selection':self._selection=value['selection_id'];self._selection_count=value['count'];self._selection_inputs=value.get('inputs',[]);self._load_page()
        elif kind=='queued':
            if isinstance(value,dict):
                job_id=value['job_id']
                if value.get('rejected'):
                    self._mutations.discard(job_id);self._busy=bool(self._mutations);self._message=value['rejected'];self.changed.emit();return
            else:job_id=value
            self._message='任务已保存：'+str(job_id);self.tasks.refresh()
        elif kind=='history':self._load_details(self.currentId)
        elif kind=='history_page':
            if value['generation']==self._generation:self._history=value['items']
        elif kind=='browse':self._browse_path=value['path'];self._browse_entries=value['entries']
        elif kind=='styles':self._styles=value
        elif kind=='style_saved':self._message='自定义照片风格已保存';self.refreshStyles()
        elif kind=='white_balance':
            self._wb_pending=False
            if value['generation']==self._generation and value['photo_uuid']==self.currentId:
                edits=self.edits;edits['wb']=value['wb'];edits.pop('temperature',None);edits.pop('tint',None);self._stage_ui_edits(edits)
                self._message='灰点白平衡已应用：线性 RGB 域，未标定。'
            self._dispatch_image()
        self.changed.emit()
    @pyqtSlot(str,str,object,object)
    def _accept_image(self,kind,photo,generation,image):
        self._image_pending=False
        if isinstance(image,str):self._message=image
        elif kind=='thumb' and generation[2]==self._library and self._image_requests.get(kind+':'+photo)==generation:
            url=self.imageProvider.publish('thumb',image)
            if photo in self._rows:self._rows[photo]['thumbUrl']=url;self._model.patch(photo,{'thumbUrl':url})
        elif generation[0]==self._generation and self._image_requests.get(kind+':'+photo)==generation:
            url=self.imageProvider.publish(kind,image)
            if kind=='review' and photo==self.currentId:self._review=url
            elif kind=='tile' and photo==self.currentId:self._tile=url;self._tile_size=(image.width(),image.height())
            elif kind=='compare' and photo==self._compare_asset.get('photo_uuid'):self._compare=url
        self.changed.emit();self._dispatch_image()
    @pyqtSlot(str,object)
    def _task_completed(self,job_id,result):
        self._mutations.discard(job_id);self._busy=bool(self._mutations)
        for item in result.get('results',[]) if isinstance(result,dict) else []:
            if isinstance(item,dict) and item.get('analysis'):self._analysis=item['analysis']
        self.sync();self._load_page()
        if self.currentId:self._load_details(self.currentId)
        if self._selection:
            selection=self._selection;library=self._library
            def refresh_selection():
                after=0
                while True:
                    with self.catalog.db(write=True) as db:
                        rows=db.execute('SELECT s.ordinal,s.photo_uuid,p.* FROM selection_items s JOIN photos p ON p.library_id=? AND p.photo_uuid=s.photo_uuid WHERE s.selection_id=? AND s.ordinal>? ORDER BY s.ordinal LIMIT 128',(library,selection,after)).fetchall()
                        if not rows:break
                        for row in rows:
                            if row['asset_state']=='purged':db.execute('DELETE FROM selection_items WHERE selection_id=? AND photo_uuid=?',(selection,row['photo_uuid']))
                            else:db.execute('UPDATE selection_items SET snapshot=? WHERE selection_id=? AND photo_uuid=?',(_json(self.catalog._row(row)),selection,row['photo_uuid']))
                        after=rows[-1]['ordinal']
                    if len(rows)<128:break
                with self.catalog.db() as db:count=db.execute('SELECT count(*) FROM selection_items WHERE selection_id=?',(selection,)).fetchone()[0]
                return {'selection_id':selection,'count':count,'inputs':[r[1] for r in self.catalog.selection_rows(selection,0,17)]}
            self._submit('selection',refresh_selection)
    def close(self):
        if self._closed:return
        pending=copy.deepcopy(self._pending_ui_edits);self._pending_ui_edits={};library=self._library
        def drain():
            for photo,value in pending.items():self.catalog.mutate(library,photo,{'edits':value['edits']})
            self.catalog.flush_outbox()
        if pending:self._metadata.submit(drain)
        self._closed=True;self._timer.stop();self._edit_timer.stop();self._wanted.clear();self._image_cancel.set()
        self._metadata.shutdown(wait=True,cancel_futures=False);self._images.shutdown(wait=True,cancel_futures=True)

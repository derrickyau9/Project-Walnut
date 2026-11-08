"""Portable user-defined photo looks. No sensor calibration or RAW mutation."""
import json
import os
from pathlib import Path
import tempfile
import uuid

from media_catalog import MediaCatalog,CatalogError,_json

FORMAT='daos.photo-styles.v1'
BUILTINS=[{'id':'natural','name':'自然','params':{'exposure':0.,'contrast':1.,'saturation':1.,'rotation':0,'crop':None}},
          {'id':'mono','name':'黑白','params':{'exposure':0.,'contrast':1.05,'saturation':0.,'rotation':0,'crop':None}},
          {'id':'punchy','name':'鲜明','params':{'exposure':0.,'contrast':1.25,'saturation':1.15,'rotation':0,'crop':None}},
          {'id':'soft','name':'柔和','params':{'exposure':0.,'contrast':.85,'saturation':.85,'rotation':0,'crop':None}}]


def validateStylesDocument(document):
    if not isinstance(document,dict)or set(document)!={'format','styles'}or document['format']!=FORMAT or not isinstance(document['styles'],list)or len(document['styles'])>100:raise CatalogError('invalid photo styles document')
    if len(_json(document).encode())>256*1024:raise CatalogError('photo styles exceed 256 KiB')
    identities=set()
    for style in document['styles']:
        if not isinstance(style,dict)or set(style)!={'id','name','params'}:raise CatalogError('invalid photo style')
        if not isinstance(style['id'],str)or str(uuid.UUID(style['id']))!=style['id']or style['id']in identities:raise CatalogError('invalid or duplicate style UUID')
        name=style['name']
        if not isinstance(name,str)or not 1<=len(name)<=80 or name!=name.strip()or any(ord(c)<32 for c in name):raise CatalogError('style name must contain 1–80 printable characters')
        MediaCatalog.validateEditParameters(style['params']);identities.add(style['id'])
    return {'valid':True,'style_count':len(identities)}


def load(path):
    path=Path(path)
    if not path.exists():return {'format':FORMAT,'styles':[]}
    if path.is_symlink():raise CatalogError('photo styles file is a symbolic link')
    with path.open('rb')as stream:raw=stream.read(256*1024+1)
    if len(raw)>256*1024:raise CatalogError('photo styles exceed size bound')
    document=json.loads(raw);validateStylesDocument(document);return document


def save(path,document):
    validateStylesDocument(document);path=Path(path);temporary=None
    if path.is_symlink()or path.parent.is_symlink():raise CatalogError('photo styles destination is redirected')
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.styles-',suffix='.tmp',delete=False)as stream:
            temporary=Path(stream.name);stream.write((_json(document)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        if os.name!='nt':
            fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:
        if temporary:temporary.unlink(missing_ok=True)

"""Bounded portable configuration archives and recoverable file publication.

No capture journal, lease, RAW file or live temporary parameter state belongs
in this format. A restored C1 bank stays passive until explicitly selected.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import uuid
from zoneinfo import ZoneInfo

BACKUP_FILES=('shell-preferences.json','shooting-workspace.json','system-preferences.json','apps/photo-styles.json')
MAX_BACKUP=64*1024*1024
MAX_FILES=20000
MAX_UPDATE=96*1024*1024
FORMAT='daos.portable-backup.v1'


def strict_json(raw):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('JSON 包含重复字段')
            value[key]=item
        return value
    def constant(_):raise ValueError('JSON 包含非有限数值')
    return json.loads(raw.decode('utf-8') if isinstance(raw,bytes) else raw,object_pairs_hook=unique,parse_constant=constant)


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(raw):return hashlib.sha256(raw).hexdigest()


def safe_path(value,*,absolute=True):
    path=Path(value)
    if absolute and not path.is_absolute():raise ValueError('请使用完整绝对路径')
    path=path.absolute()
    for part in (path,*path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('路径不能包含链接或重解析点')
    return path


def read_file(path,limit):
    path=safe_path(path)
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('文件类型或长度无效')
    with path.open('rb') as handle:
        opened=os.fstat(handle.fileno())
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('读取时文件身份变化')
        raw=handle.read(limit+1)
        after=os.fstat(handle.fileno())
    if len(raw)>limit or after.st_size!=len(raw) or after.st_mtime_ns!=opened.st_mtime_ns:raise ValueError('读取时文件发生变化')
    return raw


def sync_directory(path):
    if os.name!='posix':return
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def atomic(path,raw):
    path=safe_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex+'.tmp')
    try:
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path);sync_directory(path.parent)
    finally:temporary.unlink(missing_ok=True)


def validate_preferences(value):
    if not isinstance(value,dict) or set(value)!={'language','timezone','idle_seconds','developer_mode'}:raise ValueError('系统设置字段无效')
    if value['language'] not in ('zh_CN','en_US') or type(value['idle_seconds']) is not int or value['idle_seconds'] not in (0,30,60,120,300,600,1800):raise ValueError('系统设置值无效')
    if type(value['developer_mode']) is not bool or not isinstance(value['timezone'],str) or len(value['timezone'])>80:raise ValueError('开发模式或时区值无效')
    ZoneInfo(value['timezone'])
    return dict(value)


def allowed_document(name):
    if not isinstance(name,str):return False
    return name in BACKUP_FILES or name=='portable-media.json' or bool(re.fullmatch(r'apps/(recipes/[0-9a-f]{32}\.recipe\.json|photo-sidecars/[0-9a-f-]{36}\.daos\.json)',name))


def validate_portable(value):
    from media_catalog import MediaCatalog
    return MediaCatalog.validatePortableDocument(value)


def validate_document(name,raw):
    if not allowed_document(name):raise ValueError('配置包含不允许恢复的内容')
    limit=16*1024*1024 if name=='portable-media.json' else 262144 if name.startswith('apps/') else 65536
    if len(raw)>limit:raise ValueError('配置项超过长度限制')
    value=strict_json(raw)
    if not isinstance(value,dict):raise ValueError('配置必须是 JSON 对象')
    if name=='system-preferences.json':return encode(validate_preferences(value))
    if name=='apps/photo-styles.json':
        from photo_styles import validateStylesDocument
        validateStylesDocument(value);return raw
    if name=='shooting-workspace.json':
        from shooting_workspace import validate_state_document
        return encode(validate_state_document(value))
    if name=='shell-preferences.json':
        from shell_model import _decode
        _decode(raw);return raw
    if name=='portable-media.json':validate_portable(value);return raw
    if name.endswith('.recipe.json'):
        from daos.capability_runtime.recipes import loads_recipe
        loads_recipe(raw);return raw
    allowed={'format','request_id','rating','protected','trashed_at','edits','updated_at_unix_ms'}
    photo=Path(name).name[:-10]
    try:canonical=str(uuid.UUID(photo))
    except ValueError:raise ValueError('照片旁车名称无效') from None
    if (set(value)-allowed or value.get('format')!='daos.photo-sidecar.v1' or value.get('request_id')!=canonical
            or type(value.get('rating')) is not int or not 0<=value['rating']<=5 or type(value.get('protected')) is not bool):raise ValueError('照片旁车字段无效')
    trashed=value.get('trashed_at')
    if trashed is not None and (type(trashed) not in (int,float) or not math.isfinite(trashed) or trashed<0):raise ValueError('回收站时间无效')
    updated=value.get('updated_at_unix_ms')
    if updated is not None and (type(updated) is not int or updated<0):raise ValueError('照片元数据更新时间无效')
    validate_portable({'format':'daos.portable-photo-metadata.v1','items':[{'sha256':'0'*64,'rating':value['rating'],'protected':value['protected'],'trashed':trashed is not None,'edits':value.get('edits')}],'unavailable_fingerprints':0})
    return raw


def _portable_snapshot(database):
    source=sqlite3.connect('file:'+database.as_posix()+'?mode=ro',uri=True)
    memory=sqlite3.connect(':memory:')
    try:
        source.backup(memory)
        columns={row[1] for row in memory.execute('PRAGMA table_info(photos)')}
        if not {'fingerprint','sidecar_json','asset_state'}<=columns:raise ValueError('媒体库版本不支持便携备份')
        rows=memory.execute("SELECT fingerprint,sidecar_json FROM photos WHERE asset_state!='purged' LIMIT ?",(MAX_FILES+1,)).fetchall()
        if len(rows)>MAX_FILES:raise ValueError('备份元数据超过限制')
        items=[];missing=0
        for fingerprint,raw in rows:
            if not fingerprint:missing+=1;continue
            value=strict_json(raw)
            items.append(dict(sha256=fingerprint,rating=value.get('rating',0),protected=value.get('protected',False),trashed=value.get('trashed_at') is not None,edits=value.get('edits')))
        return {'format':'daos.portable-photo-metadata.v1','items':items,'unavailable_fingerprints':missing}
    finally:source.close();memory.close()


def create_backup(data_root,destination,catalog=None):
    root=safe_path(data_root);destination=safe_path(destination)
    if destination.exists():raise ValueError('已有备份文件不会覆盖')
    payloads={};total=0
    def add(name,raw):
        nonlocal total
        validate_document(name,raw)
        total+=len(raw)
        if total>MAX_BACKUP or len(payloads)>=MAX_FILES:raise ValueError('备份超过64 MiB或项目数量限制')
        payloads[name]=raw
    for name in BACKUP_FILES:
        path=root/name
        if path.exists():add(name,read_file(path,262144 if name=='apps/photo-styles.json' else 65536))
    for path in sorted((root/'apps/recipes').glob('*.recipe.json')):
        add(path.relative_to(root).as_posix(),read_file(path,262144))
    database=root/'apps/media-catalog.sqlite3'
    if catalog is not None:add('portable-media.json',encode(catalog.exportPortableMetadata()))
    elif database.exists():add('portable-media.json',encode(_portable_snapshot(safe_path(database))))
    else:
        for path in sorted((root/'apps/photo-sidecars').glob('*.daos.json')):
            add(path.relative_to(root).as_posix(),read_file(path,262144))
    manifest={'format':FORMAT,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'files':[dict(path=name,bytes=len(raw),sha256=digest(raw)) for name,raw in sorted(payloads.items())]}
    if not payloads:raise ValueError('当前没有可备份的配置')
    destination.parent.mkdir(parents=True,exist_ok=True)
    created=False
    try:
        with destination.open('xb') as output:
            created=True
            with tarfile.open(fileobj=output,mode='w:gz') as archive:
                for name,raw in {'manifest.json':encode(manifest),**payloads}.items():
                    info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600
                    archive.addfile(info,io.BytesIO(raw))
            output.flush();os.fsync(output.fileno())
        sync_directory(destination.parent)
    except BaseException:
        if created:destination.unlink(missing_ok=True)
        raise
    return dict(path=str(destination),files=len(payloads),bytes=destination.stat().st_size)


def inspect_backup(path):
    path=safe_path(path)
    if not path.is_file() or path.stat().st_size>128*1024*1024:raise ValueError('备份文件无效或过大')
    documents={};total=0
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            name=PurePosixPath(member.name)
            if (len(documents)>MAX_FILES or not member.isfile() or member.size<0 or member.size>MAX_BACKUP
                    or name.is_absolute() or '..' in name.parts or str(name)!=member.name or '\\' in member.name
                    or member.name in documents or member.name!='manifest.json' and not allowed_document(member.name)):
                raise ValueError('备份包含无效或重复路径')
            total+=member.size
            if total>MAX_BACKUP+2*1024*1024:raise ValueError('备份解压内容超过限制')
            documents[member.name]=archive.extractfile(member).read(member.size+1)
    if 'manifest.json' not in documents:raise ValueError('备份缺少清单')
    manifest=strict_json(documents.pop('manifest.json'))
    if not isinstance(manifest,dict) or set(manifest)!={'format','created_utc','files'} or manifest['format']!=FORMAT or not isinstance(manifest['files'],list):raise ValueError('备份清单无效')
    if not isinstance(manifest['created_utc'],str) or len(manifest['created_utc'])>64:raise ValueError('备份时间无效')
    datetime.datetime.fromisoformat(manifest['created_utc'])
    names=set()
    for row in manifest['files']:
        if (not isinstance(row,dict) or set(row)!={'path','bytes','sha256'} or not isinstance(row['path'],str)
                or row['path'] in names or row['path'] not in documents or type(row['bytes']) is not int
                or not isinstance(row['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['sha256'])):raise ValueError('备份清单项目无效')
        names.add(row['path']);raw=documents[row['path']]
        if len(raw)!=row['bytes'] or digest(raw)!=row['sha256']:raise ValueError('备份内容校验失败')
        validate_document(row['path'],raw)
    if names!=set(documents):raise ValueError('备份包含未列入清单的内容')
    return manifest,documents


def update_allowed(name):
    if not isinstance(name,str):return False
    path=PurePosixPath(name)
    if not isinstance(name,str) or path.is_absolute() or str(path)!=name or '..' in path.parts or '\\' in name:return False
    return (name.startswith(('opt/hasslefree/ui/on-device/','opt/hasslefree/camera/','opt/hasslefree/simulation/','opt/hasslefree/daos/capability_runtime/','opt/hasslefree/daos/camera_link/','opt/hasslefree/support/'))
            or name in ('opt/hasslefree/bin/hf-camerad','opt/hasslefree/bin/hf-imager','opt/hasslefree/daos/__init__.py','opt/hasslefree/camera-app-active.json','usr/share/DaOS/camera-app-installer.py','usr/local/libexec/DaOS-system-service.py','usr/local/libexec/DaOS-usb-adb-session.py','usr/local/libexec/DaOS-camera-link.py'))


def inspect_update(path,expected_hash):
    path=safe_path(path)
    if not isinstance(expected_hash,str) or not re.fullmatch('[0-9a-fA-F]{64}',expected_hash):raise ValueError('更新摘要格式无效')
    if not path.is_file() or path.stat().st_size>128*1024*1024:raise ValueError('更新文件无效或过大')
    with path.open('rb') as handle:actual=hashlib.file_digest(handle,'sha256').hexdigest()
    if actual!=expected_hash.lower():raise ValueError('更新文件 SHA-256 不匹配')
    manifest=None;payloads={};total=0;names=set()
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            if len(names)>=2048 or member.name in names or not member.isfile() or member.size<0 or member.size>MAX_UPDATE:raise ValueError('更新包含无效或重复项目')
            names.add(member.name);total+=member.size
            if total>MAX_UPDATE:raise ValueError('更新解压内容超过限制')
            if member.name=='manifest.json':
                if member.size>512*1024:raise ValueError('更新清单过大')
                manifest=strict_json(archive.extractfile(member).read(member.size+1));continue
            if not member.name.startswith('payload/') or not update_allowed(member.name[8:]):raise ValueError('更新包含不允许的路径')
            hasher=hashlib.sha256();length=0;header=b''
            with archive.extractfile(member) as handle:
                for block in iter(lambda:handle.read(1024*1024),b''):
                    if not header:header=block[:64]
                    hasher.update(block);length+=len(block)
            payloads[member.name[8:]]=dict(bytes=length,sha256=hasher.hexdigest(),tar_mode=member.mode,header=header)
    if (not isinstance(manifest,dict) or manifest.get('format')!='DaOS-camera-app-v1'
            or not re.fullmatch('camera-experience-[0-9]{3}',str(manifest.get('version',''))) or not isinstance(manifest.get('files'),list)):raise ValueError('更新清单格式无效')
    listed=set()
    for row in manifest['files']:
        if not isinstance(row,dict) or set(row)!={'path','bytes','sha256','mode'}:raise ValueError('更新文件记录无效')
        name=row['path']
        if not isinstance(name,str) or name in listed or name not in payloads or not update_allowed(name):raise ValueError('更新清单路径无效')
        listed.add(name);entry=payloads[name]
        mode=0o755 if name.startswith('opt/hasslefree/bin/') else 0o644
        if (type(row['bytes']) is not int or row['bytes']!=entry['bytes'] or row['sha256']!=entry['sha256']
                or type(row['mode']) is not int or row['mode']!=mode or entry['tar_mode']!=mode):raise ValueError('更新文件长度、权限或摘要不一致')
    if listed!=set(payloads) or not listed:raise ValueError('更新文件清单不完整')
    if not {'opt/hasslefree/ui/on-device/main.py','opt/hasslefree/ui/on-device/Main.qml'}<=listed:raise ValueError('更新缺少完整应用入口')
    if manifest['version']>='camera-experience-004':
        for name in ('opt/hasslefree/bin/hf-camerad','opt/hasslefree/bin/hf-imager'):
            header=payloads.get(name,{}).get('header',b'')
            if len(header)<64 or header[:7]!=b'\x7fELF\x02\x01\x01' or header[7]!=9 or int.from_bytes(header[18:20],'little')!=183:raise ValueError('更新缺少匹配的 FreeBSD ARM64 原生程序')
    return dict(path=str(path),sha256=actual,version=manifest['version'],files=len(listed),bytes=total)


def _transaction_root(root):
    path=safe_path(root/'apps/restore-transactions');path.mkdir(parents=True,exist_ok=True);return path


def _cleanup_stage(root,stage):
    base=_transaction_root(root).resolve()
    resolved=stage.resolve()
    if resolved.parent!=base or not re.fullmatch('[0-9a-f]{32}',resolved.name):raise ValueError('恢复暂存目录身份无效')
    safe_path(stage);shutil.rmtree(resolved);sync_directory(base)


def _publish_stage(root,stage,journal,forward):
    for index,row in enumerate(journal['files']):
        name=row['path'];target=safe_path(root/name)
        if not allowed_document(name) or name=='portable-media.json':raise ValueError('恢复日志路径无效')
        expected=row['after_sha256'] if forward else row['before_sha256']
        current=digest(read_file(target,MAX_BACKUP)) if target.exists() else None
        if current not in (row['before_sha256'],row['after_sha256']):raise ValueError('恢复期间配置被外部修改，恢复日志已保留')
        if expected is None:
            target.unlink(missing_ok=True);sync_directory(target.parent);continue
        raw=read_file(stage/('after' if forward else 'before')/str(index),MAX_BACKUP)
        if digest(raw)!=expected:raise ValueError('恢复暂存内容校验失败')
        atomic(target,raw)


def _ledger_committed(root,operation_id,catalog=None):
    if catalog is not None:return catalog.portableRestoreCommitted(operation_id)
    database=safe_path(root/'apps/media-catalog.sqlite3')
    if not database.is_file():raise ValueError('恢复涉及媒体库，但媒体库无法读取')
    connection=sqlite3.connect('file:'+database.as_posix()+'?mode=ro',uri=True)
    try:return connection.execute('SELECT 1 FROM portable_restores WHERE operation_id=?',(operation_id,)).fetchone() is not None
    finally:connection.close()


def recover_restore_transactions(data_root,catalog=None):
    root=safe_path(data_root);base=root/'apps/restore-transactions'
    if not base.exists():return []
    safe_path(base);results=[]
    for stage in sorted(base.iterdir()):
        if not stage.is_dir() or not re.fullmatch('[0-9a-f]{32}',stage.name):raise ValueError('恢复目录包含未知项目')
        path=stage/'journal.json'
        if not path.exists():_cleanup_stage(root,stage);continue
        journal=strict_json(read_file(path,4*1024*1024))
        if (not isinstance(journal,dict) or set(journal)!={'format','id','phase','files','metadata'} or journal['format']!='daos.config-restore.v1'
                or journal['id']!=stage.name or journal['phase'] not in ('prepared','applying','metadata','complete') or not isinstance(journal['files'],list) or len(journal['files'])>MAX_FILES
                or type(journal['metadata']) is not bool):raise ValueError('恢复日志格式无效')
        seen=set()
        for row in journal['files']:
            if (not isinstance(row,dict) or set(row)!={'path','before_sha256','after_sha256'} or not allowed_document(row.get('path',''))
                    or row.get('path')=='portable-media.json' or row.get('path') in seen or row.get('after_sha256') is None
                    or any(value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)) for value in (row['before_sha256'],row['after_sha256']))):raise ValueError('恢复日志项目无效')
            seen.add(row['path'])
        forward=journal['phase']=='complete' or journal['phase']=='metadata' and journal['metadata'] and _ledger_committed(root,stage.name,catalog)
        _publish_stage(root,stage,journal,forward)
        results.append(dict(id=stage.name,result='completed' if forward else 'rolled_back'))
        _cleanup_stage(root,stage)
    return results


def restore_documents(data_root,documents,catalog=None,*,developer_mode=True,fault=None):
    root=safe_path(data_root);recover_restore_transactions(root,catalog)
    canonical={name:validate_document(name,raw) for name,raw in documents.items()}
    if 'system-preferences.json' in canonical:
        preferences=strict_json(canonical['system-preferences.json']);preferences['developer_mode']=bool(developer_mode)
        canonical['system-preferences.json']=encode(preferences)
    portable=strict_json(canonical.pop('portable-media.json')) if 'portable-media.json' in canonical else None
    if portable is not None:
        if catalog is None:raise ValueError('媒体库未就绪，未恢复任何配置')
        plan=catalog.validatePortableMetadata(portable)
        if plan.get('conflicts') or plan.get('valid') is False:raise ValueError('媒体元数据存在冲突，未恢复任何配置')
    if sum(map(len,canonical.values()))>MAX_BACKUP:raise ValueError('恢复内容超过限制')
    stage=_transaction_root(root)/uuid.uuid4().hex;stage.mkdir(mode=0o700);sync_directory(stage.parent)
    rows=[]
    try:
        for index,(name,raw) in enumerate(sorted(canonical.items())):
            target=safe_path(root/name);before=read_file(target,MAX_BACKUP) if target.exists() else None
            if before is not None:atomic(stage/'before'/str(index),before)
            atomic(stage/'after'/str(index),raw)
            rows.append(dict(path=name,before_sha256=digest(before) if before is not None else None,after_sha256=digest(raw)))
        journal=dict(format='daos.config-restore.v1',id=stage.name,phase='prepared',files=rows,metadata=portable is not None)
        atomic(stage/'journal.json',encode(journal))
        if fault:fault('prepared')
        journal['phase']='applying';atomic(stage/'journal.json',encode(journal))
        _publish_stage(root,stage,journal,True)
        if fault:fault('files-applied')
        metadata_result={}
        if portable is not None:
            journal['phase']='metadata';atomic(stage/'journal.json',encode(journal))
            result=catalog.restorePortableMetadata(portable,operation_id=stage.name)
            metadata_result=result
            if result.get('conflicts'):raise ValueError('媒体数据发生冲突，配置已回退')
            if fault:fault('metadata-applied')
        journal['phase']='complete';atomic(stage/'journal.json',encode(journal))
        if fault:fault('committed')
        try:_cleanup_stage(root,stage)
        except OSError:return dict(message='配置已恢复，恢复暂存将在下次启动清理',files=len(rows),metadata=portable is not None,metadata_result=metadata_result,cleanup_pending=True)
        return dict(message='配置已恢复；开发权限保持本机设置',files=len(rows),metadata=portable is not None,metadata_result=metadata_result)
    except Exception:
        recovered=recover_restore_transactions(root,catalog)
        if any(row['id']==stage.name and row['result']=='completed' for row in recovered):
            return dict(message='配置已恢复，提交结果已通过事务记录核实',files=len(rows),metadata=portable is not None)
        raise
    except BaseException:
        # A simulated process death keeps the durable decision for next launch.
        raise

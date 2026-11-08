#!/usr/bin/env python3
"""Install source-bound DaOS release facts and a Python-free read-only CLI."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

HERE=Path(__file__).resolve().parent


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def relative(value):
    if (not isinstance(value,str) or not value or any(ord(c)<32 or ord(c)==127 for c in value)
            or '\\' in value or value.startswith('/') or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value))!=value):
        raise ValueError('Canonical relative package path required: '+str(value))
    return value


def target(stage,name):
    path=stage/relative(name)
    for parent in path.parents:
        if parent==stage.parent:break
        if parent.is_symlink():raise ValueError('Redirected staging parent')
    if os.path.lexists(path):raise ValueError('Package would overwrite an existing entry: '+name)
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


def verify_entry(stage,entry):
    name=relative(entry['path']);path=stage/name
    for parent in path.parents:
        if parent==stage.parent:break
        if parent.is_symlink():raise ValueError('Redirected component parent: '+name)
    if entry.get('type') in ('file','f'):
        if path.is_symlink() or not path.is_file() or sha(path)!=entry['sha256']:
            raise ValueError('Component file is missing or changed: '+name)
        return 'f',entry['sha256'],name
    if entry.get('type') in ('link','l'):
        if not path.is_symlink():raise ValueError('Component link missing: '+name)
        link=os.readlink(path)
        if any(ord(c)<32 for c in link) or link!=entry.get('link'):
            raise ValueError('Component link changed: '+name)
        return 'l',hashlib.sha256(link.encode()).hexdigest(),name
    raise ValueError('Only files and literal links belong in component manifests')


def install(args):
    if os.path.lexists(args.manifest):raise ValueError('Create-only component manifest already exists')
    stage=args.destdir.resolve(strict=True)
    if stage==Path('/') or args.destdir.is_symlink():raise ValueError('Private DESTDIR required')
    release=json.loads(args.release.read_text(encoding='utf-8'))
    if not isinstance(release,dict) or not isinstance(release.get('version'),str) or not release['version']:
        raise ValueError('Release object with a real version is required')
    # Base provenance is a fact supplied by the source-build coordinator.
    base=release.get('base',{})
    if (not isinstance(base,dict) or base.get('name')!='NetBSD'
            or not isinstance(base.get('version'),str) or not base['version']):
        raise ValueError('Explicit NetBSD source-base name/version required')
    if not isinstance(release.get('profile'),(dict,str)) or not release['profile']:
        raise ValueError('Explicit build profile required')
    entries={};components=[]
    for source in args.component_manifest:
        record=json.loads(source.read_text(encoding='utf-8'))
        if record.get('format')!='DaOS-BSD-component-manifest-v1' or not isinstance(record.get('name'),str):
            raise ValueError('Source-bound component manifest required: '+str(source))
        target_info=record.get('target',{})
        if target_info.get('system')!='NetBSD' or target_info.get('architecture')!='aarch64':
            raise ValueError('Only NetBSD/aarch64 components can enter this image')
        components.append({'name':record['name'],'version':str(record.get('version','unknown')),
                           'manifest_sha256':sha(source),'target':record.get('target'),
                           'build_evidence_sha256':record.get('build_evidence_sha256')})
        for entry in record['files']:
            row=verify_entry(stage,entry)
            if row[2] in entries and entries[row[2]]!=row:raise ValueError('Conflicting package path')
            entries[row[2]]=row
    components.append({'name':'daos-core','version':'0.1.0','source_sha256':{
        'install.py':sha(HERE/'install.py'),'daosctl':sha(HERE/'daosctl'),'recipe.json':sha(HERE/'recipe.json')}})
    release=dict(release,format='DaOS-BSD-release-v1',components=components,
                 CLI_scope='Read-only identity and installed-file hashes; not an updater or repair tool')
    files=[]
    def put(data,name,mode=0o644):
        path=target(stage,name)
        with path.open('xb') as output:output.write(data)
        path.chmod(mode)
        checksum=sha(path);entries[name]=('f',checksum,name)
        files.append({'path':name,'type':'file','bytes':len(data),'sha256':checksum,'mode':mode})
    put((HERE/'daosctl').read_bytes(),'usr/sbin/daosctl',0o755)
    put((json.dumps(release,sort_keys=True,indent=2)+'\n').encode(),'usr/share/daos/release.json')
    sourceversion={'product_version':release['version'],'base':release['base']}
    for key in ('source','source_lock_sha256','build_stage_receipts'):
        if key in release:sourceversion[key]=release[key]
    put((json.dumps(sourceversion,sort_keys=True,indent=2)+'\n').encode(),'usr/share/daos/sourceversion.json')
    put((json.dumps(release['profile'],sort_keys=True,indent=2)+'\n').encode(),'usr/share/daos/profile.json')
    lines=['name\tversion\tcomponent-manifest-or-source-sha256']
    for component in components:
        name=component['name'];version=component['version']
        if any(c in name+version for c in '\t\r\n'):raise ValueError('Bounded component facts required')
        checksum=component.get('manifest_sha256') or component['source_sha256']['daosctl']
        lines.append(name+'\t'+version+'\t'+checksum)
    put(('\n'.join(lines)+'\n').encode(),'usr/share/daos/components.tsv')
    rows=['# DaOS-file-manifest-v1']+[ '\t'.join(row) for name,row in sorted(entries.items()) ]
    manifest=target(stage,'usr/share/daos/files.sha256')
    manifest.write_text('\n'.join(rows)+'\n',encoding='utf-8');manifest.chmod(0o644)
    files.append({'path':'usr/share/daos/files.sha256','type':'file','bytes':manifest.stat().st_size,
                  'sha256':sha(manifest),'mode':0o644})
    result={'format':'DaOS-BSD-component-manifest-v1','name':'daos-core','version':'0.1.0',
            'target':{'system':'NetBSD','architecture':'aarch64'},'files':files,
            'verification_scope':{'file_count':len(entries),'manifest_self_hash':False,
                                  'base_world_files_included':False,'target_execution_claimed':False},
            'source_sha256':components[-1]['source_sha256'],'release_input_sha256':sha(args.release)}
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    with args.manifest.open('x',encoding='utf-8') as output:json.dump(result,output,indent=2,sort_keys=True);output.write('\n')
    print(json.dumps({'component':'daos-core','files':len(files),'verified_paths':len(entries),
                      'target_execution_claimed':False,'manifest':str(args.manifest)},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destdir',type=Path,required=True)
    parser.add_argument('--release',type=Path,required=True,help='JSON with version, base{name:NetBSD,version}, profile')
    parser.add_argument('--component-manifest',type=Path,action='append',default=[])
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args();install(args)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('DaOS core package: '+str(error),file=sys.stderr);raise SystemExit(1)

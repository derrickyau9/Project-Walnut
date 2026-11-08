"""Export pending ARM package metadata for separately verified runtime data.

This runs actual pkg checksum checks but never copies/hashes the old payload,
installs packages or creates a completed runtime receipt. The offline verifier
must independently match all retained bytes to declared package checksums.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
from common import confined,new_path,no_link_parents,require_freebsd,sha,validate_tree
from collect_runtime import (NAME,closure,documentation_legal_text,inventory,loader_directories,
                             package_path,query,reuse_payload_root)
from elf_dependencies import base_manifest
from runtime_profile import POLICY,curate,sdk_reason


def write_record(path,record):
    data=(json.dumps(record,indent=2,sort_keys=True)+'\n').encode()
    with Path(path).open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    return hashlib.sha256(data).hexdigest()


def disable_core_dumps():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))


def package_lists(fmt,packages):
    values={name:[] for name in packages}
    for line in query('%n\t'+fmt,maximum=64*1024**2).splitlines():
        fields=line.split('\t')
        if len(fields)!=2 or fields[0] not in values:
            raise ValueError('malformed batched package list metadata')
        if fields[1]:values[fields[0]].append(fields[1])
    return {name:sorted(set(items)) for name,items in values.items()}


def declared_candidates(packages,selected):
    import grp,pwd
    uids={};gids={};rows={};selected=set(selected)
    fmt='%n\t%Fp\t%Fs\t%Fu\t%Fg\t%Fm\t%Ft'
    if not selected or any(NAME.fullmatch(name) is None for name in selected):
        raise ValueError('explicit valid runtime package selection required')
    expression=' || '.join('%n = "'+name+'"' for name in sorted(selected))
    result=subprocess.run(['/usr/local/sbin/pkg','query','-e',expression,fmt],capture_output=True,text=True,timeout=60)
    if result.returncode or len(result.stdout.encode())>64*1024**2:
        raise RuntimeError('bounded selected-package metadata query failed')
    for line in result.stdout.splitlines():
        values=line.split('\t')
        if len(values)!=7:raise ValueError('malformed batched package file metadata')
        name=values.pop(0)
        if name not in selected:continue
        rel=package_path(values[0]);source=Path('/')/rel;no_link_parents(source)
        info=source.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise ValueError('non-file/link package entry: '+rel)
        if values[2] and values[2] not in uids:uids[values[2]]=pwd.getpwnam(values[2]).pw_uid
        if values[3] and values[3] not in gids:gids[values[3]]=grp.getgrnam(values[3]).gr_gid
        item={'path':rel,'type':'link' if stat.S_ISLNK(info.st_mode) else 'file',
              'uid':uids[values[2]] if values[2] else info.st_uid,
              'gid':gids[values[3]] if values[3] else info.st_gid,
              'mode':int(values[4],8) if values[4] else stat.S_IMODE(info.st_mode),
              'declared_checksum':values[1]}
        if item['type']=='link':
            item['link']=os.readlink(source)
            if item['link']!=values[5]:raise ValueError('installed link differs from pkg metadata: '+rel)
        else:item['bytes']=info.st_size
        if rel in rows and {k:v for k,v in rows[rel].items() if k!='packages'}!=item:
            raise ValueError('packages disagree about a shared file: '+rel)
        if rel not in rows:rows[rel]=dict(item,packages=[])
        rows[rel]['packages'].append(name)
    if len(rows)>100000 or sum(item.get('bytes',0) for item in rows.values())>6*1024**3:
        raise ValueError('declared runtime exceeds bounded entry/byte count')
    for item in rows.values():
        if item['type']=='file' and sdk_reason(item)=='sdk-documentation' and documentation_legal_text(Path('/')/item['path']):
            item['legal_text_detected']=True
    return list(rows.values())


def export(args):
    require_freebsd(args.architecture)
    disable_core_dumps()
    output=new_path(args.output,'DaOS-runtime')
    packages=inventory();dependencies=package_lists('%dn',packages)
    selected=closure(args.package,packages,dependencies.__getitem__)
    licenses=package_lists('%L',packages)
    major=platform.release().split('.')[0];target='aarch64' if args.architecture=='arm64' else 'amd64'
    for name in selected:
        if packages[name]['abi'] not in {'FreeBSD:'+major+':'+target,'FreeBSD:'+major+':*'}:
            raise ValueError('source package ABI differs from target')
        packages[name]['dependencies']=dependencies[name];packages[name]['licenses']=licenses[name]
    check=subprocess.run(['/usr/local/sbin/pkg','check','-s',*selected],capture_output=True,timeout=900)
    if check.returncode:raise ValueError('fresh source pkg check -s failed; no provisional export')
    candidates=declared_candidates(packages,selected)
    kept,omitted,statistics=curate(candidates,args.profile)
    validate_tree({item['path']:item for item in kept})
    payload=reuse_payload_root(args.payload,kept,output)
    for item in kept:
        path=confined(payload,item['path'],allow_leaf_link=item['type']=='link')
        info=path.lstat()
        if item['type']=='link':
            if not stat.S_ISLNK(info.st_mode) or os.readlink(path)!=item['link']:
                raise ValueError('transport source symlink differs from declared metadata: '+item['path'])
        elif (not stat.S_ISREG(info.st_mode) or info.st_size!=item['bytes']
              or stat.S_IMODE(info.st_mode)&0o777!=item['mode']&0o777):
            raise ValueError('transport source size/mode differs: '+item['path'])
    observed_base=base_manifest('/',args.architecture)
    hints,_=loader_directories(payload,kept)
    after=inventory()
    for name in selected:
        if any(after.get(name,{}).get(key)!=packages[name][key] for key in ('version','origin','abi','package_checksum')):
            raise ValueError('source installed package set changed during export')
    host={'system':platform.system(),'release':platform.release(),'machine':platform.machine()}
    sources={name:sha(Path(__file__).parent/name) for name in
             ('export_runtime_metadata.py','collect_runtime.py','common.py','runtime_profile.py','elf_dependencies.py')}
    result={'format':'DaOS-freebsd-runtime-provisional-v1','status':'metadata-only-unhashed',
        'architecture':args.architecture,'host':host,'source_host':host,
        'requested':args.package,'packages':[packages[name] for name in selected],
        'profile':args.profile,'curation_policy':POLICY if args.profile=='camera-runtime-v1' else {'id':'full-package-v1','version':1},
        'statistics':statistics,'omissions':omitted,'entries':kept,'bytes':statistics['kept_bytes'],
        'payload_original_path':str(payload),'payload_content_verified':False,
        'installed_checksums_verified':True,'package_scripts_run':False,
        'source_checksum_check':{'command':['pkg','check','-s',*selected],'returncode':0,
                                 'output_sha256':hashlib.sha256(check.stdout+check.stderr).hexdigest()},
        'base_dependency_manifest':observed_base,'loader_configuration':hints,
        'source_code_sha256':sources,'completed_runtime':False,
        'repository_signature_verified_by_collector':False,
        'signature_scope':'Source installed pkg check -s and metadata only. Pending payload byte/checksum and static ELF verification; no archive-signature or target-execution claim.'}
    output.mkdir(mode=0o700)
    digest=write_record(output/'provisional.json',result)
    print(json.dumps({'format':result['format'],'status':result['status'],'provisional_sha256':digest,
                      'entries':len(kept),'statistics':statistics,'completed_runtime':False}))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture',choices=('arm64','amd64'),default='arm64')
    parser.add_argument('--profile',choices=('camera-runtime-v1','full-package-v1'),required=True)
    parser.add_argument('--package',action='append',required=True)
    parser.add_argument('--payload',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:export(args)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print('DaOS_RUNTIME_EXPORT_FAILED '+str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())

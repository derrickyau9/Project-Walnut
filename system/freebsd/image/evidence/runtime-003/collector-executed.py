"""Collect a declared installed FreeBSD package closure as immutable image data.

No package install, script, trigger, service, mount or network request is run.
Only pkg-declared /usr/local files are copied; this is not a bootstrap-VM clone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys

from common import HOST_ARCH, confined, new_path, relative, require_freebsd, sha, stable_copy, validate_tree
from runtime_profile import POLICY, curate, sdk_reason
from elf_dependencies import base_manifest, verify_closure

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9+_.-]{0,150}\Z')


def reuse_payload_root(value, entries, output):
    """Validate an old payload as data only; never accept its run as successful."""
    root=Path(value).absolute()
    if '..' in root.parts or root.name!='payload' or not root.parent.name.startswith('DaOS-runtime'):
        raise ValueError('reuse requires an explicit dedicated DaOS-runtime*/payload')
    root=confined(root,'.')
    owner=os.geteuid() if hasattr(os,'geteuid') else 0
    for directory in (root.parent,root):
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner or info.st_mode & 0o022:
            raise ValueError('reused payload must be an owned, non-writable-to-others directory')
    if root.stat().st_dev!=output.parent.stat().st_dev:
        raise ValueError('reuse hardlinks require the same filesystem; no silent recopy')
    expected={item['path'] for item in entries};observed=set()
    for current,dirs,files in os.walk(root,followlinks=False):
        for name in list(dirs):
            child=Path(current)/name
            if child.is_symlink():
                observed.add(child.relative_to(root).as_posix());dirs.remove(name)
            elif child.lstat().st_mode & 0o022:
                raise ValueError('reused payload contains a writable-to-others directory')
        observed.update((Path(current)/name).relative_to(root).as_posix() for name in files)
    if observed!=expected:
        raise ValueError('reused payload file/link set differs from the fresh package profile')
    return root


def reuse_file(source, prior, destination, expected):
    """Hash installed and prior bytes, then create a new immutable alias only.

    No chmod/chown/write is performed on either input or the hardlinked inode.
    The caller must treat the new payload as read-only data thereafter.
    """
    source,prior,destination=Path(source),Path(prior),Path(destination)
    confined(source.parent,source.name);confined(prior.parent,prior.name)
    confined(destination.parent,destination.name)
    def identity(info):
        return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_mode,info.st_uid,info.st_gid
    def digest(path,*,old=False):
        before=path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_size!=expected['bytes']
                or before.st_size>512*1024**2 or stat.S_IMODE(before.st_mode)&0o777!=expected['mode']&0o777):
            raise ValueError('reused/installed file metadata differs: '+str(path))
        if old and before.st_uid!=(os.geteuid() if hasattr(os,'geteuid') else 0):
            raise ValueError('reused file is not owned by the collector: '+str(path))
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0));value=hashlib.sha256()
        try:
            if identity(before)!=identity(os.fstat(descriptor)):
                raise ValueError('reuse input changed before hashing: '+str(path))
            with os.fdopen(descriptor,'rb',closefd=False) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
            if identity(before)!=identity(os.fstat(descriptor)) or identity(before)!=identity(path.lstat()):
                raise ValueError('reuse input changed while hashing: '+str(path))
        finally:os.close(descriptor)
        return value.hexdigest(),before
    installed_hash,_=digest(source)
    prior_hash,before=digest(prior,old=True)
    if installed_hash!=prior_hash:
        raise ValueError('reused payload bytes differ from checked installed package: '+str(prior))
    destination.parent.mkdir(parents=True,exist_ok=True)
    confined(destination.parent,destination.name)
    os.link(prior,destination,follow_symlinks=False)
    if identity(before)!=identity(prior.lstat()) or identity(before)!=identity(destination.lstat()):
        raise ValueError('reused file changed while creating its new alias: '+str(prior))
    return prior_hash


def documentation_legal_text(path):
    markers=(b'copyright',b'all rights reserved',b'permission is hereby granted',b'redistribution and use')
    tail=b''
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(65536),b''):
            value=(tail+block).lower()
            if any(marker in value for marker in markers):return True
            tail=value[-64:]
    return False


def loader_directories(root, entries):
    hints=[];directories=[]
    for item in entries:
        if item['type']!='file' or not item['path'].startswith('usr/local/libdata/ldconfig/'):
            continue
        source=confined(root,item['path'])
        if source.stat().st_size>65536:raise ValueError('declared ldconfig hint is excessive')
        values=[]
        for line in source.read_text(encoding='utf-8').splitlines():
            for value in line.split('#',1)[0].split():
                if not value.startswith(('/usr/local/','/usr/lib/','/lib/')) or '$' in value or '*' in value or '..' in value.split('/'):
                    raise ValueError('ldconfig hint is not an explicit declared library directory')
                values.append(value)
        hints.append({'path':item['path'],'sha256':sha(source),'directories':values})
        directories.extend(values)
    return hints,sorted(set(directories))


def query(fmt, name=None):
    command = ['/usr/local/sbin/pkg', 'query']
    if name is None:
        command += ['-a', fmt]
    else:
        if NAME.fullmatch(name) is None:
            raise ValueError('invalid package name')
        command += ['-e', '%n = "' + name + '"', fmt]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode or len(result.stdout.encode()) > 16 * 1024**2:
        raise RuntimeError('bounded read-only pkg query failed')
    return result.stdout


def inventory():
    packages = {}
    for line in query('%n\t%v\t%o\t%q\t%R\t%X').splitlines():
        values = line.split('\t')
        if len(values) != 6 or NAME.fullmatch(values[0]) is None or values[0] in packages:
            raise ValueError('installed package metadata is malformed')
        packages[values[0]] = dict(zip(('name', 'version', 'origin', 'abi', 'repository', 'package_checksum'), values))
    return packages


def closure(requested, packages, dependencies):
    wanted, pending = set(), list(requested)
    while pending:
        name = pending.pop()
        if name in wanted:
            continue
        if name not in packages:
            raise ValueError('required installed runtime package is missing: ' + name)
        wanted.add(name)
        pending.extend(dependencies(name))
    return sorted(wanted)


def package_path(value):
    path = relative(value.removeprefix('/'))
    if not path.startswith('usr/local/') or any(part in {'.ssh', 'id_rsa', 'id_ed25519', 'credentials.json', 'auth.json'} for part in Path(path).parts):
        raise ValueError('package path is outside the permitted /usr/local runtime prefix')
    return path


def collect(args):
    require_freebsd(args.architecture)
    import grp
    import pwd
    output = new_path(args.output, 'DaOS-runtime')
    packages = inventory()
    selected = closure(args.package, packages, lambda name: query('%dn', name).splitlines())
    major = platform.release().split('.')[0]
    target = 'aarch64' if args.architecture == 'arm64' else 'amd64'
    allowed_abis = {'FreeBSD:'+major+':'+target, 'FreeBSD:'+major+':*'}
    for name in selected:
        if packages[name]['abi'] not in allowed_abis:
            raise ValueError('installed package ABI differs from the target architecture')
        check = subprocess.run(['/usr/local/sbin/pkg', 'check', '-s', name], capture_output=True, timeout=60)
        if check.returncode:
            raise ValueError('installed package checksum validation failed: ' + name)
    candidates = {}
    for name in selected:
        packages[name]['dependencies'] = sorted(query('%dn', name).splitlines())
        packages[name]['licenses'] = sorted(query('%L', name).splitlines())
        for line in query('%Fp\t%Fs\t%Fu\t%Fg\t%Fm\t%Ft', name).splitlines():
            values = line.split('\t')
            if len(values) != 6:
                raise ValueError('package file metadata is malformed')
            rel = package_path(values[0]); source = Path('/') / rel
            for parent in source.parents:
                if parent == Path('/usr/local').parent:
                    break
                if parent.is_symlink():
                    raise ValueError('linked runtime parent is refused: ' + rel)
            info = source.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise ValueError('package runtime contains a non-file/non-link entry')
            uid = pwd.getpwnam(values[2]).pw_uid if values[2] else info.st_uid
            gid = grp.getgrnam(values[3]).gr_gid if values[3] else info.st_gid
            mode = int(values[4], 8) if values[4] else stat.S_IMODE(info.st_mode)
            item = {'path': rel, 'type': 'link' if stat.S_ISLNK(info.st_mode) else 'file',
                    'uid': uid, 'gid': gid, 'mode': mode, 'declared_checksum': values[1]}
            if item['type'] == 'link':
                item['link'] = os.readlink(source)
                if item['link'] != values[5]:
                    raise ValueError('installed link differs from pkg metadata')
            else:
                item['bytes'] = info.st_size
            if rel in candidates and {key: value for key, value in candidates[rel].items() if key != 'packages'} != item:
                raise ValueError('runtime packages disagree about a shared file')
            if rel not in candidates:
                candidates[rel] = dict(item, packages=[])
            candidates[rel]['packages'].append(name)
    if len(candidates) > 100000 or sum(item.get('bytes', 0) for item in candidates.values()) > 6 * 1024**3:
        raise ValueError('runtime package closure exceeds file/byte bounds')
    for item in candidates.values():
        if item['type']=='file' and sdk_reason(item)=='sdk-documentation' and documentation_legal_text(Path('/')/item['path']):
            item['legal_text_detected']=True
    kept,omitted,statistics=curate(list(candidates.values()),args.profile)
    validate_tree({item['path']:item for item in kept})
    reuse=reuse_payload_root(args.reuse_payload,kept,output) if getattr(args,'reuse_payload',None) else None
    print(json.dumps({'event':'DaOS-runtime-profile','profile':args.profile,'statistics':statistics}),flush=True)
    if args.measure_only:
        return {'measured':True,'copied':False,'profile':args.profile,'statistics':statistics}
    output.mkdir(mode=0o700); payload = output / 'payload'; payload.mkdir()
    for item in kept:
        rel=item['path'];source, destination = Path('/') / rel, confined(payload,rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if item['type'] == 'link':
            if reuse:
                prior=confined(reuse,rel,allow_leaf_link=True)
                if not prior.is_symlink() or os.readlink(prior)!=item['link']:
                    raise ValueError('reused symlink differs from fresh package metadata: '+rel)
            destination.symlink_to(item['link'])
        else:
            if reuse:
                item['sha256']=reuse_file(source,confined(reuse,rel),destination,item)
            else:
                item['sha256'] = stable_copy(source, destination)
                destination.chmod(item['mode'] & 0o777)
    observed_base=base_manifest('/',args.architecture)
    hints,library_dirs=loader_directories(payload,kept)
    elf_closure=verify_closure(payload,kept,observed_base,args.architecture,extra_search_paths=library_dirs)
    after = inventory()
    if any({key: after[name][key] for key in ('version', 'origin', 'abi', 'package_checksum')}
           != {key: packages[name][key] for key in ('version', 'origin', 'abi', 'package_checksum')} for name in selected):
        raise ValueError('installed package set changed during collection')
    result = {'format': 'DaOS-freebsd-runtime-v2', 'architecture': args.architecture,
        'host': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
        'requested': args.package, 'packages': [packages[name] for name in selected],
        'profile':args.profile,'curation_policy':POLICY if args.profile=='camera-runtime-v1' else {'id':'full-package-v1','version':1},
        'statistics':statistics,'omissions':omitted,'entries':kept,'bytes':statistics['kept_bytes'],
        'base_dependency_manifest':observed_base,'loader_configuration':hints,'elf_dependency_check':elf_closure,
        'installed_checksums_verified': True, 'package_scripts_run': False,
        'repository_signature_verified_by_collector': False,
        'signature_scope': 'Exact installed pkg metadata and pkg check -s; independent archive/repository signature verification is not attested.'}
    if reuse:
        result['resumed_from']={'payload_path':str(reuse),'method':'freshly-validated-hardlinks',
            'prior_run_promoted':False,'prior_receipt_present':(reuse.parent/'runtime.json').is_file(),
            'prior_run_status':'not relied upon; missing or failed prior receipts are not successful runs',
            'verification':'Fresh package checks/profile, exact entry set, installed/prior byte hashes and identities, links, modes and new ELF closure.'}
    with (output/'runtime.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, sort_keys=True); stream.write('\n')
    print(json.dumps({'format': result['format'], 'architecture': args.architecture,
                      'profile':args.profile,'files': len(kept), 'packages': len(selected), 'bytes': result['bytes'],
                      'manifest_sha256': sha(output/'runtime.json')}))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture', choices=('arm64', 'amd64'), default='arm64')
    parser.add_argument('--profile',choices=('camera-runtime-v1','full-package-v1'),required=True)
    parser.add_argument('--measure-only',action='store_true',help='verify/package-query and measure policy without copying or creating output')
    parser.add_argument('--reuse-payload',help='reuse exact checked files from a previous dedicated payload via new hardlinks; never promote its run')
    parser.add_argument('--package', action='append', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        collect(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS_RUNTIME_FAILED ' + str(error), file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

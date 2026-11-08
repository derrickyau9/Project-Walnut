"""Assemble a new DaOS source-built FreeBSD UEFI/UFS image without mounting it.

Only completed source-build stages, a collected target-native package closure
and a tested native camera are admitted. This never runs pkg, package scripts,
chroot, gpart, mdconfig, or host mounts. Build must run in a native FreeBSD guest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from common import HOST_ARCH, MACHINES, confined, elf, new_path, pe, relative, require_freebsd, sha, stable_copy, validate_tree
from runtime_profile import curate
from collect_runtime import loader_directories
from elf_dependencies import base_manifest, verify_closure

HERE = Path(__file__).resolve().parent
EXCLUDED = {'__pycache__', 'evidence', 'evidence-round6', 'evidence-round7', 'node_modules', 'build', 'var'}
PRIVATE = {'.ssh','.aws','.codex','.agents','auth.json','credentials.json','id_rsa','id_ed25519'}
BASE_ANCHORS = {'bin/sh','sbin/init','lib/libc.so.7','boot/kernel/kernel','etc/rc','etc/master.passwd','METALOG'}
NATIVE_ANCHORS = {'camera/native/CMakeLists.txt','camera/native/src/core.cpp',
                  'camera/native/src/daemon.cpp','camera/native/src/dng.cpp',
                  'camera/native/src/calibration.cpp','camera/native/src/sha256.cpp'}
CTEST_TARGETS = {'native-core','native-authority','native-sha256','native-calibration'}


def validate_base_record(state, architecture):
    anchors=state.get('artifact_sha256',{})
    source=state.get('source',{})
    if (state.get('format')!='DaOS-freebsd-base-build-v2' or state.get('status')!='completed'
            or state.get('install_policy',{}).get('live_root_installed') is not False
            or state.get('target',{}).get('architecture')!=architecture
            or state.get('target',{}).get('kernel_ident')!='DaOS'
            or not BASE_ANCHORS.issubset(anchors)
            or any(re.fullmatch('[0-9a-f]{64}',str(anchors[name])) is None for name in BASE_ANCHORS)
            or re.fullmatch('[0-9a-f]{40}',str(source.get('commit',''))) is None):
        raise ValueError('a completed bound DaOS source-build record for this target is required')


def validate_native_record(record, architecture, checksum):
    ctest=record.get('ctest',{});tests=record.get('python_tests',{});sources=record.get('source_input_sha256',{})
    target_receipt=record.get('format')=='DaOS-freebsd-arm64-target-validation-v1'
    if target_receipt:
        if (architecture!='arm64' or record.get('target_runtime_validated') is not True
                or record.get('uid')!=1000 or record.get('execution_kind')!='actual_target'
                or record.get('build_kind')!='cross'
                or re.fullmatch('[0-9a-f]{64}',str(record.get('cross_evidence_sha256',''))) is None
                or record.get('cross_build',{}).get('format')!='DaOS-freebsd-arm64-cross-build-v1'
                or record.get('cross_build',{}).get('status')!='completed'
                or record.get('cross_build',{}).get('passed') is not True
                or record.get('cross_build',{}).get('target',{}).get('architecture')!='arm64'
                or record.get('cross_build',{}).get('artifacts',{}).get('hf-camerad',{}).get('sha256')!=checksum):
            raise ValueError('cross-build record lacks genuine target-runtime validation binding')
    if (record.get('format') not in {'hf-freebsd-native-build-v1','DaOS-freebsd-arm64-target-validation-v1'} or record.get('status')!='completed'
            or record.get('passed') is not True or record.get('uname',{}).get('sysname')!='FreeBSD'
            or HOST_ARCH.get(str(record.get('uname',{}).get('machine','')).lower())!=architecture
            or ctest.get('passed') is not True or ctest.get('ran')!=4
            or set(ctest.get('targets',[]))!=CTEST_TARGETS
            or tests.get('passed') is not True or type(tests.get('ran')) is not int or tests['ran']<1
            or type(tests.get('passed_count')) is not int or tests['passed_count']<1
            or not isinstance(tests.get('successful_tests'),list) or not isinstance(tests.get('skipped'),list)
            or len(tests.get('successful_tests',[]))!=tests['passed_count']
            or tests.get('ran')!=tests.get('passed_count')+len(tests.get('skipped',[]))
            or tests.get('failures')!=[] or tests.get('errors')!=[]
            or not NATIVE_ANCHORS.issubset(sources)
            or any(re.fullmatch('[0-9a-f]{64}',str(sources[name])) is None for name in NATIVE_ANCHORS)
            or record.get('artifacts',{}).get('hf-camerad',{}).get('sha256')!=checksum):
        raise ValueError('actual tested target-native camera evidence is incomplete or mismatched')


def unescape(value):
    return re.sub(r'\\([0-7]{3}|.)', lambda m: chr(int(m[1], 8)) if len(m[1]) == 3 else m[1], value)


def escape(value):
    return ''.join(chr(byte) if chr(byte) in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._+-'
                   else '\\%03o' % byte for byte in str(value).encode('utf-8'))


def account_maps(stage):
    users, groups = {}, {}
    for line in (Path(stage)/'etc/master.passwd').read_text().splitlines():
        if line and not line.startswith('#'):
            fields = line.split(':'); users[fields[0]] = int(fields[2])
    for line in (Path(stage)/'etc/group').read_text().splitlines():
        if line and not line.startswith('#'):
            fields = line.split(':'); groups[fields[0]] = int(fields[2])
    return users, groups


def read_metalog(path, stage):
    users, groups = account_maps(stage)
    entries, defaults = {}, {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split(); name = fields.pop(0)
        if name == '/unset':
            for key in fields: defaults.pop(key, None)
            continue
        attrs = dict(token.split('=', 1) for token in fields if '=' in token)
        if name == '/set':
            defaults.update(attrs); continue
        attrs = dict(defaults, **attrs)
        rel = '.' if name in ('.', './') else relative(unescape(name))
        kind = attrs.get('type')
        if kind not in {'dir', 'file', 'link'}:
            raise ValueError('unsupported source METALOG entry type')
        uid = int(attrs['uid']) if 'uid' in attrs else users[attrs['uname']]
        gid = int(attrs['gid']) if 'gid' in attrs else groups[attrs['gname']]
        item = {'path': rel, 'type': kind, 'uid': uid, 'gid': gid,
                'mode': int(attrs.get('mode', '755' if kind == 'dir' else '644'), 8), 'origin': 'source-base'}
        if kind == 'link': item['link'] = unescape(attrs['link'])
        if 'flags' in attrs: item['flags'] = attrs['flags']
        if rel in entries and entries[rel]['type'] != kind:
            raise ValueError('source METALOG changes the type of an existing entry')
        entries[rel] = item
    if not all(name in entries for name in ('.', 'bin/sh', 'sbin/init', 'boot/kernel/kernel')):
        raise ValueError('source METALOG is incomplete')
    validate_tree(entries)
    return entries


def write_mtree(tree, entries, destination):
    lines = ['#mtree 2.0']
    for rel, item in sorted(entries.items(), key=lambda pair: (pair[0].count('/'), pair[0])):
        name = '.' if rel == '.' else './' + escape(rel)
        attrs = [name, 'type=' + item['type'], 'uid=' + str(item['uid']), 'gid=' + str(item['gid']),
                 'mode=%04o' % item['mode']]
        if item['type'] == 'link': attrs.append('link=' + escape(item['link']))
        if item.get('flags') and item['flags'] not in ('none', '0'):
            if re.fullmatch(r'[a-zA-Z0-9_,]+', item['flags']) is None: raise ValueError('invalid file flags')
            attrs.append('flags=' + item['flags'])
        lines.append(' '.join(attrs))
    Path(destination).write_text('\n'.join(lines)+'\n')


def metadata(rel, kind='file', uid=0, gid=0, mode=0o644, **extra):
    return dict(path=rel, type=kind, uid=uid, gid=gid, mode=mode, origin='DaOS-overlay', **extra)


def overlay_files(folder=None):
    folder=Path(folder) if folder is not None else HERE/'overlay'
    return [source for source in sorted(folder.rglob('*')) if source.is_file() and not source.is_symlink()
            and '__pycache__' not in source.parts and source.suffix not in ('.pyc','.pyo')]


def parents(entries, rel):
    for parent in reversed(Path(rel).parents):
        name = parent.as_posix()
        if name != '.': entries.setdefault(name, metadata(name, 'dir', mode=0o755))


def install_file(root, entries, rel, data, mode=0o644):
    rel = relative(rel); parents(entries, rel); target = Path(root)/rel
    for parent in target.parents:
        if parent == Path(root).parent: break
        if parent.is_symlink(): raise ValueError('overlay parent is a symlink')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists() and not target.is_file():
        raise ValueError('overlay conflicts with a non-regular entry')
    # Source-world aliases may share an inode inside this new stage. Replace
    # the selected name before any write/chmod, including pwd_mkdb's input.
    if target.exists():target.unlink()
    with target.open('xb') as stream:stream.write(data)
    target.chmod(mode & 0o777)
    entries[rel] = metadata(rel, mode=mode)


def file_identity(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,
            info.st_mode,info.st_uid,info.st_gid)


def inode_records(tree,entries):
    """Validate intended metadata for each source inode before sharing aliases."""
    groups={};identities={}
    for rel,item in entries.items():
        if item['type']!='file':continue
        path=confined(tree,rel);info=path.lstat()
        if not stat.S_ISREG(info.st_mode):raise ValueError('manifest regular file is not regular: '+rel)
        flags=item.get('flags')
        flags='' if flags in (None,'','none','0') else flags
        intended=(item['uid'],item['gid'],item['mode'],flags)
        key=(info.st_dev,info.st_ino)
        group=groups.setdefault(key,{'metadata':intended,'paths':[],'bytes':info.st_size})
        if intended!=group['metadata']:
            raise ValueError('source hardlink aliases have conflicting intended metadata: '+group['paths'][0]+' / '+rel)
        group['paths'].append(rel);identities[rel]=file_identity(info)
    return groups,identities


def file_usage(tree,entries,exclude_prefixes=()):
    """Count each inode once within this intended filesystem, not per alias."""
    selected={rel:item for rel,item in entries.items() if not rel.startswith(exclude_prefixes)}
    groups,_=inode_records(tree,selected)
    logical=sum(group['bytes']*len(group['paths']) for group in groups.values())
    unique=sum(group['bytes'] for group in groups.values())
    return {'file_paths':sum(len(group['paths']) for group in groups.values()),
            'unique_files':len(groups),'hardlink_groups':sum(len(group['paths'])>1 for group in groups.values()),
            'logical_bytes':logical,'unique_bytes':unique,'redundant_bytes':logical-unique}


def runtime_verification_boundary(manifest,architecture):
    """Keep ARM source identity distinct from any offline static verifier."""
    source=manifest.get('source_host',manifest.get('host'))
    machines=('arm64','aarch64') if architecture=='arm64' else ('amd64','x86_64')
    if not isinstance(source,dict) or source.get('system')!='FreeBSD' or source.get('machine') not in machines:
        raise ValueError('runtime source host must identify the actual target FreeBSD architecture')
    kind=manifest.get('collection_kind','source-collector-static-ELF')
    if kind=='offline-verified-transport':
        validation=manifest.get('offline_validation',{})
        digest=manifest.get('source_provisional_sha256')
        if (manifest.get('host')!=source or manifest.get('offline_declared_checksums_verified') is not True
                or manifest.get('status')!='completed' or manifest.get('completed_runtime') is not True
                or manifest.get('payload_content_verified') is not True
                or manifest.get('target_execution_validated') is not False or manifest.get('physical_hardware_validated') is not False
                or validation.get('format')!='DaOS-freebsd-runtime-offline-validation-v1'
                or validation.get('prior_run_promoted') is not False
                or validation.get('all_retained_regular_file_declared_checksums_verified') is not True
                or validation.get('target_execution_validated') is not False or validation.get('physical_hardware_validated') is not False
                or not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('offline runtime lacks an explicit source/verification boundary')
        verifier=manifest.get('verification_host')
        if not isinstance(verifier,dict) or any(not isinstance(verifier.get(key),str) or not verifier[key] for key in ('system','release','machine')):
            raise ValueError('offline runtime must report its actual static verification host')
        for name in ('source_code_sha256','verifier_source_code_sha256'):
            hashes=manifest.get(name)
            if not isinstance(hashes,dict) or not hashes or any(not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in hashes.values()):
                raise ValueError('offline runtime source/verifier hashes are missing')
    elif kind=='source-collector-static-ELF':
        verifier=source;digest=None
    else:raise ValueError('unknown runtime collection/verification method')
    return {'collection_kind':kind,'source_host':dict(source),'verification_host':dict(verifier),
            'source_provisional_sha256':digest,'target_execution_validated':False,'physical_hardware_validated':False,
            'source_code_sha256':manifest.get('source_code_sha256',{}),
            'verifier_source_code_sha256':manifest.get('verifier_source_code_sha256',{})}


def preflight(args):
    work = new_path(args.work_dir, 'DaOS-image')
    base_state_path = Path(args.base_state).resolve(strict=True)
    state = json.loads(base_state_path.read_text())
    validate_base_record(state,args.architecture)
    base = Path(state.get('stage_directory', state.get('destination', ''))).resolve(strict=True)
    if not base.is_dir() or base == Path('/') or not (base/'METALOG').is_file():
        raise ValueError('source build has no private staged world/METALOG')
    for name, checksum in state.get('artifact_sha256', {}).items():
        if sha(base/relative(name)) != checksum: raise ValueError('source-build artifact changed')
    elf(base/'bin/sh', args.architecture); kernel = elf(base/'boot/kernel/kernel', args.architecture,kernel=True)
    loader = pe(base/'boot/loader.efi', args.architecture)
    base_entries=read_metalog(base/'METALOG',base)
    runtime = Path(args.runtime).resolve(strict=True)
    manifest = json.loads((runtime/'runtime.json').read_text())
    runtime_boundary=runtime_verification_boundary(manifest,args.architecture)
    if (manifest.get('format') != 'DaOS-freebsd-runtime-v2' or manifest.get('architecture') != args.architecture
            or manifest.get('installed_checksums_verified') is not True or manifest.get('package_scripts_run') is not False
            or manifest.get('profile') not in ('camera-runtime-v1','full-package-v1')
            or manifest.get('elf_dependency_check',{}).get('passed') is not True):
        raise ValueError('collected package runtime target or checksum policy is incompatible')
    entries = manifest.get('entries')
    omitted=manifest.get('omissions')
    if not isinstance(entries, list) or not isinstance(omitted,list) or len(entries)+len(omitted)>100000: raise ValueError('invalid runtime entry count')
    expected_kept,expected_omitted,statistics=curate(entries+omitted,manifest['profile'])
    if ({item['path'] for item in expected_kept}!={item['path'] for item in entries}
            or {item['path']:item['reason'] for item in expected_omitted}!={item['path']:item['reason'] for item in omitted}
            or statistics!=manifest.get('statistics')):
        raise ValueError('runtime curation decisions differ from the explicit versioned policy')
    seen = set()
    major = str(state['source']['release']).split('.')[0]
    target_abi = 'aarch64' if args.architecture == 'arm64' else 'amd64'
    if any(package.get('abi') not in {'FreeBSD:'+major+':'+target_abi, 'FreeBSD:'+major+':*'}
           for package in manifest.get('packages', [])):
        raise ValueError('runtime package ABI differs from this source base')
    for item in entries:
        rel = relative(item['path'])
        if rel in seen or not rel.startswith('usr/local/'): raise ValueError('ambiguous runtime path')
        seen.add(rel); path = confined(runtime/'payload',rel,allow_leaf_link=item['type']=='link')
        if item['type'] == 'file':
            if path.is_symlink() or path.stat().st_size!=item.get('bytes') or sha(path) != item['sha256']: raise ValueError('runtime file changed')
        elif item['type'] == 'link':
            if not path.is_symlink() or os.readlink(path) != item['link']: raise ValueError('runtime link changed')
        else: raise ValueError('unsupported runtime entry')
    required = ('usr/local/bin/python3.12', 'usr/local/libexec/Xorg', 'usr/local/bin/xauth',
                'usr/local/lib/xorg/modules/drivers/scfb_drv.so',
                'usr/local/lib/xorg/modules/input/libinput_drv.so')
    if not all(name in seen for name in required): raise ValueError('Python/Xorg/xauth runtime closure is incomplete')
    native = Path(args.native_command).resolve(strict=True)
    native_evidence_path = Path(args.native_evidence).resolve(strict=True)
    native_evidence = json.loads(native_evidence_path.read_text())
    validate_native_record(native_evidence,args.architecture,sha(native))
    if native_evidence.get('format')=='DaOS-freebsd-arm64-target-validation-v1':
        sysroot=native_evidence['cross_build'].get('sysroot',{})
        if (sysroot.get('base_source_commit')!=state['source']['commit']
                or sysroot.get('base_release')!=state['source']['release']):
            raise ValueError('native cross sysroot does not bind this exact source world/release')
    elf(native, args.architecture)
    project = Path(args.project).resolve(strict=True)
    for name in ('licenses/INVENTORY.json', 'licenses/README.md', 'docs/design-provenance.md', 'README.md',
                 'camera/native/third_party/nlohmann/LICENSE.MIT', 'camera/native/third_party/manifest.json',
                 'camera/native/third_party/sqlite/sqlite3.h'):
        source = project / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('required project source/notice is missing: ' + name)
    for rel, checksum in native_evidence.get('source_input_sha256', {}).items():
        if rel.startswith(('camera/native/', 'camera/calibration/fixtures/')) and sha(project/relative(rel)) != checksum:
            raise ValueError('tested native source differs from the supplied project')
    for path in (base, runtime, project):
        if work == path or work in path.parents or path in work.parents:
            raise ValueError('new image work tree must be separate from all input trees')
    base_usage=file_usage(base,base_entries,('var/','root/','home/'))
    runtime_usage=file_usage(runtime/'payload',{item['path']:item for item in entries})
    base_bytes=base_usage['unique_bytes'];runtime_bytes=runtime_usage['unique_bytes']
    if base_bytes+runtime_bytes+(len(base_entries)+len(entries))*512+20*1024**2 > args.root_mib*1024**2*0.88:
        raise ValueError('source base/runtime exceeds the root image budget before staging; increase --root-mib')
    return {'work': work, 'base': base, 'state': state, 'base_state_sha256': sha(base_state_path),
        'runtime': runtime, 'manifest': manifest, 'runtime_sha256': sha(runtime/'runtime.json'),
        'native': native, 'native_evidence': native_evidence, 'native_evidence_sha256': sha(native_evidence_path),
          'project': project, 'kernel': kernel, 'loader': loader,'runtime_verification':runtime_boundary,
          'base_file_usage':base_usage,'runtime_file_usage':runtime_usage}


def copy_base(base, root, entries):
    validate_tree(entries)
    _,identities=inode_records(base,entries)
    copied={}
    for rel, item in sorted(entries.items(), key=lambda pair: (pair[0].count('/'), pair[0])):
        target = confined(root,rel); source = confined(base,rel,allow_leaf_link=item['type']=='link')
        if item['type'] == 'dir': target.mkdir(parents=True, exist_ok=True)
        elif item['type'] == 'link':
            target.parent.mkdir(parents=True, exist_ok=True); target.symlink_to(item['link'])
        else:
            before=source.lstat()
            if file_identity(before)!=identities[rel]:raise ValueError('source base changed before staging: '+rel)
            key=(before.st_dev,before.st_ino)
            if key not in copied:
                stable_copy(source,target);copied[key]=rel
            else:
                first=confined(root,copied[key]);first_info=first.lstat()
                if not stat.S_ISREG(first_info.st_mode) or first_info.st_size!=before.st_size:
                    raise ValueError('new staged hardlink target changed: '+copied[key])
                target.parent.mkdir(parents=True,exist_ok=True)
                os.link(first,target,follow_symlinks=False)
                linked=target.lstat()
                if (linked.st_dev,linked.st_ino)!=(first_info.st_dev,first_info.st_ino):
                    raise ValueError('new staged hardlink did not share its copied inode')
    for rel,before in identities.items():
        if file_identity(confined(base,rel).lstat())!=before:
            raise ValueError('source base changed while staging: '+rel)


def prepare(value, args):
    work = value['work']; work.mkdir(mode=0o700)
    root = work/'root'; root.mkdir(); maps = read_metalog(value['base']/'METALOG', value['base'])
    copy_base(value['base'], root, maps)
    for item in value['manifest']['entries']:
        rel = item['path']; parents(maps, rel); target = confined(root,rel)
        if target.exists() or target.is_symlink(): raise ValueError('runtime overwrites a base entry')
        target.parent.mkdir(parents=True, exist_ok=True)
        if item['type'] == 'link': target.symlink_to(item['link'])
        else:
            if stable_copy(value['runtime']/'payload'/rel, target) != item['sha256']: raise ValueError('runtime changed while staging')
        maps[rel] = {key: item[key] for key in ('path', 'type', 'uid', 'gid', 'mode')}
        if item['type'] == 'link': maps[rel]['link'] = item['link']
    project_sources = {}
    for name in ('camera', 'daos', 'simulation', 'tools', 'ui'):
        for source in sorted((value['project']/name).rglob('*')):
            rel = source.relative_to(value['project'])
            if (not source.is_file() or source.is_symlink() or EXCLUDED.intersection(rel.parts)
                    or PRIVATE.intersection(rel.parts) or any(part.startswith('evidence') for part in rel.parts)
                    or rel.parts[:2] == ('camera', 'native')
                    or source.suffix not in {'.py', '.qml', '.json', '.html', '.css', '.js', '.svg'}): continue
            destination = 'opt/hasslefree/' + rel.as_posix()
            parents(maps,destination)
            target=confined(root,destination)
            project_sources[rel.as_posix()] = stable_copy(source,target)
            maps[destination]=metadata(destination)
    for rel, checksum in value['native_evidence'].get('source_input_sha256', {}).items():
        if rel.startswith(('camera/native/', 'camera/calibration/fixtures/')) and sha(value['project']/relative(rel)) != checksum:
            raise ValueError('tested native source differs from the supplied project')
    native_rel = 'usr/local/bin/hf-camerad'
    install_file(root, maps, native_rel, value['native'].read_bytes(), 0o755)
    for source in overlay_files():
        rel = source.relative_to(HERE/'overlay').as_posix()
        mode = 0o755 if rel.startswith(('usr/local/etc/rc.d/', 'usr/local/libexec/')) else 0o644
        install_file(root, maps, rel, source.read_bytes(), mode)
    notices=[]
    for source_name,target_name in (
        ('licenses/INVENTORY.json','project/INVENTORY.json'),
        ('licenses/README.md','project/LICENSE-STATUS.md'),
        ('docs/design-provenance.md','project/design-provenance.md'),
        ('README.md','project/README.md'),
        ('camera/native/third_party/nlohmann/LICENSE.MIT','nlohmann/LICENSE.MIT'),
        ('camera/native/third_party/manifest.json','native-third-party.json')):
        source=value['project']/source_name
        install_file(root,maps,'usr/share/DaOS/notices/'+target_name,source.read_bytes())
        notices.append({'source':source_name,'installed':target_name,'sha256':sha(source)})
    sqlite_source=value['project']/'camera/native/third_party/sqlite/sqlite3.h'
    sqlite=sqlite_source.read_bytes();end=sqlite.find(b'*/')
    if not sqlite.startswith(b'/*') or not 0<end<16384:raise ValueError('bundled SQLite notice block is unavailable')
    sqlite_notice=sqlite[:end+2]+b'\n'
    install_file(root,maps,'usr/share/DaOS/notices/sqlite/UPSTREAM-FIRST-COMMENT.txt',sqlite_notice)
    notices.append({'source':'camera/native/third_party/sqlite/sqlite3.h','source_sha256':sha(sqlite_source),
                    'installed':'sqlite/UPSTREAM-FIRST-COMMENT.txt','sha256':hashlib.sha256(sqlite_notice).hexdigest()})
    install_file(root,maps,'usr/share/DaOS/notices/MANIFEST.json',(json.dumps(notices,indent=2)+'\n').encode())
    accounts = (root/'etc/master.passwd').read_text().splitlines()
    groups = (root/'etc/group').read_text().splitlines()
    if any(line.startswith('camera:') or line and not line.startswith('#') and line.split(':')[2] == '1000' for line in accounts):
        raise ValueError('source base already owns the appliance UID; no account is overwritten')
    for index, line in enumerate(accounts):
        if line.startswith('root:'):
            fields=line.split(':'); fields[1]='*'; accounts[index]=':'.join(fields)
    accounts.append('camera:*:1000:1000::0:0:DaOS camera:/var/DaOS/home/camera:/usr/sbin/nologin')
    groups.append('camera:*:1000:')
    install_file(root, maps, 'etc/master.passwd', ('\n'.join(accounts)+'\n').encode(), 0o600)
    install_file(root, maps, 'etc/group', ('\n'.join(groups)+'\n').encode())
    subprocess.run(['/usr/sbin/pwd_mkdb', '-p', '-d', str(root/'etc'), str(root/'etc/master.passwd')], check=True, timeout=20)
    for name, mode in (('passwd', 0o644), ('pwd.db', 0o644), ('spwd.db', 0o600)):
        maps['etc/'+name] = metadata('etc/'+name, mode=mode)
    ttys=(root/'etc/ttys').read_text().splitlines()
    ttys=[re.sub(r'\bon\b', 'off', line) if line.startswith('ttyv8\t') or line.startswith('ttyv8 ') else line for line in ttys]
    install_file(root, maps, 'etc/ttys', ('\n'.join(ttys)+'\n').encode())
    for rel in list(maps):
        if rel.startswith(('root/', 'home/')) or rel in ('boot/entropy', 'etc/hostid') or rel.startswith('etc/ssh/ssh_host_'):
            target=root/rel
            if target.is_file() or target.is_symlink(): target.unlink()
            maps.pop(rel)
    var = work/'var'
    if (root/'var').is_symlink(): raise ValueError('source var mountpoint is redirected')
    (root/'var').rename(var); (root/'var').mkdir()
    var_maps={'.': dict(maps['var'], path='.')}
    for rel in list(maps):
        if rel.startswith('var/'):
            var_maps[rel[4:]] = dict(maps.pop(rel), path=rel[4:])
    for rel in ('DaOS', 'DaOS/camera', 'DaOS/camera/photos', 'DaOS/calibration', 'DaOS/ui',
                'DaOS/config', 'DaOS/cache', 'DaOS/home', 'DaOS/home/camera'):
        (var/rel).mkdir(parents=True, exist_ok=True)
        var_maps[rel] = metadata(rel, 'dir', uid=1000 if rel not in ('DaOS','DaOS/home') else 0,
                                 gid=1000 if rel not in ('DaOS','DaOS/home') else 0, mode=0o700 if rel not in ('DaOS','DaOS/home') else 0o755)
    summary={'format':'DaOS-appliance-payload-v1','architecture':args.architecture,
        'source_base':value['state']['source'],'base_state_sha256':value['base_state_sha256'],
        'runtime_manifest_sha256':value['runtime_sha256'],'packages':value['manifest']['packages'],
        'native_evidence_sha256':value['native_evidence_sha256'],'native_sha256':sha(value['native']),
        'project_source_sha256':project_sources,'runtime_package_registration':False,
        'runtime_profile':value['manifest']['profile'],'runtime_statistics':value['manifest']['statistics'],
        'runtime_verification':value['runtime_verification'],
        'base_file_usage':value['base_file_usage'],'runtime_file_usage':value['runtime_file_usage'],
        'scope':'Curated installed package-file closure and source-built world/kernel; no physical camera validation.'}
    source_libraries=base_manifest(root,args.architecture)
    hints,library_dirs=loader_directories(root,value['manifest']['entries'])
    if hints!=value['manifest']['loader_configuration']:
        raise ValueError('staged ldconfig configuration differs from the collected runtime')
    native_entry=metadata(native_rel,mode=0o755,sha256=sha(root/native_rel))
    source_check=verify_closure(root,value['manifest']['entries']+[native_entry],source_libraries,args.architecture,
                                extra_search_paths=library_dirs)
    check_bytes=(json.dumps(source_check,indent=2,sort_keys=True)+'\n').encode()
    install_file(root,maps,'usr/share/DaOS/source-world-elf-closure.json',check_bytes)
    install_file(root,maps,'usr/share/DaOS/runtime-curation.json',(value['runtime']/'runtime.json').read_bytes())
    summary['source_world_elf_dependency_check']={'passed':True,'sha256':hashlib.sha256(check_bytes).hexdigest(),
        'runtime_elf_count':source_check['runtime_elf_count'],'dependency_count':source_check['dependency_count']}
    install_file(root, maps, 'usr/share/DaOS/payload.json', (json.dumps(summary,indent=2)+'\n').encode())
    trees={'root':(root,maps),'var':(var,var_maps)}
    for name in ('calibration','photos'):
        tree=work/name; tree.mkdir(); trees[name]=(tree,{'.':metadata('.', 'dir',uid=1000,gid=1000,mode=0o700)})
    efi=work/'efi'; (efi/'EFI/BOOT').mkdir(parents=True)
    boot_name='BOOTAA64.EFI' if args.architecture=='arm64' else 'BOOTX64.EFI'
    stable_copy(value['base']/'boot/loader.efi', efi/'EFI/BOOT'/boot_name)
    for name,(tree,entries) in trees.items(): write_mtree(tree,entries,work/(name+'.mtree'))
    root_usage=file_usage(root,maps);root_bytes=root_usage['unique_bytes']
    if root_bytes+len(maps)*512+16*1024**2 > args.root_mib*1024**2*0.88:
        raise ValueError('curated runtime/base does not fit the explicit root image budget; increase --root-mib')
    return trees,summary


def efi_geometry(mib):
    """Explicit FAT32 table capacity includes its two reserved entries."""
    if type(mib) is not int or not 64<=mib<=8192:
        raise ValueError('the explicit FAT32 EFI geometry requires at least64MiB and at most8192MiB')
    total=mib*1024**2//512
    # Each 512-byte FAT sector holds128 entries. Two FAT copies consume two
    # volume sectors per table sector; data clusters also need entries0/1.
    fat_sectors=(total-32+2+130-1)//130
    return {'bytes_per_sector':512,'sectors_per_cluster':1,'reserved_sectors':32,
            'num_FAT':2,'sectors_per_fat':fat_sectors,'total_sectors':total}


def validate_efi_geometry(path):
    """Read-only bounds/capacity precheck; the full filesystem checker follows."""
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('EFI image is absent or redirected')
    length=path.stat().st_size
    with path.open('rb') as stream:
        boot=stream.read(512)
        if len(boot)!=512 or boot[510:512]!=b'\x55\xaa':raise ValueError('EFI FAT boot signature is invalid')
        number=lambda start,size:int.from_bytes(boot[start:start+size],'little')
        bps=number(11,2);spc=boot[13];reserved=number(14,2);copies=boot[16]
        total=number(32,4);fat_sectors=number(36,4)
        if (bps!=512 or spc!=1 or reserved!=32 or copies!=2 or number(17,2)!=0
                or number(19,2)!=0 or number(22,2)!=0 or number(42,2)!=0
                or not total or total*bps!=length or not fat_sectors):
            raise ValueError('EFI FAT32 BPB does not match the explicit generated geometry')
        data_sectors=total-reserved-copies*fat_sectors
        clusters=data_sectors//spc;capacity=fat_sectors*bps//4
        if not 65525<=clusters<=0x0ffffff4 or capacity<clusters+2:
            raise ValueError('EFI FAT cannot hold every data cluster plus two reserved entries')
        root_cluster=number(44,4);info_sector=number(48,2);backup_sector=number(50,2)
        if (not 2<=root_cluster<clusters+2 or not 0<info_sector<reserved
                or not 0<backup_sector<reserved or backup_sector+info_sector>=reserved):
            raise ValueError('EFI FAT32 reserved/root references are out of bounds')
        stream.seek(backup_sector*bps)
        if stream.read(bps)!=boot:raise ValueError('EFI backup boot sector differs')
        stream.seek(info_sector*bps);info=stream.read(bps)
        if info[:4]!=b'RRaA' or info[484:488]!=b'rrAa' or info[508:512]!=b'\x00\x00\x55\xaa':
            raise ValueError('EFI FAT32 FSInfo signatures are invalid')
    return {'passed':True,'scope':'BPB volume/cluster/FAT capacity and reserved-sector bounds; full fsck follows',
            'bytes':length,'bytes_per_sector':bps,'sectors_per_cluster':spc,'reserved_sectors':reserved,
            'num_FAT':copies,'sectors_per_fat':fat_sectors,'data_clusters':clusters,
            'fat_entry_capacity':capacity,'required_fat_entries':clusters+2}


def commands(value,args):
    work=value['work']; plan=[]
    for name,size,label in (('root',args.root_mib,'DaOS-root'),('var',args.var_mib,'DaOS-state'),
                            ('calibration',args.calibration_mib,'DaOS-calibration'),('photos',args.photos_mib,'DaOS-photos')):
        plan.append({'cwd':str(work/name),'argv':['/usr/sbin/makefs','-t','ffs','-B','little','-Z','-T',str(args.epoch),
            '-s',str(size)+'m','-o','version=2,label='+label+',softupdates=1',str(work/(name+'.ufs')),str(work/(name+'.mtree'))]})
    geometry=efi_geometry(args.efi_mib)
    fat_options='fat_type=32,'+','.join(key+'='+str(geometry[key]) for key in
        ('bytes_per_sector','sectors_per_cluster','reserved_sectors','num_FAT','sectors_per_fat'))
    plan.append({'cwd':str(work),'argv':['/usr/sbin/makefs','-t','msdos','-s',str(args.efi_mib)+'m',
        '-o',fat_options,str(work/'efi.fat'),str(work/'efi')]})
    plan.append({'cwd':str(work),'argv':['/usr/bin/mkimg','-s','gpt','-f','raw','-P','4096','-S','512',
        '-p','efi/DaOS-efi:='+str(work/'efi.fat'),'-p','freebsd-ufs/DaOS-root:='+str(work/'root.ufs'),
        '-p','freebsd-ufs/DaOS-state:='+str(work/'var.ufs'),'-p','freebsd-ufs/DaOS-calibration:='+str(work/'calibration.ufs'),
        '-p','freebsd-ufs/DaOS-photos:='+str(work/'photos.ufs'),'-o',str(work/('DaOS-'+args.architecture+'.img'))]})
    return plan


def filesystem_checks(work):
    work=Path(work)
    return [{'name':'efi','path':str(work/'efi.fat'),'argv':['/sbin/fsck_msdosfs','-M','-n',str(work/'efi.fat')]}]+[
        {'name':name,'path':str(work/(name+'.ufs')),'argv':['/sbin/fsck_ffs','-n',str(work/(name+'.ufs'))]}
        for name in ('root','var','calibration','photos')]


def validate_filesystems(work):
    """Require successful, byte-preserving native checks before GPT assembly.

    makefs msdos may return zero when mkfs_msdos rejects FAT geometry. Neither
    command status nor an allocated image length proves a valid filesystem.
    A checker crash is failure, never evidence that the image is clean.
    """
    work=Path(work);records=[]
    if sys.platform.startswith('freebsd'):
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    for check in filesystem_checks(work):
        image=Path(check['path'])
        if image.is_symlink() or not image.is_file() or not image.stat().st_size:
            raise ValueError('filesystem check input is absent/redirected: '+check['name'])
        header_validation=validate_efi_geometry(image) if check['name']=='efi' else None
        before=sha(image);log=work/('filesystem-check-'+check['name']+'.log')
        with log.open('xb') as stream:
            result=subprocess.run(check['argv'],stdout=stream,stderr=subprocess.STDOUT,timeout=300)
        after=sha(image)
        records.append(dict(check,returncode=result.returncode,bytes=image.stat().st_size,
                            sha256_before=before,sha256_after=after,unchanged=before==after,log_sha256=sha(log),
                            header_validation=header_validation))
        if result.returncode or before!=after:
            failed={'format':'DaOS-freebsd-filesystem-validation-v1','passed':False,'read_only':True,
                    'checks':records,'failure':check['name']+' checker failed or changed image bytes'}
            (work/'filesystem-validation.json').write_text(json.dumps(failed,indent=2)+'\n')
            raise ValueError('read-only filesystem validation failed before GPT assembly: '+check['name']+
                             ' (returncode='+str(result.returncode)+'); inspect '+str(log))
    report={'format':'DaOS-freebsd-filesystem-validation-v1','passed':True,'read_only':True,'checks':records}
    (work/'filesystem-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','build'))
    parser.add_argument('--architecture',choices=('arm64','amd64'),default='arm64')
    for name in ('base-state','runtime','project','native-evidence','native-command','work-dir'): parser.add_argument('--'+name,required=True)
    for name,default in (('root',1536),('var',128),('calibration',32),('photos',256),('efi',64)):
        parser.add_argument('--'+name+'-mib',type=int,default=default)
    parser.add_argument('--epoch',type=int,default=0)
    parser.add_argument('--compress',action='store_true',help='also create .img.xz with /usr/bin/xz; raw image is retained')
    args=parser.parse_args(argv)
    try:
        if any(not 16<=getattr(args,name+'_mib')<=8192 for name in ('root','var','calibration','photos','efi')) or args.epoch<0:
            raise ValueError('invalid explicit image size/time bounds')
        if args.efi_mib<64:raise ValueError('the explicit FAT32 EFI geometry requires at least64MiB')
        value=preflight(args);plan=commands(value,args)
        if args.action=='plan':
            print(json.dumps({'architecture':args.architecture,'commands':plan,'creates_nothing':True,
                'filesystem_checks_before_mkimg':filesystem_checks(value['work']),
                'base_file_usage':value['base_file_usage'],'runtime_file_usage':value['runtime_file_usage']},indent=2));return 0
        require_freebsd()
        trees,summary=prepare(value,args)
        results=[];filesystem_validation=None
        for number,step in enumerate(plan):
            if step['argv'][0]=='/usr/bin/mkimg':filesystem_validation=validate_filesystems(value['work'])
            log=value['work']/('image-step-'+str(number)+'.log')
            with log.open('xb') as stream:
                subprocess.run(step['argv'],cwd=step['cwd'],stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=300)
            results.append(dict(step,log_sha256=sha(log)))
        image=value['work']/('DaOS-'+args.architecture+'.img')
        if filesystem_validation is None:raise ValueError('planned GPT assembly omitted filesystem validation')
        evidence={'format':'DaOS-freebsd-image-build-v1','status':'assembled','assembled':True,'boot_validated':False,
            'gui_validated':False,'target':{'architecture':args.architecture,'elf_machine':MACHINES[args.architecture],
                'efi_machine':value['loader']['pe_machine']},'uefi':True,'host_mounts':False,'package_scripts_run':False,
            'image':{'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)},
            'base':{'state_sha256':value['base_state_sha256'],'kernel_ident':'DaOS','kernel_sha256':value['kernel']['sha256'],
                'source_commit':value['state']['source']['commit'],'source_release':value['state']['source']['release']},
            'runtime':{'architecture':args.architecture,'machine':value['manifest']['host']['machine'],
                **value['runtime_verification'],
                'abi_target':'FreeBSD:'+str(value['state']['source']['release']).split('.')[0]+(':aarch64' if args.architecture=='arm64' else ':amd64'),
                'manifest_sha256':value['runtime_sha256'],'packages':value['manifest']['packages'],
                'profile':value['manifest']['profile'],'statistics':value['manifest']['statistics'],
                'source_world_elf_dependency_check':summary['source_world_elf_dependency_check']},
            'native':{'evidence_sha256':value['native_evidence_sha256'],'sha256':sha(value['native']),
                'execution_machine':value['native_evidence']['uname']['machine']},
            'kernel':value['kernel'],'loader':value['loader'],'payload':summary,'commands':results,
            'filesystem_validation':filesystem_validation}
        if args.compress:
            with (value['work']/'compress.log').open('xb') as log:
                subprocess.run(['/usr/bin/xz','-T','2','-3','--keep',str(image)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
            compressed=Path(str(image)+'.xz')
            evidence['compressed_image']={'path':str(compressed),'bytes':compressed.stat().st_size,
                'sha256':sha(compressed),'format':'xz','raw_sha256':evidence['image']['sha256'],
                'raw_bytes':evidence['image']['bytes']}
        (value['work']/'image-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print('DaOS_IMAGE_ASSEMBLED '+str(image));return 0
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as error:
        print('DaOS_IMAGE_FAILED '+str(error),file=sys.stderr);return 1


if __name__=='__main__': raise SystemExit(main())

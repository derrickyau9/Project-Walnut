"""Validate a transported FreeBSD runtime without executing its target binaries.

The provisional source receipt is evidence to preserve, never a successful run
to promote on its own. Offline hashes are new observations. Optional runtime
export additionally requires source pkg checks and fresh declared checksums.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys

from common import HOST_ARCH, MACHINES, confined, new_path, no_link_parents, relative, validate_tree
from collect_runtime import loader_directories, package_path
from elf_dependencies import verify_closure
from runtime_profile import POLICY, curate

SHA = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9+_.-]{0,150}\Z')
MAX_JSON = 128 * 1024**2
MAX_FILE = 512 * 1024**2
SOURCE_FLAGS = ('installed_checksums_verified', 'package_scripts_run',
                'repository_signature_verified_by_collector', 'signature_scope')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns if os.name != 'nt' else None, info.st_mode, info.st_uid, info.st_gid)


def _content_identity(info):
    # Creating a hardlink necessarily changes ctime/link count, not contents.
    value = _identity(info)
    return value[:4] + value[5:]


def _read_regular(path, *, limit, collect=False):
    path = Path(path).absolute()
    no_link_parents(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError('input is not a bounded regular file: ' + str(path))
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    digest = hashlib.sha256(); chunks = []; total = 0
    try:
        if _identity(before) != _identity(os.fstat(descriptor)):
            raise ValueError('input changed before reading: ' + str(path))
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                total += len(block)
                if total > limit: raise ValueError('input grew beyond its byte bound: ' + str(path))
                digest.update(block)
                if collect: chunks.append(block)
        if (total != before.st_size or _identity(before) != _identity(os.fstat(descriptor))
                or _identity(before) != _identity(path.lstat())):
            raise ValueError('input changed during reading: ' + str(path))
    finally:
        os.close(descriptor)
    return digest.hexdigest(), before, b''.join(chunks) if collect else None


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result: raise ValueError('duplicate JSON field: ' + name)
        result[name] = value
    return result


def _count(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('invalid ' + label)
    return value


def _link(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('invalid literal symlink target')
    return value


def _entries(rows, *, omitted=False):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError('runtime entries are excessive or malformed')
    result = {}
    for row in rows:
        if not isinstance(row, dict): raise ValueError('invalid runtime entry')
        if not isinstance(row.get('path'), str): raise ValueError('invalid runtime entry path')
        name = package_path(row['path'])
        if name != row['path'] or name in result:
            raise ValueError('runtime entry path is duplicate or noncanonical')
        if row.get('type') not in ('file', 'link'):
            raise ValueError('runtime entries must be files or links')
        for field in ('uid', 'gid'): _count(row.get(field), field, 2**32-1)
        _count(row.get('mode'), 'mode', 0o7777)
        if row['type'] == 'file':
            _count(row.get('bytes'), 'file size', MAX_FILE)
            if 'sha256' in row and (not isinstance(row['sha256'], str) or not SHA.fullmatch(row['sha256'])):
                raise ValueError('invalid prior SHA256')
        else:
            _link(row.get('link'))
        if not isinstance(row.get('declared_checksum'), str):
            raise ValueError('missing declared package checksum')
        if (not isinstance(row.get('packages'), list) or not row['packages']
                or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in row['packages'])):
            raise ValueError('invalid runtime package ownership metadata')
        result[name] = copy.deepcopy(row)
    validate_tree(result)
    if sum(row.get('bytes', 0) for row in result.values()) > 6 * 1024**3:
        raise ValueError('runtime payload exceeds byte bound')
    return result


def validate_metadata(record):
    if (not isinstance(record, dict) or record.get('format') != 'DaOS-freebsd-runtime-provisional-v1'
            or record.get('status') not in ('metadata-only-unhashed', 'payload-hashed-awaiting-elf')):
        raise ValueError('expected a known provisional runtime receipt')
    architecture = record.get('architecture')
    if architecture not in MACHINES: raise ValueError('unsupported runtime architecture')
    if type(record.get('installed_checksums_verified')) is not bool or record.get('package_scripts_run') is not False:
        raise ValueError('provisional source checksum/script flags are missing or invalid')
    source_host = record.get('source_host', record.get('host'))
    if (not isinstance(source_host, dict) or source_host.get('system') != 'FreeBSD'
            or HOST_ARCH.get(str(source_host.get('machine', '')).lower()) != architecture
            or not re.match(r'^[0-9]+[.-]', str(source_host.get('release', '')))):
        raise ValueError('source host metadata does not describe target FreeBSD')
    packages = record.get('packages')
    if not isinstance(packages, list) or not 0 < len(packages) <= 10000:
        raise ValueError('package inventory is missing or excessive')
    names = set(); major = source_host['release'].split('.')[0]
    target = 'aarch64' if architecture == 'arm64' else 'amd64'
    for package in packages:
        name = package.get('name') if isinstance(package, dict) else None
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError('invalid or duplicate package name')
        names.add(name)
        if package.get('abi') not in ('FreeBSD:'+major+':'+target, 'FreeBSD:'+major+':*'):
            raise ValueError('package ABI differs from source target')
        for field in ('version', 'origin', 'repository', 'package_checksum'):
            if not isinstance(package.get(field), str): raise ValueError('missing package field: ' + field)
        for field in ('dependencies', 'licenses'):
            if not isinstance(package.get(field), list) or any(not isinstance(v, str) for v in package[field]):
                raise ValueError('invalid package ' + field)
    for package in packages:
        if any(name not in names for name in package['dependencies']):
            raise ValueError('package dependency closure is incomplete')
    requested = record.get('requested')
    if not isinstance(requested, list) or not requested or any(name not in names for name in requested):
        raise ValueError('requested packages are missing from the inventory')
    entries = _entries(record.get('entries')); omissions = _entries(record.get('omissions'), omitted=True)
    if not entries or set(entries) & set(omissions):
        raise ValueError('retained/omitted runtime entries overlap or payload is empty')
    for row in list(entries.values()) + list(omissions.values()):
        if any(name not in names for name in row['packages']):
            raise ValueError('entry names a package outside the declared closure')
    profile = record.get('profile')
    expected_policy = POLICY if profile == 'camera-runtime-v1' else {'id':'full-package-v1','version':1}
    if record.get('curation_policy') != expected_policy:
        raise ValueError('runtime curation policy differs from the implemented policy')
    kept, omitted, statistics = curate(list(entries.values()) + list(omissions.values()), profile)
    if ({row['path'] for row in kept} != set(entries)
            or {row['path'] for row in omitted} != set(omissions)
            or statistics != record.get('statistics')):
        raise ValueError('runtime profile/counts do not reproduce from declared metadata')
    for row in omitted:
        old = omissions[row['path']]
        if any(row.get(key) != old.get(key) for key in ('policy', 'reason', 'target', 'target_reason')):
            raise ValueError('runtime omission reason does not reproduce')
    if record.get('status') == 'payload-hashed-awaiting-elf' and any(
            row['type'] == 'file' and 'sha256' not in row for row in entries.values()):
        raise ValueError('hashed provisional receipt lacks retained file hashes')
    base = record.get('base_dependency_manifest')
    if not isinstance(base, list) or not 0 < len(base) <= 100000:
        raise ValueError('recorded source base library manifest is absent or excessive')
    base_names = set()
    for row in base:
        if not isinstance(row, dict): raise ValueError('invalid base library record')
        name = relative(row.get('path', ''))
        if name != row['path'] or name in base_names or not (name.startswith(('lib/', 'usr/lib/')) or name == 'libexec/ld-elf.so.1'):
            raise ValueError('invalid or duplicate recorded base library path')
        base_names.add(name)
        if row.get('type') == 'link': _link(row.get('link'))
        elif row.get('type') == 'file':
            meta = row if 'machine' in row else row.get('elf', {})
            if (not isinstance(meta, dict) or not isinstance(row.get('sha256'), str) or not SHA.fullmatch(row['sha256'])
                    or meta.get('machine') != MACHINES[architecture] or meta.get('osabi') not in (0, 9)
                    or not isinstance(meta.get('needed'), list)):
                raise ValueError('recorded base ELF architecture/hash metadata is invalid')
        else: raise ValueError('invalid recorded base library type')
    validate_tree({row['path']:row for row in base})
    if not isinstance(record.get('loader_configuration'), list):
        raise ValueError('loader configuration is missing')
    return architecture, source_host, entries


def _payload_set(root):
    observed = set()
    for current, directories, files in os.walk(root, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error)):
        for name in list(directories):
            path = Path(current) / name
            if path.is_symlink():
                observed.add(path.relative_to(root).as_posix()); directories.remove(name)
            elif not stat.S_ISDIR(path.lstat().st_mode):
                raise ValueError('payload includes a non-directory ancestor')
        observed.update((Path(current)/name).relative_to(root).as_posix() for name in files)
        if len(observed) > 100000: raise ValueError('payload contains excessive entries')
    return observed


def _report_path(value, payload, provisional):
    path = Path(value).absolute(); no_link_parents(path)
    if ('..' in path.parts or path.suffix != '.json' or os.path.lexists(path) or path.name == 'runtime.json'
            or path == provisional or path == payload or payload in path.parents):
        raise ValueError('offline report must be a new JSON outside the payload, never runtime.json')
    if not path.parent.is_dir(): raise ValueError('report parent must already exist')
    return path


def _write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())


def verify(args):
    provisional = Path(args.provisional).absolute()
    source_hash, _, raw = _read_regular(provisional, limit=MAX_JSON, collect=True)
    record = json.loads(raw, object_pairs_hook=_unique_object)
    architecture, source_host, entries = validate_metadata(record)
    payload = confined(Path(args.payload).absolute(), '.')
    if not payload.is_dir(): raise ValueError('payload must be an existing nonlinked directory')
    output = _report_path(args.output, payload, provisional)
    runtime_output = getattr(args, 'runtime_output', None)
    runtime_dir = new_path(runtime_output, 'DaOS-runtime') if runtime_output else None
    if runtime_dir is not None:
        if (runtime_dir == payload or payload in runtime_dir.parents or runtime_dir in payload.parents
                or runtime_dir == provisional or runtime_dir in provisional.parents
                or runtime_dir == output or runtime_dir in output.parents):
            raise ValueError('runtime output must be separate from all input/report paths')
        if record.get('installed_checksums_verified') is not True or record.get('package_scripts_run') is not False:
            raise ValueError('runtime export requires source pkg checks and no package scripts')
        if runtime_dir.parent.stat().st_dev != payload.stat().st_dev:
            raise ValueError('runtime export requires same-filesystem read-only hardlinks')
    if _payload_set(payload) != set(entries):
        raise ValueError('payload file/link set differs from the exact declared profile')
    check_declared = bool(runtime_dir) or getattr(args, 'verify_package_checksums', False)
    if check_declared:
        from pkg_checksum import verify_declared
    prior_count = fresh_count = declared_count = 0
    identities = {}
    for name, row in sorted(entries.items()):
        path = confined(payload, name, allow_leaf_link=row['type'] == 'link')
        info = path.lstat()
        if row['type'] == 'link':
            if not stat.S_ISLNK(info.st_mode) or os.readlink(path) != row['link']:
                raise ValueError('payload literal symlink differs: ' + name)
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_size != row['bytes']
                    or stat.S_IMODE(info.st_mode) & 0o777 != row['mode'] & 0o777):
                raise ValueError('payload regular-file size/mode differs: ' + name)
            if check_declared:
                checked = verify_declared(path, row['declared_checksum'])
                if checked.get('verified') is not True or checked.get('bytes') != row['bytes']:
                    raise ValueError('declared package checksum was not verified: ' + name)
                digest = checked['sha256']; declared_count += 1
            else:
                digest, _, _ = _read_regular(path, limit=MAX_FILE)
            if 'sha256' in row:
                if digest != row['sha256']: raise ValueError('payload SHA256 differs from provisional receipt: ' + name)
                prior_count += 1
            else: fresh_count += 1
            row['sha256'] = digest
        if _identity(info) != _identity(path.lstat()):
            raise ValueError('payload changed while reading/checking metadata: ' + name)
        identities[name] = _identity(info)
    hints, directories = loader_directories(payload, list(entries.values()))
    if hints != record['loader_configuration']:
        raise ValueError('loader hints differ from provisional source metadata')
    elf = verify_closure(payload, list(entries.values()), record['base_dependency_manifest'],
                         architecture, extra_search_paths=directories)
    if _payload_set(payload) != set(entries): raise ValueError('payload set changed during validation')
    for name, before in identities.items():
        if before != _identity(confined(payload, name, allow_leaf_link=True).lstat()):
            raise ValueError('payload changed during offline validation: ' + name)
    if _read_regular(provisional, limit=MAX_JSON)[0] != source_hash:
        raise ValueError('provisional receipt changed during validation')
    verification_host = {'system':platform.system(), 'release':platform.release(), 'machine':platform.machine()}
    verifier_sources = ('verify_runtime_offline.py', 'common.py', 'collect_runtime.py',
                        'elf_dependencies.py', 'runtime_profile.py') + (('pkg_checksum.py',) if check_declared else ())
    verifier_hashes = {name:_read_regular(Path(__file__).parent/name, limit=MAX_JSON)[0] for name in verifier_sources}
    result = copy.deepcopy(record)
    result.update({'format':'DaOS-freebsd-runtime-offline-validation-v1', 'status':'completed', 'passed':True,
        'source_status':record['status'], 'source_provisional_sha256':source_hash,
        'source_host':source_host, 'verification_host':verification_host,
        'verifier_source_code_sha256':verifier_hashes,
        'source_claims':{key:record[key] for key in SOURCE_FLAGS if key in record},
        'entries':list(entries.values()), 'elf_dependency_check':elf,
        'offline_hash_observations':{'prior_sha256_matched_files':prior_count,
            'new_sha256_files':fresh_count, 'new_hashes_attest_prior_pkg_checks':False,
            'declared_package_checksum_files_verified':declared_count},
        'target_execution_validated':False, 'physical_hardware_validated':False,
        'limitations':['Offline static validation does not execute target code or prove target boot.',
            'Recorded source package/base metadata is preserved, not independently recollected here.',
            'Fresh SHA256 observations alone do not attest source package checks or an earlier successful collection.',
            'Static DT_NEEDED checks do not cover dlopen-only modules or actual dynamic-loader behavior.']})
    # Source claims remain nested: the verifier never impersonates pkg check.
    for key in SOURCE_FLAGS: result.pop(key, None)
    if runtime_dir is not None:
        runtime_dir.mkdir(mode=0o700); target = runtime_dir/'payload'; target.mkdir(mode=0o700)
        for name, row in sorted(entries.items()):
            destination = confined(target, name); destination.parent.mkdir(parents=True, exist_ok=True)
            source = confined(payload, name, allow_leaf_link=row['type'] == 'link')
            expected_content = identities[name][:4] + identities[name][5:]
            if _content_identity(source.lstat()) != expected_content: raise ValueError('payload changed before export: ' + name)
            if row['type'] == 'link': destination.symlink_to(row['link'])
            else:
                os.link(source, destination, follow_symlinks=False)
                if (_content_identity(source.lstat()) != expected_content
                        or _content_identity(destination.lstat()) != expected_content):
                    raise ValueError('payload changed while hardlinking export: ' + name)
        runtime = copy.deepcopy(record)
        runtime.update({'format':'DaOS-freebsd-runtime-v2', 'entries':list(entries.values()),
            'status':'completed', 'completed_runtime':True, 'payload_content_verified':True,
            'source_status':record['status'],
            'source_provisional_claims':{key:copy.deepcopy(record[key]) for key in (
                'status', 'completed_runtime', 'payload_content_verified', *SOURCE_FLAGS) if key in record},
            'signature_scope':('The source reports successful installed pkg checks. Offline verification '
                'freshly matched every retained regular file to its declared pkg checksum and SHA256, '
                'checked literal symlinks, and passed static ELF dependency closure. This offline '
                'verifier does not independently attest archive/repository signatures, target execution, '
                'or physical hardware validation.'),
            'elf_dependency_check':elf, 'host':source_host, 'source_host':source_host,
            'verification_host':verification_host, 'source_provisional_sha256':source_hash,
            'verifier_source_code_sha256':verifier_hashes,
            'collection_kind':'offline-verified-transport',
            'target_execution_validated':False, 'physical_hardware_validated':False,
            'offline_declared_checksums_verified':True,
            'repository_signature_verified_by_collector':record.get('repository_signature_verified_by_collector', False),
            'offline_validation':{'format':result['format'], 'source_status':record['status'],
                'prior_run_promoted':False, 'all_retained_regular_file_declared_checksums_verified':True,
                'target_execution_validated':False, 'physical_hardware_validated':False},
            'bytes':record['statistics']['kept_bytes']})
        _write_new(runtime_dir/'runtime.json', runtime)
        result['runtime_export'] = {'directory':str(runtime_dir), 'format':runtime['format'],
            'method':'read-only payload hardlinks and literal symlinks; no chmod/chown/write to source files'}
    _write_new(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provisional', required=True)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--output', required=True, help='new offline receipt; never runtime.json')
    parser.add_argument('--verify-package-checksums', action='store_true')
    parser.add_argument('--runtime-output', help='optional NEW DaOS-runtime* directory; requires source pkg checks and fresh declared checksums')
    args = parser.parse_args(argv)
    try:
        result = verify(args)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print('DaOS_OFFLINE_RUNTIME_FAILED ' + str(error), file=sys.stderr); return 1
    print(json.dumps({'format':result['format'], 'passed':True,
                      'architecture':result['architecture'], 'files':len(result['entries'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

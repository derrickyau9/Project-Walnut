"""Owned FreeBSD builder-side verification and stage runner; no VM lifecycle."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import signal
import struct
import subprocess
import sys
import time


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, path)


def safe(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Canonical non-symlink builder path required: ' + str(path))
    return path


def target_elf(path):
    path = safe(path)
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 183:
        raise ValueError('Expected an actual AArch64 ELF file: ' + str(path))
    return {'machine': 183, 'class': 64, 'OSABI': header[7], 'sha256': sha(path)}


def verify_base(request):
    if platform.system() != 'FreeBSD' or not platform.release().startswith('15.1-'):
        raise ValueError('An actual native FreeBSD 15.1 source builder is required')
    cfg = request['base']
    path = safe(cfg['receipt'])
    if sha(path) != cfg['receipt_sha256']:
        raise ValueError('Source-built base receipt differs from the profile pin')
    state = json.loads(path.read_text())
    if (state.get('format') != 'DaOS-freebsd-base-build-v2' or state.get('status') != 'completed'
            or state.get('target', {}).get('architecture') != 'arm64'
            or state.get('source', {}).get('commit') != request['source']['commit']
            or state['source'].get('archive_sha256') != request['source']['archive_sha256']):
        raise ValueError('Completed, source-pinned ARM64 base evidence required')
    if state.get('source_lock_sha256') != request['source_lock_sha256']:
        raise ValueError('The completed base was built with a different source lock')
    source, stage = safe(cfg['source_root']), safe(cfg['stage'])
    if str(source) != state['source_directory'] or str(stage) != state['stage_directory']:
        raise ValueError('Profile source/stage paths differ from their build receipt')
    archive = safe(request['source_archive'])
    archive_digest = sha(archive)
    if archive.stat().st_size != request['source']['archive_bytes'] or archive_digest != request['source']['archive_sha256']:
        raise ValueError('Actual source archive differs from the source lock')
    checked = {}
    for relative, expected in state.get('source_anchor_sha256', {}).items():
        rel = PurePosixPath(relative)
        if rel.is_absolute() or '..' in rel.parts or sha(safe(source / relative)) != expected:
            raise ValueError('Pinned source anchor changed: ' + relative)
        checked[relative] = expected
    if len(checked) < 4:
        raise ValueError('Source receipt lacks its upstream anchor hashes')
    artifacts = {}
    for relative, expected in state.get('artifact_sha256', {}).items():
        rel = PurePosixPath(relative)
        if rel.is_absolute() or '..' in rel.parts or sha(safe(stage / relative)) != expected:
            raise ValueError('Staged world/kernel anchor changed: ' + relative)
        artifacts[relative] = expected
    if not {'bin/sh', 'sbin/init', 'boot/kernel/kernel', 'lib/libc.so.7', 'METALOG'} <= artifacts.keys():
        raise ValueError('Source-built base artifact closure is incomplete')
    if sha(safe(cfg['src_conf'])) != state['configuration_sha256']['src.conf']:
        raise ValueError('Base world configuration changed')
    if state['configuration_sha256']['src.conf'] != request['world_config_sha256']:
        raise ValueError('Requested world configuration differs from the reused source base')
    kernel = state.get('kernel_config', {})
    if kernel.get('device_splash') is not True or sha(safe(kernel['path'])) != kernel['sha256']:
        raise ValueError('The receipt must bind an actually rebuilt splash kernel')
    if kernel['sha256'] != request['kernel_config_sha256']:
        raise ValueError('Requested kernel configuration differs from the reused source base')
    if not safe(Path(cfg['kernel_object']) / 'opt_global.h').is_file():
        raise ValueError('Matching private kernel object/configuration is unavailable')
    sdk = {name: sha(safe(stage / name)) for name in ('usr/include/sys/types.h', 'usr/include/sys/param.h')}
    return {'format': 'DaOS-FreeBSD-source-reuse-verification-v1', 'status': 'success',
            'executed': False, 'verification_executed': True, 'reused': True,
            'world_recompiled': False, 'kernel_recompiled': False,
            'receipt_sha256': sha(path), 'source_archive_sha256': archive_digest,
            'source_anchor_sha256': checked, 'stage_anchor_sha256': artifacts,
            'SDK_header_sha256': sdk,
            'kernel_object_options_sha256': sha(Path(cfg['kernel_object']) / 'opt_global.h'),
            'elf': {name: target_elf(stage / name) for name in ('bin/sh', 'lib/libc.so.7', 'boot/kernel/kernel')},
            'active_base': cfg, 'source': state['source'],
            'builder': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()}}


def run_stage(request):
    root = safe(request['guest_root'])
    log = safe(Path(request['log']))
    receipt = safe(Path(request['receipt']))
    if root not in log.parents or root not in receipt.parents:
        raise ValueError('Stage logs/receipts must remain inside the owned builder workspace')
    log.parent.mkdir(exist_ok=True)
    receipt.parent.mkdir(exist_ok=True)
    if log.exists() or receipt.exists():
        raise ValueError('Stage execution evidence may not be overwritten')
    record = {'format': 'DaOS-FreeBSD-USB-stage-execution-v1', 'status': 'running', 'executed': True,
              'argv': request['argv'], 'started_at': time.time(), 'input_hash': request['input_hash']}
    save(receipt, record)
    process = None
    try:
        with log.open('xb') as output:
            process = subprocess.Popen(request['argv'], stdout=output, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = process.wait(timeout=request['timeout'])
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
        record.update(status='success' if code == 0 else 'failed', returncode=code,
                      finished_at=time.time(), log=str(log), log_sha256=sha(log))
        save(receipt, record)
        return record
    except BaseException as error:
        record.update(status='failed', error=str(error), finished_at=time.time())
        save(receipt, record)
        raise


def inspect_files(request):
    result = {}
    for name, value in request['files'].items():
        path = safe(value)
        result[name] = {'path': str(path), 'sha256': sha(path), 'size': path.stat().st_size}
        if path.suffix == '.json':
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Artifact JSON evidence exceeds its bound')
            result[name]['json'] = json.loads(path.read_text())
        else:
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic == b'\x7fELF':
                result[name]['elf'] = target_elf(path)
    return result


def verify_scratch(request):
    """Read-only gate: a directory on the root disk is not image scratch."""
    if platform.system() != 'FreeBSD':
        raise ValueError('Image scratch must be checked on the native FreeBSD builder')
    root = safe(request['root'])
    if not root.is_dir():
        raise ValueError('Image scratch is missing; prepare its dedicated tmpfs before assembly')
    required = request['required_free_bytes']
    if type(required) is not int or required <= 0:
        raise ValueError('An explicit positive image scratch-space requirement is required')
    mounts = subprocess.run(['/sbin/mount', '-p'], check=True, capture_output=True,
                            text=True, timeout=10).stdout
    if len(mounts) > 1024 * 1024:
        raise ValueError('Native mount table exceeds its expected bound')
    matches = [line.split() for line in mounts.splitlines()
               if len(line.split()) >= 3 and line.split()[1] == str(root)]
    if len(matches) != 1 or matches[0][2] != 'tmpfs':
        raise ValueError('Image scratch is not an actual tmpfs mount: ' + str(root)
                         + '; a same-name directory on the builder root disk is insufficient')
    stats = os.statvfs(root)
    available = stats.f_bavail * stats.f_frsize
    if available < required:
        raise ValueError('Image scratch has insufficient free space: ' + str(available)
                         + ' bytes available, ' + str(required)
                         + ' required; preserve/export prior work and prepare enough tmpfs space')
    return {'format': 'DaOS-FreeBSD-image-scratch-verification-v1', 'status': 'success',
            'executed': False, 'verification_executed': True, 'root': str(root),
            'filesystem': 'tmpfs', 'mounted': True,
            'capacity_bytes': stats.f_blocks * stats.f_frsize,
            'available_bytes': available, 'required_free_bytes': required,
            'image_mutation_executed': False}


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    action = request['action']
    if action == 'verify_base':
        result = verify_base(request)
    elif action == 'run':
        result = run_stage(request)
    elif action == 'inspect':
        result = inspect_files(request)
    elif action == 'scratch':
        result = verify_scratch(request)
    else:
        raise ValueError('Unknown builder action')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

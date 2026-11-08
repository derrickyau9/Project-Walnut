"""FreeBSD source-built Pi4 USB target: verified base, native ADB, overlay, image.

This backend uses the existing native FreeBSD builder through its SSH API. It
never starts a VM, changes the live OS, mounts a host disk, or substitutes an
old image. Default base reuse performs real byte verification and explicitly
reports executed=False. Rebuilding world/kernel requires rebuild_base=True.
"""
from __future__ import annotations
import base64
import hashlib
import json
import io
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import uuid

from . import netbsd as common

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
FORMAT = 'DaOS-FreeBSD-USB-buildroot-v1'
COMMIT = '95719d8a895bf8170851e9844d9b4092b1d2b6fe'
USB_FILES = ('Makefile', 'daos_usb_adb.c', 'adb_packet.h', 'daos_ms_os.h',
             'build-in-env.sh', 'recipe.json', 'kmod/Makefile', 'kmod/daos_adb_template.c')


def _usb_archive():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name in USB_FILES:
            content = (PROJECT / 'system/adb/freebsd_usb' / name).read_bytes()
            info = tarfile.TarInfo('freebsd_usb/' + name)
            info.size, info.mode, info.mtime = len(content), 0o644, 1793836800
            archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


def _guest_path(value, *, output=False):
    if (not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or '..' in PurePosixPath(value).parts or '//' in value or value.endswith('/')):
        raise common.BackendError('Canonical absolute builder path required: ' + str(value))
    if output and not (value.startswith('/var/daos-builds/') or value.startswith('/var/DaOS-')):
        raise common.BackendError('Builder output must be a private /var/daos-builds or /var/DaOS-* path')
    return value


def _spec(profile_path, workspace, rebuild_base=False):
    profile_path = common._no_symlink_components(Path(profile_path).absolute())
    profile = common._read_json(profile_path)
    if (profile.get('format') != 'DaOS-bsd-build-profile-v1' or profile.get('backend') != 'freebsd_usb'
            or profile.get('target') != {'machine': 'arm64', 'architecture': 'aarch64', 'board': 'raspberrypi4'}):
        raise common.BackendError('A FreeBSD USB Raspberry Pi4/AArch64 profile is required')
    lock_path = common._no_symlink_components(profile_path.parent / profile['source_lock'])
    lock = common._read_json(lock_path)
    if (lock.get('format') != 'DaOS-freebsd-base-source-v2' or lock.get('architecture') != 'arm64'
            or lock.get('commit') != COMMIT or lock.get('release') != '15.1-RELEASE-p6'
            or not re.fullmatch(r'[0-9a-f]{64}', lock.get('archive_sha256', ''))):
        raise common.BackendError('The pinned FreeBSD 15.1-p6 ARM64 source lock is required')
    workspace = common._workspace(workspace)
    cfg = profile['base']
    for key in ('receipt', 'source_root', 'stage', 'kernel_object', 'object_prefix', 'src_conf'):
        _guest_path(cfg[key])
    if not re.fullmatch(r'[0-9a-f]{64}', cfg['receipt_sha256']):
        raise common.BackendError('Base receipt needs a pinned SHA256')
    _guest_path(profile['source_archive'])
    _guest_path(profile['builder']['work_dir'])
    _guest_path(profile['builder']['python'])
    if profile['builder']['user'] not in ('camera', 'root'):
        raise common.BackendError('Use an explicitly isolated task VM user')
    for value in profile['guest_inputs'].values():
        _guest_path(value)
    for name in ('root', 'var', 'calibration', 'photos', 'efi'):
        size = profile['storage'][name + '_mib']
        if not isinstance(size, int) or isinstance(size, bool) or not 16 <= size <= 8192:
            raise common.BackendError('Image partition sizes must be bounded integer MiB values')
    if profile['storage']['efi_mib'] < 64:
        raise common.BackendError('The FAT32 EFI partition requires at least 64 MiB')
    for name in ('vid', 'pid'):
        value = profile['usb'][name]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise common.BackendError('USB VID/PID must be nonzero 16-bit integers')
    _guest_path(profile['image_work_root'] + '/new-image', output=True)
    required = [profile_path, lock_path, Path(__file__), HERE / 'freebsd_usb_guest.py',
                PROJECT / 'system/bsd-buildroot/daos.py',
                PROJECT / 'tools/freebsd_vm.py', Path(common.__file__),
                PROJECT / 'system/adb/build_freebsd.py', PROJECT / 'system/adb/integration/install.py',
                PROJECT / 'system/freebsd/base/build_base.py', PROJECT / 'system/freebsd/base/src.conf',
                PROJECT / 'system/freebsd/raspberrypi/assemble_pi4.py',
                profile_path.parent / profile['kernel']['config'],
                PROJECT / 'system/adb/daemon/runtime-manifest.json']
    required += [p for p in (PROJECT / 'system/freebsd/image').glob('*.py') if not p.name.startswith('test_')]
    required += [p for p in (PROJECT / 'system/adb/integration').glob('*.py') if not p.name.startswith('test_')]
    required += [PROJECT / 'system/adb/freebsd_usb' / name for name in USB_FILES]
    runtime = common._read_json(PROJECT / 'system/adb/daemon/runtime-manifest.json')
    required += [PROJECT / 'system/adb/daemon' / name for name in runtime['files']]
    required += [p for p in (PROJECT / 'system/adb/integration/files').rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')]
    hashes = {}
    for path in required:
        path = common._no_symlink_components(path)
        if not path.is_file():
            raise common.BackendError('Required source recipe is missing: ' + str(path))
        hashes[str(path)] = common._hash(path)
    archive = _usb_archive()
    archive_hash = hashlib.sha256(archive).hexdigest()
    inputs = {'source_sha256': hashes, 'usb_source_archive_sha256': archive_hash,
              'rebuild_base_requested': bool(rebuild_base)}
    fingerprint = common._identity(inputs)
    token = common._identity({'workspace': str(workspace), 'inputs': fingerprint})[:14]
    guest = '/var/daos-builds/daos-buildroot-usb-' + token
    paths = {'guest_root': guest, 'usb_source_archive': guest + '/usb-source.tar',
             'usb_work': '/var/daos-builds/daos-build-usb-adb-' + token,
             'base_work': '/var/daos-builds/daos-base-buildroot-' + token,
             'bundle': guest + '/adb-bundle',
             'image_work': profile['image_work_root'] + '/DaOS-image-buildroot-usb-' + token}
    return {'profile_path': profile_path, 'profile': profile, 'lock_path': lock_path, 'lock': lock,
            'workspace': workspace, 'inputs': inputs, 'input_hash': fingerprint, 'paths': paths,
            'rebuild_base': bool(rebuild_base), 'usb_archive_bytes': archive, 'usb_archive_sha256': archive_hash}


def _provider(spec):
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    from tools import freebsd_vm
    freebsd_vm._metadata(Path(spec['profile']['builder']['work_dir']))
    return freebsd_vm


def _builder_command(spec):
    return ['python3', str(PROJECT / 'tools/freebsd_vm.py'), 'serve', '--work-dir',
            spec['profile']['builder']['work_dir'], '--memory-mib', '16384', '--cpus', '12']


def _ssh(spec, code, timeout=90):
    cfg = spec['profile']['builder']
    command = shlex.join([cfg['python'], '-c', code])
    result = _provider(spec).ssh_command(Path(cfg['work_dir']), command, user=cfg['user'], timeout=timeout)
    if result['returncode']:
        detail = (result['stderr'] + '\n' + result['stdout'])[-6000:]
        hint = ('\nThe provisioned native builder must already be running. In a separate WSL terminal run: '
                + shlex.join(_builder_command(spec))) if result['returncode'] == 255 else ''
        raise common.BackendError('FreeBSD builder command failed: ' + detail + hint)
    return result['stdout']


def _rpc(spec, action, data, timeout=90):
    source = (HERE / 'freebsd_usb_guest.py').read_bytes()
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    bootstrap = ("import base64,json; ns={'__name__':'daos_freebsd_usb_guest'};"
                 'exec(base64.b64decode(' + repr(base64.b64encode(source).decode()) + '),ns);'
                 'r=json.loads(base64.b64decode(' + repr(encoded) + '));')
    if action == 'verify_base':
        code = bootstrap + "print(json.dumps(ns['verify_base'](r),sort_keys=True))"
    elif action == 'run':
        code = bootstrap + "print(json.dumps(ns['run_stage'](r),sort_keys=True))"
    elif action == 'inspect':
        code = bootstrap + "print(json.dumps(ns['inspect_files'](r),sort_keys=True))"
    elif action == 'scratch':
        code = bootstrap + "print(json.dumps(ns['verify_scratch'](r),sort_keys=True))"
    else:
        raise common.BackendError('Unknown builder RPC action')
    return json.loads(_ssh(spec, code, timeout=timeout))


def _verify_base(spec, base=None):
    return _rpc(spec, 'verify_base', {'base': base or spec['profile']['base'],
        'source': spec['lock'], 'source_archive': spec['profile']['source_archive'],
        'source_lock_sha256': common._hash(spec['lock_path']),
        'world_config_sha256': common._hash(PROJECT / 'system/freebsd/base/src.conf'),
        'kernel_config_sha256': common._hash(spec['profile_path'].parent / spec['profile']['kernel']['config'])})


def _verify_project(spec):
    files, expected = {}, {}
    prefixes = ('system/adb/integration/', 'system/freebsd/image/', 'system/adb/daemon/')
    for source, checksum in spec['inputs']['source_sha256'].items():
        path = Path(source)
        if PROJECT not in path.parents:
            continue
        relative = path.relative_to(PROJECT).as_posix()
        if relative.startswith(prefixes) or relative == 'system/freebsd/raspberrypi/assemble_pi4.py':
            files[relative] = spec['profile']['guest_inputs']['project'] + '/' + relative
            expected[relative] = checksum
    actual = _rpc(spec, 'inspect', {'files': files})
    if any(actual[name]['sha256'] != checksum for name, checksum in expected.items()):
        raise common.BackendError('Guest project snapshot differs from the configured recipe/daemon sources')
    return {'verified_files': len(expected), 'sha256': expected}


def _verify_uploaded_inputs(spec):
    root = spec['paths']['guest_root']
    expected = {
        'usb_archive': (spec['paths']['usb_source_archive'], spec['usb_archive_sha256']),
        'usb_recipe': (root + '/build_freebsd.py', common._hash(PROJECT / 'system/adb/build_freebsd.py')),
    }
    if spec['rebuild_base']:
        for source, name in ((PROJECT / 'system/freebsd/base/build_base.py', 'build_base.py'),
                             (PROJECT / 'system/freebsd/base/src.conf', 'src.conf'),
                             (spec['lock_path'], 'source.lock.json'),
                             (spec['profile_path'].parent / spec['profile']['kernel']['config'], 'DaOS')):
            expected['base_' + name] = (root + '/base-recipe/' + name, common._hash(source))
    actual = _rpc(spec, 'inspect', {'files': {name: value[0] for name, value in expected.items()}})
    if any(actual[name]['sha256'] != value[1] for name, value in expected.items()):
        raise common.BackendError('Uploaded source archive/build recipe changed after configure')
    return {name: value['sha256'] for name, value in actual.items()}


def _init(spec):
    workspace = spec['workspace']
    marker = workspace / 'workspace.json'
    if marker.exists():
        previous = common._read_json(marker)
        if previous.get('format') != FORMAT or previous.get('input_hash') != spec['input_hash']:
            raise common.BackendError('USB workspace inputs changed; use a new private workspace')
    else:
        if workspace.exists() and any(workspace.iterdir()):
            raise common.BackendError('Cannot adopt a nonempty unowned USB workspace')
        workspace.mkdir(parents=True, exist_ok=True)
        common._write_json(marker, {'format': FORMAT, 'input_hash': spec['input_hash'],
                                   'created_at': common._now(), 'paths': spec['paths']})
    for name in ('state', 'logs', 'receipts', 'inputs'):
        common._inside(workspace, workspace / name).mkdir(exist_ok=True)


def _load_spec(profile_path, workspace):
    receipt = common._read_json(common._workspace(workspace) / 'state/configure.json')
    spec = _spec(profile_path, workspace, receipt['rebuild_base_requested'])
    if receipt.get('status') != 'success' or receipt.get('input_hash') != spec['input_hash']:
        raise common.BackendError('Profile/source recipes changed since configure')
    return spec, receipt


def _argv(spec, stage, base=None, *, jobs=12, guest_work_dir=None):
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= 16:
        raise common.BackendError('FreeBSD jobs must be in 1..16')
    p, g, b = spec['profile'], spec['paths'], base or spec['profile']['base']
    py, project = p['builder']['python'], p['guest_inputs']['project']
    if stage == 'base':
        return [py, g['guest_root'] + '/base-recipe/build_base.py', '--source-archive', p['source_archive'],
                '--source-lock', g['guest_root'] + '/base-recipe/source.lock.json', '--architecture', 'arm64',
                '--work-dir', g['base_work'], '--jobs', str(jobs)]
    if stage == 'usb':
        return [py, g['guest_root'] + '/build_freebsd.py', '--archive', g['usb_source_archive'],
                '--archive-sha256', spec['usb_archive_sha256'], '--base-state', b['receipt'],
                '--source-root', b['source_root'], '--kernel-object', b['kernel_object'],
                '--object-prefix', b['object_prefix'], '--src-conf', b['src_conf'], '--work-dir', g['usb_work']]
    if stage == 'packages':
        return [py, project + '/system/adb/integration/install.py', '--base-state', b['receipt'],
                '--firmware-dir', p['guest_inputs']['firmware'], '--helper', g['usb_work'] + '/output/helper/daos-usb-adb',
                '--module', g['usb_work'] + '/output/kmod/daos_adb_template.ko',
                '--usb-build-receipt', g['usb_work'] + '/build-evidence.json',
                '--daemon-dir', project + '/system/adb/daemon',
                '--authorized-keys', p['guest_inputs']['authorized_keys'], '--output', g['bundle'],
                '--vid', hex(p['usb']['vid']), '--pid', hex(p['usb']['pid']), '--serial', p['usb']['serial']]
    if stage == 'image':
        output = guest_work_dir or g['image_work']
        _guest_path(output, output=True)
        if not output.startswith(p['image_work_root'] + '/'):
            raise common.BackendError('Image work directory must stay under the configured private image work root')
        argv = [py, project + '/system/freebsd/raspberrypi/assemble_pi4.py', 'build',
                '--architecture', 'arm64', '--base-state', b['receipt'], '--runtime', p['guest_inputs']['runtime'],
                '--project', project, '--native-evidence', p['guest_inputs']['native_evidence'],
                '--native-command', p['guest_inputs']['native_command'], '--firmware-dir', p['guest_inputs']['firmware'],
                '--work-dir', output, '--adb-bundle', g['bundle']]
        for name in ('root', 'var', 'calibration', 'photos', 'efi'):
            argv += ['--' + name + '-mib', str(p['storage'][name + '_mib'])]
        argv += ['--epoch', str(p.get('epoch', 1793836800))]
        return argv
    raise common.BackendError('Unknown FreeBSD USB build stage')


def plan(profile_path, workspace, *, jobs=12, rebuild_base=False, guest_work_dir=None):
    spec = _spec(profile_path, workspace, rebuild_base)
    stages = (['base'] if rebuild_base else []) + ['usb', 'packages', 'image']
    return {'format': FORMAT, 'operation': 'plan', 'status': 'planned', 'executed': False,
            'input_hash': spec['input_hash'], 'paths': spec['paths'], 'source': spec['lock'],
            'base_policy': 'actually rebuild world/kernel from pinned archive' if rebuild_base else 'verify pinned source-built world/kernel bytes; reuse without claiming compilation',
            'commands': {name: _argv(spec, name, jobs=jobs, guest_work_dir=guest_work_dir) for name in stages},
            'native_builder': dict(spec['profile']['builder'], must_already_be_running=True,
                                   auto_started=False, serve_argv=_builder_command(spec)),
            'image_scratch': _scratch_request(spec),
            'source_SDK': 'installed matching headers/libraries are WORLDTMP; pruned old object cache is not a new toolchain build',
            'physical_usb_validated': False, 'boots_VM': False}


def configure(profile_path, workspace, *, rebuild_base=False):
    spec = _spec(profile_path, workspace, rebuild_base)
    _init(spec)
    w = spec['workspace']
    with common._locked(w):
        state = w / 'state/configure.json'
        if state.exists():
            previous = common._read_json(state)
            if previous['input_hash'] != spec['input_hash']:
                raise common.BackendError('USB workspace configuration mismatch')
            if not rebuild_base:
                _verify_base(spec)
            _verify_project(spec)
            _verify_uploaded_inputs(spec)
            return dict(previous, cached=True, executed=False)
        if rebuild_base:
            request = {'path': spec['profile']['source_archive'], 'size': spec['lock']['archive_bytes'],
                       'sha256': spec['lock']['archive_sha256']}
            code = "import hashlib,json,platform;from pathlib import Path;r=" + repr(request) + ";p=Path(r['path']);assert platform.system()=='FreeBSD' and platform.release().startswith('15.1-');assert p.is_file() and not p.is_symlink() and p.stat().st_size==r['size'];h=hashlib.sha256();f=p.open('rb');[h.update(b) for b in iter(lambda:f.read(4194304),b'')];assert h.hexdigest()==r['sha256'];print(json.dumps({'source_archive_verified':True,'world_compilation_executed':False}))"
            verification = json.loads(_ssh(spec, code))
        else:
            verification = _verify_base(spec)
        project_verification = _verify_project(spec)
        root = spec['paths']['guest_root']
        code = ("import json,os;from pathlib import Path;p=Path(" + repr(root) + ");"
                "assert p.parent==Path('/var/daos-builds') and not p.is_symlink();"
                "p.mkdir(mode=0o700,exist_ok=False);(p/'logs').mkdir();(p/'receipts').mkdir();print(json.dumps({'created':str(p)}))")
        _ssh(spec, code)
        archive = w / 'inputs/usb-source.tar'
        with archive.open('xb') as output:
            output.write(spec['usb_archive_bytes'])
        provider, builder = _provider(spec), spec['profile']['builder']
        for source, destination in ((archive, spec['paths']['usb_source_archive']),
                                    (PROJECT / 'system/adb/build_freebsd.py', root + '/build_freebsd.py')):
            provider.copy_to(Path(builder['work_dir']), source, destination, user=builder['user'], timeout=120)
        if rebuild_base:
            _ssh(spec, "from pathlib import Path;Path(" + repr(root + '/base-recipe') + ").mkdir();print('{}')")
            recipe = [(PROJECT / 'system/freebsd/base/build_base.py', 'build_base.py'),
                      (PROJECT / 'system/freebsd/base/src.conf', 'src.conf'), (spec['lock_path'], 'source.lock.json'),
                      (spec['profile_path'].parent / spec['profile']['kernel']['config'], 'DaOS')]
            for source, name in recipe:
                provider.copy_to(Path(builder['work_dir']), source, root + '/base-recipe/' + name,
                                 user=builder['user'], timeout=120)
        if _spec(profile_path, workspace, rebuild_base)['input_hash'] != spec['input_hash']:
            raise common.BackendError('USB configuration sources changed during verification/upload')
        value = {'format': FORMAT, 'operation': 'configure', 'status': 'success',
                 'executed': False, 'verification_executed': True, 'reused': not rebuild_base,
                 'rebuild_base_requested': rebuild_base, 'input_hash': spec['input_hash'],
                 'inputs': spec['inputs'], 'verification': verification, 'paths': spec['paths'],
                 'project_snapshot_verification': project_verification,
                 'usb_source_archive_sha256': common._hash(archive), 'finished_at': common._now(),
                 'active_base': None if rebuild_base else spec['profile']['base'], 'receipt': str(state)}
        common._write_json(state, value)
        return value


def _stage(spec, name, argv, files, *, timeout=1800, preflight=None):
    w, g = spec['workspace'], spec['paths']
    run = name + '-' + uuid.uuid4().hex
    path = w / 'receipts' / (run + '.json')
    value = {'format': FORMAT, 'operation': name, 'status': 'running', 'executed': True,
             'input_hash': spec['input_hash'], 'argv': argv, 'started_at': common._now(), 'receipt': str(path)}
    if preflight is not None:
        value['preflight'] = preflight
    common._write_json(path, value)
    request = {'guest_root': g['guest_root'], 'log': g['guest_root'] + '/logs/' + run + '.log',
               'receipt': g['guest_root'] + '/receipts/' + run + '.json', 'argv': argv,
               'input_hash': spec['input_hash'], 'timeout': timeout}
    try:
        execution = _rpc(spec, 'run', request, timeout=timeout + 60)
        value['execution'] = execution
        cfg, provider = spec['profile']['builder'], _provider(spec)
        provider.copy_from(Path(cfg['work_dir']), request['log'], w / 'logs' / (run + '.log'), user=cfg['user'], timeout=120)
        value['log'] = str(w / 'logs' / (run + '.log'))
        if execution.get('status') != 'success' or execution.get('returncode') != 0:
            raise common.BackendError('Actual FreeBSD stage failed; see ' + value['log'])
        value['outputs'] = _rpc(spec, 'inspect', {'files': files})
        for item in value['outputs'].values():
            record = item.get('json')
            if record and record.get('status') in ('failed', 'running', 'preparing'):
                raise common.BackendError('Stage produced incomplete/failed artifact evidence')
        if name == 'usb':
            evidence = value['outputs']['build']['json']
            if (evidence.get('format') != 'DaOS-USB-ADB-FreeBSD-build-v1' or evidence.get('status') != 'completed'
                    or evidence.get('source_commit') != COMMIT):
                raise common.BackendError('A completed source-pinned USB build receipt is required')
            for role, key in (('transport', 'helper'), ('kernel_module', 'module')):
                artifact = value['outputs'][key]
                if (evidence['artifacts'][role]['sha256'] != artifact['sha256']
                        or evidence['artifacts'][role]['size'] != artifact['size']
                        or artifact.get('elf', {}).get('machine') != 183):
                    raise common.BackendError('Actual USB artifact differs from its compile/ELF evidence')
        elif name == 'packages':
            manifest = value['outputs']['manifest']['json']
            if (manifest.get('format') != 'DaOS-USB-ADB-image-overlay-v1'
                    or manifest.get('private_keys_staged') is not False
                    or sorted(manifest.get('authorized_public_key_fingerprints', [])) !=
                    sorted(spec['profile']['usb']['authorized_public_key_fingerprints'])):
                raise common.BackendError('USB package overlay identity/authorization closure differs from profile')
        elif name == 'image':
            evidence = value['outputs']['evidence']['json']
            artifact = evidence.get('image', {})
            if (evidence.get('format') != 'DaOS-freebsd-image-build-v1' or evidence.get('status') != 'assembled'
                    or artifact.get('sha256') != value['outputs']['image']['sha256']
                    or artifact.get('bytes') != value['outputs']['image']['size']
                    or not evidence.get('usb_adb')):
                raise common.BackendError('Assembler did not produce a verified native USB image/evidence pair')
        if _spec(spec['profile_path'], w, spec['rebuild_base'])['input_hash'] != spec['input_hash']:
            raise common.BackendError('Local source/profile inputs changed during stage execution')
        value.update(status='success', finished_at=common._now())
        common._write_json(path, value)
        common._write_json(w / 'state' / (name + '.json'), value)
        return value
    except BaseException as error:
        value.update(status='failed', error=str(error), finished_at=common._now())
        common._write_json(path, value)
        raise


def _cached(spec, name):
    state = spec['workspace'] / 'state' / (name + '.json')
    if not state.exists():
        return None
    value = common._read_json(state)
    if value.get('status') != 'success' or value.get('input_hash') != spec['input_hash']:
        raise common.BackendError('Stage cache differs from configured USB inputs')
    actual = _rpc(spec, 'inspect', {'files': {k: v['path'] for k, v in value['outputs'].items()}})
    if actual != value['outputs']:
        raise common.BackendError('Previously compiled/staged FreeBSD artifact changed')
    return dict(value, executed=False, cached=True)


def build_base(profile_path, workspace, *, jobs=12, rebuild_base=False):
    spec, configured = _load_spec(profile_path, workspace)
    if rebuild_base != spec['rebuild_base']:
        raise common.BackendError('Configure a new workspace with the explicitly selected rebuild_base policy first')
    with common._locked(spec['workspace']):
        _verify_uploaded_inputs(spec)
        if not rebuild_base:
            verified = _verify_base(spec)
            path = spec['workspace'] / 'state/base.json'
            value = dict(verified, operation='base', input_hash=spec['input_hash'], receipt=str(path))
            common._write_json(path, value)
            return value
        previous = _cached(spec, 'base')
        if previous and configured.get('active_base'):
            _verify_base(spec, configured['active_base'])
            return dict(previous, active_base=configured['active_base'])
        # The owned recipe copy uses the private DaOS splash config. Its real
        # original build-state remains intact; metadata enrichment is separate.
        result = previous or _stage(spec, 'base', _argv(spec, 'base', jobs=jobs),
                                    {'base': spec['paths']['base_work'] + '/build-state.json'}, timeout=14400)
        original = result['outputs']['base']['json']
        expected = {'buildworld', 'buildkernel', 'installworld', 'distribution', 'installkernel'}
        if (original.get('status') != 'completed' or not expected <= {s.get('step') for s in original.get('steps', [])}
                or not all(step.get('returncode') == 0 for step in original.get('steps', []))):
            raise common.BackendError('Full source world/kernel build did not complete')
        root = spec['paths']['base_work']
        enrich = ("import json,hashlib;from pathlib import Path;p=Path(" + repr(root + '/build-state.json') + ");"
                  "s=json.loads(p.read_text());c=Path(" + repr(root + '/kernel-config/DaOS') + ");"
                  "v=c.read_text();h=hashlib.sha256(c.read_bytes()).hexdigest();"
                  "assert 'device splash' in v and h==s['configuration_sha256']['DaOS'];"
                  "s.update(kernel_splash=True,kernel_config={'name':'DaOS','path':str(c),'content':v,'sha256':h,'ident':'DaOS','device_splash':True},world_recompiled=True);"
                  "q=p.with_name('build-state-with-splash.json');q.write_text(json.dumps(s,indent=2)+'\\n');"
                  "print(json.dumps({'receipt':str(q),'receipt_sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'source_root':s['source_directory'],'stage':s['stage_directory']}))")
        data = json.loads(_ssh(spec, enrich))
        active = dict(data, object_prefix=root + '/obj', src_conf=root + '/config/src.conf',
                      kernel_object=root + '/obj' + data['source_root'] + '/arm64.aarch64/sys/DaOS')
        configured['active_base'] = active
        common._write_json(spec['workspace'] / 'state/configure.json', configured)
        return dict(result, active_base=active, world_recompiled=True, kernel_recompiled=True)


def _active(spec, configured):
    base = configured.get('active_base')
    if base is None:
        raise common.BackendError('Run the explicit full base build before USB/package/image stages')
    _verify_uploaded_inputs(spec)
    current = _verify_base(spec, base)
    previous = configured.get('verification', {})
    for field in ('SDK_header_sha256', 'kernel_object_options_sha256'):
        if field in previous and previous[field] != current[field]:
            raise common.BackendError('The configured source SDK/kernel object inputs changed')
    return base


def build_usb(profile_path, workspace, *, jobs=12):
    spec, configured = _load_spec(profile_path, workspace)
    with common._locked(spec['workspace']):
        base = _active(spec, configured)
        previous = _cached(spec, 'usb')
        if previous:
            return previous
        argv = _argv(spec, 'usb', base, jobs=jobs)
        if configured['usb_source_archive_sha256'] != spec['usb_archive_sha256']:
            raise common.BackendError('Frozen USB source archive identity changed')
        root = spec['paths']['usb_work']
        return _stage(spec, 'usb', argv, {'build': root + '/build-evidence.json',
            'helper': root + '/output/helper/daos-usb-adb', 'module': root + '/output/kmod/daos_adb_template.ko'})


def packages(profile_path, workspace):
    spec, configured = _load_spec(profile_path, workspace)
    with common._locked(spec['workspace']):
        base = _active(spec, configured)
        if _cached(spec, 'usb') is None:
            raise common.BackendError('Build the source-bound native USB helper/module first')
        _verify_project(spec)
        previous = _cached(spec, 'packages')
        if previous:
            return previous
        root = spec['paths']['bundle']
        return _stage(spec, 'packages', _argv(spec, 'packages', base),
                      {'manifest': root + '/manifest.json', 'integration': root + '/receipt.json'})


def _scratch_request(spec):
    total_mib = sum(spec['profile']['storage'][name + '_mib']
                    for name in ('root', 'var', 'calibration', 'photos', 'efi'))
    # Prepared filesystem trees, partition images, and combined raw disk can
    # coexist. Reserve their declared capacity, plus GPT/alignment overhead.
    return {'root': spec['profile']['image_work_root'], 'filesystem': 'tmpfs',
            'required_free_bytes': (3 * total_mib + 32) * 1024 * 1024,
            'space_policy': 'three copies of declared partition capacity plus 32 MiB'}


def image_preflight(profile_path, workspace):
    """Verify current native scratch without creating an image or workspace."""
    spec = _spec(profile_path, workspace)
    return _rpc(spec, 'scratch', _scratch_request(spec))


def image(profile_path, workspace, *, guest_work_dir=None):
    spec, configured = _load_spec(profile_path, workspace)
    with common._locked(spec['workspace']):
        base = _active(spec, configured)
        if _cached(spec, 'packages') is None:
            raise common.BackendError('Stage and verify the native USB integration bundle first')
        _verify_project(spec)
        previous = _cached(spec, 'image')
        if previous and (guest_work_dir is None or Path(previous['outputs']['evidence']['path']).parent == Path(guest_work_dir)):
            return previous
        scratch = _rpc(spec, 'scratch', _scratch_request(spec))
        argv = _argv(spec, 'image', base, guest_work_dir=guest_work_dir)
        root = guest_work_dir or spec['paths']['image_work']
        return _stage(spec, 'image', argv, {'image': root + '/DaOS-BSD-RPi4-USB-ADB-arm64.img',
                                           'evidence': root + '/image-evidence.json'}, timeout=3600,
                      preflight={'image_scratch': scratch})


build = image
_workspace = common._workspace


def fetch(profile_path, workspace, *, rebuild_base=False):
    """Verify the locked, already provisioned source archive on the native builder.

    The lock records a git-archive pin, not an invented binary-image download.
    configure performs the same archive verification plus complete base reuse.
    """
    spec = _spec(profile_path, workspace, rebuild_base)
    p = spec['profile']['source_archive']
    wanted = spec['lock']
    code = ("import json,hashlib,platform;from pathlib import Path;p=Path(" + repr(p) + ");"
            "assert platform.system()=='FreeBSD' and platform.release().startswith('15.1-');"
            "assert p.is_file() and not p.is_symlink() and p.stat().st_size==" + str(wanted['archive_bytes']) + ";"
            "h=hashlib.sha256();f=p.open('rb');[h.update(v) for v in iter(lambda:f.read(4194304),b'')];"
            "assert h.hexdigest()==" + repr(wanted['archive_sha256']) + ";"
            "print(json.dumps({'status':'success','executed':False,'verification_executed':True,'reused':True,'source_archive_sha256':h.hexdigest()}))")
    return json.loads(_ssh(spec, code))

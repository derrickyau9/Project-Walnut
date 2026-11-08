"""Bounded rootless ARM64 update-model guest adapter, independent of product builds.

The caller serializes the whole VM lifecycle and owns slot/data compatibility.
Only regular image files are attached; there are no host mounts, external NICs,
host shares, package installs or physical-device update operations.
"""
from __future__ import annotations

from contextlib import ExitStack
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

from tools.image_boot import MAX_SERIAL, _stop, check_ext4, digest, pinned, qemu_command
from tools.image_build import DATA_UUID, ROOT_UUID, arm_elf, newc
from tools.image_fs import _run, _tool, extract_file

ROOT = Path(__file__).resolve().parents[1]
MAX_BOOTSTRAP = 128 * 1024**2
MAX_HEALTH_PROFILE = 256 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
LEGACY_PROFILE = {'format': 'hf-update-health-profile-v1', 'name': 'legacy-v1',
                  'data_abi': 1, 'capture_root': 'ui-r7/session'}


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def validate_health_profile(profile=None):
    """Pin a legacy or bound-v2 health contract before creating any output."""
    if profile is None:
        return dict(LEGACY_PROFILE)
    if type(profile) is not dict:
        raise ValueError('health profile must be an object')
    encoded = _canonical(profile)
    if len(encoded) > MAX_HEALTH_PROFILE:
        raise ValueError('health profile exceeds bound')
    result = strict_json(encoded)
    if result == LEGACY_PROFILE and type(result.get('data_abi')) is int:
        return result
    keys = {'format', 'name', 'data_abi', 'capture_root', 'expected_settings', 'expected_binding',
            'expected_calibration', 'expected_capture_id', 'expected_dng_sha', 'expected_native_sha'}
    if (set(result) != keys or result.get('format') != LEGACY_PROFILE['format'] or result.get('name') != 'bound-v2' or
            type(result.get('data_abi')) is not int or result['data_abi'] != 2):
        raise ValueError('unsupported health profile')
    root = result['capture_root']
    if (not isinstance(root, str) or len(root) > 255 or not re.fullmatch('[A-Za-z0-9_./-]+', root) or
            root.startswith('/') or any(part in ('', '.', '..') for part in root.split('/'))):
        raise ValueError('capture root must be a canonical relative data path')
    settings = result['expected_settings']
    if (type(settings) is not dict or set(settings) != {'sensor_mode', 'requested_exposure_us', 'requested_analogue_gain', 'output_format', 'simulation_scene'} or
            settings['sensor_mode'] not in ('sim-small', 'sim-full') or settings['output_format'] != 'dng' or
            settings['simulation_scene'] not in ('scene', 'dark', 'flat') or
            type(settings['requested_exposure_us']) is not int or not 100 <= settings['requested_exposure_us'] <= 30000000 or
            type(settings['requested_analogue_gain']) not in (int, float) or not 1 <= settings['requested_analogue_gain'] <= 16):
        raise ValueError('invalid expected persistent device settings')
    from camera.calibration import validate_binding
    binding = validate_binding(result['expected_binding'])
    if binding != result['expected_binding']:
        raise ValueError('noncanonical expected calibration binding')
    expected_descriptor = {'sensor_id': 'HF-Sim', 'binding_id': binding['binding_id'], 'calibration_id': binding['binding_id'],
                           'package_checksum': binding['package_checksum'], 'hardware_calibrated': False,
                           'pixel_corrections_applied': False}
    descriptor = result['expected_calibration']
    if (type(descriptor) is not dict or descriptor != expected_descriptor or
            descriptor.get('hardware_calibrated') is not False or descriptor.get('pixel_corrections_applied') is not False):
        raise ValueError('expected calibration descriptor does not match its binding')
    _identities(result['expected_capture_id'], 'A', 1, 'healthy', result['expected_capture_id'],
                result['expected_dng_sha'], result['expected_native_sha'])
    return result


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate JSON member')
            result[key] = value
        return result
    def finite(text):
        value = float(text)
        if not math.isfinite(value): raise ValueError('nonfinite JSON')
        return value
    def constant(_): raise ValueError('nonfinite JSON')
    return json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=constant)


def _json_file(path, maximum=1024 * 1024):
    with pinned(path) as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum: raise ValueError('JSON input exceeds bound')
    result = strict_json(raw)
    if type(result) is not dict: raise ValueError('JSON object required')
    return result


def _write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as output:
        json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
        output.write('\n'); output.flush(); os.fsync(output.fileno())


def _new_directory(path):
    path = Path(os.path.abspath(os.fspath(path)))
    if path.parent.resolve(strict=True) != path.parent or os.path.lexists(path):
        raise ValueError('output must be new beneath an existing canonical parent')
    path.mkdir(mode=0o700)
    return path


def _linux():
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('update guest operations require a non-root Linux host')


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def parse_newc(raw):
    """Read only a bounded, root-owned newc archive; never extract host paths."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BOOTSTRAP:
        raise ValueError('bootstrap exceeds bound')
    entries, offset = {}, 0
    while offset < len(raw):
        if len(entries) >= 128 or raw[offset:offset + 6] != b'070701' or offset + 110 > len(raw):
            raise ValueError('invalid bounded newc header')
        fields_raw = raw[offset + 6:offset + 110]
        if re.fullmatch(b'[0-9a-fA-F]{104}', fields_raw) is None:
            raise ValueError('invalid newc integer')
        fields = [int(fields_raw[i:i + 8], 16) for i in range(0, 104, 8)]
        mode, owner, group, links, size, name_size = fields[1], fields[2], fields[3], fields[4], fields[6], fields[11]
        if owner or group or links != 1 or not 1 <= name_size <= 4096 or size > 32 * 1024**2:
            raise ValueError('unsupported newc ownership, link or size')
        start = offset + 110
        end = start + name_size
        if end > len(raw) or raw[end - 1] != 0 or b'\0' in raw[start:end - 1]:
            raise ValueError('invalid newc name')
        try: name = raw[start:end - 1].decode('ascii')
        except UnicodeError as error: raise ValueError('nonportable bootstrap name') from error
        data_start = (end + 3) & ~3
        data_end = data_start + size
        next_offset = (data_end + 3) & ~3
        if next_offset > len(raw) or any(raw[end:data_start]) or any(raw[data_end:next_offset]):
            raise ValueError('truncated or unaligned newc entry')
        if name == 'TRAILER!!!':
            if size or mode or any(raw[next_offset:]): raise ValueError('invalid newc trailer')
            return entries
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or name.startswith('/') or
                any(part in ('', '.', '..') for part in name.split('/')) or name in entries):
            raise ValueError('unsafe or duplicate newc member')
        kind = stat.S_IFMT(mode)
        if kind not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFCHR) or kind != stat.S_IFREG and size:
            raise ValueError('unsupported newc member type')
        for parent in Path(name).parents:
            if str(parent) != '.' and parent.as_posix() in entries and stat.S_IFMT(entries[parent.as_posix()][0]) != stat.S_IFDIR:
                raise ValueError('newc member traverses a non-directory')
        entries[name] = (mode, raw[data_start:data_end])
        offset = next_offset
    raise ValueError('missing newc trailer')


def prepare_runtime(base_build: Path, destination: Path, *, health_profile=None) -> dict:
    """Build one NEW bootstrap from verified immutable base kernel modules."""
    _linux()
    profile = validate_health_profile(health_profile)
    profile_bytes = _canonical(profile)
    base = Path(base_build).resolve(strict=True)
    manifest = _json_file(base / 'build-evidence.json')
    if manifest.get('format') != 'hf-arm64-image-build-v1' or manifest.get('uid') != 1000:
        raise ValueError('verified base ARM64 image build required')
    expected = {'root': manifest.get('rootfs_image', {}).get('sha256'),
                'kernel': manifest.get('kernel_sha256'), 'bootstrap': manifest.get('bootstrap_sha256')}
    if not all(_sha(value) for value in expected.values()): raise ValueError('missing base image hashes')
    sources = [ROOT / 'system/update-model/init.c', ROOT / 'system/update-model/health.py', Path(__file__)]
    hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    compiler = shutil.which('aarch64-linux-gnu-gcc')
    if not compiler: raise RuntimeError('existing AArch64 static C compiler required')
    with ExitStack() as stack:
        inputs = {key: stack.enter_context(pinned(base / filename)) for key, filename in
                  [('root', 'rootfs.ext4'), ('kernel', 'kernel.img'), ('bootstrap', 'bootstrap.cpio.gz')]}
        for key, stream in inputs.items():
            maximum = 4096 * 1024**2 if key == 'root' else 128 * 1024**2
            if not 1 <= os.fstat(stream.fileno()).st_size <= maximum or digest(stream) != expected[key]:
                raise ValueError('base image input hash/size mismatch')
        check_ext4(inputs['root'], ROOT_UUID)
        with gzip.GzipFile(fileobj=inputs['bootstrap']) as packed:
            raw = packed.read(MAX_BOOTSTRAP + 1)
        entries = parse_newc(raw)
        if 'modules/load-order' not in entries or stat.S_IFMT(entries['modules/load-order'][0]) != stat.S_IFREG:
            raise ValueError('base module load order missing')
        order = entries['modules/load-order'][1]
        if len(order) > 4096 or not order.endswith(b'\n'): raise ValueError('invalid module load order')
        try: names = order.decode('ascii').splitlines()
        except UnicodeError as error: raise ValueError('invalid module load order') from error
        if not 1 <= len(names) <= 32 or len(set(names)) != len(names) or any(not re.fullmatch('[a-z0-9_-]{1,120}', name) for name in names):
            raise ValueError('unsafe or repeated module name')
        modules = {}
        for name in names:
            mode, data = entries.get('modules/' + name + '.ko', (0, b''))
            expected_sha = manifest.get('modules', {}).get(name, {}).get('sha256')
            if (stat.S_IFMT(mode) != stat.S_IFREG or not _sha(expected_sha) or hashlib.sha256(data).hexdigest() != expected_sha or
                    len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183):
                raise ValueError('base ARM64 module identity mismatch')
            modules[name] = data
        output = _new_directory(destination)
        command = [compiler, '-std=c11', '-O2', '-static', '-Wall', '-Wextra', '-Werror', str(sources[0]), '-o', str(output / 'init')]
        compiled = _run(command, timeout=60)
        if compiled['returncode']: raise RuntimeError('update init compile failed: ' + compiled['stderr'][:2000])
        arm_elf(output / 'init', static=True)
        health = sources[1].read_bytes()
        if not 1 <= len(health) <= 65536: raise ValueError('health source exceeds bound')
        members = [(name, b'', stat.S_IFDIR | 0o755, 0, 0) for name in ('dev', 'proc', 'sys', 'newroot', 'modules')]
        members += [('dev/console', b'', stat.S_IFCHR | 0o600, 5, 1), ('dev/null', b'', stat.S_IFCHR | 0o666, 1, 3),
                    ('init', (output / 'init').read_bytes(), stat.S_IFREG | 0o755, 0, 0),
                    ('health.py', health, stat.S_IFREG | 0o444, 0, 0),
                    ('profile.json', profile_bytes, stat.S_IFREG | 0o444, 0, 0),
                    ('modules/load-order', order, stat.S_IFREG | 0o644, 0, 0)]
        members += [('modules/' + name + '.ko', data, stat.S_IFREG | 0o600, 0, 0) for name, data in modules.items()]
        bootstrap = gzip.compress(newc(members), mtime=0)
        with (output / 'bootstrap.cpio.gz').open('xb') as target:
            target.write(bootstrap); target.flush(); os.fsync(target.fileno())
        inputs['kernel'].seek(0)
        with (output / 'kernel.img').open('xb') as target:
            shutil.copyfileobj(inputs['kernel'], target, 1024 * 1024); target.flush(); os.fsync(target.fileno())
        for key, stream in inputs.items():
            if digest(stream) != expected[key]: raise ValueError('base input changed during runtime preparation')
        if hashes != {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}:
            raise ValueError('update guest source changed during preparation')
    result = {'format': 'hf-arm64-update-runtime-v1', 'kernel': str(output / 'kernel.img'),
              'bootstrap': str(output / 'bootstrap.cpio.gz'), 'kernel_sha256': expected['kernel'],
              'bootstrap_sha256': hashlib.sha256(bootstrap).hexdigest(), 'base_root_sha256': expected['root'],
              'base_bootstrap_sha256': expected['bootstrap'], 'source_sha256': hashes,
              'init_sha256': hashlib.sha256((output / 'init').read_bytes()).hexdigest(),
              'health_sha256': hashlib.sha256(health).hexdigest(),
              'health_profile': profile, 'health_profile_sha256': hashlib.sha256(profile_bytes).hexdigest(),
              'modules_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in modules.items()},
              'compile': compiled, 'scope': 'generic virtual ARM64 update model; synthetic camera only'}
    _write_json(output / 'runtime-evidence.json', result)
    return result


def _identities(attempt_id, slot, version, health_mode, expected_capture_id, expected_dng_sha, expected_native_sha):
    for value in (attempt_id, expected_capture_id):
        if not isinstance(value, str) or str(uuid.UUID(value)) != value: raise ValueError('canonical UUID required')
    if slot not in ('A', 'B') or type(version) is not int or not 1 <= version <= 2147483647:
        raise ValueError('invalid slot or version')
    if health_mode not in ('healthy', 'fail') or not _sha(expected_dng_sha) or not _sha(expected_native_sha):
        raise ValueError('invalid health mode or expected hash')


def parse_serial(raw, *, attempt_id, slot, version, health_mode):
    if not isinstance(raw, bytes) or len(raw) > MAX_SERIAL: raise ValueError('serial output exceeds bound')
    text = raw.decode('utf-8', 'replace').replace('\r', '')
    suffix = 'attempt_id=' + re.escape(attempt_id)
    markers = {
        'HF_UPDATE_INIT ': r'pid=1 machine=aarch64 ' + suffix + ' slot=' + re.escape(slot) + ' version=' + re.escape(str(version)),
        'HF_UPDATE_ROOT ': r'readonly=1 filesystem=ext4 device=vda',
        'HF_UPDATE_DATA ': r'readwrite=1 filesystem=ext4 device=vdb',
        'HF_UPDATE_RUNTIME ': r'uid=1000 ' + suffix,
        'HF_UPDATE_CHILD ': r'status=0 ' + suffix,
        'HF_UPDATE_DATA_UNMOUNTED ': r'clean=1',
        'HF_UPDATE_RESULT ': r'PASS ' + suffix + ' slot=' + re.escape(slot) + ' version=' + re.escape(str(version))}
    for prefix, pattern in markers.items():
        lines = re.findall('^' + re.escape(prefix) + '(.*)$', text, re.M)
        if len(lines) != 1 or re.fullmatch(pattern, lines[0]) is None:
            raise ValueError('guest did not uniquely verify update boot sequence')
    lines = re.findall(r'^HF_UPDATE_HEALTH (.*)$', text, re.M)
    if len(lines) != 1 or len(lines[0]) > 65536: raise ValueError('missing bounded unique health commitment')
    value = strict_json(lines[0])
    if (type(value) is not dict or value.get('attempt_id') != attempt_id or value.get('slot') != slot or
            type(value.get('version')) is not int or value.get('version') != version or value.get('health_mode') != health_mode or type(value.get('healthy')) is not bool or
            value.get('report') != attempt_id + '.json' or not _sha(value.get('report_sha256'))):
        raise ValueError('health commitment identity mismatch')
    return value


def boot(rootfs, kernel, bootstrap, data, output, *, attempt_id, slot, version, health_mode,
         expected_capture_id, expected_dng_sha, expected_native_sha, timeout=120, initialize_fixtures=False,
         health_profile=None) -> dict:
    """Boot the selected immutable root with the caller's one shared data image."""
    _linux()
    _identities(attempt_id, slot, version, health_mode, expected_capture_id, expected_dng_sha, expected_native_sha)
    profile = validate_health_profile(health_profile)
    profile_sha = hashlib.sha256(_canonical(profile)).hexdigest()
    bound = profile['name'] == 'bound-v2'
    if bound and (profile['expected_capture_id'] != expected_capture_id or profile['expected_dng_sha'] != expected_dng_sha or
                  profile['expected_native_sha'] != expected_native_sha):
        raise ValueError('bound profile differs from the requested capture/native identities')
    if type(initialize_fixtures) is not bool: raise ValueError('initialize_fixtures must be boolean')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError('timeout must be finite within 1-600 seconds')
    executable = shutil.which('qemu-system-aarch64')
    if not executable: raise RuntimeError('existing ARM64 system QEMU required')
    destination = _new_directory(output)
    process = None
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            paths = {'root': Path(rootfs), 'kernel': Path(kernel), 'bootstrap': Path(bootstrap), 'data': Path(data)}
            files = {name: stack.enter_context(pinned(path, writable=name == 'data')) for name, path in paths.items()}
            identities = [(os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) for stream in files.values()]
            if len(set(identities)) != 4: raise ValueError('guest image inputs must be distinct regular files')
            for name, stream in files.items():
                length = os.fstat(stream.fileno()).st_size
                minimum, maximum = (32 * 1024**2, 4096 * 1024**2) if name in ('root', 'data') else (1, 128 * 1024**2)
                if not minimum <= length <= maximum: raise ValueError('image input exceeds supported size')
            check_ext4(files['root'], ROOT_UUID); check_ext4(files['data'], DATA_UUID)
            before = {name: digest(stream) for name, stream in files.items()}
            descriptor_paths = {name: '/proc/self/fd/' + str(stream.fileno()) for name, stream in files.items()}
            command = qemu_command(executable, descriptor_paths, 'first')
            command[command.index('-append') + 1] = ' '.join(['console=ttyAMA0', 'panic=-1', 'rdinit=/init',
                'HASSLEFREE_ARM64_UPDATE=1', 'HF_UPDATE_GUEST=1', 'HF_UPDATE_ATTEMPT_ID=' + attempt_id, 'HF_UPDATE_SLOT=' + slot,
                'HF_UPDATE_VERSION=' + str(version), 'HF_UPDATE_HEALTH_MODE=' + health_mode,
                'HF_UPDATE_INITIALIZE_FIXTURES=' + ('1' if initialize_fixtures else '0'),
                'HF_UPDATE_CAPTURE_ID=' + expected_capture_id, 'HF_UPDATE_DNG_SHA=' + expected_dng_sha,
                'HF_UPDATE_NATIVE_SHA=' + expected_native_sha])
            serial_path = destination / 'serial.log'
            with serial_path.open('xb') as serial:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           pass_fds=tuple(stream.fileno() for stream in files.values()))
                problems = []
                def collect():
                    total = 0
                    try:
                        while True:
                            block = process.stdout.read(4096)
                            if not block: break
                            total += len(block)
                            if total > MAX_SERIAL:
                                problems.append('serial limit'); process.terminate(); break
                            serial.write(block); serial.flush()
                    except (OSError, ValueError) as error: problems.append(type(error).__name__)
                reader = threading.Thread(target=collect, daemon=True, name='arm64-update-serial')
                reader.start()
                try: code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('bounded update guest timed out; data and partial evidence retained') from error
                finally:
                    _stop(process); reader.join(timeout=5); process.stdout.close()
                if code or reader.is_alive() or problems: raise RuntimeError('update guest failed; see retained serial.log')
            compact = parse_serial(serial_path.read_bytes(), attempt_id=attempt_id, slot=slot, version=version, health_mode=health_mode)
            after = {name: digest(stream) for name, stream in files.items()}
            if any(before[name] != after[name] for name in ('root', 'kernel', 'bootstrap')):
                raise RuntimeError('immutable selected guest input changed')
            checked = _run([_tool('e2fsck'), '-fn', descriptor_paths['data']], pass_fds=(files['data'].fileno(),), timeout=60)
            if checked['returncode']: raise RuntimeError('persistent data did not verify clean after update guest shutdown')
        extracted = destination / (attempt_id + '.json')
        report_info = extract_file(paths['data'], '/update-model-reports/' + attempt_id + '.json', extracted, 256 * 1024)
        if report_info['sha256'] != compact['report_sha256']: raise ValueError('guest health report commitment mismatch')
        report = _json_file(extracted, 256 * 1024)
        if (report.get('format') != 'hf-arm64-update-health-v1' or report.get('attempt_id') != attempt_id or report.get('slot') != slot or
                type(report.get('version')) is not int or report.get('version') != version or
                report.get('health_mode') != health_mode or report.get('healthy') is not compact['healthy'] or
                report.get('app_checks_passed') is not True or report.get('native_process_closed') is not True or
                report.get('same_capture_verified') is not True or type(report.get('journal_rows')) is not int or report['journal_rows'] != 1 or
                report.get('request_id') != expected_capture_id or report.get('capture_id') != expected_capture_id or
                report.get('frame_id') != expected_capture_id or report.get('dng_sha256') != expected_dng_sha or
                report.get('native_binary_sha256') != expected_native_sha or report.get('effective_uid') != 1000 or report.get('machine') != 'aarch64'):
            raise ValueError('actual guest application health evidence is incomplete or mismatched')
        protected = report.get('protected_sha256')
        protected_keys = {'configuration', 'calibration', 'original_dng', 'journal_capture_rows'}
        if bound:
            protected_keys.update(('device_settings', 'calibration_binding'))
        if (type(protected) is not dict or set(protected) != protected_keys or
                not all(_sha(value) for value in protected.values()) or protected['original_dng'] != expected_dng_sha):
            raise ValueError('guest protected data commitment missing')
        if bound or 'data_profile' in report:
            if (report.get('data_profile') != profile['name'] or type(report.get('data_abi')) is not int or
                    report['data_abi'] != profile['data_abi'] or report.get('capture_root') != profile['capture_root'] or
                    report.get('health_profile_sha256') != profile_sha):
                raise ValueError('guest health profile does not match its pinned bootstrap profile')
        if bound:
            if (report.get('api_version') != '1.1' or type(report.get('capture_record_versions')) is not list or
                    any(type(item) is not int for item in report['capture_record_versions']) or
                    not {1, 2}.issubset(report['capture_record_versions']) or report.get('settings_persistent') is not True or
                    report.get('settings_format') != 'hf-device-settings-v1' or report.get('device_settings') != profile['expected_settings'] or
                    report.get('calibration') != profile['expected_calibration'] or type(report.get('capture_record_version')) is not int or
                    report['capture_record_version'] != 2 or report.get('calibration_binding') != profile['expected_binding'] or
                    report.get('hardware_calibrated') is not False or report.get('pixel_corrections_applied') is not False or
                    report.get('actual_exposure_us') != profile['expected_settings']['requested_exposure_us'] or
                    protected['device_settings'] != hashlib.sha256(_canonical(profile['expected_settings'])).hexdigest() or
                    protected['calibration_binding'] != hashlib.sha256(_canonical(profile['expected_binding'])).hexdigest()):
                raise ValueError('guest did not establish the pinned v2 settings and calibration contract')
        if compact['healthy'] != (health_mode == 'healthy'): raise ValueError('guest health result differs from explicit release mode')
        result = {'format': 'hf-arm64-update-boot-v1', 'passed': True, 'healthy': compact['healthy'],
                  'attempt_id': attempt_id, 'slot': slot, 'version': version, 'health_mode': health_mode,
                  'rootfs_unchanged': True, 'rootfs_sha256_before': before['root'], 'rootfs_sha256_after': after['root'],
                  'kernel_sha256': before['kernel'], 'bootstrap_sha256': before['bootstrap'],
                  'data_sha256_before': before['data'], 'data_sha256_after': after['data'],
                  'guest_report': report, 'report_sha256': report_info['sha256'], 'protected_sha256': protected,
                  'data_profile': profile['name'], 'data_abi': profile['data_abi'], 'capture_root': profile['capture_root'],
                  'health_profile_sha256': profile_sha,
                  'serial_sha256': hashlib.sha256(serial_path.read_bytes()).hexdigest(), 'data_fsck': checked,
                  'command': command, 'seconds': time.monotonic() - started, 'host_mounts': False,
                  'network': False, 'host_filesystem_sharing': False,
                  'scope': 'generic virtual ARM64 update model, synthetic camera only; no physical update validation'}
        _write_json(destination / 'boot-evidence.json', result)
        return result
    finally:
        if process is not None: _stop(process)

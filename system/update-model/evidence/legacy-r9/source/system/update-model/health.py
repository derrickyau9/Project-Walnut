"""Guest-only health evidence for the generic ARM64 virtual update model.

The selected immutable system queries one already committed synthetic capture.
It never acquires control, submits a capture, migrates data, or repairs evidence.
The persistent configuration and calibration files are deliberately synthetic
fixtures; they do not establish physical calibration or production update safety.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import sys
import uuid


APP_ROOT = Path('/opt/hasslefree')
RELEASE_PATH = Path('/etc/hf-update-release.json')
NATIVE_PATH = APP_ROOT / 'bin/hf-camerad'
CAMERA_ROOT = Path('/data/ui-r7/session')
PERSIST_ROOT = Path('/data/update-model-persist')
REPORT_ROOT = Path('/data/update-model-reports')
MAX_DNG_BYTES = 6064 * 4040 * 2 + 65536


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite JSON value')
    return result


def _constant(_):
    raise ValueError('nonfinite JSON value')


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_float=_finite,
                      parse_constant=_constant)


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('canonical lowercase UUID required')
    return value


def _sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('lowercase SHA-256 required')
    return value


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _regular(path, maximum):
    path = Path(path)
    initial = path.lstat()
    if (not stat.S_ISREG(initial.st_mode) or initial.st_size > maximum or
            path.resolve(strict=True) != path):
        raise ValueError('bounded canonical regular guest file required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    stream = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _snapshot(opened) != _snapshot(initial):
            raise ValueError('guest file changed during open')
        stream = os.fdopen(descriptor, 'rb')
        yield stream
        if (_snapshot(os.fstat(stream.fileno())) != _snapshot(initial) or
                _snapshot(path.lstat()) != _snapshot(initial)):
            raise ValueError('guest file changed during inspection')
    finally:
        if stream is None:
            os.close(descriptor)
        else:
            stream.close()


def _read(path, maximum=65536):
    with _regular(path, maximum) as stream:
        return stream.read(maximum + 1)


def _digest(path, maximum=64 * 1024**2):
    digest = hashlib.sha256()
    with _regular(path, maximum) as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def _elf(path):
    path = Path(path).resolve(strict=True)
    with _regular(path, 64 * 1024**2) as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:6] != b'\x7fELF\x02\x01' or
            int.from_bytes(header[18:20], 'little') != 183):
        raise ValueError('actual little-endian AArch64 ELF64 required')
    return {'file': path.name, 'elf_machine': 183, 'sha256': _digest(path)}


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path:
        raise ValueError('canonical ordinary guest directory required')


def _write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fixtures(capture_id, initialize=False):
    expected = {
        'config.json': _canonical({'format': 'hf-update-persistent-config-v1',
                                  'retained_capture_id': capture_id,
                                  'synthetic': True, 'owner_setting': 'preserve-across-slots'}),
        'calibration.json': _canonical({'format': 'hf-update-persistent-calibration-v1',
                                       'sensor_id': 'HF-Sim',
                                       'calibration_id': 'synthetic-uncalibrated',
                                       'status': 'synthetic-uncalibrated', 'measured': False}),
    }
    created = False
    if initialize:
        try:
            PERSIST_ROOT.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
    elif not PERSIST_ROOT.exists():
        raise ValueError('persistent fixtures are missing; explicit initialization is required')
    _directory(PERSIST_ROOT)
    if created:
        for name, raw in expected.items():
            _write_new(PERSIST_ROOT / name, raw)
        _sync_directory(PERSIST_ROOT)
        _sync_directory(PERSIST_ROOT.parent)
    if {path.name for path in PERSIST_ROOT.iterdir()} != set(expected):
        raise ValueError('persistent fixture inventory changed; no automatic repair')
    result = {}
    for name, raw in expected.items():
        if _read(PERSIST_ROOT / name) != raw:
            raise ValueError('persistent fixture changed; no automatic repair')
        result['configuration' if name == 'config.json' else 'calibration'] = hashlib.sha256(raw).hexdigest()
    return result, created


def _capture_rows(capture_id):
    journal = CAMERA_ROOT / 'journal.sqlite3'
    # Opening SQLite is read-only here; only the actual native authority owns
    # its ordinary startup epoch update. Compare capture rows, not volatile DB
    # pages or lock/WAL metadata that naturally change on a process restart.
    with _regular(journal, 64 * 1024**2):
        with closing(sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True, timeout=3)) as database:
            rows = database.execute('SELECT request_id,request_json,record_json,status FROM captures ORDER BY request_id').fetchall()
    if len(rows) != 1 or rows[0][0] != capture_id or rows[0][3] != 'complete':
        raise ValueError('persistent journal must retain exactly the original complete capture')
    record = _json(rows[0][2])
    if (record.get('request_id') != capture_id or record.get('capture_id') != capture_id or
            record.get('frame_id') != capture_id or record.get('status') != 'complete'):
        raise ValueError('persistent journal capture identity changed')
    return rows, hashlib.sha256(_canonical(rows)).hexdigest()


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--slot', required=True, choices=('A', 'B'))
    parser.add_argument('--version', required=True, type=int)
    parser.add_argument('--health-mode', required=True, choices=('healthy', 'fail'))
    parser.add_argument('--initialize-fixtures', action='store_true')
    parser.add_argument('--expected-capture-id', required=True)
    parser.add_argument('--expected-dng-sha', required=True)
    parser.add_argument('--expected-native-sha', required=True)
    args = parser.parse_args(argv)
    _uuid(args.attempt_id)
    _uuid(args.expected_capture_id)
    _sha(args.expected_dng_sha)
    _sha(args.expected_native_sha)
    if not 1 <= args.version <= 2147483647:
        raise ValueError('version must be a positive signed 32-bit integer')
    return args


def check(args):
    # Refuse a host or qemu-user invocation before touching persistent state.
    if (sys.platform != 'linux' or platform.machine().lower() != 'aarch64' or
            os.geteuid() != 1000 or os.getppid() != 1 or os.environ.get('HF_UPDATE_GUEST') != '1'):
        raise RuntimeError('update health requires actual Linux ARM64 guest UID 1000')
    with open('/proc/cmdline', 'rb') as command_line:
        boot_arguments = command_line.read(4097)
    if len(boot_arguments) > 4096 or b'HF_UPDATE_GUEST=1' not in boot_arguments.split():
        raise RuntimeError('update health requires the explicit full-system guest boot marker')
    python_identity = _elf(sys.executable)
    release = _json(_read(RELEASE_PATH, 16384))
    if (type(release) is not dict or release.get('format') != 'hf-update-release-v1' or
            release.get('target') != 'generic-qemu-virt-aarch64' or
            type(release.get('data_abi')) is not int or release.get('data_abi') != 1 or
            release.get('native_abi') != 'hf-native-rpc-v1' or
            type(release.get('version')) is not int or release.get('version') != args.version or
            release.get('health_mode') != args.health_mode):
        raise ValueError('selected root release marker does not match this attempt')
    native_identity = _elf(NATIVE_PATH)
    if native_identity['sha256'] != args.expected_native_sha:
        raise ValueError('selected root native binary SHA-256 mismatch')
    _directory(CAMERA_ROOT)
    rows_before, journal_before = _capture_rows(args.expected_capture_id)
    source = CAMERA_ROOT / 'photos' / (args.expected_capture_id + '.dng')
    if _digest(source, MAX_DNG_BYTES) != args.expected_dng_sha:
        raise ValueError('original retained DNG bytes changed')
    sys.path.insert(0, str(APP_ROOT))
    from camera.camerad.native_client import NativeClient
    from camera.dng.inspect import inspect_dng
    with _regular(source, MAX_DNG_BYTES) as stream:
        structure = inspect_dng(stream)
    if (structure['request_id'] != args.expected_capture_id or structure['metadata_state'] != 'valid' or
            structure['cfa'] != 'RGGB' or structure['black_level'] != 64 or
            structure['white_level'] != 16383):
        raise ValueError('original retained DNG structure or identity changed')
    protected_before, fixtures_created = _fixtures(args.expected_capture_id, args.initialize_fixtures)
    client = NativeClient(CAMERA_ROOT, command=[str(NATIVE_PATH)], timeout_seconds=5)
    try:
        state = client.get_state()
        if (state.get('authority') != 'native' or state.get('journal_healthy') is not True or
                state.get('camera_state') != 'idle'):
            raise ValueError('selected native authority or persistent journal is not healthy')
        record = client.query_capture(args.expected_capture_id)
        if (record.get('status') != 'complete' or record.get('request_id') != args.expected_capture_id or
                record.get('capture_id') != args.expected_capture_id or record.get('frame_id') != args.expected_capture_id):
            raise ValueError('selected native authority did not reconcile the original capture')
        asset = client.get_capture_asset(args.expected_capture_id)
        if asset.get('byte_count') != source.stat().st_size:
            raise ValueError('native retained asset size changed')
        client.close()
    finally:
        if not client.closed:
            client.abort()
    if client.process.poll() is None:
        raise RuntimeError('update health left the native authority running')
    rows_after, journal_after = _capture_rows(args.expected_capture_id)
    protected_after, repeated_creation = _fixtures(args.expected_capture_id)
    final_dng = _digest(source, MAX_DNG_BYTES)
    if (rows_before != rows_after or journal_before != journal_after or protected_before != protected_after or
            repeated_creation or final_dng != args.expected_dng_sha):
        raise ValueError('query-only health changed protected persistent capture data')
    protected_after.update(original_dng=final_dng, journal_capture_rows=journal_after)
    return {'format': 'hf-arm64-update-health-v1', 'attempt_id': args.attempt_id,
            'slot': args.slot, 'version': args.version, 'health_mode': args.health_mode,
            'healthy': args.health_mode == 'healthy', 'app_checks_passed': True,
            'intentional_failure': args.health_mode == 'fail', 'simulated': True,
            'machine': platform.machine(), 'effective_uid': os.geteuid(),
            'python_elf': python_identity, 'native_authority': native_identity,
            'native_binary_sha256': native_identity['sha256'],
            'release_sha256': _digest(RELEASE_PATH, 16384),
            'authority': 'native', 'journal_healthy': True, 'capture_status': 'complete',
            'request_id': args.expected_capture_id, 'capture_id': args.expected_capture_id,
            'frame_id': args.expected_capture_id, 'dng_sha256': final_dng,
            'same_capture_verified': True, 'journal_rows': len(rows_after),
            'native_process_closed': True, 'fixtures_created': fixtures_created,
            'protected_sha256': protected_after, 'sensor_acquisition_validated': False,
            'scope': 'generic virtual ARM64 selected-root query-only update health'}


def main(argv=None):
    args = _arguments(argv)
    # Actual operational failures are distinct from an intentionally unhealthy
    # release: they exit nonzero and cannot yield a confirmation marker.
    result = check(args)
    try:
        REPORT_ROOT.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _directory(REPORT_ROOT)
    report = REPORT_ROOT / (args.attempt_id + '.json')
    _write_new(report, _canonical(result))
    _sync_directory(REPORT_ROOT)
    _sync_directory(REPORT_ROOT.parent)
    compact = {key: result[key] for key in ('attempt_id', 'slot', 'version', 'health_mode',
               'healthy', 'app_checks_passed', 'request_id', 'native_process_closed', 'journal_rows')}
    compact.update(report=report.name, report_sha256=_digest(report))
    print('HF_UPDATE_HEALTH ' + json.dumps(compact, sort_keys=True, separators=(',', ':'),
                                          allow_nan=False), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('ARM64 update health failed: ' + type(error).__name__, file=sys.stderr, flush=True)
        raise SystemExit(2)

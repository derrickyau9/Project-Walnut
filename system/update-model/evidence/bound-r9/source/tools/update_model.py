"""Durable regular-image A/B development model with real isolated ARM64 boots.

This Linux user-space coordinator is not a board bootloader or authenticated
firmware updater. Hashes detect changes; they do not authenticate a package.
Only owned regular files beneath a NEW workspace build directory are written.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'generic-qemu-virt-aarch64'
DATA_ABI = 1
APP_ABI = 'hf-native-rpc-v1'
FORMAT = 'hf-update-image-bundle-v1'
MAX_IMAGE = 2 * 1024**3
MAX_BUNDLES = 4
MAX_ATTEMPTS = 32
MAX_JSON = 256 * 1024
LEGACY_PROFILE = {'format': 'hf-update-health-profile-v1', 'name': 'legacy-v1',
                  'data_abi': 1, 'capture_root': 'ui-r7/session'}


class UpdateError(ValueError):
    pass


def _linux():
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise UpdateError('this model requires an existing non-root Linux environment')


def _require(value, message):
    if not value:
        raise UpdateError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _json(raw):
    _require(isinstance(raw, (str, bytes)) and len(raw) <= MAX_JSON, 'JSON exceeds metadata limit')
    def pairs(items):
        result = {}
        for name, value in items:
            _require(name not in result, 'duplicate JSON member')
            result[name] = value
        return result
    def number(value):
        result = float(value)
        _require(math.isfinite(result), 'nonfinite JSON number')
        return result
    def reject(_):
        raise UpdateError('nonfinite JSON number')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise UpdateError('invalid bounded JSON metadata') from error


def _directory(path):
    path = Path(os.path.abspath(path))
    _require(path.resolve(strict=True) == path and stat.S_ISDIR(path.lstat().st_mode),
             'canonical directory without redirects required')
    return path


def _hash(path, *, maximum=MAX_IMAGE):
    from tools.image_boot import pinned, digest
    with pinned(path) as stream:
        before = os.fstat(stream.fileno())
        _require(0 < before.st_size <= maximum, 'regular input exceeds size bound')
        value = digest(stream)
        after = os.fstat(stream.fileno())
        current = Path(path).lstat()
        _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
                 (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns), 'input changed during hashing')
        return value


def _read(path):
    from tools.image_boot import pinned
    with pinned(path) as stream:
        _require(os.fstat(stream.fileno()).st_size <= MAX_JSON, 'metadata file exceeds limit')
        return _json(stream.read(MAX_JSON + 1))


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(_canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(Path(path).parent)


def _copy(source, destination, expected):
    from tools.image_boot import pinned
    _require(_hash(source) == expected, 'source hash mismatch before copy')
    with pinned(source) as source_file, Path(destination).open('xb') as output:
        shutil.copyfileobj(source_file, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    _require(_hash(source) == expected == _hash(destination), 'source/copy changed')
    _sync_directory(Path(destination).parent)


def _base(directory):
    directory = _directory(directory)
    build = _read(directory / 'build-evidence.json')
    first = _read(directory / 'boots/first/boot-evidence.json')
    last = _read(directory / 'boots/resume/boot-evidence.json')
    _require(build.get('format') == 'hf-arm64-image-build-v1' and build.get('uid') == 1000,
             'tested image build evidence required')
    _require(first.get('passed') is True and last.get('passed') is True and
             first.get('rootfs_unchanged') is True and last.get('rootfs_unchanged') is True and
             last.get('persistent_same_request_id') is True and first.get('request_id') == last.get('request_id'),
             'two successful same-capture immutable image boots required')
    expected = {'rootfs.ext4': build['rootfs_image']['sha256'], 'kernel.img': build['kernel_sha256'],
                'bootstrap.cpio.gz': build['bootstrap_sha256'], 'data.ext4': last['data_sha256_after']}
    for name, checksum in expected.items():
        _require(_hash(directory / name) == checksum, 'base image differs from its successful boot evidence')
    _require(first['rootfs_sha256_after'] == last['rootfs_sha256_after'] == expected['rootfs.ext4'],
             'base root image identity differs')
    report = last['guest_report']
    _require(report.get('journal_rows') == 1 and report.get('native_process_closed') is True,
             'base capture/authority health evidence required')
    return directory, build, last, expected


def _profile(policy):
    from tools.update_guest_boot import validate_health_profile
    return validate_health_profile(policy.get('health_profile', LEGACY_PROFILE))


def _source_profile(base, last, name, capture_root=None):
    """Pin a declared data profile from an immutable, already tested data image."""
    _require(name in ('legacy-v1', 'bound-v2'), 'unsupported data health profile')
    capture_root = capture_root or ('ui-r7/session' if name == 'legacy-v1' else 'ui-r7/offline/basic')
    _require(isinstance(capture_root, str) and len(capture_root) <= 128 and
             re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._/-]*', capture_root) is not None and
             all(part not in ('', '.', '..') for part in capture_root.split('/')), 'unsafe relative capture root')
    report = last['guest_report']
    if name == 'bound-v2':
        offline = report.get('offline_camera', {})
        basic = offline.get('basic', {})
        _require(offline.get('format') == 'hf-offline-camera-smoke-v2' and offline.get('passed') is True and
                 basic.get('capture_record_version') == 2 and basic.get('settings_persistent') is True,
                 'bound-v2 requires tested R9 offline binding/settings evidence')
    else:
        basic = report
    capture_id, dng_sha = basic['request_id'], basic['dng_sha256']
    from tools.image_fs import extract_file
    with tempfile.TemporaryDirectory(prefix='hf-update-data-profile-') as temporary:
        temporary = Path(temporary)
        journal_guest = '/' + capture_root + '/journal.sqlite3'
        journal = temporary / 'journal.sqlite3'
        extract_file(base / 'data.ext4', journal_guest, journal, 64 * 1024**2)
        # Cleanly closed applications normally checkpoint their WAL. If a WAL
        # remains, inspect its copied bytes too; never write to the source image.
        try:
            extract_file(base / 'data.ext4', journal_guest + '-wal', temporary / 'journal.sqlite3-wal', 64 * 1024**2)
        except ValueError:
            pass
        database = sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            rows = database.execute('SELECT request_id,record_json,status FROM captures ORDER BY request_id').fetchall()
            metadata = dict(database.execute('SELECT key,value FROM meta'))
        finally:
            database.close()
        _require(len(rows) == 1 and rows[0][0] == capture_id and rows[0][2] == 'complete',
                 'profile must identify exactly its tested original complete capture')
        record = _json(rows[0][1])
        _require(record.get('request_id') == record.get('capture_id') == record.get('frame_id') == capture_id,
                 'profile capture identity differs')
        if name == 'legacy-v1':
            _require('capture_record_version' not in record and 'calibration_binding' not in record,
                     'bound records cannot be labeled legacy data ABI 1')
            return dict(LEGACY_PROFILE, capture_root=capture_root), capture_id, dng_sha
        from camera.calibration import validate_binding
        binding = validate_binding(record.get('calibration_binding'))
        _require(record.get('capture_record_version') == 2 and record.get('hardware_calibrated') is False and
                 record.get('pixel_corrections_applied') is False and
                 binding['binding_id'] == basic.get('calibration_id') and
                 binding['package_checksum'] == basic.get('package_checksum') and
                 record.get('actual_exposure_us') == basic.get('actual_exposure_us'), 'bound capture differs from tested evidence')
        settings_envelope = _json(metadata.get('settings', ''))
        calibration_envelope = _json(metadata.get('calibration_binding', ''))
        _require(metadata.get('settings_format') == settings_envelope.get('format') == 'hf-device-settings-v1' and
                 metadata.get('calibration_binding_format') == calibration_envelope.get('format') == 'hf-active-calibration-v1' and
                 calibration_envelope.get('binding') == binding, 'persistent settings/binding markers differ')
        settings = settings_envelope.get('settings')
        _require(type(settings) is dict and set(settings) == {'sensor_mode', 'requested_exposure_us',
                 'requested_analogue_gain', 'output_format', 'simulation_scene'} and
                 settings.get('requested_exposure_us') == basic.get('actual_exposure_us'), 'persistent baseline differs from offline evidence')
        binding_file = temporary / 'binding.json'
        extract_file(base / 'data.ext4', '/' + str(Path(capture_root).parent.as_posix()) + '/binding.json', binding_file, 256 * 1024)
        _require(validate_binding(_read(binding_file)) == binding, 'external pinned binding artifact differs from journal snapshot')
        calibration = {'sensor_id': binding['sensor_id'], 'binding_id': binding['binding_id'],
            'calibration_id': binding['binding_id'], 'package_checksum': binding['package_checksum'],
            'hardware_calibrated': False, 'pixel_corrections_applied': False}
        profile = {'format': 'hf-update-health-profile-v1', 'name': 'bound-v2', 'data_abi': 2,
            'capture_root': capture_root, 'expected_settings': settings, 'expected_binding': binding,
            'expected_calibration': calibration, 'expected_capture_id': capture_id, 'expected_dng_sha': dng_sha,
            'expected_native_sha': report['native_authority']['sha256']}
        return _profile({'health_profile': profile}), capture_id, dng_sha


def _fsck(path):
    from tools.image_boot import pinned
    from tools.image_fs import _run
    executable = shutil.which('e2fsck')
    _require(executable is not None, 'existing e2fsck required')
    with pinned(path) as stream:
        result = _run([executable, '-fn', '/proc/self/fd/' + str(stream.fileno())],
                      pass_fds=(stream.fileno(),), timeout=60)
    _require(result['returncode'] == 0, 'image filesystem is not clean; no automatic repair')
    return result


def _payload(directory, manifest, policy):
    """Validate filesystem, release marker and actual AArch64 application bytes."""
    from tools.image_boot import pinned, check_ext4
    from tools.image_build import ROOT_UUID
    from tools.image_fs import extract_file
    with pinned(directory / 'rootfs.ext4') as stream:
        check_ext4(stream, ROOT_UUID)
    _fsck(directory / 'rootfs.ext4')
    from tools.image_fs import _run
    debugfs = shutil.which('debugfs')
    _require(debugfs is not None, 'existing debugfs required')
    with pinned(directory / 'rootfs.ext4') as image:
        info = _run([debugfs, '-R', 'stat /etc/hf-update-release.json', '/proc/self/fd/' + str(image.fileno())],
                    pass_fds=(image.fileno(),), timeout=30)
    mode = re.search(r'\bMode:\s+([0-7]+)', info['stdout'])
    _require(info['returncode'] == 0 and mode is not None and int(mode[1], 8) & 0o004,
             'release marker must be readable by the non-root guest health process')
    with tempfile.TemporaryDirectory(prefix='hf-update-inspection-') as temporary:
        temporary = Path(temporary)
        extract_file(directory / 'rootfs.ext4', '/etc/hf-update-release.json', temporary / 'release.json', 16384)
        release = _read(temporary / 'release.json')
        expected = {'format': 'hf-update-release-v1', 'target': TARGET, 'version': manifest['version'],
                    'health_mode': manifest['health_mode'], 'data_abi': _profile(policy)['data_abi'], 'native_abi': APP_ABI}
        _require(release == expected, 'on-image release marker differs from bundle compatibility')
        result = extract_file(directory / 'rootfs.ext4', '/opt/hasslefree/bin/hf-camerad', temporary / 'native', 32 * 1024**2)
        _require(result['sha256'] == manifest['native_sha256'] == policy['native_sha256'], 'native application differs')
        with (temporary / 'native').open('rb') as stream:
            header = stream.read(64)
        _require(header[:6] == b'\x7fELF\x02\x01' and len(header) == 64 and
                 int.from_bytes(header[18:20], 'little') == 183, 'AArch64 application required')


def validate_bundle(directory, policy, *, payload=True):
    directory = _directory(directory)
    manifest = _read(directory / 'manifest.json')
    fields = {'format', 'target', 'architecture', 'data_abi', 'native_abi', 'version', 'health_mode',
              'native_sha256', 'bootstrap_sha256', 'source_build_sha256', 'files'}
    _require(type(manifest) is dict and set(manifest) == fields and manifest['format'] == FORMAT and
             manifest['target'] == TARGET and manifest['architecture'] == 'aarch64' and
             type(manifest['data_abi']) is int and manifest['data_abi'] == _profile(policy)['data_abi'] and
             manifest['native_abi'] == APP_ABI, 'incompatible image target/data/application ABI')
    _require(type(manifest['version']) is int and 1 <= manifest['version'] <= 2**31 - 1 and
             manifest['health_mode'] in ('healthy', 'fail'), 'invalid version or health mode')
    _require(manifest['native_sha256'] == policy['native_sha256'] and
             manifest['bootstrap_sha256'] == policy['bootstrap_sha256'] and
             manifest['source_build_sha256'] == policy['source_build_sha256'], 'incompatible runtime/base image')
    _require(type(manifest['files']) is dict and set(manifest['files']) == {'rootfs.ext4', 'kernel.img'},
             'exact image/kernel file inventory required')
    _require({path.name for path in directory.iterdir()} == {'rootfs.ext4', 'kernel.img', 'manifest.json'},
             'unexpected bundle entry')
    for name, entry in manifest['files'].items():
        _require(type(entry) is dict and set(entry) == {'size', 'sha256'} and type(entry['size']) is int and
                 0 < entry['size'] <= MAX_IMAGE and isinstance(entry['sha256'], str) and
                 re.fullmatch('[0-9a-f]{64}', entry['sha256']) is not None, 'invalid file inventory')
        _require((directory / name).lstat().st_size == entry['size'] and _hash(directory / name) == entry['sha256'],
                 'bundle image hash/size mismatch')
    _require(manifest['files']['kernel.img']['sha256'] == policy['kernel_sha256'],
             'this model only supports its verified kernel/module runtime')
    if payload:
        _payload(directory, manifest, policy)
    return manifest


def _database(path):
    database = sqlite3.connect(str(path), timeout=1)
    database.row_factory = sqlite3.Row
    database.execute('PRAGMA journal_mode=WAL')
    database.execute('PRAGMA synchronous=FULL')
    return database


def _schema(database):
    database.executescript('''
      CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), confirmed TEXT NOT NULL,
        trial TEXT, remaining INTEGER NOT NULL, pending TEXT, protected TEXT, data_sha TEXT NOT NULL);
      CREATE TABLE slots(slot TEXT PRIMARY KEY, bundle TEXT NOT NULL, manifest_sha TEXT NOT NULL,
        version INTEGER NOT NULL);
      CREATE TABLE attempts(id TEXT PRIMARY KEY, slot TEXT NOT NULL, manifest_sha TEXT NOT NULL,
        version INTEGER NOT NULL, is_trial INTEGER NOT NULL, remaining INTEGER NOT NULL,
        status TEXT NOT NULL, before_sha TEXT NOT NULL, after_sha TEXT, receipt TEXT);
    ''')


def initialize(base_build, destination, *, data_profile='legacy-v1', capture_root=None):
    _linux()
    from tools.image_build import new_destination
    from tools.update_guest_boot import prepare_runtime
    base, build, last, hashes = _base(base_build)
    profile, capture_id, dng_sha = _source_profile(base, last, data_profile, capture_root)
    destination = new_destination(destination)
    destination.mkdir(mode=0o700)
    for name in ('bundles', 'attempts'):
        (destination / name).mkdir()
    runtime = prepare_runtime(base, destination / 'runtime', health_profile=profile)
    _require(runtime['kernel_sha256'] == hashes['kernel.img'], 'runtime kernel differs from base')
    _copy(base / 'data.ext4', destination / 'data.ext4', hashes['data.ext4'])
    policy = {'format': 'hf-update-model-policy-v1', 'store_id': str(uuid.uuid4()), 'target': TARGET,
              'data_abi': profile['data_abi'], 'native_abi': APP_ABI, 'source_build': str(base),
              'health_profile': profile,
              'source_build_sha256': _hash(base / 'build-evidence.json', maximum=MAX_JSON),
              'native_sha256': last['guest_report']['native_authority']['sha256'],
              'kernel_sha256': hashes['kernel.img'], 'bootstrap_sha256': runtime['bootstrap_sha256'],
              'capture_id': capture_id, 'dng_sha256': dng_sha,
              'upstream_sha256': hashes}
    _write(destination / 'policy.json', policy)
    database = _database(destination / 'control.sqlite3')
    try:
        _schema(database)
        database.execute('INSERT INTO state VALUES(1,?,NULL,0,NULL,NULL,?)', ('A', hashes['data.ext4']))
        database.commit()
    finally:
        database.close()
    with UpdateStore(destination, initializing=True) as store:
        bundle = store.build_bundle(1, 'healthy')
        manifest = validate_bundle(bundle, policy)
        store.db.execute('INSERT INTO slots VALUES(?,?,?,?)',
                         ('A', bundle.relative_to(destination).as_posix(), _hash(bundle / 'manifest.json'), manifest['version']))
        store.db.commit()
    _sync_directory(destination)
    return {'state_dir': str(destination), 'confirmed_slot': 'A', 'baseline': 'clone of verified R8 image plus model version marker',
            'capture_id': policy['capture_id'], 'upstream_unchanged': all(_hash(base / name) == digest for name, digest in hashes.items())}


class UpdateStore:
    def __init__(self, directory, *, initializing=False):
        _linux()
        import fcntl
        self.root = _directory(directory)
        _require(self.root.is_relative_to((ROOT / 'build').resolve()), 'model state must remain under workspace build')
        self.lock_fd = os.open(self.root / '.update.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        self.db = None
        try:
            _require(stat.S_ISREG(os.fstat(self.lock_fd).st_mode), 'regular lock file required')
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UpdateError('another coordinator owns this complete VM lifecycle') from error
            self.policy = _read(self.root / 'policy.json')
            _require(self.policy.get('format') == 'hf-update-model-policy-v1' and self.policy.get('target') == TARGET,
                     'unsupported update model')
            _require(stat.S_ISREG((self.root / 'control.sqlite3').lstat().st_mode), 'regular control database required')
            self.db = _database(self.root / 'control.sqlite3')
            if not initializing:
                self._state()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _state(self):
        row = self.db.execute('SELECT * FROM state WHERE id=1').fetchone()
        _require(row is not None and row['confirmed'] in ('A', 'B') and row['trial'] in (None, 'A', 'B') and
                 row['trial'] != row['confirmed'] and type(row['remaining']) is int and 0 <= row['remaining'] <= 3,
                 'invalid durable boot state')
        return dict(row)

    def _slot(self, name, *, payload=True):
        row = self.db.execute('SELECT * FROM slots WHERE slot=?', (name,)).fetchone()
        _require(row is not None, 'selected slot is absent')
        relative = row['bundle']
        _require(isinstance(relative, str) and re.fullmatch(r'bundles/[0-9a-f-]{36}', relative) is not None,
                 'unsafe stored bundle path')
        bundle = _directory(self.root / relative)
        _require(_hash(bundle / 'manifest.json') == row['manifest_sha'], 'selected manifest changed')
        manifest = validate_bundle(bundle, self.policy, payload=payload)
        _require(manifest['version'] == row['version'], 'stored version differs')
        return dict(row), bundle, manifest

    def build_bundle(self, version, health_mode='healthy'):
        _require(type(version) is int and 1 <= version <= 2**31 - 1 and health_mode in ('healthy', 'fail'),
                 'invalid artificial model version/health mode')
        bundles = _directory(self.root / 'bundles')
        _require(len(list(bundles.iterdir())) < MAX_BUNDLES, 'model retains at most four regular-image bundles')
        base, _, _, hashes = _base(self.policy['source_build'])
        _require(hashes == self.policy['upstream_sha256'], 'immutable upstream image changed')
        _require(shutil.disk_usage(self.root).free >= (base / 'rootfs.ext4').stat().st_size +
                 (base / 'kernel.img').stat().st_size + 64 * 1024**2, 'insufficient free space for one new inactive image')
        bundle = bundles / str(uuid.uuid4())
        bundle.mkdir(mode=0o700)
        _copy(base / 'rootfs.ext4', bundle / 'rootfs.ext4', hashes['rootfs.ext4'])
        _copy(base / 'kernel.img', bundle / 'kernel.img', hashes['kernel.img'])
        marker = {'format': 'hf-update-release-v1', 'target': TARGET, 'version': version,
                  'health_mode': health_mode, 'data_abi': _profile(self.policy)['data_abi'], 'native_abi': APP_ABI}
        from tools.image_boot import pinned
        from tools.image_fs import _run
        debugfs = shutil.which('debugfs')
        _require(debugfs is not None, 'existing debugfs required')
        with tempfile.TemporaryFile() as source, pinned(bundle / 'rootfs.ext4', writable=True) as image:
            # debugfs preserves its source file's mode; its ordinary temporary
            # default 0600 would hide the marker from the guest's UID 1000.
            os.fchmod(source.fileno(), 0o644)
            source.write(_canonical(marker) + b'\n')
            source.flush()
            source.seek(0)
            result = _run([debugfs, '-w', '-R', 'write /proc/self/fd/' + str(source.fileno()) +
                           ' /etc/hf-update-release.json', '/proc/self/fd/' + str(image.fileno())],
                          pass_fds=(source.fileno(), image.fileno()), timeout=30)
            _require(result['returncode'] == 0, 'release marker image write failed')
            image.flush()
            os.fsync(image.fileno())
        manifest = {'format': FORMAT, 'target': TARGET, 'architecture': 'aarch64', 'data_abi': _profile(self.policy)['data_abi'],
                    'native_abi': APP_ABI, 'version': version, 'health_mode': health_mode,
                    'native_sha256': self.policy['native_sha256'], 'bootstrap_sha256': self.policy['bootstrap_sha256'],
                    'source_build_sha256': self.policy['source_build_sha256'],
                    'files': {name: {'size': (bundle / name).stat().st_size, 'sha256': _hash(bundle / name)}
                              for name in ('rootfs.ext4', 'kernel.img')}}
        _write(bundle / 'manifest.json', manifest)
        validate_bundle(bundle, self.policy)
        for name in ('rootfs.ext4', 'kernel.img', 'manifest.json'):
            (bundle / name).chmod(0o400)
        _sync_directory(bundle)
        return bundle

    def stage(self, directory, *, attempts=2):
        _require(type(attempts) is int and 1 <= attempts <= 3, 'trial budget must be 1-3')
        bundle = _directory(directory)
        _require(bundle.parent == self.root / 'bundles', 'stage an owned candidate created by this model')
        manifest = validate_bundle(bundle, self.policy)
        state = self._state()
        _require(state['protected'] is not None and state['pending'] is None and state['trial'] is None,
                 'baseline health/confirmation required; existing trial must finish first')
        current, _, _ = self._slot(state['confirmed'])
        _require(manifest['version'] > current['version'], 'candidate version must advance the confirmed model version')
        inactive = 'B' if state['confirmed'] == 'A' else 'A'
        checksum = _hash(bundle / 'manifest.json')
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO slots VALUES(?,?,?,?)',
                            (inactive, bundle.relative_to(self.root).as_posix(), checksum, manifest['version']))
            self.db.execute('UPDATE state SET trial=?,remaining=? WHERE id=1', (inactive, attempts))
        return {'trial_slot': inactive, 'version': manifest['version'], 'attempts_remaining': attempts,
                'manifest_sha256': checksum, 'confirmed_slot_preserved': state['confirmed']}

    def select_boot(self):
        _require(_hash(self.root / 'runtime/bootstrap.cpio.gz') == self.policy['bootstrap_sha256'],
                 'actual guest bootstrap changed; no boot is permitted')
        state = self._state()
        _require(self.db.execute('SELECT count(*) FROM attempts').fetchone()[0] < MAX_ATTEMPTS,
                 'bounded model reached its 32-attempt evidence limit')
        data_hash = _hash(self.root / 'data.ext4')
        pending = None
        if state['pending']:
            pending = self.db.execute('SELECT * FROM attempts WHERE id=?', (state['pending'],)).fetchone()
            _require(pending is not None, 'pending attempt is missing')
        if data_hash != state['data_sha']:
            _require(pending is not None and pending['status'] == 'selected', 'shared data changed outside an interrupted boot')
            _fsck(self.root / 'data.ext4')
        slot = state['trial'] if state['trial'] and state['remaining'] > 0 else state['confirmed']
        try:
            row, _, manifest = self._slot(slot)
        except (OSError, ValueError, RuntimeError):
            if slot == state['confirmed']:
                raise
            slot = state['confirmed']
            row, _, manifest = self._slot(slot)
        is_trial = slot == state['trial']
        remaining = state['remaining'] - 1 if is_trial else 0
        attempt_id = str(uuid.uuid4())
        with self.transaction():
            if pending is not None:
                if pending['status'] in ('selected', 'healthy'):
                    self.db.execute('UPDATE attempts SET status=?,after_sha=COALESCE(after_sha,?) WHERE id=?',
                                    ('interrupted' if pending['status'] == 'selected' else 'unconfirmed', data_hash, pending['id']))
            self.db.execute('UPDATE state SET trial=?,remaining=?,pending=?,data_sha=? WHERE id=1',
                            (state['trial'] if is_trial else None, remaining, attempt_id, data_hash))
            self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
                            (attempt_id, slot, row['manifest_sha'], manifest['version'], int(is_trial), remaining,
                             'selected', data_hash))
        return {'attempt_id': attempt_id, 'slot': slot, 'version': manifest['version'],
                'manifest_sha256': row['manifest_sha'], 'is_trial': is_trial, 'attempts_remaining': remaining,
                'health_mode': manifest['health_mode'], 'data_sha256_before': data_hash}

    def _record_result(self, selected, receipt):
        state = self._state()
        _require(state['pending'] == selected['attempt_id'], 'stale boot attempt')
        row, _, manifest = self._slot(selected['slot'])
        _require(receipt.get('passed') is True and type(receipt.get('healthy')) is bool and
                 receipt.get('attempt_id') == selected['attempt_id'] and receipt.get('slot') == selected['slot'] and
                 receipt.get('version') == selected['version'] and receipt.get('health_mode') == selected['health_mode'] and
                 receipt.get('rootfs_unchanged') is True and
                 receipt.get('rootfs_sha256_before') == receipt.get('rootfs_sha256_after') == manifest['files']['rootfs.ext4']['sha256'] and
                 receipt.get('kernel_sha256') == self.policy['kernel_sha256'] and
                 receipt.get('bootstrap_sha256') == self.policy['bootstrap_sha256'] and
                 receipt.get('data_sha256_before') == selected['data_sha256_before'] and
                 receipt.get('data_sha256_after') == _hash(self.root / 'data.ext4') and
                 row['manifest_sha'] == selected['manifest_sha256'], 'boot receipt does not match selected immutable image and data')
        protected = receipt.get('protected_sha256')
        profile = _profile(self.policy)
        protected_keys = {'configuration', 'calibration', 'original_dng', 'journal_capture_rows'}
        if profile['name'] == 'bound-v2':
            protected_keys |= {'device_settings', 'calibration_binding'}
            _require(receipt.get('data_profile') == 'bound-v2' and receipt.get('data_abi') == 2 and
                     receipt.get('capture_root') == profile['capture_root'] and
                     type(protected) is dict and protected.get('device_settings') ==
                     hashlib.sha256(_canonical(profile['expected_settings']) + b'\n').hexdigest() and
                     protected.get('calibration_binding') ==
                     hashlib.sha256(_canonical(profile['expected_binding']) + b'\n').hexdigest(), 'actual persistent settings/binding differ from pinned ABI-2 baseline')
        _require(type(protected) is dict and set(protected) == protected_keys and
                 all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in protected.values()) and
                 protected['original_dng'] == self.policy['dng_sha256'], 'protected data evidence missing')
        if state['protected'] is not None:
            _require(protected == _json(state['protected']), 'configuration/calibration fixture or retained DNG changed')
        else:
            _require(receipt['healthy'] is True and selected['is_trial'] is False, 'baseline fixtures require healthy confirmed-slot boot')
        attempt_directory = _directory(self.root / 'attempts' / selected['attempt_id'])
        _require(_read(attempt_directory / 'boot-evidence.json') == receipt, 'guest receipt file differs before durable recording')
        # Publish the file and its directory entries before committing the
        # SQLite reference to it. A failed evidence sync cannot become health.
        _sync_directory(attempt_directory)
        _sync_directory(attempt_directory.parent)
        with self.transaction():
            self.db.execute('UPDATE attempts SET status=?,after_sha=?,receipt=? WHERE id=?',
                            ('healthy' if receipt['healthy'] else 'unhealthy', receipt['data_sha256_after'],
                             _canonical(receipt).decode(), selected['attempt_id']))
            self.db.execute('UPDATE state SET protected=?,data_sha=? WHERE id=1',
                            (_canonical(protected).decode(), receipt['data_sha256_after']))

    def boot(self, *, timeout=120):
        from tools.update_guest_boot import boot
        selected = self.select_boot()  # durable attempt consumption precedes process launch
        _, bundle, _ = self._slot(selected['slot'])
        receipt = boot(bundle / 'rootfs.ext4', bundle / 'kernel.img', self.root / 'runtime/bootstrap.cpio.gz',
            self.root / 'data.ext4', self.root / 'attempts' / selected['attempt_id'],
            attempt_id=selected['attempt_id'], slot=selected['slot'], version=selected['version'],
            health_mode=selected['health_mode'], expected_capture_id=self.policy['capture_id'],
            expected_dng_sha=self.policy['dng_sha256'], expected_native_sha=self.policy['native_sha256'], timeout=timeout,
            initialize_fixtures=self._state()['protected'] is None, health_profile=_profile(self.policy))
        self._record_result(selected, receipt)
        return dict(selected, healthy=receipt['healthy'], receipt=receipt)

    def confirm(self, attempt_id):
        _require(_hash(self.root / 'runtime/bootstrap.cpio.gz') == self.policy['bootstrap_sha256'],
                 'guest bootstrap changed before confirmation')
        state = self._state()
        _require(isinstance(attempt_id, str) and str(uuid.UUID(attempt_id)) == attempt_id and state['pending'] == attempt_id,
                 'confirmation requires the current attempt UUID')
        attempt = self.db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
        _require(attempt is not None and attempt['status'] == 'healthy', 'unhealthy/unverified boot cannot be confirmed')
        row, _, _ = self._slot(attempt['slot'])
        _require(row['manifest_sha'] == attempt['manifest_sha'] and
                 _hash(self.root / 'data.ext4') == state['data_sha'], 'selected image/data changed before confirmation')
        receipt = _json(attempt['receipt'])
        evidence = _read(self.root / 'attempts' / attempt_id / 'boot-evidence.json')
        _require(evidence == receipt and receipt['healthy'] is True, 'stored receipt/evidence changed')
        with self.transaction():
            self.db.execute('UPDATE state SET confirmed=?,trial=NULL,remaining=0,pending=NULL WHERE id=1', (attempt['slot'],))
            self.db.execute("UPDATE attempts SET status='confirmed' WHERE id=?", (attempt_id,))
        return {'confirmed_slot': attempt['slot'], 'version': attempt['version'], 'attempt_id': attempt_id}

    def status(self):
        state = self._state()
        state['protected'] = _json(state['protected']) if state['protected'] else None
        return dict(state, slots=[dict(row) for row in self.db.execute('SELECT * FROM slots ORDER BY slot')],
                    attempts=[{key: row[key] for key in ('id', 'slot', 'version', 'is_trial', 'remaining', 'status', 'before_sha', 'after_sha')}
                              for row in self.db.execute('SELECT * FROM attempts ORDER BY rowid')])


def demo(base_build, state_dir, *, timeout=120, data_profile='legacy-v1', capture_root=None):
    initialized = initialize(base_build, state_dir, data_profile=data_profile, capture_root=capture_root)
    boots = []
    with UpdateStore(state_dir) as store:
        baseline = store.boot(timeout=timeout)
        boots.append(baseline)
        store.confirm(baseline['attempt_id'])
        healthy = store.build_bundle(2)
        store.stage(healthy, attempts=2)
        trial = store.boot(timeout=timeout)
        boots.append(trial)
        _require(trial['slot'] == 'B' and trial['healthy'], 'healthy inactive trial failed')
        store.confirm(trial['attempt_id'])
        failing = store.build_bundle(3, 'fail')
        store.stage(failing, attempts=2)
    # Reopen between each trial: attempts/budget are durable, not in-memory.
    for _ in range(2):
        with UpdateStore(state_dir) as store:
            trial = store.boot(timeout=timeout)
            _require(trial['slot'] == 'A' and trial['healthy'] is False, 'injected failed candidate unexpectedly healthy')
            try:
                store.confirm(trial['attempt_id'])
            except UpdateError:
                pass
            else:
                raise UpdateError('failed candidate was confirmable')
            boots.append(trial)
    with UpdateStore(state_dir) as store:
        fallback = store.boot(timeout=timeout)
        _require(fallback['slot'] == 'B' and fallback['healthy'] and not fallback['is_trial'], 'confirmed fallback failed')
        store.confirm(fallback['attempt_id'])
        boots.append(fallback)
        upstream = all(_hash(Path(store.policy['source_build']) / name) == digest
                       for name, digest in store.policy['upstream_sha256'].items())
        report = {'format': 'hf-arm64-ab-update-demo-v1', 'passed': True, 'initialized': initialized,
                  'boot_sequence': [{key: boot[key] for key in ('attempt_id', 'slot', 'version', 'is_trial', 'healthy', 'attempts_remaining')}
                                    for boot in boots], 'status': store.status(), 'upstream_unchanged': upstream,
                  'preservation_fixtures_not_bound_calibration': True,
                  'scope': 'same application code with artificial release markers, rootless QEMU software update model',
                  'authentication': False, 'physical_bootloader_or_powercut_verified': False}
        _require(upstream, 'immutable original R8 images changed')
        _write(store.root / 'demo-evidence.json', report)
        return report


def bound_demo(base_build, state_dir, *, timeout=120, capture_root=None):
    """Three actual boots preserving pinned R9 settings/binding and capture."""
    initialized = initialize(base_build, state_dir, data_profile='bound-v2', capture_root=capture_root)
    boots = []
    with UpdateStore(state_dir) as store:
        baseline = store.boot(timeout=timeout)
        boots.append(baseline)
        store.confirm(baseline['attempt_id'])
        failed = store.build_bundle(2, 'fail')
        store.stage(failed, attempts=1)
    with UpdateStore(state_dir) as store:
        trial = store.boot(timeout=timeout)
        _require(trial['slot'] == 'B' and not trial['healthy'] and trial['attempts_remaining'] == 0,
                 'ABI-2 failing trial did not consume its one attempt')
        boots.append(trial)
        try:
            store.confirm(trial['attempt_id'])
        except UpdateError:
            pass
        else:
            raise UpdateError('unhealthy ABI-2 trial was confirmable')
    with UpdateStore(state_dir) as store:
        fallback = store.boot(timeout=timeout)
        _require(fallback['slot'] == 'A' and fallback['healthy'] and not fallback['is_trial'],
                 'confirmed ABI-2 fallback failed')
        store.confirm(fallback['attempt_id'])
        boots.append(fallback)
        upstream = all(_hash(Path(store.policy['source_build']) / name) == digest
                       for name, digest in store.policy['upstream_sha256'].items())
        _require(upstream, 'immutable R9 base images changed')
        report = {'format': 'hf-arm64-bound-update-demo-v1', 'passed': True, 'initialized': initialized,
            'data_profile': 'bound-v2', 'data_abi': 2, 'capture_root': _profile(store.policy)['capture_root'],
            'binding_id': _profile(store.policy)['expected_binding']['binding_id'],
            'boot_sequence': [{key: boot[key] for key in ('attempt_id', 'slot', 'version', 'healthy', 'is_trial', 'attempts_remaining')}
                              for boot in boots], 'status': store.status(), 'upstream_unchanged': upstream,
            'actual_persistent_settings_and_binding_verified': True, 'hardware_calibrated': False,
            'pixel_corrections_applied': False, 'authentication': False,
            'scope': 'same R9 application code, artificial release versions, real virtual ARM64 selection/fallback'}
        _write(store.root / 'bound-demo-evidence.json', report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'demo', 'bound-demo'):
        item = commands.add_parser(name)
        item.add_argument('--base-build', type=Path, required=True)
        item.add_argument('--state-dir', type=Path, required=True)
        if name != 'bound-demo':
            item.add_argument('--data-profile', choices=('legacy-v1', 'bound-v2'), default='legacy-v1')
        item.add_argument('--capture-root')
        if name in ('demo', 'bound-demo'):
            item.add_argument('--timeout', type=float, default=120)
    for name in ('status', 'bundle', 'stage', 'boot', 'confirm'):
        item = commands.add_parser(name)
        item.add_argument('--state-dir', type=Path, required=True)
        if name == 'bundle':
            item.add_argument('--version', type=int, required=True)
            item.add_argument('--health-mode', choices=('healthy', 'fail'), default='healthy')
        elif name == 'stage':
            item.add_argument('--bundle', type=Path, required=True)
            item.add_argument('--attempts', type=int, default=2)
        elif name == 'boot':
            item.add_argument('--timeout', type=float, default=120)
        elif name == 'confirm':
            item.add_argument('--attempt-id', required=True)
    args = parser.parse_args(argv)
    if args.command == 'init':
        result = initialize(args.base_build, args.state_dir, data_profile=args.data_profile, capture_root=args.capture_root)
    elif args.command == 'demo':
        result = demo(args.base_build, args.state_dir, timeout=args.timeout, data_profile=args.data_profile, capture_root=args.capture_root)
    elif args.command == 'bound-demo':
        result = bound_demo(args.base_build, args.state_dir, timeout=args.timeout, capture_root=args.capture_root)
    else:
        with UpdateStore(args.state_dir) as store:
            if args.command == 'bundle':
                result = {'bundle': str(store.build_bundle(args.version, args.health_mode))}
            elif args.command == 'stage':
                result = store.stage(args.bundle, attempts=args.attempts)
            elif args.command == 'boot':
                result = store.boot(timeout=args.timeout)
            elif args.command == 'confirm':
                result = store.confirm(args.attempt_id)
            else:
                result = store.status()
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print('Update model refused: ' + str(error), file=sys.stderr)
        raise SystemExit(2)

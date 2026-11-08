"""Offline A/B trial model for host simulator bundles, never a bootloader updater.

Only recovery.sqlite3 in the explicitly selected state directory is changed.
Known host-bundle and camera-journal trees are refused before creating anything.
Other owner storage must be kept separate explicitly; arbitrary unmarked data
directories cannot be inferred from their contents.
SHA-256 checks detect corruption; they do not authenticate an untrusted package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

TARGET = 'hf-sim-linux-host-v1'
FORMAT = 'hf-sim-host-bundle-v1'
MAX_BYTES = 1024 * 1024 * 1024


class RecoveryError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError('duplicate JSON member')
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(RecoveryError('nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError('invalid JSON') from error


def validate_bundle(directory, *, architecture=None):
    """Validate an already extracted, immutable host bundle without executing it."""
    original = Path(directory)
    if original.is_symlink():
        raise RecoveryError('bundle root must not be a symlink')
    root = original.resolve(strict=True)
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise RecoveryError('missing or oversized manifest')
    raw = manifest_path.read_bytes()
    manifest = _json(raw)
    if not isinstance(manifest, dict) or set(manifest) != {'format', 'package_id', 'target', 'architecture', 'files'}:
        raise RecoveryError('unsupported manifest fields')
    if manifest['format'] != FORMAT or manifest['target'] != TARGET:
        raise RecoveryError('incompatible simulator bundle format or target')
    if not isinstance(manifest['package_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', manifest['package_id']):
        raise RecoveryError('invalid package identity')
    if not isinstance(manifest['architecture'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', manifest['architecture']):
        raise RecoveryError('invalid architecture')
    if architecture is not None and manifest['architecture'] != architecture:
        raise RecoveryError('incompatible host architecture')
    entries = manifest['files']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4096:
        raise RecoveryError('manifest must contain 1-4096 files')
    expected, total = set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256', 'mode'}:
            raise RecoveryError('invalid file entry')
        name = entry['path']
        if not isinstance(name, str) or '\\' in name:
            raise RecoveryError('invalid bundle path')
        relative = PurePosixPath(name)
        if (relative.is_absolute() or relative.as_posix() != name or '..' in relative.parts or
                not relative.parts or relative.parts[0] not in {'bin', 'runtime', 'systemd', 'licenses'} or
                any(':' in part or '\x00' in part for part in relative.parts) or name in expected):
            raise RecoveryError('unsafe or duplicate bundle path')
        if type(entry['size']) is not int or entry['size'] < 0 or entry['size'] > MAX_BYTES:
            raise RecoveryError('invalid declared file size')
        if type(entry['mode']) is not int or entry['mode'] not in (0o644, 0o755):
            raise RecoveryError('unsupported file permissions')
        if not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise RecoveryError('invalid SHA-256')
        total += entry['size']
        if total > MAX_BYTES:
            raise RecoveryError('bundle exceeds byte budget')
        path = root.joinpath(*relative.parts)
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
            raise RecoveryError('symlinks are not allowed in a bundle')
        if not path.is_file() or path.stat().st_size != entry['size']:
            raise RecoveryError('bundle file missing or wrong length: ' + name)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != entry['sha256']:
            raise RecoveryError('bundle checksum mismatch: ' + name)
        expected.add(name)
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise RecoveryError('symlinks are not allowed in a bundle')
        if path.is_file() and path != manifest_path:
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise RecoveryError('bundle contains undeclared files')
    return {'path': str(root), 'package_id': manifest['package_id'],
            'architecture': manifest['architecture'], 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'files': len(entries), 'bytes': total}


class RecoveryStore:
    """Transactional host simulation of bounded trial attempts and confirmation."""
    def __init__(self, directory, *, architecture):
        self.root = Path(directory).resolve()
        if any((self.root / name).exists() for name in ('journal.sqlite3', 'photos', 'calibration')):
            raise RecoveryError('recovery state must use a separate directory from camera data')
        # Resolve existing parents first so a symlinked location cannot bypass
        # the ancestry check. Inspect markers before mkdir or opening SQLite.
        for ancestor in (self.root, *self.root.parents):
            journal = ancestor / 'journal.sqlite3'
            if journal.exists() or journal.is_symlink():
                raise RecoveryError('recovery state must not be inside a camera journal tree')
            manifest_path = ancestor / 'manifest.json'
            if manifest_path.is_file() and not manifest_path.is_symlink() and manifest_path.stat().st_size <= 1024 * 1024:
                try:
                    manifest = _json(manifest_path.read_bytes())
                except RecoveryError:
                    continue
                if isinstance(manifest, dict) and manifest.get('format') == FORMAT:
                    raise RecoveryError('recovery state must not be inside a host simulator bundle')
        self.root.mkdir(parents=True, exist_ok=True)
        self.architecture = architecture
        if any((self.root / name).is_symlink() for name in ('recovery.sqlite3', 'recovery.sqlite3-wal', 'recovery.sqlite3-shm')):
            raise RecoveryError('recovery database must not be a symlink')
        self.db = sqlite3.connect(self.root / 'recovery.sqlite3', timeout=5)
        initial = {'schema': 1, 'simulated': True, 'generation': 0, 'active': 'A',
                   'slots': {'A': None, 'B': None}, 'trial': None, 'last_error': None}
        try:
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and tables != {'state'}:
                raise RecoveryError('existing database is not a recovery model store')
            if tables:
                self.state()  # Never reset a missing or malformed persisted state.
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            if not tables:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
                self.db.execute('INSERT INTO state VALUES(1,?)', (json.dumps(initial, sort_keys=True),))
                self.db.commit()
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def state(self):
        row = self.db.execute('SELECT value FROM state WHERE id=1').fetchone()
        if row is None:
            raise RecoveryError('missing persisted recovery state; no automatic reset')
        state = _json(row[0])
        if (not isinstance(state, dict) or set(state) != {'schema', 'simulated', 'generation', 'active', 'slots', 'trial', 'last_error'} or
                type(state.get('schema')) is not int or state['schema'] != 1 or state.get('simulated') is not True or
                state.get('active') not in ('A', 'B') or type(state.get('generation')) is not int or
                state['generation'] < 0 or not isinstance(state.get('slots'), dict) or set(state['slots']) != {'A', 'B'}):
            raise RecoveryError('corrupt recovery state')
        if state['last_error'] is not None and not isinstance(state['last_error'], str):
            raise RecoveryError('corrupt recovery reason')
        trial = state.get('trial')
        if trial is not None and (not isinstance(trial, dict) or set(trial) != {'slot', 'remaining', 'attempt_id'} or trial.get('slot') not in ('A', 'B') or
                                  trial['slot'] == state['active'] or type(trial.get('remaining')) is not int or
                                  not 0 <= trial['remaining'] <= 5 or
                                  (trial.get('attempt_id') is not None and not isinstance(trial.get('attempt_id'), str))):
            raise RecoveryError('corrupt trial state')
        return state

    def _change(self, action):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            state = self.state()
            result = action(state)
            state['generation'] += 1
            self.db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(state, sort_keys=True),))
            self.db.commit()  # Decision/attempt budget is durable before returning.
            return result if result is not None else state
        except BaseException:
            self.db.rollback()
            raise

    def _check(self, reference):
        if not isinstance(reference, dict):
            raise RecoveryError('slot has no verified simulator bundle')
        current = validate_bundle(reference['path'], architecture=self.architecture)
        if current != reference:
            raise RecoveryError('slot bundle changed after staging')

    def bootstrap(self, bundle):
        reference = validate_bundle(bundle, architecture=self.architecture)
        def action(state):
            if state['slots']['A'] is not None or state['slots']['B'] is not None or state['trial'] is not None:
                raise RecoveryError('bootstrap is allowed only for empty recovery state')
            state['slots']['A'] = reference
        return self._change(action)

    def stage(self, bundle, *, attempts=2):
        if type(attempts) is not int or not 1 <= attempts <= 5:
            raise RecoveryError('trial attempts must be 1-5')
        reference = validate_bundle(bundle, architecture=self.architecture)
        def action(state):
            if state['trial'] is not None:
                raise RecoveryError('a trial is already pending')
            self._check(state['slots'][state['active']])
            inactive = 'B' if state['active'] == 'A' else 'A'
            state['slots'][inactive] = reference
            state['trial'] = {'slot': inactive, 'remaining': attempts, 'attempt_id': None}
            state['last_error'] = None
        return self._change(action)

    def choose_boot(self):
        """Return a simulated selection, persisting trial consumption first."""
        def action(state):
            trial = state['trial']
            if trial is not None:
                try:
                    self._check(state['slots'][trial['slot']])
                    if trial['remaining'] <= 0:
                        raise RecoveryError('trial budget exhausted without health confirmation')
                    trial['remaining'] -= 1
                    trial['attempt_id'] = str(uuid.uuid4())
                    return {'simulated': True, 'mode': 'trial', 'slot': trial['slot'],
                            'attempt_id': trial['attempt_id'], **state['slots'][trial['slot']]}
                except (RecoveryError, OSError) as error:
                    state['last_error'] = str(error)
                    state['trial'] = None
            try:
                reference = state['slots'][state['active']]
                self._check(reference)
                return {'simulated': True, 'mode': 'confirmed', 'slot': state['active'], **reference}
            except (RecoveryError, OSError) as error:
                state['last_error'] = str(error)
                return {'simulated': True, 'mode': 'recovery', 'slot': None, 'error': str(error)}
        return self._change(action)

    def confirm(self, attempt_id, health):
        def action(state):
            trial = state['trial']
            if trial is None or trial['attempt_id'] != attempt_id or not attempt_id:
                raise RecoveryError('stale or unknown trial attempt')
            reference = state['slots'][trial['slot']]
            self._check(reference)
            if (not isinstance(health, dict) or health.get('format') != 'hf-sim-health-v1' or
                    health.get('passed') is not True or health.get('simulated') is not True or
                    health.get('authority') != 'native' or health.get('journal_healthy') is not True or
                    health.get('capture_status') != 'complete' or health.get('attempt_id') != attempt_id or
                    health.get('manifest_sha256') != reference['manifest_sha256'] or
                    health.get('native_binary_sha256') != hashlib.sha256((Path(reference['path']) / 'bin/hf-camerad').read_bytes()).hexdigest() or
                    not isinstance(health.get('artifact_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', health['artifact_sha256'])):
                raise RecoveryError('trial health evidence is incomplete or mismatched')
            state['active'] = trial['slot']; state['trial'] = None; state['last_error'] = None
        return self._change(action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--architecture', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('bootstrap', 'stage'):
        command = sub.add_parser(name); command.add_argument('bundle', type=Path)
        if name == 'stage': command.add_argument('--attempts', type=int, default=2)
    sub.add_parser('choose-boot'); sub.add_parser('state')
    confirm = sub.add_parser('confirm'); confirm.add_argument('attempt_id'); confirm.add_argument('health', type=Path)
    args = parser.parse_args()
    with RecoveryStore(args.state_dir, architecture=args.architecture) as store:
        if args.action == 'stage': result = store.stage(args.bundle, attempts=args.attempts)
        elif args.action == 'bootstrap': result = store.bootstrap(args.bundle)
        elif args.action == 'choose-boot': result = store.choose_boot()
        elif args.action == 'confirm': result = store.confirm(args.attempt_id, _json(args.health.read_bytes()))
        else: result = store.state()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

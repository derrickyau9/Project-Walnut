#!/usr/pkg/bin/python3.12
"""Finish staged pkgsrc registration/scripts using native, offline pkg_add."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

def require_native():
    if platform.system() != 'NetBSD' or os.geteuid() != 0:
        raise RuntimeError('Package finalization requires native NetBSD/aarch64 root')
    architecture = subprocess.check_output(['/sbin/sysctl', '-n', 'hw.machine_arch'], text=True, timeout=5).strip()
    if architecture != 'aarch64':
        raise RuntimeError('Package finalization requires native NetBSD/aarch64 root')
    return architecture

def sha(path, algorithm):
    result = hashlib.new(algorithm)
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): result.update(block)
    return result.hexdigest()

def verify_payload(manifest, root=Path('/')):
    """Check post-install bytes against the immutable factory payload baseline."""
    record = json.loads(manifest.read_text())
    if record.get('format') != 'DaOS-NetBSD-ui-payload-baseline-v1':
        raise RuntimeError('Expected the factory UI payload baseline')
    failures = []
    for row in record['files']:
        name = row['path']
        if not name.startswith('usr/pkg/') or '..' in Path(name).parts or '\\' in name:
            raise RuntimeError('Unconfined factory payload path')
        path = root / name
        if row['type'] == 'file':
            actual = sha(path, 'sha256') if path.is_file() and not path.is_symlink() else None
        elif row['type'] == 'link':
            actual = hashlib.sha256(os.readlink(path).encode()).hexdigest() if path.is_symlink() else None
        else: raise RuntimeError('Unknown factory payload kind')
        if actual != row['sha256']: failures.append(name)
    return record, failures

def finalize(cache, state, database, payload_manifest, run=subprocess.run, payload_root=Path('/')):
    lock_path = cache / 'runtime.lock.json'
    lock = json.loads(lock_path.read_text())
    if (lock.get('format') != 'DaOS-NetBSD-binary-runtime-lock-v1'
            or lock['target']['system'] != 'NetBSD' or lock['target']['architecture'] != 'aarch64'):
        raise RuntimeError('Expected the pinned NetBSD/aarch64 package lock')
    for row in lock['packages']:
        if (not re.fullmatch(r'[A-Za-z0-9_+.-]+', row['name'])
                or row['file'] != row['name'] + '.tgz'
                or not re.fullmatch(r'[0-9a-f]{128}', row['sha512'])):
            raise RuntimeError('Invalid offline package record')
    lock_hash = sha(lock_path, 'sha256')
    if state.is_symlink(): raise RuntimeError('Redirected package state')
    state.mkdir(parents=True, exist_ok=True); state.chmod(0o700)
    receipt = state / 'finalized.json'
    env = {'PATH': '/sbin:/bin:/usr/sbin:/usr/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/X11R7/bin',
           'HOME': '/root', 'PKG_PATH': str(cache), 'PKG_DBDIR': str(database),
           'PKG_REFCOUNT_DBDIR': str(database) + '.refcount', 'LANG': 'C'}
    def registered():
        for row in lock['packages']:
            run(['/usr/sbin/pkg_info', '-K', str(database), '-q', '-e', row['name']],
                check=True, env=env, stdout=subprocess.DEVNULL)
    if receipt.is_symlink(): raise RuntimeError('Redirected package completion receipt')
    if receipt.exists():
        old = json.loads(receipt.read_text())
        if old.get('runtime_lock_sha256') == lock_hash and old.get('package_count') == len(lock['packages']):
            registered()
            return dict(old, resumed=True)
        raise RuntimeError('A different UI package generation is already registered')
    for row in lock['packages']:
        archive = cache / row['file']
        if archive.is_symlink() or archive.stat().st_size != row['bytes'] or sha(archive, 'sha512') != row['sha512']:
            raise RuntimeError('Offline package archive is missing or changed: ' + row['file'])
    # Retrying an interrupted install must not skip registered packages whose
    # previous POST-INSTALL did not complete. -U reruns this exact generation.
    command = ['/usr/sbin/pkg_add', '-U', '-K', str(database)] + [str(cache / row['file']) for row in lock['packages']]
    run(command, check=True, env=env)
    registered()
    baseline, changes = verify_payload(payload_manifest, payload_root)
    if baseline.get('runtime_lock_sha256') != lock_hash:
        raise RuntimeError('Factory payload baseline belongs to another runtime generation')
    if changes:
        (state / 'payload-changes.json').write_text(json.dumps({'runtime_lock_sha256': lock_hash, 'changed': changes}, indent=2) + '\n')
        raise RuntimeError('Package post-install changed factory payload bytes: ' + ', '.join(changes[:5]))
    result = {'format': 'DaOS-NetBSD-ui-native-finalization-v1', 'runtime_lock_sha256': lock_hash,
              'package_count': len(lock['packages']), 'native_package_scripts_completed': True,
              'source_built_Qt': False, 'GUI_execution_verified': False,
              'factory_payload_unchanged': True, 'factory_payload_files_verified': len(baseline['files'])}
    with tempfile.NamedTemporaryFile(mode='w', dir=state, prefix='finalized-', delete=False) as output:
        json.dump(result, output, indent=2); output.write('\n'); output.flush(); os.fsync(output.fileno()); temporary = output.name
    os.replace(temporary, receipt)
    print('DAOS_UI_PACKAGES_FINALIZED ' + json.dumps(result), flush=True)
    return result

def main():
    require_native()
    if Path('/var').stat().st_dev == Path('/').stat().st_dev:
        raise RuntimeError('Persistent /var must be mounted before package finalization')
    finalize(Path('/usr/share/daos/ui-packages'), Path('/var/db/daos-ui'), Path('/var/db/pkg'),
             Path('/usr/share/daos/ui-runtime-payload.json'))

if __name__ == '__main__': main()

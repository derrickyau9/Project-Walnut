"""Authenticated, pinned ARM64 Python/Qt rootfs inputs and script-free staging.

No package installation, maintainer script, chroot, binfmt registration or
privileged mount is performed. This is a generic software-emulation userland.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://deb.debian.org/debian/'
LOCK_FORMAT = 'hf-arm64-qt-package-lock-v1'
STAGE_FORMAT = 'hf-arm64-qt-rootfs-v1'
MAX_TOTAL = 1024 * 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def json_write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def fetch(url, maximum):
    if not isinstance(url, str) or not url.startswith(BASE) or '?' in url or '#' in url:
        raise ValueError('only credential-free official Debian archive URLs are supported')
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('authenticated input exceeds byte limit')
    return data


def profile_read(path):
    profile = path if isinstance(path, dict) else json.loads(Path(path).read_text(encoding='utf-8'))
    if (profile.get('format') != 'hf-arm64-qt-rootfs-profile-v1' or profile.get('architecture') != 'arm64' or
            profile.get('base_url') != BASE or profile.get('suite') != 'trixie' or profile.get('component') != 'main' or
            profile.get('index') != 'main/binary-arm64/Packages.xz' or profile.get('include_recommends') is not False or
            profile.get('execute_maintainer_scripts') is not False or not isinstance(profile.get('seeds'), list) or
            not profile['seeds'] or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]+', name) for name in profile['seeds'])):
        raise ValueError('unsupported ARM64 rootfs profile')
    return profile


def release_metadata(release, profile, *, allow_historical=False, now=None):
    text = release.decode('utf-8')
    fields = {}
    for name in ('Origin', 'Codename', 'Architectures', 'Components', 'Date', 'Valid-Until'):
        values = re.findall('^' + re.escape(name) + r': (.+)$', text, re.M)
        if len(values) > 1: raise ValueError('ambiguous signed release field')
        fields[name] = values[0] if values else None
    if (fields['Origin'] != 'Debian' or fields['Codename'] != profile['suite'] or
            profile['architecture'] not in (fields['Architectures'] or '').split() or
            profile['component'] not in (fields['Components'] or '').split()):
        raise ValueError('signed release does not describe the requested Debian profile')
    try:
        issued = parsedate_to_datetime(fields['Date'])
        expires = parsedate_to_datetime(fields['Valid-Until']) if fields['Valid-Until'] else None
    except (TypeError, ValueError) as exc: raise ValueError('invalid signed release date') from exc
    if issued.tzinfo is None or (expires and expires.tzinfo is None): raise ValueError('signed release date lacks timezone')
    now = now or datetime.now(timezone.utc)
    if issued > now + timedelta(days=1): raise ValueError('signed release is from the future')
    if not allow_historical and (issued < now - timedelta(days=370) or (expires and expires < now)):
        raise ValueError('fresh input acquisition refuses expired/stale release metadata')
    return {'codename': fields['Codename'], 'date': fields['Date'], 'valid_until': fields['Valid-Until'],
            'historical_replay_permitted': bool(allow_historical)}


def authenticate(inputs, profile, *, expected=None, offline=False):
    """Verify signed release and the exact ARM64 index before trusting records."""
    inputs = Path(inputs)
    release_path, index_path = inputs / 'InRelease', inputs / 'Packages.xz'
    if expected:
        # Preserve authenticated metadata with source, so a fresh pinned replay
        # does not depend on retention of a moving mirror's old signed index.
        for path, name, key, maximum in ((release_path, 'InRelease', 'release_sha256', 2 * 1024**2),
                                         (index_path, 'Packages.xz', 'index_sha256', 32 * 1024**2)):
            saved = ROOT / 'system/arm64-rootfs/metadata' / name
            if not path.exists() and saved.is_file() and saved.stat().st_size <= maximum and digest(saved) == expected.get(key):
                path.write_bytes(saved.read_bytes())
    if not release_path.exists():
        if offline: raise ValueError('offline cache lacks InRelease')
        release_path.write_bytes(fetch(BASE + 'dists/trixie/InRelease', 2 * 1024**2))
    release = release_path.read_bytes()
    if len(release) > 2 * 1024**2:
        raise ValueError('release metadata exceeds limit')
    keyring = Path('/usr/share/keyrings/debian-archive-keyring.gpg')
    if not keyring.is_file():
        raise RuntimeError('installed official Debian public archive keyring is required')
    checked = subprocess.run(['gpgv', '--status-fd=1', '--keyring', str(keyring), str(release_path)],
                             capture_output=True, text=True)
    signatures = re.findall(r'^\[GNUPG:\] VALIDSIG ([0-9A-F]+) ', checked.stdout, re.M)
    if checked.returncode or not signatures:
        raise ValueError('Debian InRelease signature verification failed')
    metadata = release_metadata(release, profile, allow_historical=expected is not None)
    match = re.search(r'^ ([0-9a-f]{64}) +(\d+) +' + re.escape(profile['index']) + r'$', release.decode('utf-8'), re.M)
    if not match or not 0 < int(match[2]) <= 32 * 1024**2:
        raise ValueError('signed ARM64 index entry is missing or oversized')
    if not index_path.exists():
        if offline: raise ValueError('offline cache lacks the signed ARM64 index')
        # A content-addressed index URL prevents a moving suite index from
        # silently changing the authenticated snapshot during preparation.
        index_url = BASE + 'dists/trixie/main/binary-arm64/by-hash/SHA256/' + match[1]
        index_path.write_bytes(fetch(index_url, 32 * 1024**2))
    packed = index_path.read_bytes()
    if len(packed) != int(match[2]) or hashlib.sha256(packed).hexdigest() != match[1]:
        raise ValueError('ARM64 package index differs from signed release')
    decoder = lzma.LZMADecompressor()
    text = decoder.decompress(packed, max_length=128 * 1024**2)
    if not decoder.eof or decoder.unused_data:
        raise ValueError('ARM64 package index expanded beyond bounds or contains trailing data')
    auth = {'signature_verified': True, 'release_sha256': hashlib.sha256(release).hexdigest(),
            'index_sha256': match[1], 'index_bytes': len(packed), 'signing_fingerprints': sorted(set(signatures)),
            'keyring_sha256': digest(keyring), 'release_url': BASE + 'dists/trixie/InRelease',
            'index_url': BASE + 'dists/trixie/main/binary-arm64/by-hash/SHA256/' + match[1],
            'release_metadata': {key: value for key, value in metadata.items() if key != 'historical_replay_permitted'}}
    if expected and any(auth[key] != expected.get(key) for key in ('release_sha256', 'index_sha256', 'index_bytes', 'signing_fingerprints')):
        raise ValueError('cached authenticated snapshot differs from pinned lock')
    return text.decode('utf-8'), auth


def package_entry(record):
    filename = record['Filename']
    if (not filename.startswith('pool/') or '..' in PurePosixPath(filename).parts or '\\' in filename or ':' in filename or
            not re.fullmatch('[0-9a-f]{64}', record.get('SHA256', '')) or not record.get('Size', '').isdigit()):
        raise ValueError('invalid authenticated package archive declaration')
    size = int(record['Size'])
    if not 0 < size <= 256 * 1024**2:
        raise ValueError('package archive exceeds individual byte budget')
    return {'name': record['Package'], 'version': record['Version'], 'architecture': record['Architecture'],
            'filename': filename, 'url': BASE + filename, 'sha256': record['SHA256'], 'bytes': size,
            'installed_kib': int(record.get('Installed-Size', '0')), 'depends': record.get('Depends', ''),
            'pre_depends': record.get('Pre-Depends', ''), 'provides': record.get('Provides', '')}


def download_package(inputs, package, *, offline=False):
    directory = Path(inputs) / 'packages'; directory.mkdir(exist_ok=True)
    target = directory / (package['sha256'] + '.deb')
    if target.exists():
        if target.is_symlink() or target.stat().st_size != package['bytes'] or digest(target) != package['sha256']:
            raise ValueError('cached package differs from pinned hash: ' + package['name'])
        return
    if offline: raise ValueError('offline cache lacks package: ' + package['name'])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=package['sha256'] + '.', suffix='.part', delete=False) as stream:
            temporary = Path(stream.name)
            hasher, length = hashlib.sha256(), 0
            with urllib.request.urlopen(package['url'], timeout=90) as response:
                while block := response.read(128 * 1024):
                    length += len(block)
                    if length > package['bytes']: raise ValueError('package exceeds declared size')
                    hasher.update(block); stream.write(block)
        if length != package['bytes'] or hasher.hexdigest() != package['sha256']:
            raise ValueError('package checksum differs from signed index: ' + package['name'])
        os.replace(temporary, target); temporary = None
        print('Verified ' + package['name'] + ' ' + package['version'], flush=True)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def prepare(profile_path, inputs, *, lock_path=None, offline=False):
    from tools.rootfs_deps import parse_index, resolve
    profile = profile_read(profile_path)
    inputs = Path(inputs).resolve()
    marker = inputs / '.rootfs-inputs.json'
    if inputs.exists() and any(inputs.iterdir()) and not marker.is_file():
        raise ValueError('input cache must be new or a marked rootfs input directory')
    inputs.mkdir(parents=True, exist_ok=True)
    if not marker.exists(): json_write(marker, {'format': 'hf-rootfs-input-cache-v1'})
    pinned = json.loads(Path(lock_path).read_text(encoding='utf-8')) if lock_path else None
    if pinned and (pinned.get('format') != LOCK_FORMAT or pinned.get('profile') != profile):
        raise ValueError('pinned package lock/profile mismatch')
    text, auth = authenticate(inputs, profile, expected=pinned.get('authentication') if pinned else None, offline=offline)
    closure = resolve(parse_index(text), profile['seeds'], architecture='arm64')
    packages = [package_entry(record) for record in closure['packages']]
    if len(packages) > 512 or sum(item['bytes'] for item in packages) > MAX_TOTAL:
        raise ValueError('declared dependency closure exceeds package/byte budget')
    lock = {'format': LOCK_FORMAT, 'profile': profile, 'authentication': auth, 'packages': packages,
            'edges': closure['edges'], 'depends_and_predepends_complete': True,
            'recommends_included': False, 'maintainer_scripts_executed': False,
            'archive_bytes': sum(item['bytes'] for item in packages),
            'installed_kib': sum(item['installed_kib'] for item in packages)}
    if pinned and (pinned.get('packages') != packages or pinned.get('edges') != closure['edges']):
        raise ValueError('pinned dependency closure differs from its authenticated index')
    if pinned:
        # A newer trusted local keyring may verify the same pinned signatures.
        # Preserve the original acquisition observation in the immutable lock.
        lock['authentication'] = pinned['authentication']
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda item: download_package(inputs, item, offline=offline), packages))
    target = inputs / 'package-lock.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8')) != lock:
        raise ValueError('existing package lock differs; use a new input snapshot directory')
    json_write(target, lock)
    print(json.dumps({'package_lock': str(target), 'packages': len(packages),
                      'archive_bytes': lock['archive_bytes'], 'installed_kib': lock['installed_kib']}, indent=2))
    return lock


def guest_path(name):
    """Normalize a data.tar path without consulting host filesystem symlinks."""
    if not isinstance(name, str) or '\x00' in name or '\\' in name or name.startswith('/'):
        raise ValueError('unsafe package member path')
    parts = []
    for part in name.split('/'):
        if part in ('', '.'): continue
        if part == '..': raise ValueError('package member traverses its root')
        parts.append(part)
    return PurePosixPath(*parts).as_posix() if parts else ''


def normalized_link(path, target):
    if not isinstance(target, str) or not target or '\x00' in target or '\\' in target:
        raise ValueError('invalid guest symlink')
    parts = [] if target.startswith('/') else list(PurePosixPath(path).parent.parts)
    for part in target.split('/'):
        if part in ('', '.'): continue
        if part == '..':
            if not parts: raise ValueError('guest symlink escapes root')
            parts.pop()
        else: parts.append(part)
    destination = '/'.join(parts) or '.'
    return os.path.relpath(destination, str(PurePosixPath(path).parent)).replace(os.sep, '/')


def confined(root, relative):
    path = root / relative
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError('package parent escapes staging root')
    return path


def extract_archive(archive, root, transforms):
    """Extract only authenticated package data, never control scripts or devices."""
    process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = []
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as tar:
            for member in tar:
                relative = guest_path(member.name)
                if not relative: continue
                path = confined(root, relative)
                mode = member.mode & 0o1777
                if member.mode & 0o6000: transforms['stripped_special_permissions'].append(relative)
                if member.isdir():
                    if path.is_symlink():
                        if not path.resolve().is_relative_to(root) or not path.is_dir(): raise ValueError('directory alias escapes root')
                    elif path.exists() and not path.is_dir(): raise ValueError('package directory/file collision: ' + relative)
                    else: path.mkdir(parents=True, exist_ok=True); path.chmod(mode)
                elif member.issym():
                    target = normalized_link(relative, member.linkname)
                    if member.linkname != target:
                        transforms['rewritten_symlinks'].append({'path': relative, 'original': member.linkname, 'target': target})
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.is_symlink():
                        if os.readlink(path) != target: raise ValueError('conflicting package symlink: ' + relative)
                    elif path.exists(): raise ValueError('package symlink collision: ' + relative)
                    else: path.symlink_to(target)
                elif member.islnk():
                    pending.append((relative, guest_path(member.linkname), mode))
                elif member.isfile():
                    if member.size < 0 or member.size > 512 * 1024 * 1024: raise ValueError('package member exceeds limit')
                    transforms['expanded_bytes'] = transforms.get('expanded_bytes', 0) + member.size
                    if transforms['expanded_bytes'] > 3 * 1024**3: raise ValueError('expanded rootfs exceeds staging budget')
                    if path.is_symlink() or (path.exists() and not path.is_file()): raise ValueError('package file/type collision: ' + relative)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    value = hashlib.sha256()
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.hf-extract-', delete=False) as stream:
                        temporary = Path(stream.name)
                        left = member.size
                        while left:
                            block = source.read(min(left, 1024 * 1024))
                            if not block: raise ValueError('truncated package file')
                            value.update(block); stream.write(block); left -= len(block)
                    try:
                        if path.exists():
                            if digest(path) != value.hexdigest() or stat.S_IMODE(path.stat().st_mode) != mode:
                                raise ValueError('conflicting package file: ' + relative)
                        else: temporary.chmod(mode); temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                else: raise ValueError('unsupported special package member: ' + relative)
        if process.wait() != 0: raise ValueError('dpkg-deb data extraction failed: ' + process.stderr.read().decode(errors='replace')[:1000])
        for relative, target, mode in pending:
            path, original = confined(root, relative), confined(root, target)
            if original.is_symlink() or not original.is_file(): raise ValueError('invalid package hardlink target')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_file() or digest(path) != digest(original): raise ValueError('conflicting package hardlink')
            else: os.link(original, path)
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()


def inventory(root, *, validate_code=True):
    entries, elf_count, python_count = [], 0, 0
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat(); mode = stat.S_IMODE(info.st_mode)
            entry = {'path': relative, 'mode': mode}
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if target.startswith('/') or normalized_link(relative, target) != target:
                    raise ValueError('noncontained/noncanonical staged symlink: ' + relative)
                if not path.resolve().is_relative_to(root): raise ValueError('staged symlink escapes root')
                entry.update(type='symlink', target=target)
            elif stat.S_ISDIR(info.st_mode): entry.update(type='directory')
            elif stat.S_ISREG(info.st_mode):
                entry.update(type='file', sha256=digest(path), size=info.st_size)
                with path.open('rb') as stream: header = stream.read(64)
                if header.startswith(b'\x7fELF'):
                    if len(header) < 64 or header[4:6] != b'\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
                        raise ValueError('rootfs contains non-AArch64 ELF: ' + relative)
                    elf_count += 1
                if validate_code and path.suffix == '.py' and relative.startswith('usr/lib/python3'):
                    compile(path.read_bytes(), relative, 'exec', dont_inherit=True)
                    python_count += 1
            else: raise ValueError('unexpected rootfs special file: ' + relative)
            entries.append(entry)
    entries.sort(key=lambda item: item['path'])
    return entries, {'aarch64_elf_files': elf_count, 'python_sources_compiled_without_execution': python_count}


def tree_digest(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_stage(rootfs, manifest):
    """Validate the complete script-free rootfs inventory for image consumers."""
    root = Path(rootfs).resolve()
    document = json.loads(Path(manifest).read_text(encoding='utf-8')) if not isinstance(manifest, dict) else manifest
    if (document.get('format') != STAGE_FORMAT or document.get('architecture') != 'arm64' or
            document.get('no_postinst') is not True or document.get('authentication', {}).get('signature_verified') is not True or
            not re.fullmatch(r'[0-9a-f]{64}', document.get('package_lock_sha256', ''))):
        raise ValueError('unverified ARM64 rootfs manifest')
    entries, code = inventory(root)
    if entries != document.get('entries') or tree_digest(entries) != document.get('tree_sha256'):
        raise ValueError('staged rootfs tree differs from manifest')
    if code != document.get('code_validation'): raise ValueError('staged code inventory differs')
    return {'format': STAGE_FORMAT, 'verified': True, 'entries': len(entries), 'tree_sha256': document['tree_sha256'],
            'package_lock_sha256': document['package_lock_sha256'], **code}


def stage(lock_path, inputs, output):
    from tools.rootfs_deps import parse_index, resolve
    if os.name != 'posix': raise ValueError('rootfs staging requires Linux symlink/mode semantics')
    inputs, output = Path(inputs).resolve(), Path(output).absolute()
    if output.resolve().is_relative_to(inputs): raise ValueError('staging output must be separate from authenticated input cache')
    if output.resolve().is_relative_to(ROOT) and not output.resolve().is_relative_to(ROOT / 'build'):
        raise ValueError('staging inside source is supported only beneath build/')
    lock_path = Path(lock_path).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if lock.get('format') != LOCK_FORMAT: raise ValueError('unsupported package lock')
    profile = profile_read(lock['profile'])
    text, auth = authenticate(inputs, profile, expected=lock['authentication'], offline=True)
    closure = resolve(parse_index(text), profile['seeds'], architecture='arm64')
    if ([package_entry(record) for record in closure['packages']] != lock.get('packages') or closure['edges'] != lock.get('edges') or
            lock.get('maintainer_scripts_executed') is not False): raise ValueError('lock does not match authenticated dependency closure')
    for package in lock['packages']: download_package(inputs, package, offline=True)
    if output.exists() or output.is_symlink(): raise ValueError('staging output must not already exist')
    output.mkdir(parents=True)
    root = output / 'rootfs'; root.mkdir()
    for name in ('bin', 'sbin', 'lib'):
        (root / 'usr' / name).mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to('usr/' + name)
    transforms = {'rewritten_symlinks': [], 'stripped_special_permissions': []}
    for package in lock['packages']:
        extract_archive(inputs / 'packages' / (package['sha256'] + '.deb'), root, transforms)
        print('Extracted ' + package['name'], flush=True)
    entries, code = inventory(root)
    manifest = {'format': STAGE_FORMAT, 'architecture': 'arm64', 'no_postinst': True,
                'package_lock_sha256': digest(lock_path), 'authentication': auth,
                'packages': [{key: item[key] for key in ('name', 'version', 'architecture', 'sha256')} for item in lock['packages']],
                'entries': entries, 'tree_sha256': tree_digest(entries), 'code_validation': code,
                'transforms': transforms, 'scope': 'Extracted Debian Python/Qt runtime; no general Debian installation, package scripts, board or hardware validation'}
    json_write(output / 'stage-manifest.json', manifest)
    print(json.dumps(verify_stage(root, manifest), indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--profile', type=Path, default=ROOT / 'system/arm64-rootfs/debian-trixie-qt5.json')
    command.add_argument('--inputs-dir', type=Path, required=True)
    command.add_argument('--lock', type=Path); command.add_argument('--offline', action='store_true')
    command = sub.add_parser('stage')
    command.add_argument('--lock', type=Path, required=True); command.add_argument('--inputs-dir', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('validate')
    command.add_argument('--rootfs', type=Path, required=True); command.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare': prepare(args.profile, args.inputs_dir, lock_path=args.lock, offline=args.offline)
    elif args.action == 'stage': stage(args.lock, args.inputs_dir, args.output)
    else: print(json.dumps(verify_stage(args.rootfs, args.manifest), indent=2))


if __name__ == '__main__': main()

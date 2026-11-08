#!/usr/bin/env python3
"""Lock, fetch and stage the real NetBSD/aarch64 Qt/PyQt runtime closure.

No target executable or package script is executed on the host. Binary package
inputs remain explicitly separate from the source-built base and camera.
"""
from __future__ import annotations

import argparse
import bz2
from concurrent.futures import ThreadPoolExecutor
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tarfile
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
RECIPE = json.loads((HERE / 'recipe.json').read_text())


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink(): raise ValueError('Redirected output: ' + str(path))
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('x', encoding='utf-8') as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write('\n')
    os.replace(temp, path)


def summary():
    data = bz2.decompress((HERE / 'metadata/pkg_summary.bz2').read_bytes()).decode('utf-8')
    records = {}
    for block in data.split('\n\n'):
        row = {}
        for line in block.splitlines():
            key, sep, value = line.partition('=')
            if sep: row.setdefault(key, []).append(value)
        if 'PKGNAME' in row: records[row['PKGNAME'][0]] = row
    return records


def version(value):
    # pkgsrc Dewey ordering: alpha < beta < rc/pre < release, nb last.
    # Reference: NetBSD/pkgsrc pkgtools/pkg_install/files/lib/dewey.c.
    if not re.fullmatch(r'[0-9][0-9A-Za-z._+]*', value) or len(value) > 128:
        raise ValueError('Unsupported dependency version syntax: ' + value)
    numbers, revision = [], 0
    while value:
        number = re.match(r'[0-9]+', value)
        if number:
            numbers.append(int(number.group())); value = value[len(number.group()):]; continue
        suffix = re.match(r'(?i)nb([0-9]*)', value)
        if suffix:
            revision = int(suffix.group(1) or '0'); value = value[len(suffix.group()):]; continue
        for modifier, number in (('alpha', -3), ('beta', -2), ('pre', -1), ('rc', -1), ('pl', 0), ('_', 0), ('.', 0)):
            if value.lower().startswith(modifier):
                numbers.append(number); value = value[len(modifier):]; break
        else:
            if value[0].isalpha(): numbers.extend((0, ord(value[0].lower()) - ord('a') + 1))
            value = value[1:]
    return tuple(numbers) + (0,) * (256 - len(numbers)) + (revision,)


def matches(name, constraint):
    brace = re.search(r'\{([^{}]*)\}', constraint)
    if brace:
        return any(matches(name, constraint[:brace.start()] + choice + constraint[brace.end():])
                   for choice in brace.group(1).split(','))
    if '{' in constraint or '}' in constraint: raise ValueError('Unbalanced package pattern')
    if not any(c in constraint for c in '<>='):
        return fnmatch.fnmatchcase(name, constraint)
    match = re.fullmatch(r'([^<>=]+)((?:[<>]=?[^<>=]+|=[^<>=]+)+)', constraint)
    if not match: raise ValueError('Unsupported dependency constraint: ' + constraint)
    base, wanted = match.groups()
    actual_base, actual_version = name.rsplit('-', 1)
    if actual_base != base: return False
    actual = version(actual_version)
    for operator, required in re.findall(r'(>=|<=|>|<|=)([^<>=]+)', wanted):
        required = version(required)
        if not {'>=': actual >= required, '<=': actual <= required,
                '>': actual > required, '<': actual < required, '=': actual == required}[operator]:
            return False
    return True


def lock_runtime(args):
    records = summary()
    base_index = {}
    for name in records: base_index.setdefault(name.rsplit('-', 1)[0], []).append(name)
    selected, roots, visiting, order = {}, [], set(), []
    def visit(name):
        if name in selected: return
        if name in visiting: raise ValueError('Dependency cycle: ' + name)
        visiting.add(name)
        row = records[name]
        for field, expected in RECIPE['package_build_abi'].items():
            if row.get(field) != [expected]:
                raise ValueError('Unexpected binary package ABI: ' + name + ' ' + field)
        dependencies = []
        for constraint in row.get('DEPENDS', []):
            candidates = [n for n in records if matches(n, constraint)]
            if not candidates:
                raise ValueError('Missing dependency package: ' + constraint)
            chosen = max(candidates, key=lambda n: version(n.rsplit('-', 1)[1]))
            dependencies.append(chosen); visit(chosen)
        visiting.remove(name)
        selected[name] = (row, dependencies); order.append(name)
    for base in RECIPE['roots']:
        candidates = base_index.get(base, [])
        if not candidates: raise ValueError('Missing root: ' + base)
        chosen = max(candidates, key=lambda n: version(n.rsplit('-', 1)[1]))
        roots.append(chosen); visit(chosen)
    checksum_text = bz2.decompress((HERE / 'metadata/SHA512.bz2').read_bytes()).decode()
    checksums = dict(re.findall(r'SHA512 \(([^)]+)\) = ([0-9a-f]{128})', checksum_text))
    provided = {p for row, _ in selected.values() for p in row.get('PROVIDES', [])}
    required = {p for row, _ in selected.values() for p in row.get('REQUIRES', [])}
    missing_package_libs = sorted(p for p in required - provided if p.startswith('/usr/pkg/'))
    if missing_package_libs:
        raise ValueError('Unresolved package library providers: ' + ', '.join(missing_package_libs))
    packages = []
    for name in order:
        row, deps = selected[name]
        filename = row['FILE_NAME'][0]
        found = {value for key, value in checksums.items() if key == filename or key.endswith('/' + filename)}
        if len(found) != 1: raise ValueError('Missing/ambiguous official SHA512: ' + filename)
        packages.append({'name': name, 'file': filename,
                         'url': RECIPE['repository'] + 'All/' + urllib.parse.quote(filename),
                         'sha512': found.pop(), 'bytes': int(row['FILE_SIZE'][0]),
                         'installed_bytes': int(row.get('SIZE_PKG', ['0'])[0]),
                         'depends': deps, 'dependency_constraints': row.get('DEPENDS', []),
                         'requires': row.get('REQUIRES', []), 'provides': row.get('PROVIDES', []),
                         'abi': RECIPE['package_build_abi'], 'license': row.get('LICENSE', [''])[0]})
    lock = {'format': 'DaOS-NetBSD-binary-runtime-lock-v1', 'name': 'daos-ui',
            'target': RECIPE['target'], 'repository': RECIPE['repository'], 'roots': roots,
            'metadata': {name: {'url': RECIPE['repository'] + ('All/' if name == 'pkg_summary.bz2' else '') + name,
                               'sha512': digest(HERE / 'metadata' / name, 'sha512')}
                         for name in ('pkg_summary.bz2', 'SHA512.bz2')},
            'authentication': 'Official HTTPS repository checksum metadata; no independent package signature claim.',
            'source_world_requires': sorted(required - provided), 'packages': packages,
            'download_bytes': sum(p['bytes'] for p in packages),
            'installed_bytes': sum(p['installed_bytes'] for p in packages),
            'target_execution_verified': False}
    save(args.lock, lock)
    print(json.dumps({'lock': str(args.lock), 'roots': roots, 'packages': len(packages),
                      'download_bytes': lock['download_bytes'], 'installed_bytes': lock['installed_bytes'],
                      'source_world_requires': len(lock['source_world_requires'])}, indent=2))


def read_lock(path):
    lock = json.loads(Path(path).read_text())
    if lock.get('format') != 'DaOS-NetBSD-binary-runtime-lock-v1' or lock['target'] != RECIPE['target']:
        raise ValueError('Expected pinned DaOS NetBSD/aarch64 UI runtime lock')
    for row in lock['packages']:
        if not re.fullmatch(r'[A-Za-z0-9_+.-]+\.tgz', row['file']): raise ValueError('Invalid package filename')
        if not re.fullmatch(r'[0-9a-f]{128}', row['sha512']): raise ValueError('Invalid SHA512')
        if row['abi'] != RECIPE['package_build_abi']: raise ValueError('Foreign ABI in lock')
        if row['url'] != RECIPE['repository'] + 'All/' + urllib.parse.quote(row['file']):
            raise ValueError('Package URL outside pinned repository')
    return lock


def fetch_runtime(args):
    lock = read_lock(args.lock)
    args.cache.mkdir(parents=True, exist_ok=True)
    def get(row):
        target = args.cache / row['file']
        if target.exists():
            if target.is_symlink() or target.stat().st_size != row['bytes'] or digest(target, 'sha512') != row['sha512']:
                raise ValueError('Cached package is corrupt: ' + row['file'])
            return row['file']
        temp = target.with_suffix('.tgz.partial')
        if temp.exists(): raise ValueError('Existing partial download: ' + str(temp))
        with urllib.request.urlopen(row['url'], timeout=60) as response, temp.open('xb') as output:
            if response.status != 200: raise ValueError('Non-200 package response')
            shutil.copyfileobj(response, output, 1024 * 1024)
        if temp.stat().st_size != row['bytes'] or digest(temp, 'sha512') != row['sha512']:
            raise ValueError('Downloaded package SHA512/size mismatch: ' + row['file'])
        temp.replace(target)
        return row['file']
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name in pool.map(get, lock['packages']): print('verified ' + name, flush=True)
    save(args.cache / 'download-receipt.json', {'format': 'DaOS-ui-binary-download-receipt-v1',
         'runtime_lock_sha256': digest(args.lock), 'packages_verified': len(lock['packages']),
         'download_bytes': lock['download_bytes'], 'target_executables_run': False})


def canonical(name):
    if not name or '\\' in name or any(ord(c) < 32 for c in name): raise ValueError('Invalid path')
    while name.startswith('./'): name = name[2:]
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts: raise ValueError('Escaping package path: ' + name)
    return p.as_posix()


def safe_target(root, name):
    result = root / canonical(name)
    for part in result.parents:
        if part == root.parent: break
        if part.is_symlink(): raise ValueError('Symlink parent in staging path: ' + str(part))
    return result


def in_tree(root, name):
    """Resolve absolute target links within a staging root, never in the host."""
    parts = list(PurePosixPath(name.lstrip('/')).parts)
    done, count = [], 0
    while parts:
        part = parts.pop(0)
        if part == '.': continue
        if part == '..':
            if not done: raise ValueError('Escaping target link')
            done.pop(); continue
        candidate = root.joinpath(*done, part)
        if candidate.is_symlink():
            count += 1
            if count > 40: raise ValueError('Target link loop')
            link = os.readlink(candidate)
            if link.startswith('/'): done = []
            parts = list(PurePosixPath(link).parts)[1 if link.startswith('/') else 0:] + parts
        else: done.append(part)
    return root.joinpath(*done)


def elf_check(path, allow_relocatable=False):
    with path.open('rb') as stream: head = stream.read(64)
    if not head.startswith(b'\x7fELF'): return
    if len(head) != 64 or head[4:6] != b'\x02\x01' or struct.unpack_from('<H', head, 18)[0] != 183:
        raise ValueError('Foreign ELF in NetBSD runtime: ' + str(path))
    elf_type = struct.unpack_from('<H', head, 16)[0]
    if elf_type == 1:
        if not allow_relocatable: raise ValueError('Relocatable object lacks a verified package ABI context')
        if struct.unpack_from('<H', head, 56)[0] != 0: raise ValueError('Unexpected program table in relocatable object')
        return 'relocatable'
    if elf_type not in (2, 3): raise ValueError('Unsupported ELF type')
    data = path.read_bytes()
    offset, size, count = struct.unpack_from('<Q', data, 32)[0], *struct.unpack_from('<HH', data, 54)
    if size != 56 or not 1 <= count <= 1024 or offset + size * count > len(data): raise ValueError('Bad ELF program table')
    netbsd_note = False
    for i in range(count):
        kind, flags, start, va, pa, length, mem, align = struct.unpack_from('<IIQQQQQQ', data, offset + i * size)
        if start + length > len(data): raise ValueError('Bad ELF segment')
        segment = data[start:start+length]
        if kind == 3 and segment.rstrip(b'\0') not in (b'/libexec/ld.elf_so', b'/usr/libexec/ld.elf_so'):
            raise ValueError('Foreign ELF interpreter')
        if kind != 4: continue
        cursor = 0
        while cursor + 12 <= len(segment):
            ns, ds, typ = struct.unpack_from('<III', segment, cursor); cursor += 12
            if ns > 4096 or ds > 65536 or cursor + ((ns+3)&~3) + ((ds+3)&~3) > len(segment): raise ValueError('Bad ELF note')
            name = segment[cursor:cursor+ns].rstrip(b'\0'); cursor += (ns+3)&~3
            desc = segment[cursor:cursor+ds]; cursor += (ds+3)&~3
            if name == b'NetBSD' and typ == 1 and ds == 4: netbsd_note = True
            if name == b'GNU' and typ == 1: raise ValueError('GNU/Linux ELF ABI note in runtime')
            if name in (b'FreeBSD', b'OpenBSD'): raise ValueError('Foreign BSD ELF note in runtime')
    if not netbsd_note: raise ValueError('ELF lacks a NetBSD ABI note: ' + str(path))


def source_world_missing(base, lock):
    # NetBSD installs both video and input drivers using Makefile.xf86-driver's
    # modules/drivers LIBDIR; Xorg's standard search includes this subdirectory.
    absent = [name for name in lock['source_world_requires'] if not in_tree(base, name).is_file()]
    absent += [name for name in ('/usr/X11R7/bin/Xorg', '/usr/X11R7/bin/xauth',
               '/usr/X11R7/lib/modules/drivers/wsfb_drv.so', '/usr/X11R7/lib/modules/drivers/kbd_drv.so',
               '/usr/X11R7/lib/modules/drivers/mouse_drv.so', '/usr/sbin/pkg_add', '/usr/sbin/pkg_info',
               '/usr/libexec/ld.elf_so', '/libexec/ld.elf_so')
               if not in_tree(base, name).is_file()]
    return absent


def stage_runtime(args):
    lock = read_lock(args.lock)
    root, base = args.destdir.resolve(strict=True), args.base.resolve(strict=True)
    if root == Path('/') or base == Path('/') or root == base: raise ValueError('Private, separate roots required')
    if args.manifest.exists(): raise ValueError('Create-only manifest already exists')
    # Refuse missing source-world ABI before mutating the package overlay.
    absent = source_world_missing(base, lock)
    if absent: raise ValueError('Missing source-world runtime libraries: ' + ', '.join(absent))
    entries, scripts = {}, []
    def record(name, package=None):
        path = safe_target(root, name)
        if path.is_symlink():
            link = os.readlink(path)
            entries[name] = {'path': name, 'type': 'link', 'link': link,
                             'sha256': hashlib.sha256(link.encode()).hexdigest(), 'mode': 0o777}
        elif path.is_file():
            elf_kind = elf_check(path, allow_relocatable=package is not None)
            entries[name] = {'path': name, 'type': 'file', 'bytes': path.stat().st_size,
                             'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
            if elf_kind == 'relocatable':
                entries[name]['elf_kind'] = 'relocatable'
                entries[name]['abi_provenance'] = {
                    'format': 'DaOS-NetBSD-package-ABI-v1', 'package': package['name'],
                    'archive_sha512': package['sha512'], 'archive_member': name.removeprefix('usr/pkg/'),
                    'OPSYS': 'NetBSD', 'MACHINE_ARCH': 'aarch64', 'OS_VERSION': '11.0',
                    'build_info_sha256': package['build_info_sha256'], 'archive_build_info_checked': True}
    for row in lock['packages']:
        archive = args.cache / row['file']
        if archive.is_symlink() or archive.stat().st_size != row['bytes'] or digest(archive, 'sha512') != row['sha512']:
            raise ValueError('Package archive not verified: ' + str(archive))
        with tarfile.open(archive, 'r:gz') as tar:
            info_bytes = tar.extractfile('+BUILD_INFO').read()
            row = dict(row, build_info_sha256=hashlib.sha256(info_bytes).hexdigest())
            info = info_bytes.decode()
            for key, value in row['abi'].items():
                if not re.search(r'^' + key + '=' + re.escape(value) + r'$', info, re.M): raise ValueError('Archive ABI metadata mismatch')
            contents = tar.extractfile('+CONTENTS').read().decode()
            prefixes = set(re.findall(r'^@cwd (.+)$', contents, re.M))
            if prefixes != {'/usr/pkg'}: raise ValueError('Unexpected package prefix: ' + str(prefixes))
            if any(m.name in ('+INSTALL', '+DEINSTALL') for m in tar.getmembers()): scripts.append(row['name'])
            hardlinks = []
            for member in tar.getmembers():
                raw = canonical(member.name)
                if raw.startswith('+') or raw == '.': continue
                name = 'usr/pkg/' + raw
                path = safe_target(root, name)
                if member.isdir():
                    if path.exists() and not path.is_dir(): raise ValueError('Directory collision')
                    path.mkdir(parents=True, exist_ok=True); continue
                path.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(path): raise ValueError('Payload path collision: ' + name)
                if member.isfile():
                    with tar.extractfile(member) as source, path.open('xb') as output: shutil.copyfileobj(source, output)
                    path.chmod(member.mode & 0o777)
                elif member.issym():
                    if '\\' in member.linkname or any(ord(c) < 32 for c in member.linkname): raise ValueError('Invalid symlink')
                    path.symlink_to(member.linkname)
                    in_tree(root, '/' + name)
                elif member.islnk():
                    hardlinks.append((name, 'usr/pkg/' + canonical(member.linkname))); continue
                else: raise ValueError('Special file in binary package: ' + name)
                record(name, row)
            for name, source_name in hardlinks:
                source = safe_target(root, source_name)
                if source.is_symlink() or not source.is_file(): raise ValueError('Invalid package hardlink')
                path = safe_target(root, name)
                shutil.copyfile(source, path); path.chmod(source.stat().st_mode & 0o777); record(name, row)
    # The source-world + binary-package file closure must now be complete.
    missing = [name for row in lock['packages'] for name in row['requires']
               if not (in_tree(root, name).is_file() or in_tree(base, name).is_file())]
    if missing: raise ValueError('Staged library closure is incomplete: ' + ', '.join(sorted(set(missing))))
    payload_baseline = {'format': 'DaOS-NetBSD-ui-payload-baseline-v1',
                        'runtime_lock_sha256': digest(args.lock),
                        'files': [entries[name] for name in sorted(entries)]}
    def put(source, name, mode=0o644):
        path = safe_target(root, name)
        if os.path.lexists(path): raise ValueError('Runtime integration path collision: ' + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path); path.chmod(mode); record(name)
    for source in sorted((HERE / 'files').rglob('*')):
        if not source.is_file(): continue
        if '__pycache__' in source.relative_to(HERE / 'files').parts or source.suffix in ('.pyc', '.pyo'):
            continue
        if source.is_symlink(): raise ValueError('Redirected runtime integration source')
        name = source.relative_to(HERE / 'files').as_posix()
        mode = 0o755 if name.startswith(('usr/libexec/', 'etc/rc.d/')) else 0o644
        put(source, name, mode)
    baseline_path = safe_target(root, 'usr/share/daos/ui-runtime-payload.json')
    if os.path.lexists(baseline_path): raise ValueError('Factory payload baseline already exists')
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with baseline_path.open('x', encoding='utf-8') as output:
        json.dump(payload_baseline, output, indent=2, sort_keys=True); output.write('\n')
    baseline_path.chmod(0o644); record('usr/share/daos/ui-runtime-payload.json')
    # Keep the exact offline archives for native pkg_add; host extraction does
    # not substitute for target install scripts or the real package database.
    put(args.lock, 'usr/share/daos/ui-packages/runtime.lock.json')
    for row in lock['packages']:
        put(args.cache / row['file'], 'usr/share/daos/ui-packages/' + row['file'])
    manifest = {'format': 'DaOS-BSD-component-manifest-v1', 'name': 'daos-ui', 'version': RECIPE['version'],
                'target': {'system': 'NetBSD', 'architecture': 'aarch64'},
                'files': [entries[name] for name in sorted(entries)],
                'runtime_lock_sha256': digest(args.lock),
                'source_sha256': {'install.py': digest(HERE / 'install.py'), 'recipe.json': digest(HERE / 'recipe.json')},
                'binary_runtime_closure': True, 'source_built_Qt': False,
                'package_scripts_present': scripts, 'package_scripts_executed': False,
                'package_registration_completed': False, 'target_execution_claimed': False,
                'post_install_mutates': ['usr/pkg', 'var/db/pkg', 'var/db/pkg.refcount',
                                        'var/db/daos-ui', 'var/log/daos-ui-packages.log'],
                'native_finalization': '/usr/libexec/daos/ui-finalize.py (called by rc.d/daos_ui before the GUI)',
                'verification_scope': {'ELF64_aarch64_NetBSD_notes_checked': True, 'source_world_requires_checked': True}}
    save(args.manifest, manifest)
    print(json.dumps({'component': 'daos-ui', 'files': len(entries), 'packages': len(lock['packages']),
                      'manifest': str(args.manifest), 'package_scripts_executed': False,
                      'target_execution_claimed': False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    resolve = sub.add_parser('resolve'); resolve.add_argument('--lock', type=Path, default=HERE / 'runtime.lock.json')
    fetch = sub.add_parser('fetch'); fetch.add_argument('--lock', type=Path, default=HERE / 'runtime.lock.json')
    fetch.add_argument('--cache', type=Path, required=True); fetch.add_argument('--jobs', type=int, default=4)
    stage = sub.add_parser('stage'); stage.add_argument('--lock', type=Path, default=HERE / 'runtime.lock.json')
    stage.add_argument('--cache', type=Path, required=True); stage.add_argument('--destdir', type=Path, required=True)
    stage.add_argument('--base', type=Path, required=True); stage.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    {'resolve': lock_runtime, 'fetch': fetch_runtime, 'stage': stage_runtime}[args.command](args)


if __name__ == '__main__': main()

"""Read-only, bounded ELF inspection for the DaOS FreeBSD runtime profile.

No target binary, ldd, or package script is executed. Resolution models explicit
paths, per-object search paths and recorded ldconfig directories; it does not
claim to replace a real target boot or dynamic-loader test.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import posixpath
import re
import stat
import struct

MACHINES = {'arm64': 183, 'amd64': 62}
MAX_FILE = 1024 * 1024 * 1024
MAX_PHDR = 1024
MAX_DYNAMIC = 65536
MAX_STRTAB = 16 * 1024 * 1024
MAX_STRING = 8192
MAX_ENTRIES = 200000


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _extent(offset, length, size):
    if offset < 0 or length < 0 or offset > size or length > size - offset:
        raise ValueError('ELF extent is outside the file')


def parse_elf(path):
    """Return ELF64 little-endian metadata, or None for a non-ELF regular file.

    A symlink must be resolved through the declared manifest by the caller.
    ELF32, big-endian, malformed and excessively large ELF files are refused.
    The OSABI is reported, not inferred from a filename or the executing host.
    """
    try:
        return _parse_elf(path)
    except ValueError as exc:
        raise ValueError('ELF inspection failed for ' + repr(str(Path(path))) + ': ' + str(exc)) from exc


def _parse_elf(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('ELF inspection requires an unlinked regular file')
    size = before.st_size
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            return None
        if size < 64 or size > MAX_FILE:
            raise ValueError('ELF file size is outside the inspection bound')
        stream.seek(0)
        header = struct.unpack('<16sHHIQQQIHHHHHH', stream.read(64))
        ident, _, machine, version, _, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = header
        if ident[4:7] != b'\x02\x01\x01' or version != 1 or ehsize != 64:
            raise ValueError('only valid ELF64 little-endian version 1 is supported '
                             '(class=' + str(ident[4]) + ', data=' + str(ident[5])
                             + ', ident_version=' + str(ident[6]) + ', header_version='
                             + str(version) + ', header_size=' + str(ehsize) + ')')
        if phnum > MAX_PHDR or phnum == 65535 or phnum and (phentsize != 56 or phoff < 64):
            raise ValueError('ELF program-header table is unsupported or excessive')
        _extent(phoff, phnum * phentsize, size)
        loads, dynamic = [], []
        for index in range(phnum):
            stream.seek(phoff + index * phentsize)
            kind, _, offset, vaddr, _, filesz, memsz, _ = struct.unpack('<IIQQQQQQ', stream.read(56))
            _extent(offset, filesz, size)
            if filesz > memsz or vaddr + memsz > 2 ** 64:
                raise ValueError('invalid ELF segment extent')
            if kind == 1:
                loads.append((offset, vaddr, filesz))
            elif kind == 2:
                dynamic.append((offset, filesz))
        if len(dynamic) > 1:
            raise ValueError('multiple ELF dynamic segments are unsupported')
        tags, needed = {}, []
        if dynamic:
            offset, length = dynamic[0]
            if length % 16 or length // 16 > MAX_DYNAMIC:
                raise ValueError('ELF dynamic table is malformed or excessive')
            terminated = False
            for index in range(length // 16):
                stream.seek(offset + index * 16)
                tag, value = struct.unpack('<qQ', stream.read(16))
                if tag == 0:
                    terminated = True
                    break
                if tag == 1:
                    if len(needed) >= 1024:
                        raise ValueError('too many ELF dependencies')
                    needed.append(value)
                elif tag in (5, 10, 14, 15, 29):
                    if tag in tags:
                        raise ValueError('duplicate singleton ELF dynamic tag')
                    tags[tag] = value
            if not terminated:
                raise ValueError('ELF dynamic table has no terminator')
        strings = b''
        if tags or needed:
            if 5 not in tags or 10 not in tags or not 0 < tags[10] <= MAX_STRTAB:
                raise ValueError('ELF dynamic strings lack a bounded string table')
            matches = []
            for offset, vaddr, filesz in loads:
                if vaddr <= tags[5] and tags[5] - vaddr <= filesz and tags[10] <= filesz - (tags[5] - vaddr):
                    matches.append(offset + tags[5] - vaddr)
            if len(matches) != 1:
                raise ValueError('ELF string table must map to one file-backed load segment')
            stream.seek(matches[0])
            strings = stream.read(tags[10])

        def string(offset):
            if offset >= len(strings):
                raise ValueError('ELF dynamic string offset is outside its table')
            end = strings.find(b'\0', offset, min(len(strings), offset + MAX_STRING + 1))
            if end < 0:
                raise ValueError('ELF dynamic string is unterminated or excessive')
            try:
                value = strings[offset:end].decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('ELF dynamic string is not UTF-8') from exc
            if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError('ELF dynamic string is empty or contains controls')
            return value

        result = {'machine': machine, 'osabi': ident[7],
                  'needed': [string(value) for value in needed],
                  'soname': string(tags[14]) if 14 in tags else None,
                  'rpath': string(tags[15]).split(':') if 15 in tags else [],
                  'runpath': string(tags[29]).split(':') if 29 in tags else []}
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('ELF file changed during inspection')
    return result


def _relative(value):
    if (not isinstance(value, str) or not value or value.startswith('/')
            or re.match(r'^[A-Za-z]:', value) or '\\' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('invalid manifest-relative ELF path: ' + repr(value))
    if any(part == '..' for part in value.split('/')):
        raise ValueError('manifest-relative ELF path escapes its root: ' + repr(value))
    original = value
    value = posixpath.normpath(value.removeprefix('./'))
    if value in ('.', '..'):
        raise ValueError('invalid manifest-relative ELF path: ' + repr(original))
    return value


def _absolute(value):
    if not isinstance(value, str) or not value.startswith('/') or '\\' in value or '$' in value or any(ord(c) < 32 for c in value):
        raise ValueError('ELF search paths must be explicit absolute directories')
    return posixpath.normpath(value)


def _metadata_valid(meta, machine, path=None):
    suffix = ' for ' + repr(str(path)) if path is not None else ''
    if meta.get('machine') != machine or meta.get('osabi') not in (0, 9):
        raise ValueError('dependency ELF has an incompatible machine or OSABI' + suffix
                         + ' (machine=' + str(meta.get('machine')) + ', osabi='
                         + str(meta.get('osabi')) + ', required_machine=' + str(machine) + ')')
    if not isinstance(meta.get('needed'), list) or len(meta['needed']) > 1024:
        raise ValueError('dependency ELF metadata is malformed' + suffix)
    for field in ('rpath', 'runpath'):
        if not isinstance(meta.get(field, []), list):
            raise ValueError('dependency ELF search-path metadata is malformed' + suffix)


def base_manifest(base_root='/', architecture='arm64'):
    """Inspect only base /lib, /usr/lib (*.so*) and /libexec/ld-elf.so.1.

    The argument is the actual base root or a clean source-world stage. Paths
    in the returned records are relative to that root. Directory symlinks are
    never traversed. Leaf aliases are explicit, rather than silently followed.
    The exact FreeBSD /usr/lib/debug separate-symbol tree and .debug symbol
    files are not loader candidates; their target class/machine may differ.
    """
    if architecture not in MACHINES:
        raise ValueError('unsupported architecture')
    root = Path(base_root).absolute()
    if root.is_symlink():
        raise ValueError('base root is redirected')
    candidates = []
    for prefix in ('lib', 'usr/lib'):
        folder = root / prefix
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError('explicit base library directory is absent or redirected')
        for current, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()
                       and (Path(current) / name).relative_to(root).as_posix() != 'usr/lib/debug']
            candidates.extend(Path(current) / name for name in files
                              if '.so' in name and not name.endswith('.debug'))
    loader = root / 'libexec/ld-elf.so.1'
    if os.path.lexists(loader):
        candidates.append(loader)
    if len(candidates) > MAX_ENTRIES:
        raise ValueError('base library manifest is excessive')
    records = []
    for path in sorted(set(candidates)):
        rel = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            records.append({'path': rel, 'type': 'link', 'link': os.readlink(path)})
        elif stat.S_ISREG(info.st_mode):
            metadata = parse_elf(path)
            if metadata is not None:
                _metadata_valid(metadata, MACHINES[architecture], rel)
                records.append({'path': rel, 'type': 'file', 'bytes': info.st_size, 'sha256': _sha(path), **metadata})
    return records


collect_base_manifest = base_manifest


def verify_closure(runtime_root, entries, base_entries, architecture='arm64',
                   extra_search_paths=(), library_dirs=()):
    """Verify DT_NEEDED resolution using recorded files and loader directories.

    runtime_root contains paths such as usr/local/lib. Base records must be
    collected from the intended source world; they are data, not a host lookup.
    OSABI 0 is valid for unbranded FreeBSD shared objects; 9 is branded FreeBSD.
    This static check cannot validate dlopen-only modules or loader execution.
    """
    if architecture not in MACHINES or len(entries) + len(base_entries) > MAX_ENTRIES:
        raise ValueError('unsupported architecture or excessive manifest')
    machine = MACHINES[architecture]
    root = Path(runtime_root).absolute()
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError('runtime root is redirected')
    nodes, elfs, runtime_elfs = {}, {}, []
    for origin, records in (('runtime', entries), ('base', base_entries)):
        for item in records:
            name = _relative(item['path'])
            if item.get('type') == 'dir':
                continue
            if name in nodes:
                raise ValueError('duplicate dependency manifest path: ' + name)
            if item.get('type') not in ('file', 'link'):
                raise ValueError('unsupported dependency manifest entry')
            nodes[name] = {**item, 'origin': origin}
            if origin == 'runtime':
                if os.name == 'nt' and ':' in name:
                    raise ValueError('POSIX colon filenames require a POSIX verification filesystem: ' + repr(name))
                path = root / name
                for parent in path.parents:
                    if parent == root.parent:
                        break
                    if parent.is_symlink():
                        raise ValueError('runtime file parent is redirected')
                if item['type'] == 'link' and (not path.is_symlink() or os.readlink(path) != item.get('link')):
                    raise ValueError('runtime symlink does not match its manifest: ' + name)
            if item['type'] != 'file':
                continue
            if origin == 'runtime':
                if not re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')):
                    raise ValueError('runtime file metadata lacks a file hash: ' + name)
                if path.is_symlink() or not path.is_file():
                    raise ValueError('runtime file is absent or redirected: ' + name)
                if _sha(path) != item['sha256']:
                    raise ValueError('runtime file hash does not match its manifest: ' + name)
                metadata = parse_elf(path)
            else:
                metadata = item if 'machine' in item else item.get('elf')
                if metadata is not None and not re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')):
                    raise ValueError('base ELF metadata lacks a file hash')
            if metadata is not None:
                _metadata_valid(metadata, machine, name)
                elfs[name] = metadata
                if origin == 'runtime':
                    runtime_elfs.append(name)
    extra = list(dict.fromkeys(_absolute(value) for value in (*extra_search_paths, *library_dirs)))

    def resolve(name):
        seen = set()
        for _ in range(33):
            if name in seen:
                raise ValueError('dependency manifest contains a symlink cycle')
            seen.add(name)
            node = nodes.get(name)
            if node is None:
                return None
            if node['type'] == 'file':
                return name if name in elfs else None
            target = node.get('link')
            if not isinstance(target, str) or not target or '\\' in target or any(ord(c) < 32 for c in target):
                raise ValueError('invalid dependency symlink target')
            target = posixpath.normpath(target if target.startswith('/') else posixpath.join(posixpath.dirname(name), target))
            if target == '..' or target.startswith('../'):
                raise ValueError('dependency symlink escapes the image')
            name = _relative(target.lstrip('/'))
        raise ValueError('dependency symlink chain is excessive')

    edges, missing, checked, base_used = [], [], set(), set()
    queue = list(runtime_elfs)
    while queue:
        name = queue.pop()
        if name in checked:
            continue
        checked.add(name)
        metadata = elfs[name]
        raw_paths = metadata.get('runpath') or metadata.get('rpath') or []
        search = []
        for value in raw_paths:
            value = value.replace('${ORIGIN}', '/' + posixpath.dirname(name))
            value = re.sub(r'\$ORIGIN(?=/|$)', lambda _: '/' + posixpath.dirname(name), value)
            search.append(_absolute(value))
        search = list(dict.fromkeys(search + ['/lib', '/usr/lib', '/usr/local/lib'] + extra))
        for dependency in metadata['needed']:
            if not isinstance(dependency, str) or not dependency or '\\' in dependency or '$' in dependency or any(ord(c) < 32 for c in dependency):
                raise ValueError('invalid DT_NEEDED name')
            if '/' in dependency:
                if not dependency.startswith('/'):
                    raise ValueError('relative DT_NEEDED path depends on an unspecified working directory')
                candidates = [_absolute(dependency).lstrip('/')]
            else:
                candidates = [posixpath.join(directory.lstrip('/'), dependency) for directory in search]
            target = next((resolved for candidate in candidates if (resolved := resolve(candidate)) is not None), None)
            if target is None:
                missing.append({'from': name, 'needed': dependency, 'searched': candidates})
            else:
                origin = nodes[target]['origin']
                edges.append({'from': name, 'needed': dependency, 'to': target, 'origin': origin})
                if origin == 'base':
                    base_used.add(target)
                if target not in checked:
                    queue.append(target)
    if missing:
        example = '; '.join(item['from'] + ': ' + item['needed'] for item in missing[:12])
        raise ValueError('unresolved ELF DT_NEEDED dependencies (' + str(len(missing)) + '): ' + example)
    return {'format': 'DaOS-freebsd-elf-closure-v1', 'passed': True,
            'architecture': architecture, 'runtime_elf_count': len(runtime_elfs),
            'checked_elf_count': len(checked), 'dependency_count': len(edges),
            'extra_search_paths': extra, 'dependencies': edges,
            'base_files_used': [{'path': name, 'sha256': nodes[name]['sha256']} for name in sorted(base_used)],
            'limitations': ['static DT_NEEDED inspection; dlopen-only modules and actual loader execution require target tests']}

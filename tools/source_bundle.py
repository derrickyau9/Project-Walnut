"""Build/verify a deterministic, source-only FF-1 engineering handoff ZIP.

This is an engineering snapshot, not a license grant or hardware release.
Only known project roots are considered. Runtime data, binaries, caches and
screenshots outside immutable hardware imports are excluded. Personal workspace
prefixes in evidence copies are redacted; source/import bytes stay unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {'.github', 'camera', 'daos', 'docs', 'firmware', 'fpga', 'hardware',
               'licenses', 'mounts', 'simulation', 'system', 'tests', 'tools', 'ui'}
ROOT_FILES = {'.gitignore', 'README.md', 'HANDOFF.md', 'requirements-test.txt', 'requirements-ui.txt'}
EXCLUDED_DIRECTORIES = {'build', 'var', 'captures', '__pycache__', '.git', '.codex', '.agents', '.aws',
                        '.ssh', '.venv', 'venv', '.pytest_cache', 'node_modules', 'dist', 'target',
                        '.native-worker-build'}
RUNTIME_SUFFIXES = {'.pyc', '.pyo', '.dng', '.sqlite', '.sqlite3', '.db', '.tmp', '.exe', '.dll',
                    '.pdb', '.ilk', '.obj', '.o', '.a', '.lib', '.vvp', '.vcd', '.fst',
                    '.deb', '.so', '.elf', '.bin', '.img', '.ext2', '.ext3', '.ext4', '.qcow2',
                    '.cpio', '.initrd', '.squashfs', '.iso', '.tar'}
PRIVATE_NAMES = {'auth.json', 'credentials', 'credentials.json', 'cookies.sqlite', 'id_rsa', 'id_ed25519'}
STATE_NAMES = {'device-ui.json', '.service.lock', '.workflow.lock', '.camera.lock',
               'journal.sqlite3', 'recovery.sqlite3', 'workflow.sqlite3'}
PREFIX = 'HassleFree-FF1/'
MANIFEST = 'SOURCE-MANIFEST.json'
ZIP_TIME = (2026, 11, 5, 0, 0, 0)
EVIDENCE_SYSTEM_ROOTS = {'linux', 'arm64', 'qemu-virt', 'arm64-rootfs', 'arm64-image', 'targets', 'update-model'}
UPSTREAM_DEBIAN_METADATA = {'system/arm64-rootfs/metadata/InRelease',
                            'system/arm64-rootfs/metadata/Packages.xz'}
SOURCE_JSON_PRIVACY_BOUND = 8 * 1024**2
RUNTIME_INVENTORY_PRIVACY_BOUND = 32 * 1024**2
# These two declared inventories include the full retained-file and omission
# ledgers. Keep their larger inspection budget tied to exact portable member
# names and their declared formats, never an evidence directory or suffix.
RUNTIME_INVENTORY_JSON_FORMATS = {
    'system/freebsd/image/evidence/runtime-004/runtime.json': 'DaOS-freebsd-runtime-v2',
    'system/freebsd/image/evidence/runtime-004/offline-validation.json':
        'DaOS-freebsd-runtime-offline-validation-v1',
}
# Only this task's named workspace family is eligible; arbitrary home paths,
# similarly named siblings and embedded /var/home paths are not rewritten.
TASK_HOME_PREFIX = re.compile(
    r"(?<![\w/\\.-])(?P<uri>file://)?/home/[A-Za-z0-9_.-]{1,64}/"
    r"(?P<task>hf-task(?:-[A-Za-z0-9][A-Za-z0-9_.-]{0,127})?)"
    r"(?=$|[/\\\s\"';,:?#)\]])")
SECRET_PATTERNS = [re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
                   re.compile(rb'\bAKIA[0-9A-Z]{16}\b'),
                   re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
                   re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b'),
                   re.compile(rb'[?&]X-Amz-Signature=[a-fA-F0-9]{32,}'),
                   re.compile(rb'[?&]sig=[A-Za-z0-9%+/]{40,}')]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or ':' in name
            or any(part in {'', '.', '..'} for part in name.split('/'))
            or any(ord(c) < 32 for c in name)):
        raise ValueError('unsafe source path')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)}
    if any(part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved for part in path.parts):
        raise ValueError('source path has a nonportable Windows alias')
    return path


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def exclusion(relative):
    parts = relative.parts
    if any(part.lower() in EXCLUDED_DIRECTORIES for part in parts):
        return 'runtime/cache/build directory'
    name = parts[-1].lower()
    if name in PRIVATE_NAMES or name == '.env' or name.startswith('.env.'):
        return 'private configuration'
    # Preserve all original imported prototype bytes, including source archives,
    # editable CAD, manufacturing references and renders; they have their own ledger.
    imported = parts[:2] == ('hardware', 'imports')
    if not imported and (name in STATE_NAMES or name.startswith('device-ui-unknown-')
                         or name.endswith(('.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal',
                                           '.sqlite-wal', '.sqlite-shm', '.sqlite-journal',
                                           '.db-wal', '.db-shm', '.db-journal', '.dng.partial',
                                           '.cpio.gz', '.cpio.xz', '.tar.gz', '.tar.xz',
                                           '.img.gz', '.img.xz', '.ext4.gz', '.ext4.xz'))
                         or relative.suffix.lower() in RUNTIME_SUFFIXES
                         or re.search(r'\.so(?:\.[0-9]+)+$', name)):
        return 'runtime/generated binary'
    if not imported and relative.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
        return 'screenshot not part of source handoff'
    return None


def source_paths(root):
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.name in ROOT_FILES:
            if is_link(child) or not child.is_file():
                raise ValueError('source root file is not regular: ' + child.name)
            yield child
        elif child.name in DIRECTORIES:
            if is_link(child) or not child.is_dir():
                raise ValueError('source root directory is not regular: ' + child.name)
            for directory, directories, files in os.walk(child, followlinks=False):
                current = Path(directory)
                kept = []
                for name in sorted(directories):
                    path = current / name
                    if name.lower() in EXCLUDED_DIRECTORIES:
                        continue
                    if is_link(path):
                        raise ValueError('linked source directory refused')
                    kept.append(name)
                directories[:] = kept
                for name in sorted(files):
                    path = current / name
                    if not exclusion(path.relative_to(root)):
                        if is_link(path) or not stat.S_ISREG(path.lstat().st_mode):
                            raise ValueError('nonregular source file refused')
                        yield path


def evidence_location(relative):
    return (((len(relative.parts) >= 3 and relative.parts[0] == 'system'
              and relative.parts[1] in EVIDENCE_SYSTEM_ROOTS and relative.parts[2] == 'evidence')
             or (len(relative.parts) >= 5 and relative.parts[:2] == ('system', 'freebsd')
                 and relative.parts[2] in {'vm', 'arm-vm', 'base', 'image', 'image-boot'}
                 and relative.parts[3] == 'evidence')
             or relative.parts[:3] == ('ui', 'on-device', 'evidence-round7')
             or relative.parts[:3] == ('camera', 'native', 'evidence')
             or relative.parts[:3] == ('daos', 'capability_runtime', 'evidence')
             or relative.parts[:3] == ('docs', 'testing', 'artifacts')
             or relative.as_posix() == 'fpga/testbench/rtl-simulation-evidence.json')
            and relative.suffix.lower() in {'.json', '.txt', '.log'})


def path_prefix_pattern(prefix):
    # Match a whole absolute path prefix, including file:// URI spellings.
    # A sibling such as <root>-other is neither redacted nor called private.
    return (r"(?<![\w/\\.-])(?P<uri>file:///?)?"
            + re.escape(prefix) + r"(?=$|[/\\\s\"';,:?#)\]])")


def evidence_document(data, relative):
    large_json = relative.suffix.lower() == '.json' and len(data) > SOURCE_JSON_PRIVACY_BOUND
    inventory_format = RUNTIME_INVENTORY_JSON_FORMATS.get(relative.as_posix())
    if large_json and (inventory_format is None or len(data) > RUNTIME_INVENTORY_PRIVACY_BOUND):
        raise ValueError('source JSON exceeds privacy inspection bound')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate evidence JSON key')
            value[key] = item
        return value
    document = (json.loads(data, object_pairs_hook=unique) if relative.suffix.lower() == '.json'
                else data.decode('utf-8-sig'))
    if large_json and (not isinstance(document, dict) or document.get('format') != inventory_format
                       or document.get('architecture') != 'arm64'
                       or any(not isinstance(document.get(key), list)
                              for key in ('entries', 'omissions', 'packages', 'base_dependency_manifest'))):
        raise ValueError('oversized JSON is not the declared runtime inventory')
    return document


def privacy_document(data, relative):
    if relative.suffix.lower() == '.json':
        try:
            return evidence_document(data, relative)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ValueError('invalid or oversized source JSON for privacy inspection') from error
    return data.decode('utf-8', errors='ignore')


def has_task_home(value):
    if isinstance(value, str):
        return TASK_HOME_PREFIX.search(value) is not None
    if isinstance(value, list):
        return any(has_task_home(item) for item in value)
    if isinstance(value, dict):
        return any(has_task_home(key) or has_task_home(item) for key, item in value.items())
    return False


def evidence_copy(data, relative, root):
    """Normalize explicitly admitted evidence copies, never source/import bytes."""
    if not evidence_location(relative):
        return data, False
    document = evidence_document(data, relative)
    prefixes = [(str(root), '<SOURCE_ROOT>'), (root.as_posix(), '<SOURCE_ROOT>')]
    if root.drive:
        prefixes.append(('/mnt/' + root.drive[0].lower() + root.as_posix()[2:], '<SOURCE_ROOT>'))
    for key in ('TEMP', 'TMP'):
        value = os.environ.get(key)
        if value:
            prefixes.extend([(value, '<TEMP>'), (value.replace('\\', '/'), '<TEMP>')])

    def normalize(value):
        if isinstance(value, str):
            for before, after in sorted(set(prefixes), key=lambda item: -len(item[0])):
                value = re.sub(path_prefix_pattern(before),
                               lambda match: (match.group('uri') or '') + after, value)
            return TASK_HOME_PREFIX.sub(
                lambda match: (match.group('uri') or '') + '<TASK_RUNTIME_ROOT>/' + match.group('task'), value)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                changed_key = normalize(key)
                if changed_key in result:
                    raise ValueError('evidence key collision after personal-prefix redaction')
                result[changed_key] = normalize(item)
            return result
        return value
    changed = normalize(document)
    if changed == document:
        return data, False
    output = json.dumps(changed, indent=2, ensure_ascii=False) + '\n' if relative.suffix.lower() == '.json' else changed
    return output.encode('utf-8'), True


def write_entry(archive, name, data):
    info = zipfile.ZipInfo(PREFIX + name, date_time=ZIP_TIME)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=9)


def rights_origin(name, imports):
    if name in UPSTREAM_DEBIAN_METADATA:
        return {'origin': 'official Debian archive release/package-index metadata retained byte-for-byte; authentication recorded in system/arm64-rootfs/package-lock.json',
                'rights': 'upstream Debian archive metadata; applicable upstream terms apply; not independent project material or a blanket project license'}
    if name in {'system/arm64/notices/gcc-cross-copyright.txt','system/arm64/notices/glibc-cross-copyright.txt'}:
        return {'origin':'upstream toolchain/runtime notice; see system/arm64/notices/sources.json',
                'rights':'applicable upstream component terms retained as supplied; not a project blanket license'}
    if name in {'camera/native/third_party/sqlite/sqlite3.c', 'camera/native/third_party/sqlite/sqlite3.h'}:
        return {'origin': 'SQLite 3.54.1 official amalgamation', 'rights': 'upstream public-domain notice preserved'}
    if name in {'camera/native/third_party/nlohmann/json.hpp', 'camera/native/third_party/nlohmann/LICENSE.MIT'}:
        return {'origin': 'nlohmann/json 3.12.0 official single header and license', 'rights': 'MIT'}
    if name in imports:
        return {'origin': imports[name], 'rights': 'supplied prototype; project rightsholder clearance pending; scoped notices preserved'}
    return {'origin': 'independent FF-1 project implementation, documentation or integration overlay',
            'rights': 'project proposals pending confirmation; see licenses/INVENTORY.json'}


def create(output, root=ROOT):
    root, output = root.resolve(strict=True), output.absolute()
    output = output.parent.resolve() / output.name
    if output.is_relative_to(root):
        target = output.relative_to(root)
        if target.parts[0] in DIRECTORIES or output.name in ROOT_FILES:
            raise ValueError('bundle output must be outside included source roots')
    if output.exists():
        raise ValueError('existing source bundle refused')
    output.parent.mkdir(parents=True, exist_ok=True)
    entries, keys = [], set()
    imports = {}
    provenance = root / 'hardware/imports/provenance.json'
    if provenance.is_file():
        for source in json.loads(provenance.read_bytes()).get('sources', []):
            origin = 'Library ' + source['library_file_id'] + ' version ' + str(source['version_number'])
            imports['hardware/imports/' + source['archive_relative_path']] = origin
            for item in source['files']:
                imports['hardware/imports/' + item['path']] = origin
    personal = [str(root), root.as_posix()]
    if root.drive:
        personal.append('/mnt/' + root.drive[0].lower() + root.as_posix()[2:])
    personal.extend(json.dumps(prefix)[1:-1] for prefix in tuple(personal))
    descriptor, temporary = tempfile.mkstemp(prefix='.hf-source-', suffix='.zip', dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True) as archive:
            for path in sorted(source_paths(root), key=lambda p: p.relative_to(root).as_posix()):
                relative = path.relative_to(root)
                name = relative.as_posix()
                safe_relative(name)
                key = unicodedata.normalize('NFC', name).casefold()
                if key in keys or name == MANIFEST:
                    raise ValueError('ambiguous source member')
                keys.add(key)
                original = path.read_bytes()
                data, normalized = evidence_copy(original, relative, root)
                if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                    raise ValueError('credential-shaped content refused in ' + name)
                if any(re.search(path_prefix_pattern(prefix).encode('utf-8'), data) for prefix in personal):
                    raise ValueError('personal workspace prefix remains in ' + name)
                if relative.parts[:2] != ('hardware', 'imports') and has_task_home(
                        privacy_document(data, relative)):
                    raise ValueError('personal task-home prefix remains outside normalized evidence in ' + name)
                write_entry(archive, name, data)
                item = {'path': name, 'bytes': len(data), 'sha256': digest(data)}
                item.update(rights_origin(name, imports))
                if normalized:
                    item['source_sha256'] = digest(original)
                    item['transformation'] = 'evidence-copy personal-prefix redaction; original workspace unchanged'
                entries.append(item)
            report = {'format': 'hf-source-bundle-v1', 'snapshot_date': '2026-11-05',
                      'scope': 'private engineering handoff; simulator and unvalidated hardware prototypes',
                      'license_status': 'project proposals pending rightsholder confirmation; third-party notices retained',
                      'exclusions': ['unknown root entries', 'runtime data and captures', 'build outputs and caches',
                                     'private configuration', 'non-import screenshots'],
                      'evidence_note': 'Evidence references may identify excluded screenshots. Historical report hashes refer to original workspace artifacts; this manifest hashes actual bundled bytes and records any redacted originals.',
                      'files': entries}
            write_entry(archive, MANIFEST, (json.dumps(report, indent=2, ensure_ascii=False) + '\n').encode())
        result = verify(temporary)
        # Exclusive publication: do not replace a concurrent owner's destination.
        os.link(temporary, output)
        result.update({'path': str(output), 'archive_sha256': digest(output.read_bytes()),
                       'archive_bytes': output.stat().st_size})
        return result
    finally:
        temporary.unlink(missing_ok=True)


def verify(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if (len(infos) > 10000 or sum(item.file_size for item in infos) > 1024**3
                or any(item.file_size > 256 * 1024**2 for item in infos)):
            raise ValueError('source archive exceeds verification bounds')
        names, keys = set(), set()
        for item in infos:
            if not item.filename.startswith(PREFIX) or item.is_dir():
                raise ValueError('unexpected archive root/directory')
            name = item.filename[len(PREFIX):]
            relative = safe_relative(name)
            if name != MANIFEST:
                if not (name in ROOT_FILES or (len(relative.parts) > 1 and relative.parts[0] in DIRECTORIES)):
                    raise ValueError('archive entry is outside source roots')
                if exclusion(relative):
                    raise ValueError('archive entry violates source inclusion policy')
            key = unicodedata.normalize('NFC', name).casefold()
            if key in keys or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('duplicate or linked archive entry')
            names.add(name)
            keys.add(key)
        if MANIFEST not in names:
            raise ValueError('source manifest missing')
        if archive.getinfo(PREFIX + MANIFEST).file_size > 5 * 1024**2:
            raise ValueError('source manifest exceeds verification bound')
        document = json.loads(archive.read(PREFIX + MANIFEST))
        if document.get('format') != 'hf-source-bundle-v1' or not isinstance(document.get('files'), list):
            raise ValueError('invalid source manifest')
        listed = set()
        for item in document['files']:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise ValueError('invalid source manifest entry')
            name = item['path']
            if name in listed or name == MANIFEST or name not in names:
                raise ValueError('ambiguous source manifest entry')
            listed.add(name)
            data = archive.read(PREFIX + name)
            if len(data) != item.get('bytes') or digest(data) != item.get('sha256'):
                raise ValueError('source checksum mismatch')
            if not all(isinstance(item.get(key), str) and item[key] for key in ('origin', 'rights')):
                raise ValueError('source origin/rights inventory missing')
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError('credential-shaped content in source archive')
            relative = safe_relative(name)
            if relative.parts[:2] != ('hardware', 'imports') and has_task_home(
                    privacy_document(data, relative)):
                raise ValueError('personal task-home prefix in source archive')
        if names != listed | {MANIFEST}:
            raise ValueError('unlisted source archive entries')
        return {'verified': True, 'files': len(listed), 'uncompressed_bytes': sum(item['bytes'] for item in document['files']),
                'redacted_evidence_copies': sum('source_sha256' in item for item in document['files'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--output', type=Path)
    action.add_argument('--verify', type=Path)
    args = parser.parse_args()
    print(json.dumps(create(args.output) if args.output else verify(args.verify), indent=2))


if __name__ == '__main__':
    main()

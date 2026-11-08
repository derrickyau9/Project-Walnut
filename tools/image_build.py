"""Build new immutable-root/separate-data ARM64 guest disk images without mounts.

Requires an existing non-root Linux host, authenticated staged Debian ARM64
userspace, tested static ARM64 camera binaries and the pinned kernel cache.
No packages are installed and no host device, mount or network is used.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import lzma
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
EPOCH = 1700000000
ROOT_UUID = 'a7c41d22-6607-4cd6-9000-000000000001'
DATA_UUID = 'a7c41d22-6607-4cd6-9000-000000000002'
# ext4 requests the crypto CRC32C driver at mount time; modinfo's dependency
# list alone does not capture that runtime request in the minimal initramfs.
MODULE_NAMES = ('crc16', 'crc32c_generic', 'mbcache', 'jbd2', 'ext4', 'virtio_blk')
NATIVE_NAMES = ('hf-core-tests', 'hf-daemon-tests', 'hf-native-worker', 'hf-camerad')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write('\n')


def safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith('/') or '\\' in name or
            any(ord(c) < 32 or ord(c) == 127 for c in name) or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('unsafe image member path')
    return name


def regular(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError('regular non-linked input required: ' + str(path))
    return path


def arm_elf(path, *, static=False):
    path = regular(path)
    with path.open('rb') as source:
        data = source.read(64)
        if (len(data) != 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183):
            raise ValueError('AArch64 ELF64 input required: ' + str(path))
    if static:
        from tools.arm64_elf import inspect_arm64_elf
        inspect_arm64_elf(path, require_static=True)


def new_destination(path, root=ROOT):
    root = Path(root).resolve()
    path = Path(os.path.abspath(os.fspath(path)))
    build_root = root / 'build'
    build_root.mkdir(exist_ok=True)
    if build_root.is_symlink() or build_root.resolve() != build_root:
        raise ValueError('image build root must be an ordinary workspace build directory')
    if os.path.lexists(path) or not path.is_relative_to(build_root):
        raise ValueError('image build destination must be NEW beneath workspace build')
    parent = path.parent.resolve(strict=True)
    if parent != path.parent or not parent.is_relative_to(build_root):
        raise ValueError('image build parent must be canonical beneath build')
    return path


def app_entries(root, native_dir):
    root, native_dir = Path(root).resolve(), Path(native_dir).resolve()
    paths = [path for directory in ('camera', 'simulation') for path in (root / directory).rglob('*.py')]
    paths += [root / 'ui/on-device/controller.py', root / 'ui/on-device/Main.qml', root / 'ui/on-device/main.py',
              root / 'tests/ui/arm64_smoke.py', root / 'tests/ui/offline_smoke.py']
    paths += list((root / 'daos/capability_runtime').glob('*.py'))
    paths += [path for directory in ('daos/console', 'daos/camera-contracts')
              for path in (root / directory).rglob('*') if path.is_file() and path.suffix in ('.py', '.json', '.html', '.js', '.css', '.md')]
    result, hashes = [], {}
    for path in sorted(set(paths)):
        regular(path)
        relative = path.relative_to(root).as_posix()
        result.append({'path': 'opt/hasslefree/' + relative, 'type': 'file', 'mode': 0o644, 'source': path})
        hashes[relative] = sha(path)
    for name in NATIVE_NAMES:
        path = native_dir / name
        arm_elf(path, static=True)
        result.append({'path': 'opt/hasslefree/bin/' + name, 'type': 'file', 'mode': 0o755, 'source': path})
        hashes['native/' + name] = sha(path)
    return result, hashes


def write_tar(path, entries):
    """Write sorted root-owned fixed-time members; never extract to the host."""
    members = {}
    for raw in entries:
        entry = dict(raw)
        name = safe_name(entry['path'])
        if name in members:
            raise ValueError('duplicate image member: ' + name)
        members[name] = entry
    for name in tuple(members):
        for parent in PurePosixPath(name).parents:
            value = parent.as_posix()
            if value == '.':
                break
            if value not in members:
                members[value] = {'path': value, 'type': 'directory', 'mode': 0o755}
            elif members[value]['type'] != 'directory':
                raise ValueError('image member would traverse a link or file: ' + name)
    byte_count = 0
    with Path(path).open('xb') as output, tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, entry in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = EPOCH
            mode = entry['mode']
            info.mode = (int(mode, 8) if isinstance(mode, str) else mode) & 0o7777
            kind = entry['type']
            if kind == 'directory':
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == 'symlink':
                info.type, info.linkname = tarfile.SYMTYPE, entry['target']
                archive.addfile(info)
            elif kind == 'file':
                source = regular(entry['source'])
                info.size = source.stat().st_size
                byte_count += info.size
                if byte_count > 1536 * 1024**2:
                    raise ValueError('rootfs file data exceeds image build budget')
                with source.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                raise ValueError('unsupported image member type')
    return {'members': len(members), 'file_bytes': byte_count, 'sha256': sha(path)}


def newc(entries):
    output = bytearray()
    seen = set()
    kinds = {}
    for index, (name, data, mode, major, minor) in enumerate(sorted(entries), 1):
        safe_name(name)
        if type(mode) is not int or not 0 <= mode <= 0xffff:
            raise ValueError('invalid bootstrap file mode')
        kind = stat.S_IFMT(mode)
        if (name in seen or name == 'TRAILER!!!' or kind not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFCHR) or
                not isinstance(data, bytes) or kind != stat.S_IFREG and data or
                type(major) is not int or type(minor) is not int or not 0 <= major <= 4095 or not 0 <= minor <= 4095):
            raise ValueError('invalid bootstrap entry type, device, or name')
        for parent in PurePosixPath(name).parents:
            value = parent.as_posix()
            if value != '.' and value in kinds and kinds[value] != stat.S_IFDIR:
                raise ValueError('bootstrap entry would traverse a non-directory parent')
        seen.add(name)
        kinds[name] = kind
        if len(output) + len(data) > 128 * 1024**2:
            raise ValueError('bootstrap archive exceeds bound')
        encoded = name.encode('utf-8') + b'\0'
        fields = [index, mode, 0, 0, 1, EPOCH, len(data), 0, 0, major, minor, len(encoded), 0]
        output += b'070701' + b''.join(f'{item:08x}'.encode() for item in fields) + encoded
        output += b'\0' * (-len(output) % 4)
        output += data
        output += b'\0' * (-len(output) % 4)
    encoded = b'TRAILER!!!\0'
    fields = [len(seen) + 1, 0, 0, 0, 1, EPOCH, 0, 0, 0, 0, 0, len(encoded), 0]
    output += b'070701' + b''.join(f'{item:08x}'.encode() for item in fields) + encoded
    output += b'\0' * (-len(output) % 4)
    return bytes(output)


def verified_kernel(kernel_dir, destination):
    """Read selected modules from the checksum-verified DEB, not a mutable cache tree."""
    kernel_dir = Path(kernel_dir).resolve()
    manifest_path = regular(kernel_dir / 'kernel-manifest.json')
    manifest = json.loads(manifest_path.read_text())
    package = regular(kernel_dir / 'kernel.deb')
    image = regular(kernel_dir / manifest['kernel_relative_path'])
    if (manifest.get('release_signature_verified') is not True or sha(package) != manifest.get('sha256') or
            sha(image) != manifest.get('kernel_sha256')):
        raise ValueError('verified pinned kernel package and image are required')
    with (destination / 'kernel.img').open('xb') as output, image.open('rb') as source:
        shutil.copyfileobj(source, output, 1024 * 1024)
    wanted = {name + '.ko.xz': name for name in MODULE_NAMES}
    modules = {}
    process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                basename = PurePosixPath(member.name).name
                if basename not in wanted:
                    continue
                name = wanted[basename]
                if name in modules or not member.isfile() or member.size > 16 * 1024**2:
                    raise ValueError('ambiguous or oversized kernel module')
                compressed = archive.extractfile(member).read(16 * 1024**2 + 1)
                unpacker = lzma.LZMADecompressor()
                data = unpacker.decompress(compressed, max_length=32 * 1024**2)
                if not unpacker.eof or len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
                    raise ValueError('bounded ARM64 kernel module required')
                modules[name] = {'data': data, 'archive_path': member.name,
                    'compressed_sha256': hashlib.sha256(compressed).hexdigest(), 'sha256': hashlib.sha256(data).hexdigest()}
        if len(process.stdout.read(1024 * 1024 + 1)) > 1024 * 1024:
            raise ValueError('kernel tar trailing data exceeds bound')
        error = process.stderr.read(65537)
        if process.wait(timeout=30) or len(error) > 65536 or set(modules) != set(MODULE_NAMES):
            raise RuntimeError('verified kernel module extraction did not complete')
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        process.stdout.close(); process.stderr.close()
    return manifest, modules


def build(rootfs, rootfs_manifest, kernel_dir, native_dir, build_dir):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('image build requires an existing non-root Linux host')
    from tools.rootfs_build import verify_stage
    from tools.arm64_build import source_inputs
    from tools.image_fs import create_ext4
    rootfs = Path(rootfs).resolve(strict=True)
    rootfs_manifest = regular(rootfs_manifest).resolve()
    verify_stage(rootfs, rootfs_manifest)
    stage = json.loads(rootfs_manifest.read_text())
    if (stage.get('format') != 'hf-arm64-qt-rootfs-v1' or
            stage.get('architecture', stage.get('arch')) != 'arm64' or
            stage.get('authentication', {}).get('signature_verified') is not True or stage.get('no_postinst') is not True):
        raise ValueError('authenticated script-free ARM64 Qt rootfs staging required')
    native_dir = Path(native_dir).resolve(strict=True)
    native_manifest = regular(native_dir / 'arm64-build-evidence.json')
    evidence = json.loads(native_manifest.read_text())
    if (evidence.get('format') != 'hf-arm64-cross-build-v1' or evidence.get('tests_passed') is not True or
            evidence.get('native_input_sha256') != source_inputs(ROOT)):
        raise ValueError('matching actual tested ARM64 native source evidence required')
    overlay, inputs = app_entries(ROOT, native_dir)
    for name in NATIVE_NAMES:
        if evidence.get('artifacts', {}).get(name, {}).get('sha256') != inputs['native/' + name]:
            raise ValueError('native binary differs from tested ARM64 evidence')
    destination = new_destination(build_dir)
    destination.mkdir(mode=0o700)
    members = {}
    for item in stage['entries']:
        entry = dict(item)
        name = safe_name(entry['path'])
        if entry['type'] == 'file':
            entry['source'] = rootfs / name
            if not entry['source'].resolve().is_relative_to(rootfs):
                raise ValueError('staged file escapes source root')
        members[name] = entry
    # These are empty mount points in the immutable image, not host mounts.
    for name, mode in (('dev', 0o755), ('proc', 0o755), ('sys', 0o755), ('run', 0o755),
                       ('tmp', 0o1777), ('data', 0o755), ('opt', 0o755)):
        if name in members and members[name]['type'] != 'directory':
            raise ValueError('runtime mount point is not a directory')
        members[name] = {'path': name, 'type': 'directory', 'mode': mode}
    for entry in overlay:
        if entry['path'] in members: raise ValueError('application overlay would replace a base file')
        members[entry['path']] = entry
    tar_path = destination / 'rootfs.tar'
    tar_info = write_tar(tar_path, members.values())
    size_mib = max(512, math.ceil((tar_info['file_bytes'] * 1.2 + 64 * 1024**2) / (64 * 1024**2)) * 64)
    root_image = create_ext4(tar_path, destination / 'rootfs.ext4', size_mib=size_mib,
                             label='HF_R7_ROOT', uuid=ROOT_UUID, epoch=EPOCH)
    empty_tar = destination / 'data.tar'
    write_tar(empty_tar, [])
    data_image = create_ext4(empty_tar, destination / 'data.ext4', size_mib=256,
                             label='HF_R7_DATA', uuid=DATA_UUID, epoch=EPOCH)
    kernel, modules = verified_kernel(kernel_dir, destination)
    compiler = shutil.which('aarch64-linux-gnu-gcc')
    if not compiler: raise RuntimeError('installed AArch64 static C compiler required')
    init_source = ROOT / 'system/arm64-image/init.c'
    init_before = sha(init_source)
    command = [compiler, '-std=c11', '-O2', '-static', '-Wall', '-Wextra', '-Werror',
               str(init_source), '-o', str(destination / 'init')]
    subprocess.run(command, check=True, timeout=60)
    arm_elf(destination / 'init', static=True)
    entries = [(name, b'', stat.S_IFDIR | 0o755, 0, 0) for name in ('dev', 'proc', 'sys', 'newroot', 'modules')]
    entries += [('dev/console', b'', stat.S_IFCHR | 0o600, 5, 1), ('dev/null', b'', stat.S_IFCHR | 0o666, 1, 3),
                ('init', (destination / 'init').read_bytes(), stat.S_IFREG | 0o755, 0, 0),
                ('modules/load-order', ('\n'.join(MODULE_NAMES) + '\n').encode(), stat.S_IFREG | 0o644, 0, 0)]
    entries += [('modules/' + name + '.ko', modules[name]['data'], stat.S_IFREG | 0o600, 0, 0) for name in MODULE_NAMES]
    packed = gzip.compress(newc(entries), mtime=0)
    with (destination / 'bootstrap.cpio.gz').open('xb') as output: output.write(packed)
    verify_stage(rootfs, rootfs_manifest)
    if inputs != app_entries(ROOT, native_dir)[1] or init_before != sha(init_source):
        raise ValueError('image inputs changed during build')
    input_hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in
                   [init_source, ROOT / 'tools/image_build.py', ROOT / 'tools/image_fs.py']}
    report = {'format': 'hf-arm64-image-build-v1', 'scope': 'generic virtual ARM64, synthetic camera only',
        'rootfs_manifest_sha256': sha(rootfs_manifest), 'rootfs_tree_sha256': stage['tree_sha256'],
        'package_lock_sha256': stage['package_lock_sha256'], 'authentication': stage['authentication'],
        'native_build_evidence_sha256': sha(native_manifest), 'application_input_sha256': inputs,
        'image_input_sha256': input_hashes, 'rootfs_image': root_image, 'data_image': data_image,
        'rootfs_tar': tar_info, 'kernel': kernel, 'kernel_sha256': sha(destination / 'kernel.img'),
        'modules': {name: {key: value for key, value in modules[name].items() if key != 'data'} for name in MODULE_NAMES},
        'bootstrap_sha256': hashlib.sha256(packed).hexdigest(), 'init_sha256': sha(destination / 'init'),
        'compile_command': command, 'phase_commands': {'first': 'python3 -B /opt/hasslefree/tests/ui/arm64_smoke.py --phase first',
                                                     'resume': 'same command with --phase resume and the same /data image'},
        'storage': {'root': 'rootfs.ext4 read-only', 'data': 'data.ext4 read-write, mounted /data',
                    'host_mounts': False, 'network': False, 'host_filesystem_sharing': False},
        'uid': 1000, 'epoch': EPOCH}
    write_json(destination / 'build-evidence.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    item = sub.add_parser('build')
    for name in ('rootfs', 'rootfs-manifest', 'kernel-dir', 'native-dir', 'build-dir'):
        item.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = build(args.rootfs, args.rootfs_manifest, args.kernel_dir, args.native_dir, args.build_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    if __package__ in (None, ''):
        sys.path.insert(0, str(ROOT))
    main()

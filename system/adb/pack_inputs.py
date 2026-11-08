#!/usr/bin/env python3
"""Snapshot local project source and public-only USB image inputs to a new tar."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SKIP = {'__pycache__', '.git', '.venv', 'node_modules', '.ssh', '.pytest_cache',
        'build', 'dist', 'upstream', 'references'}
SUFFIXES = {'.py', '.json', '.qml', '.svg', '.c', '.h', '.cpp', '.hpp', '.md', '.txt',
            '.sh', '.conf', '.html', '.css', '.js', '.png', '.jpg', '.pub', '.dts', '.dtsi'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiled', type=Path, required=True)
    parser.add_argument('--adb-public-key', type=Path, action='append', default=[],
                        help='Public key to authorize; repeat for multiple hosts. Defaults to the project development public key.')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('A new output archive is required')
    sources = {}
    for folder in ('camera', 'daos', 'simulation', 'tools', 'ui', 'system/freebsd', 'system/adb', 'assets/boot-logo', 'licenses'):
        for path in (ROOT / folder).rglob('*'):
            rel = path.relative_to(ROOT)
            if (not path.is_file() or path.is_symlink() or SKIP.intersection(rel.parts)
                    or any(part.startswith('evidence') for part in rel.parts)):
                continue
            if path.suffix in ('.pyc', '.pyo'):
                continue
            if path.name == 'this-pc-adbkey.pub':
                continue  # A workstation authorization is a local build input.
            legal = any(word in path.name.lower() for word in ('license', 'licence', 'copying', 'notice', 'copyright'))
            if path.suffix in SUFFIXES or legal or path.name in ('Makefile', 'DaOS', 'DaOS-camera') or 'overlay' in rel.parts or 'files' in rel.parts:
                sources['project/' + rel.as_posix()] = path
    for name in ('README.md', 'docs/design-provenance.md'):
        sources['project/' + name] = ROOT / name
    for name in ('camera/native/third_party/nlohmann/LICENSE.MIT', 'camera/native/third_party/manifest.json',
                 'camera/native/third_party/sqlite/sqlite3.h', 'licenses/INVENTORY.json', 'licenses/README.md'):
        if 'project/' + name not in sources:
            raise ValueError('Required source/notice omitted from the snapshot: ' + name)
    for name in ('daos-usb-adb', 'daos_adb_template.ko', 'build-evidence.json'):
        sources['compiled/' + name] = args.compiled / name
    key_paths = args.adb_public_key or [HERE / 'daemon/keys/daos-development-adbkey.pub']
    if any(path.is_symlink() or not path.is_file() for path in key_paths):
        raise ValueError('Regular public ADB key inputs are required')
    keys = b''.join(path.read_bytes().replace(b'\0', b'').strip() + b'\n' for path in key_paths)
    if b'PRIVATE KEY' in keys:
        raise ValueError('Only public ADB key material may enter the image')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    key_file = args.output.with_suffix(args.output.suffix + '.public-keys')
    with key_file.open('xb') as output:
        output.write(keys)
    sources['authorized-adb-keys.pub'] = key_file
    records = {}
    with tarfile.open(args.output, 'x:gz', compresslevel=3) as archive:
        for name, source in sorted(sources.items()):
            if source.is_symlink() or not source.is_file():
                raise ValueError('Regular snapshot input required: ' + str(source))
            records[name] = {'sha256': sha(source), 'bytes': source.stat().st_size}
            archive.add(source, arcname=name, recursive=False)
    receipt = {'format': 'DaOS-USB-image-input-snapshot-v1', 'archive': str(args.output),
               'archive_sha256': sha(args.output), 'files': records, 'private_keys_staged': False}
    with args.output.with_suffix(args.output.suffix + '.json').open('x') as output:
        json.dump(receipt, output, indent=2)
        output.write('\n')
    print(json.dumps({'archive': str(args.output), 'sha256': receipt['archive_sha256'],
                      'files': len(records), 'bytes': args.output.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()

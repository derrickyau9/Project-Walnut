#!/usr/bin/env python3
"""Extract a hash-bound source snapshot and tested native tools in a new guest tree."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def extract(archive, destination, allowed_prefixes, count_limit, size_limit):
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        if len(members) > count_limit or sum(m.size for m in members) > size_limit:
            raise ValueError('Snapshot exceeds its explicit bounds')
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or not path.parts or (allowed_prefixes and path.parts[0] not in allowed_prefixes)):
                raise ValueError('Unexpected snapshot member: ' + member.name)
        stream.extractall(destination, filter='data')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--native-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sha(args.archive) != args.sha256:
        raise ValueError('Input snapshot differs from its recorded SHA256')
    output = args.output.absolute()
    if output.exists() or output.parent != Path('/var') or not output.name.startswith('DaOS-usb-image-input-'):
        raise ValueError('Use a new private /var/DaOS-usb-image-input-* directory')
    output.mkdir(mode=0o700)
    extract(args.archive, output, {'project', 'compiled', 'authorized-adb-keys.pub'}, 1000, 64 * 1024**2)
    native = output / 'native'
    native.mkdir()
    extract(args.native_archive, native, None, 16, 128 * 1024**2)
    result = {'format': 'DaOS-USB-source-snapshot-extract-v1', 'output': str(output),
              'source_archive_sha256': sha(args.archive), 'native_archive_sha256': sha(args.native_archive),
              'native_binary_validation': 'Required separately by assembler against target execution evidence'}
    (output / 'extraction.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

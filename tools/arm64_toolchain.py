"""Prepare isolated ARM64 C++ headers/static library for installed cross tools.

Downloads only a pinned official Debian package, checks its exact SHA-256 and
extracts files with dpkg-deb. No apt install, package script or service is run.
The installed Clang, cross GCC C/sysroot, GNU linker and QEMU are used read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare(destination, lock):
    if sys.platform != 'linux':
        raise RuntimeError('ARM64 cross tools must be prepared in an actual Linux process')
    destination, lock = Path(destination).resolve(), Path(lock).resolve()
    configuration = json.loads(lock.read_text(encoding='utf-8'))
    if configuration.get('format') != 'hf-arm64-extra-toolchain-v1' or len(configuration.get('packages', [])) != 1:
        raise ValueError('unsupported ARM64 toolchain lock')
    package = configuration['packages'][0]
    if (package.get('name') != 'libstdc++-14-dev-arm64-cross' or
            not package.get('source', '').startswith('https://deb.debian.org/debian/pool/main/g/gcc-14-cross/') or
            len(package.get('sha256', '')) != 64 or type(package.get('bytes')) is not int or
            not 0 < package['bytes'] <= 16 * 1024 * 1024):
        raise ValueError('invalid ARM64 tool package declaration')
    marker = destination / 'arm64-tools.json'
    if destination.exists() and any(destination.iterdir()) and not marker.is_file():
        raise ValueError('refusing an existing directory not marked as isolated ARM64 tools')
    destination.mkdir(parents=True, exist_ok=True)
    # A marker makes interrupted downloads/extractions safe to retry in this
    # explicitly selected task directory, without replacing unrelated files.
    if not marker.exists():
        marker.write_text(json.dumps({'format': configuration['format'], 'status': 'preparing'}) + '\n')
    archive = destination / 'libstdc++-14-dev-arm64-cross.deb'
    if not archive.exists():
        payload = urllib.request.urlopen(package['source'], timeout=60).read(package['bytes'] + 1)
        if len(payload) != package['bytes'] or hashlib.sha256(payload).hexdigest() != package['sha256']:
            raise ValueError('official Debian cross-C++ package length/hash mismatch')
        with archive.open('xb') as stream:
            stream.write(payload)
    if archive.stat().st_size != package['bytes'] or sha256(archive) != package['sha256']:
        raise ValueError('isolated cross-C++ archive differs from its lock')
    dpkg = shutil.which('dpkg-deb')
    if not dpkg:
        raise RuntimeError('dpkg-deb is required for script-free isolated extraction')
    extra = destination / 'sysroot-extra'
    subprocess.run([dpkg, '-x', str(archive), str(extra)], check=True)
    headers = extra / 'usr/aarch64-linux-gnu/include/c++/14'
    library = extra / 'usr/lib/gcc-cross/aarch64-linux-gnu/14/libstdc++.a'
    if not (headers / 'vector').is_file() or not (headers / 'aarch64-linux-gnu/bits/c++config.h').is_file() or not library.is_file():
        raise RuntimeError('extracted cross-C++ package has an unexpected layout')
    evidence = {'format': configuration['format'], 'status': 'ready', 'package': package,
                'lock_sha256': sha256(lock), 'archive_sha256': sha256(archive),
                'headers': str(headers), 'library': str(library), 'library_sha256': sha256(library),
                'system_packages_installed': False,
                'limits': 'Supplement to already installed Debian cross GCC C/sysroot, Clang and QEMU; not a board SDK.'}
    marker.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-dir', type=Path, default=Path('build/arm64-tools'))
    parser.add_argument('--lock', type=Path, default=Path(__file__).resolve().parents[1] / 'system/arm64/toolchain-lock.json')
    args = parser.parse_args()
    print(json.dumps(prepare(args.tools_dir, args.lock), indent=2))


if __name__ == '__main__':
    main()

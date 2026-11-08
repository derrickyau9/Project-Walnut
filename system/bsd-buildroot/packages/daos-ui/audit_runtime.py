#!/usr/bin/env python3
"""Audit downloaded archive payloads without running target binaries."""
import argparse
import json
from pathlib import Path
import tarfile
import tempfile
import install

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cache', type=Path, required=True)
parser.add_argument('--receipt', type=Path, required=True)
args = parser.parse_args()
lock = install.read_lock(install.HERE / 'runtime.lock.json')
files, errors, duplicates, elf_count, scripts, links = set(), [], [], 0, 0, 0
with tempfile.TemporaryDirectory(prefix='daos-ui-archive-audit-') as tmp:
    scratch = Path(tmp) / 'elf'
    for row in lock['packages']:
        archive = args.cache / row['file']
        if install.digest(archive, 'sha512') != row['sha512']: raise ValueError('Corrupt package')
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            info = tar.extractfile('+BUILD_INFO').read().decode()
            for key, value in row['abi'].items():
                if key + '=' + value not in info.splitlines(): raise ValueError('Archive ABI mismatch')
            scripts += any(m.name == '+INSTALL' for m in members)
            for member in members:
                name = install.canonical(member.name)
                if name.startswith('+') or name == '.' or member.isdir(): continue
                full = 'usr/pkg/' + name
                if full in files: duplicates.append(full)
                files.add(full)
                if member.issym() or member.islnk():
                    links += 1; continue
                if not member.isfile(): errors.append(full + ': unexpected special file'); continue
                with tar.extractfile(member) as source:
                    head = source.read(64)
                    if not head.startswith(b'\x7fELF'): continue
                    elf_count += 1
                    scratch.write_bytes(head + source.read())
                try: install.elf_check(scratch, allow_relocatable=True)
                except ValueError as error: errors.append(full + ': ' + str(error))
        print('audited ' + row['name'], flush=True)
result = {'format': 'DaOS-NetBSD-ui-archive-audit-v1', 'package_count': len(lock['packages']),
          'file_count': len(files), 'ELF_count': elf_count, 'link_count': links,
          'packages_with_install_scripts': scripts, 'duplicate_payload_paths': duplicates,
          'errors': errors, 'target_executables_run': False, 'target_boot_verified': False,
          'runtime_lock_sha256': install.digest(install.HERE / 'runtime.lock.json')}
install.save(args.receipt, result)
print(json.dumps(result, indent=2))
if errors or duplicates: raise SystemExit(1)

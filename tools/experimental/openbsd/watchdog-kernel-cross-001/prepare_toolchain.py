"""Isolated Debian LLVM extraction; no dpkg/apt installation or config writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

OUT = Path(__file__).resolve().parent
WORK = Path('/home/dy09/daos-openbsd-cross-001')
PACKAGES = ('clang-22', 'libclang-cpp22', 'libllvm22',
            'libclang-common-22-dev', 'llvm-22-linker-tools',
            'libclang1-22', 'lld-22', 'llvm-22', 'llvm-22-runtime')
VERSION = '1:22.1.8-1~deb13u4'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1 << 20), b''):
            h.update(data)
    return h.hexdigest()


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs).stdout


def main():
    OUT.mkdir(exist_ok=True)
    debs = WORK / 'debs'
    root = WORK / 'toolroot'
    debs.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=True)
    lists = Path('/var/lib/apt/lists')
    release = lists / 'deb.debian.org_debian_dists_trixie_InRelease'
    index = lists / 'deb.debian.org_debian_dists_trixie_main_binary-amd64_Packages'
    signature = run(['gpgv', '--keyring', '/usr/share/keyrings/debian-archive-keyring.gpg', str(release)])
    (OUT / 'InRelease.gpgv.log').write_text(signature)
    shutil.copyfile(release, OUT / 'InRelease')
    signed = release.read_text()
    expected_line = next(line for line in signed.splitlines()
                         if line.endswith(' main/binary-amd64/Packages')
                         and len(line.split()[0]) == 64)
    expected_hash, expected_bytes, _ = expected_line.split()
    assert sha(index) == expected_hash and index.stat().st_size == int(expected_bytes)
    records = {}
    for paragraph in index.read_text().split('\n\n'):
        lines = paragraph.splitlines()
        if not lines or not lines[0].startswith('Package: '):
            continue
        name = lines[0].split(': ', 1)[1]
        if name not in PACKAGES:
            continue
        fields = dict(line.split(': ', 1) for line in lines if ': ' in line and not line.startswith(' '))
        if fields.get('Version') == VERSION:
            assert fields['Architecture'] == 'amd64'
            records[name] = fields
    assert set(records) == set(PACKAGES)
    (OUT / 'signed-package-records.json').write_text(json.dumps(records, indent=2) + '\n')
    downloads = []
    for name in PACKAGES:
        record = records[name]
        dest = debs / Path(record['Filename']).name
        if not dest.exists():
            downloads.append(run(['apt-get', 'download', name + '=' + VERSION], cwd=debs))
            downloaded = [p for p in debs.glob(name + '_*.deb')
                          if sha(p) == record['SHA256']]
            assert len(downloaded) == 1
            downloaded[0].rename(dest)
        assert sha(dest) == record['SHA256']
        assert dest.stat().st_size == int(record['Size'])
        run(['dpkg-deb', '-x', str(dest), str(root)])
    (OUT / 'download.log').write_text('\n'.join(downloads))
    env = dict(os.environ, LD_LIBRARY_PATH=str(root / 'usr/lib/x86_64-linux-gnu'))
    version = run([str(root / 'usr/bin/clang-22'), '--version'], env=env)
    ldd = run(['ldd', str(root / 'usr/lib/llvm-22/bin/clang')], env=env)
    assert 'not found' not in ldd
    (OUT / 'toolchain-version.txt').write_text(version)
    (OUT / 'toolchain-ldd.txt').write_text(ldd)
    receipt = {
        'status': 'EXTRACTED_ONLY_NO_SYSTEM_INSTALL',
        'work': str(WORK), 'version': VERSION,
        'native_version': 'OpenBSD clang 22.1.6',
        'compiler_version_delta': 'Debian LLVM 22.1.8 versus native OpenBSD LLVM 22.1.6; not binary-identical toolchains',
        'inrelease_sha256': sha(release),
        'index_signed_sha256': expected_hash,
        'index_bytes': int(expected_bytes),
        'public_keyring_sha256': sha(Path('/usr/share/keyrings/debian-archive-keyring.gpg')),
        'packages': {name: {'sha256': records[name]['SHA256'], 'bytes': int(records[name]['Size'])}
                     for name in PACKAGES},
        'commands': ['gpgv InRelease', 'SHA256 complete signed Packages index',
                     'apt-get download exact-version', 'dpkg-deb -x private-toolroot'],
    }
    (OUT / 'toolchain-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

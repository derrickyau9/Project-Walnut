"""Fetch hash-checked MSYS2 UCRT Icarus runtime into an explicit isolated folder.

No package manager, system PATH edit, installer or package script is executed.
Requires the optional zstandard Python module. Tool binaries are not vendored
into the camera source repository. Metadata URLs and exact bytes are recorded.
"""
import argparse
import hashlib
import html
import io
import json
from pathlib import Path
import re
import tarfile
import urllib.request


PREFIX = 'mingw-w64-ucrt-x86_64-'
SITE = 'https://packages.msys2.org/packages/'


def fetch(destination, lock=None):
    import zstandard
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    locked = None
    if lock is not None:
        entries = json.loads(Path(lock).read_text(encoding='utf-8'))['packages']
        locked = {entry['name']: entry for entry in entries}
        if len(locked) != len(entries):
            raise ValueError('duplicate package in toolchain lock')
    pending = list(locked) if locked is not None else [PREFIX + 'iverilog', PREFIX + 'gcc-libs', PREFIX + 'libwinpthread']
    seen, records, total = set(), [], 0
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        if not re.fullmatch(r'mingw-w64-ucrt-x86_64-[A-Za-z0-9_+.-]+', name) or len(seen) >= 40:
            raise ValueError('unexpected dependency graph')
        seen.add(name)
        page_url = SITE + name
        if locked is not None:
            url, expected = locked[name]['archive_source'], locked[name]['sha256']
        else:
            page = urllib.request.urlopen(page_url, timeout=45).read(2 * 1024 * 1024).decode('utf-8')
            url = html.unescape(re.search(r'File:</dt>\s*<dd[^>]*>\s*<a href="([^"]+\.pkg\.tar\.zst)"', page).group(1))
            expected = re.search(r'SHA256:</dt>\s*<dd[^>]*><code>([0-9a-f]{64})</code>', page).group(1)
            section = page.split('Dependencies:</dt>', 1)[1].split('</dd>', 1)[0]
            pending.extend(re.findall(r'https://packages\.msys2\.org/packages/(mingw-w64-ucrt-x86_64-[A-Za-z0-9_+.-]+)', section))
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('invalid tool archive digest')
        if not url.startswith('https://mirror.msys2.org/mingw/ucrt64/'):
            raise ValueError('unexpected binary source')
        payload = urllib.request.urlopen(url, timeout=60).read(64 * 1024 * 1024 + 1)
        total += len(payload)
        if len(payload) > 64 * 1024 * 1024 or total > 180 * 1024 * 1024:
            raise ValueError('tool download exceeds budget')
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('MSYS2 archive checksum mismatch')
        extracted = []
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as decompressed:
            with tarfile.open(fileobj=decompressed, mode='r|') as archive:
                for member in archive:
                    relative = Path(member.name)
                    allowed = any(member.name.startswith(part) for part in
                                  ('ucrt64/bin/', 'ucrt64/lib/ivl/', 'ucrt64/share/licenses/'))
                    if not allowed:
                        continue
                    target = (destination / relative).resolve()
                    if not target.is_relative_to(destination) or member.issym() or member.islnk():
                        raise ValueError('unsafe tool archive entry')
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile() or member.size > 64 * 1024 * 1024:
                        raise ValueError('unsupported tool archive entry')
                    content = archive.extractfile(member).read()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and target.read_bytes() != content:
                        raise ValueError('tool file conflicts with existing destination')
                    target.write_bytes(content)
                    extracted.append({'path': member.name, 'sha256': hashlib.sha256(content).hexdigest()})
        records.append({'name': name, 'metadata_source': page_url, 'archive_source': url,
                        'sha256': expected, 'bytes': len(payload), 'files': extracted})
        print('Verified ' + name + ' (' + str(len(payload)) + ' bytes)', flush=True)
    result = {'source': 'MSYS2 official package distribution', 'packages': records,
              'notes': 'Runtime-only isolated extraction; no package scripts or global configuration changes.'}
    (destination / 'toolchain-manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return destination / 'ucrt64/bin'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', required=True)
    parser.add_argument('--lock', help='Use preserved package URLs/hashes instead of resolving current versions')
    args = parser.parse_args()
    print(fetch(args.dest, args.lock))

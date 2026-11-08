"""Verify declared FreeBSD pkg file checksums without running target code.

Format facts and golden vectors were checked against the official pkg 2.8.4:
https://raw.githubusercontent.com/freebsd/pkg/2.8.4/libpkg/pkg/checksum.h
https://raw.githubusercontent.com/freebsd/pkg/2.8.4/libpkg/pkg_checksum.c
https://raw.githubusercontent.com/freebsd/pkg/2.8.4/tests/lib/checksum.c

File records use <type>$<digest>, not the package manifest's version/type
format. pkg's base32 uses the z-base32 alphabet with LSB-first packing.
This independently implemented reader supports the confirmed textual file
formats only. Symlinks require separate literal-target verification.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import stat

MAX_FILE_BYTES = 1024 * 1024 * 1024
BLOCK_BYTES = 1024 * 1024
ALPHABET = 'ybndrfg8ejkmcpqxot1uwisza345h769'
FORMATS = {
    0: ('sha256', 'sha256', 32, 'pkg-zbase32-lsb'),
    1: ('sha256', 'sha256', 32, 'hex'),
    2: ('blake2b', 'blake2b-512', 64, 'pkg-zbase32-lsb'),
    5: ('blake2s', 'blake2s-256', 32, 'pkg-zbase32-lsb'),
}


def _base32(digest):
    """Encode digest bytes in pkg's documented LSB-first alphabet order."""
    accumulator, bits, result = 0, 0, []
    for byte in digest:
        accumulator |= byte << bits
        bits += 8
        while bits >= 5:
            result.append(ALPHABET[accumulator & 31])
            accumulator >>= 5
            bits -= 5
    if bits:
        result.append(ALPHABET[accumulator])
    return ''.join(result)


def _declared(value):
    if not isinstance(value, str) or len(value) > 110:
        raise ValueError('declared pkg checksum must be bounded text')
    if re.fullmatch('[0-9a-f]{64}', value):
        kind, encoded = 1, value  # pkg's legacy unprefixed SHA256 format.
    else:
        match = re.fullmatch('([0125])\\$([^$]+)', value)
        if match is None:
            raise ValueError('unsupported or malformed declared pkg file checksum')
        kind, encoded = int(match[1]), match[2]
    _, _, digest_bytes, encoding = FORMATS[kind]
    if encoding == 'hex':
        valid = re.fullmatch('[0-9a-f]{' + str(digest_bytes * 2) + '}', encoded) is not None
    else:
        expected = (digest_bytes * 8 + 4) // 5
        valid = len(encoded) == expected and all(char in ALPHABET for char in encoded)
        remaining_bits = digest_bytes * 8 % 5
        if valid and remaining_bits:
            valid = ALPHABET.index(encoded[-1]) < 1 << remaining_bits
    if not valid:
        raise ValueError('declared pkg checksum digest is not canonical for its type')
    return kind, encoded


def _identity(info):
    # On Windows Python 3.12 ctime denotes creation time, not POSIX metadata
    # change time, and fresh NTFS entries can settle it after their first stat.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns if os.name != 'nt' else None, info.st_mode)


def verify_declared(path, declared):
    """Return verified pkg checksum, bytes and SHA256 from one stable read.

    Invalid/unsupported checksums, mismatches and changed or redirected files
    raise ValueError; no unverified success result is returned. A successful
    checksum comparison binds file contents to the supplied declaration, not
    the package database's origin, signature or trustworthiness.
    """
    kind, expected = _declared(declared)
    factory, algorithm, _, encoding = FORMATS[kind]
    path = Path(path)
    if any(parent.is_symlink() for parent in path.absolute().parents):
        raise ValueError('pkg checksum file parent is redirected: ' + repr(str(path)))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_FILE_BYTES:
        raise ValueError('pkg checksum requires a bounded regular file: ' + repr(str(path)))
    digest = getattr(hashlib, factory)()
    sha256 = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if _identity(before) != _identity(opened):
            raise ValueError('pkg checksum file changed before reading: ' + repr(str(path)))
        total = 0
        while True:
            block = os.read(fd, BLOCK_BYTES)
            if not block:
                break
            total += len(block)
            if total > MAX_FILE_BYTES:
                raise ValueError('pkg checksum file exceeds the byte bound: ' + repr(str(path)))
            digest.update(block)
            sha256.update(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (total != before.st_size or _identity(before) != _identity(after)
            or _identity(before) != _identity(path.lstat())):
        raise ValueError('pkg checksum file changed during reading: ' + repr(str(path)))
    calculated = digest.hexdigest() if encoding == 'hex' else _base32(digest.digest())
    if not hmac.compare_digest(calculated, expected):
        raise ValueError('declared pkg checksum mismatch: ' + repr(str(path)))
    return {'algorithm': algorithm, 'encoding': encoding, 'checksum_type': kind,
            'verified': True, 'sha256': sha256.hexdigest(), 'bytes': total,
            'declared_checksum': declared}


verify = verify_declared

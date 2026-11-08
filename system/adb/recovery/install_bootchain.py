#!/usr/local/bin/python3.12
"""Install a separately named Pi U-Boot; publish the firmware selector last.

All operations require a SHA-bound bootchain-manifest.json. Scratch mode uses
private --root/--efi trees. --live requires stable FreeBSD, read-only normal
root, bound dual GPT, unmounted ESP, and persistent work on the bound state.
Only two FAT files can change. No root, EFI loader, raw-device or OTP writes.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys

import activate_failover as failover
import install as early

FORMAT = 'DaOS-bootchain-bundle-v1'
RECEIPT = 'DaOS-bootchain-install-v1'
MANIFEST = 'bootchain-manifest.json'
UBOOT = 'u-boot-daos-rescue.bin'
CONFIG = 'config.txt'
ROOT_PINS = {'boot/kernel/kernel', 'etc/rc.d/DaOS-rescue-clean'}
EFI_PINS = {'config.txt', 'u-boot.bin', 'EFI/BOOT/BOOTAA64.EFI', 'boot.scr',
            'DaOS-recovery/root.ufs.gz', 'DaOS-recovery/loader.efi',
            'DaOS-recovery/loader.lua', 'DaOS-recovery/kernel/kernel',
            'DaOS-recovery/kernel/usb_template.ko',
            'DaOS-recovery/kernel/daos_adb_template.ko'}
MAX_UBOOT = 4 * 1024**2


def selector(data):
    # Preserve section placement, line endings, comments and all other bytes.
    lines = data.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if re.match(rb'^\s*kernel\s*=', line)]
    if len(matches) != 1:
        raise ValueError('Exactly one firmware kernel= assignment required')
    index = matches[0]
    if lines[index].rstrip(b'\r\n') != b'kernel=u-boot.bin':
        raise ValueError('Firmware selector is not the exact original U-Boot')
    lines[index] = lines[index].replace(b'u-boot.bin', UBOOT.encode(), 1)
    return b''.join(lines)


def create_manifest(bundle, identity, root_pins, efi_pins):
    """Package an already reviewed UBOOT binary; never reads or writes devices."""
    bundle = early.unredirected(bundle)
    source = early.target(bundle, UBOOT)
    record = {'format': FORMAT, 'identity': identity,
              'pins': {'root': root_pins, 'efi': efi_pins},
              'uboot': {'path': UBOOT, 'bytes': source.stat().st_size,
                        'sha256': early.sha(source)}}
    path = bundle / MANIFEST
    with path.open('x', encoding='utf-8') as file:
        json.dump(record, file, indent=2); file.write('\n')
    # Validate immediately; an invalid candidate is never an applicable bundle.
    load_bundle(bundle, early.sha(path))
    return early.sha(path)


def load_bundle(bundle, expected):
    bundle = early.unredirected(bundle)
    if not re.fullmatch('[0-9a-f]{64}', expected or ''):
        raise ValueError('Exact manifest SHA256 binding required')
    raw = early.target(bundle, MANIFEST).read_bytes()
    if early.digest(raw) != expected:
        raise ValueError('Bootchain manifest changed')
    value = json.loads(raw)
    if value.get('format') != FORMAT:
        raise ValueError('Unexpected bootchain bundle')
    identity = value.get('identity', {})
    if (identity.get('format') != 'DaOS-recovery-identity-v1'
            or not re.fullmatch(r'/dev/(?:mmcsd|sdda)[0-9]+', identity.get('disk', ''))
            or not re.fullmatch('[0-9a-f-]{36}', identity.get('disk_uuid', ''))
            or type(identity.get('disk_bytes')) is not int or identity['disk_bytes'] <= 0
            or identity.get('sector_size') != 512
            or set(identity.get('partitions', {})) != {'efi', 'root', 'state'}):
        raise ValueError('Explicit SD geometry/GPT identity required')
    for role, index in (('efi', 1), ('root', 2), ('state', 3)):
        part = identity['partitions'][role]
        if (part.get('index') != index
                or not re.fullmatch('[0-9a-f-]{36}', part.get('uuid', ''))
                or any(type(part.get(key)) is not int or part[key] <= 0
                       for key in ('start_lba', 'sectors'))):
            raise ValueError('Explicit root/state partition identities required')
    pins = value.get('pins', {})
    if set(pins) != {'root', 'efi'}:
        raise ValueError('Both boot identity pin sets required')
    for area, names in (('root', ROOT_PINS), ('efi', EFI_PINS)):
        if set(pins[area]) != names:
            raise ValueError('Exact boot dependency pin set required')
        if any(not re.fullmatch('[a-f0-9]{64}', pin or '') for pin in pins[area].values()):
            raise ValueError('Invalid boot dependency SHA256')
    entry = value.get('uboot', {})
    if (set(entry) != {'path', 'bytes', 'sha256'} or entry['path'] != UBOOT
            or type(entry['bytes']) is not int or not 64 <= entry['bytes'] <= MAX_UBOOT
            or not re.fullmatch('[a-f0-9]{64}', entry['sha256'] or '')):
        raise ValueError('Invalid separate U-Boot payload')
    path = early.target(bundle, UBOOT)
    if not path.is_file() or path.stat().st_size != entry['bytes'] or early.sha(path) != entry['sha256']:
        raise ValueError('Separate U-Boot payload changed')
    return bundle, value


def check_dependencies(root, efi, manifest, *, installed_config=None, require_zero=True):
    if early.target(root, failover.POLICY).exists():
        raise ValueError('Watchdog policy must be absent during bootchain changes')
    for area, base in (('root', root), ('efi', efi)):
        for name, pin in manifest['pins'][area].items():
            if area == 'efi' and name == CONFIG and installed_config is not None:
                pin = installed_config
            if early.sha(early.target(base, name)) != pin:
                raise ValueError('Bound boot dependency changed: ' + area + '/' + name)
    trial = early.target(root, 'boot/nextboot.conf').read_bytes()
    if re.findall(rb'^\s*nextboot_enable\s*=\s*"([^"\r\n]*)"\s*$', trial, re.M) != [b'NO']:
        raise ValueError('One-shot rescue trial must be consumed before installation')
    marker = early.target(efi, failover.MARKER).read_bytes()
    if marker not in ((b'\0',) if require_zero else (b'\0', b'\1')):
        raise ValueError('Expected exact cleared normal.running marker')


def make_plan(root, efi, bundle, expected, *, location=None):
    root = early.unredirected(root); efi = early.unredirected(efi)
    if root == efi:
        raise ValueError('Separate root and EFI trees required')
    bundle, manifest = load_bundle(bundle, expected)
    check_dependencies(root, efi, manifest)
    path = early.target(efi, UBOOT)
    if path.exists():
        raise ValueError('Separate U-Boot filename already exists; inspect its original transaction')
    data = early.target(efi, CONFIG).read_bytes()
    changed = selector(data)
    if shutil.disk_usage(efi).free < manifest['uboot']['bytes'] + len(changed) + 1024**2:
        raise ValueError('Insufficient FAT space for atomic bootchain staging')
    return {'format': RECEIPT, 'root': str(root), 'efi_identity': location or str(efi),
            'manifest_sha256': expected, 'disk_uuid': manifest['identity']['disk_uuid'],
            'status': 'planned', 'config_before_sha256': early.digest(data),
            'config_after_sha256': early.digest(changed),
            'uboot_sha256': manifest['uboot']['sha256'],
            'publication_order': [UBOOT, CONFIG], 'root_written': False,
            'primary_efi_written': False, 'original_uboot_written': False,
            'raw_disk_writes': False, 'otp_modified': False, 'reboot_performed': False}


def save(work, record):
    early.atomic(work / 'deployment.json', (json.dumps(record, indent=2) + '\n').encode(), 0o600)


def _validate_rollback(root, efi, bundle, expected, work, *, location=None):
    _, manifest = load_bundle(bundle, expected)
    record = json.loads(early.target(work, 'deployment.json').read_text())
    if (record.get('format') != RECEIPT or record.get('root') != str(root)
            or record.get('efi_identity') != (location or str(efi))
            or record.get('manifest_sha256') != expected
            or record.get('disk_uuid') != manifest['identity']['disk_uuid']
            or record.get('publication_order') != [UBOOT, CONFIG]
            or record.get('uboot_sha256') != manifest['uboot']['sha256']):
        raise ValueError('Rollback receipt binding changed')
    original = early.target(work, 'backup/config.txt').read_bytes()
    before = manifest['pins']['efi'][CONFIG]
    after = early.digest(selector(original))
    if (early.digest(original) != before or record.get('config_before_sha256') != before
            or record.get('config_after_sha256') != after):
        raise ValueError('Original config backup/receipt changed')
    current = early.sha(early.target(efi, CONFIG))
    if current not in (before, after):
        raise ValueError('Refusing externally changed firmware config')
    check_dependencies(root, efi, manifest, installed_config=current, require_zero=False)
    path = early.target(efi, UBOOT)
    if path.exists() and early.sha(path) != manifest['uboot']['sha256']:
        raise ValueError('Refusing externally changed separate U-Boot')
    return record, original


def rollback(root, efi, bundle, expected, work, *, location=None):
    root = early.unredirected(root); efi = early.unredirected(efi); work = early.unredirected(work)
    record, original = _validate_rollback(root, efi, bundle, expected, work, location=location)
    # Validate everything above, then restore selector before deleting our image.
    path = early.target(efi, CONFIG)
    if early.sha(path) != record['config_before_sha256']:
        failover.fat_atomic(path, original)
        if early.sha(path) != record['config_before_sha256']:
            raise ValueError('Restored firmware selector readback failed')
    image = early.target(efi, UBOOT)
    if image.exists():
        image.unlink()
        if hasattr(os, 'sync'): os.sync()
    record['status'] = 'rolled-back'; save(work, record)
    return record


def apply(root, efi, bundle, expected, work, *, writer=None, location=None):
    root = early.unredirected(root); efi = early.unredirected(efi); work = early.unredirected(work)
    record = make_plan(root, efi, bundle, expected, location=location)
    if work.exists():
        raise ValueError('Fresh persistent bootchain work directory required')
    # Backups must never be inside either publication destination or the bundle.
    if any(work == base or base in work.parents for base in (efi, early.unredirected(bundle))):
        raise ValueError('Backup work must be separate from EFI and bundle')
    work.mkdir(parents=True, mode=0o700)
    backup = work / 'backup'; backup.mkdir(mode=0o700)
    original = early.target(efi, CONFIG).read_bytes()
    early.atomic(backup / CONFIG, original, 0o600)
    if early.sha(backup / CONFIG) != record['config_before_sha256']:
        raise ValueError('Config backup readback failed')
    save(work, record)  # Durable receipt exists before any FAT publication.
    try:
        bundle, manifest = load_bundle(bundle, expected)
        check_dependencies(root, efi, manifest)
        publish = writer or failover.fat_atomic
        record['status'] = 'applying'; save(work, record)
        image = early.target(efi, UBOOT)
        if image.exists(): raise ValueError('Separate U-Boot appeared during staging')
        data = early.target(bundle, UBOOT).read_bytes()
        if early.digest(data) != record['uboot_sha256']:
            raise ValueError('Candidate U-Boot changed after planning')
        publish(image, data, 0o644)
        if early.sha(image) != record['uboot_sha256']:
            raise ValueError('Separate U-Boot readback failed')
        record['uboot_verified'] = True; save(work, record)
        # The original selector stays active through all source/dependency checks.
        load_bundle(bundle, expected)
        check_dependencies(root, efi, manifest)
        publish(early.target(efi, CONFIG), selector(original), 0o644)
        if early.sha(early.target(efi, CONFIG)) != record['config_after_sha256']:
            raise ValueError('Firmware selector readback failed')
        record['status'] = 'installed-reboot-required'; save(work, record)
        return record
    except BaseException as error:
        record['error'] = str(error)
        try:
            rollback(root, efi, bundle, expected, work, location=location)
            record['status'] = 'failed-rolled-back'
        except BaseException as rollback_error:
            record['status'] = 'failed-rollback-refused'; record['rollback_error'] = str(rollback_error)
        save(work, record)
        raise


def require_normal_root(identity, labels, mounts, work=None):
    aliases = {}
    for line in labels.splitlines():
        fields = line.split()
        if len(fields) != 3 or '/dev/' + fields[0] in aliases:
            raise ValueError('Ambiguous GEOM labels')
        aliases['/dev/' + fields[0]] = '/dev/' + fields[2]
    def resolve(source):
        seen = set()
        while source in aliases:
            if source in seen: raise ValueError('GEOM label cycle')
            seen.add(source); source = aliases[source]
        return source
    rows = [line.split() for line in mounts.splitlines()]
    if any(len(row) != 6 for row in rows): raise ValueError('Malformed mount table')
    roots = [row for row in rows if row[1] == '/']
    if (len(roots) != 1 or resolve(roots[0][0]) != identity['disk'] + 'p2'
            or roots[0][2] != 'ufs' or 'ro' not in roots[0][3].split(',')):
        raise ValueError('Stable normal root must remain read-only on the bound SD')
    if work is not None:
        work = str(early.unredirected(work))
        if not work.startswith(('/var/DaOS/updates/', '/var/DaOS/usb-recovery/')):
            raise ValueError('Fresh persistent work under an approved /var/DaOS recovery directory required')
        parents = [r for r in rows if work == r[1] or work.startswith(r[1].rstrip('/') + '/')]
        row = max(parents, key=lambda r: len(r[1]))
        if resolve(row[0]) != identity['disk'] + 'p3' or row[2] != 'ufs' or 'rw' not in row[3].split(','):
            raise ValueError('Backup work must reside on the bound writable state partition')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'apply', 'rollback'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--efi', type=Path); parser.add_argument('--live', action='store_true')
    parser.add_argument('--bundle', type=Path, required=True); parser.add_argument('--sha256', required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args(); root = early.unredirected(args.root); work = early.unredirected(args.work)
    _, manifest = load_bundle(args.bundle, args.sha256)
    def execute(efi, location=None):
        if args.action == 'plan': return make_plan(root, efi, args.bundle, args.sha256, location=location)
        operation = apply if args.action == 'apply' else rollback
        return operation(root, efi, args.bundle, args.sha256, work, location=location)
    if args.live:
        if root != Path('/') or not sys.platform.startswith('freebsd') or os.geteuid() != 0 or args.efi is not None:
            raise ValueError('--live requires native FreeBSD root and internally mounted EFI')
        if early.target(root, failover.POLICY).exists():
            raise ValueError('Watchdog policy must be absent before mounting EFI')
        labels = failover.command(['/sbin/geom', 'label', 'status', '-s'])
        require_normal_root(manifest['identity'], labels, failover.command(['/sbin/mount', '-p']), work)
        with failover.mounted_efi(manifest['identity'], args.action != 'plan') as efi:
            result = execute(efi, manifest['identity']['disk'] + 'p1')
        # No writable_root context is used: this installer never changes root.
        require_normal_root(manifest['identity'], failover.command(['/sbin/geom', 'label', 'status', '-s']),
                            failover.command(['/sbin/mount', '-p']), work)
    else:
        if root == Path(root.anchor) or args.efi is None:
            raise ValueError('Scratch mode requires private --root and --efi trees')
        result = execute(early.unredirected(args.efi))
    print(json.dumps(result, indent=2)); return 0


if __name__ == '__main__': raise SystemExit(main())

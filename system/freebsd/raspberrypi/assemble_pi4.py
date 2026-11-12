#!/usr/bin/env python3
"""Create a new Raspberry Pi 4 ARM64 DaOS R14 SD image using native FreeBSD tools.

Reuses the appliance assembler's verified world/runtime/native inputs and five
read-only filesystem checks. It creates files only in a new dedicated work tree;
no installed system, disk device, mount or existing image is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'image'))
from assemble_image import (preflight, prepare, install_file, write_mtree,
                            commands, validate_filesystems, sha)
from common import MACHINES, require_freebsd, stable_copy, relative
from fastboot_profile import normal_boot_configuration

IMAGE_NAME = 'DaOS-R14-RPi4-arm64.img'
PINNED_COMMIT = '95719d8a895bf8170851e9844d9b4092b1d2b6fe'
RPI_RECIPE = ('https://raw.githubusercontent.com/freebsd/freebsd-src/'
              + PINNED_COMMIT + '/release/arm64/RPI.conf')
FIRMWARE_FILES = (
    'armstub8.bin', 'armstub8-gic.bin', 'bootcode.bin', 'fixup_cd.dat',
    'fixup_db.dat', 'fixup_x.dat', 'fixup.dat', 'LICENCE.broadcom',
    'start_cd.elf', 'start_db.elf', 'start_x.elf', 'start.elf',
    'fixup4.dat', 'fixup4cd.dat', 'fixup4db.dat', 'fixup4x.dat',
    'start4.elf', 'start4cd.elf', 'start4db.elf', 'start4x.elf',
    'bcm2710-rpi-2-b.dtb', 'bcm2710-rpi-3-b.dtb', 'bcm2710-rpi-3-b-plus.dtb',
    'bcm2710-rpi-cm3.dtb', 'bcm2710-rpi-zero-2-w.dtb', 'bcm2711-rpi-4-b.dtb',
    'bcm2711-rpi-cm4-io.dtb', 'bcm2711-rpi-cm4.dtb')
OVERLAYS = ('mmc.dtbo', 'pwm.dtbo', 'disable-bt.dtbo')
HDMI_OVERRIDE = '''
# DaOS R14 Pi4 HDMI / Boot Logo profile (DMT 800x600 at 60 Hz).
# Keep the upstream kernel, armstub and board-specific settings above.
[all]
disable_splash=1
hdmi_safe=0
disable_overscan=1
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=9
framebuffer_width=800
framebuffer_height=600
max_framebuffer_width=800
max_framebuffer_height=600
'''


def ordinary(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('ordinary input file required: ' + str(path))
    return path


def unique_file(root, suffix):
    suffix = Path(suffix).parts
    found = [p for p in root.rglob(suffix[-1])
             if p.is_file() and not p.is_symlink()
             and tuple(p.parts[-len(suffix):]) == suffix]
    if len(found) != 1:
        raise ValueError('expected one firmware input ending in ' + '/'.join(suffix)
                         + ', found ' + str(len(found)))
    return ordinary(found[0])


def splash_configuration(value, args):
    state = value['state']
    recorded = state.get('kernel_config', {})
    if (recorded.get('ident') != 'DaOS' or recorded.get('device_splash') is not True
            or not isinstance(recorded.get('name'), str) or not recorded['name']):
        raise ValueError('completed base-build record must identify the DaOS splash kernel configuration')
    config = Path(args.kernel_config) if args.kernel_config else Path(recorded['path'])
    ordinary(config)
    expected = state.get('configuration_sha256', {}).get(recorded['name'])
    content = config.read_text()
    if (expected != sha(config) or recorded.get('sha256') != expected
            or recorded.get('content') != content):
        raise ValueError('kernel configuration does not match completed base-build receipt')
    active = [line.split('#', 1)[0].strip() for line in content.splitlines()]
    if (not any(re.fullmatch(r'device\s+splash', line) for line in active)
            or any(re.fullmatch(r'nodevice\s+splash', line) for line in active)):
        raise ValueError('a rebuilt kernel with device splash is required for the PNG Boot Logo')
    return {'configuration_name': recorded['name'], 'configuration_sha256': expected, 'device_splash': True,
            'binding': 'completed source-base configuration and kernel artifact hashes',
            'boot_observed': False}


def logo_info(path):
    ordinary(path)
    data = path.read_bytes()
    if (len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n'
            or data[12:16] != b'IHDR' or data[24:26] != b'\x08\x06'):
        raise ValueError('Boot Logo must be an 8-bit RGBA PNG for the pinned vt splash path')
    width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if not 1 <= width <= 800 or not 1 <= height <= 600 or len(data) > 4 * 1024**2:
        raise ValueError('Boot Logo must fit the fixed 800x600 framebuffer and 4 MiB cap')
    return {'sha256': sha(path), 'bytes': len(data), 'width': width, 'height': height,
            'format': 'PNG RGBA8', 'path': '/boot/images/DaOS-logo.png',
            'resized_by_assembler': False, 'boot_observed': False}


def firmware_inputs(args):
    root = Path(args.firmware_dir).resolve(strict=True)
    # Accept both complete package roots and the fetcher's curated flat share tree.
    firmware = unique_file(root, 'rpi-firmware/config_arm64.txt').parent
    uboot = unique_file(root, 'u-boot-rpi-arm64/u-boot.bin')
    inputs = {name: ordinary(firmware / name) for name in FIRMWARE_FILES}
    inputs['config_arm64.txt'] = ordinary(firmware / 'config_arm64.txt')
    inputs['u-boot.bin'] = uboot
    inputs['README.u-boot'] = ordinary(uboot.parent / 'README')
    for name in OVERLAYS:
        inputs['overlays/' + name] = ordinary(firmware / 'overlays' / name)
    # Pi 400 has its own model DTB; do not advertise it unless actually included.
    optional = firmware / 'bcm2711-rpi-400.dtb'
    if optional.is_file():
        inputs[optional.name] = ordinary(optional)
    licenses = [p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                and 'licenses' in p.relative_to(root).parts]
    for source in licenses:
        position = source.parts.index('licenses')
        rel = '/'.join(source.parts[position + 1:])
        target = 'licenses/' + relative(rel)
        if target in inputs and sha(inputs[target]) != sha(source):
            raise ValueError('conflicting package license payload: ' + target)
        inputs[target] = ordinary(source)
    config = inputs['config_arm64.txt'].read_text()
    if not re.search(r'^\s*kernel\s*=\s*u-boot\.bin\s*$', config, re.M):
        raise ValueError('official config_arm64.txt does not select the supplied U-Boot')
    for source in firmware.iterdir():
        if source.is_file() and source.name.lower().startswith(('copying', 'licence', 'license')):
            inputs.setdefault(source.name, ordinary(source))
    candidates = [path for path in (root / 'firmware-receipt.json', root.parent / 'firmware-receipt.json')
                  if path.is_file()]
    if len(candidates) > 1:
        raise ValueError('ambiguous firmware receipt beside payload')
    provenance = None
    if candidates:
        receipt_path = ordinary(candidates[0])
        record = json.loads(receipt_path.read_text())
        if (record.get('format') != 'DaOS-pi4-official-firmware-fetch-v1'
                or record.get('package_installation') is not False
                or record.get('package_scripts_executed') is not False
                or not isinstance(record.get('files'), list)
                or not 1 <= len(record['files']) <= 100000):
            raise ValueError('invalid or incomplete firmware fetch receipt')
        packages = record.get('packages', [])
        if {item.get('name') for item in packages} != {'rpi-firmware', 'u-boot-rpi-arm64'}:
            raise ValueError('firmware receipt must bind the two selected official packages')
        if any(not re.fullmatch('[0-9a-f]{64}', str(item.get('archive_sha256', '')))
               for item in packages):
            raise ValueError('firmware package archive hashes missing')
        payload = firmware.parent
        expected = {}
        for item in record['files']:
            rel = relative(item['path'])
            if rel in expected:
                raise ValueError('duplicate firmware receipt path: ' + rel)
            expected[rel] = item
            source = ordinary(payload / rel)
            if source.stat().st_size != item['bytes'] or sha(source) != item['sha256']:
                raise ValueError('firmware payload differs from fetch receipt: ' + rel)
        for source in inputs.values():
            rel = source.relative_to(payload).as_posix()
            if rel not in expected or sha(source) != expected[rel]['sha256']:
                raise ValueError('selected firmware input is not bound by its fetch receipt: ' + rel)
        provenance = {'receipt_path': str(receipt_path), 'receipt_sha256': sha(receipt_path),
                      'format': record['format'], 'packages': packages,
                      'all_payload_files_verified': len(expected),
                      'package_installation': False, 'package_scripts_executed': False}
    return inputs, provenance


def pi_plan(value, args):
    plan = commands(value, args)
    plan[-1]['argv'][-1] = str(value['work'] / output_image_name(args))
    return plan


def output_image_name(args):
    return 'DaOS-BSD-RPi4-USB-ADB-arm64.img' if getattr(args, 'adb_bundle', None) else IMAGE_NAME


def usb_bundle_summary(value, firmware_provenance, bundle):
    """Read-only binding check for the inspectable assembly plan."""
    folder = Path(bundle).resolve(strict=True)
    receipt_path, manifest_path = folder / 'receipt.json', folder / 'manifest.json'
    if any(p.is_symlink() or not p.is_file() for p in (receipt_path, manifest_path)):
        raise ValueError('An ordinary completed USB overlay bundle is required')
    receipt = json.loads(receipt_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if (receipt.get('format') != 'DaOS-USB-ADB-integration-stage-v1' or receipt.get('status') != 'staged'
            or receipt.get('manifest_sha256') != sha(manifest_path)
            or manifest.get('format') != 'DaOS-USB-ADB-image-overlay-v1'
            or manifest.get('base_state_sha256') != value['base_state_sha256']
            or manifest.get('firmware_receipt_sha256') != (firmware_provenance or {}).get('receipt_sha256')
            or manifest.get('private_keys_staged') is not False):
        raise ValueError('USB bundle differs from this source world or official firmware')
    return {'path': str(folder), 'manifest_sha256': sha(manifest_path),
            'receipt_sha256': sha(receipt_path), 'identity': manifest['identity'],
            'physical_usb_verified': False, 'windows_binding_verified': False}


def prepare_pi(value, args, firmware, firmware_provenance, logo, kernel_splash):
    trees, summary = prepare(value, args)
    root, maps = trees['root']
    efi = value['work'] / 'efi'
    boot_manifest = []

    def copy_boot(source, rel):
        rel = relative(rel)
        destination = efi / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Pi boot input conflicts with an existing file: ' + rel)
        checksum = stable_copy(source, destination)
        boot_manifest.append({'path': rel, 'bytes': destination.stat().st_size,
                              'sha256': checksum})

    for rel, source in sorted(firmware.items()):
        copy_boot(source, rel)
    config = firmware['config_arm64.txt'].read_text().rstrip() + '\n' + HDMI_OVERRIDE
    (efi / 'config.txt').write_text(config)
    boot_manifest.append({'path': 'config.txt', 'bytes': (efi / 'config.txt').stat().st_size,
                          'sha256': sha(efi / 'config.txt'),
                          'derived_from_sha256': sha(firmware['config_arm64.txt'])})
    dtb = value['base'] / 'boot/dtb'
    if not dtb.is_dir() or dtb.is_symlink():
        raise ValueError('source-built base has no ordinary boot/dtb directory')
    for source in sorted(dtb.rglob('*')):
        if source.is_symlink():
            raise ValueError('redirected source DTB tree')
        if source.is_file():
            copy_boot(source, 'dtb/' + source.relative_to(dtb).as_posix())
    old_loader = (root / 'boot/loader.conf').read_text()
    loader_lines = [line for line in old_loader.splitlines()
                    if not re.match(r'\s*(virtio_random_load|boot_mute|splash|shutdown_splash|console|kern\.vt\.splash_cpu)\s*=', line)]
    loader_lines += ['# Raspberry Pi 4 / modern vt kernel Boot Logo',
                     'console="efi"', 'boot_mute="YES"',
                     'splash="/boot/images/DaOS-logo.png"', 'kern.vt.splash_cpu="0"']
    loader_text='\n'.join(loader_lines)+'\n'
    if getattr(args,'fast_boot',False):
        loader_text,rc_text=normal_boot_configuration(loader_text,(root/'etc/rc.conf').read_text())
        install_file(root,maps,'etc/rc.conf',rc_text.encode())
    install_file(root, maps, 'boot/loader.conf', loader_text.encode())
    install_file(root, maps, 'boot/images/DaOS-logo.png', Path(args.logo).read_bytes())
    provenance = Path(args.project) / 'assets/boot-logo/provenance.json'
    if provenance.is_file():
        install_file(root, maps, 'usr/share/DaOS/boot-logo-provenance.json', ordinary(provenance).read_bytes())
    if firmware_provenance:
        receipt_path = Path(firmware_provenance['receipt_path'])
        if sha(receipt_path) != firmware_provenance['receipt_sha256']:
            raise ValueError('firmware receipt changed while staging')
        install_file(root, maps, 'usr/share/DaOS/pi4-firmware.json', receipt_path.read_bytes())
    board = {'profile': 'raspberrypi4-arm64-v1', 'board': 'Raspberry Pi 4 Model B',
             'pi400_DTB_included': 'bcm2711-rpi-400.dtb' in firmware,
             'architecture': 'arm64', 'partition_scheme': 'GPT',
             'boot_chain': 'Raspberry Pi EEPROM/GPU firmware -> U-Boot -> EFI loader -> DaOS FreeBSD kernel',
             'firmware_recipe': RPI_RECIPE, 'firmware_recipe_commit': PINNED_COMMIT,
             'display': {'profile': 'HDMI DMT 800x600 at 60 Hz', 'physical_display_validated': False},
             'kernel_splash': kernel_splash, 'boot_logo': logo,
             'firmware_provenance': firmware_provenance,
             'boot_payload': sorted(boot_manifest, key=lambda item: item['path']),
             'physical_board_boot_validated': False, 'QEMU_board_emulation_claimed': False}
    board['fast_boot_candidate']={'enabled':getattr(args,'fast_boot',False),
        'scope':'Normal FreeBSD loader and Xorg/Python session; U-Boot firmware inputs are unchanged',
        'uboot_zero_countdown_requires_separate_selector_build':True,
        'physical_power_on_to_UI_seconds':None}
    summary['board'] = board
    install_file(root, maps, 'usr/share/DaOS/payload.json', (json.dumps(summary, indent=2) + '\n').encode())
    install_file(root, maps, 'usr/share/DaOS/raspberrypi-board.json', (json.dumps(board, indent=2) + '\n').encode())
    write_mtree(root, maps, value['work'] / 'root.mtree')
    return summary, board


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'build'))
    parser.add_argument('--architecture', choices=('arm64',), default='arm64')
    for name in ('base-state', 'runtime', 'project', 'native-evidence', 'native-command', 'work-dir', 'firmware-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--kernel-config', help='transported configuration matching the completed base-build kernel_config record')
    parser.add_argument('--logo', type=Path)
    parser.add_argument('--adb-bundle', type=Path, help='verified native FreeBSD USB ADB overlay, with derived Pi4 DTBs')
    for name, default in (('root', 2560), ('var', 128), ('calibration', 32), ('photos', 256), ('efi', 128)):
        parser.add_argument('--' + name + '-mib', type=int, default=default)
    parser.add_argument('--epoch', type=int, default=0)
    parser.add_argument('--compress', action='store_true')
    parser.add_argument('--fast-boot',action='store_true',help='Candidate zero loader countdown and overlapping Xorg/Python startup; preserve verified U-Boot input')
    args = parser.parse_args(argv)
    if args.logo is None:
        args.logo = Path(args.project) / 'assets/boot-logo/DaOS-logo.png'
    try:
        if any(not 16 <= getattr(args, name + '_mib') <= 8192
               for name in ('root', 'var', 'calibration', 'photos', 'efi')) or args.epoch < 0:
            raise ValueError('invalid image size/time bounds')
        if args.efi_mib < 64:
            raise ValueError('FAT32 EFI partition requires at least 64 MiB')
        value = preflight(args)
        kernel_splash = splash_configuration(value, args)
        logo = logo_info(args.logo)
        firmware, firmware_provenance = firmware_inputs(args)
        usb_plan = usb_bundle_summary(value, firmware_provenance, args.adb_bundle) if args.adb_bundle else None
        plan = pi_plan(value, args)
        if args.action == 'plan':
            print(json.dumps({'architecture': 'arm64', 'board': 'Raspberry Pi 4 Model B',
                              'creates_nothing': True, 'commands': plan,
                              'kernel_splash': kernel_splash, 'boot_logo': logo,
                              'firmware_provenance': firmware_provenance,
                              'usb_adb_bundle': usb_plan,
                              'firmware_inputs': {name: sha(path) for name, path in firmware.items()}}, indent=2))
            return 0
        require_freebsd()
        summary, board = prepare_pi(value, args, firmware, firmware_provenance, logo, kernel_splash)
        usb_adb = None
        if args.adb_bundle:
            integration = Path(args.project) / 'system/adb/integration'
            sys.path.insert(0, str(integration))
            from apply_overlay import apply_to_prepared
            usb_adb = apply_to_prepared(value, summary, board, args.adb_bundle)
        results, filesystem_validation = [], None
        for number, step in enumerate(plan):
            if step['argv'][0] == '/usr/bin/mkimg':
                filesystem_validation = validate_filesystems(value['work'])
            log = value['work'] / ('image-step-' + str(number) + '.log')
            with log.open('xb') as stream:
                subprocess.run(step['argv'], cwd=step['cwd'], stdout=stream,
                               stderr=subprocess.STDOUT, check=True, timeout=300)
            results.append(dict(step, log_sha256=sha(log)))
        if filesystem_validation is None:
            raise ValueError('GPT assembly omitted filesystem validation')
        image = value['work'] / output_image_name(args)
        evidence = {'format': 'DaOS-freebsd-image-build-v1', 'status': 'assembled', 'assembled': True,
            'board_profile': 'raspberrypi4-arm64-v1', 'boot_validated': False, 'gui_validated': False,
            'physical_board_boot_validated': False, 'target': {'architecture': 'arm64',
                'elf_machine': MACHINES['arm64'], 'efi_machine': value['loader']['pe_machine']},
            'uefi': True, 'host_mounts': False, 'package_scripts_run': False,
            'image': {'path': str(image), 'bytes': image.stat().st_size, 'sha256': sha(image)},
            'base': {'state_sha256': value['base_state_sha256'], 'kernel_ident': 'DaOS',
                'kernel_sha256': value['kernel']['sha256'], 'source_commit': value['state']['source']['commit'],
                'source_release': value['state']['source']['release']},
            'runtime': {'architecture': 'arm64', 'machine': value['manifest']['host']['machine'],
                **value['runtime_verification'], 'abi_target': 'FreeBSD:' + str(value['state']['source']['release']).split('.')[0] + ':aarch64',
                'manifest_sha256': value['runtime_sha256'], 'packages': value['manifest']['packages'],
                'profile': value['manifest']['profile'], 'statistics': value['manifest']['statistics'],
                'source_world_elf_dependency_check': summary['source_world_elf_dependency_check']},
            'native': {'evidence_sha256': value['native_evidence_sha256'], 'sha256': sha(value['native']),
                       'execution_machine': value['native_evidence']['uname']['machine']},
            'kernel': value['kernel'], 'loader': value['loader'], 'payload': summary, 'board': board,
            'commands': results, 'filesystem_validation': filesystem_validation,
            'assembler_sha256': sha(__file__)}
        if usb_adb:
            evidence['usb_adb'] = usb_adb
        if args.compress:
            subprocess.run(['/usr/bin/xz', '-T0', '-k', str(image)], check=True, timeout=1800)
            compressed = image.with_name(image.name + '.xz')
            evidence['compressed_image'] = {'path': str(compressed), 'bytes': compressed.stat().st_size,
                'sha256': sha(compressed), 'format': 'xz', 'raw_sha256': evidence['image']['sha256'],
                'raw_bytes': evidence['image']['bytes']}
        with (value['work'] / 'image-evidence.json').open('x') as stream:
            json.dump(evidence, stream, indent=2)
            stream.write('\n')
        print('DaOS_PI4_IMAGE_ASSEMBLED ' + str(image))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('DaOS_PI4_IMAGE_FAILED ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

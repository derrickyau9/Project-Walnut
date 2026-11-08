#!/usr/bin/env python3
"""Build the native USB transport against a verified source-built ARM64 BSD base."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import struct
import subprocess
import tarfile
import time


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def elf(path, allowed):
    data = path.read_bytes()
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', data, 18)[0] != 183
            or struct.unpack_from('<H', data, 16)[0] not in allowed):
        raise ValueError('Expected source-built ELF64/AArch64: ' + str(path))
    return {'machine': 'AArch64', 'elf_machine': 183,
            'elf_type': struct.unpack_from('<H', data, 16)[0],
            'size': len(data), 'sha256': sha(path), 'path': str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'archive-sha256', 'base-state', 'source-root', 'kernel-object', 'object-prefix', 'src-conf', 'work-dir'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if platform.system() != 'FreeBSD' or not platform.release().startswith('15.1-'):
        raise ValueError('A native FreeBSD 15.1 builder is required')
    archive, base_state = Path(args.archive), Path(args.base_state)
    if sha(archive) != args.archive_sha256 or archive.stat().st_size > 1024 * 1024:
        raise ValueError('USB source input archive does not match its recorded hash/size')
    state = json.loads(base_state.read_text())
    if (state.get('status') != 'completed' or state['target']['architecture'] != 'arm64'
            or state['source']['commit'] != '95719d8a895bf8170851e9844d9b4092b1d2b6fe'):
        raise ValueError('The source-built ARM64 15.1-p6 base receipt is required')
    source = Path(args.source_root).resolve(strict=True)
    kernel_object = Path(args.kernel_object).resolve(strict=True)
    sysroot = Path(state['stage_directory']).resolve(strict=True)
    if not (sysroot / 'usr/include/sys/types.h').is_file():
        raise ValueError('The verified target stage must contain its matching SDK headers')
    elf(sysroot / 'boot/kernel/kernel', (2, 3))
    if not (kernel_object / 'opt_global.h').is_file():
        raise ValueError('Matching ARM64 kernel object/configuration is required')
    work = Path(args.work_dir).absolute()
    if (work.exists() or work.is_symlink() or work.parent != Path('/var/daos-builds')
            or not work.name.startswith('daos-build-usb-adb-')):
        raise ValueError('Use a new private /var/daos-builds/daos-build-usb-adb-* directory')
    work.mkdir(mode=0o700)
    inputs = work / 'inputs'
    inputs.mkdir()
    record = {'format': 'DaOS-USB-ADB-FreeBSD-build-v1', 'status': 'running',
              'host': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
              'target': {'system': 'FreeBSD', 'architecture': 'aarch64'},
              'archive_sha256': sha(archive), 'base_state_sha256': sha(base_state),
              'source_commit': state['source']['commit'], 'source_root': str(source),
              'target_sysroot': str(sysroot),
              'kernel_object': str(kernel_object), 'work': str(work),
              'builder_sha256': sha(__file__), 'target_execution_validated': False,
              'physical_usb_enumeration_validated': False, 'started_at_unix': time.time()}
    receipt = work / 'build-evidence.json'
    try:
        with tarfile.open(archive) as stream:
            members = stream.getmembers()
            if len(members) > 64:
                raise ValueError('Unexpected USB source archive member count')
            for member in members:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or '..' in name.parts or not name.parts
                        or name.parts[0] != 'freebsd_usb' or not member.isfile()):
                    raise ValueError('Only canonical source files are accepted in the USB archive')
            stream.extractall(inputs, filter='data')
        usb_source = inputs / 'freebsd_usb'
        record['source_sha256'] = {p.relative_to(inputs).as_posix(): sha(p)
                                   for p in inputs.rglob('*') if p.is_file()}
        environment = dict(os.environ, MAKEOBJDIRPREFIX=args.object_prefix,
                           DAOS_USB_SOURCE=str(usb_source), DAOS_USB_OUTPUT=str(work / 'output'),
                           DAOS_KERNEL_OBJECT=str(kernel_object), DAOS_FREEBSD_SOURCE=str(source))
        command = ['/usr/bin/make', '-C', str(source), 'TARGET=arm64', 'TARGET_ARCH=aarch64',
                   'SRCCONF=' + args.src_conf, 'SRC_ENV_CONF=/dev/null', '__MAKE_CONF=/dev/null',
                   'WORLDTMP=' + str(sysroot), 'buildenv',
                   # The installed SDK contains target Unix tools. Keep host
                   # tools on PATH while retaining buildenv's cross CC/flags.
                   'BUILDENV_SHELL=/usr/bin/env PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh ' + str(usb_source / 'build-in-env.sh')]
        record['command'] = command
        receipt.write_text(json.dumps(record, indent=2) + '\n')
        with (work / 'build.log').open('xb') as log:
            subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=900)
        record['artifacts'] = {
            'transport': elf(work / 'output/helper/daos-usb-adb', (2, 3)),
            # FreeBSD arm64 links kmods with -Bshareable (ET_DYN).
            'kernel_module': elf(work / 'output/kmod/daos_adb_template.ko', (1, 3))}
        record.update(status='completed', finished_at_unix=time.time(), build_log_sha256=sha(work / 'build.log'))
    except BaseException as error:
        record.update(status='failed', error=str(error), finished_at_unix=time.time())
        raise
    finally:
        receipt.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()

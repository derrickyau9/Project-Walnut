"""Build and stage the DaOS ARM64 FreeBSD base in a private guest work tree.

No download, host installation, live /etc change, disk partitioning or image
mount is performed. A staged world is not by itself a boot-tested DaOS image.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
PROTECTED = ('/etc', '/boot', '/bin', '/sbin', '/lib', '/libexec', '/dev', '/proc',
             '/sys', '/usr/bin', '/usr/sbin', '/usr/lib', '/usr/libexec',
             '/usr/src', '/usr/obj', '/usr/local')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def new_work(path):
    value = Path(path).absolute()
    parent = value.parent.resolve(strict=True)
    value = parent / value.name
    if (len(value.parts) < 3 or os.path.lexists(value)
            or not value.name.startswith(('daos-base', 'daos-build'))
            or any(value == Path(item) or Path(item) in value.parents for item in PROTECTED)):
        raise ValueError('work must be a new dedicated daos-base*/daos-build* directory outside live system trees')
    if parent.stat().st_uid not in (os.geteuid(), 0):
        raise ValueError('work parent must belong to the invoking user or root')
    return value


def preflight(args, lock):
    if platform.system() != 'FreeBSD' or platform.machine() not in ('amd64', 'arm64', 'aarch64'):
        raise ValueError('an actual amd64/arm64 FreeBSD build host is required; Linux and compatibility jails are refused')
    if platform.release().split('-')[0] != '15.1':
        raise ValueError('this recipe requires a FreeBSD 15.1 build guest')
    if subprocess.check_output(['/sbin/sysctl', '-n', 'security.jail.jailed'],
                               text=True, timeout=5).strip() != '0':
        raise ValueError('a native FreeBSD VM/host is required, not a jail')
    if sys.version_info < (3, 12):
        raise ValueError('Python 3.12+ with the safe tar extraction filter is required')
    work = new_work(args.work_dir)
    archive = Path(args.source_archive).absolute()
    info = archive.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != lock['archive_bytes']:
        raise ValueError('source archive must be a regular file of the pinned size')
    if digest(archive) != lock['archive_sha256']:
        raise ValueError('source archive differs from the official release MANIFEST pin')
    if shutil.disk_usage(work.parent).free < args.min_free_gib * 1024**3:
        raise ValueError('insufficient free space for the configured native build')
    return work, archive


def read_lock(path):
    raw = Path(path).read_bytes()
    if len(raw) > 65536:
        raise ValueError('source lock exceeds its metadata bound')
    lock = json.loads(raw)
    if (not isinstance(lock, dict) or lock.get('format') not in ('daos-freebsd-base-source-v1', 'DaOS-freebsd-base-source-v2')
            or lock.get('architecture') not in ('amd64', 'arm64')
            or re.fullmatch(r'15\.1-RELEASE(?:-p[0-9]+)?', str(lock.get('release', ''))) is None
            or re.fullmatch(r'[0-9a-f]{40}', str(lock.get('commit', ''))) is None
            or re.fullmatch(r'[0-9a-f]{64}', str(lock.get('archive_sha256', ''))) is None
            or type(lock.get('archive_bytes')) is not int
            or not 0 < lock['archive_bytes'] <= 1024**3
            or lock.get('source_prefix', 'usr/src') != 'usr/src'
            or type(lock.get('archive_git_commit_comment_required', False)) is not bool):
        raise ValueError('unsupported or incomplete pinned FreeBSD 15.1 source lock')
    return lock, raw


def extract(archive, destination, lock):
    with tarfile.open(archive, 'r:xz') as source:
        if (lock.get('archive_git_commit_comment_required', False)
                and source.pax_headers.get('comment') != lock['commit']):
            raise ValueError('git archive commit comment does not match the pinned source commit')
        members = source.getmembers()
        if len(members) > 200000 or sum(max(item.size, 0) for item in members) > 8 * 1024**3:
            raise ValueError('source archive exceeds release extraction bounds')
        for item in members:
            name = item.name.removeprefix('./').rstrip('/')
            parts = PurePosixPath(name).parts
            if (item.name.startswith('/') or '..' in parts or '\\' in name
                    or any(ord(char) < 32 for char in name)
                    or not (name == 'usr' and item.isdir() or name == 'usr/src'
                            or name.startswith('usr/src/'))
                    or not (item.isdir() or item.isfile() or item.issym() or item.islnk())):
                raise ValueError('source entry is outside the expected usr/src tree')
            if item.issym() or item.islnk():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), item.linkname)
                                            if item.issym() else item.linkname)
                if item.linkname.startswith('/') or not (target == 'usr/src' or target.startswith('usr/src/')):
                    raise ValueError('source link leaves the pinned source tree')
        source.extractall(destination, members=members, filter='data')
    result = destination / 'usr/src'
    if not all((result / item).is_file() for item in ('Makefile', 'Makefile.inc1', 'sys/conf/newvers.sh')):
        raise ValueError('extracted source is not a complete FreeBSD base tree')
    version = (result / 'sys/conf/newvers.sh').read_text()
    revision = re.search(r'^REVISION="([^"]+)"$', version, re.MULTILINE)
    branch = re.search(r'^BRANCH="([^"]+)"$', version, re.MULTILINE)
    if not revision or not branch or revision[1] + '-' + branch[1] != lock['release']:
        raise ValueError('source newvers release/patch level differs from the selected lock')
    return result


def stop_owned(process):
    # A make leader can exit on TERM before a compiler descendant does. Do not
    # equate reaping that leader with stopping its entire owned session group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('owned build process did not stop') from error


def execute(argv, cwd, env, log, timeout, max_bytes):
    start = time.monotonic()
    count = 0
    with log.open('xb') as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                if time.monotonic() - start > timeout:
                    raise TimeoutError('build stage exceeded its recorded time bound')
                for key, _ in selector.select(1):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(block)
                    if count > max_bytes:
                        raise RuntimeError('build stage exceeded its recorded log bound')
                    output.write(block)
            status = process.wait(timeout=10)
            if status:
                raise RuntimeError('make stage failed with status ' + str(status))
        except BaseException:
            stop_owned(process)
            raise
        finally:
            selector.close()
            process.stdout.close()
            output.flush()
            os.fsync(output.fileno())
    return {'argv': argv, 'returncode': status, 'seconds': round(time.monotonic() - start, 3),
            'log_bytes': count, 'log_sha256': digest(log), 'log': log.name}


def write_state(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def interrupt_build(number, _frame):
    # Interrupt exactly once so a second TERM cannot bypass owned-group cleanup.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise KeyboardInterrupt('build interrupted by signal ' + str(number))


def toolchain_evidence():
    result = {}
    for name, command in (('compiler', ['/usr/bin/cc', '--version']),
                          ('compiler_targets', ['/usr/bin/cc', '--print-targets']),
                          ('linker', ['/usr/bin/ld.lld', '--version'])):
        value = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if len(value.stdout.encode()) + len(value.stderr.encode()) > 65536:
            raise ValueError('host toolchain information exceeds its output bound')
        result[name] = {'argv': command, 'returncode': value.returncode,
                        'stdout': value.stdout, 'stderr': value.stderr}
        if value.returncode:
            raise ValueError('host compiler/linker capability query failed: ' + name)
    return result


def check_target_elf(path, expected_machine):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:4] != b'\x7fELF' or header[4] != 2
            or header[5] != 1 or int.from_bytes(header[18:20], 'little') != expected_machine):
        raise RuntimeError('staged artifact is not the selected 64-bit little-endian target ELF: ' + str(path))
    return {'class': 64, 'byte_order': 'little', 'machine': expected_machine, 'osabi': header[7]}


def build(args):
    lock, raw_lock = read_lock(args.source_lock)
    if lock['architecture'] != args.architecture:
        raise ValueError('source profile architecture differs from the explicitly selected DaOS target')
    work, archive = preflight(args, lock)
    work.mkdir(mode=0o700)
    (work / 'logs').mkdir()
    (work / 'config').mkdir()
    (work / 'obj').mkdir()
    (work / 'stage').mkdir()
    (work / 'home').mkdir()
    (work / 'tmp').mkdir()
    (work / 'config/source.lock.json').write_bytes(raw_lock)
    stage = work / 'stage'
    target_arch = 'aarch64' if args.architecture == 'arm64' else 'amd64'
    elf_machine = 183 if args.architecture == 'arm64' else 62
    host_target = 'arm64' if platform.machine() in ('arm64', 'aarch64') else 'amd64'
    state = {'format': 'DaOS-freebsd-base-build-v2', 'status': 'preparing',
             'scope': 'DaOS source-built FreeBSD appliance base; not yet target image/boot/UI validation',
             'build_kind': 'native' if host_target == args.architecture else 'cross',
             'target': {'architecture': args.architecture, 'make_target': args.architecture,
                        'make_target_arch': target_arch, 'kernel_ident': 'DaOS', 'elf_machine': elf_machine},
             'source': lock, 'source_lock_sha256': hashlib.sha256(raw_lock).hexdigest(),
             'host': {'system': platform.system(), 'release': platform.release(),
                      'machine': platform.machine(), 'architecture': host_target, 'uid': os.geteuid()},
             'work_dir': str(work), 'destination': str(stage), 'stage_directory': str(stage), 'steps': [],
             'install_policy': {'no_root': True, 'destination_is_private': True, 'live_root_installed': False},
             'limits': {'jobs': args.jobs, 'stage_timeout_seconds': args.stage_timeout_seconds,
                        'log_max_bytes': args.log_max_mib * 1024**2}}
    record = work / 'build-state.json'
    write_state(record, state)
    try:
        source = extract(archive, work / 'source', lock)
        config = work / 'config/src.conf'
        shutil.copyfile(HERE / 'src.conf', config)
        kernels = work / 'kernel-config'
        shutil.copytree(source / ('sys/' + args.architecture + '/conf'), kernels, symlinks=False)
        shutil.copyfile(HERE / 'DaOS', kernels / 'DaOS')
        state['configuration_sha256'] = {'src.conf': digest(config), 'DaOS': digest(kernels / 'DaOS')}
        state['source_directory'] = str(source)
        anchors = ('Makefile', 'Makefile.inc1', 'sys/conf/newvers.sh',
                   'sys/' + args.architecture + '/conf/GENERIC')
        initial = {name: digest(source / name) for name in anchors}
        state['source_anchor_sha256'] = initial
        environment = {'PATH': '/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin',
                       'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'),
                       'LC_ALL': 'C', 'MAKEOBJDIRPREFIX': str(work / 'obj')}
        state['host_toolchain'] = toolchain_evidence()
        common = ['/usr/bin/make', 'TARGET=' + args.architecture, 'TARGET_ARCH=' + target_arch,
                  'SRCCONF=' + str(config), 'SRC_ENV_CONF=/dev/null', '__MAKE_CONF=/dev/null',
                  'KERNCONF=DaOS', 'KERNCONFDIR=' + str(kernels)]
        stages = [
            ('buildworld', common + ['-j' + str(args.jobs), 'buildworld']),
            ('buildkernel', common + ['-j' + str(args.jobs), 'buildkernel']),
            ('installworld', common + ['DESTDIR=' + str(stage), 'NO_ROOT=yes', 'installworld']),
            ('distribution', common + ['DESTDIR=' + str(stage), 'NO_ROOT=yes', 'distribution']),
            ('installkernel', common + ['DESTDIR=' + str(stage), 'NO_ROOT=yes', 'installkernel'])]
        for name, command in stages:
            state.update(status='running', active_step=name)
            write_state(record, state)
            print('DaOS_BASE_STEP ' + name, flush=True)
            result = execute(command, source, environment, work / 'logs' / (name + '.log'),
                             args.stage_timeout_seconds, args.log_max_mib * 1024**2)
            result['step'] = name
            state['steps'].append(result)
            write_state(record, state)
        if initial != {name: digest(source / name) for name in anchors}:
            raise RuntimeError('pinned source anchors changed during the build')
        expected = ('bin/sh', 'sbin/init', 'lib/libc.so.7', 'boot/kernel/kernel',
                    'etc/rc', 'etc/master.passwd', 'METALOG')
        if not all((stage / name).is_file() for name in expected):
            raise RuntimeError('staged world/kernel/configuration/ownership metadata are incomplete')
        state['target_elf_checks'] = {name: check_target_elf(stage / name, elf_machine)
                                      for name in ('bin/sh', 'lib/libc.so.7', 'boot/kernel/kernel')}
        state.update(status='completed', active_step=None,
                     artifact_sha256={name: digest(stage / name) for name in expected},
                     image_boot_validated=False, ui_validated=False)
        write_state(record, state)
        print('DaOS_BASE_BUILD_COMPLETE ' + str(record), flush=True)
        return state
    except BaseException as error:
        state.update(status='failed', error_type=type(error).__name__, error=str(error))
        write_state(record, state)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--architecture', choices=('arm64', 'amd64'), default='arm64',
                        help='DaOS target architecture; amd64 is a separate development profile')
    parser.add_argument('--source-lock', type=Path, default=HERE / 'source-arm64.lock.json',
                        help='explicit checksum/commit/patch/target profile; original release ARM64 is the default')
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=12)
    parser.add_argument('--stage-timeout-seconds', type=int, default=7200)
    parser.add_argument('--log-max-mib', type=int, default=512)
    parser.add_argument('--min-free-gib', type=int, default=12)
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 16 or not 60 <= args.stage_timeout_seconds <= 14400:
        parser.error('jobs must be1..16 and timeout60..14400 seconds')
    if not 1 <= args.log_max_mib <= 1024 or not 4 <= args.min_free_gib <= 64:
        parser.error('invalid log/disk bounds')
    try:
        for item in (signal.SIGINT, signal.SIGTERM):
            signal.signal(item, interrupt_build)
        result = build(args)
    except (KeyboardInterrupt, OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print('DaOS_BASE_BUILD_FAILED ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

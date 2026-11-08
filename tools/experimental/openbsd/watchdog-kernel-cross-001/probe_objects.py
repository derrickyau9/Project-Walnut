"""Replay exported native cc1 arguments on an isolated authenticated LLVM."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time

OUT = Path(__file__).resolve().parent
REFERENCE = OUT.parent / 'watchdog-kernel-002-arm64'
WORK = Path('/home/dy09/daos-openbsd-cross-001')
SYS = Path('/home/dy09/daos-openbsd-watchdog-001/sys')
TOOL = WORK / 'toolroot'
ENV = dict(os.environ, LD_LIBRARY_PATH=str(TOOL / 'usr/lib/x86_64-linux-gnu'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, *, cwd, log):
    start = time.monotonic()
    p = subprocess.run(args, cwd=cwd, env=ENV, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    (OUT / log).write_text(p.stdout)
    if p.returncode:
        raise RuntimeError(f'{log}: command exit {p.returncode}: {p.stdout[-1500:]}')
    return time.monotonic() - start


def main():
    ref = WORK / 'native-reference'
    obj = WORK / 'obj'
    ref.mkdir(exist_ok=True)
    obj.mkdir(exist_ok=True)
    archive = REFERENCE / 'native-cross-reference.tgz'
    assert sha(archive) == '2d087b93aa3817af5d2af15b56eb697854a6f72761f6f748aa518620cb41ac1f'
    with tarfile.open(archive) as tar:
        for entry in tar:
            assert '/' not in entry.name and entry.name not in ('.', '..')
            if entry.issym():
                assert entry.name in ('machine', 'arm64')
                continue
            assert entry.isfile()
            (ref / entry.name).write_bytes(tar.extractfile(entry).read())
    for name in ('machine', 'arm64'):
        link = ref / name
        if link.is_symlink():
            link.unlink()
        assert not link.exists()
        link.symlink_to(SYS / 'arch/arm64/include')
    text = (REFERENCE / 'exact-native-bcmdog-cc1.log').read_text()
    cc1_line = next(line.strip() for line in text.splitlines() if '"-cc1"' in line)
    native = shlex.split(cc1_line)
    assert native[:2] == ['/usr/bin/cc', '-cc1']
    commands = {}
    timings = {}
    for name, relative in [('bcm2835_dog', 'dev/fdt/bcm2835_dog.c'),
                           ('kern_watchdog', 'kern/kern_watchdog.c')]:
        args = [str(TOOL / 'usr/lib/llvm-22/bin/clang')]
        for token in native[1:]:
            if token.startswith('/usr/src/sys'):
                token = str(SYS) + token[len('/usr/src/sys'):]
            elif token == '/root/daos-qa/obj':
                # Include directory only. Debug/coverage compilation paths stay
                # native to avoid gratuitous debug-section path differences.
                token = str(ref) if native[native.index(token)-1] == '-I' else token
            if token in ('bcm2835_dog.o', 'bcm2835_dog.d', 'bcm2835_dog.c'):
                token = token.replace('bcm2835_dog', name)
            if token == str(SYS / 'dev/fdt/bcm2835_dog.c'):
                token = str(SYS / relative)
            if token == '/usr/lib/clang/22':
                token = str(TOOL / 'usr/lib/llvm-22/lib/clang/22')
            args.append(token)
        # Native compilation dirs appear twice; rebase includes by the exact
        # argument pair, while retaining all actual code-generation switches.
        for i in range(1, len(args)):
            if args[i-1] == '-I' and args[i] == '/root/daos-qa/obj':
                args[i] = str(ref)
        commands[name] = args
        (OUT / (name + '.argv.json')).write_text(json.dumps(args, indent=2) + '\n')
        timings[name] = run(args, cwd=obj, log=name + '.compile.log')
    run([str(TOOL / 'usr/bin/ld.lld-22'), '-r', str(obj / 'bcm2835_dog.o'),
         str(obj / 'kern_watchdog.o'), '-o', str(obj / 'watchdog-linked.o')],
        cwd=obj, log='partial-link.log')
    for path in obj.glob('*.o'):
        shutil.copyfile(path, OUT / path.name)
    checks = {}
    for name in ('bcm2835_dog', 'kern_watchdog', 'watchdog-linked'):
        for label, base in [('native', ref), ('cross', obj)]:
            run([str(TOOL / 'usr/bin/llvm-readelf-22'), '-h', '-S', '-s', '-r', str(base / (name + '.o'))],
                cwd=obj, log=name + '.' + label + '.readelf.txt')
        native_syms = subprocess.check_output([str(TOOL / 'usr/bin/llvm-nm-22'), '--format=posix',
                                               str(ref / (name + '.o'))], env=ENV, text=True)
        cross_syms = subprocess.check_output([str(TOOL / 'usr/bin/llvm-nm-22'), '--format=posix',
                                              str(obj / (name + '.o'))], env=ENV, text=True)
        # Ignore compiler-generated local mapping symbols and addresses; bind
        # the complete externally visible definitions and unresolved ABI names.
        symbols = lambda s: sorted(tuple(line.split()[:2]) for line in s.splitlines()
                                   if len(line.split()) >= 2 and line.split()[1].isupper())
        checks[name + '_external_symbols_equal'] = symbols(native_syms) == symbols(cross_syms)
        for label, output in [('native', native_syms), ('cross', cross_syms)]:
            (OUT / (name + '.' + label + '.nm.txt')).write_text(output)
    receipt = {
        'status': 'CROSS_OBJECTS_AND_PARTIAL_LINK_ONLY_NOT_BOOT_VALIDATED',
        'passed': all(checks.values()), 'checks': checks, 'seconds': timings,
        'compiler': 'Debian clang22.1.8 versus native OpenBSD clang22.1.6',
        'flags': 'Complete native cc1 argv replay, only tool/resource/include/source/output paths rebased',
        'native_archive_sha256': sha(archive),
        'native_cc1_log_sha256': sha(REFERENCE / 'exact-native-bcmdog-cc1.log'),
        'source_archive_sha256': '964b1aed7839b86f4e010a0fdd03dcd409e6f569e69fe85f95e5fceb740c2aaf',
        'object_files': {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size}
                         for p in obj.glob('*.o')},
        'no_flags_removed': True, 'retguard_flag_added': False,
        'native_build_modified_or_paused': False, 'physical_actions': False,
    }
    (OUT / 'object-probe-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

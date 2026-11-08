"""Build and boot a tiny, isolated ARM64 QEMU-virt software-camera test target.

Not a Zynq BSP or sensor simulation. No network, disk, host filesystem sharing,
KVM, firmware installation or physical device is used. The guest's initramfs
and native binaries are ephemeral; a bounded DNG stream is returned on serial.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
BASE = 'https://deb.debian.org/debian/'
INDEX = 'main/binary-arm64/Packages.xz'
RID = '26110500-0000-4000-8000-000000000002'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, maximum):
    if not url.startswith(BASE):
        raise ValueError('only the official Debian archive is supported')
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('download exceeds bound')
    return data


def kernel(destination, lock=None):
    """Download/extract only a kernel package; never install it on the host."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('kernel destination must be new')
    destination.mkdir(parents=True)
    if lock is None:
        release_url = BASE + 'dists/trixie/InRelease'
        release = fetch(release_url, 2 * 1024**2)
        signed = destination / 'InRelease'
        signed.write_bytes(release)
        subprocess.run(['gpgv', '--keyring', '/usr/share/keyrings/debian-archive-keyring.gpg', str(signed)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        match = re.search(r'^ ([0-9a-f]{64}) +(\d+) +' + re.escape(INDEX) + r'$', release.decode(), re.M)
        if not match:
            raise ValueError('signed ARM64 package index checksum missing')
        packed = fetch(BASE + 'dists/trixie/' + INDEX, 32 * 1024**2)
        if len(packed) != int(match[2]) or sha(packed) != match[1]:
            raise ValueError('ARM64 package index differs from signed release')
        unpacker = lzma.LZMADecompressor()
        text = unpacker.decompress(packed, max_length=128 * 1024**2)
        if not unpacker.eof:
            raise ValueError('ARM64 package index exceeds bound')
        records = {}
        for paragraph in text.decode().split('\n\n'):
            fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
            if fields.get('Package', '').startswith('linux-image-'):
                records[fields['Package']] = fields
        meta = records['linux-image-arm64']
        name = re.search(r'linux-image-[A-Za-z0-9.+-]+-arm64', meta['Depends'])[0]
        package = records[name]
        selected = {'package': name, 'version': package['Version'], 'url': BASE + package['Filename'],
                    'sha256': package['SHA256'], 'bytes': int(package['Size']),
                    'release_url': release_url, 'release_sha256': sha(release),
                    'index_sha256': sha(packed), 'release_signature_verified': True,
                    'scope': 'Debian ARM64 generic kernel for QEMU virt only; no camera BSP'}
        selected['verification_mode'] = 'signed Debian release and matching package index'
    else:
        selected = json.loads(Path(lock).read_text(encoding='utf-8'))
        if (not isinstance(selected.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', selected['sha256'])
                or type(selected.get('bytes')) is not int or not 0 < selected['bytes'] <= 256 * 1024**2):
            raise ValueError('invalid kernel lock')
        selected['verification_mode'] = 'pinned package SHA-256 from supplied lock; no new release-signature verification'
    data = fetch(selected['url'], 256 * 1024**2)
    if len(data) != selected['bytes'] or sha(data) != selected['sha256']:
        raise ValueError('kernel package checksum mismatch')
    archive = destination / 'kernel.deb'
    archive.write_bytes(data)
    subprocess.run(['dpkg-deb', '-x', str(archive), str(destination / 'extracted')], check=True)
    candidates = list((destination / 'extracted/boot').glob('vmlinuz-*'))
    if len(candidates) != 1:
        raise ValueError('kernel package has no unique boot image')
    image = candidates[0]
    selected['kernel_relative_path'] = image.relative_to(destination).as_posix()
    selected['kernel_sha256'] = sha(image.read_bytes())
    (destination / 'kernel-manifest.json').write_text(json.dumps(selected, indent=2) + '\n')
    return selected


def arm64_elf(data, static_required=True):
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183):
        raise ValueError('little-endian ARM64 ELF64 required')
    offset = int.from_bytes(data[32:40], 'little')
    entry_size = int.from_bytes(data[54:56], 'little')
    count = int.from_bytes(data[56:58], 'little')
    if entry_size < 56 or count > 4096 or offset + entry_size * count > len(data):
        raise ValueError('invalid ARM64 ELF program headers')
    if static_required and any(int.from_bytes(data[offset+i*entry_size:offset+i*entry_size+4], 'little') == 3 for i in range(count)):
        raise ValueError('guest requires static ELF without an interpreter')


def cpio(entries):
    """Deterministic newc archive, including guest-only character devices."""
    result = bytearray()
    def append(name, data, mode, major=0, minor=0):
        encoded = name.encode() + b'\0'
        fields = [len(result) + 1, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0]
        result.extend(b'070701' + b''.join(f'{value:08x}'.encode() for value in fields) + encoded)
        result.extend(b'\0' * (-len(result) % 4)); result.extend(data)
        result.extend(b'\0' * (-len(result) % 4))
    names=set()
    for name, data, mode, major, minor in sorted(entries):
        if (name.startswith('/') or any(part in {'','..','.'} for part in name.split('/'))
                or '\\' in name or '\0' in name or name in names or name=='TRAILER!!!'):
            raise ValueError('unsafe guest archive path')
        if stat.S_IFMT(mode) not in {stat.S_IFREG,stat.S_IFDIR,stat.S_IFCHR}:
            raise ValueError('unsupported guest entry type')
        names.add(name)
        append(name, data, mode, major, minor)
    append('TRAILER!!!', b'', 0)
    return bytes(result)


def build(build_dir, native_dir, cc='aarch64-linux-gnu-gcc'):
    from tools.arm64_build import source_inputs
    from tools.arm64_elf import inspect_arm64_elf
    build_dir, native_dir = Path(build_dir).resolve(), Path(native_dir).resolve()
    if build_dir.exists():
        raise ValueError('guest build directory must be new')
    source = ROOT / 'system/qemu-virt/init.c'
    rpc_source = ROOT / 'system/qemu-virt/rpc.c'
    native = {}
    for name in ('hf-core-tests', 'hf-daemon-tests', 'hf-native-worker', 'hf-camerad'):
        data = (native_dir / name).read_bytes(); arm64_elf(data)
        inspect_arm64_elf(native_dir/name,require_static=True)
        native[name] = data
    native_evidence = json.loads((native_dir/'arm64-build-evidence.json').read_text())
    if (native_evidence.get('format')!='hf-arm64-cross-build-v1' or native_evidence.get('tests_passed') is not True
            or native_evidence.get('target_triple')!='aarch64-linux-gnu'):
        raise ValueError('successful actual ARM64 cross-build/test evidence required')
    inputs=native_evidence.get('native_input_sha256')
    if not isinstance(inputs,dict) or not inputs or inputs!=source_inputs(ROOT):
        raise ValueError('complete matching ARM64 source hashes required; rebuild before guest packaging')
    for relative,expected in inputs.items():
        source_input=(ROOT/relative).resolve()
        if not source_input.is_relative_to(ROOT) or sha(source_input.read_bytes())!=expected:
            raise ValueError('ARM64 source inputs changed; rebuild before guest packaging')
    if any(native_evidence.get('artifacts',{}).get(name,{}).get('sha256')!=sha(data) for name,data in native.items()):
        raise ValueError('ARM64 binaries differ from tested cross-build')
    build_dir.mkdir(parents=True)
    command = [cc, '-std=c11', '-O2', '-static', '-Wall', '-Wextra', '-Werror', str(source), str(rpc_source), '-o', str(build_dir / 'init')]
    before = sha(source.read_bytes())
    guest_inputs={path.relative_to(ROOT).as_posix():sha(path.read_bytes()) for path in (source,rpc_source,Path(__file__))}
    subprocess.run(command, check=True)
    init = (build_dir / 'init').read_bytes(); arm64_elf(init)
    inspect_arm64_elf(build_dir/'init',require_static=True)
    if any(sha((ROOT/name).read_bytes())!=expected for name,expected in guest_inputs.items()):
        raise ValueError('guest init changed during build')
    entries = [(name, b'', stat.S_IFDIR | mode, 0, 0) for name, mode in [('bin',0o755),('dev',0o755),('proc',0o755),('photos',0o700),('camera',0o700),('tmp',0o1777)]]
    entries.extend((name,b'',stat.S_IFCHR | 0o600,major,minor) for name,major,minor in [('dev/console',5,1),('dev/null',1,3),('dev/urandom',1,9)])
    entries.append(('init',init,stat.S_IFREG | 0o755,0,0))
    entries.extend(('bin/'+name,data,stat.S_IFREG | 0o755,0,0) for name,data in native.items())
    packed = gzip.compress(cpio(entries), mtime=0)
    (build_dir / 'initramfs.cpio.gz').write_bytes(packed)
    report = {'format':'hf-qemu-virt-build-v1','scope':'generic virtual ARM64, synthetic images only',
              'init_source_sha256':before,'init_sha256':sha(init),'initramfs_sha256':sha(packed),
              'native_sha256':{name:sha(data) for name,data in native.items()},'command':command,
              'arm64_build_evidence_sha256':sha((native_dir/'arm64-build-evidence.json').read_bytes()),
              'native_input_sha256':inputs,
              'guest_input_sha256':guest_inputs,
              'cc_version':subprocess.run([cc,'--version'],check=True,capture_output=True,text=True).stdout.splitlines()[0]}
    (build_dir / 'build-evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def parse_serial(data):
    if len(data)>4*1024**2:
        raise ValueError('guest serial data exceeds bound')
    text = data.decode('ascii', errors='replace').replace('\r', '')
    required = ['HF_QEMU_INIT pid=1 machine=aarch64', 'HF_QEMU_NATIVE_TESTS_OK',
                'HF_QEMU_NOREPLACE_OK', 'HF_QEMU_RPC_RESTART_OK', 'HF_QEMU_RESULT PASS synthetic-only']
    if (any(text.count(marker)!=1 for marker in required) or 'HF_QEMU_RESULT FAIL' in text
            or text.count('HF_QEMU_DNG_BEGIN')!=1):
        raise ValueError('guest did not confirm all required synthetic tests')
    start = re.findall(r'^HF_QEMU_DNG_BEGIN (\d+)$', text, re.M)
    if len(start) != 1 or not 8 <= int(start[0]) <= 65536 or text.count('HF_QEMU_DNG_END') != 1:
        raise ValueError('guest DNG transfer framing invalid')
    section = text.split('HF_QEMU_DNG_BEGIN '+start[0]+'\n',1)[1].split('HF_QEMU_DNG_END',1)[0]
    lines = section.splitlines()
    if any(not re.fullmatch(r'HF_QEMU_DNG [0-9a-f]{2,96}',line) for line in lines):
        raise ValueError('guest DNG transfer data invalid')
    raw = bytes.fromhex(''.join(line[12:] for line in lines))
    if len(raw) != int(start[0]):
        raise ValueError('guest DNG transfer length differs')
    return raw, text


def boot(kernel_path, build_dir, *, qemu='qemu-system-aarch64', timeout=90):
    build_dir, kernel_path = Path(build_dir).resolve(), Path(kernel_path).resolve()
    if any((build_dir/name).exists() for name in ('serial.log','guest.dng','boot-evidence.json')):
        raise ValueError('guest run output exists; use a fresh guest build directory')
    if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not 0 < timeout <= 300:
        raise ValueError('guest timeout must be within 0..300 seconds')
    # The input must be an image extracted by fetch-kernel, tied to its exact
    # recorded package hash. An arbitrary kernel cannot inherit that provenance.
    manifest_root=kernel_path.parent.parent.parent
    manifest=json.loads((manifest_root/'kernel-manifest.json').read_text())
    expected_image=(manifest_root/manifest['kernel_relative_path']).resolve()
    if (expected_image!=kernel_path or sha(kernel_path.read_bytes())!=manifest.get('kernel_sha256')
            or sha((manifest_root/'kernel.deb').read_bytes())!=manifest.get('sha256')):
        raise ValueError('guest kernel differs from authenticated or pinned package evidence')
    evidence = json.loads((build_dir/'build-evidence.json').read_text())
    image = build_dir/'initramfs.cpio.gz'
    if sha(image.read_bytes()) != evidence['initramfs_sha256']:
        raise ValueError('guest initramfs changed after build')
    expected_guest={name:sha((ROOT/name).read_bytes()) for name in ('system/qemu-virt/init.c','system/qemu-virt/rpc.c','tools/qemu_target.py')}
    if evidence.get('guest_input_sha256')!=expected_guest:
        raise ValueError('guest sources changed after build')
    from tools.arm64_build import source_inputs
    if evidence.get('native_input_sha256')!=source_inputs(ROOT):
        raise ValueError('native ARM64 inputs changed after guest build')
    command = [qemu,'-M','virt','-cpu','cortex-a53','-accel','tcg','-smp','2','-m','512',
               '-kernel',str(kernel_path),'-initrd',str(image),'-append','console=ttyAMA0 rdinit=/init panic=-1 quiet HASSLEFREE_QEMU_VIRT=1',
               '-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot']
    process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    output = bytearray(); overflow = threading.Event()
    def collect():
        while chunk := process.stdout.read(4096):
            if len(output)+len(chunk)>4*1024**2:
                overflow.set(); process.kill(); break
            output.extend(chunk)
    reader=threading.Thread(target=collect,daemon=True); reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(); reader.join(timeout=3)
        if not reader.is_alive(): process.stdout.close()
        (build_dir/'serial.log').write_bytes(output)
        raise RuntimeError('QEMU guest timed out; owned process stopped') from None
    finally:
        if process.poll() is None: process.kill(); process.wait()
    reader.join(timeout=3)
    if not reader.is_alive(): process.stdout.close()
    (build_dir/'serial.log').write_bytes(output)
    if reader.is_alive() or overflow.is_set() or process.returncode != 0:
        raise RuntimeError('QEMU guest failed or exceeded bounded serial capture')
    raw,text = parse_serial(bytes(output))
    assertion_groups = {}
    for number, component in re.findall(r'PASS: (\d+) native (core|authority) assertions', text):
        if component in assertion_groups:
            raise ValueError('guest native assertion report repeated')
        assertion_groups[component] = int(number)
    if set(assertion_groups) != {'core', 'authority'} or any(n <= 0 for n in assertion_groups.values()):
        raise ValueError('guest native assertion evidence missing')
    artifact=build_dir/'guest.dng'
    with artifact.open('xb') as handle: handle.write(raw)
    from camera.dng.inspect import inspect_dng
    inspected=inspect_dng(artifact)
    if inspected['width']!=128 or inspected['height']!=96 or inspected['request_id']!=RID:
        raise ValueError('guest DNG identity/dimensions invalid')
    report={'format':'hf-qemu-virt-boot-v1','passed':True,'simulated':True,'kernel_sha256':sha(kernel_path.read_bytes()),
            'initramfs_sha256':sha(image.read_bytes()),'guest_dng_sha256':sha(raw),'guest_dng_bytes':len(raw),
            'native_assertions':assertion_groups,'guest_no_replace':True,'command':command,
            'guest_daemon_capture_restart_query':True,
            'qemu_version':subprocess.run([qemu,'--version'],check=True,capture_output=True,text=True).stdout.splitlines()[0],
            'scope':'full generic ARM64 virtual Linux; RAM-only storage; no sensor, board, persistence or power-cut validation'}
    report['kernel_package_sha256']=manifest['sha256']
    report['kernel_manifest_sha256']=sha((manifest_root/'kernel-manifest.json').read_bytes())
    (build_dir/'boot-evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('fetch-kernel'); p.add_argument('--dest',type=Path,required=True); p.add_argument('--lock',type=Path)
    p=sub.add_parser('build'); p.add_argument('--build-dir',type=Path,required=True); p.add_argument('--native-dir',type=Path,required=True); p.add_argument('--cc',default='aarch64-linux-gnu-gcc')
    p=sub.add_parser('boot'); p.add_argument('--build-dir',type=Path,required=True); p.add_argument('--kernel',type=Path,required=True); p.add_argument('--timeout',type=float,default=90)
    args=parser.parse_args()
    result=kernel(args.dest,args.lock) if args.action=='fetch-kernel' else build(args.build_dir,args.native_dir,args.cc) if args.action=='build' else boot(args.kernel,args.build_dir,timeout=args.timeout)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()

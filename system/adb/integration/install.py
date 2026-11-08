#!/usr/bin/env python3
"""Create a bound native USB ADB root/EFI overlay; never modify an input stage."""
from __future__ import annotations
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT/'system/freebsd/image'))
from device_tree import derive, sha
from assemble_image import validate_base_record
from elf_dependencies import parse_elf

COMMIT = '95719d8a895bf8170851e9844d9b4092b1d2b6fe'
CANONICAL_DTB = 'daos-rpi4-usb-device.dtb'

def ordinary(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode): raise ValueError('ordinary file required: '+str(path))
    return path

def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or any(ord(c)<32 for c in name):
        raise ValueError('invalid overlay relative path')
    return path.as_posix()

def read_json(path): return json.loads(ordinary(path).read_text())

def json_bytes(value): return (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()

def source_world_dtbs(base):
    directory=Path(base)/'boot/dtb'
    if directory.is_symlink() or not directory.is_dir(): raise ValueError('ordinary source-world DTB directory required')
    candidates=sorted(directory.rglob('bcm2711-rpi-4-b.dtb'))
    for path in candidates: ordinary(path)
    # FreeBSD's source-built DTB set may contain only RPi overlays. Board DTBs
    # then come from the separately pinned official rpi-firmware package.
    return candidates

def validate_keys(path):
    blob = ordinary(path).read_bytes()
    if not 1 <= len(blob) <= 1024*1024 or b'PRIVATE KEY' in blob: raise ValueError('only authorized public ADB keys may be staged')
    fingerprints = []
    for line in blob.decode('ascii').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        raw = base64.b64decode(line.split('\0',1)[0].strip().split()[0], validate=True)
        if len(raw) != 524: raise ValueError('ADB RSA2048 public key structure required')
        words, inverse = struct.unpack_from('<II', raw)
        modulus = int.from_bytes(raw[8:264], 'little'); rr = int.from_bytes(raw[264:520], 'little')
        exponent, = struct.unpack_from('<I', raw, 520)
        if (words != 64 or modulus.bit_length() != 2048 or not modulus&1 or exponent != 65537
                or inverse != (-pow(modulus&0xffffffff,-1,1<<32))&0xffffffff
                or rr != pow(2,4096,modulus)): raise ValueError('invalid Android RSA2048 public key')
        fingerprint = hashlib.sha256(raw).hexdigest()
        if fingerprint not in fingerprints: fingerprints.append(fingerprint)
    if not fingerprints: raise ValueError('at least one authorized public key is required')
    return blob, fingerprints

def source_resolve(base, relative_path):
    # Interpret absolute target symlinks inside the target stage, never host /lib.
    path = PurePosixPath('/'+relative(relative_path)); seen = set()
    for _ in range(32):
        pieces = list(path.parts[1:]); normalized = []
        for piece in pieces:
            if piece == '..':
                if not normalized: raise ValueError('target symlink escapes stage')
                normalized.pop()
            elif piece != '.': normalized.append(piece)
        path = PurePosixPath('/'+'/'.join(normalized))
        if str(path) in seen: raise ValueError('target symlink cycle')
        seen.add(str(path)); current = base
        for index, piece in enumerate(path.parts[1:]):
            current = current/piece
            if current.is_symlink():
                target = PurePosixPath(os.readlink(current))
                if not target.is_absolute(): target = PurePosixPath('/'+'/'.join(path.parts[1:index+1]))/target
                path = target.joinpath(*path.parts[index+2:]); break
        else: return ordinary(current)
    raise ValueError('too many target symlinks')

def elf_closure(helper, base):
    metadata = parse_elf(helper)
    if metadata is None or metadata['machine'] != 183 or metadata['osabi'] != 9: raise ValueError('AArch64 FreeBSD helper required')
    data=helper.read_bytes(); phoff=struct.unpack_from('<Q',data,32)[0]
    phsize, phnum=struct.unpack_from('<HH',data,54); interpreters=[]
    if phsize!=56 or phnum>1024 or phoff+phsize*phnum>len(data): raise ValueError('invalid helper program headers')
    for index in range(phnum):
        kind, _, offset, _, _, size, _, _=struct.unpack_from('<IIQQQQQQ',data,phoff+index*phsize)
        if kind==3:
            if not 1<size<=4096 or offset+size>len(data): raise ValueError('invalid helper interpreter extent')
            raw=data[offset:offset+size]
            if raw[-1]!=0 or 0 in raw[:-1]: raise ValueError('invalid helper interpreter string')
            interpreters.append(raw[:-1].decode('ascii'))
    if len(interpreters)!=1: raise ValueError('one helper interpreter required')
    loader = interpreters[0]; metadata['interpreter']=loader
    if loader != '/libexec/ld-elf.so.1': raise ValueError('unexpected native FreeBSD loader')
    source_resolve(base, loader.lstrip('/'))
    pending, bound = list(metadata.get('needed', [])), {}
    while pending:
        soname = pending.pop()
        if soname in bound: continue
        if '/' in soname or len(bound) >= 128: raise ValueError('invalid transport library closure')
        matches = []
        for directory in ('lib', 'usr/lib'):
            candidate = base/directory/soname
            if candidate.exists() or candidate.is_symlink(): matches.append(source_resolve(base, directory+'/'+soname))
        if not matches: raise ValueError('source world lacks native library '+soname)
        if len({sha(p) for p in matches}) != 1: raise ValueError('ambiguous source-world library '+soname)
        candidate = matches[0]; entry = parse_elf(candidate)
        if entry is None or entry['machine'] != 183 or entry['osabi'] != 9: raise ValueError('foreign transport dependency')
        bound[soname] = {'path': candidate.relative_to(base).as_posix(), 'sha256':sha(candidate)}
        pending.extend(entry.get('needed', []))
    return {'helper':metadata, 'source_world_libraries':bound, 'target_execution_validated':False}

def validate_inputs(args):
    state_path = ordinary(args.base_state); state = read_json(state_path); validate_base_record(state, 'arm64')
    if state['source']['commit'] != COMMIT: raise ValueError('unexpected BSD source commit')
    base = Path(state.get('stage_directory',state.get('destination',''))).resolve(strict=True)
    if base.is_symlink() or not base.is_dir(): raise ValueError('ordinary source world required')
    for rel in ('bin/sh','sbin/init','lib/libc.so.7','boot/kernel/kernel','etc/rc','etc/master.passwd','METALOG'):
        if sha(ordinary(base/rel)) != state['artifact_sha256'][rel]: raise ValueError('source world anchor changed: '+rel)
    build = read_json(args.usb_build_receipt)
    if (build.get('format') != 'DaOS-USB-ADB-FreeBSD-build-v1' or build.get('status') != 'completed'
            or build.get('target') != {'system':'FreeBSD','architecture':'aarch64'}
            or build.get('source_commit') != COMMIT or build.get('base_state_sha256') != sha(state_path)):
        raise ValueError('successful USB compile receipt must bind this exact source world')
    for role, path in (('transport',args.helper),('kernel_module',args.module)):
        path = ordinary(path); artifact = build['artifacts'][role]
        header = path.read_bytes()[:64]
        if (sha(path) != artifact['sha256'] or path.stat().st_size != artifact['size']
                or len(header)!=64 or header[:6]!=b'\x7fELF\x02\x01' or header[7] not in (0,9)
                or int.from_bytes(header[18:20],'little') != 183 or int.from_bytes(header[16:18],'little') != artifact['elf_type']
                or artifact['elf_machine'] != 183 or artifact['elf_type'] != 3): raise ValueError('compiled USB artifact differs: '+role)
    for rel, checksum in build.get('source_sha256',{}).items():
        if sha(ordinary(HERE.parent/relative(rel))) != checksum: raise ValueError('USB source differs from actual build receipt: '+rel)
    if not build.get('source_sha256'): raise ValueError('USB compile receipt lacks source hashes')
    template_candidates = [base/'boot/kernel/usb_template.ko',base/'boot/modules/usb_template.ko']
    template = next((p for p in template_candidates if p.is_file() and not p.is_symlink()),None)
    if template is None: raise ValueError('source-built usb_template.ko is required in base image')
    template_elf = parse_elf(template)
    if template_elf is None or template_elf['machine'] != 183: raise ValueError('wrong architecture usb_template.ko')
    daemon = Path(args.daemon_dir).resolve(strict=True); runtime = read_json(daemon/'runtime-manifest.json')
    if (runtime.get('format') != 'DaOS-ADB-device-runtime-manifest-v1' or runtime.get('preauthorized_RSA2048_required') is not True
            or runtime.get('network_listener_default') is not False or runtime.get('private_keys_in_source_tree') is not False):
        raise ValueError('authenticated stdio-only daemon manifest required')
    files = runtime.get('files',{})
    if not {'__init__.py','daos_adbd.py','auth.py','server.py','pty_child.py'}.issubset(files): raise ValueError('daemon closure incomplete')
    for rel, item in files.items():
        if '/' in rel or not rel.endswith('.py'): raise ValueError('unexpected daemon runtime file')
        source = ordinary(daemon/rel)
        if source.stat().st_size != item['bytes'] or sha(source) != item['sha256']: raise ValueError('daemon source changed: '+rel)
    keys, fingerprints = validate_keys(args.authorized_keys)
    firmware = Path(args.firmware_dir).resolve(strict=True)
    candidates = [p for p in firmware.rglob('bcm2711-rpi-4-b.dtb') if p.is_file() and not p.is_symlink() and p.parent.name=='rpi-firmware']
    if len(candidates) != 1: raise ValueError('one raw Pi4 firmware DTB required')
    firmware_dtb = ordinary(candidates[0])
    receipts=[p for p in (firmware/'firmware-receipt.json',firmware.parent/'firmware-receipt.json') if p.is_file()]
    if len(receipts)!=1: raise ValueError('one official firmware receipt required')
    firmware_receipt=read_json(receipts[0])
    if firmware_receipt.get('format')!='DaOS-pi4-official-firmware-fetch-v1': raise ValueError('unrecognized firmware receipt')
    selected=[item for item in firmware_receipt.get('files',[]) if item.get('path')=='rpi-firmware/bcm2711-rpi-4-b.dtb']
    if (len(selected)!=1 or selected[0]['sha256']!=sha(firmware_dtb)
            or selected[0]['bytes']!=firmware_dtb.stat().st_size): raise ValueError('raw firmware DTB differs from official package receipt')
    base_dtbs = source_world_dtbs(base)
    return {'state':state,'base':base,'build':build,'daemon':daemon,'runtime':runtime,'keys':keys,'fingerprints':fingerprints,
            'firmware_dtb':firmware_dtb,'base_dtbs':base_dtbs,'elf_closure':elf_closure(ordinary(args.helper),base),
            'firmware_receipt_sha256':sha(receipts[0]),
            'usb_template':{'path':template.relative_to(base).as_posix(),'sha256':sha(template)}}

def stage(args):
    value = validate_inputs(args)
    output = Path(args.output).absolute()
    if output.exists() or output.is_symlink() or len(output.parts)<3: raise ValueError('new private output directory required')
    for parent in output.parents:
        if parent.is_symlink(): raise ValueError('redirected output parent')
    output.mkdir(mode=0o700)
    records = {'root':{},'efi':{}}
    def put(tree, rel, blob, mode=0o644):
        rel = relative(rel); target = output/tree/rel
        if rel in records[tree]: raise ValueError('duplicate overlay destination '+rel)
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(blob); target.chmod(mode)
        records[tree][rel] = {'path':rel,'type':'file','mode':mode,'uid':0,'gid':0,'bytes':len(blob),'sha256':sha(target)}
    put('root','usr/local/libexec/daos-usb-adb',ordinary(args.helper).read_bytes(),0o755)
    put('root','boot/modules/daos_adb_template.ko',ordinary(args.module).read_bytes(),0o555)
    for rel in value['runtime']['files']:
        put('root','usr/local/libexec/daos-adb/'+rel,ordinary(value['daemon']/rel).read_bytes())
    for source in sorted((HERE/'files').rglob('*')):
        if not source.is_file() or source.is_symlink() or '__pycache__' in source.parts or source.suffix in {'.pyc','.pyo'}: continue
        rel = source.relative_to(HERE/'files').as_posix()
        put('root',rel,source.read_bytes(),0o755 if rel.startswith(('usr/local/libexec/','usr/local/etc/rc.d/')) else 0o644)
    put('root','etc/daos/adb_keys',value['keys'],0o600)
    config = {'format':'DaOS-USB-ADB-session-v1','vid':args.vid,'pid':args.pid,'authorized_keys':'/etc/daos/adb_keys','daemon_root':'/usr/local/libexec/daos-adb'}
    put('root','usr/local/etc/daos/usb-adb.json',json_bytes(config),0o600)
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,63}',args.serial): raise ValueError('safe per-device USB serial required')
    loader = ('# DaOS native USB development identity; unallocated VID/PID until formally assigned.\n'
              '# Hide the peripheral until the authenticated ADB template is registered by RC.\n'
              'hw.usb.template="-1"\n'
              f'hw.usb.daos_adb.vendor_id="{args.vid}"\nhw.usb.daos_adb.product_id="{args.pid}"\n'
              f'hw.usb.daos_adb.serial="{args.serial}"\n')
    put('root','boot/loader.conf.local',loader.encode())
    derivations=[]
    firmware_derived=output/'derived/firmware-pi4.dtb'
    result=derive(value['firmware_dtb'],firmware_derived,args.dtc); result['origin']='official-firmware'; derivations.append(result)
    blob=firmware_derived.read_bytes()
    put('efi','bcm2711-rpi-4-b.dtb',blob); put('efi',CANONICAL_DTB,blob)
    put('root','boot/dtb/'+CANONICAL_DTB,blob)
    for index, source in enumerate(value['base_dtbs']):
        destination=output/f'derived/source-world-{index}.dtb'
        result=derive(source,destination,args.dtc); result['origin']='source-built-world'; derivations.append(result)
        rel=source.relative_to(value['base']/'boot/dtb').as_posix(); blob=destination.read_bytes()
        put('root','boot/dtb/'+rel,blob); put('efi','dtb/'+rel,blob)
    dtb_receipt={'format':'DaOS-Pi4-USB-DTB-derivation-v1','source_commit':COMMIT,'changes':['status=okay','dr_mode=peripheral','compatible includes snps,dwc2'],
                 'inputs_unchanged':True,'source_world_candidates':len(value['base_dtbs']),
                 'derivations':derivations,'candidate_paths':{tree:sorted(rel for rel in records[tree] if rel.endswith('.dtb')) for tree in records},
                 'boot_chain':'GPU firmware -> U-Boot -> EFI loader (EFI FDT table) -> kernel',
                 'preserve_gpu_runtime_fixups':True,'raw_dtb_preload_in_efi':False,'physical_final_fdt_verified':False}
    put('root','usr/share/DaOS/usb-dtb-derivation.json',json_bytes(dtb_receipt))
    put('root','usr/share/DaOS/usb-adb-build.json',ordinary(args.usb_build_receipt).read_bytes())
    put('root','usr/share/DaOS/usb-adb-runtime.json',ordinary(value['daemon']/'runtime-manifest.json').read_bytes())
    append=('[all]\n# DaOS native USB ADB: derived Pi4 DTB, GPU fixups preserved.\n'
            'device_tree='+CANONICAL_DTB+'\n').encode()
    (output/'config.txt.append').write_bytes(append)
    manifest={'format':'DaOS-USB-ADB-image-overlay-v1','target':{'system':'FreeBSD','architecture':'aarch64','board':'raspberrypi4'},
              'root':list(records['root'].values()),'efi':list(records['efi'].values()),
              'config_append':{'path':'config.txt.append','sha256':sha(output/'config.txt.append')},
              'identity':{'vid':args.vid,'pid':args.pid,'serial':args.serial,'usb_if_assigned':False},
              'authorized_public_key_fingerprints':value['fingerprints'],'private_keys_staged':False,
              'base_state_sha256':sha(args.base_state),'usb_build_receipt_sha256':sha(args.usb_build_receipt),
              'firmware_receipt_sha256':value['firmware_receipt_sha256'],
              'source_world_usb_template':value['usb_template'],'transport_elf_closure':value['elf_closure'],
              'physical_usb_verified':False,'windows_binding_verified':False,'image_boot_verified':False}
    (output/'manifest.json').write_bytes(json_bytes(manifest))
    receipt={'format':'DaOS-USB-ADB-integration-stage-v1','status':'staged','manifest_sha256':sha(output/'manifest.json'),
             'source_world_inputs_unchanged':True,'firmware_inputs_unchanged':True,'dtb':dtb_receipt,
             'output':str(output),'configuration_enables_native_usb':True,'physical_usb_verified':False}
    (output/'receipt.json').write_bytes(json_bytes(receipt))
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('base-state','firmware-dir','helper','module','usb-build-receipt','daemon-dir','authorized-keys','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--dtc',default='/usr/bin/dtc')
    parser.add_argument('--vid',type=lambda n:int(n,0),default=0xffff); parser.add_argument('--pid',type=lambda n:int(n,0),default=0xda05)
    parser.add_argument('--serial',default='DaOS-USB-ADB-DEVELOPMENT')
    args=parser.parse_args()
    if not 1<=args.vid<=65535 or not 1<=args.pid<=65535: parser.error('VID/PID must be nonzero 16-bit values')
    print(json.dumps(stage(args),indent=2))

if __name__=='__main__': main()

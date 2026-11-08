"""Build a private, unprivileged NetBSD/aarch64 GPT image from verified sources.

The caller first builds NetBSD distribution/kernel and stages source-bound
components in WORKSPACE/packages/{overlay,manifests}. This backend never uses
FreeBSD tools, mounts disks, runs target ELF, or installs into the host.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import uuid

from . import netbsd
from .image_formats import FAT32, FFS2, FormatError, gpt

FORMAT='DaOS-NetBSD-image-v1'
FFS_GUID='49f48d5a-b10e-11dc-b99b-0019d1879648'
EFI_GUID='c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
STATE_MOUNT='var'
PHOTOS_MOUNT='var/DaOS/camera/photos'


def _regular(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise netbsd.BackendError('Regular input required: '+str(path))
    return path


def _relative(value):
    if (not isinstance(value,str) or not value or value.startswith('/') or '\\' in value
            or any(ord(c)<32 or ord(c)==127 for c in value) or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value))!=value):raise netbsd.BackendError('Canonical relative image path required: '+str(value))
    return value


def _escape(value):
    return ''.join(chr(byte) if 33<=byte<127 and byte not in (35,92) else '\\%03o'%byte for byte in value.encode('utf-8'))


def _target_file(root,name):
    """Resolve target absolute/relative symlinks without entering the host root."""
    root=netbsd._no_symlink_components(root)
    pending=list(PurePosixPath(_relative(name)).parts);resolved=[];links=0
    while pending:
        part=pending.pop(0)
        if part in ('','.'):continue
        if part=='..':
            if not resolved:raise netbsd.BackendError('Target symlink escapes its root')
            resolved.pop();continue
        candidate=root.joinpath(*resolved,part)
        if candidate.is_symlink():
            links+=1
            if links>40:raise netbsd.BackendError('Target symlink cycle/depth limit')
            target=os.readlink(candidate)
            if not target or '\\' in target or any(ord(c)<32 or ord(c)==127 for c in target):
                raise netbsd.BackendError('Invalid target symlink')
            if target.startswith('/'):resolved=[]
            pending=[p for p in PurePosixPath(target).parts if not p.startswith('/')]+pending
        else:resolved.append(part)
    result=root.joinpath(*resolved)
    return _regular(result)


def _tool(workspace,name):
    path=workspace/'tools/bin'/name
    if not path.is_file() or not os.access(path,os.X_OK):raise netbsd.BackendError('Build the source host tool first: '+str(path))
    if workspace not in path.resolve().parents:raise netbsd.BackendError('Host tool escapes private workspace')
    return str(path)


def _run(command,workspace,log,input_text=None,cwd=None):
    print('Image step: '+' '.join(map(str,command)),flush=True)
    with log.open('xb') as output:
        result=subprocess.run(list(map(str,command)),cwd=cwd or workspace,env=netbsd._build_environment(workspace),
                              input=input_text.encode() if input_text is not None else None,
                              stdout=output,stderr=subprocess.STDOUT,timeout=1800)
    if result.returncode:raise netbsd.BackendError('Image tool failed; see '+str(log))


def _elf(path,require_note=True):
    facts=netbsd.check_target_elf(_regular(path));data=path.read_bytes()
    if facts['osabi'] not in (0,2):raise netbsd.BackendError('Foreign target ELF OSABI: '+str(path))
    if facts['type_id'] not in (2,3):raise netbsd.BackendError('Executable/shared NetBSD ELF required: '+str(path))
    phoff=struct.unpack_from('<Q',data,32)[0];entsize,count=struct.unpack_from('<HH',data,54)
    if entsize!=56 or not 1<=count<=1024 or phoff+count*entsize>len(data):raise netbsd.BackendError('Invalid target ELF program headers')
    notes=[];interpreter=None
    for index in range(count):
        kind,flags,begin,va,pa,size,ms,align=struct.unpack_from('<IIQQQQQQ',data,phoff+index*entsize)
        if begin+size>len(data):raise netbsd.BackendError('Invalid target ELF segment bounds')
        if kind==3:
            interpreter=data[begin:begin+size].rstrip(b'\0').decode('ascii')
            if interpreter not in ('/libexec/ld.elf_so','/usr/libexec/ld.elf_so'):raise netbsd.BackendError('Foreign ELF interpreter: '+interpreter)
        if kind!=4:continue
        cursor=begin;end=begin+size
        while cursor+12<=end:
            namesz,descsz,tag=struct.unpack_from('<III',data,cursor);cursor+=12
            if namesz>4096 or descsz>65536:raise netbsd.BackendError('Unbounded target ELF note')
            name=data[cursor:cursor+namesz].rstrip(b'\0');cursor+=(namesz+3)&~3
            if cursor+descsz>end:raise netbsd.BackendError('Truncated target ELF note')
            if name in (b'FreeBSD',b'OpenBSD') or (name==b'GNU' and tag==1):raise netbsd.BackendError('Foreign OS note in target ELF: '+str(path))
            if name==b'NetBSD' and tag==1 and descsz==4:notes.append(struct.unpack_from('<I',data,cursor)[0])
            cursor+=(descsz+3)&~3
    if require_note and len(notes)!=1:raise netbsd.BackendError('One NetBSD version note required: '+str(path))
    return dict(facts,NetBSD_version_notes=notes,interpreter=interpreter)


def _pe(path):
    data=_regular(path).read_bytes()
    if len(data)<128 or data[:2]!=b'MZ':raise netbsd.BackendError('AArch64 EFI PE image required')
    offset=struct.unpack_from('<I',data,60)[0]
    if (offset+94>len(data) or data[offset:offset+4]!=b'PE\0\0'
            or struct.unpack_from('<H',data,offset+4)[0]!=0xaa64
            or struct.unpack_from('<H',data,offset+24)[0]!=0x20b
            or struct.unpack_from('<H',data,offset+24+68)[0]!=10):
        raise netbsd.BackendError('Source-built bootaa64.efi must be PE32+/AArch64 EFI application')
    return {'machine':'AArch64','format':'PE32+','subsystem':'EFI application','sha256':netbsd._hash(path)}


def _relocatable(source,row,overlay):
    """Bind non-executable development objects to an audited NetBSD package.

    ET_REL has no program headers/OS note by design. Only exact .o members of
    the declared, hash-pinned target package may take this exception.
    """
    facts=netbsd.check_target_elf(source);provenance=row.get('abi_provenance',{})
    if (facts['type_id']!=1 or facts['osabi'] not in (0,2) or row.get('elf_kind')!='relocatable' or source.suffix!='.o'
            or provenance.get('format')!='DaOS-NetBSD-package-ABI-v1'
            or provenance.get('OPSYS')!='NetBSD' or provenance.get('MACHINE_ARCH')!='aarch64'
            or provenance.get('archive_build_info_checked') is not True):
        raise netbsd.BackendError('Only explicitly package-bound NetBSD development objects may lack executable ABI notes')
    package=provenance.get('package','');member=_relative(provenance.get('archive_member'))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]*',package) or row['path']!='usr/pkg/'+member:
        raise netbsd.BackendError('Relocatable object package/member mismatch')
    expected=provenance.get('archive_sha512','');info_hash=provenance.get('build_info_sha256','')
    if not re.fullmatch(r'[0-9a-f]{128}',expected) or not re.fullmatch(r'[0-9a-f]{64}',info_hash):
        raise netbsd.BackendError('Pinned archive and +BUILD_INFO hashes required for ET_REL')
    archive=_regular(overlay/'usr/share/daos/ui-packages'/(package+'.tgz'))
    if overlay not in archive.resolve(strict=True).parents:raise netbsd.BackendError('Relocatable package archive escapes overlay')
    if netbsd._hash(archive,'sha512')!=expected:raise netbsd.BackendError('Relocatable provenance archive changed')
    with tarfile.open(archive,'r:*') as package_archive:
        def read_member(name):
            matches=[m for m in package_archive.getmembers() if m.name.removeprefix('./')==name]
            if len(matches)!=1 or not matches[0].isfile():raise netbsd.BackendError('One regular package member required: '+name)
            stream=package_archive.extractfile(matches[0])
            with stream:return stream.read()
        info=read_member('+BUILD_INFO')
        if hashlib.sha256(info).hexdigest()!=info_hash:raise netbsd.BackendError('Package +BUILD_INFO differs from ABI provenance')
        values=dict(line.split('=',1) for line in info.decode().splitlines() if '=' in line)
        for key in ('OPSYS','MACHINE_ARCH','OS_VERSION'):
            if values.get(key)!=provenance.get(key):raise netbsd.BackendError('Package target ABI metadata differs: '+key)
        if hashlib.sha256(read_member(member)).hexdigest()!=row['sha256']:
            raise netbsd.BackendError('Relocatable object differs from exact package member')
    return dict(facts,package_provenance_verified=True)


def _packages(workspace):
    root=netbsd._no_symlink_components(workspace/'packages/overlay')
    folder=netbsd._no_symlink_components(workspace/'packages/manifests')
    if not root.is_dir() or not folder.is_dir():raise netbsd.BackendError('Stage packages/overlay and packages/manifests first')
    entries={};components=[];verified_loaders=set()
    for path in sorted(folder.glob('*.json')):
        record=netbsd._read_json(path)
        if (record.get('format')!='DaOS-BSD-component-manifest-v1' or not isinstance(record.get('name'),str)
                or record.get('target',{}).get('system')!='NetBSD'
                or record.get('target',{}).get('architecture')!='aarch64'):
            raise netbsd.BackendError('Only source-bound NetBSD/aarch64 component manifests may enter an image: '+str(path))
        components.append({'name':record['name'],'version':record.get('version'),'manifest':str(path),'sha256':netbsd._hash(path)})
        for row in record['files']:
            name=_relative(row['path']);source=root/name
            for parent in source.parents:
                if parent==root.parent:break
                if parent.is_symlink():raise netbsd.BackendError('Component path redirected by a symlink: '+name)
            kind=row.get('type')
            if kind in ('file','f'):
                _regular(source);digest=netbsd._hash(source)
                with source.open('rb') as stream:magic=stream.read(4)
                if magic==b'\x7fELF':
                    if row.get('elf_kind')=='relocatable':
                        _relocatable(source,row,root);elf={'interpreter':None}
                    else:elf=_elf(source)
                    if elf['interpreter']:
                        loader=_target_file(workspace/'destdir',elf['interpreter'].lstrip('/'))
                        if loader not in verified_loaders:
                            _elf(loader,False);verified_loaders.add(loader)
            elif kind in ('link','l'):
                if not source.is_symlink():raise netbsd.BackendError('Component symlink missing: '+name)
                link=os.readlink(source)
                if 'link' in row and row['link']!=link:raise netbsd.BackendError('Component symlink target changed: '+name)
                digest=hashlib.sha256(link.encode()).hexdigest()
            else:raise netbsd.BackendError('Manifest accepts files/literal links only: '+name)
            if digest!=row['sha256']:raise netbsd.BackendError('Component payload changed: '+name)
            canonical={'path':name,'type':'link' if source.is_symlink() else 'file','sha256':digest,
                       'mode':int(row.get('mode',stat.S_IMODE(source.lstat().st_mode)))}
            if source.is_symlink():canonical['link']=os.readlink(source)
            if name in entries and entries[name]!=canonical:raise netbsd.BackendError('Conflicting component file: '+name)
            entries[name]=canonical
    if not components:raise netbsd.BackendError('At least one installed source-bound component is required')
    actual=set()
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            path=Path(parent)/name
            if path.is_symlink() or not path.is_dir():actual.add(path.relative_to(root).as_posix())
    if actual!=set(entries):raise netbsd.BackendError('Package overlay differs from exact manifests; unlisted='+str(sorted(actual-set(entries))[:8])+' missing='+str(sorted(set(entries)-actual)[:8]))
    return root,entries,components


def _layout(profile):
    storage=profile.get('storage',{});result=[];sector=32768
    for key,label,kind,minimum in (('efi_mib','daos-efi','efi',64),('root_mib','daos-root','ffs',128),
                                  ('state_mib','daos-state','ffs',16),('photos_mib','daos-photos','ffs',16)):
        value=storage.get(key,0)
        if isinstance(value,bool) or not isinstance(value,int) or value<0 or value>65536:raise netbsd.BackendError('Bounded integer storage size required: '+key)
        if not value:
            if key in ('efi_mib','root_mib'):raise netbsd.BackendError('EFI and root partitions are required')
            continue
        if value<minimum:raise netbsd.BackendError('Partition too small: '+key)
        sectors=value*2048
        result.append({'index':len(result)+1,'label':label,'kind':kind,'mib':value,
                       'start_sector':sector,'sectors':sectors,'offset':sector*512,'bytes':sectors*512})
        sector+=sectors
        sector=(sector+8191)//8192*8192
    return result,(sector+2048)*512


def _timestamp(spec):
    value=1793577600
    for line in spec['mkconf_path'].read_text().splitlines():
        match=re.fullmatch(r'MKREPRO_TIMESTAMP\s*=\s*([0-9]+)',line.strip())
        if match:value=int(match[1])
    return value


def _put(root,name,data,mode=0o644):
    target=root/_relative(name)
    for parent in target.parents:
        if parent==root.parent:break
        if parent.is_symlink():raise netbsd.BackendError('Generated image file would follow a symlink: '+name)
    if target.is_symlink() or (target.exists() and not target.is_file()):raise netbsd.BackendError('Image customization type conflict: '+name)
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(mode)
    return target


def _merge(source,destination):
    destination.mkdir(parents=True,exist_ok=True)
    for item in source.iterdir():
        target=destination/item.name
        if item.is_symlink():
            if os.path.lexists(target):raise netbsd.BackendError('Filesystem staging conflict: '+str(target))
            target.symlink_to(os.readlink(item))
        elif item.is_dir():
            if target.is_symlink() or (target.exists() and not target.is_dir()):raise netbsd.BackendError('Filesystem directory conflict')
            _merge(item,target)
        elif item.is_file():
            if target.is_symlink() or (target.exists() and not target.is_file()):raise netbsd.BackendError('Filesystem file conflict')
            shutil.copy2(item,target)
        else:raise netbsd.BackendError('Package/base staging must contain ordinary files, directories or links')


def _tree_rows(root,uid=0,gid=0):
    rows={'.':f'. type=dir uid={uid} gid={gid} mode=0755'}
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            path=Path(parent)/name;relative=path.relative_to(root).as_posix();mode=stat.S_IMODE(path.lstat().st_mode)
            kind='link' if path.is_symlink() else 'dir' if path.is_dir() else 'file'
            row='./'+_escape(relative)+f' type={kind} uid={uid} gid={gid} mode={mode:04o}'
            if kind=='link':row+=' link='+_escape(os.readlink(path))
            rows['./'+relative]=row
    return rows


def _copy_region(source,raw,offset):
    with source.open('rb') as input_file,raw.open('r+b') as output:
        output.seek(offset)
        for block in iter(lambda:input_file.read(4*1024*1024),b''):
            if block.count(0)==len(block):output.seek(len(block),1)
            else:output.write(block)


def _inspect(raw,layout,root_entries,boot_files):
    table=gpt(raw)
    bylabel={p['label']:p for p in table['partitions']}
    for expected in layout:
        actual=bylabel.get(expected['label'])
        if not actual or any(actual[k]!=expected[k] for k in ('index','start_sector','sectors')):
            raise FormatError('Actual GPT differs from requested partition layout')
        if actual['type']!=(EFI_GUID if expected['kind']=='efi' else FFS_GUID):raise FormatError('Unexpected GPT filesystem type')
    if len(bylabel)!=len(layout):raise FormatError('Unexpected GPT partitions')
    files=0
    with FAT32(raw,bylabel['daos-efi']['offset'],bylabel['daos-efi']['bytes']) as fat:
        for name,digest in boot_files.items():
            if fat.sha256(name)!=digest:raise FormatError('FAT boot payload mismatch: '+name)
    readers={}
    try:
        for partition in table['partitions']:
            if partition['label']!='daos-efi':readers[partition['label']]=FFS2(raw,partition['offset'],partition['bytes'])
        root_inode=readers['daos-root'].inode(2)
        if (root_inode['uid'],root_inode['gid'],root_inode['mode']&0o777)!=(0,0,0o755):
            raise FormatError('The filesystem root must be root:wheel mode 0755')
        for name,row in root_entries.items():
            label='daos-root';inside=name
            if name.startswith(PHOTOS_MOUNT+'/') and 'daos-photos' in readers:
                label='daos-photos';inside=name[len(PHOTOS_MOUNT)+1:]
            elif name.startswith('var/') and 'daos-state' in readers:
                label='daos-state';inside=name[4:]
            if readers[label].sha256(inside,follow=row['type']!='link')!=row['sha256']:
                raise FormatError('Installed image payload mismatch: '+name)
            files+=1
        for label in ('daos-state','daos-photos'):
            if label in readers:readers[label].inode(2)
    finally:
        for reader in readers.values():reader.close()
    return dict(table,verified_payload_files=files,verified_fat_files=len(boot_files),
                filesystem_check='Read-only GPT CRCs, FAT chains, NetBSD FFSv2 inode/path/block traversal and payload hashes',
                root_owner={'uid':root_inode['uid'],'gid':root_inode['gid'],'mode':root_inode['mode']&0o777},
                full_fsck_completed=False,target_boot_validated=False)


def build(profile_path,workspace):
    """Build and inspect an image; return a receipt only after all checks pass."""
    spec=netbsd._profile_inputs(profile_path);workspace=netbsd._workspace(workspace)
    with netbsd._locked(workspace):
        netbsd._configured(workspace,spec);netbsd._verify_sources(workspace,spec['lock_hash'])
        states={}
        for stage in ('tools','distribution','kernel'):
            receipt=netbsd._read_json(workspace/'state'/(stage+'.json'))
            if receipt.get('status')!='success' or receipt.get('input_hash')!=spec['input_hash']:
                raise netbsd.BackendError('Successful matching source-built stage required: '+stage)
            netbsd._cached_artifacts(receipt,workspace);states[stage]=netbsd._hash(workspace/'state'/(stage+'.json'))
        package_root,package_entries,components=_packages(workspace)
        layout,total=_layout(spec['profile']);labels={p['label'] for p in layout}
        ui='etc/rc.d/daos_ui' in package_entries
        if ui and not {'daos-state','daos-photos'}<=labels:raise netbsd.BackendError('UI image needs explicit state and photos partitions')
        tools={name:_tool(workspace,name) for name in ('nbmakefs','nbgpt','nbmtree','nbpwd_mkdb')}
        tools['nbfdisk']=_tool(workspace,'aarch64--netbsd-fdisk')
        destination=workspace/'destdir';kernel=Path(netbsd._paths(workspace,spec)['kernel_path'])
        kernel_elf=_elf(kernel,False);base_elf={name:_elf(destination/name) for name in ('bin/sh','sbin/init')}
        bootloader=destination/'usr/mdec/bootaa64.efi';efi=_pe(bootloader)
        board=spec['profile']['target'].get('board')
        if board not in ('raspberrypi4','qemu-virt'):raise netbsd.BackendError('Explicit Pi4 or QEMU board required')
        if board=='raspberrypi4':
            tools['objcopy']=_tool(workspace,'aarch64--netbsd-objcopy')
            tools['mkubootimage']=_tool(workspace,'nbmkubootimage')
        kernel_img=kernel.with_name(kernel.name+'.img')
        if board=='raspberrypi4':
            data=_regular(kernel_img).read_bytes()
            if data[56:60]!=b'ARMd':raise netbsd.BackendError('Source-built ARM64 netbsd.img header missing')
        runid=spec['profile']['name']+'-'+uuid.uuid4().hex[:12]
        work=netbsd._inside(workspace,workspace/'images'/runid);work.mkdir(parents=True)
        receipt_path=work/'image-evidence.json'
        evidence={'format':FORMAT,'status':'building','executed':True,'input_hash':spec['input_hash'],
                  'source_lock_sha256':spec['lock_hash'],'base_stage_receipts':states,'components':components,
                  'assembler_sha256':netbsd._hash(Path(__file__)),'format_reader_sha256':netbsd._hash(Path(__file__).with_name('image_formats.py')),
                  'profile':spec['profile'],'profile_path':str(spec['profile_path']),
                  'target_boot_validated':False,'physical_pi4_validated':False,'ui_runtime_validated':False,
                  'paths':{'work':str(work)},'kernel':{'path':str(kernel),'sha256':netbsd._hash(kernel),'elf':kernel_elf},
                  'base_elf':base_elf,'efi':efi,'partition_layout':layout,
                  'mounts':{'state':'/var' if 'daos-state' in labels else None,
                            'photos':'/'+PHOTOS_MOUNT if 'daos-photos' in labels else None},
                  'runtime_account':{'name':'daos-camera','uid':1000,'gid':1000},
                  'ui_requested':ui,'tools':{name:netbsd._hash(Path(path).resolve()) for name,path in tools.items()}}
        netbsd._write_json(receipt_path,evidence)
        try:
            root=work/'root-overlay';shutil.copytree(package_root,root,symlinks=True)
            boot=work/'boot';boot.mkdir()
            provenance_files={}
            for source,relative in (
                    (workspace/'src/distrib/notes/common/legal.common','usr/share/daos/licenses/NetBSD-legal.common'),
                    (workspace/'src/doc/3RDPARTY','usr/share/daos/licenses/NetBSD-3RDPARTY'),
                    (spec['lock_path'],'usr/share/daos/provenance/'+spec['lock_path'].name),
                    (Path(__file__).resolve().parents[3]/'licenses/bsd-buildroot-inventory.json',
                     'usr/share/daos/provenance/bsd-buildroot-inventory.json')):
                installed=_put(root,relative,_regular(source).read_bytes())
                provenance_files[relative]={'source':str(source),'sha256':netbsd._hash(installed)}
            evidence['provenance_files']=provenance_files
            _put(boot,'EFI/BOOT/bootaa64.efi',bootloader.read_bytes(),0o755)
            _put(root,'netbsd',kernel.read_bytes(),0o755)
            bootcfg=('menu=Boot DaOS:boot netbsd root=NAME=daos-root console=fb\n'
                     'menu=Boot DaOS serial diagnostics:boot netbsd root=NAME=daos-root console=com -v\n'
                     'menu=Single user console:boot netbsd root=NAME=daos-root -s\n'
                     'menu=Boot prompt:prompt\ndefault=1\ntimeout=3\nclear=1\n')
            _put(root,'boot.cfg',bootcfg.encode());_put(boot,'boot.cfg',bootcfg.encode())
            if board=='raspberrypi4':
                # The GPU cannot consume ELF. Reproduce the upstream raw-image
                # conversion and require byte identity with the kernel build.
                regenerated=work/'kernel-regenerated.img';binary=work/'kernel-regenerated.bin'
                _run([tools['objcopy'],'-S','-O','binary',kernel,binary],workspace,work/'kernel-objcopy.log')
                _run([tools['mkubootimage'],'-f','arm64','-u','-a','0x200000','-t',str(_timestamp(spec)),binary,regenerated],
                     workspace,work/'kernel-arm64-image.log')
                if netbsd._hash(regenerated)!=netbsd._hash(kernel_img):
                    raise netbsd.BackendError('Raw ARM64 image does not reproduce from the verified kernel ELF')
                firmware=workspace/'src/external/broadcom/rpi-firmware/dist'
                firmware_names=('LICENCE.broadcom','bootcode.bin','fixup4cd.dat','fixup4.dat','start4cd.elf','start4.elf')
                for name in firmware_names:_put(boot,name,_regular(firmware/name).read_bytes())
                dtbs=destination/'boot/dtb'
                _regular(dtbs/'broadcom/bcm2711-rpi-4-b.dtb')
                shutil.copytree(dtbs,boot/'dtb',symlinks=False)
                _put(boot,'netbsd.img',kernel_img.read_bytes(),0o755)
                _put(boot,'cmdline.txt',b'root=NAME=daos-root console=fb\n')
                _put(boot,'config.txt',b'upstream_kernel=1\narm_64bit=1\nos_prefix=dtb/broadcom/\ncmdline=../../cmdline.txt\nkernel=/netbsd.img\nkernel_address=0x200000\nenable_uart=1\nforce_turbo=0\nhdmi_force_hotplug=1\nframebuffer_width=1280\nframebuffer_height=720\n')
                evidence['pi_firmware']={'source':'NetBSD source-lock src.tgz external/broadcom/rpi-firmware/dist',
                                         'files':{name:netbsd._hash(firmware/name) for name in firmware_names},
                                         'raw_kernel_sha256':netbsd._hash(kernel_img),'board_dtb_sha256':netbsd._hash(dtbs/'broadcom/bcm2711-rpi-4-b.dtb')}
            fstab=['NAME=daos-root / ffs rw,noatime 1 1','NAME=daos-efi /boot msdos rw 0 0',
                   'ptyfs /dev/pts ptyfs rw 0 0','tmpfs /tmp tmpfs rw,-m1777,-s128m 0 0',
                   'tmpfs /var/shm tmpfs rw,-m1777,-s64m 0 0']
            if 'daos-state' in labels:fstab.append('NAME=daos-state /var ffs rw,noatime 1 2')
            if 'daos-photos' in labels:fstab.append('NAME=daos-photos /'+PHOTOS_MOUNT+' ffs rw,noatime 1 2')
            # Parent mounts precede child mounts, including /var/shm.
            fstab.sort(key=lambda row:(row.split()[1].count('/'),row.split()[1]))
            _put(root,'etc/fstab',('\n'.join(fstab)+'\n').encode())
            rc=_regular(destination/'etc/rc.conf').read_text()
            rc+='\n# DaOS private source-built image\nrc_configured=YES\nhostname='+spec['profile']['name']+'\nno_swap=YES\nsavecore=NO\nsshd=NO\ndhcpcd=YES\ndhcpcd_flags="-b"\nntpd=NO\ndevpubd=YES\n'
            rc+='daos_ui='+('YES' if ui else 'NO')+'\n'
            if ui:
                rc+='wscons=YES\n'
                tty_lines=[]
                for line in _regular(destination/'etc/ttys').read_text().splitlines():
                    match=re.match(r'^(console|constty|ttyE0|ttyE1|plcom0)\s+("[^"]+")\s+(\S+)\s+(on|off)(.*)$',line)
                    if match:
                        device,getty,terminal,status,tail=match.groups()
                        status='on' if device in ('ttyE1','plcom0') else 'off'
                        line=f'{device}\t{getty}\t{terminal}\t{status}{tail}'
                    tty_lines.append(line)
                if not any(line.startswith('plcom0\t') for line in tty_lines):
                    tty_lines.append('plcom0\t"/usr/libexec/getty std.115200"\tvt100\ton secure')
                _put(root,'etc/ttys',('\n'.join(tty_lines)+'\n').encode())
            if 'usr/sbin/daosctl' in package_entries:
                rc+='daos_image_check=YES\n'
                check=b'''#!/bin/sh
# PROVIDE: daos_image_check
# REQUIRE: mountall
# BEFORE: LOGIN daos_ui
. /etc/rc.subr
name=daos_image_check
rcvar=$name
start_cmd=daos_image_check_start
stop_cmd=:
daos_image_check_start() {
    echo DAOS_IMAGE_CHECK_BEGIN
    echo DAOS_IMAGE_CHECK_SCOPE identity-and-native-smoke
    /usr/sbin/daosctl info || return 1
    /usr/sbin/daosctl components || return 1
    while read -r expected relative; do
        actual=$(sha256 -q "/$relative") || return 1
        [ "$actual" = "$expected" ] || { echo "DAOS_BOOT_FILE_CHANGED $relative"; return 1; }
    done < /usr/share/daos/boot-files.sha256
    if [ -x /usr/libexec/daos/tests/hf-sha256-tests ]; then
        /usr/libexec/daos/tests/hf-sha256-tests || return 1
    fi
    echo DAOS_IMAGE_CHECK_PASSED
}
load_rc_config $name
run_rc_command "$1"
'''
                _put(root,'etc/rc.d/daos_image_check',check,0o555)
            _put(root,'etc/rc.conf',rc.encode())
            if (re.search(r'^MKYP\s*=\s*no\s*$',spec['mkconf_path'].read_text(),re.M)
                    and not (destination/'etc/rc.d/ypbind').exists()):
                adjusted=[]
                for name in ('etc/rc.d/mountall','etc/rc.d/amd'):
                    source=destination/name
                    if not source.is_file():continue
                    original=source.read_text();lines=[]
                    for line in original.splitlines():
                        if line.startswith('# REQUIRE:'):line=re.sub(r'\bypbind\b','',line)
                        lines.append(line)
                    updated='\n'.join(lines)+'\n'
                    if updated!=original:_put(root,name,updated.encode(),0o555);adjusted.append(name)
                evidence['rc_dependency_adjustments']={'unavailable_provider':'ypbind','world_setting':'MKYP=no','files':adjusted}
            group=_regular(destination/'etc/group').read_text()
            master=_regular(destination/'etc/master.passwd').read_text()
            if any(line.split(':')[0]=='daos-camera' or (len(line.split(':'))>2 and line.split(':')[2]=='1000') for line in master.splitlines()):
                raise netbsd.BackendError('Camera UID conflicts with source base account')
            if any(line.split(':')[0]=='daos-camera' or (len(line.split(':'))>2 and line.split(':')[2]=='1000') for line in group.splitlines()):
                raise netbsd.BackendError('Camera GID conflicts with source base group')
            _put(root,'etc/group',(group.rstrip()+'\ndaos-camera:*:1000:\n').encode())
            _put(root,'etc/master.passwd',(master.rstrip()+'\ndaos-camera:*:1000:1000::0:0:DaOS camera:/var/DaOS/camera:/bin/sh\n').encode(),0o600)
            _run([tools['nbpwd_mkdb'],'-L','-p','-V','1','-d',root,root/'etc/master.passwd'],workspace,work/'pwd-mkdb.log')
            for name in ('etc/passwd','etc/pwd.db','etc/spwd.db','etc/master.passwd'):_regular(root/name)
            for name in ('boot','var','dev/pts','tmp','var/shm',PHOTOS_MOUNT):
                target=root/name
                if target.is_symlink():raise netbsd.BackendError('Mountpoint cannot be a symlink')
                target.mkdir(parents=True,exist_ok=True)
            (root/'var/DaOS/camera').chmod(0o700)
            if 'usr/sbin/daosctl' in package_entries:
                essential=('usr/sbin/daosctl','usr/libexec/daos/hf-camerad','usr/libexec/daos/hf-native-worker',
                           'usr/libexec/daos/tests/hf-core-tests','usr/libexec/daos/tests/hf-daemon-tests',
                           'usr/libexec/daos/tests/hf-sha256-tests','usr/libexec/daos/tests/hf-calibration-tests',
                           'usr/libexec/daos/camera-app.py','usr/libexec/daos/camera-session.py',
                           'etc/rc.d/daos_ui','etc/rc.d/daos_pkg_finalize')
                checks=[netbsd._hash(root/name)+' '+name for name in essential if (root/name).is_file()]
                _put(root,'usr/share/daos/boot-files.sha256',('\n'.join(checks)+'\n').encode())
                evidence['boot_check_scope']={'name':'identity-and-native-smoke','essential_file_hashes':len(checks),
                                              'native_test':'hf-sha256-tests if installed','full_daosctl_verify':False}
            # Normalize the original NO_ROOT metadata with the same NetBSD mtree
            # that produced the base; device nodes are declarations, not mknod.
            devices=subprocess.run(['/bin/sh',str(_regular(destination/'dev/MAKEDEV')),'-s','all'],cwd=work,
                                   env=netbsd._build_environment(workspace),capture_output=True,text=True,check=True,timeout=120)
            device_text='\n'.join('./dev/'+line[2:] if line.startswith('./') else line for line in devices.stdout.splitlines())
            combined=_regular(destination/'METALOG').read_text()+'\n'+device_text+'\n'
            base_spec=work/'base.mtree'
            with base_spec.open('xb') as normalized, (work/'base-mtree.log').open('xb') as errors:
                result=subprocess.run([tools['nbmtree'],'-CS','-k','all','-N',str(destination/'etc')],
                                      input=combined.encode(),stdout=normalized,stderr=errors,
                                      env=netbsd._build_environment(workspace),timeout=180)
            if result.returncode:raise netbsd.BackendError('Base mtree normalization failed: '+str(work/'base-mtree.log'))
            base_rows={}
            for line in base_spec.read_text().splitlines():
                if line and not line.startswith('#'):
                    name=line.split()[0];base_rows[name]=line
            overlay_rows=_tree_rows(root);rows=dict(base_rows);rows.update(overlay_rows)
            # Reuse the distribution's ownership for existing directories.
            for name,line in overlay_rows.items():
                if ' type=dir ' in line and name in base_rows:rows[name]=base_rows[name]
            # NetBSD METALOG's synthesized root can be only "type=dir optional".
            # Explicit ownership must survive normalization on an unprivileged host.
            rows['.']='. type=dir uid=0 gid=0 mode=0755'
            rows['./var/DaOS/camera']='./var/DaOS/camera type=dir uid=1000 gid=1000 mode=0700'
            state=work/'state';photos=work/'photos'
            if 'daos-state' in labels:
                _merge(destination/'var',state);_merge(root/'var',state)
                state_rows={'.':'. type=dir uid=0 gid=0 mode=0755'}
                for name,line in rows.items():
                    if name.startswith('./var/'):
                        new='./'+name[6:];state_rows[new]=new+line[len(name):]
                state_rows.update({name:line for name,line in _tree_rows(state).items() if name not in state_rows})
                state_rows['./DaOS/camera']='./DaOS/camera type=dir uid=1000 gid=1000 mode=0700'
                rows={name:line for name,line in rows.items() if not name.startswith('./var/')}
                (work/'state.mtree').write_text('\n'.join(state_rows[k] for k in sorted(state_rows))+'\n')
            if 'daos-photos' in labels:
                photos.mkdir();(work/'photos.mtree').write_text('. type=dir uid=1000 gid=1000 mode=0700\n')
            specfile=work/'root.mtree';specfile.write_text('\n'.join(rows[k] for k in sorted(rows))+'\n')
            own_entries={}
            for parent,dirs,files in os.walk(root,followlinks=False):
                for name in dirs+files:
                    path=Path(parent)/name
                    if path.is_symlink() or path.is_file():
                        rel=path.relative_to(root).as_posix();kind='link' if path.is_symlink() else 'file'
                        own_entries[rel]={'type':kind,'sha256':hashlib.sha256(os.readlink(path).encode()).hexdigest() if kind=='link' else netbsd._hash(path)}
            own_entries['bin/sh']={'type':'file','sha256':netbsd._hash(destination/'bin/sh')}
            own_entries['sbin/init']={'type':'file','sha256':netbsd._hash(destination/'sbin/init')}
            boot_files={path.relative_to(boot).as_posix():netbsd._hash(path) for path in boot.rglob('*') if path.is_file()}
            timestamp=_timestamp(spec)
            for part in layout:
                image=work/(part['label']+'.fs');part['filesystem_image']=str(image)
                if part['kind']=='efi':
                    command=[tools['nbmakefs'],'-t','msdos','-T',str(timestamp),'-s',str(part['bytes']),
                             '-o','fat_type=32,sectors_per_cluster=1,volume_label=DAOS_BOOT',str(image),str(boot)]
                else:
                    name=part['label'];tree=destination if name=='daos-root' else state if name=='daos-state' else photos
                    metadata=specfile if name=='daos-root' else work/('state.mtree' if name=='daos-state' else 'photos.mtree')
                    command=[tools['nbmakefs'],'-rx','-B','le','-N',str(destination/'etc'),'-t','ffs','-T',str(timestamp),
                             '-s',str(part['bytes']),'-o','version=2,bsize=16384,fsize=2048,density=8192',
                             '-F',str(metadata),str(image),str(tree)]
                    if name=='daos-root':command.append(str(root))
                _run(command,workspace,work/(part['label']+'-makefs.log'))
                if image.stat().st_size!=part['bytes']:raise netbsd.BackendError('Filesystem image size differs from partition')
            raw=work/(spec['profile']['name']+'.img')
            with raw.open('xb') as output:output.truncate(total)
            for part in layout:_copy_region(Path(part['filesystem_image']),raw,part['offset'])
            _run([tools['nbgpt'],str(raw),'create','-p','32'],workspace,work/'gpt-create.log')
            for part in layout:
                _run([tools['nbgpt'],str(raw),'add','-i',str(part['index']),'-b',str(part['start_sector']),
                      '-s',str(part['sectors']),'-t',part['kind'],'-l',part['label']],workspace,work/('gpt-add-'+str(part['index'])+'.log'))
            _run([tools['nbgpt'],str(raw),'set','-a','bootme','-i','2'],workspace,work/'gpt-root-bootme.log')
            if board=='raspberrypi4':
                first=layout[0]
                _run([tools['nbfdisk'],'-f','-g','-u','-0','-a','-s',f"12/{first['start_sector']}/{first['sectors']}",'-F',str(raw)],workspace,work/'hybrid-fat-mbr.log')
                _run([tools['nbfdisk'],'-f','-g','-u','-3','-s',f"238/1/{first['start_sector']-1}",'-F',str(raw)],workspace,work/'hybrid-protective-mbr.log')
            evidence['validation']=_inspect(raw,layout,own_entries,boot_files)
            # Bind the actual copied contents, not just their staging intent.
            if _packages(workspace)[1]!=package_entries:raise netbsd.BackendError('Package inputs changed while image was built')
            netbsd._configured(workspace,spec)
            for stage in states:
                netbsd._cached_artifacts(netbsd._read_json(workspace/'state'/(stage+'.json')),workspace)
            evidence.update(status='assembled-not-boot-validated',image={'path':str(raw),'size':raw.stat().st_size,'sha256':netbsd._hash(raw)},
                            overlay_payloads=own_entries,boot_payloads=boot_files,finished_at=netbsd._now())
            netbsd._write_json(receipt_path,evidence)
            netbsd._write_json(workspace/'state/image.json',dict(evidence,receipt=str(receipt_path)))
            return dict(evidence,receipt=str(receipt_path))
        except BaseException as error:
            evidence.update(status='failed',error_type=type(error).__name__,error=str(error),finished_at=netbsd._now())
            netbsd._write_json(receipt_path,evidence);raise

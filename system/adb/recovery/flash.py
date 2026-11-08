#!/usr/local/bin/python3.12
"""Bounded SD repair over an already authenticated ADB rescue shell.

This is not a network server and does not grant authorization. The ADB daemon
must enforce its provisioned RSA keys. Native writes require a verified RAM
root, an installed root-owned card identity, and unmounted target providers.
Only root/state partition-relative extents are accepted. No GPT/whole-card,
calibration, or photos writes are implemented.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import uuid
import zlib

FORMAT = 'DaOS-recovery-write-v1'
IDENTITY = Path('/etc/daos/recovery-identity.json')
MARKER = Path('/run/daos-recovery/ram-root.json')
WORK = Path('/DaOS-recovery-ram/flash')
ROLES = {'root', 'state'}
CHUNK = 1024 * 1024
UFS_GUID = '516e7cb6-6ecf-11d6-8ff8-00022d09712b'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Expected lowercase SHA256')
    return value


def secure_path(path):
    path = Path(path)
    for part in (path, *path.parents):
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Untrusted or redirected recovery path: '+str(part))
    return path


def read_at(fd, offset, count):
    os.lseek(fd, offset, os.SEEK_SET)
    data = bytearray()
    while len(data) < count:
        block = os.read(fd, min(CHUNK, count-len(data)))
        if not block:
            raise ValueError('Short provider/image read')
        data.extend(block)
    return bytes(data)


def hash_at(fd, offset, count):
    result = hashlib.sha256()
    while count:
        n = min(CHUNK, count)
        result.update(read_at(fd, offset, n))
        offset += n
        count -= n
    return result.hexdigest()


def write_at(fd, offset, data):
    os.lseek(fd, offset, os.SEEK_SET)
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError('Short provider write')
        view = view[n:]


def save(path, value):
    tmp = path.with_name(path.name+'.tmp')
    with tmp.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def lock(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory/'writer.lock'
    if path.is_symlink():
        raise ValueError('Redirected lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, 0)
            os.write(fd, b'0')
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def validate_request(request, partition_bytes, sector_size):
    if request.get('format') != FORMAT or not re.fullmatch('[a-f0-9]{32}', str(request.get('operation', ''))):
        raise ValueError('Invalid request/operation identity')
    if request.get('role') not in ROLES:
        raise ValueError('Only root/state partition repair is supported')
    image = request.get('image')
    if not isinstance(image, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,120}', image) or image in ('.', '..'):
        raise ValueError('Image must be a local upload basename')
    checked_hash(request.get('image_sha256'))
    image_bytes = request.get('image_bytes')
    if type(image_bytes) is not int or image_bytes <= 0:
        raise ValueError('Invalid image length')
    extents = request.get('extents')
    if not isinstance(extents, list) or not 1 <= len(extents) <= 65536:
        raise ValueError('Invalid extent list')
    end = 0
    total = 0
    for extent in extents:
        offset, source, count = (extent.get(k) for k in ('target_offset', 'image_offset', 'bytes'))
        if any(type(v) is not int for v in (offset, source, count)):
            raise ValueError('Extent coordinates must be integers')
        if offset < end or source < 0 or count <= 0 or offset+count > partition_bytes or source+count > image_bytes:
            raise ValueError('Extent overlaps or escapes partition/image')
        if any(v % sector_size for v in (offset, source, count)):
            raise ValueError('Unaligned repair extent')
        checked_hash(extent.get('before_sha256'))
        checked_hash(extent.get('sha256'))
        end = offset+count
        total += count
    return total


class FileBackend:
    """Explicit file-only test sandbox. Never accepts native device nodes."""
    def __init__(self, root, partitions, sector_size=512):
        self.root = Path(root).resolve(strict=True)
        self.parts = {k: Path(v) for k, v in partitions.items()}
        self.sector_size = sector_size
        self.work = self.root/'receipts'

    def local(self, path):
        path = Path(path)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Redirected sandbox file')
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('Sandbox requires ordinary files inside its directory')
        return path

    def prove(self, role):
        if role not in ROLES or role not in self.parts:
            raise ValueError('Unknown repair partition')
        path = self.local(self.parts[role])
        return {'path': path, 'bytes': path.stat().st_size, 'identity': str(path)}

    def image(self, name):
        return self.local(self.root/name)


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=15)


def parse_gpt(fd, disk_bytes, sector_size):
    """Verify both GPT copies; this tool deliberately cannot repair GPT."""
    def header(lba):
        raw = read_at(fd, lba*sector_size, sector_size)
        if raw[:8] != b'EFI PART':
            raise ValueError('Missing GPT header')
        size = struct.unpack_from('<I', raw, 12)[0]
        if not 92 <= size <= sector_size:
            raise ValueError('Invalid GPT header length')
        copy = bytearray(raw[:size]); wanted = struct.unpack_from('<I', raw, 16)[0]
        struct.pack_into('<I', copy, 16, 0)
        if zlib.crc32(copy) != wanted:
            raise ValueError('GPT header CRC mismatch')
        current, backup, first, last = struct.unpack_from('<QQQQ', raw, 24)
        table, count, stride, crc = struct.unpack_from('<QIII', raw, 72)
        if current != lba or count != 128 or stride != 128 or table*sector_size+count*stride > disk_bytes:
            raise ValueError('Unexpected GPT layout')
        entries = read_at(fd, table*sector_size, count*stride)
        if zlib.crc32(entries) != crc:
            raise ValueError('GPT entry CRC mismatch')
        return {'current':current,'backup':backup,'first':first,'last':last,
                'disk_uuid':str(uuid.UUID(bytes_le=raw[56:72])),'entries':entries}
    front = header(1)
    back = header(disk_bytes//sector_size-1)
    if front['backup'] != back['current'] or back['backup'] != 1 or any(front[k] != back[k] for k in ('disk_uuid','first','last','entries')):
        raise ValueError('GPT copies disagree')
    parts = {}
    for i in range(128):
        entry = front['entries'][i*128:(i+1)*128]
        if not any(entry[:16]):
            continue
        start, end = struct.unpack_from('<QQ', entry, 32)
        if not front['first'] <= start <= end <= front['last']:
            raise ValueError('Partition outside usable GPT area')
        parts[i+1] = {'uuid':str(uuid.UUID(bytes_le=entry[16:32])), 'start_lba':start,
                      'sectors':end-start+1,'type':str(uuid.UUID(bytes_le=entry[:16])),
                      'label':entry[56:128].decode('utf-16le').rstrip('\0')}
    intervals=sorted((part['start_lba'],part['start_lba']+part['sectors']) for part in parts.values())
    if any(current[0]<previous[1] for previous,current in zip(intervals,intervals[1:])):
        raise ValueError('Overlapping GPT partitions cannot be repaired with this tool')
    return front['disk_uuid'], parts


class NativeBackend:
    def __init__(self):
        self.work = WORK
        self.sector_size = 512

    def prove(self, role):
        if not sys.platform.startswith('freebsd') or os.geteuid() != 0:
            raise ValueError('Native FreeBSD root required')
        if role not in ROLES:
            raise ValueError('Only root/state is supported')
        marker = json.loads(secure_path(MARKER).read_text())
        if marker.get('format') != 'DaOS-ram-rescue-v1' or marker.get('flash_enabled') is not True:
            raise ValueError('RAM rescue marker is missing or disabled')
        identity = json.loads(secure_path(IDENTITY).read_text())
        if identity.get('format') != 'DaOS-recovery-identity-v1' or set(identity.get('partitions', {})) != ROLES:
            raise ValueError('Installed card identity is invalid')
        disk = identity['disk']
        if not re.fullmatch(r'/dev/(?:mmcsd|sdda|da)[0-9]+', disk):
            raise ValueError('Unexpected fixed card provider')
        self.sector_size = identity['sector_size']
        if self.sector_size not in (512,4096) or type(identity['disk_bytes']) is not int:
            raise ValueError('Invalid installed card geometry')
        rows = [line.split() for line in command(['/sbin/mount','-p']).splitlines() if line.strip()]
        if any(len(row) != 6 for row in rows):
            raise ValueError('Unexpected mount table')
        root = [row for row in rows if row[1] == '/']
        if len(root) != 1:
            raise ValueError('No unique active root')
        if root[0][2] != 'tmpfs':
            md = re.fullmatch('/dev/md([0-9]+)', root[0][0])
            if not md or root[0][2] != 'ufs':
                raise ValueError('Flash requires a RAM root')
            desc = command(['/sbin/mdconfig','-l','-v','-u',md[1]]).split()
            if len(desc) < 2 or desc[0] != 'md'+md[1] or desc[1] not in ('preload','malloc'):
                raise ValueError('Root memory disk is not resident RAM')
        mounts = [row for row in rows if row[1] == '/DaOS-recovery-ram']
        if len(mounts) != 1 or mounts[0][2] != 'tmpfs':
            raise ValueError('/DaOS-recovery-ram must be tmpfs for upload and receipts')
        actual_bytes = int(command(['/usr/sbin/diskinfo',disk]).split()[2])
        if actual_bytes != identity['disk_bytes']:
            raise ValueError('Card size changed')
        fd = os.open(disk, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            disk_uuid, parts = parse_gpt(fd,actual_bytes,self.sector_size)
        finally:
            os.close(fd)
        if disk_uuid != identity['disk_uuid']:
            raise ValueError('GPT disk UUID changed')
        aliases = {}
        for line in command(['/sbin/geom','label','status','-s']).splitlines():
            fields = line.split()
            if len(fields) != 3:
                raise ValueError('Unexpected GEOM label table')
            aliases['/dev/'+fields[0]] = '/dev/'+fields[2]
        def resolve(source):
            seen = set()
            while source in aliases:
                if source in seen:raise ValueError('GEOM alias cycle')
                seen.add(source);source=aliases[source]
            return source
        for name, expected in identity['partitions'].items():
            index = expected['index']
            if type(index) is not int or index not in parts:
                raise ValueError('Missing fixed partition index')
            part = parts[index]
            if part['type'] != UFS_GUID or part['label'] != 'DaOS-'+name or any(part[k] != expected[k] for k in ('uuid','start_lba','sectors')):
                raise ValueError('Fixed partition identity changed')
            provider = disk+'p'+str(index)
            if any(resolve(row[0]) == provider for row in rows):
                raise ValueError('A repair partition is mounted; refusing all writes')
        expected = identity['partitions'][role]
        path = Path(disk+'p'+str(expected['index']))
        if path.is_symlink() or not stat.S_ISCHR(path.stat().st_mode):
            raise ValueError('Unexpected direct partition device')
        size = expected['sectors']*self.sector_size
        if int(command(['/usr/sbin/diskinfo',str(path)]).split()[2]) != size:
            raise ValueError('Partition device geometry changed')
        return {'path':path,'bytes':size,'identity':{'disk_uuid':disk_uuid,**expected}}

    def image(self, name):
        path = secure_path(WORK/'incoming'/name)
        if not path.is_file():raise ValueError('Upload must be an ordinary RAM file')
        return path


def inspect(request, backend, target_fd=None):
    target = backend.prove(request.get('role'))
    total = validate_request(request,target['bytes'],backend.sector_size)
    image = backend.image(request['image'])
    fd = os.open(image,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    own_target = target_fd is None
    target_fd = os.open(target['path'],os.O_RDONLY | getattr(os,'O_NOFOLLOW',0)) if own_target else target_fd
    try:
        if os.fstat(fd).st_size != request['image_bytes'] or hash_at(fd,0,request['image_bytes']) != request['image_sha256']:
            raise ValueError('Uploaded image length/SHA256 changed')
        status = []
        for extent in request['extents']:
            if hash_at(fd,extent['image_offset'],extent['bytes']) != extent['sha256']:
                raise ValueError('Extent image SHA256 mismatch')
            current = hash_at(target_fd,extent['target_offset'],extent['bytes'])
            if current == extent['sha256']:status.append('already-applied')
            elif current == extent['before_sha256']:status.append('pending')
            else:raise ValueError('Target preimage mismatch; a partial/torn extent needs a new repair plan')
        return {'target':target,'image':image,'statuses':status,'bytes_to_write':sum(e['bytes'] for e,s in zip(request['extents'],status) if s=='pending'),'extent_bytes':total}
    finally:
        os.close(fd)
        if own_target:os.close(target_fd)


def execute(request, backend, *, apply=False):
    if isinstance(backend,NativeBackend):
        # Prove the environment before creating even RAM lock/receipt files.
        backend.prove(request.get('role'))
        backend.work.mkdir(mode=0o700,parents=True,exist_ok=True)
        secure_path(backend.work)
    with lock(backend.work):
        plan=inspect(request,backend)
        request_sha=digest(canonical(request))
        path=backend.work/(request['operation']+'.json')
        if path.is_symlink():raise ValueError('Redirected receipt')
        if path.exists() and json.loads(path.read_text()).get('request_sha256') != request_sha:
            raise ValueError('Operation ID cannot be reused for a changed request')
        report={'format':FORMAT,'operation':request['operation'],'request_sha256':request_sha,
                'role':request['role'],'target_identity':plan['target']['identity'],
                'statuses':plan['statuses'],'bytes_to_write':plan['bytes_to_write'],
                'bytes_written':0,'status':'planned'}
        if not apply:return report
        save(path,report)
        target_fd=image_fd=None
        try:
            backend.prove(request['role'])
            target_fd=os.open(plan['target']['path'],os.O_RDWR | getattr(os,'O_NOFOLLOW',0))
            fresh=inspect(request,backend,target_fd)
            image_fd=os.open(fresh['image'],os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
            for index,(extent,status_) in enumerate(zip(request['extents'],fresh['statuses'])):
                if status_=='already-applied':continue
                backend.prove(request['role'])
                if hash_at(target_fd,extent['target_offset'],extent['bytes']) != extent['before_sha256']:
                    raise ValueError('Extent changed immediately before write')
                remaining=extent['bytes'];source=extent['image_offset'];offset=extent['target_offset']
                # Hash the same opened upload immediately before every extent.
                if hash_at(image_fd,source,remaining) != extent['sha256']:
                    raise ValueError('Upload changed immediately before write')
                while remaining:
                    count=min(CHUNK,remaining);data=read_at(image_fd,source,count)
                    write_at(target_fd,offset,data);source+=count;offset+=count;remaining-=count
                    report['bytes_written']+=count
                os.fsync(target_fd)
                if hash_at(target_fd,extent['target_offset'],extent['bytes']) != extent['sha256']:
                    raise OSError('Physical write readback mismatch')
                report['statuses'][index]='verified';report['status']='writing';save(path,report)
            report['status']='complete'
            report['readback_verified']=True
            save(path,report)
            return report
        except BaseException as error:
            report.update(status='failed',error=str(error))
            save(path,report)
            raise
        finally:
            if image_fd is not None:os.close(image_fd)
            if target_fd is not None:os.close(target_fd)


def probe(role, offset, count, backend):
    """Read-only preimage for one uploaded chunk; no caller-selected device."""
    target=backend.prove(role)
    if any(type(v) is not int for v in (offset,count)) or offset<0 or count<=0 or count>64*1024**2:
        raise ValueError('Probe length must be 1..64MiB')
    if offset % backend.sector_size or count % backend.sector_size or offset+count>target['bytes']:
        raise ValueError('Probe escapes partition or is unaligned')
    if isinstance(backend,NativeBackend):
        backend.work.mkdir(mode=0o700,parents=True,exist_ok=True)
        secure_path(backend.work)
    with lock(backend.work):
        target=backend.prove(role)
        fd=os.open(target['path'],os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        try:before=hash_at(fd,offset,count)
        finally:os.close(fd)
    return {'format':'DaOS-recovery-probe-v1','role':role,'target_identity':target['identity'],
            'partition_bytes':target['bytes'],'sector_size':backend.sector_size,
            'target_offset':offset,'bytes':count,'before_sha256':before}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','apply','probe'))
    p.add_argument('--request',type=Path)
    p.add_argument('--role',choices=('root','state'))
    p.add_argument('--offset',type=int,default=0)
    p.add_argument('--bytes',type=int)
    args=p.parse_args()
    if args.action=='probe':
        if args.role is None or args.bytes is None:p.error('probe requires --role and --bytes')
        print(json.dumps(probe(args.role,args.offset,args.bytes,NativeBackend()),indent=2));return
    if args.request is None:p.error('plan/apply requires --request')
    request=json.loads(secure_path(args.request).read_text())
    print(json.dumps(execute(request,NativeBackend(),apply=args.action=='apply'),indent=2))


if __name__=='__main__':main()

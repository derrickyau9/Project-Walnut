"""New vnode GPT/UFS only. NO physical-device path is accepted as input."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time

OUTPUT=Path('/var/tmp/daos-postmortem-ufs-proof.json')
BASE=Path(tempfile.mkdtemp(prefix='daos-ufs-postmortem-private-',dir='/var/tmp'))
IMAGE=BASE/'ONLY-PRIVATE-fixture.img'
MOUNT=BASE/'mount';MOUNT.mkdir()
NONCE=b'a18d4085f31d4b129d292468ab7824b1'
MAGIC=b'DAOS-LOG-1\0\0'
SIZE=512*1024
def sha(data):return hashlib.sha256(data).hexdigest()
def command(args):return subprocess.run(list(map(str,args)),check=True,capture_output=True,text=True,timeout=45).stdout.strip()
def guard(index,side):
    body=struct.pack('<12s32sIB',b'DAOS-GUARD1\0',NONCE,index,side).ljust(480,b'\0')
    return body+hashlib.sha256(body).digest()
def record(index,payload):
    assert len(payload)<=400
    body=struct.pack('<12s32sQH',MAGIC,NONCE,index,len(payload))+payload
    body=body.ljust(480,b'\0')
    return body+hashlib.sha256(body).digest()
def decode(block):
    assert len(block)==512 and hashlib.sha256(block[:480]).digest()==block[480:]
    magic,nonce,index,size=struct.unpack_from('<12s32sQH',block)
    assert magic==MAGIC and nonce==NONCE and size<=400
    return index,block[54:54+size]
def raw_read(fd,offset,size):
    data=os.pread(fd,size,offset);assert len(data)==size;return data

started=time.monotonic();md=None;mounted=False;fd=None;whole=None;checks={};details={};failure=None
initial_md=command(['/sbin/mdconfig','-l']).split()
try:
    spec=importlib.util.spec_from_file_location('readonly_gpt','/var/tmp/daos-postmortem-flash.py')
    flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(flash)
    with IMAGE.open('xb') as f:f.truncate(192*1024*1024)
    md=command(['/sbin/mdconfig','-a','-t','vnode','-f',IMAGE])
    assert md.startswith('md') and md[2:].isdigit() and md not in initial_md
    disk='/dev/'+md;root=disk+'p2'
    command(['/sbin/gpart','create','-s','gpt',disk])
    for index,(kind,size,label) in enumerate([('efi','16m','DaOS-efi'),('freebsd-ufs','96m','DaOS-root'),('freebsd-ufs','16m','DaOS-state'),('freebsd-ufs','16m','DaOS-calibration'),('freebsd-ufs','16m','DaOS-photos')],1):
        command(['/sbin/gpart','add','-i',index,'-t',kind,'-s',size,'-l',label,disk])
    command(['/sbin/newfs','-U',root])
    command(['/sbin/mount','-t','ufs',root,MOUNT]);mounted=True
    probe=MOUNT/'geometry';probe.write_bytes(b'geometry')
    bsize=probe.stat().st_blksize;assert bsize>=4096 and bsize%512==0 and SIZE%bsize==0
    probe.unlink()
    original=b''.join(guard(i,0)+bytes(bsize-1024)+guard(i,1) for i in range(SIZE//bsize))
    logfile=MOUNT/'one-trial.log'
    with logfile.open('xb') as f:f.write(original);f.flush();os.fsync(f.fileno())
    logfile.chmod(0o600)
    sentinel=MOUNT/'PRESERVE-sentinel';sentinel.write_bytes(b'UNRELATED-ROOT-FILE\n'*1000)
    sentinel_hash=sha(sentinel.read_bytes())
    mapping=json.loads(command(['/var/tmp/daos-postmortem-map-ufs',logfile]))
    assert mapping['statfs_f_iosize']==mapping['st_blksize']==bsize
    details['mapping']=mapping
    os.sync();command(['/sbin/mount','-u','-r',MOUNT]);command(['/sbin/umount',MOUNT]);mounted=False
    whole=os.open(disk,os.O_RDONLY)
    identity,parts=flash.parse_gpt(whole,IMAGE.stat().st_size,512)
    assert set(parts)=={1,2,3,4,5}
    details['gpt']={'disk_uuid':identity,'partitions':parts}
    part=parts[2];part_bytes=part['sectors']*512;start=part['start_lba']*512
    allowed=[];mapped=[]
    for item in mapping['maps']:
        offset=item['sector512']*512
        assert offset%512==0 and 0<offset<part_bytes-bsize
        mapped.extend(range(offset,offset+bsize,512))
        allowed.extend(range(offset+512,offset+bsize-512,512))
    assert len(mapped)==len(set(mapped))
    assert len(mapping['maps'])==SIZE//bsize
    fd=os.open(root,os.O_RDWR)
    assert stat.S_ISCHR(os.fstat(fd).st_mode)
    assert str(IMAGE) in command(['/sbin/mdconfig','-l','-v','-u',md[2:]])
    assert not any(row.split()[0]==root for row in command(['/sbin/mount','-p']).splitlines())
    reconstructed=b''.join(raw_read(fd,item['sector512']*512,bsize) for item in mapping['maps'])
    assert reconstructed==original
    checks['FIOBMAP2_full_file_raw_read_matches']=True
    checks['f_iosize_equals_stat_blksize_fullblocks']=True
    checks['direct_and_indirect_logical_blocks_mapped']=len(mapping['maps'])>12
    baseline=raw_read(whole,0,IMAGE.stat().st_size)
    def verify_guards():
        for item in mapping['maps']:
            base=item['sector512']*512;i=item['logical']
            assert raw_read(fd,base,512)==guard(i,0)
            assert raw_read(fd,base+bsize-512,512)==guard(i,1)
    verify_guards()
    # Public API is an append index, never a caller-supplied byte offset.
    def append(index,payload):
        assert type(index) is int and 0<=index<len(allowed)
        assert isinstance(payload,bytes) and len(payload)<=400
        block=record(index,payload)
        assert os.pwrite(fd,block,allowed[index])==512
        os.fsync(fd)
        assert raw_read(fd,allowed[index],512)==block
    for index in range(5):append(index,('MILESTONE-'+str(index)).encode())
    checks['aligned_root_partition_char_writes_fsync_readback']=True
    for invalid in (-1,len(allowed),True):
        try:append(invalid,b'bad')
        except AssertionError:pass
        else:raise AssertionError('Invalid record index accepted')
    try:append(6,bytes(401))
    except AssertionError:pass
    else:raise AssertionError('Oversized record accepted')
    checks['negative_slot_and_payload_bounds_refuse']=True
    before=raw_read(fd,allowed[6],512)
    try:os.pwrite(fd,b'TORN',allowed[6])
    except OSError as error:details['unaligned_pwrite_errno']=error.errno
    else:raise AssertionError('Unaligned raw write unexpectedly accepted')
    assert raw_read(fd,allowed[6],512)==before
    checks['unaligned_write_rejected_without_mutation']=True
    corrupt=bytearray(record(7,b'simulated-torn-record'));corrupt[100]^=1
    assert os.pwrite(fd,corrupt,allowed[7])==512;os.fsync(fd)
    try:decode(raw_read(fd,allowed[7],512))
    except AssertionError:pass
    else:raise AssertionError('Corrupt record checksum accepted')
    checks['aligned_corrupt_record_detected']=True
    verify_guards();checks['immutable_per_fsblock_nonce_guards_preserved']=True
    changed=raw_read(whole,0,len(baseline))
    mask_before=bytearray(baseline);mask_after=bytearray(changed)
    for offset in allowed:
        pos=start+offset
        mask_before[pos:pos+512]=bytes(512);mask_after[pos:pos+512]=bytes(512)
    assert mask_before==mask_after
    checks['ALL_GPT_p1_p3_p4_p5_UFS_metadata_and_guards_unchanged']=True
    details['outside_allowed_data_sha256']=sha(mask_after)
    details['allowed_512B_record_slots']=len(allowed)
    details['raw_char_fsync_calls_succeeded']=6
    details['note']='FreeBSD fixture fsync only; OpenBSD VCHR fsync is not a durability barrier; no cold-power claim'
    expected=b''.join(raw_read(fd,item['sector512']*512,bsize) for item in mapping['maps'])
    os.close(fd);fd=None;os.close(whole);whole=None
    command(['/sbin/mount','-t','ufs','-o','ro',root,MOUNT]);mounted=True
    assert logfile.read_bytes()==expected and sha(sentinel.read_bytes())==sentinel_hash
    assert logfile.stat().st_ino==mapping['inode'] and logfile.stat().st_size==SIZE
    checks['fresh_RO_file_read_and_other_file_hash_unchanged']=True
    command(['/sbin/umount',MOUNT]);mounted=False
    fsck=subprocess.run(['/sbin/fsck_ufs','-n',root],capture_output=True,text=True,timeout=60)
    details['fsck_output']=fsck.stdout+fsck.stderr
    assert fsck.returncode==0
    checks['fsck_readonly_clean']=True
except BaseException as error:
    failure=type(error).__name__+': '+str(error)
finally:
    if fd is not None:os.close(fd)
    if whole is not None:os.close(whole)
    if mounted:
        command(['/sbin/mount','-u','-r',MOUNT]);command(['/sbin/umount',MOUNT])
    if md is not None:
        assert str(IMAGE) in command(['/sbin/mdconfig','-l','-v','-u',md[2:]])
        command(['/sbin/mdconfig','-d','-u',md[2:]])
    released=set(command(['/sbin/mdconfig','-l']).split())==set(initial_md)
    report={'passed':failure is None and all(checks.values()),'error':failure,'checks':checks,
            'details':details,'all_owned_MD_released':released,'seconds':time.monotonic()-started,
            'physical_devices_or_images_used':False,'production_environment_gate_not_executed':True,
            'write_target':'Only newly-owned vnode md p2 char provider; whole md opened READONLY',
            'scope':'FreeBSD FIOBMAP2/data-confinement proof, not OpenBSD provider mapping or hardware power-loss proof'}
    OUTPUT.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if failure:raise SystemExit(1)

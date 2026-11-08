"""Back up and apply sector-bounded, identity-checked DaOS boot recovery."""
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'build/DaOS_WiFi_Recovery_001'
SIZE=3254820864
BLOCK=8*1024**2
spec=importlib.util.spec_from_file_location('sd',ROOT/'tools/write_daos_sd.py')
sd=importlib.util.module_from_spec(spec);spec.loader.exec_module(sd)

def save(record):
    tmp=WORK/'status.tmp';tmp.write_text(json.dumps(record,indent=2)+'\n');os.replace(tmp,WORK/'status.json')

def read(h,count):
    buf=c.create_string_buffer(count);done=w.DWORD()
    sd.checked(sd.k.ReadFile(h,buf,count,c.byref(done),None))
    if done.value!=count:raise RuntimeError('Short physical read')
    return buf.raw[:count]

def seek(h,offset):sd.checked(sd.k.SetFilePointerEx(h,offset,None,0))

def main():
    if not c.windll.shell32.IsUserAnAdmin():raise RuntimeError('Windows administrator access required')
    WORK.mkdir(exist_ok=True)
    operation=sys.argv[1]
    if operation not in ('backup','apply'):raise ValueError('Unknown recovery operation')
    record={'format':'DaOS-SD-boot-recovery-v1','operation':operation,'started':datetime.now(timezone.utc).isoformat(),'status':'preparing'}
    disk=None;volumes=[]
    try:
        record['target']=sd.identity();save(record)
        for path in record['target']['volume_paths']:
            h=sd.open_device(path.rstrip('\\'),0xC0000000);volumes.append(h)
            sd.ioctl(h,0x00090018);sd.ioctl(h,0x00090020)
        disk=sd.open_device(r'\\.\PhysicalDrive11',0x80000000 if operation=='backup' else 0xC0000000)
        length=c.c_longlong();sd.ioctl(disk,0x0007405C,c.byref(length),8)
        if length.value!=sd.CARD_SIZE:raise RuntimeError('Opened device identity mismatch')
        if operation=='backup':
            output=WORK/'card-before.img';digest=hashlib.sha256();total=0
            with output.open('xb') as f:
                while total<SIZE:
                    data=read(disk,min(BLOCK,SIZE-total));f.write(data);digest.update(data);total+=len(data)
                    record.update(status='backing-up',bytes_read=total);save(record)
                f.flush();os.fsync(f.fileno())
            record.update(status='backup-complete',backup_sha256=digest.hexdigest(),backup_bytes=total)
            (WORK/'backup-receipt.json').write_text(json.dumps(record,indent=2)+'\n')
        else:
            plan=json.loads((WORK/'sector-plan.json').read_text())
            backup=json.loads((WORK/'backup-receipt.json').read_text())
            if plan['original_sha256']!=backup['backup_sha256'] or plan['image_bytes']!=SIZE:raise RuntimeError('Recovery input identity mismatch')
            digest=hashlib.sha256();total=0
            while total<SIZE:
                data=read(disk,min(BLOCK,SIZE-total));digest.update(data);total+=len(data)
            if digest.hexdigest()!=plan['original_sha256']:raise RuntimeError('Card contents changed since backup')
            patched=WORK/'card-repaired.img'
            with patched.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=plan['repaired_sha256']:raise RuntimeError('Repaired image changed')
                record.update(status='repairing-boot-sectors',bytes_written=0);save(record)
                last_end=0
                for entry in plan['sectors']:
                    offset,count=entry['offset'],entry['bytes']
                    # Only the captured EFI/root partitions may be changed.
                    if not (type(offset) is int and type(count) is int and offset%512==0 and count%512==0 and
                            offset>=20480 and offset>=last_end and count>0 and offset+count<=2818592768):raise ValueError('Write escaped boot/root bounds')
                    seek(disk,offset)
                    if hashlib.sha256(read(disk,count)).hexdigest()!=entry['before_sha256']:raise RuntimeError('Sector changed before repair')
                    f.seek(offset);data=f.read(count)
                    if len(data)!=count or hashlib.sha256(data).hexdigest()!=entry['sha256']:raise RuntimeError('Patch sector mismatch')
                    seek(disk,offset);buf=c.create_string_buffer(data);done=w.DWORD()
                    sd.checked(sd.k.WriteFile(disk,buf,count,c.byref(done),None))
                    if done.value!=count:raise RuntimeError('Short repair write')
                    last_end=offset+count;record['bytes_written']+=count;save(record)
            sd.checked(sd.k.FlushFileBuffers(disk));seek(disk,0)
            digest=hashlib.sha256();total=0
            while total<SIZE:
                data=read(disk,min(BLOCK,SIZE-total));digest.update(data);total+=len(data)
            if digest.hexdigest()!=plan['repaired_sha256']:raise RuntimeError('Physical recovery read-back mismatch')
            record.update(status='repaired-and-read-back-matched',read_back_sha256=digest.hexdigest(),
                          sectors=len(plan['sectors']),untouched_partitions=['state','calibration','photos'])
        record['completed']=datetime.now(timezone.utc).isoformat();save(record)
        print(json.dumps({'status':record['status'],'operation':operation}))
    except BaseException as e:
        record.update(status='failed',error=str(e));save(record);raise
    finally:
        if disk is not None:sd.k.CloseHandle(disk)
        for h in reversed(volumes):sd.k.CloseHandle(h)

if __name__=='__main__':main()

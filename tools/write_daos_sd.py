"""Write this release to the explicitly identified removable card (Windows admin)."""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / 'build/DaOS_BSD_RPi4_USB_ADB_ARM64/DaOS-BSD-RPi4-USB-ADB-arm64.img'
EXPECTED = 'ed84cce5eb14da514732f88909d0baee8f22266a3f837569185b30f855e1394e'
SIZE = 3254820864
CARD_SIZE = 63864569856
BLOCK = 8 * 1024 * 1024
k = c.WinDLL('kernel32', use_last_error=True)
k.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
k.CreateFileW.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.DeviceIoControl.argtypes = [w.HANDLE,w.DWORD,c.c_void_p,w.DWORD,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
k.DeviceIoControl.restype = w.BOOL
k.WriteFile.argtypes = [w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
k.WriteFile.restype = w.BOOL
k.ReadFile.argtypes = k.WriteFile.argtypes
k.ReadFile.restype = w.BOOL
k.FlushFileBuffers.argtypes = [w.HANDLE]
k.FlushFileBuffers.restype = w.BOOL
k.SetFilePointerEx.argtypes = [w.HANDLE,c.c_longlong,c.POINTER(c.c_longlong),w.DWORD]
k.SetFilePointerEx.restype = w.BOOL

def checked(ok):
    if not ok:
        raise c.WinError(c.get_last_error())

def open_device(name, access):
    h = k.CreateFileW(name, access, 3, None, 3, 0x80000000, None)
    if h == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    return h

def ioctl(h, code, output=None, length=0):
    n=w.DWORD()
    checked(k.DeviceIoControl(h,code,None,0,output,length,c.byref(n),None))

def identity():
    command = """$ErrorActionPreference='Stop'; $d=Get-Disk -Number 11; $p=Get-Partition -DiskNumber 11; $c=Get-CimInstance Win32_DiskDrive | Where-Object Index -eq 11; @{disk=$d | Select-Object Number,FriendlyName,SerialNumber,BusType,Size,IsBoot,IsSystem; pnp=$c.PNPDeviceID; letters=@($p | Where-Object { $_.DriveLetter -and [int][char]$_.DriveLetter -ne 0 } | ForEach-Object { [string]$_.DriveLetter }); volume_paths=@($p.AccessPaths | Where-Object { $_ -like '*Volume{*' } | Select-Object -Unique)} | ConvertTo-Json -Depth 4 -Compress"""
    value=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],text=True))
    d=value['disk']
    if not (d['Number']==11 and d['FriendlyName']=='Generic STORAGE DEVICE' and d['BusType'] in ('USB',7)
            and d['SerialNumber'].strip()=='000000001532' and d['Size']==CARD_SIZE
            and not d['IsBoot'] and not d['IsSystem']
            and value['pnp']==r'USBSTOR\DISK&VEN_GENERIC&PROD_STORAGE_DEVICE&REV_1532\000000001532&1'
            and value['letters'] in (['K'],[])):
        raise RuntimeError('Card identity changed; refusing write: '+json.dumps(value))
    if any(not re.fullmatch(r'\\\\\?\\Volume\{[0-9a-fA-F-]{36}\}\\',p) for p in value['volume_paths']):
        raise RuntimeError('Unexpected volume path on the selected card')
    return value

def main():
    global IMAGE, EXPECTED, SIZE
    out=Path(sys.argv[1]).resolve()
    if len(sys.argv)>2:
        directory=Path(sys.argv[2]).resolve(strict=True)
        if not directory.is_relative_to(ROOT/'build'):
            raise ValueError('Release must remain within the project build directory')
        deployment=directory/'deployment-status.json'
        if deployment.is_file() and json.loads(deployment.read_text(encoding='utf-8')).get('status','').startswith('withdrawn'):
            raise ValueError('This release is withdrawn after a physical boot failure; refusing to flash it')
        receipt=json.loads((directory/'image-evidence.json').read_text(encoding='utf-8'))
        entry=receipt['image']
        if (receipt.get('format')!='DaOS-freebsd-image-build-v1' or receipt.get('assembled') is not True
                or receipt.get('target',{}).get('architecture')!='arm64'
                or not re.fullmatch('[0-9a-f]{64}',str(entry.get('sha256','')))
                or type(entry.get('bytes')) is not int or not 32*1024**2<=entry['bytes']<CARD_SIZE):
            raise ValueError('Invalid assembled ARM64 release receipt')
        name=Path(entry['path']).name
        if not re.fullmatch(r'DaOS-[A-Za-z0-9_.-]+\.img',name):
            raise ValueError('Invalid release image filename')
        IMAGE=directory/name
        EXPECTED=entry['sha256']
        SIZE=entry['bytes']
    record={'format':'DaOS-physical-SD-write-v1','image':str(IMAGE),'expected_sha256':EXPECTED,
            'image_bytes':SIZE,'started':datetime.now(timezone.utc).isoformat(),'status':'preparing'}
    def save():
        temp=out/'status.tmp'
        temp.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        os.replace(temp,out/'status.json')
    volumes=[]
    disk=None
    try:
        if not c.windll.shell32.IsUserAnAdmin():
            raise RuntimeError('Windows administrator access is required')
        record['target']=identity()
        if IMAGE.stat().st_size!=SIZE:
            raise RuntimeError('Source image length differs from release')
        digest=hashlib.sha256()
        with IMAGE.open('rb') as source:
            for data in iter(lambda:source.read(BLOCK),b''):
                digest.update(data)
        if digest.hexdigest()!=EXPECTED:
            raise RuntimeError('Source image SHA256 differs from release')
        record['source_sha256']=digest.hexdigest()
        record['target']=identity()
        save()
        for path in record['target']['volume_paths']:
            volume=open_device(path.rstrip('\\'),0xC0000000)
            volumes.append(volume)
            ioctl(volume,0x00090018)  # FSCTL_LOCK_VOLUME
            ioctl(volume,0x00090020)  # FSCTL_DISMOUNT_VOLUME
        disk=open_device(r'\\.\PhysicalDrive11',0xC0000000)
        length=c.c_longlong()
        ioctl(disk,0x0007405C,c.byref(length),8)  # IOCTL_DISK_GET_LENGTH_INFO
        if length.value!=CARD_SIZE:
            raise RuntimeError('Opened physical drive size differs from identified card')
        record.update(status='writing',bytes_written=0)
        save()
        with IMAGE.open('rb') as source:
            while data:=source.read(BLOCK):
                buffer=c.create_string_buffer(data)
                done=w.DWORD()
                checked(k.WriteFile(disk,buffer,len(data),c.byref(done),None))
                if done.value!=len(data):
                    raise RuntimeError('Incomplete physical disk write')
                record['bytes_written']+=done.value
                save()
        checked(k.FlushFileBuffers(disk))
        record.update(status='reading-back',bytes_read_back=0)
        save()
        checked(k.SetFilePointerEx(disk,0,None,0))
        buffer=c.create_string_buffer(BLOCK)
        digest=hashlib.sha256()
        while record['bytes_read_back']<SIZE:
            count=min(BLOCK,SIZE-record['bytes_read_back'])
            done=w.DWORD()
            checked(k.ReadFile(disk,buffer,count,c.byref(done),None))
            if done.value!=count:
                raise RuntimeError('Incomplete physical disk read-back')
            digest.update(buffer.raw[:count])
            record['bytes_read_back']+=count
            save()
        record['read_back_sha256']=digest.hexdigest()
        if digest.hexdigest()!=EXPECTED:
            raise RuntimeError('Physical SD read-back SHA256 mismatch')
        record.update(status='completed',completed=datetime.now(timezone.utc).isoformat(),write_and_read_back_matched=True)
        save()
        print('SD image written and complete image read-back SHA256 matched.',flush=True)
    except BaseException as error:
        record.update(status='failed',error=str(error),failed=datetime.now(timezone.utc).isoformat())
        save()
        raise
    finally:
        if disk is not None:
            k.CloseHandle(disk)
        for volume in reversed(volumes):
            k.CloseHandle(volume)

if __name__=='__main__':
    main()

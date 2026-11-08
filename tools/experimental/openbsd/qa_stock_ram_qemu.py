"""Boot only a verified private OpenBSD ARM64 miniroot; no NIC or host disks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('image',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--prepare-data',type=Path);a=p.parse_args()
    image=a.image.resolve(strict=True);out=a.output;out.mkdir();before=sha(image)
    variables=out/'AAVMF_VARS.fd';shutil.copyfile('/usr/share/AAVMF/AAVMF_VARS.fd',variables)
    sock='/tmp/daos-openbsd-ram-'+str(os.getpid())+'.sock'
    args=['qemu-system-aarch64','-machine','virt,gic-version=3','-accel','tcg,thread=multi','-cpu','cortex-a72','-smp','4','-m','2048',
        '-display','none','-serial','unix:'+sock+',server=on,wait=off','-monitor','none','-no-reboot','-nic','none',
        '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd',
        '-drive','if=pflash,format=raw,file='+str(variables),
        '-drive','if=none,id=install,file='+str(image)+',format=raw,snapshot=on',
        '-device','virtio-blk-pci,drive=install,bootindex=1']
    if a.prepare_data is not None:
        args+=['-drive','if=none,id=owneddata,file='+str(a.prepare_data.resolve(strict=True))+',format=raw',
               '-device','virtio-blk-pci,drive=owneddata']
    commands=['uname -a; mount; df -k; echo QA_OPENBSD_RAM_SHELL',
        'ls -l / /bin /sbin /usr/bin /usr/sbin /etc; echo QA_OPENBSD_RAM_LIST',
        'ls -l /etc/firmware; sysctl kern.watchdog; echo QA_OPENBSD_RAM_CAPS',
        'dmesg; echo QA_OPENBSD_DONE']
    if a.prepare_data is not None:
        commands=['cd /dev; /bin/sh MAKEDEV sd1; cd /; mount; disklabel sd1; echo QA_OPENBSD_PRIVATE_DATA_DISK',
                  'mount_msdos /dev/sd1c /mnt; echo QA_OPENBSD_PRIVATE_DATA_MOUNT',
                  '/bin/sh /mnt/prepare.sh',
                  'umount /mnt; echo QA_OPENBSD_DONE']
    all_data=bytearray();selection=False;index=0;last=-1;began=time.monotonic();passed=False
    with (out/'qemu.log').open('wb') as log:
        proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log);s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            stop=time.monotonic()+5
            while True:
                try:s.connect(sock);break
                except (FileNotFoundError,ConnectionRefusedError):
                    if time.monotonic()>stop:raise
                    time.sleep(.05)
            s.settimeout(.25);progress=time.monotonic()+15
            with (out/'serial.log').open('wb') as serial:
                while time.monotonic()-began<180 and proc.poll() is None:
                    try:data=s.recv(65536)
                    except socket.timeout:data=b''
                    if data:all_data.extend(data);serial.write(data);serial.flush()
                    text=all_data.decode(errors='replace')
                    if not selection and '(S)hell?' in text:s.sendall(b'S\n');selection=True
                    if selection and index<len(commands) and len(all_data)>last and re.search(r'(?:\r?\n|^)# $',text):
                        s.sendall((commands[index]+'\n').encode());index+=1;last=len(all_data)
                    if re.search(r'\r?\nQA_OPENBSD_DONE\r?\n',text):passed=True;break
                    if len(all_data)>1024**2:raise ValueError('Unbounded console output')
                    if time.monotonic()>=progress:
                        print(json.dumps({'seconds':round(time.monotonic()-began),'serial_bytes':len(all_data),'tail':text[-240:]}),flush=True);progress=time.monotonic()+15
        finally:
            s.close()
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            if Path(sock).is_socket():Path(sock).unlink()
    text=all_data.decode(errors='replace')
    if a.prepare_data is not None:passed=passed and 'DAOS_OPENBSD_NATIVE_EXPORT_READY' in text
    report={'format':'DaOS-OpenBSD80-ARM64-stock-RAM-QEMU-v1','passed':passed and before==sha(image),
            'image_sha256':before,'image_unchanged':before==sha(image),'command':args,'main_card_attached':False,
            'network_attached':False,'physical_USB_or_WiFi_tested':False,'private_QEMU_closed':proc.poll() is not None,
            'seconds':time.monotonic()-began,'serial_sha256':sha(out/'serial.log'),
            'root_is_ram':bool(re.search(r'root on rd0a',text)),'shell_reached':'QA_OPENBSD_RAM_SHELL' in text}
    if a.prepare_data is not None:report.update(private_data_image=str(a.prepare_data),native_RAM_payload_prepared='DAOS_OPENBSD_NATIVE_PAYLOAD_READY' in text,native_export_completed='DAOS_OPENBSD_NATIVE_EXPORT_READY' in text)
    (out/'qa.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    if not report['passed']:raise ValueError('Inspect bounded stock RAM boot console')


if __name__=='__main__':main()

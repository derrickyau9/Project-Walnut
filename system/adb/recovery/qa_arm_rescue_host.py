"""Boot the standalone FAT/RAM rescue image under owned ARM64 QEMU TCG."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--usb-helper-sha256')
    options=parser.parse_args()
    if options.usb_helper_sha256 is not None and not re.fullmatch('[0-9a-f]{64}',options.usb_helper_sha256):
        raise ValueError('Exact USB helper SHA256 required')
    image=options.image.resolve(strict=True);out=options.output;out.mkdir()
    before=sha(image)
    variables=out/'AAVMF_VARS.fd';shutil.copyfile('/usr/share/AAVMF/AAVMF_VARS.fd',variables)
    sockpath='/tmp/daos-arm-rescue-qa-'+str(os_pid())+'.sock'
    args=['qemu-system-aarch64','-machine','virt,gic-version=3','-accel','tcg,thread=multi','-cpu','cortex-a72','-smp','4','-m','2048',
        '-display','none','-serial','unix:'+sockpath+',server=on,wait=off','-monitor','none','-no-reboot','-nic','none',
        '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd',
        '-drive','if=pflash,format=raw,file='+str(variables),
        '-drive','if=none,id=rescue,file='+str(image)+',format=raw,snapshot=on',
        '-device','virtio-blk-pci,drive=rescue']
    start=time.monotonic();all_data=bytearray();shell_requested=False;command_index=0;last_command_at=-1;passed=False
    shell_command="export LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib; /rescue/mount -p; /rescue/sysctl kern.bootfile; /rescue/kenv daos_recovery; /rescue/mdconfig -l -v -u 0; /usr/local/bin/python3.12 -I -c 'import json,hashlib,fcntl,selectors,termios,pty,socket,sys; sys.path.insert(0,\"/usr/local/libexec/DaOS-recovery\"); import flash; print(\"QA_ARM_PYTHON_READY\")'; /usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py status && echo QA_ARM_SUPERVISOR_READY; /bin/mkdir /DaOS-recovery-ram/qa-shell && /bin/chmod 700 /DaOS-recovery-ram/qa-shell && /bin/rm -r /DaOS-recovery-ram/qa-shell && echo QA_ARM_HOST_FILE_TOOLS_READY; /sbin/geom label status -s && echo QA_ARM_GEOM_READY; /usr/sbin/diskinfo /dev/md0 && echo QA_ARM_DISKINFO_READY; echo QA_ARM_COMMAND_DONE\n"
    extra="/sbin/kldstat -q -m usb_template; rc=$?; if [ \"$rc\" -eq 0 ] || [ \"$rc\" -eq 1 ]; then echo QA_ARM_KLDSTAT_PRESENT; fi; /usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py health; "
    shell_command=shell_command.replace('echo QA_ARM_COMMAND_DONE',extra+'echo QA_ARM_COMMAND_DONE')
    # Wait for a real shell prompt: single_user's console setup can flush input
    # sent immediately after RETURN. Short lines also avoid canonical line limits.
    shell_commands=[
        'export LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib; /rescue/mount -p; /rescue/sysctl kern.bootfile; /rescue/kenv daos_recovery; /rescue/kenv -q init_script; /rescue/mdconfig -l -v -u 0',
        "/usr/local/bin/python3.12 -I -c 'import json,hashlib,fcntl,selectors,termios,pty,socket,sys; sys.path.insert(0,\"/usr/local/libexec/DaOS-recovery\"); import flash; print(\"QA_ARM_PYTHON_READY\")'",
        '/usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py status && echo QA_ARM_SUPERVISOR_READY',
        '/bin/mkdir /DaOS-recovery-ram/qa-shell && /bin/chmod 700 /DaOS-recovery-ram/qa-shell && /bin/rm -r /DaOS-recovery-ram/qa-shell && echo QA_ARM_HOST_FILE_TOOLS_READY',
        '/sbin/geom label status -s && echo QA_ARM_GEOM_READY',
        '/usr/sbin/diskinfo /dev/md0 && echo QA_ARM_DISKINFO_READY',
        '/sbin/kldstat -q -m usb_template; rc=$?; if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then echo QA_ARM_KLDSTAT_PRESENT; fi',
        '/usr/local/bin/python3.12 -I -B /usr/local/libexec/DaOS-recovery/early_adb.py health',
        "/usr/local/bin/python3.12 -I -c 'import hashlib;from pathlib import Path;print(\"QA_ARM_USB_HELPER_SHA256=\"+hashlib.sha256(Path(\"/usr/local/libexec/daos-usb-adb\").read_bytes()).hexdigest())'",
        'echo QA_ARM_COMMAND_DONE']
    report={'format':'DaOS-standalone-ARM64-RAM-rescue-boot-QA-v1','image':str(image),'image_sha256':before,'command':args,
        'physical_usb_tested':False,'main_root_partition_attached':False}
    with (out/'qemu.log').open('wb') as log:
        proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            deadline=time.monotonic()+5
            while True:
                try:connection.connect(sockpath);break
                except (FileNotFoundError,ConnectionRefusedError):
                    if time.monotonic()>deadline:raise
                    time.sleep(.05)
            connection.settimeout(.25);next_update=time.monotonic()+15
            with (out/'serial.log').open('wb') as serial:
                while time.monotonic()-start<180 and proc.poll() is None:
                    try:data=connection.recv(65536)
                    except socket.timeout:data=b''
                    if data:
                        all_data.extend(data);serial.write(data);serial.flush()
                    text=all_data.decode(errors='replace')
                    if not shell_requested and 'Enter full pathname of shell or RETURN for' in text:
                        connection.sendall(b'\n');shell_requested=True
                    if shell_requested and command_index<len(shell_commands) and len(all_data)>last_command_at and re.search(r'(?:\r?\n|^)# $',text):
                        connection.sendall((shell_commands[command_index]+'\n').encode());command_index+=1;last_command_at=len(all_data)
                    if re.search(r'\r?\nQA_ARM_COMMAND_DONE\r?\n',text):
                        passed='DAOS_RAM_SUPERVISOR_ALIVE' in text and bool(re.search(r'\r?\nQA_ARM_PYTHON_READY\r?\n',text));break
                    if 'mountroot>' in text:
                        break
                    if len(all_data)>4*1024**2:raise RuntimeError('Unbounded serial output')
                    if time.monotonic()>next_update:
                        print(json.dumps({'elapsed':round(time.monotonic()-start),'serial_bytes':len(all_data),'tail':text[-180:]}),flush=True);next_update=time.monotonic()+15
        finally:
            connection.close()
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            p=Path(sockpath)
            if p.is_socket():p.unlink()
    text=all_data.decode(errors='replace')
    health_reports=[]
    for line in text.splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if isinstance(value,dict) and value.get('format')=='DaOS-early-recovery-adb-health-v1':health_reports.append(value)
    report.update(passed=passed and sha(image)==before,seconds=time.monotonic()-start,
        checks={'ram_supervisor_started':'DAOS_RAM_SUPERVISOR_ALIVE' in text,'single_user_prompt':'Enter full pathname of shell or RETURN for' in text,
                'python_dependencies':bool(re.search(r'\r?\nQA_ARM_PYTHON_READY\r?\n',text)),
                'supervisor_alive':bool(re.search(r'\r?\nQA_ARM_SUPERVISOR_READY\r?\n',text)),
                'host_file_tools':bool(re.search(r'\r?\nQA_ARM_HOST_FILE_TOOLS_READY\r?\n',text)),
                'geom_label_plugin':bool(re.search(r'\r?\nQA_ARM_GEOM_READY\r?\n',text)),
                'diskinfo':bool(re.search(r'\r?\nQA_ARM_DISKINFO_READY\r?\n',text)),
                'kldstat_executable':bool(re.search(r'\r?\nQA_ARM_KLDSTAT_PRESENT\r?\n',text)),
                'pre_rc_init_script':bool(re.search(r'\r?\nDAOS_RAM_PRE_RC_TMPFS_READY\r?\n',text)) and bool(re.search(r'\r?\n/usr/local/libexec/DaOS-recovery/early-init.sh\r?\n',text)) and 'DAOS_RAM_BOOTSTRAP_START' in text and text.index('DAOS_RAM_PRE_RC_TMPFS_READY')<text.index('DAOS_RAM_BOOTSTRAP_START'),
                'health_reports_no_false_usb_ready':bool(health_reports) and all(v.get('ready') is False for v in health_reports),
                'preloaded_ram_root':bool(re.search(r'md0\s+preload',text)),
                'recovery_tmpfs':bool(re.search(r'tmpfs\s+/DaOS-recovery-ram\s+tmpfs\s+rw',text)),
                'ram_root_source':'/dev/md0' in text,'efi_own_device':'DaOS rescue: pinned to EFI boot device' in text,'input_image_unchanged':sha(image)==before},
        qemu_returncode=proc.returncode,test_vm_stopped_after_assertion=True,serial_sha256=sha(out/'serial.log'))
    report['health_reports']=health_reports
    helper_hashes=re.findall(r'\r?\nQA_ARM_USB_HELPER_SHA256=([a-f0-9]{64})\r?\n',text)
    report['usb_helper_sha256']=helper_hashes[-1] if helper_hashes else None
    if options.usb_helper_sha256 is not None:
        report['checks']['usb_helper_exact_sha256']=helper_hashes==[options.usb_helper_sha256]
    report['usb_authentication_tested']=False
    report['passed']=report['passed'] and all(report['checks'].values())
    (out/'boot-qa.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)
    if not report['passed']:raise RuntimeError('Standalone ARM rescue boot QA did not pass; inspect serial.log')


def os_pid():
    import os
    return os.getpid()


if __name__=='__main__':main()

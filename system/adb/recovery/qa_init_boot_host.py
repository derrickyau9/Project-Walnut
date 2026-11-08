"""Boot owned disposable init QA images on WSL KVM; never controls the builder."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    work=Path(sys.argv[1]).resolve(strict=True)
    report={'format':'DaOS-init-failure-transition-boot-QA-v1','physical_usb_tested':False,'results':{}}
    for mode in ('rc-abort','direct-single'):
        image=work/(mode+'.img');before=sha(image)
        session=work/(mode+'-run');session.mkdir()
        varfile=session/'OVMF_VARS.fd';shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd',varfile)
        logfile=session/'serial.log'
        args=['qemu-system-x86_64','-machine','q35,accel=kvm','-cpu','host','-smp','2','-m','1024','-display','none',
            '-serial','file:'+str(logfile),'-monitor','none','-no-reboot','-nic','none',
            '-drive','if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
            '-drive','if=pflash,format=raw,file='+str(varfile),
            '-drive','file='+str(image)+',format=raw,if=virtio,snapshot=on']
        start=time.monotonic();ready=False
        with (session/'qemu.log').open('wb') as log:
            proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
            try:
                while time.monotonic()-start<90 and proc.poll() is None:
                    text=logfile.read_text(errors='replace') if logfile.exists() else ''
                    if len(text)>2*1024**2:raise RuntimeError('Unexpected serial output volume')
                    if 'Enter full pathname of shell or RETURN for' in text and text.count('QA_RECOVERY_HOOK')>=2:
                        ready=True;break
                    time.sleep(.25)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        text=logfile.read_text(errors='replace')
        first=text.find('QA_RECOVERY_HOOK');second=text.find('QA_RECOVERY_HOOK',first+1);prompt=text.find('Enter full pathname of shell or RETURN for')
        rc=text.find('QA_RC_ABORT')
        checks={'hook_before_single_prompt':0<=first<second<prompt,'two_hooks':text.count('QA_RECOVERY_HOOK')==2,
            'input_image_unchanged':sha(image)==before,
            'rc_abort_between_hooks':first<rc<second if mode=='rc-abort' else rc<0}
        result={'passed':ready and all(checks.values()),'checks':checks,'seconds':time.monotonic()-start,
            'qemu_returncode':proc.returncode,'test_vm_stopped_after_assertion':True,'input_image_sha256':before,
            'command':args,'serial_sha256':sha(logfile)}
        report['results'][mode]=result
        (work/'boot-qa.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({mode:result}),flush=True)
        if not result['passed']:raise RuntimeError('Init transition failed '+mode+'\n'+text[-4500:])
    report['passed']=True
    (work/'boot-qa.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()

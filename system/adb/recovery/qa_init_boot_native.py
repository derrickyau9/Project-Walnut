"""Build disposable amd64 init transition images inside native FreeBSD."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    out=Path(sys.argv[1]);out.mkdir(mode=0o700)
    patched=Path('/var/DaOS004-work/usb-recovery-001/init-amd64')
    root=out/'root';root.mkdir()
    for name in ('bin','sbin','rescue','etc','dev','tmp','var','usr/local/libexec/DaOS-recovery','boot/kernel','boot/defaults'):
        (root/name).mkdir(parents=True,exist_ok=True)
    shutil.copyfile('/rescue/rescue',root/'rescue/rescue')
    os.chmod(root/'rescue/rescue',0o555)
    for name in ('sh','cat','mount','sysctl','kenv','sleep','echo','kill','reboot'):
        os.link(root/'rescue/rescue',root/'rescue'/name)
    (root/'bin/sh').symlink_to('/rescue/sh')
    shutil.copyfile(patched,root/'sbin/init');os.chmod(root/'sbin/init',0o555)
    shutil.copyfile('/boot/kernel/kernel',root/'boot/kernel/kernel')
    shutil.copytree('/boot/lua',root/'boot/lua')
    shutil.copyfile('/boot/defaults/loader.conf',root/'boot/defaults/loader.conf')
    if Path('/boot/fonts').exists():shutil.copytree('/boot/fonts',root/'boot/fonts')
    (root/'etc/master.passwd').write_text('root:*:0:0::0:0:root:/:/rescue/sh\n')
    (root/'etc/ttys').write_text('console none unknown off secure\n')
    (root/'etc/fstab').write_text('/dev/gpt/DaOS-QA-root / ufs rw 1 1\n')
    hook=root/'usr/local/libexec/DaOS-recovery/early-init.sh'
    hook.write_text('''#!/rescue/sh
count=0
if [ -f /tmp/qa-hook-count ]; then read -r count < /tmp/qa-hook-count; fi
count=$((count+1))
printf '%s\\n' "$count" > /tmp/qa-hook-count
printf 'QA_RECOVERY_HOOK count=%s\\n' "$count"
exit 0
''');hook.chmod(0o555)
    rc=root/'etc/rc'
    rc.write_text('''#!/rescue/sh
printf 'QA_RC_ABORT sending_SIGTERM_to_PID1\\n'
kill -TERM 1
sleep 30
exit 1
''');rc.chmod(0o555)
    efi=out/'efi';(efi/'EFI/BOOT').mkdir(parents=True)
    shutil.copyfile('/boot/loader.efi',efi/'EFI/BOOT/BOOTX64.EFI')
    def run(args):
        r=subprocess.run(args,capture_output=True,text=True,timeout=90)
        if r.returncode:raise RuntimeError(str(args)+'\n'+r.stdout+r.stderr)
        return r
    run(['makefs','-t','msdos','-s','40m','-o','fat_type=32,sectors_per_cluster=1',str(out/'efi.fat'),str(efi)])
    boot_sector=(out/'efi.fat').read_bytes()[:512]
    assert boot_sector[510:512]==b'\x55\xaa' and boot_sector[13]==1,'makefs did not produce a valid FAT boot sector'
    run(['/sbin/fsck_msdosfs','-n',str(out/'efi.fat')])
    report={'init_sha256':sha(patched),'kernel_sha256':sha(root/'boot/kernel/kernel'),'images':{}}
    for mode in ('rc-abort','direct-single'):
        conf='''autoboot_delay="-1"
beastie_disable="YES"
loader_gfx="NO"
loader_logo="none"
console="comconsole"
comconsole_speed="115200"
boot_serial="YES"
vfs.root.mountfrom="ufs:/dev/gpt/DaOS-QA-root"
init_script="/usr/local/libexec/DaOS-recovery/early-init.sh"
'''
        if mode=='direct-single':conf+='boot_single="YES"\n'
        (root/'boot/loader.conf').write_text(conf)
        ufs=out/(mode+'.ufs');image=out/(mode+'.img')
        run(['makefs','-t','ffs','-B','little','-s','160m','-o','version=2',str(ufs),str(root)])
        run(['mkimg','-s','gpt','-p','efi/DaOS-QA-efi:='+str(out/'efi.fat'),'-p','freebsd-ufs/DaOS-QA-root:='+str(ufs),'-o',str(image)])
        report['images'][mode]={'path':str(image),'bytes':image.stat().st_size,'sha256':sha(image)}
    (out/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()

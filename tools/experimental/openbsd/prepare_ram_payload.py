"""Prepare a white-listed public-input RAM payload and an isolated QEMU key."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import uuid
from ffs1_readonly import Ffs1

ROOT=Path('/mnt/f/Project HasselFree')
BASE=ROOT/'build/DaOS_OpenBSD_001'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-name',default='ram-prototype-003');args=parser.parse_args()
    if not args.output_name.replace('-','').isalnum():raise ValueError('Confined output name required')
    out=BASE/args.output_name;out.mkdir()
    # Only the explicitly supplied task public key is opened. Its private key
    # remains on the PC and may be consumed only by the native SSH client.
    public=ROOT/'build/DaOS_Private_Recovery_State/openbsd-001/client_ed25519.pub'
    nonce=uuid.uuid4().hex;files={};modes={}
    report=json.loads((BASE/'network/ssh-debug-strip-candidate-002.json').read_text())
    for name,entry in report['files'].items():
        p=BASE/'network/ssh-payload-debug-stripped-002'/name;data=p.read_bytes()
        assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];files[name]=data;modes[name]=0o555 if '/lib/' not in name else 0o444
    for p in (BASE/'firmware/payload/etc/firmware').iterdir():
        assert p.is_file() and not p.is_symlink();files['etc/firmware/'+p.name]=p.read_bytes();modes['etc/firmware/'+p.name]=0o444
    config='''Port 22
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
AuthorizedKeysFile /etc/daos/authorized_keys
PermitRootLogin prohibit-password
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
StrictModes yes
AllowUsers root
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
LoginGraceTime 15
MaxAuthTries 3
MaxSessions 2
PidFile /var/run/daos-sshd.pid
LogLevel VERBOSE
'''
    # The stock RAM disk ships pwd.db/spwd.db, not master.passwd. Rebuilding
    # from a new sshd-only file would discard root and DHCP service accounts.
    stock=Ffs1(BASE/'official/ramdisk-stock.fs')
    account_lines=[]
    for line in stock.contents(stock.lookup('etc/passwd')).decode().splitlines():
        name,password,uid,gid,gecos,home,shell=line.split(':')
        password='' if name=='root' else '*'
        account_lines.append(':'.join((name,password,uid,gid,'','0','0',gecos,home,shell)))
    account_lines.append('sshd:*:27:27::0:0:sshd privsep:/var/empty:/sbin/nologin')
    extras={'etc/ssh/sshd_config':config.encode(),'etc/daos/authorized_keys':public.read_bytes(),
        'etc/master.passwd':('\n'.join(account_lines)+'\n').encode(),
        'etc/daos/probe-nonce':(nonce+'\n').encode(),
        'etc/login.conf':(BASE/'network/reference-files/etc/login.conf').read_bytes(),
        'etc/daos/ram-init-qemu.sh':(ROOT/'system/openbsd/raspberrypi/ram_init_qemu.sh').read_bytes(),
        'etc/daos/network_probe.sh':(ROOT/'system/openbsd/raspberrypi/network_probe.sh').read_bytes(),
        '.profile':b'PATH=/sbin:/bin:/usr/bin:/usr/sbin:/; export PATH\n/bin/sh /etc/daos/ram-init-qemu.sh\n',
        'etc/daos/PROTOTYPE-ONLY.txt':b'Isolated QEMU prototype. No physical installation approval. No hardware watchdog in stock SMALL_KERNEL. Client key is ephemeral test public key only.\n'}
    for name,data in extras.items():files[name]=data;modes[name]=0o555 if name.endswith('.sh') else 0o600
    payload=out/'payload.tgz'
    with tarfile.open(payload,'w:gz') as tar:
        directories=set()
        for name in files:
            parent=Path(name).parent
            while str(parent)!='.':directories.add(parent.as_posix());parent=parent.parent
        directories.update(('root','var/empty','var/run','var/log'))
        for name in sorted(directories,key=lambda s:(s.count('/'),s)):
            e=tarfile.TarInfo(name);e.type=tarfile.DIRTYPE;e.mode=0o700 if name=='root' else 0o755;e.uid=e.gid=0;tar.addfile(e)
        for name,data in files.items():
            e=tarfile.TarInfo(name);e.mode=modes[name];e.uid=e.gid=0;e.size=len(data);tar.addfile(e,io.BytesIO(data))
    prepare='''#!/bin/sh
set -e
mount | grep '^/dev/rd0a on / type ffs' >/dev/null
[ "$(mount | sed -n '$=')" -eq 2 ]
mount | grep '^/dev/sd1c on /mnt type msdos' >/dev/null
# Both disks are exclusively private QEMU files; removal below is RAM rd0a only.
rm -rf /usr/mdec /etc/firmware /etc/ssl
tar -xzpf /mnt/payload.tgz -C /
echo 'sshd:*:27:' >>/etc/group
pwd_mkdb -p /etc/master.passwd
chmod 755 /var/empty
chmod 600 /etc/daos/authorized_keys /etc/ssh/sshd_config
sync
df -k
echo DAOS_OPENBSD_NATIVE_PAYLOAD_READY
dd if=/dev/rrd0a of=/mnt/custom-root.fs bs=64k
fsck_ffs -fy /mnt/custom-root.fs || [ "$?" -eq 1 ]
fsck_ffs -fn /mnt/custom-root.fs
sha256 /mnt/custom-root.fs
echo DAOS_OPENBSD_NATIVE_EXPORT_READY
'''
    (out/'prepare.sh').write_text(prepare)
    data=out/'payload-data-fat.img'
    with data.open('xb') as f:f.truncate(64*1024**2)
    subprocess.run(['/usr/sbin/mkfs.vfat','-F','32','-n','DAOS_OBSD',str(data)],check=True,capture_output=True)
    mcopy='/home/dy09/hf-openbsd-host-tools-001/root/usr/bin/mcopy'
    for p in (payload,out/'prepare.sh'):subprocess.run([mcopy,'-i',str(data),str(p),'::'+p.name],check=True)
    manifest={'format':'DaOS-OpenBSD80-RAM-QEMU-prototype-inputs-v1','nonce':nonce,'status':'PRIVATE_NATIVE_PREPARATION_PENDING',
        'payload_bytes':payload.stat().st_size,'payload_sha256':hashlib.sha256(payload.read_bytes()).hexdigest(),
        'private_client_key_opened_or_packaged':False,'client_public_key_sha256':hashlib.sha256(public.read_bytes()).hexdigest(),
        'files':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':modes[name]} for name,data in files.items()},
        'private_user_data_included':False,'physical_actions':False,'raw_kernel_unchanged_outside_embedded_RAM_image':True}
    (out/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'out':str(out),'nonce':nonce,'payload_bytes':payload.stat().st_size,'files_bytes':sum(map(len,files.values()))}))


if __name__=='__main__':main()

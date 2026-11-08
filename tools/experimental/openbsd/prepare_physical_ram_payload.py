"""Copy four explicitly authorized opaque inputs into a PRIVATE RAM build.

This performs no Pi/AP actions and never reads the task client private key.
Every generated image/archive remains beneath the existing private state.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from ffs1_readonly import Ffs1

ROOT=Path('/mnt/f/Project HasselFree');BASE=ROOT/'build/DaOS_OpenBSD_001'
PRIVATE=ROOT/'build/DaOS_Private_Recovery_State/openbsd-001'
EXPECTED={'test-ssid':'/etc/daos/test-ssid','test-psk':'/etc/daos/test-psk',
    'authorized_keys':'/etc/ssh/authorized_keys','ssh_host_ed25519_key':'/etc/ssh/ssh_host_ed25519_key'}
def digest(data):return hashlib.sha256(data).hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-name',default='ram-native-002');args=parser.parse_args()
    assert args.output_name.startswith('ram-native-') and args.output_name[len('ram-native-'):].isdigit()
    metadata_path=BASE/'PHYSICAL-PROVISION-PUBLIC.json';metadata=json.loads(metadata_path.read_text());nonce=metadata['nonce']
    assert len(nonce)==32 and all(x in '0123456789abcdef' for x in nonce)
    assert metadata['opaque_files']==EXPECTED and metadata['all_guest_file_modes']=='0600'
    assert metadata['client_private_key_included'] is False and metadata['publishing_private_prototype_authorized'] is False
    overlay=Path(subprocess.check_output(['wslpath','-u',metadata['private_overlay_directory']],text=True).strip()).resolve(strict=True)
    assert overlay==PRIVATE/('physical-'+nonce) and not overlay.is_symlink()
    out=overlay/args.output_name;out.mkdir(mode=0o700)
    public=BASE/'ram-prototype-007';inputs=json.loads((public/'inputs.json').read_text())
    assert digest((public/'bsd.rd').read_bytes())=='a0b88b10024a19d2f9c46e7c71251eff42d02940c8a18b16ed9576bf9a71ef2f'
    files={};modes={}
    with tarfile.open(public/'payload.tgz','r:gz') as tar:
        for item in tar:
            if item.isdir():continue
            assert item.isreg() and item.name in inputs['files'] and not item.name.startswith('/') and '..' not in Path(item.name).parts
            data=tar.extractfile(item).read();expected=inputs['files'][item.name]
            assert digest(data)==expected['sha256'] and len(data)==expected['bytes']
            files[item.name]=data;modes[item.name]=item.mode
    for name in ('etc/daos/ram-init-qemu.sh','etc/daos/authorized_keys','etc/daos/PROTOTYPE-ONLY.txt'):
        del files[name];del modes[name]
    files['.profile']=b'PATH=/sbin:/bin:/usr/bin:/usr/sbin:/; export PATH\n/bin/sh /etc/daos/ram-init-trial.sh\n'
    files['etc/daos/probe-nonce']=(nonce+'\n').encode()
    files['etc/daos/trial-kind']=b'OPENBSD8_PI4_RAM_WIFI_ONLY\n';modes['etc/daos/trial-kind']=0o600
    files['etc/daos/ram-init-trial.sh']=(ROOT/'system/openbsd/raspberrypi/ram_init_trial.sh').read_bytes();modes['etc/daos/ram-init-trial.sh']=0o555
    config=files['etc/ssh/sshd_config'].replace(b'AuthorizedKeysFile /etc/daos/authorized_keys',b'AuthorizedKeysFile /etc/ssh/authorized_keys')
    assert config!=files['etc/ssh/sshd_config'];files['etc/ssh/sshd_config']=config
    # The physical-only copy also omits the private SSID from interface logs.
    probe=files['etc/daos/network_probe.sh'].replace(b"/wpakey/d;",b"/nwid/d; /wpakey/d;")
    assert probe!=files['etc/daos/network_probe.sh'];files['etc/daos/network_probe.sh']=probe
    public_key=(PRIVATE/'client_ed25519.pub').read_bytes()
    for source,target in EXPECTED.items():
        path=overlay/source;assert path.is_file() and not path.is_symlink()
        before=path.stat();data=path.read_bytes();after=path.stat()
        assert before.st_ino==after.st_ino and before.st_size==after.st_size==len(data) and before.st_mtime_ns==after.st_mtime_ns and data
        if source=='authorized_keys':assert data==public_key
        files[target.lstrip('/')]=data;modes[target.lstrip('/')]=0o600
    with tarfile.open(out/'payload.tgz','w:gz') as tar:
        directories={'root','var/empty','var/run','var/log','var/db/dhcpleased'}
        for name in files:
            parent=Path(name).parent
            while str(parent)!='.':directories.add(parent.as_posix());parent=parent.parent
        for name in sorted(directories,key=lambda x:(x.count('/'),x)):
            item=tarfile.TarInfo(name);item.type=tarfile.DIRTYPE;item.mode=0o700 if name=='root' else 0o755;item.uid=item.gid=0;tar.addfile(item)
        for name,data in files.items():
            item=tarfile.TarInfo(name);item.mode=modes[name];item.uid=item.gid=0;item.size=len(data);tar.addfile(item,io.BytesIO(data))
    # Same native RAM-only preparation as the proven public candidate. This
    # script is copied into a new private FAT file; it is never sourced on host.
    preparation=(public/'prepare.sh').read_text().replace('/etc/daos/authorized_keys','/etc/ssh/authorized_keys')
    assert 'master.passwd' in preparation and 'fsck_ffs -fn' in preparation
    (out/'prepare.sh').write_text(preparation)
    fat=out/'payload-data-fat.img'
    with fat.open('xb') as f:f.truncate(64*1024**2)
    subprocess.run(['/usr/sbin/mkfs.vfat','-F','32','-n','DAOS_PRIV',str(fat)],check=True,capture_output=True)
    mcopy='/home/dy09/hf-openbsd-host-tools-001/root/usr/bin/mcopy'
    for path in (out/'payload.tgz',out/'prepare.sh'):
        subprocess.run([mcopy,'-i',str(fat),str(path),'::'+path.name],check=True)
    private_manifest={'format':'DaOS-OpenBSD80-private-physical-RAM-inputs-v1','nonce':nonce,'status':'NATIVE_PREPARATION_PENDING_NOT_INSTALLED',
        'payload_sha256':digest((out/'payload.tgz').read_bytes()),'client_private_key_opened_or_copied':False,
        'public_provision_metadata_sha256':digest(metadata_path.read_bytes()),'source_ram_init_sha256':digest(files['etc/daos/ram-init-trial.sh']),
        'files':{name:{'bytes':len(data),'sha256':digest(data),'mode':modes[name]} for name,data in files.items()}}
    (out/'inputs-private.json').write_text(json.dumps(private_manifest,indent=2)+'\n')
    receipt={'format':'DaOS-OpenBSD80-private-physical-RAM-preparation-public-v1','status':'PRIVATE_PREPARED_NOT_NATIVE_TESTED_NOT_INSTALLED',
        'nonce':nonce,'opaque_inputs_copied':4,'all_opaque_guest_modes':'0600','client_private_key_opened_or_copied':False,
        'private_outputs_only':True,'physical_actions':False,'AP_started':False,
        'source_ram_init_sha256':private_manifest['source_ram_init_sha256'],'private_output_directory':str(out),
        'requires':['Independent FDT provenance/EFI installer proof','Native readonly FFS QA','Physical owner review before any trial'],
        'limitations':['Stock SMALL_KERNEL lacks registered BCM hardware watchdog','Kernel/global hang requires manual reset',
            '120s userland fallback starts after kernel reaches RAM login shell','Wi-Fi and DHCP not proven by private shell fixtures']}
    (BASE/'PHYSICAL-RAM-PREPARED-PUBLIC.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))
if __name__=='__main__':main()

#!/usr/local/bin/python3.12
"""One fixed private OpenBSD trial, staged only from authenticated stable RAM.

plan/apply/rollback/reboot are separate. This utility has no generic block-write,
normal-root live-update, network, GPT, policy, calibration or photos operation.
The private bsd.rd is never printed, embedded in source, or copied to a bundle.
"""
from __future__ import annotations
import argparse
import errno
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys

NONCE='84c2b56c7596433c9755a4c9af7d6015'
FORMAT='DaOS-OpenBSD-RAM-stage-v1'
RAM=Path('/DaOS-recovery-ram/flash')
FLASH=Path('/usr/local/libexec/DaOS-recovery/flash.py')
FLASH_SHA='f7860e9432820f7056635aed1140a95ec1ec74df13fdfee808d651ae58822d5b'
NEXT_SHA='a996d4135a2de9489515098f36174ea0870e3a1970cca0a00104ec671f984b96'
PAYLOADS={
    'BOOTAA64.EFI':(239725,'620f98bfb82f5c4916ddfde0abfa48dc95b5cf108243e22659bcc1a91ca38d0c'),
    'bsd.rd':(19851545,'cc79a640c4dcc05601423ce0eb08b164b778ee987bc0194256f79bb6adf63bc0'),
    'd':(74248,'d5552f928cc2ec06e85ecfc7f77c31f9e6d86797ba72b965a811e8707530bcc2')}
BOOT_CONF=b'set tty fb0\nmachine dtb /DaOS-nextbsd/d\nboot esp0a:/DaOS-nextbsd/bsd.rd\n'
SELECTOR=('nextboot_enable="YES"\nautoboot_delay="NO"\nbeastie_disable="YES"\n'
          'daos_nextbsd_trial="'+NONCE+'"\nexec="include /boot/DaOS-nextbsd/once.lua"\n').encode()
CONSUMED=SELECTOR.replace(b'nextboot_enable="YES"',b'nextboot_enable="NO" ',1)
POLICY_DATA=b'DaOS-recovery-watchdog-v1\n'
# BINDINGS_FROM_C306: generated literal values, not a runtime import.
BINDINGS={'DISK': '/dev/mmcsd0',
 'DISK_BYTES': 63864569856,
 'DISK_UUID': 'd0a0ead0-d9ab-11f1-2887-fbd68f5c160e',
 'EFI_PINS': {'DaOS-recovery/kernel/daos_adb_template.ko': 'dfbfa4346cad57d2dc581f1b2b28f5389f221d386b6f58ca36a3364b76e19e67',
              'DaOS-recovery/kernel/kernel': '5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec',
              'DaOS-recovery/kernel/usb_template.ko': 'c385973ab0fd4483ad8238bfc760f4cdffc5a1b41213a7dca6ad4ce6be6f4fbc',
              'DaOS-recovery/loader.efi': '8567870cea56264ebee45c573196a04b4fadf5d7d36a52c303ba5336248b30f4',
              'DaOS-recovery/loader.lua': '2bd2b7bc031ff260590200b82bcfc960c837052db4ff0f9069eb2738f227f159',
              'DaOS-recovery/root.ufs.gz': 'e6af2f549a0c6935c83c17b0e1739bd6add522bcedff813e42b9be5d14ed8e2c',
              'EFI/BOOT/BOOTAA64.EFI': '7374ad7c46452c29e24f090e9ee2c9cc4ee5013a82c28f836b71113691fe0472',
              'boot.scr': '05e286e0bc3db1a6fdfdb80867d2c672bfc4f9f95ccc206badc023220e6fb447',
              'config.txt': '3cca8c524fcd65b4a8b3fd659106e0c67a49c314f4cb3dab145e70a74b633f0b',
              'u-boot-daos-rescue.bin': '592cd960b7d41a12a87de55d78492b467a174e0c381b6aa637f0a916d0bf8312',
              'u-boot.bin': '9ca13c64711d947be830382d50a8459b945e01626eaa4c497843d72448818b53'},
 'PARTITIONS': {1: {'label': 'DaOS-efi',
                    'sectors': 262144,
                    'start_lba': 40,
                    'type': 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b',
                    'uuid': 'd0a0e3aa-d9ab-11f1-067b-ff9d86f8dced'},
                2: {'label': 'DaOS-root',
                    'sectors': 5242880,
                    'start_lba': 262184,
                    'type': '516e7cb6-6ecf-11d6-8ff8-00022d09712b',
                    'uuid': 'd0a0e3be-d9ab-11f1-1d08-61407e6af4da'},
                3: {'label': 'DaOS-state',
                    'sectors': 262144,
                    'start_lba': 5505064,
                    'type': '516e7cb6-6ecf-11d6-8ff8-00022d09712b',
                    'uuid': 'd0a0e3be-d9ab-11f1-17a5-d14fff7c5968'},
                4: {'label': 'DaOS-calibration',
                    'sectors': 65536,
                    'start_lba': 5767208,
                    'type': '516e7cb6-6ecf-11d6-8ff8-00022d09712b',
                    'uuid': 'd0a0e3be-d9ab-11f1-0432-f1d3b03b0818'},
                5: {'label': 'DaOS-photos',
                    'sectors': 118900696,
                    'start_lba': 5832744,
                    'type': '516e7cb6-6ecf-11d6-8ff8-00022d09712b',
                    'uuid': 'd0a0e3be-d9ab-11f1-3fe7-9181628e1ee9'}},
 'ROOT_PINS': {'/boot/kernel/kernel': '5ac681d051cb623b23a724f91e6ecb818a883f034c8aa88eab678162221f2dec',
               '/boot/kernel/usb_template.ko': 'c385973ab0fd4483ad8238bfc760f4cdffc5a1b41213a7dca6ad4ce6be6f4fbc',
               '/boot/modules/daos_adb_template.ko': 'dfbfa4346cad57d2dc581f1b2b28f5389f221d386b6f58ca36a3364b76e19e67',
               '/etc/rc.d/DaOS-rescue-clean': 'bdda4eb930dbbca728a4fb443b026ce81acd950ae24d6f11cf063ff231ee75ec',
               '/sbin/init': '7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427',
               '/usr/local/libexec/DaOS-recovery/early-init.sh': 'f43adcab9d87d041cfa9df807b3826916749bd3ff2332dfb53ff240cfbcdb95f',
               '/usr/local/libexec/DaOS-recovery/kernel-watchdog': 'b06cdf2b16b375c6d287a48e3c7b7d8196b0a9cc9bcc6b12f1a3dd2db1fdcdac',
               '/usr/local/libexec/daos-usb-adb': 'd6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa'},
 'SECTOR': 512}

def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def check_disk_inventory(value):
    names=value.split()
    if len(names)!=len(set(names)) or set(names) not in ({'mmcsd0'},{'mmcsd0','md0'}):
        raise ValueError('Fixed EFI enumeration requires mmcsd0 and no extra disk')
def plain(path,*,directory=False):
    path=Path(path)
    if any(part.is_symlink() for part in (path,*path.parents)):raise ValueError('Redirected trial path')
    info=path.lstat()
    if info.st_uid!=0 or info.st_mode&0o022 or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode)):
        raise ValueError('Untrusted ordinary trial path')
    if not directory and info.st_nlink!=1:raise ValueError('Linked trial file')
    return path
def syncdir(path):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(fd)
    finally:os.close(fd)
def atomic(path,data,mode=0o600):
    plain(path.parent,directory=True)
    temporary=path.with_name('.'+path.name+'.'+NONCE+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),mode)
    opened=os.fstat(fd);identity=(opened.st_dev,opened.st_ino)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())
        os.replace(temporary,path);syncdir(path.parent)
    finally:
        if temporary.exists():
            current=temporary.lstat()
            if (current.st_dev,current.st_ino)==identity:temporary.unlink()
            else:raise RuntimeError('Unknown atomic temporary retained')

def lua_source():
    # Include only reads/registers; chain happens after checkNextboot consumes NO.
    return ('''local hook=require("hook")
local expected_yes=[==[ENABLED]==]
local expected_no=[==[DISABLED]==]
local function read(path)
 local file=io.open(path);if not file then return nil end
 local value=io.read(file);io.close(file);return value
end
local saw_yes=read("/boot/nextboot.conf")==expected_yes
local attempted=false
local function stop(reason)
 loader.setenv("autoboot_delay","NO");loader.setenv("beastie_disable","YES")
 print("DAOS_NEXTBSD_REFUSED "..reason);loader.perform("reboot")
 error("DAOS_NEXTBSD_RESET_RETURNED",0)
end
hook.register("config.loaded",function()
 if attempted then return stop("already-attempted") end;attempted=true
 if not saw_yes or loader.getenv("efi-version")==nil or
  loader.getenv("nextboot_conf")~="/boot/nextboot.conf" or
  loader.getenv("nextboot_enable")~="NO" or
  loader.getenv("daos_nextbsd_trial")~="NONCE" or
  read("/boot/nextboot.conf")~=expected_no or
  read("disk0p1:/DaOS-recovery/normal.running")~=string.char(1) then return stop("selector-or-marker") end
 print("DAOS_NEXTBSD_SELECTOR_CONSUMED NONCE")
 local status=loader.perform("chain disk0p1:/DaOS-nextbsd/BOOTAA64.EFI")
 return stop("chain-returned-"..tostring(status))
end)
'''.replace('ENABLED',SELECTOR.decode()).replace('DISABLED',CONSUMED.decode()).replace('NONCE',NONCE)).encode()

def fixed_record(original):
    return {'format':FORMAT,'nonce':NONCE,'state':'selector-will-be-published-last',
            'original_nextboot_sha256':NEXT_SHA,'original_nextboot_bytes':len(original),'original_nextboot_mode':0o644,
            'selector_sha256':digest(SELECTOR),'consumed_selector_sha256':digest(CONSUMED),
            'lua_sha256':digest(lua_source()),'boot_conf_sha256':digest(BOOT_CONF),
            'payloads':{name:{'bytes':size,'sha256':value} for name,(size,value) in PAYLOADS.items()},
            'disk_uuid':BINDINGS['DISK_UUID']}

class Transaction:
    def __init__(self,runtime):
        self.rt=runtime;self.created={};self.original=None;self.saved=None
        self.receipt=runtime.work/'receipt.json';self.record={'format':FORMAT,'nonce':NONCE,'status':'new','reboot_requested':False}
    def save(self):atomic(self.receipt,canonical(self.record))
    def mark(self,event):
        self.record['phase']=event;self.save();self.rt.checkpoint(event)
    def read_inputs(self,folder):
        self.rt.input_guard(folder)
        data={}
        for name,(size,value) in PAYLOADS.items():
            path=plain(folder/name)
            if path.stat().st_size!=size:raise ValueError('Pinned trial input size differs: '+name)
            body=path.read_bytes()
            if len(body)!=size or digest(body)!=value:raise ValueError('Pinned trial input bytes differ: '+name)
            data[name]=body
        return data
    def verify_root(self,root):
        for name,value in BINDINGS['ROOT_PINS'].items():
            if sha(plain(root/name.lstrip('/')))!=value:raise ValueError('Pinned normal-root file changed: '+name)
        policy=plain(root/'etc/daos/recovery-watchdog.enabled')
        if policy.read_bytes()!=POLICY_DATA or stat.S_IMODE(policy.stat().st_mode)!=0o444:raise ValueError('Production policy differs')
    def verify_efi(self,efi,marker):
        for name,value in BINDINGS['EFI_PINS'].items():
            if sha(plain(efi/name))!=value:raise ValueError('Pinned existing FAT file changed: '+name)
        if plain(efi/'DaOS-recovery/normal.running').read_bytes()!=bytes([marker]):raise ValueError('FAT marker differs')
    def plan(self,folder):
        self.rt.guard();data=self.read_inputs(folder)
        with self.rt.mounted('efi') as efi:
            self.verify_efi(efi,1)
            if any((efi/name).exists() or (efi/name).is_symlink() for name in ('DaOS-nextbsd','etc')):raise ValueError('New FAT namespace or etc already exists')
            need=sum(len(value) for value in data.values())+len(BOOT_CONF)+8*1024**2
            available=self.rt.available(efi)
            if available<need:raise ValueError('FAT available space below payload plus rollback margin')
        with self.rt.mounted('root') as root:
            self.verify_root(root)
            if (root/'boot/DaOS-nextbsd').exists() or (root/'boot/DaOS-nextbsd').is_symlink():raise ValueError('New UFS namespace already exists')
            path=plain(root/'boot/nextboot.conf');original=path.read_bytes()
            if digest(original)!=NEXT_SHA or stat.S_IMODE(path.stat().st_mode)!=0o644:raise ValueError('Bound disabled nextboot differs')
        proof=self.rt.guard()
        return data,original,{'format':FORMAT,'nonce':NONCE,'status':'planned','bytes_to_FAT':sum(map(len,data.values()))+len(BOOT_CONF),
                              'fat_available_bytes':available,'reboot_requested':False,'proof':proof}
    def new_file(self,path,data,mode=0o600):
        plain(path.parent,directory=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),mode)
        info=os.fstat(fd);self.created[str(path)]={'inode':(info.st_dev,info.st_ino),'data':data}
        try:
            with os.fdopen(fd,'wb') as file:
                os.fchmod(file.fileno(),mode);self.rt.write(file,data);file.flush();os.fsync(file.fileno())
            self.rt.sync_directory(path.parent)
            if sha(plain(path))!=digest(data):raise ValueError('New trial file readback mismatch')
        finally:self.rt.checkpoint('after-new-file:'+path.name)
    def mkdir(self,path):
        path.mkdir(mode=0o700);self.rt.sync_directory(path.parent)
    def apply(self,folder):
        data,original,planned=self.plan(folder)
        self.original=original;self.saved=fixed_record(original)
        self.record.update(planned,status='applying',boot_id=self.rt.boot_id(),persistent_record_sha256=digest(canonical(self.saved)))
        self.save()
        try:
            with self.rt.mounted('efi') as efi:
                self.verify_efi(efi,1);self.rt.writable('efi',efi)
                self.mkdir(efi/'DaOS-nextbsd');self.mkdir(efi/'etc')
                for name,body in data.items():self.new_file(efi/'DaOS-nextbsd'/name,body)
                self.new_file(efi/'etc/boot.conf',BOOT_CONF)
                self.mark('fat-payloads-verified')
            # msdosfs does not support directory fsync. A complete RO remount
            # after unmount verifies the flushed FAT namespace before UFS arm.
            with self.rt.mounted('efi') as efi:
                self.verify_efi(efi,1);self.verify_staged(efi);self.mark('fat-unmounted-readback')
            with self.rt.mounted('root') as root:
                self.verify_root(root)
                if sha(plain(root/'boot/nextboot.conf'))!=NEXT_SHA:raise ValueError('Selector changed before publication')
                self.rt.writable('root',root);self.mkdir(root/'boot/DaOS-nextbsd')
                self.new_file(root/'boot/DaOS-nextbsd/nextboot.original',original)
                self.new_file(root/'boot/DaOS-nextbsd/once.lua',lua_source())
                self.new_file(root/'boot/DaOS-nextbsd/transaction.json',canonical(self.saved))
                self.mark('before-selector-publication')
                atomic(root/'boot/nextboot.conf',SELECTOR,0o644)
                if plain(root/'boot/nextboot.conf').read_bytes()!=SELECTOR:raise ValueError('Published selector readback failed')
                self.mark('selector-published')
            with self.rt.mounted('efi') as efi:
                self.verify_efi(efi,1);self.verify_staged(efi)
                self.rt.writable('efi',efi);self.marker(efi,1,0)
                self.mark('marker-cleared')
            self.record.update(status='armed',post_proof=self.rt.guard());self.save()
            return self.record
        except BaseException as error:
            self.record['failure']=type(error).__name__;self.record['status']='failed'
            try:self.rollback(internal=True)
            except BaseException as cleanup:self.record.update(status='failed-rollback-incomplete',cleanup_error=type(cleanup).__name__)
            try:self.save()
            except BaseException:pass
            return self.record
    def verify_staged(self,efi):
        folder=plain(efi/'DaOS-nextbsd',directory=True)
        if set(path.name for path in folder.iterdir())!=set(PAYLOADS):raise ValueError('Unexpected staged FAT namespace entry')
        for name,(size,value) in PAYLOADS.items():
            path=plain(folder/name)
            if path.stat().st_size!=size or sha(path)!=value:raise ValueError('Staged private payload changed')
        if set(path.name for path in plain(efi/'etc',directory=True).iterdir())!={'boot.conf'} or plain(efi/'etc/boot.conf').read_bytes()!=BOOT_CONF:raise ValueError('New FAT boot.conf namespace changed')
    def marker(self,efi,before,after):
        path=plain(efi/'DaOS-recovery/normal.running')
        if path.read_bytes()!=bytes([before]):raise ValueError('Marker changed before byte update')
        fd=os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0))
        try:
            if os.write(fd,bytes([after]))!=1:raise OSError('Short marker update')
            os.fsync(fd);os.lseek(fd,0,os.SEEK_SET)
            if os.read(fd,2)!=bytes([after]):raise OSError('Marker readback differs')
        finally:os.close(fd)
        os.sync()
    def remove_file(self,path,expected,*,internal):
        if not path.exists() and not path.is_symlink():return
        plain(path);body=path.read_bytes()
        owner=self.created.get(str(path))
        owned_partial=internal and owner and (path.stat().st_dev,path.stat().st_ino)==owner['inode'] and body==owner['data'][:len(body)]
        if digest(body)!=digest(expected) and not owned_partial:raise ValueError('Unknown changed file retained')
        path.unlink();self.rt.sync_directory(path.parent)
    def rollback(self,*,internal=False):
        try:return self._rollback(internal=internal)
        except BaseException as error:
            self.record.update(status='rollback-incomplete',rollback_errors=[type(error).__name__])
            try:self.record['post_proof']=self.rt.guard(cleanup=internal)
            except BaseException as proof_error:self.record['post_proof_error']=type(proof_error).__name__
            try:self.save()
            except BaseException:self.record['receipt_error']=True
            return self.record
    def _rollback(self,*,internal=False):
        self.rt.guard(cleanup=internal);errors=[]
        # Arm the existing independent rescue branch before any rollback that
        # might be refused because a selector or new namespace was tampered.
        with self.rt.mounted('efi') as efi:
            for name,value in BINDINGS['EFI_PINS'].items():
                if sha(plain(efi/name))!=value:raise ValueError('Pinned EFI changed before rollback')
            flag=plain(efi/'DaOS-recovery/normal.running').read_bytes()
            if flag not in (b'\0',b'\1'):raise ValueError('Invalid rollback marker')
            if flag==b'\0':self.rt.writable('efi',efi);self.marker(efi,0,1)
        # Restore selector before removing anything it could execute.
        with self.rt.mounted('root') as root:
            self.verify_root(root);folder=root/'boot/DaOS-nextbsd'
            if self.original is None:
                if not folder.exists() and sha(plain(root/'boot/nextboot.conf'))==NEXT_SHA:
                    self.original=plain(root/'boot/nextboot.conf').read_bytes()
                else:self.original=plain(folder/'nextboot.original').read_bytes()
                if digest(self.original)!=NEXT_SHA:raise ValueError('Original selector backup differs')
                self.saved=fixed_record(self.original)
                if folder.exists() and plain(folder/'transaction.json').read_bytes()!=canonical(self.saved):raise ValueError('Persistent ownership record differs')
            current=plain(root/'boot/nextboot.conf').read_bytes()
            if current not in (self.original,SELECTOR,CONSUMED):raise ValueError('Unknown selector retained; rollback refused')
            if current!=self.original or folder.exists():self.rt.writable('root',root)
            if current!=self.original:atomic(root/'boot/nextboot.conf',self.original,0o644)
            if sha(root/'boot/nextboot.conf')!=NEXT_SHA:raise ValueError('Original selector restoration failed')
            if folder.exists():
                for name,body in [('once.lua',lua_source()),('transaction.json',canonical(self.saved)),('nextboot.original',self.original)]:
                    try:self.remove_file(folder/name,body,internal=internal)
                    except BaseException as error:errors.append(type(error).__name__)
                try:folder.rmdir();self.rt.sync_directory(folder.parent)
                except OSError:errors.append('UFSNamespaceRetained')
        with self.rt.mounted('efi') as efi:
            # Marker1 is enforced even if later unknown private files are kept.
            for name,value in BINDINGS['EFI_PINS'].items():
                if sha(plain(efi/name))!=value:raise ValueError('Pinned EFI changed during rollback')
            current=plain(efi/'DaOS-recovery/normal.running').read_bytes()
            if current not in (b'\0',b'\1'):raise ValueError('Invalid marker retained')
            self.rt.writable('efi',efi)
            if current==b'\0':self.marker(efi,0,1)
            for name in PAYLOADS:
                path=efi/'DaOS-nextbsd'/name
                expected=self.created.get(str(path),{}).get('data')
                if expected is None:
                    if path.exists() and sha(plain(path))==PAYLOADS[name][1]:path.unlink();self.rt.sync_directory(path.parent)
                    elif path.exists():errors.append('UnknownFATPayloadRetained')
                else:
                    try:self.remove_file(path,expected,internal=internal)
                    except BaseException as error:errors.append(type(error).__name__)
            try:self.remove_file(efi/'etc/boot.conf',BOOT_CONF,internal=internal)
            except BaseException as error:errors.append(type(error).__name__)
            for name in ('DaOS-nextbsd','etc'):
                try:
                    if (efi/name).exists():(efi/name).rmdir();self.rt.sync_directory(efi)
                except OSError:errors.append('FATNamespaceRetained')
        proof=self.rt.guard(cleanup=internal)
        self.record.update(status='rollback-incomplete' if errors else 'rolled-back-marker-one',post_proof=proof,rollback_errors=errors)
        self.save();return self.record
    def reboot(self):
        record=json.loads(plain(self.receipt).read_text())
        if record.get('status')!='armed' or record.get('nonce')!=NONCE or record.get('boot_id')!=self.rt.boot_id():raise ValueError('Complete same-boot armed receipt required')
        self.rt.guard()
        with self.rt.mounted('efi') as efi:self.verify_efi(efi,0);self.verify_staged(efi)
        with self.rt.mounted('root') as root:
            self.verify_root(root)
            if plain(root/'boot/nextboot.conf').read_bytes()!=SELECTOR or plain(root/'boot/DaOS-nextbsd/once.lua').read_bytes()!=lua_source():raise ValueError('Armed selector or callback changed')
            original=plain(root/'boot/DaOS-nextbsd/nextboot.original').read_bytes()
            if digest(original)!=NEXT_SHA or plain(root/'boot/DaOS-nextbsd/transaction.json').read_bytes()!=canonical(fixed_record(original)):raise ValueError('Armed persistent backup changed')
        self.rt.guard();self.record.update(record,reboot_requested=True,status='reboot-requested');self.save()
        self.rt.detach_reboot();return self.record

class Native:
    def __init__(self):
        if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise ValueError('Native FreeBSD uid0 RAM rescue required')
        if sha(plain(FLASH))!=FLASH_SHA:raise ValueError('Installed native proof module differs')
        spec=importlib.util.spec_from_file_location('fixed_ram_flash',FLASH)
        self.flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.flash)
        self.backend=self.flash.NativeBackend();self.work=RAM/('newbsd-'+NONCE);self.authorized=False;self.owned_mount=None
    def command(self,args):
        result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError('Native recovery command failed: '+str(args[0])+' '+result.stderr[-400:])
        return result.stdout.strip()
    def rows(self):
        rows=[line.split() for line in self.command(['/rescue/mount','-p']).splitlines() if line.strip()]
        if any(len(row)!=6 for row in rows):raise ValueError('Malformed mount inventory')
        return rows
    def check_mounts(self,role=None,path=None,mode=None):
        rows=self.rows();roots=[row for row in rows if row[1]=='/']
        if len(roots)!=1 or roots[0][0]!='/dev/md0' or roots[0][2]!='ufs':raise ValueError('Unique stable md0 root required')
        seen=[]
        for row in rows:
            if row[1]=='/':
                if row[0]!='/dev/md0' or row[2]!='ufs':raise ValueError('Stable resident md0 root required')
            elif path is not None and row[1]==str(path):
                provider=BINDINGS['DISK']+('p1' if role=='efi' else 'p2')
                if row[0]!=provider or row[2]!=('msdosfs' if role=='efi' else 'ufs') or mode not in row[3].split(',') or ('rw' if mode=='ro' else 'ro') in row[3].split(','):
                    raise ValueError('Owned physical mount differs')
                seen.append(row)
            elif row[2] not in ('tmpfs','devfs','procfs','fdescfs'):raise ValueError('Every physical filesystem must be unmounted')
        if path is not None and len(seen)!=1:raise ValueError('Owned mount missing')
    def query_watchdog(self):
        import fcntl
        fd=os.open('/dev/fido',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Watchdog character device required')
            data=bytearray(8);fcntl.ioctl(fd,0x40085736,data,True)
            return struct.unpack('=q',data)[0]
        finally:os.close(fd)
    def boot_id(self):return self.command(['/rescue/sysctl','-n','kern.boottime'])
    def guard(self,*,cleanup=False):
        if self.command(['/rescue/sysctl','-n','vm.swap_total'])!='0':raise ValueError('Active or malformed swap inventory refused')
        target=self.backend.prove('root');self.check_mounts()
        if self.command(['/rescue/mdconfig','-l','-v','-u','0']).split()[:2]!=['md0','preload']:raise ValueError('Preloaded stable RAM root required')
        if self.command(['/rescue/sysctl','-n','kern.bootfile']) not in ('/DaOS-recovery/kernel/kernel','/boot/kernel/kernel'):raise ValueError('Stable recovery kernel path differs')
        if sha(plain('/boot/kernel/kernel'))!=BINDINGS['ROOT_PINS']['/boot/kernel/kernel'] or sha(plain('/usr/local/libexec/daos-usb-adb'))!=BINDINGS['ROOT_PINS']['/usr/local/libexec/daos-usb-adb']:
            raise ValueError('Resident stable kernel or production USB helper differs')
        # md devices are omitted from kern.disks on the actual stable kernel.
        # Their root backing is independently proved as md0 preload above.
        check_disk_inventory(self.command(['/rescue/sysctl','-n','kern.disks']))
        if self.command(['/rescue/sysctl','-n','dev.bcmwd.0.%driver'])!='bcmwd' or self.query_watchdog()!=0:raise ValueError('BCM watchdog must remain GET0')
        fd=os.open(BINDINGS['DISK'],os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise ValueError('Bound card character device required')
            if int(self.command(['/usr/sbin/diskinfo',BINDINGS['DISK']]).split()[2])!=BINDINGS['DISK_BYTES']:raise ValueError('Bound card size differs')
            disk_uuid,parts=self.flash.parse_gpt(fd,BINDINGS['DISK_BYTES'],BINDINGS['SECTOR'])
        finally:os.close(fd)
        if disk_uuid!=BINDINGS['DISK_UUID'] or parts!=BINDINGS['PARTITIONS']:raise ValueError('Exact five-partition dual GPT differs')
        if not cleanup:
            health=json.loads(self.command(['/usr/local/bin/python3.12','-I','-B','/usr/local/libexec/DaOS-recovery/early_adb.py','health']))
            if health.get('format')!='DaOS-early-recovery-adb-health-v1' or any(health.get(name)is not True for name in ('ready','supervisor_alive','host_configured','adb_authenticated')):
                raise ValueError('Existing USB must currently be authenticated')
            self.authorized=True
        elif not self.authorized:raise ValueError('Internal cleanup requires prior authenticated entry')
        return {'disk_uuid':disk_uuid,'partitions_checked':5,'watchdog_GET':0,'ram_md0':True,'boot_id':self.boot_id()}
    def input_guard(self,path):
        path=plain(path,directory=True)
        if path.parent!=RAM/'incoming' or set(child.name for child in path.iterdir())!=set(PAYLOADS):raise ValueError('Dedicated three-file RAM incoming directory required')
    def available(self,path):
        value=os.statvfs(path);return value.f_bavail*value.f_frsize
    def fat_mount_command(self,path,*,readonly,update=False):
        # Generic mount execs an external mount_msdosfs helper, absent from the
        # frozen RAM root. The crunched applet is resident. Each -o is one
        # nmount option; this applet does not split comma-separated options.
        args=['/rescue/rescue','mount_msdosfs']
        if update:args+=['-o','update']
        args+=['-o','ro' if readonly else 'noro','-u','0','-g','0','-m','0700','-M','0700',BINDINGS['DISK']+'p1',path]
        return args
    def writable(self,role,path):
        args=self.fat_mount_command(path,readonly=False,update=True) if role=='efi' else ['/rescue/mount','-u','-w',path]
        self.command(args);self.check_mounts(role,path,'rw')
    def readonly(self,role,path):
        args=self.fat_mount_command(path,readonly=True,update=True) if role=='efi' else ['/rescue/mount','-u','-r',path]
        self.command(args);self.check_mounts(role,path,'ro')
    def sync_directory(self,path):
        owned=self.owned_mount
        if owned is not None and owned[0]=='efi' and Path(path).is_relative_to(owned[1]):
            self.check_mounts('efi',owned[1],'rw')
            try:syncdir(path)
            except OSError as error:
                if error.errno!=errno.EINVAL:raise
                # Verified msdosfs only. All file fsyncs remain mandatory; the
                # transaction also unmounts then verifies on a fresh RO mount.
                os.sync()
        else:syncdir(path)
    @contextmanager
    def mounted(self,role):
        self.check_mounts();path=self.work/role
        path.mkdir(mode=0o700,exist_ok=True);plain(path,directory=True)
        provider=BINDINGS['DISK']+('p1' if role=='efi' else 'p2')
        attempted=False;cleanup_errors=[]
        try:
            attempted=True
            if role=='efi':args=self.fat_mount_command(path,readonly=True)
            else:args=['/rescue/mount','-t','ufs','-o','ro',provider,path]
            self.command(args);self.owned_mount=(role,path)
            self.check_mounts(role,path,'ro')
            yield path
        finally:
            # Discover actual state after even a failed mount command. Never
            # assume an exception means the mount did not happen.
            rows=[row for row in self.rows() if row[1]==str(path)] if attempted else []
            if rows:
                if len(rows)!=1 or rows[0][0]!=provider:raise RuntimeError('Unknown mount retained at private mountpoint')
                try:self.readonly(role,path)
                except BaseException as error:cleanup_errors.append(type(error).__name__+': '+str(error)[-240:])
                try:self.command(['/rescue/umount',path]);self.owned_mount=None
                except BaseException as error:cleanup_errors.append(type(error).__name__+': '+str(error)[-240:])
            self.check_mounts()
            if cleanup_errors:raise RuntimeError('Physical mount cleanup incomplete: '+','.join(cleanup_errors))
    def checkpoint(self,event):pass
    def write(self,file,data):
        if file.write(data)!=len(data):raise OSError('Short staged write')
    def detach_reboot(self):
        os.sync()
        subprocess.Popen(['/rescue/sh','-c','/rescue/sleep 1; exec /rescue/reboot -qn'],stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','apply','rollback','reboot'));parser.add_argument('--inputs',type=Path)
    args=parser.parse_args(argv)
    if args.action in ('plan','apply') and args.inputs is None:parser.error('--inputs is required for plan/apply')
    runtime=Native();runtime.guard()
    RAM.mkdir(mode=0o700,exist_ok=True);plain(RAM,directory=True)
    runtime.work.mkdir(mode=0o700,exist_ok=True);plain(runtime.work,directory=True)
    for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(number,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt('Trial interrupted')))
    with runtime.flash.lock(RAM):
        runtime.guard();transaction=Transaction(runtime)
        if args.action=='plan':result=transaction.plan(args.inputs)[2]
        elif args.action=='apply':result=transaction.apply(args.inputs)
        elif args.action=='rollback':result=transaction.rollback()
        else:result=transaction.reboot()
    print(json.dumps(result,separators=(',',':')),flush=True)
    expected={'plan':'planned','apply':'armed','rollback':'rolled-back-marker-one','reboot':'reboot-requested'}
    return 0 if result['status']==expected[args.action] else 1

if __name__=='__main__':raise SystemExit(main())

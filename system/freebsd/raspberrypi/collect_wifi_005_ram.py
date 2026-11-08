#!/usr/local/bin/python3.12
"""Bounded no-radio evidence collection from the separately selected 005 RAM boot.

This program never mounts, loads a module, reads a card provider, changes a
sysctl/kenv value, arms a watchdog, or requests a reboot. Candidate SHA checks
cover copies uploaded to owned tmpfs. Running-kernel provenance additionally
depends on the separately verified one-shot selector; it is not a measurement
of resident kernel memory.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import sys
import time

FORMAT = 'DaOS-wifi005-RAM-no-RF-v1'
RAM = Path('/DaOS-recovery-ram')
BOOTFILE = '/boot/kernel.daos-wifi-005-ramprobe/kernel'
KERNEL_DIR = '/boot/kernel.daos-wifi-005-ramprobe'
RAM_GZIP_SHA = 'e6af2f549a0c6935c83c17b0e1739bd6add522bcedff813e42b9be5d14ed8e2c'
PINS = {
    'kernel': (19595896, 'f966d6d2211cd6df06d1f9a0a50c520450f812c53662cddf7f91da0488e2e012'),
    'usb_template.ko': (76856, 'c385973ab0fd4483ad8238bfc760f4cdffc5a1b41213a7dca6ad4ce6be6f4fbc'),
    'daos_adb_template.ko': (10184, 'dfbfa4346cad57d2dc581f1b2b28f5389f221d386b6f58ca36a3364b76e19e67'),
}
HELPER = '/usr/local/libexec/daos-usb-adb'
HELPER_PIN = (17616, 'd6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa')
SUPERVISOR = '/usr/local/libexec/DaOS-recovery/early_adb.py'
SUPERVISOR_PIN = (14940, '9346d4f39c01a8c8b8e0c3077553253357e91df864454724a6d5d0031e6c1288')
INIT_SCRIPT = '/usr/local/libexec/DaOS-recovery/early-init.sh'
MAX_REPORT = 24576
MAX_COMMAND = 1024 * 1024
ERRORS = re.compile(r'(?i)(g_vfs_done|I/O error|Input/output error|pager read error|panic:|CAM status:|(?:mmc|sdhci)[^\n]*(?:timeout|error))')
CONTROLLER = re.compile(r'^dev\.sdhci_bcm\.(\d+)\.slot0\.quirks$')
TIMEOUT_REGISTER_ROW = re.compile(r'^sdhci_bcm[01]-slot0: Timeout:\s+0x([0-9a-fA-F]{8}) \| Int stat: 0x([0-9a-fA-F]{8})$')
SDHCI_INT_ERROR_MASK = 0xffff8000  # pinned sys/dev/sdhci/sdhci.h
# modfind/kldstat -m uses moduledata.name, not MDT_VERSION dependency labels.
# pci exports version label pci but registers pcib/pci. PERIPHDRIVER_DECLARE
# stringifies SDIOB_NAME for moduledata.name while MODULE_VERSION __XSTRING
# expands it to the dependency label sdiob. Both are built into pinned 005.
DEPENDENCY_MODULES = {'pci':'pcib/pci','firmware':'firmware','wlan':'wlan','sdiob':'SDIOB_NAME'}


class Refused(ValueError):
    pass


class Runner:
    """Read commands with aggregate deadline and bounded pipes, including stderr."""
    def __init__(self, seconds=30):
        self.deadline = time.monotonic() + seconds

    def run(self, args, limit=65536, accepted=(0,)):
        remaining = min(4, self.deadline - time.monotonic())
        if remaining <= 0:
            raise Refused('Collection deadline reached')
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={'PATH':'/rescue:/sbin:/bin:/usr/sbin:/usr/bin',
                                'LD_LIBRARY_PATH':'/lib:/usr/lib:/usr/local/lib', 'LC_ALL':'C'})
        streams = selectors.DefaultSelector()
        chunks = {'out':bytearray(), 'err':bytearray()}
        stop = time.monotonic() + remaining
        try:
            for file, name in ((proc.stdout,'out'), (proc.stderr,'err')):
                os.set_blocking(file.fileno(), False)
                streams.register(file, selectors.EVENT_READ, name)
            while streams.get_map():
                left = stop - time.monotonic()
                if left <= 0:
                    raise Refused('Read-only command timed out: '+args[0])
                for key, _ in streams.select(min(left, .1)):
                    data = os.read(key.fd, 16384)
                    if not data:
                        streams.unregister(key.fileobj)
                    else:
                        chunks[key.data].extend(data)
                        if sum(map(len, chunks.values())) > min(limit, MAX_COMMAND):
                            raise Refused('Read-only command exceeded its output bound: '+args[0])
            code = proc.wait(timeout=max(.01, stop-time.monotonic()))
            if code not in accepted:
                raise Refused('Read-only command failed: '+args[0]+' rc='+str(code))
            if chunks['err']:
                raise Refused('Read-only command reported stderr: '+args[0])
            return code, bytes(chunks['out'])
        finally:
            streams.close()
            if proc.poll() is None:
                proc.kill()
                try:proc.wait(timeout=.2)
                except subprocess.TimeoutExpired:pass
            for file in (proc.stdout,proc.stderr):file.close()

    def text(self, args, **kwargs):
        code, data = self.run(args, **kwargs)
        try:return code, data.decode('utf-8').strip()
        except UnicodeDecodeError as error:raise Refused('Invalid command text') from error


def native_kenv(name):
    # kenv(1) returns255 when absent; errno from libc is the precise API.
    libc = ctypes.CDLL(None, use_errno=True)
    api = libc.kenv
    api.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
    api.restype = ctypes.c_int
    data = ctypes.create_string_buffer(4096)
    ctypes.set_errno(0)
    count = api(0, name.encode('ascii'), data, len(data))
    if count < 0:
        number = ctypes.get_errno()
        raise Refused('Required kenv unavailable: '+name+' errno='+str(number))
    if not 1 <= count < len(data) or data.raw[count-1:count] != b'\0' or b'\0' in data.raw[:count-1]:
        raise Refused('Invalid kenv length/value: '+name)
    try:return data.raw[:count-1].decode('utf-8')
    except UnicodeDecodeError as error:raise Refused('Invalid kenv encoding') from error


def mounts(text):
    rows = [line.split() for line in text.splitlines() if line.strip()]
    if not rows or any(len(row)!=6 for row in rows):raise Refused('Malformed mount table')
    for target in {row[1] for row in rows}:
        same=[row for row in rows if row[1]==target]
        if len(same)>1 and not (target=='/dev' and all(row==same[0] for row in same)
                               and same[0][0]=='devfs' and same[0][2]=='devfs'):
            raise Refused('Duplicate mount target')
    return rows


def isolation(rows):
    roots = [row for row in rows if row[1]=='/']
    if len(roots)!=1 or roots[0][0]!='/dev/md0' or roots[0][2]!='ufs':
        raise Refused('Exact /dev/md0 UFS RAM root required')
    for source, target, kind, options, _, _ in rows:
        modes = options.split(',')
        if ('ro' in modes)==('rw' in modes):raise Refused('Ambiguous filesystem mode')
        if target=='/':continue
        if kind=='devfs' and source=='devfs' and target=='/dev':continue
        if kind=='tmpfs' and source=='tmpfs' and target in ('/DaOS-recovery-ram','/tmp','/run','/var'):continue
        raise Refused('Physical, aliased, or unknown mount is present: '+target)
    ram = [row for row in rows if row[1]==str(RAM)]
    if len(ram)!=1 or ram[0][2]!='tmpfs' or 'rw' not in ram[0][3].split(','):
        raise Refused('Dedicated writable recovery tmpfs required')
    return rows


def trusted(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute():raise Refused('Absolute owned path required')
    for p in (path,*path.parents):
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
            raise Refused('Untrusted or redirected RAM path')
    s = path.lstat()
    if directory:
        if not stat.S_ISDIR(s.st_mode) or stat.S_IMODE(s.st_mode)!=0o700:
            raise Refused('Owned 0700 directory required')
    elif not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:
        raise Refused('Owned ordinary unlinked file required')
    return path


def pinned(path, pin):
    trusted(path)
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022 or info.st_size!=pin[0]:
            raise Refused('Artifact size/ownership changed: '+Path(path).name)
        digest = hashlib.sha256(); total=0
        while True:
            block=os.read(fd,262144)
            if not block:break
            total+=len(block)
            if total>pin[0]:raise Refused('Artifact grew during hashing')
            digest.update(block)
        after=os.fstat(fd)
        if total!=pin[0] or digest.hexdigest()!=pin[1] or (info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise Refused('Artifact hash changed: '+Path(path).name)
    finally:os.close(fd)
    return {'bytes':pin[0],'sha256':pin[1]}


def fdt_properties(data):
    """Read a bounded flattened device tree; no external parser or filesystem IO."""
    if not 40<=len(data)<=131072:raise Refused('Invalid runtime FDT size')
    magic,total,off_struct,off_strings,_,version,compatible,_,size_strings,size_struct=struct.unpack_from('>10I',data)
    if magic!=0xd00dfeed or not 17<=version or compatible>17 or not 40<=total<=len(data):raise Refused('Invalid FDT header')
    if not 40<=off_struct<total or off_struct+size_struct>total or not 40<=off_strings<total or off_strings+size_strings>total:
        raise Refused('FDT region exceeds blob')
    strings=data[off_strings:off_strings+size_strings];end=off_struct+size_struct;cursor=off_struct;stack=[];nodes={};ended=False
    while cursor+4<=end:
        token=struct.unpack_from('>I',data,cursor)[0];cursor+=4
        if token==1:
            zero=data.find(b'\0',cursor,end)
            if zero<0 or zero-cursor>255:raise Refused('Invalid FDT node')
            name=data[cursor:zero].decode('ascii');cursor=(zero+4)&~3
            if '/' in name or len(stack)>32 or (not stack and name):raise Refused('Invalid FDT node path')
            stack.append(name);path='/'.join(stack) or '/'
            if path in nodes:raise Refused('Duplicate FDT node')
            nodes[path]={}
        elif token==2:
            if not stack:raise Refused('FDT node stack underflow')
            stack.pop()
        elif token==3:
            if cursor+8>end or not stack:raise Refused('Invalid FDT property')
            length,offset=struct.unpack_from('>II',data,cursor);cursor+=8
            if cursor+length>end or offset>=len(strings):raise Refused('FDT property exceeds blob')
            zero=strings.find(b'\0',offset)
            if zero<0:raise Refused('Invalid FDT property name')
            name=strings[offset:zero].decode('ascii');props=nodes['/'.join(stack) or '/']
            if name in props:raise Refused('Duplicate FDT property')
            props[name]=data[cursor:cursor+length];cursor=(cursor+length+3)&~3
        elif token==4:continue
        elif token==9:
            if stack:raise Refused('Unclosed FDT node')
            ended=True;break
        else:raise Refused('Unknown FDT token')
    if not ended:raise Refused('FDT end missing')
    return nodes


def dt_text(props,key,default=None):
    value=props.get(key)
    if value is None:return default
    if not value.endswith(b'\0'):raise Refused('Unterminated FDT string')
    return value[:-1].decode('ascii')


def validate_fdt(data):
    nodes=fdt_properties(data)
    if 'brcm,bcm2711' not in dt_text(nodes['/'],'compatible','').split('\0'):
        raise Refused('BCM2711 board required')
    expected={'/soc/mmc@7e202000':'disabled','/soc/mmc@7e300000':'disabled',
              '/soc/mmcnr@7e300000':'okay','/emmc2bus/mmc@7e340000':'okay'}
    result={}
    for path,status in expected.items():
        props=nodes.get(path)
        if props is None or dt_text(props,'status','okay')!=status:raise Refused('Unexpected controller DT status: '+path)
        result[path]={'status':status,'compatible':dt_text(props,'compatible','').split('\0'),'reg_hex':props.get('reg',b'').hex()}
    aliases=nodes.get('/aliases',{})
    if dt_text(aliases,'mmc0')!='/emmc2bus/mmc@7e340000' or dt_text(aliases,'mmc1')!='/soc/mmcnr@7e300000':
        raise Refused('Physical SD/Wi-Fi aliases differ from captured board')
    wifi=nodes['/soc/mmcnr@7e300000'];power=nodes.get('/daos-wifi-pwrseq',{})
    if not wifi.get('mmc-pwrseq') or wifi['mmc-pwrseq']!=power.get('phandle') or dt_text(power,'compatible')!='mmc-pwrseq-simple':
        raise Refused('Bound Wi-Fi power sequence overlay missing')
    return {'sha256':hashlib.sha256(data).hexdigest(),'controllers':result,'aliases_verified':True,'wifi_pwrseq_verified':True}


def resident_dependencies(text):
    header=re.compile(r'^\s*(\d+)\s+\d+\s+0x[0-9a-f]+\s+[0-9a-f]+\s+(\S+)(?:\s+\(([^)]+)\))?\s*$',re.I)
    module=re.compile(r'^\s+(\d+)\s+(\S+)\s*$')
    active=False;found={};kernels=0
    for line in text.splitlines():
        match=header.fullmatch(line)
        if match:
            active=match.group(2)=='kernel' and match.group(3)==BOOTFILE
            if active:kernels+=1
        elif active and (match:=module.fullmatch(line)):
            if match.group(2) in found:raise Refused('Duplicate registered kernel module')
            found[match.group(2)]=int(match.group(1))
    if kernels!=1 or not set(DEPENDENCY_MODULES.values())<=set(found):
        raise Refused('Required dependencies are not registered in the selected kernel')
    return {label:{'registered_name':name,'module_id':found[name],'resident_file':BOOTFILE}
            for label,name in DEPENDENCY_MODULES.items()}


def dmesg_evidence(text):
    fields=[]
    for line in text.splitlines():
        match=TIMEOUT_REGISTER_ROW.fullmatch(line)
        if match:
            # This exact dump row prints the timeout-control register, not a
            # timeout event. Permit it only when the status has NO error bits.
            if int(match.group(1),16)>0xff or int(match.group(2),16)&SDHCI_INT_ERROR_MASK:
                raise Refused('SDHCI register dump reports invalid control/error status')
            fields.append(line)
        elif ERRORS.search(line):
            raise Refused('Current boot contains storage/CAM errors')
    return {'normal_timeout_register_rows':len(fields),'normal_timeout_register_examples':fields[-4:]}


def collect(artifacts, nonce, *, runner=None, getenv=native_kenv, hash_file=pinned, native=True):
    if not re.fullmatch('[a-f0-9]{32}',nonce):raise Refused('Exact trial nonce required')
    if str(artifacts)!=str(RAM/('wifi005-no-rf-'+nonce)):raise Refused('Fixed owned RAM artifact directory required')
    if native and (not sys.platform.startswith('freebsd') or os.geteuid()!=0 or os.uname().machine not in ('arm64','aarch64')):
        raise Refused('Native FreeBSD ARM64 root required')
    runner=runner or Runner()
    def text(args, **kw):return runner.text(args,**kw)[1]
    before=isolation(mounts(text(['/rescue/mount','-p'],limit=32768)))
    desc=text(['/sbin/mdconfig','-l','-v','-u','0'],limit=4096).split()
    if len(desc)<2 or desc[0]!='md0' or desc[1] not in ('preload','malloc'):
        raise Refused('Root image is not resident RAM')
    if text(['/sbin/sysctl','-n','vm.swap_total'],limit=128)!='0':raise Refused('Active swap is forbidden')
    trusted(RAM,directory=True);trusted(artifacts,directory=True)
    if set(p.name for p in Path(artifacts).iterdir())!=set(PINS):raise Refused('Only three pinned boot artifacts may be supplied')
    values={'daos_recovery':'YES','daos_wifi_trial':'005','hw.bcm2835.sdhci.pio_mode':'1',
            'init_script':INIT_SCRIPT,'nextboot_enable':'NO','sdio_load':'NO','if_brcmfmac_load':'NO','rpi_ft5406_load':'NO',
            'daos_wifi005_no_rf':'YES','daos_wifi005_trial_nonce':nonce,
            'daos_wifi005_kernel_sha256':PINS['kernel'][1],'daos_wifi005_ram_gzip_sha256':RAM_GZIP_SHA}
    for key,value in values.items():
        if getenv(key)!=value:raise Refused('Unexpected required boot setting: '+key)
    bootfile=text(['/sbin/sysctl','-n','kern.bootfile'],limit=1024)
    if bootfile!=BOOTFILE:raise Refused('005 trial bootfile differs')
    module_path=text(['/sbin/sysctl','-n','kern.module_path'],limit=4096)
    if not module_path or any(p!=KERNEL_DIR for p in module_path.split(';')):raise Refused('Unexpected module search path')
    hashes={name:hash_file(Path(artifacts)/name,pin) for name,pin in PINS.items()}
    hashes['helper']=hash_file(HELPER,HELPER_PIN)
    hash_file(SUPERVISOR,SUPERVISOR_PIN)
    dependencies=resident_dependencies(text(['/sbin/kldstat','-v'],limit=65536))
    for module in DEPENDENCY_MODULES.values():
        runner.run(['/sbin/kldstat','-q','-m',module],limit=1024)
    for module in ('usb_template.ko','daos_adb_template.ko'):
        runner.run(['/sbin/kldstat','-q','-n',module],limit=1024)
    code,_=runner.run(['/sbin/kldstat','-q','-n','if_brcmfmac.ko'],limit=1024,accepted=(0,1))
    if code!=1 or text(['/sbin/sysctl','-n','net.wlan.devices'],limit=1024):raise Refused('Radio already attached')
    health=json.loads(text(['/usr/local/bin/python3.12','-I','-B',SUPERVISOR,'health'],limit=16384))
    if health.get('format')!='DaOS-early-recovery-adb-health-v1' or any(health.get(k) is not True for k in ('ready','supervisor_alive','host_configured','adb_authenticated')):
        raise Refused('Authenticated early USB is not ready')
    fdt=validate_fdt(runner.run(['/sbin/sysctl','-b','hw.fdt.dtb'],limit=131072)[1])
    names=text(['/sbin/sysctl','-aN'],limit=MAX_COMMAND).splitlines()
    units=sorted({match.group(1) for name in names if (match:=CONTROLLER.fullmatch(name))})
    if len(units)!=2:raise Refused('Exactly two BCM SDHCI controllers required')
    controllers={};mapping=set()
    for unit in units:
        prefix='dev.sdhci_bcm.'+unit
        pnp=text(['/sbin/sysctl','-n',prefix+'.%pnpinfo'],limit=1024)
        match=re.fullmatch(r'name=(mmcnr@7e300000|mmc@7e340000) compat=(.+)',pnp)
        if not match or match.group(1) in mapping:raise Refused('Unexpected or duplicate physical controller mapping')
        mapping.add(match.group(1))
        quirk=text(['/sbin/sysctl','-n',prefix+'.slot0.quirks'],limit=128)
        if not quirk.isdecimal():raise Refused('Invalid controller quirk value')
        quirk=int(quirk)
        if not quirk&4 or quirk&2:raise Refused('Both DMA paths must be disabled')
        controllers[prefix]={'pnpinfo':pnp,'quirks':quirk,
            'registers':text(['/sbin/sysctl','-n',prefix+'.slot0.debug.dumpregs'],limit=4096),
            'capabilities':text(['/sbin/sysctl','-n',prefix+'.slot0.debug.dumpcaps'],limit=4096)}
    dmesg=text(['/rescue/dmesg','-a'],limit=262144)
    dmesg_proof=dmesg_evidence(dmesg)
    if dmesg.count('DaOS diagnostic: platform DMA and generic SDMA disabled')!=2:
        raise Refused('Both 005 PIO diagnostic messages required')
    after=isolation(mounts(text(['/rescue/mount','-p'],limit=32768)))
    if after!=before:raise Refused('Mount topology changed during collection')
    if text(['/sbin/sysctl','-n','vm.swap_total'],limit=128)!='0':raise Refused('Swap changed during collection')
    code,_=runner.run(['/sbin/kldstat','-q','-n','if_brcmfmac.ko'],limit=1024,accepted=(0,1))
    if code!=1 or text(['/sbin/sysctl','-n','net.wlan.devices'],limit=1024):raise Refused('Radio changed during collection')
    final_health=json.loads(text(['/usr/local/bin/python3.12','-I','-B',SUPERVISOR,'health'],limit=16384))
    if final_health.get('format')!='DaOS-early-recovery-adb-health-v1' or any(final_health.get(k) is not True for k in ('ready','supervisor_alive','host_configured','adb_authenticated')):
        raise Refused('Authenticated USB changed during collection')
    report={'format':FORMAT,'passed':True,'trial_nonce':nonce,'radio_loaded':False,
        'physical_filesystems_mounted':False,'card_provider_reads':False,'writes_performed':False,
        'watchdog_armed_by_collector':False,'bootfile':bootfile,'boot_settings_verified':values,
        'artifact_sha256_from_owned_tmpfs':hashes,'resident_kernel_sha_measured':False,
        'provenance_requirement':'Owner must bind preboot file readback and selector to this trial nonce',
        'ram_root':desc[:5],'mounts':before,'identical_devfs_mount_entries':sum(row[1]=='/dev' for row in before),
        'fdt':fdt,'controllers':controllers,
        'resident_dependencies':dependencies,
        'usb':{k:final_health.get(k) for k in ('format','phase','ready','supervisor_alive','host_configured','adb_authenticated')},
        'dmesg_sha256':hashlib.sha256(dmesg.encode()).hexdigest(),'dmesg_tail':dmesg[-4096:],
        'dmesg_error_check':dmesg_proof}
    encode_report(report)
    return report


def encode_report(report):
    data=json.dumps(report,sort_keys=True,separators=(',',':')).encode()
    if len(data)>MAX_REPORT:raise Refused('Final evidence report exceeds its bound')
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--trial-nonce',required=True)
    options=parser.parse_args()
    try:report=collect(options.artifacts,options.trial_nonce)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'format':FORMAT,'passed':False,'status':'refused','error':str(error)[:600],
                          'radio_actions':False,'card_writes':False},sort_keys=True))
        return 1
    sys.stdout.buffer.write(encode_report(report)+b'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())

"""Build a separate, pinned Pi4 U-Boot which enters the rescue selector first.

Linux, ordinary user, private build directories only. No SD, EEPROM or OTP
access. Original firmware files are never opened for writing. Integration is a
separate backed-up config.txt transaction after the generated image is tested.
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

VERSION='2026.07'
PORTS_COMMIT='f52037ffbc23c908369ca8545c29dd5a4509f8d3'
ARCHIVE_SHA256='78e8bfc382fe388f9b55aa1daf8c563522a037779b5d4c349d1415e381f1243e'
ARCHIVE_BYTES=35029463
# Buildroot's public source mirror serves the identical release bytes; the
# FreeBSD ports distinfo SHA256 below remains the mandatory authority.
ARCHIVE_URL='https://sources.buildroot.net/uboot/u-boot-'+VERSION+'.tar.bz2'
FILENAME='u-boot-daos-rescue.bin'
BOOTCOMMAND='if load mmc 0:1 ${scriptaddr} /boot.scr; then source ${scriptaddr}; fi; echo DAOS_BOOT_SELECTOR_FAILED; while true; do sleep 60; done'
REQUIRED_CONFIG=('CONFIG_HUSH_PARSER','CONFIG_CMD_SOURCE','CONFIG_CMD_FAT',
 'CONFIG_FAT_WRITE','CONFIG_CMD_MEMORY','CONFIG_CMD_ITEST','CONFIG_CMD_SLEEP',
 'CONFIG_CMD_BOOTEFI','CONFIG_CMD_BOOTEFI_BINARY','CONFIG_OF_BOARD','CONFIG_LEGACY_IMAGE_FORMAT')
PORT_FILES={
 'u-boot-master/files/FreeBSD_Fragment':'c6e8ecdf78d1cd38a64ab299268a74c7772470a5bfa0dafb04ac10693a38e4f2',
 'u-boot-master/files/patch-tools_rkcommon.c':'b71e9000b25961dc6237841ae6ec7960d719b01576095cdc0481594d9d5f77af',
 'u-boot-rpi-arm64/files/rpi_arm64_fragment':'456b216bf7dc419dd5fa1b86d2629f53bcdd7cd945c781e142b3bcb86bad72d0',
 'u-boot-rpi-arm64/files/patch-lib_efi__loader_efi__console.c':'d8c3c03d7c596ad84c8e2f425f51553412127c1cb304270c0f927df44ef48939'}


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def fetch(url,path,expected,maximum=1024**2):
    with urllib.request.urlopen(url,timeout=60) as response,path.open('xb') as stream:
        count=0
        while block:=response.read(1024**2):
            count+=len(block)
            if count>maximum:raise ValueError('Download exceeds bound')
            stream.write(block)
    if sha(path)!=expected:raise ValueError('Pinned source hash mismatch: '+url)


def configuration(data):
    return dict(line.split('=',1) for line in data.splitlines() if line.startswith('CONFIG_') and '=' in line)


def build(output,jobs,cross):
    if sys.platform!='linux' or os.geteuid()==0:raise ValueError('Ordinary Linux build user required')
    if not 1<=jobs<=16 or not re.fullmatch(r'[A-Za-z0-9_./-]+-',cross):raise ValueError('Bounded compiler/jobs required')
    if output.exists() or output.is_symlink():raise ValueError('Fresh private build directory required')
    if output!=output.absolute() or any(p.is_symlink() for p in output.parents):raise ValueError('Canonical absolute output directory required')
    for tool in (cross+'gcc','make','bison','flex','dtc','patch'):
        if not shutil.which(tool):raise ValueError('Existing build tool missing: '+tool)
    output.mkdir(parents=True);inputs=output/'inputs';inputs.mkdir()
    archive=inputs/('u-boot-'+VERSION+'.tar.bz2')
    fetch(ARCHIVE_URL,archive,ARCHIVE_SHA256,ARCHIVE_BYTES)
    if archive.stat().st_size!=ARCHIVE_BYTES:raise ValueError('Pinned archive size mismatch')
    for name,digest in PORT_FILES.items():
        fetch('https://raw.githubusercontent.com/freebsd/freebsd-ports/'+PORTS_COMMIT+'/sysutils/'+name,inputs/name.replace('/','_'),digest)
    with tarfile.open(archive) as tar:tar.extractall(output,filter='data')
    source=output/('u-boot-'+VERSION);obj=output/'obj';obj.mkdir()
    commands=[]
    def run(args,timeout=600):
        commands.append(args)
        result=subprocess.run(args,cwd=source,capture_output=True,text=True,timeout=timeout)
        with (output/'build.log').open('a') as stream:stream.write('$ '+repr(args)+'\n'+result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Build command failed; see '+str(output/'build.log'))
        return result.stdout
    for name in PORT_FILES:
        if '/patch-' in name:run(['patch','-p0','--batch','--fuzz=0','-i',str(inputs/name.replace('/','_'))])
    make=['make','O='+str(obj),'ARCH=arm','CROSS_COMPILE='+cross]
    run(make+['rpi_arm64_defconfig'])
    fragments=[str(inputs/name.replace('/','_')) for name in PORT_FILES if not '/patch-' in name]
    run(['/bin/sh',str(source/'scripts/kconfig/merge_config.sh'),'-m','-O',str(obj),str(obj/'.config'),*fragments])
    run(make+['olddefconfig'])
    baseline=(obj/'.config').read_text();(output/'baseline.config').write_text(baseline)
    old='CONFIG_BOOTCOMMAND="bootflow scan"'
    if baseline.count(old)!=1 or 'CONFIG_USE_BOOTCOMMAND=y' not in baseline:raise ValueError('Unexpected pinned baseline boot command')
    (obj/'.config').write_text(baseline.replace(old,'CONFIG_BOOTCOMMAND='+json.dumps(BOOTCOMMAND),1))
    run(make+['olddefconfig'])
    changed=(obj/'.config').read_text();left=configuration(baseline);right=configuration(changed)
    delta={key:{'before':left.get(key),'after':right.get(key)} for key in left.keys()|right.keys() if left.get(key)!=right.get(key)}
    if set(delta)!={'CONFIG_BOOTCOMMAND'}:raise ValueError('Unexpected configuration change')
    required={key:right.get(key)=='y' for key in REQUIRED_CONFIG}
    if not all(required.values()):raise ValueError('Required selector feature disabled: '+repr(required))
    (output/'selector.config').write_text(changed)
    (output/'config.diff').write_text(''.join(difflib.unified_diff(baseline.splitlines(True),changed.splitlines(True),fromfile='FreeBSD-rpi-arm64-baseline.config',tofile='DaOS-selector.config')))
    # This option is referenced only by tools/Makefile, not target C sources.
    # Omit the unused host capsule generator instead of installing host packages;
    # the recorded target .config still differs only in CONFIG_BOOTCOMMAND.
    run(make+['CONFIG_TOOLS_MKEFICAPSULE=n','-j'+str(jobs),'u-boot.bin'],timeout=1200)
    binary=output/FILENAME;shutil.copyfile(obj/'u-boot.bin',binary)
    embedded=('bootcmd='+BOOTCOMMAND).encode()+b'\0'
    if binary.read_bytes().count(embedded)!=1:raise ValueError('Compiled selector boot command missing')
    # In this release test/true are selected by HUSH, not CONFIG_CMD_TEST.
    test_with_hush='obj-$(CONFIG_HUSH_PARSER) += test.o' in (source/'cmd/Makefile').read_text() and (obj/'cmd/test.o').is_file()
    if not test_with_hush:raise ValueError('Compiled test/true commands missing')
    report={'format':'DaOS-pinned-uboot-selector-v1','status':'BUILT_NOT_INSTALLED','source_version':VERSION,
        'source_archive_sha256':ARCHIVE_SHA256,'source_download_url':ARCHIVE_URL,'ports_commit':PORTS_COMMIT,'port_inputs_sha256':PORT_FILES,
        'filename':FILENAME,'sha256':sha(binary),'bytes':binary.stat().st_size,'bootcommand':BOOTCOMMAND,
        'configuration_delta':delta,'baseline_config_sha256':sha(output/'baseline.config'),'selector_config_sha256':sha(output/'selector.config'),
        'required_selector_config':required,'test_and_true_compiled_with_hush':test_with_hush,
        'compiler':run([cross+'gcc','--version']).splitlines()[0],'commands':commands,
        'host_only_build_override':{'CONFIG_TOOLS_MKEFICAPSULE':'n'},
        'physical_boot_verified':False,'qemu_boot_verified':False,'original_u_boot_modified':False,'otp_modified':False,'eeprom_modified':False}
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n');return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=4);p.add_argument('--cross-compile',default='aarch64-linux-gnu-')
    a=p.parse_args();print(json.dumps(build(a.output,a.jobs,a.cross_compile),indent=2))


if __name__=='__main__':main()

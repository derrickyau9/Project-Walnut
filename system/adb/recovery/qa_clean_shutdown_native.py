"""Native FreeBSD clean-hook QA on a private COPY of an explicit FAT file.

The GEOM/root inspection table is a fixture because this VM is not the bound SD.
The hook's mount, dd, sync, umount and marker readback use actual native msdosfs.
No real card or running root is modified; no reboot or watchdog is invoked.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys


def command(args,check=True):
    return subprocess.run(args,text=True,capture_output=True,check=check,timeout=30)


def sha(path):
    return hashlib.file_digest(path.open('rb'),'sha256').hexdigest()


def run(image,output,source):
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:
        raise ValueError('Native FreeBSD root required')
    if image.is_symlink() or not stat.S_ISREG(image.stat().st_mode):
        raise ValueError('QA input must be an ordinary FAT image FILE')
    if output.exists() or output.is_symlink():raise ValueError('Fresh private output directory required')
    if image.stat().st_size!=128*1024*1024:raise ValueError('Expected the bounded 128MiB EFI copy')
    before=sha(image);output.mkdir(parents=True)
    copied=output/'private-fat.img';shutil.copyfile(image,copied)
    mountpoint=output/'mnt';mountpoint.mkdir()
    spec=importlib.util.spec_from_file_location('qa_boot',source)
    boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
    labels='gpt/DaOS-efi N/A mmcsd0p1\ngptid/efi-uuid N/A mmcsd0p1\ngpt/DaOS-root N/A mmcsd0p2\n'
    mounts='/dev/gpt/DaOS-root / ufs ro,noatime 1 1\ndevfs /dev devfs rw 0 0\n'
    (output/'labels').write_text(labels);(output/'mounts').write_text(mounts)
    md=command(['/sbin/mdconfig','-a','-t','vnode','-f',str(copied)]).stdout.strip()
    if not md.startswith('md') or not md[2:].isdigit():raise ValueError('Unexpected allocated md unit')
    marker=mountpoint/'DaOS-recovery/normal.running'
    results={}
    try:
        def mount(mode):command(['/sbin/mount','-t','msdosfs','-o',mode,'/dev/'+md,str(mountpoint)])
        def umount():command(['/sbin/umount',str(mountpoint)])
        def reset():
            mount('rw')
            try:
                marker.parent.mkdir(exist_ok=True);marker.write_bytes(b'\1');command(['/bin/sync'])
            finally:umount()
        def read():
            mount('ro')
            try:return marker.read_bytes()
            finally:umount()
        for name,mode,recovery,table,expected_status,expected_marker in (
                ('clean_native_fat','ok','NO',mounts,0,b'\0'),
                ('sync_failure_rearms','sync-failed','NO',mounts,1,b'\1'),
                ('alias_duplicate_rejected','ok','NO',mounts+'/dev/gptid/efi-uuid /other msdosfs rw 0 0\n',1,b'\1'),
                ('recovery_skips_clear','ok','YES',mounts,0,b'\1')):
            reset();(output/'mounts').write_text(table)
            log=output/(name+'.log')
            prefix=f'''qa_dir={shlex.quote(str(output))}
qa_log={shlex.quote(str(log))}
qa_mode={shlex.quote(mode)}
qa_count=0
qa_kenv() {{ printf '%s\\n' {shlex.quote(recovery)}; }}
qa_geom() {{ /bin/cat "$qa_dir/labels"; }}
qa_mount() {{
    if [ "$1" = -p ]; then /bin/cat "$qa_dir/mounts"; return; fi
    printf 'MOUNT:%s\\n' "$*" >> "$qa_log"
    /sbin/mount "$@"
}}
qa_sync() {{
    qa_count=$((qa_count+1))
    printf 'SYNC\\n' >> "$qa_log"
    if [ "$qa_mode" = sync-failed ] && [ "$qa_count" = 1 ]; then return 1; fi
    /bin/sync
}}
'''
            function=boot.CLEAN_SHUTDOWN.split('DaOS_rescue_clear() {',1)[1].split('\nload_rc_config',1)[0]
            script='DaOS_rescue_clear() {'+function+'\nDaOS_rescue_clear\n'
            for old,new in (('/bin/kenv','qa_kenv'),('/sbin/mount','qa_mount'),('/sbin/geom','qa_geom'),('/bin/sync','qa_sync')):
                script=script.replace(old,new)
            script=script.replace('/DaOS-recovery-efi',str(mountpoint))
            script=script.replace(' -o rw /dev/gpt/DaOS-efi "$mountpoint"',' -o rw /dev/'+md+' "$mountpoint"')
            file=output/(name+'.sh');file.write_text(prefix+script)
            result=command(['/bin/sh',str(file)],check=False)
            (output/(name+'.stdout')).write_text(result.stdout)
            (output/(name+'.stderr')).write_text(result.stderr)
            marker_value=read();calls=log.read_text() if log.exists() else ''
            passed=result.returncode==expected_status and marker_value==expected_marker
            if name in ('alias_duplicate_rejected','recovery_skips_clear'):passed=passed and not calls
            else:passed=passed and calls.count('MOUNT:')==1 and 'SYNC' in calls
            results[name]={'passed':passed,'status':result.returncode,'marker_hex':marker_value.hex(),'operations':calls.splitlines()}
    finally:
        mounted=any(len(fields:=line.split())==6 and fields[1]==str(mountpoint) for line in command(['/sbin/mount','-p']).stdout.splitlines())
        if mounted:command(['/sbin/umount',str(mountpoint)])
        command(['/sbin/mdconfig','-d','-u',md[2:]])
    report={'passed':all(item['passed'] for item in results.values()),'cases':results,
        'input_sha256_before':before,'input_sha256_after':sha(image),'input_unchanged':before==sha(image),
        'boot_config_sha256':sha(source),'clean_hook_sha256':hashlib.sha256(boot.CLEAN_SHUTDOWN.encode()).hexdigest(),
        'native_msdosfs_mount_write_sync_unmount':True,'geom_and_root_identity_fixture':True,
        'only_provider_and_mountpoint_rebound_to_private_md':True,'owned_md_detached':True,'physical_sd_writes':False}
    report['passed']=report['passed'] and report['input_unchanged']
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--image',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--boot-config',type=Path,required=True)
    a=p.parse_args();report=run(a.image,a.output,a.boot_config);print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())

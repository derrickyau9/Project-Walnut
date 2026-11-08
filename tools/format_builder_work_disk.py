"""Initialize only the newly attached, serial-identified DaOS004 scratch disk."""
import json
import os
from pathlib import Path
import subprocess


def run(args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60).stdout


def main():
    if os.uname().sysname!='FreeBSD' or os.geteuid()!=0:raise ValueError('private FreeBSD builder root required')
    matches=[]
    names=run(['/sbin/sysctl','-n','kern.disks']).split()
    for name in names:
        if not name.startswith('vtbd'):continue
        text=run(['/usr/sbin/diskinfo','-v','/dev/'+name])
        if 'DAOS004-WORK' in text:matches.append((name,text))
    if len(matches)!=1:raise ValueError('exactly one new DaOS004 disk required')
    name,text=matches[0]
    if name=='vtbd0' or '137438953472' not in text:raise ValueError('scratch disk size/identity mismatch')
    existing=subprocess.run(['/sbin/gpart','show',name],capture_output=True,text=True,timeout=10)
    if existing.returncode==0:raise ValueError('scratch disk is already partitioned; no overwrite')
    device='/dev/'+name
    run(['/sbin/gpart','create','-s','gpt',device])
    run(['/sbin/gpart','add','-t','freebsd-ufs','-l','DaOS004Work','-a','1M',device])
    run(['/sbin/newfs','-U','-L','DaOS004Work','/dev/gpt/DaOS004Work'])
    target=Path('/var/DaOS004-work');target.mkdir(mode=0o755)
    run(['/sbin/mount','-t','ufs','/dev/gpt/DaOS004Work',str(target)])
    record={'format':'DaOS004-native-workspace-v1','device':device,'serial':'DAOS004-WORK','mount':str(target)}
    (target/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))


if __name__=='__main__':main()

"""Transfer only reviewed public source to the owned isolated builder."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path('/mnt/f/Project HasselFree');sys.path.insert(0,str(ROOT))
from tools import freebsd_vm as vm
WORK=Path('/home/dy09/hf-task-freebsd-r10/vm-001')
OUT=ROOT/'build/DaOS_OpenBSD_001/usb-postmortem-review'
files=[(OUT/'map_ufs.c','/var/tmp/daos-postmortem-map-ufs.c'),
       (OUT/'native_ufs_fixture.py','/var/tmp/daos-postmortem-ufs-fixture.py'),
       (ROOT/'system/adb/recovery/flash.py','/var/tmp/daos-postmortem-flash.py')]
for source,dest in files:
    result=vm.copy_to(WORK,str(source),dest,timeout=45)
    assert result['format']=='hf-freebsd-vm-copy-v1'
command='cc -std=c11 -Wall -Wextra -Werror /var/tmp/daos-postmortem-map-ufs.c -o /var/tmp/daos-postmortem-map-ufs && /usr/local/bin/python3.12 /var/tmp/daos-postmortem-ufs-fixture.py'
result=vm.ssh_command(WORK,command,timeout=180)
(OUT/'native-ufs.stdout').write_text(result['stdout']);(OUT/'native-ufs.stderr').write_text(result['stderr'])
if result['returncode']:
    print(result['stderr']);print(result['stdout'][-3000:])
copied=vm.copy_from(WORK,'/var/tmp/daos-postmortem-ufs-proof.json',str(OUT/'native-ufs-proof.json'),timeout=30)
assert copied['format']=='hf-freebsd-vm-copy-v1'
receipt={'returncode':result['returncode'],'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p,_ in files},
         'receipt_sha256':hashlib.sha256((OUT/'native-ufs-proof.json').read_bytes()).hexdigest()}
(OUT/'native-ufs-source-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2));assert result['returncode']==0

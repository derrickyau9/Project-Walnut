"""Prepare a dedicated scratch disk; restart the builder with --work-disk to attach.

The private q35 machine has no hot-pluggable root slot. This helper creates only
one fresh F-backed WSL image and never changes a running VM or its root disk.
"""
import json,os
from pathlib import Path
import subprocess

def main():
 directory=Path('/home/dy09/hf-task-freebsd-r10')
 image=directory/'daos004-work.qcow2'
 if os.geteuid()==0 or directory.resolve(strict=True)!=directory or directory.stat().st_uid!=os.geteuid() or image.exists() or image.is_symlink():
  raise ValueError('A fresh private builder work disk is required')
 subprocess.run(['qemu-img','create','-f','qcow2',str(image),'128G'],check=True,timeout=20)
 image.chmod(0o600)
 record={'format':'DaOS004-owned-builder-work-disk-v1','image':str(image),'virtual_bytes':128*1024**3,
         'guest_serial':'DAOS004-WORK','attached':False,'next_step':'Restart owned builder with --work-disk, verify serial, then explicitly initialize only that new disk'}
 (directory/'daos004-work-disk.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps(record))
if __name__=='__main__':main()

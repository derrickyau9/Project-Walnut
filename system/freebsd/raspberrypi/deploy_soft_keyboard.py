#!/usr/local/bin/python3.12
"""Apply a create-only keyboard update over the already authorized USB shell."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

APP=Path('/opt/hasslefree/ui/on-device')
WORK=Path('/var/DaOS/updates/soft-keyboard-001')
FILES=('main.py','Main.qml','Console.qml','SoftKeyboard.qml','soft_keyboard.py')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    if not sys.platform.startswith('freebsd') or os.geteuid()!=0:raise RuntimeError('Native FreeBSD root shell required')
    WORK.parent.mkdir(mode=0o700,exist_ok=True)
    WORK.mkdir(mode=0o700)
    archive=Path(sys.argv[1])
    record={'format':'DaOS-soft-keyboard-deploy-v1','status':'preparing','archive_sha256':sha(archive),
            'backup':str(WORK/'backup'),'files':{},'photos_or_settings_changed':False}
    if record['archive_sha256']!=sys.argv[2]:raise RuntimeError('Update archive changed')
    incoming=WORK/'incoming';incoming.mkdir()
    with tarfile.open(archive) as t:
        members=t.getmembers()
        if {m.name for m in members}!=set(FILES) or any(not m.isfile() or m.size>256*1024 for m in members):
            raise RuntimeError('Unexpected update members')
        t.extractall(incoming,filter='data')
    backup=WORK/'backup';backup.mkdir()
    for name in FILES:
        target=APP/name
        if target.is_symlink():raise RuntimeError('Redirected app path')
        old=sha(target) if target.exists() else None
        if old:shutil.copy2(target,backup/name)
        record['files'][name]={'before':old,'after':sha(incoming/name)}
    (WORK/'deployment.json').write_text(json.dumps(record,indent=2)+'\n')
    with tarfile.open(WORK/'backup.tar.gz','x:gz') as t:
        for f in backup.iterdir():t.add(f,arcname=f.name)
    stopped=False;written=[]
    try:
        subprocess.run(['/usr/sbin/service','DaOS-camera','stop'],check=True,timeout=45,capture_output=True)
        stopped=True
        subprocess.run(['/sbin/mount','-u','-w','/'],check=True,capture_output=True)
        for name in FILES:
            temp=APP/(name+'.soft-keyboard-001.tmp')
            with temp.open('xb') as stream:stream.write((incoming/name).read_bytes());stream.flush();os.fsync(stream.fileno())
            os.chmod(temp,0o644);os.chown(temp,0,0)
            os.replace(temp,APP/name);written.append(name)
        subprocess.run(['/sbin/mount','-u','-r','/'],check=True,capture_output=True)
        subprocess.run(['/usr/sbin/service','DaOS-camera','start'],check=True,timeout=20,capture_output=True)
        record['status']='installed-service-started'
    except BaseException as error:
        record.update(status='failed',error=str(error))
        if written:
            subprocess.run(['/sbin/mount','-u','-w','/'],capture_output=True)
            for name in written:
                if (backup/name).exists():shutil.copy2(backup/name,APP/name)
                else:(APP/name).unlink()
            subprocess.run(['/sbin/mount','-u','-r','/'],capture_output=True)
            record['rollback']='original files restored'
        if stopped:subprocess.run(['/usr/sbin/service','DaOS-camera','start'],capture_output=True,timeout=20)
        raise
    finally:
        (WORK/'deployment.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':record['status'],'receipt':str(WORK/'deployment.json')},separators=(',',':')))

if __name__=='__main__':main()

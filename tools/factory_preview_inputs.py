#!/usr/bin/env python3
"""Pack only generic early USB inputs; no card data or authentication keys."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
INIT='7a10779068a259de11ea0c0e97c2bdb87cfedfc7729ecd146fe3be9739605427'
HELPER='d6ca440605b3c1718f1144dd8eda6ae2e34f8fd53893c712a7c2dfcdf33fb2aa'
OLD_INIT='70621ffd991e433ca1cbc177c4d7355f5ab16253e2f28a9da1307f22b8d5feae'

def digest(data):return hashlib.sha256(data).hexdigest()

def build(output,init,helper):
    for path,expected in ((init,INIT),(helper,HELPER)):
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes())!=expected:raise ValueError('Pinned ARM64 build input required')
    sources=[('sbin/init',init,0o755),
        ('usr/local/libexec/DaOS-recovery/early_adb.py',ROOT/'system/adb/recovery/early_adb.py',0o755),
        ('usr/local/libexec/DaOS-recovery/early_daemon.py',ROOT/'system/adb/recovery/early_daemon.py',0o755),
        ('etc/daos/recovery-adb.json',ROOT/'system/adb/recovery/early-adb.json',0o644),
        ('usr/local/etc/rc.d/DaOS-usb-adb',ROOT/'system/adb/integration/files/usr/local/etc/rc.d/DaOS-usb-adb',0o755),
        ('usr/local/libexec/DaOS-usb-adb-session.py',ROOT/'system/adb/integration/files/usr/local/libexec/DaOS-usb-adb-session.py',0o755),
        ('usr/local/libexec/DaOS-recovery/early-init.sh',ROOT/'system/adb/recovery/factory_early_init.sh',0o755),
        ('usr/local/libexec/DaOS-recovery/early-init-base.sh',ROOT/'system/adb/recovery/early-init.sh',0o755)]
    files={};entries=[]
    for name,source,mode in sources:
        if source.is_symlink():raise ValueError('Input source must not be redirected')
        data=source.read_bytes();files['bundle/payload/'+name]=data
        entries.append({'source':'payload/'+name,'path':name,'bytes':len(data),'sha256':digest(data),'mode':mode})
    manifest={'format':'DaOS-usb-recovery-bundle-v1','version':'usb-recovery-001','base_init_sha256':OLD_INIT,'files':entries}
    files['bundle/manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    files['install.py']=(ROOT/'system/adb/recovery/install.py').read_bytes()
    files['helper']=helper.read_bytes()
    bindings={'format':'DaOS-factory-generic-core-inputs-v1','files':{name:{'bytes':len(data),'sha256':digest(data)} for name,data in files.items()}}
    files['inputs.json']=(json.dumps(bindings,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w') as archive:
        for name,data in sorted(files.items()):
            entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o600
            archive.addfile(entry,io.BytesIO(data))
    receipt={'format':'DaOS-factory-generic-core-archive-v1','bytes':output.stat().st_size,'sha256':digest(output.read_bytes()),
        'manifest_sha256':digest(files['bundle/manifest.json']),'files':bindings['files'],'contains_private_keys':False,
        'contains_card_identity_or_watchdog_policy':False}
    with Path(str(output)+'.json').open('x',encoding='utf-8') as dest:json.dump(receipt,dest,indent=2);dest.write('\n')
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--init',type=Path,required=True)
    parser.add_argument('--helper',type=Path,required=True)
    args=parser.parse_args();result=build(args.output,args.init,args.helper)
    print(json.dumps({key:result[key] for key in ('sha256','bytes','manifest_sha256')}))

if __name__=='__main__':main()

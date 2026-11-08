"""Bounded private cross build from exported native config and exact flags.

All C cc1 options are replayed, not translated through Linux driver defaults.
Assembly uses the native expanded NORMAL_S command with the OpenBSD target.
The native build and original source tree are never written.
"""
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

OUT=Path(__file__).resolve().parent
REF=OUT.parent/'watchdog-kernel-002-arm64'
WORK=Path('/home/dy09/daos-openbsd-cross-001')
SYS=Path('/home/dy09/daos-openbsd-watchdog-001/sys')
SNAP=Path(os.environ.get('DAOS_CROSS_REFERENCE',str(WORK/'native-apm-reference')))
OBJ=WORK/'DAOS_WDOG_APM_RAMDISK_CROSS22/obj'
TOOL=WORK/'toolroot'
ENV=dict(os.environ,LD_LIBRARY_PATH=str(TOOL/'usr/lib/x86_64-linux-gnu'))
CLANG=str(TOOL/'usr/lib/llvm-22/bin/clang')
LD=str(TOOL/'usr/bin/ld.lld-22')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args,log):
    t=time.monotonic()
    result=subprocess.run(args,cwd=OBJ,env=ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (OBJ/(log+'.log')).write_text(result.stdout)
    return {'returncode':result.returncode,'seconds':time.monotonic()-t,'output':result.stdout[-2000:]}


def cc1(source,name):
    base=json.loads((OUT/'bcm2835_dog.argv.json').read_text())
    args=base[:]
    replace={'-main-file-name':Path(source).name,'-dependency-file':name+'.d',
             '-MT':name+'.o','-o':name+'.o'}
    for i in range(1,len(args)):
        if args[i-1] in replace:args[i]=replace[args[i-1]]
        if args[i-1]=='-I' and args[i]==str(WORK/'native-reference'):args[i]=str(OBJ)
    assert args[-2:]==['c',str(SYS/'dev/fdt/bcm2835_dog.c')]
    args[-1]=str(source)
    return args


def main():
    start=time.monotonic()
    OBJ.mkdir(parents=True,exist_ok=True)
    for path in SNAP.iterdir():
        if path.is_symlink():
            dest=OBJ/path.name
            if not dest.exists():dest.symlink_to(path.resolve())
        elif path.suffix in ('.h','.c') or path.name=='Makefile' or path.name.startswith('DAOS_WDOG_'):
            shutil.copyfile(path,OBJ/path.name)
    text=(SNAP/'Makefile').read_text()
    flat=text.replace('\\\n',' ')
    names=re.search(r'^OBJS=\s*(.*)$',flat,re.M).group(1).split()
    mapping={name:SYS/relative for name,relative in re.findall(r'^([\w.-]+\.o): \$S/([^\s]+)',text,re.M)}
    assert len(set(names))==len(names)
    assert set(names)<=set(mapping)
    assert all(mapping[name].is_file() for name in names)
    plan=gzip.decompress((REF/'native-apm-make-plan.txt.gz').read_bytes()).decode()
    assembly_template=next(shlex.split(line) for line in plan.splitlines()
                           if line.startswith('cc ') and '-x assembler-with-cpp' in line)
    commands={}
    for name in names+['locore0.o','param.o','ioconf.o','swapbsd.o']:
        source=mapping.get(name)
        if name=='locore0.o':source=SYS/'arch/arm64/arm64/locore0.S'
        elif name=='param.o':source=SYS/'conf/param.c'
        elif name in ('ioconf.o','swapbsd.o'):source=OBJ/(Path(name).stem+'.c')
        assert source is not None
        if source.suffix=='.c':
            args=cc1(source,Path(name).stem)
        else:
            assert source.suffix=='.S'
            args=[CLANG,'--target=aarch64-unknown-openbsd8.0']+assembly_template[1:]
            for i,token in enumerate(args):
                if token.startswith('-I/usr/src/sys'):
                    args[i]='-I'+str(SYS)+token[len('-I/usr/src/sys'):]
                elif token in ('-I/root/daos-qa/obj','-I/root/daos-qa/obj-apm'):args[i]='-I'+str(OBJ)
                elif token.startswith('/usr/src/sys'):
                    args[i]=str(SYS)+token[len('/usr/src/sys'):]
                elif token=='/root/daos-qa/obj':args[i]=str(OBJ)
            args[-1]=str(source)
            args+=['-o',name]
        commands[name]=args
    (OUT/'full-commands.json.gz').write_bytes(gzip.compress(json.dumps(commands,indent=2).encode(),mtime=0))
    results={}
    def compile_one(item):
        name,args=item
        return name,command(args,name)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for name,result in pool.map(compile_one,commands.items()):
            results[name]=result
    report={'status':'COMPILE_ONLY_BEFORE_VERSION_GAP_AND_LINK','passed':all(v['returncode']==0 for v in results.values()),
            'objects':len(results),'failures':{n:r for n,r in results.items() if r['returncode']},
            'seconds':time.monotonic()-start,'compiler_version_delta':'Debian22.1.8 versus OpenBSD22.1.6',
            'native_config_snapshot_sha256':sha(REF/'native-apm-cross-reference.tgz'),
            'native_make_plan_sha256':sha(REF/'native-apm-make-plan.txt.gz'),
            'all_native_C_cc1_flags_preserved':True,'assembly_native_NORMAL_S_flags_preserved':True,
            'source_or_native_object_tree_modified':False,'physical_actions':False}
    (OUT/'full-compile-receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    (OUT/'full-compile-timings.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()

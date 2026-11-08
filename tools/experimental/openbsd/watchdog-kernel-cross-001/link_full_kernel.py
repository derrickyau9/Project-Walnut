"""Run unmodified OpenBSD version/gap recipes with narrow host-query shims."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import subprocess
import sys

import build_full_kernel as build

OUT=build.OUT
OBJ=build.OBJ
SYS=build.SYS
TOOL=build.TOOL


def call(args,log,env):
    result=subprocess.run(args,cwd=OBJ,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (OUT/log).write_text(result.stdout)
    if result.returncode:raise RuntimeError(f'{log} exit{result.returncode}: '+result.stdout[-2500:])


def main():
    compile_report=json.loads((OUT/'full-compile-receipt.json').read_text())
    assert compile_report['passed']
    shims=build.WORK/'host-query-shims'
    shims.mkdir(exist_ok=True)
    # OpenBSD's scripts call these host-query commands. The implementations are
    # exact/narrow, live in this private directory and never alter host tools.
    scripts={
        'sysctl':"#!/usr/bin/python3\nimport sys\nassert sys.argv[1:]==['-n','hw.pagesize']\nprint(4096)\n",
        'jot':"#!/usr/bin/python3\nimport sys,secrets\nassert sys.argv[1:4]==['-r','1','0'] and len(sys.argv)==5\nprint(secrets.randbelow(int(sys.argv[4])+1))\n",
        'logname':"#!/bin/sh\nprintf '%s\\n' daos-cross-qa\n",
        'hostname':"#!/bin/sh\nprintf '%s\\n' isolated-cross-host\n",
    }
    for name,text in scripts.items():
        path=shims/name;path.write_text(text);path.chmod(0o755)
    env=dict(build.ENV,PATH=str(shims)+':'+os.environ['PATH'],LD=build.LD)
    call(['bash',str(SYS/'conf/newvers.sh')],'newvers.log',env)
    call(build.cc1(OBJ/'vers.c','vers'),'vers.compile.log',env)
    (OBJ/'gapdummy.c').write_text('__asm(".section .rodata,\\\"a\\\"");\n')
    call(build.cc1(OBJ/'gapdummy.c','gapdummy'),'gapdummy.compile.log',env)
    call(['bash',str(SYS/'conf/makegap.sh'),'0xd4d4d4d4','gapdummy.o'],'makegap.log',env)
    script=(SYS/'arch/arm64/conf/kern.ldscript').read_text()
    (OBJ/'ld.script').write_text(script.replace('@KERNEL_BASE_PHYS@','0x00200000')
                               .replace('@KERNEL_BASE_VIRT@','0xffffff8000200000'))
    text=(build.SNAP/'Makefile').read_text().replace('\\\n',' ')
    names=re.search(r'^OBJS=\s*(.*)$',text,re.M).group(1).split()
    # The normal non-SMALL build shuffles these objects with sort-R. Use a
    # cryptographic shuffle here; record the actual order for repeat review.
    order=names+['param.o','ioconf.o','vers.o']
    secrets.SystemRandom().shuffle(order)
    (OBJ/'lorder').write_text('\n'.join(order)+'\n')
    # Unmodified exported LINKFLAGS, including OpenBSD segment script, -X,
    # --warn-common, -nopie and --no-mmap-output-file. Debug information stays
    # in bsd.gdb until native ctfstrip (not replaced by generic llvm-strip).
    args=[build.LD,'-T','ld.script','-X','--warn-common','-nopie',
          '--no-mmap-output-file','-o','bsd.gdb','locore0.o','gap.o']+order+['swapbsd.o']
    (OUT/'link.argv.json').write_text(json.dumps(args,indent=2)+'\n')
    call(args,'final-link.log',env)
    result=OBJ/'bsd.gdb'
    shutil.copyfile(result,OUT/'bsd.cross.gdb')
    report={'status':'LINKED_DEBUG_ELF_NOT_NATIVE_CTFSTRIPPED_OR_BOOTED',
            'passed':True,'bytes':result.stat().st_size,'sha256':build.sha(result),
            'link_flags_unchanged':True,'OpenBSD_newvers_and_makegap_scripts_unmodified':True,
            'host_query_shims':scripts,'object_order':order,
            'objects_count':len(order)+3,'compiler_version_delta':compile_report['compiler_version_delta'],
            'source_kernel_modified':False,'native_build_modified':False,'physical_actions':False}
    (OUT/'full-link-receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('object_order','host_query_shims')},indent=2))


if __name__=='__main__':main()

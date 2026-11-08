"""Package cross objects for unchanged native OpenBSD link and ctfstrip."""
import hashlib
import json
import re
import tarfile

import build_full_kernel as build

OUT=build.OUT
OBJ=build.OBJ
report=json.loads((OUT/'full-compile-receipt.json').read_text())
assert report['passed'] and report['objects']==768
assert json.loads((OUT/'apm-five-object-code-comparison.json').read_text())['passed']
assert json.loads((OUT/'source-input-verification.json').read_text())['passed']
order=(OBJ/'lorder').read_text().splitlines()
assert len(set(order))==len(order) and all(re.fullmatch(r'[a-zA-Z0-9_.-]+\.o',name) for name in order)
names=['locore0.o','gap.o']+order+['swapbsd.o']
assert len(set(names))==len(names)
inputs=names+['ld.script','lorder','gap.link','vers.c','version','DAOS_WDOG_APM_RAMDISK']
manifest={name:{'sha256':build.sha(OBJ/name),'bytes':(OBJ/name).stat().st_size} for name in inputs}
script='''#!/bin/sh
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
set --
while IFS= read -r entry; do
    case "$entry" in *[!a-zA-Z0-9_.-]*|""|*/*) exit 61;; esac
    test -f "$entry"
    set -- "$@" "$entry"
done < lorder
/usr/bin/ld -T ld.script -X --warn-common -nopie --no-mmap-output-file -o bsd.gdb locore0.o gap.o "$@" swapbsd.o
/usr/bin/ctfstrip -S -o bsd bsd.gdb
sha256 bsd bsd.gdb
file bsd bsd.gdb
nm -n bsd | egrep 'rd_root_(image|size)|bcmdog_wdog|wdog_register|sysctl_wdog'
'''
(OBJ/'native-link.sh').write_text(script)
(OBJ/'cross-input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
archive=OUT/'cross-objects-native-tail.tgz'
with tarfile.open(archive,'w:gz',compresslevel=5) as tar:
    for name in inputs+['native-link.sh','cross-input-manifest.json']:
        tar.add(OBJ/name,arcname=name,recursive=False)
receipt={'status':'READY_FOR_NATIVE_EXACT_LINK_AND_CTFSTRIP_NOT_BOOTED',
         'archive':archive.name,'sha256':build.sha(archive),'bytes':archive.stat().st_size,
         'cross_objects':len(names),'native_link_flags_unchanged':True,
         'native_link_script_sha256':build.sha(OBJ/'native-link.sh'),
         'configuration_delta':['rmoption SMALL_KERNEL','apm0 at mainbus? exact GENERIC line'],
         'compiler_version_delta':'Debian22.1.8 versus native OpenBSD22.1.6',
         'host_makegap_recipe_unmodified':True,'host_newvers_recipe_unmodified':True,
         'private_host_query_shims':['sysctl -n hw.pagesize returns4096','jot secure random ranges','build-only logname/hostname'],
         'no_source_kernel_edits':True,'no_physical_actions':True,
         'artifact_contains_no_private_RAM_payload_or_keys':True}
(OUT/'native-tail-handoff.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))

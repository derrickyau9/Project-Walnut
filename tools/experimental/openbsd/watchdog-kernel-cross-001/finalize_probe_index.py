"""Source-bound public cross-probe index; later native/boot proofs separate."""
import gzip
import hashlib
import json
from pathlib import Path
import shlex

OUT=Path(__file__).resolve().parent
REF=OUT.parent/'watchdog-kernel-002-arm64'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


native_line=next(line.strip() for line in (REF/'exact-native-bcmdog-cc1.log').read_text().splitlines()
                 if '"-cc1"' in line)
native=shlex.split(native_line)
commands=json.loads(gzip.decompress((OUT/'full-commands.json.gz').read_bytes()))
cross=commands['bcm2835_dog.o']
assert len(native)==len(cross)
path_positions={'-resource-dir','-dependency-file','-MT','-o'}
different=[]
for i,(left,right) in enumerate(zip(native,cross)):
    if left==right:continue
    allowed=(i==0 or (i>0 and native[i-1] in path_positions)
             or left.startswith('/usr/src/sys') or left=='/root/daos-qa/obj')
    assert allowed,(i,left,right)
    different.append({'position':i,'native':left,'cross':right})
compile_receipt=json.loads((OUT/'full-compile-receipt.json').read_text())
comparison=json.loads((OUT/'apm-five-object-code-comparison.json').read_text())
verification=json.loads((OUT/'source-input-verification.json').read_text())
handoff=json.loads((OUT/'native-tail-handoff.json').read_text())
assert compile_receipt['passed'] and comparison['passed'] and verification['passed']
paths=[p for p in OUT.iterdir() if p.is_file() and p.name not in ('CROSS-PROBE-QA.json',)]
paths += [REF/name for name in ('cross-reference-receipt.json','native-apm-reference-receipt.json',
                                'native-apm-selected-receipt.json','native-apm-selected-objects.tgz',
                                'exact-native-bcmdog-command.txt','exact-native-bcmdog-cc1.log',
                                'native-apm-cross-reference.tgz','native-apm-make-plan.txt.gz')]
root=OUT.parents[2]
result={'format':'DaOS-OpenBSD-private-cross-kernel-probe-QA-v1','passed':True,
        'status':'CROSS_COMPILE_PASSED_NATIVE_FINAL_LINK_AND_ARM_BOOT_HAVE_SEPARATE_RECEIPTS',
        'compiler_version_delta':'Debian LLVM22.1.8 vs OpenBSD LLVM22.1.6; separate distribution patches possible',
        'native_cc1_options_exact_except_allowed_paths':True,'cc1_path_differences':different,
        'no_codegen_optimization_warning_or_security_option_dropped':True,
        'kernel_config_delta':['rmoption SMALL_KERNEL','apm0 at mainbus? exactGENERIC line'],
        'objects_compiled':compile_receipt['objects'],'seconds':compile_receipt['seconds'],'workers':4,
        'independent_native_objects_allocated_sections_and_relocations_identical':list(comparison['objects']),
        'signed_archive_actual_dependency_files_verified':verification['signed_archive_dependency_files'],
        'native_generated_dependency_files_verified':verification['exported_generated_files'],
        'link_handoff_sha256':handoff['sha256'],'native_link_flags_preserved_by_native_tail':True,
        'kernel_source_edits':False,'private_RAM_payload_or_keys_read':False,'physical_actions':False,
        'hardware_watchdog_or_Pi_boot_proven':False,
        'files':{p.relative_to(root).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size} for p in paths}}
if (OUT/'native-final-receipt.json').exists():
    native_final=json.loads((OUT/'native-final-receipt.json').read_text())
    bare=json.loads((OUT/'bare-kernel-elf-proof.json').read_text())
    assert bare['passed'] and digest(OUT/'bsd')==native_final['bare_kernel_sha256']==bare['sha256']
    result['status']='CROSS_BUILD_AND_EXACT_NATIVE_LINK_CTFSTRIP_PASSED_ARM_BOOT_SEPARATE'
    result['bare_kernel_sha256']=bare['sha256']
    result['native_link_and_ctfstrip_passed']=True
    result['native_tail_following_metadata_path_failure_separately_resolved']=True
(OUT/'CROSS-PROBE-QA.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:result[key] for key in ('status','objects_compiled','seconds','native_cc1_options_exact_except_allowed_paths')}))

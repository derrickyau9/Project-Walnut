"""Independent structural proof of the public, unpopulated ARM64 ELF."""
import hashlib
import json
from pathlib import Path
import struct

OUT=Path(__file__).resolve().parent
path=OUT/'bsd'
data=path.read_bytes()
assert hashlib.sha256(data).hexdigest()=='75f8fadfb342b86fb73e1b542e09f931380fedc4f14bc66afaa5b1f2f05ad9ed'
assert data[:8]==b'\x7fELF\x02\x01\x01\x00'
assert (OUT.parent/'official/bsd.rd').read_bytes()[:8]==data[:8]
kind,machine=struct.unpack_from('<HH',data,16)
entry,phoff,shoff=struct.unpack_from('<QQQ',data,24)
phsize,phnum,shsize,shnum,shstr=struct.unpack_from('<HHHHH',data,54)
assert (kind,machine,phsize,shsize)==(2,183,56,64)
ph=[struct.unpack_from('<IIQQQQQQ',data,phoff+i*phsize) for i in range(phnum)]
assert any(p[0]==1 and p[1]&1 and p[3]<=entry<p[3]+p[6] for p in ph)
assert not any(p[0]==1 and p[1]&3==3 for p in ph)
sh=[struct.unpack_from('<IIQQQQIIQQ',data,shoff+i*shsize) for i in range(shnum)]
symbols={}
for section in sh:
    if section[1]!=2:continue
    strs=sh[section[6]]
    strings=data[strs[4]:strs[4]+strs[5]]
    for offset in range(section[4],section[4]+section[5],24):
        name,info,other,index,value,size=struct.unpack_from('<IBBHQQ',data,offset)
        text=strings[name:strings.index(b'\0',name)].decode()
        if index:symbols[text]={'address':value,'bytes':size,'binding':info>>4,'type':info&15}
required=['rd_root_image','rd_root_size','bcmdog_attach','bcmdog_wdog_cb','bcmdog_wdog_set','wdog_register','sysctl_wdog']
assert all(name in symbols for name in required)
def file_offset(name):
    value=symbols[name]['address']
    matches=[p[2]+value-p[3] for p in ph if p[0]==1 and p[3]<=value<p[3]+p[5]]
    assert len(matches)==1
    return matches[0]
ram_size=struct.unpack_from('<I',data,file_offset('rd_root_size'))[0]
assert ram_size==12582912 and symbols['rd_root_image']['bytes']==ram_size
ram_offset=file_offset('rd_root_image')
ram=data[ram_offset:ram_offset+ram_size]
prefix=b'|This is the root ramdisk!\n'
assert len(ram)==ram_size and ram.startswith(prefix) and not any(ram[len(prefix):])
receipt={'passed':True,'file':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
         'ELF':'ELF64 little-endian AArch64 ET_EXEC; native-linker SystemV ABI matches official bsd.rd','entry_hex':hex(entry),
         'entry_in_executable_load_segment':True,'no_writable_executable_load_segment':True,
         'required_symbols':{name:symbols[name] for name in required},
         'rd_root_size':ram_size,'rd_root_image_offset':ram_offset,
         'ram_placeholder_exact_source_prefix_then_all_zero':True,
         'private_RAM_or_keys_in_bare_kernel':False,
         'boot_or_hardware_watchdog_proof':False,'physical_actions':False}
(OUT/'bare-kernel-elf-proof.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))

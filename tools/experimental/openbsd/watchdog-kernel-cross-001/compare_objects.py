"""Compare loadable sections and normalized AArch64 ELF relocations."""
import hashlib
import json
from pathlib import Path
import struct

OUT=Path(__file__).resolve().parent
NATIVE=Path('/home/dy09/daos-openbsd-cross-001/native-reference')


def elf(path):
    data=path.read_bytes()
    assert data[:8]==b'\x7fELF\x02\x01\x01\x0c'
    assert struct.unpack_from('<H',data,18)[0]==183
    off=struct.unpack_from('<Q',data,40)[0]
    size,count,names=struct.unpack_from('<HHH',data,58)
    assert size==64
    headers=[struct.unpack_from('<IIQQQQIIQQ',data,off+i*size) for i in range(count)]
    strings=data[headers[names][4]:headers[names][4]+headers[names][5]]
    string=lambda table,pos:table[pos:table.index(b'\0',pos)].decode()
    sections={string(strings,h[0]):h for h in headers}
    allocated={name:{'flags':h[2],'type':h[1],'bytes':h[5],
                     'align':h[8],'sha256':hashlib.sha256(data[h[4]:h[4]+h[5]]).hexdigest() if h[1]!=8 else None}
               for name,h in sections.items() if h[2]&2}
    relocs={}
    for name,h in sections.items():
        if h[1]!=4 or not headers[h[7]][2]&2:continue
        sym= headers[h[6]]
        strs=headers[sym[6]]
        symstrings=data[strs[4]:strs[4]+strs[5]]
        entries=[]
        for start in range(h[4],h[4]+h[5],24):
            offset,info,addend=struct.unpack_from('<QQq',data,start)
            symoff=sym[4]+(info>>32)*24
            sname=struct.unpack_from('<I',data,symoff)[0]
            entries.append([offset,info&0xffffffff,string(symstrings,sname),addend])
        relocs[name]=entries
    return allocated,relocs


def compare(names,native_root,cross_root,receipt):
    report={}
    for name in names:
        native=elf(native_root/(name+'.o'))
        cross=elf(cross_root/(name+'.o'))
        report[name]={'all_allocated_sections_identical':native[0]==cross[0],
                      'all_allocated_relocations_identical':native[1]==cross[1],
                      'native_allocated_sections':native[0], 'cross_allocated_sections':cross[0],
                      'native_relocations':native[1], 'cross_relocations':cross[1]}
    result={'passed':all(v['all_allocated_sections_identical'] and v['all_allocated_relocations_identical'] for v in report.values()),
            'status':'CODE_DATA_RELOCATION_COMPARISON_ONLY_NOT_BOOT_PROOF','objects':report}
    receipt.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:{k:v for k,v in value.items() if k.startswith('all_')} for name,value in report.items()},indent=2))


if __name__=='__main__':
    compare(('bcm2835_dog','kern_watchdog','watchdog-linked'),NATIVE,OUT,OUT/'object-code-comparison.json')

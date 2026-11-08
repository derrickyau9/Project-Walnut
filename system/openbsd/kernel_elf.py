"""Bounded ELF64/AArch64 inspection for offline RAM-kernel images.

Pure byte inspection only: no devices, credentials, filesystem writes or boot.
"""
import struct

class Elf64:
    def __init__(self,data):
        self.data=data
        if data[:7]!=b'\x7fELF\x02\x01\x01':raise ValueError('ELF64 little-endian version1 required')
        self.header=struct.unpack_from('<16sHHIQQQIHHHHHH',self.read(0,64))
        _,kind,machine,version,entry,phoff,shoff,flags,ehsize,phsize,phnum,shsize,shnum,shstr=self.header
        if kind!=2 or machine!=183 or version!=1 or ehsize!=64 or phsize!=56 or shsize!=64 or not 0<phnum<1024 or not 0<shnum<4096:
            raise ValueError('Bounded ET_EXEC ARM64 headers required')
        self.entry=entry;self.metadata=[(0,ehsize),(phoff,phoff+phsize*phnum),(shoff,shoff+shsize*shnum)]
        self.programs=[]
        for index in range(phnum):
            values=struct.unpack('<IIQQQQQQ',self.read(phoff+index*phsize,phsize))
            item=dict(zip(('type','flags','offset','vaddr','paddr','filesz','memsz','align'),values));item['index']=index
            if item['filesz']>item['memsz'] and item['type']==1:raise ValueError('Invalid load geometry')
            self.read(item['offset'],item['filesz']);self.programs.append(item)
        self.sections=[]
        for index in range(shnum):
            values=struct.unpack('<IIQQQQIIQQ',self.read(shoff+index*shsize,shsize))
            item=dict(zip(('name','type','flags','addr','offset','size','link','info','align','entsize'),values));item['index']=index
            if item['type']!=8:self.read(item['offset'],item['size'])
            self.sections.append(item)
        if not 0<=shstr<shnum or self.sections[shstr]['type']!=3:raise ValueError('Section name table required')
        strings=self.sections[shstr];self.names=self.read(strings['offset'],strings['size'])
        for item in self.sections:item['section_name']=self.string(self.names,item['name'])
        self.symbols={}
        for section in self.sections:
            if section['type']!=2:continue
            if section['entsize']!=24 or section['size']%24 or not 0<=section['link']<shnum:raise ValueError('Invalid symbol table')
            names=self.sections[section['link']]
            if names['type']!=3:raise ValueError('Symbol string table required')
            text=self.read(names['offset'],names['size']);self.metadata.extend(((section['offset'],section['offset']+section['size']),(names['offset'],names['offset']+names['size'])))
            for offset in range(section['offset'],section['offset']+section['size'],24):
                name,info,other,owner,value,size=struct.unpack('<IBBHQQ',self.read(offset,24));name=self.string(text,name)
                if not name or owner==0:continue
                item={'name':name,'info':info,'other':other,'section':owner,'value':value,'size':size}
                if name in self.symbols and self.symbols[name]!=item:
                    if name in ('rd_root_image','rd_root_size'):raise ValueError('Ambiguous duplicate RAM kernel symbol')
                    continue  # Unrelated local C symbols can share a name.
                self.symbols[name]=item
    def read(self,offset,count):
        if offset<0 or count<0 or offset+count>len(self.data):raise ValueError('ELF span outside pinned file')
        return self.data[offset:offset+count]
    @staticmethod
    def string(table,offset):
        if offset<0 or offset>=len(table):raise ValueError('String offset outside table')
        end=table.find(b'\0',offset)
        if end<0:raise ValueError('Unterminated ELF string')
        return table[offset:end].decode('ascii')
    def object(self,name,size):
        symbol=self.symbols[name]
        if symbol['size']!=size or symbol['info']&15!=1 or symbol['info']>>4!=1 or not 0<symbol['section']<len(self.sections):
            raise ValueError('Expected exact-sized OBJECT kernel symbol: '+name)
        section=self.sections[symbol['section']]
        if section['type']!=1 or section['flags']&3!=3 or not section['addr']<=symbol['value'] or symbol['value']+size>section['addr']+section['size']:
            raise ValueError('Kernel RAM object not wholly in writable allocated PROGBITS')
        loads=[item for item in self.programs if item['type']==1 and item['flags']&6==6 and item['vaddr']<=symbol['value'] and symbol['value']+size<=item['vaddr']+item['filesz']]
        if len(loads)!=1:raise ValueError('Kernel object must map to exactly one file-backed readable/writable PT_LOAD')
        load=loads[0];offset=load['offset']+symbol['value']-load['vaddr']
        if offset!=section['offset']+symbol['value']-section['addr']:raise ValueError('Section and load translation disagree')
        if any(offset<end and start<offset+size for start,end in self.metadata):raise ValueError('Object overlaps ELF metadata')
        return {'symbol':symbol,'section':section['section_name'],'file_offset':offset,'bytes':size,'load_index':load['index'],'load_flags':load['flags']}


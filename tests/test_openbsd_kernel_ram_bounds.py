"""Corrupt public bare-ELF geometry only; never open private RAM or keys."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ram_injection_bounds',ROOT/'system/openbsd/kernel_elf.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
BARE=ROOT/'build/DaOS_OpenBSD_001/watchdog-kernel-cross-001/bsd'

@unittest.skipUnless(BARE.exists(),'requires verified task bare kernel artifact')
class BoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=BARE.read_bytes()
        assert hashlib.sha256(cls.data).hexdigest()=='75f8fadfb342b86fb73e1b542e09f931380fedc4f14bc66afaa5b1f2f05ad9ed'
        cls.elf=mod.Elf64(cls.data);cls.ram=cls.elf.object('rd_root_image',12582912)
    def test_exact_symbols_and_RW_load_translation(self):
        self.assertEqual(8000636,self.ram['file_offset']);self.assertEqual(6,self.ram['load_flags'])
        size=self.elf.object('rd_root_size',4)
        self.assertEqual(12582912,struct.unpack('<I',self.elf.read(size['file_offset'],4))[0])
        image=self.elf.read(self.ram['file_offset'],12582912)
        self.assertEqual(b'|This is the root ramdisk!\n',image.rstrip(b'\0'))
    def test_truncated_ELF_is_rejected(self):
        with self.assertRaises(ValueError):mod.Elf64(self.data[:-100])
    def test_wrong_architecture_is_rejected(self):
        data=bytearray(self.data);struct.pack_into('<H',data,18,62)
        with self.assertRaises(ValueError):mod.Elf64(bytes(data))
    def test_RAM_tail_outside_filebacked_PT_LOAD_is_rejected(self):
        data=bytearray(self.data);load=self.elf.programs[self.ram['load_index']]
        new_size=self.ram['symbol']['value']+12582912-load['vaddr']-1
        struct.pack_into('<Q',data,self.elf.header[5]+load['index']*56+32,new_size)
        elf=mod.Elf64(bytes(data))
        with self.assertRaises(ValueError):elf.object('rd_root_image',12582912)
    def test_readonly_RAM_section_is_rejected(self):
        data=bytearray(self.data);number=self.ram['symbol']['section'];section=self.elf.sections[number]
        struct.pack_into('<Q',data,self.elf.header[6]+number*64+8,section['flags']&~1)
        elf=mod.Elf64(bytes(data))
        with self.assertRaises(ValueError):elf.object('rd_root_image',12582912)
    def test_disagreeing_section_and_load_offsets_are_rejected(self):
        data=bytearray(self.data);load=self.elf.programs[self.ram['load_index']]
        struct.pack_into('<Q',data,self.elf.header[5]+load['index']*56+8,load['offset']+16)
        elf=mod.Elf64(bytes(data))
        with self.assertRaises(ValueError):elf.object('rd_root_image',12582912)
    def test_wrong_declared_object_size_is_rejected(self):
        data=bytearray(self.data);found=0
        for section in self.elf.sections:
            if section['type']!=2:continue
            names=self.elf.sections[section['link']];strings=self.elf.read(names['offset'],names['size'])
            for offset in range(section['offset'],section['offset']+section['size'],24):
                name=struct.unpack_from('<I',data,offset)[0]
                if self.elf.string(strings,name)=='rd_root_image':
                    struct.pack_into('<Q',data,offset+16,12582911);found+=1
        self.assertEqual(1,found);elf=mod.Elf64(bytes(data))
        with self.assertRaises(ValueError):elf.object('rd_root_image',12582912)
if __name__=='__main__':unittest.main()

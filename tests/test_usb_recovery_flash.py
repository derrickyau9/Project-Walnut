"""Exercise USB rescue repair only on ordinary private files."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zlib

SOURCE=Path(__file__).resolve().parents[1]/'system/adb/recovery/flash.py'
spec=importlib.util.spec_from_file_location('recovery_flash',SOURCE)
flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(flash)


class FlashTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-flash-qa-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.original=b'A'*4096+b'B'*4096+b'C'*4096
        self.target=self.root/'root.img';self.target.write_bytes(self.original)
        self.state=self.root/'state.img';self.state.write_bytes(b'S'*12288)
        self.image=self.root/'repair.bin';self.image.write_bytes(b'X'*4096+b'Y'*4096)
        self.backend=flash.FileBackend(self.root,{'root':self.target,'state':self.state})
        self.request={'format':flash.FORMAT,'operation':'a'*32,'role':'root','image':'repair.bin',
            'image_bytes':8192,'image_sha256':flash.digest(self.image.read_bytes()),
            'extents':[{'target_offset':0,'image_offset':0,'bytes':4096,'before_sha256':flash.digest(b'A'*4096),'sha256':flash.digest(b'X'*4096)},
                       {'target_offset':8192,'image_offset':4096,'bytes':4096,'before_sha256':flash.digest(b'C'*4096),'sha256':flash.digest(b'Y'*4096)}]}

    def test_plan_never_changes_partition_or_creates_operation_receipt(self):
        result=flash.execute(self.request,self.backend)
        self.assertEqual(result['bytes_to_write'],8192)
        self.assertEqual(result['status'],'planned')
        self.assertEqual(self.target.read_bytes(),self.original)
        self.assertFalse((self.backend.work/('a'*32+'.json')).exists())

    def test_probe_computes_exact_partition_relative_preimage_read_only(self):
        result=flash.probe('root',4096,4096,self.backend)
        self.assertEqual(result['before_sha256'],flash.digest(b'B'*4096))
        self.assertEqual(result['partition_bytes'],len(self.original))
        self.assertEqual(result['sector_size'],512)
        self.assertEqual(self.target.read_bytes(),self.original)
        for offset,count in ((1,512),(0,0),(0,64*1024**2+512),(12288,512)):
            with self.subTest(offset=offset,count=count),self.assertRaises(ValueError):flash.probe('root',offset,count,self.backend)

    def test_apply_preserves_gaps_other_partition_and_readback_matches(self):
        result=flash.execute(self.request,self.backend,apply=True)
        self.assertEqual(result['status'],'complete')
        self.assertTrue(result['readback_verified'])
        self.assertEqual(self.target.read_bytes(),b'X'*4096+b'B'*4096+b'Y'*4096)
        self.assertEqual(self.state.read_bytes(),b'S'*12288)

    def test_same_completed_request_is_idempotent_without_second_write(self):
        flash.execute(self.request,self.backend,apply=True)
        with patch.object(flash,'write_at',side_effect=AssertionError('repeat wrote')):
            result=flash.execute(self.request,self.backend,apply=True)
        self.assertEqual(result['bytes_written'],0)
        self.assertEqual(result['statuses'],['already-applied','already-applied'])

    def test_reused_operation_with_changed_request_is_rejected(self):
        flash.execute(self.request,self.backend,apply=True)
        changed=copy.deepcopy(self.request);changed['extents']=changed['extents'][:1]
        with self.assertRaisesRegex(ValueError,'reused'):
            flash.execute(changed,self.backend,apply=True)

    def test_all_preimages_checked_before_first_write(self):
        self.target.write_bytes(self.original[:-1]+b'!')
        before=self.target.read_bytes()
        with patch.object(flash,'write_at') as writer:
            with self.assertRaisesRegex(ValueError,'preimage'):
                flash.execute(self.request,self.backend,apply=True)
        writer.assert_not_called();self.assertEqual(self.target.read_bytes(),before)

    def test_changed_truncated_or_wrong_extent_image_rejected(self):
        for mode in ('hash','short','extent'):
            request=copy.deepcopy(self.request)
            self.image.write_bytes(b'X'*4096+b'Y'*4096)
            if mode=='hash':self.image.write_bytes(b'Z'*8192)
            elif mode=='short':self.image.write_bytes(b'X'*4096)
            else:request['extents'][0]['sha256']='f'*64
            with self.subTest(mode=mode),self.assertRaises(ValueError):flash.execute(request,self.backend,apply=True)
            self.assertEqual(self.target.read_bytes(),self.original)

    def test_no_photos_calibration_whole_disk_or_arbitrary_paths(self):
        for role in ('photos','calibration','disk','../root',''):
            request=copy.deepcopy(self.request);request['role']=role
            with self.subTest(role=role),self.assertRaises(ValueError):flash.execute(request,self.backend,apply=True)
        for image in ('../repair.bin','/dev/mmcsd0','a/b','..'):
            request=copy.deepcopy(self.request);request['image']=image
            with self.subTest(image=image),self.assertRaises(ValueError):flash.execute(request,self.backend,apply=True)

    def test_bounds_overlap_alignment_and_boolean_coordinates_rejected(self):
        cases=[]
        for key,value in [('target_offset',12288),('target_offset',-512),('target_offset',True),('image_offset',1),('bytes',0),('bytes',8192)]:
            request=copy.deepcopy(self.request);request['extents'][0][key]=value;cases.append(request)
        request=copy.deepcopy(self.request);request['extents'][1]['target_offset']=0;cases.append(request)
        for request in cases:
            with self.subTest(extents=request['extents']),self.assertRaises(ValueError):flash.execute(request,self.backend,apply=True)

    @unittest.skipIf(os.name=='nt','Creating symlinks requires Windows developer privileges')
    def test_redirected_upload_and_partition_are_rejected(self):
        alias=self.root/'alias';alias.symlink_to(self.image)
        request=copy.deepcopy(self.request);request['image']='alias'
        with self.assertRaisesRegex(ValueError,'Redirected'):flash.execute(request,self.backend,apply=True)
        target_alias=self.root/'target-alias';target_alias.symlink_to(self.target)
        backend=flash.FileBackend(self.root,{'root':target_alias})
        with self.assertRaisesRegex(ValueError,'Redirected'):flash.execute(self.request,backend,apply=True)

    def test_partial_operation_resumes_only_wholly_verified_extents(self):
        original_write=flash.write_at
        calls=[]
        def interrupt(fd,offset,data):
            calls.append(offset)
            if len(calls)==2:raise OSError('simulated USB/process interruption')
            original_write(fd,offset,data)
        with patch.object(flash,'write_at',side_effect=interrupt):
            with self.assertRaises(OSError):flash.execute(self.request,self.backend,apply=True)
        self.assertEqual(self.target.read_bytes(),b'X'*4096+b'B'*4096+b'C'*4096)
        result=flash.execute(self.request,self.backend,apply=True)
        self.assertEqual(result['bytes_written'],4096)
        self.assertEqual(result['status'],'complete')

    def test_torn_extent_is_refused_and_receipt_captures_failure(self):
        original_write=flash.write_at
        def tear(fd,offset,data):
            original_write(fd,offset,data[:512]);raise OSError('power interruption')
        with patch.object(flash,'write_at',side_effect=tear):
            with self.assertRaises(OSError):flash.execute(self.request,self.backend,apply=True)
        receipt=json.loads((self.backend.work/('a'*32+'.json')).read_text())
        self.assertEqual(receipt['status'],'failed')
        with self.assertRaisesRegex(ValueError,'partial/torn'):flash.execute(self.request,self.backend,apply=True)

    def test_corrupt_physical_readback_is_not_reported_complete(self):
        original_write=flash.write_at
        def corrupt(fd,offset,data):original_write(fd,offset,b'!'*len(data))
        with patch.object(flash,'write_at',side_effect=corrupt):
            with self.assertRaisesRegex(OSError,'readback'):flash.execute(self.request,self.backend,apply=True)
        self.assertEqual(json.loads((self.backend.work/('a'*32+'.json')).read_text())['status'],'failed')

    def test_native_gate_refuses_non_freebsd_before_any_device_open(self):
        with patch.object(flash.sys,'platform','linux'),patch.object(flash.os,'open') as opened:
            with self.assertRaisesRegex(ValueError,'Native FreeBSD'):flash.execute(self.request,flash.NativeBackend(),apply=True)
        opened.assert_not_called()

    def test_provider_is_reproved_before_write_and_mount_change_aborts(self):
        original=self.backend.prove;calls=[]
        def changed(role):
            calls.append(role)
            if len(calls)==4:raise ValueError('Target became mounted')
            return original(role)
        with patch.object(self.backend,'prove',side_effect=changed),patch.object(flash,'write_at') as writer:
            with self.assertRaisesRegex(ValueError,'mounted'):flash.execute(self.request,self.backend,apply=True)
        writer.assert_not_called()


class GPTTests(unittest.TestCase):
    def make_gpt(self):
        sector=512;size=2*1024**2;last=size//sector-1
        disk=bytearray(size);entries=bytearray(128*128)
        root=uuid.UUID('015262e8-05ac-4848-8937-d30fa4799275')
        entries[:16]=uuid.UUID(flash.UFS_GUID).bytes_le;entries[16:32]=root.bytes_le
        struct.pack_into('<QQ',entries,32,40,1000);entries[56:74]='DaOS-root'.encode('utf-16le')
        disk_uuid=uuid.UUID('de14ca88-c732-4ada-a610-8ec7454e23fd')
        def header(current,backup,table):
            h=bytearray(512);h[:8]=b'EFI PART';struct.pack_into('<III',h,8,0x10000,92,0)
            struct.pack_into('<QQQQ',h,24,current,backup,34,last-33);h[56:72]=disk_uuid.bytes_le
            struct.pack_into('<QIII',h,72,table,128,128,zlib.crc32(entries))
            struct.pack_into('<I',h,16,zlib.crc32(h[:92]));return h
        disk[1024:1024+len(entries)]=entries
        disk[(last-32)*512:last*512]=entries
        disk[512:1024]=header(1,last,2);disk[last*512:]=header(last,1,last-32)
        return disk,str(disk_uuid),str(root)

    def test_valid_gpt_both_copies_and_partition_identity(self):
        disk,wanted,root=self.make_gpt()
        with tempfile.TemporaryFile() as f:
            f.write(disk);f.flush();disk_uuid,parts=flash.parse_gpt(f.fileno(),len(disk),512)
        self.assertEqual(disk_uuid,wanted);self.assertEqual(parts[1]['uuid'],root)
        self.assertEqual(parts[1]['label'],'DaOS-root')

    def test_bad_primary_backup_or_entry_crc_is_rejected(self):
        for offset in (512+20,len(self.make_gpt()[0])-512+20,1024+80):
            disk,_,_=self.make_gpt();disk[offset]^=1
            with tempfile.TemporaryFile() as f:
                f.write(disk);f.flush()
                with self.subTest(offset=offset),self.assertRaises(ValueError):flash.parse_gpt(f.fileno(),len(disk),512)

    def test_overlapping_photos_partition_is_rejected_even_with_valid_crcs(self):
        disk,_,_=self.make_gpt();last=len(disk)//512-1
        entries=bytearray(disk[1024:1024+16384])
        entries[128:256]=entries[:128]
        entries[128+16:128+32]=uuid.uuid4().bytes_le
        entries[128+56:128+128]=b'\0'*72
        entries[128+56:128+78]='DaOS-photos'.encode('utf-16le')
        disk[1024:1024+16384]=entries;disk[(last-32)*512:last*512]=entries
        for lba in (1,last):
            start=lba*512;header=bytearray(disk[start:start+512])
            struct.pack_into('<I',header,88,zlib.crc32(entries));struct.pack_into('<I',header,16,0)
            struct.pack_into('<I',header,16,zlib.crc32(header[:92]));disk[start:start+512]=header
        with tempfile.TemporaryFile() as f:
            f.write(disk);f.flush()
            with self.assertRaisesRegex(ValueError,'Overlapping'):flash.parse_gpt(f.fileno(),len(disk),512)


@unittest.skipIf(os.name=='nt','Native proof simulations require POSIX descriptor semantics')
class NativeGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daos-native-proof-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.marker=self.root/'marker.json';self.config=self.root/'identity.json'
        self.marker.write_text(json.dumps({'format':'DaOS-ram-rescue-v1','flash_enabled':True}))
        self.identity={'format':'DaOS-recovery-identity-v1','disk':'/dev/mmcsd0','disk_bytes':1048576,
            'disk_uuid':'12345678-1234-1234-1234-123456789abc','sector_size':512,
            'partitions':{'root':{'index':2,'uuid':'root-uuid','start_lba':40,'sectors':128},
                          'state':{'index':3,'uuid':'state-uuid','start_lba':168,'sectors':128}}}
        self.config.write_text(json.dumps(self.identity))
        self.disk=self.root/'disk.bin';self.disk.write_bytes(b'Q'*1048576)
        self.mounts='/dev/md0 / ufs ro 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'
        self.md='md0 preload 384M\n'
        self.labels='gpt/DaOS-root N/A mmcsd0p2\ngptid/root-uuid N/A mmcsd0p2\ngpt/DaOS-state N/A mmcsd0p3\n'

    def prove(self):
        original_open=os.open;original_stat=Path.stat
        def opened(path,flags,*args):
            self.assertEqual(str(path),'/dev/mmcsd0')
            self.assertFalse(flags & os.O_RDWR)
            return original_open(self.disk,flags,*args)
        def trusted(path):
            return self.marker if Path(path)==flash.MARKER else self.config
        def fake_stat(path,*args,**kw):
            if str(path)=='/dev/mmcsd0p2':return SimpleNamespace(st_mode=stat.S_IFCHR)
            return original_stat(path,*args,**kw)
        def command(args):
            if args[0]=='/sbin/mount':return self.mounts
            if args[0]=='/sbin/mdconfig':return self.md
            if args[0]=='/sbin/geom':return self.labels
            if args[0]=='/usr/sbin/diskinfo':return args[1]+' 512 '+str(1048576 if args[1]=='/dev/mmcsd0' else 65536)+'\n'
            raise AssertionError(args)
        parts={p['index']:{**p,'label':'DaOS-'+role,'type':flash.UFS_GUID} for role,p in self.identity['partitions'].items()}
        with patch.object(flash.sys,'platform','freebsd15'),patch.object(flash.os,'geteuid',return_value=0),patch.object(flash,'secure_path',side_effect=trusted),patch.object(flash,'command',side_effect=command),patch.object(flash.os,'open',side_effect=opened),patch.object(flash,'parse_gpt',return_value=(self.identity['disk_uuid'],parts)),patch.object(Path,'stat',fake_stat):
            return flash.NativeBackend().prove('root')

    def test_verified_ram_root_and_fixed_unmounted_card_are_accepted(self):
        result=self.prove();self.assertEqual(result['path'],Path('/dev/mmcsd0p2'));self.assertEqual(result['bytes'],65536)

    def test_single_user_disk_root_or_vnode_root_are_not_ram_recovery(self):
        for mode in ('disk','vnode'):
            self.mounts='/dev/md0 / ufs ro 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'
            self.md='md0 preload 384M\n'
            if mode=='disk':self.mounts=self.mounts.replace('/dev/md0','/dev/gpt/DaOS-root')
            else:self.md='md0 vnode 384M /root/rescue.img\n'
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.prove()

    def test_mounted_target_alias_or_state_partition_blocks_all_writes(self):
        for source in ('/dev/mmcsd0p2','/dev/gpt/DaOS-root','/dev/gptid/root-uuid','/dev/gpt/DaOS-state'):
            self.mounts='/dev/md0 / ufs ro 0 0\ntmpfs /DaOS-recovery-ram tmpfs rw 0 0\n'+source+' /mnt ufs ro 0 0\n'
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'mounted'):self.prove()

    def test_marker_and_actual_tmpfs_are_both_required(self):
        self.marker.write_text(json.dumps({'format':'DaOS-ram-rescue-v1','flash_enabled':False}))
        with self.assertRaisesRegex(ValueError,'marker'):self.prove()
        self.marker.write_text(json.dumps({'format':'DaOS-ram-rescue-v1','flash_enabled':True}))
        self.mounts=self.mounts.replace('tmpfs /DaOS-recovery-ram tmpfs','/dev/da1 /DaOS-recovery-ram ufs')
        with self.assertRaisesRegex(ValueError,'tmpfs'):self.prove()


if __name__=='__main__':unittest.main()

"""Image guards and optional integration with actual source-built NetBSD tools."""
from __future__ import annotations
import contextlib
import hashlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backends import image
from backends.image_formats import FAT32,FFS2,FormatError,gpt


class ImageGuards(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','POSIX target symlink semantics')
    def test_absolute_target_loader_symlinks_stay_inside_target_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'libexec').mkdir();(root/'usr/libexec').mkdir(parents=True)
            loader=root/'libexec/ld.elf_so';loader.write_bytes(b'target loader fixture')
            link=root/'usr/libexec/ld.elf_so';link.symlink_to('/libexec/ld.elf_so')
            self.assertEqual(image._target_file(root,'usr/libexec/ld.elf_so'),loader)
            link.unlink();link.symlink_to('../../libexec/ld.elf_so')
            self.assertEqual(image._target_file(root,'usr/libexec/ld.elf_so'),loader)
            link.unlink();link.symlink_to('/../../host-escape')
            with self.assertRaises(image.netbsd.BackendError):image._target_file(root,'usr/libexec/ld.elf_so')
            link.unlink();link.symlink_to('/usr/libexec/ld.elf_so')
            with self.assertRaises(image.netbsd.BackendError):image._target_file(root,'usr/libexec/ld.elf_so')

    def test_target_elf_accepts_build_id_and_rejects_foreign_abi(self):
        def note(name,tag,description):
            name=name+b'\0'
            return struct.pack('<III',len(name),len(description),tag)+name+b'\0'*((-len(name))%4)+description+b'\0'*((-len(description))%4)
        netbsd_note=note(b'NetBSD',1,struct.pack('<I',1101000000))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'target'
            for extra,allowed in ((note(b'GNU',3,b'build-id'),True),(note(b'GNU',1,struct.pack('<IIII',0,5,15,0)),False),(note(b'FreeBSD',1,b'version'),False)):
                notes=netbsd_note+extra;header=bytearray(64);header[:8]=b'\x7fELF\x02\x01\x01\0'
                struct.pack_into('<HHIQQQIHHHHHH',header,16,2,183,1,0,64,0,0,64,56,1,0,0,0)
                path.write_bytes(bytes(header)+struct.pack('<IIQQQQQQ',4,4,120,0,0,len(notes),len(notes),4)+notes)
                if allowed:self.assertEqual(image._elf(path)['NetBSD_version_notes'],[1101000000])
                else:
                    with self.assertRaises(image.netbsd.BackendError):image._elf(path)

    def test_relocatable_exception_requires_exact_archive_and_target_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay=Path(directory);source=overlay/'usr/pkg/lib/config/python.o';source.parent.mkdir(parents=True)
            header=bytearray(64);header[:8]=b'\x7fELF\x02\x01\x01\0';struct.pack_into('<HHI',header,16,1,183,1)
            source.write_bytes(header)
            info=b'OPSYS=NetBSD\nMACHINE_ARCH=aarch64\nOS_VERSION=11.0\n'
            archive=overlay/'usr/share/daos/ui-packages/python312-test.tgz';archive.parent.mkdir(parents=True)
            with tarfile.open(archive,'w:gz') as tar:
                for name,data in (('+BUILD_INFO',info),('lib/config/python.o',bytes(header))):
                    member=tarfile.TarInfo(name);member.size=len(data);tar.addfile(member,io.BytesIO(data))
            row={'path':'usr/pkg/lib/config/python.o','sha256':image.netbsd._hash(source),'elf_kind':'relocatable',
                 'abi_provenance':{'format':'DaOS-NetBSD-package-ABI-v1','package':'python312-test',
                                   'archive_sha512':image.netbsd._hash(archive,'sha512'),'archive_member':'lib/config/python.o',
                                   'OPSYS':'NetBSD','MACHINE_ARCH':'aarch64','OS_VERSION':'11.0',
                                   'build_info_sha256':hashlib.sha256(info).hexdigest(),'archive_build_info_checked':True}}
            self.assertTrue(image._relocatable(source,row,overlay)['package_provenance_verified'])
            row['abi_provenance']['OPSYS']='FreeBSD'
            with self.assertRaises(image.netbsd.BackendError):image._relocatable(source,row,overlay)
            row['abi_provenance']['OPSYS']='NetBSD';row['abi_provenance']['archive_sha512']='0'*128
            with self.assertRaises(image.netbsd.BackendError):image._relocatable(source,row,overlay)

    def test_layout_is_aligned_and_volume_sizes_are_bounded(self):
        parts,total=image._layout({'storage':{'efi_mib':64,'root_mib':128,'state_mib':16,'photos_mib':16}})
        self.assertEqual([p['label'] for p in parts],['daos-efi','daos-root','daos-state','daos-photos'])
        self.assertTrue(all(p['offset']%(4*1024*1024)==0 for p in parts))
        self.assertGreater(total,parts[-1]['offset']+parts[-1]['bytes'])
        for bad in (-1,True,65537,'128'):
            with self.assertRaises(image.netbsd.BackendError):image._layout({'storage':{'efi_mib':64,'root_mib':bad}})

    def test_image_paths_cannot_escape(self):
        for value in ('../escape','/host','usr/../etc','usr//bin','usr\\bin','line\nbreak'):
            with self.assertRaises(image.netbsd.BackendError):image._relative(value)

    def test_foreign_efi_and_broken_filesystems_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad';path.write_bytes(b'not EFI')
            with self.assertRaises(image.netbsd.BackendError):image._pe(path)
            path.write_bytes(b'\0'*(1024*1024))
            with self.assertRaises(FormatError):gpt(path)
            with self.assertRaises(FormatError):FFS2(path)
            with self.assertRaises(FormatError):FAT32(path)


@unittest.skipUnless(os.name=='posix' and os.environ.get('DAOS_NETBSD_TOOLS'),'Set DAOS_NETBSD_TOOLS for actual NetBSD host-tool integration')
class RealNetBSDFormats(unittest.TestCase):
    def test_complete_multi_volume_assembler_with_declared_fixture_provenance(self):
        """Real NetBSD filesystem/password tools; source-stage gates are fixtures.

        This covers orchestration and permissions, not NetBSD bootability.
        """
        tools=Path(os.environ['DAOS_NETBSD_TOOLS'])/'bin'
        with tempfile.TemporaryDirectory(prefix='daos-image-assembler-',dir=os.environ.get('DAOS_TEST_TMPDIR')) as directory:
            work=Path(directory);workspace=work/'workspace';workspace.mkdir()
            for name in ('state','tmp','config','src','packages/manifests'):(workspace/name).mkdir(parents=True,exist_ok=True)
            target=workspace/'destdir';target.mkdir()
            def put(name,data):
                path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path
            notes=struct.pack('<III',8,4,1)+b'NetBSD\0\0'+struct.pack('<I',1101000000)
            header=bytearray(64);header[:8]=b'\x7fELF\x02\x01\x01\0'
            struct.pack_into('<HHIQQQIHHHHHH',header,16,2,183,1,0,64,0,0,64,56,1,0,0,0)
            synthetic=bytes(header)+struct.pack('<IIQQQQQQ',4,4,120,0,0,len(notes),len(notes),4)+notes
            put('bin/sh',synthetic);put('sbin/init',synthetic)
            pe=bytearray(512);pe[:2]=b'MZ';struct.pack_into('<I',pe,60,128);pe[128:132]=b'PE\0\0'
            struct.pack_into('<H',pe,132,0xaa64);struct.pack_into('<H',pe,152,0x20b);struct.pack_into('<H',pe,220,10)
            put('usr/mdec/bootaa64.efi',pe)
            put('etc/master.passwd',b'root::0:0::0:0:root:/root:/bin/sh\n')
            put('etc/group',b'wheel:*:0:root\n');put('etc/rc.conf',b'# Fixture rc configuration\n')
            put('etc/ttys',b'console "/usr/libexec/getty default" vt100 off secure\nconstty "/usr/libexec/getty default" vt100 on secure\nttyE0 "/usr/libexec/getty Pc" wsvt25 off secure\nttyE1 "/usr/libexec/getty Pc" wsvt25 off secure\n')
            put('var/db/base-state-fixture',b'state partition keeps the source var tree\n')
            put('dev/MAKEDEV',b'#!/bin/sh\necho "./console type=char uid=0 gid=0 mode=0600 device=netbsd,0,0"\n')
            base_rows=image._tree_rows(target)
            base_rows['.']='. type=dir optional' # Actual unprivileged NetBSD METALOG root.
            put('METALOG',('\n'.join(base_rows[k] for k in sorted(base_rows))+'\n').encode())
            overlay=workspace/'packages/overlay';(overlay/'usr/share/daos').mkdir(parents=True)
            payload=overlay/'usr/share/daos/fixture';payload.write_bytes(b'fixture-only component; not a boot validation\n')
            for name in ('etc/rc.d/daos_ui','usr/sbin/daosctl'):
                path=overlay/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'#!/bin/sh\nexit 0\n');path.chmod(0o555)
            package={'format':'DaOS-BSD-component-manifest-v1','name':'fixture','version':'test',
                     'target':{'system':'NetBSD','architecture':'aarch64'},
                     'files':[{'path':'usr/share/daos/fixture','type':'file','sha256':image.netbsd._hash(payload),'mode':0o644}]}
            for name in ('etc/rc.d/daos_ui','usr/sbin/daosctl'):
                package['files'].append({'path':name,'type':'file','sha256':image.netbsd._hash(overlay/name),'mode':0o555})
            (workspace/'packages/manifests/fixture.json').write_text(__import__('json').dumps(package))
            kernel=workspace/'obj/kernels/DAOS_TEST/netbsd';kernel.parent.mkdir(parents=True);kernel.write_bytes(synthetic)
            profile={'name':'fixture-image','target':{'machine':'evbarm','architecture':'aarch64','board':'qemu-virt'},
                     'kernel':{'name':'DAOS_TEST'},'storage':{'efi_mib':64,'root_mib':128,'state_mib':16,'photos_mib':16}}
            mkconf=workspace/'config/mk.conf';mkconf.write_text('MKREPRO_TIMESTAMP=1793577600\n')
            for name in ('distrib/notes/common/legal.common','doc/3RDPARTY'):
                path=workspace/'src'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'Fixture source notice; not NetBSD release provenance\n')
            lock=work/'sources.lock.json';lock.write_text('{"fixture":true}\n')
            spec={'profile':profile,'profile_path':work/'profile.json','lock_path':lock,
                  'lock_hash':image.netbsd._hash(lock),'input_hash':'b'*64,'mkconf_path':mkconf}
            for stage in ('tools','distribution','kernel'):
                (workspace/'state'/(stage+'.json')).write_text(__import__('json').dumps({'status':'success','input_hash':'b'*64}))
            with (mock.patch.object(image.netbsd,'_profile_inputs',return_value=spec),
                  mock.patch.object(image.netbsd,'_configured'),mock.patch.object(image.netbsd,'_verify_sources'),
                  mock.patch.object(image.netbsd,'_cached_artifacts'),
                  mock.patch.object(image,'_tool',side_effect=lambda unused,name:str(tools/name)),
                  contextlib.redirect_stdout(io.StringIO())):
                result=image.build(work/'profile.json',workspace)
            self.assertEqual(result['status'],'assembled-not-boot-validated')
            self.assertFalse(result['target_boot_validated'])
            self.assertEqual(result['mounts'],{'state':'/var','photos':'/var/DaOS/camera/photos'})
            parts={p['label']:p for p in result['validation']['partitions']};raw=Path(result['image']['path'])
            with FFS2(raw,parts['daos-state']['offset'],parts['daos-state']['bytes']) as fs:
                self.assertEqual(fs.sha256('db/base-state-fixture'),image.netbsd._hash(target/'var/db/base-state-fixture'))
                self.assertEqual(fs.lookup('DaOS/camera')['mode']&0o777,0o700)
                self.assertEqual(fs.lookup('DaOS/camera')['uid'],1000)
            with FFS2(raw,parts['daos-photos']['offset'],parts['daos-photos']['bytes']) as fs:
                self.assertEqual(fs.inode(2)['mode']&0o777,0o700)
                self.assertEqual(fs.inode(2)['uid'],1000)
            with FFS2(raw,parts['daos-root']['offset'],parts['daos-root']['bytes']) as fs:
                self.assertEqual((fs.inode(2)['uid'],fs.inode(2)['gid']),(0,0))
                ttys=fs.contents(fs.lookup('etc/ttys')).decode()
                self.assertIn('constty\t"/usr/libexec/getty default"\tvt100\toff secure',ttys)
                self.assertIn('plcom0\t"/usr/libexec/getty std.115200"\tvt100\ton secure',ttys)
                check=fs.contents(fs.lookup('etc/rc.d/daos_image_check')).decode()
                self.assertNotIn('/usr/sbin/daosctl verify',check)
                self.assertIn('identity-and-native-smoke',check)
                self.assertNotIn('daos_pkg_finalize',check)

    def test_raw_gpt_fat_and_ffs_payloads_from_actual_netbsd_tools(self):
        tools=Path(os.environ['DAOS_NETBSD_TOOLS'])/'bin'
        with tempfile.TemporaryDirectory(prefix='daos-image-formats-',dir=os.environ.get('DAOS_TEST_TMPDIR')) as directory:
            work=Path(directory);boot=work/'boot';boot.mkdir();root=work/'root';root.mkdir()
            (boot/'EFI/BOOT').mkdir(parents=True)
            (boot/'EFI/BOOT/bootaa64.efi').write_bytes(b'EFI source payload fixture\n')
            (boot/'dtb/broadcom').mkdir(parents=True)
            (boot/'dtb/broadcom/bcm2711-rpi-4-b.dtb').write_bytes(b'long FAT filename fixture')
            (root/'usr/share/daos').mkdir(parents=True)
            payload=bytes(range(256))*12289 # >3 MiB exercises double-indirect FFS blocks at bsize=4096
            (root/'usr/share/daos/payload.bin').write_bytes(payload)
            (root/'usr/share/daos/current').symlink_to('payload.bin')
            (root/'usr/share/daos/absolute').symlink_to('/usr/share/daos/payload.bin')
            (root/'usr/share/daos/relative').symlink_to('../daos/payload.bin')
            (root/'netbsd').write_bytes(b'kernel source payload fixture\n')
            rows=image._tree_rows(root)
            metadata=work/'root.mtree';metadata.write_text('\n'.join(rows[k] for k in sorted(rows))+'\n')
            rootfs=work/'root.fs';fatfs=work/'efi.fs'
            def run(argv):
                result=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=120)
                self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            run([tools/'nbmakefs','-rx','-B','le','-t','ffs','-s','128m','-o','version=2,bsize=4096,fsize=512,density=8192','-F',metadata,rootfs,root])
            run([tools/'nbmakefs','-t','msdos','-s','64m','-o','fat_type=32,sectors_per_cluster=1',fatfs,boot])
            with FFS2(rootfs) as filesystem:
                expected=hashlib.sha256(payload).hexdigest()
                for name in ('payload.bin','current','absolute','relative'):
                    self.assertEqual(filesystem.sha256('usr/share/daos/'+name),expected)
                self.assertEqual(filesystem.sha256('usr/share/daos/current',False),hashlib.sha256(b'payload.bin').hexdigest())
            with FAT32(fatfs) as filesystem:
                self.assertEqual(filesystem.sha256('EFI/BOOT/bootaa64.efi'),image.netbsd._hash(boot/'EFI/BOOT/bootaa64.efi'))
                self.assertEqual(filesystem.sha256('dtb/broadcom/bcm2711-rpi-4-b.dtb'),image.netbsd._hash(boot/'dtb/broadcom/bcm2711-rpi-4-b.dtb'))
            layout,total=image._layout({'storage':{'efi_mib':64,'root_mib':128}})
            raw=work/'raw.img'
            with raw.open('xb') as output:output.truncate(total)
            image._copy_region(fatfs,raw,layout[0]['offset']);image._copy_region(rootfs,raw,layout[1]['offset'])
            run([tools/'nbgpt',raw,'create','-p','32'])
            for part in layout:run([tools/'nbgpt',raw,'add','-i',part['index'],'-b',part['start_sector'],'-s',part['sectors'],'-t',part['kind'],'-l',part['label']])
            run([tools/'nbgpt',raw,'set','-a','bootme','-i','2'])
            # Pi EEPROM needs the FAT partition in the hybrid MBR as well.
            first=layout[0]
            run([tools/'aarch64--netbsd-fdisk','-f','-g','-u','-0','-a','-s',f"12/{first['start_sector']}/{first['sectors']}",'-F',raw])
            run([tools/'aarch64--netbsd-fdisk','-f','-g','-u','-3','-s',f"238/1/{first['start_sector']-1}",'-F',raw])
            entries={'netbsd':{'type':'file','sha256':image.netbsd._hash(root/'netbsd')},
                     'usr/share/daos/payload.bin':{'type':'file','sha256':hashlib.sha256(payload).hexdigest()},
                     'usr/share/daos/current':{'type':'link','sha256':hashlib.sha256(b'payload.bin').hexdigest()}}
            boot_files={path.relative_to(boot).as_posix():image.netbsd._hash(path) for path in boot.rglob('*') if path.is_file()}
            result=image._inspect(raw,layout,entries,boot_files)
            self.assertEqual(result['verified_payload_files'],3)
            self.assertTrue(result['both_gpt_crcs_valid'])
            with raw.open('r+b') as stream:
                stream.seek(512+16);stream.write(b'\0\0\0\0')
            with self.assertRaises(FormatError):gpt(raw)


if __name__=='__main__':unittest.main()

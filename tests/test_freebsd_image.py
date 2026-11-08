"""Pure image/package guards; these tests never mount or build an OS image."""
import argparse
import io
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

IMAGE = Path(__file__).resolve().parents[1]/'system/freebsd/image'
sys.path.insert(0,str(IMAGE))
common = importlib.import_module('common')
collector = importlib.import_module('collect_runtime')
assembler = importlib.import_module('assemble_image')


def fat_header_fixture(path,fat_sectors=1009):
    """Sparse BPB precheck fixture only; native fsck is mocked in guard tests."""
    boot=bytearray(512);boot[510:512]=b'\x55\xaa'
    struct.pack_into('<H',boot,11,512);boot[13]=1
    struct.pack_into('<H',boot,14,32);boot[16]=2
    struct.pack_into('<I',boot,32,131072);struct.pack_into('<I',boot,36,fat_sectors)
    struct.pack_into('<I',boot,44,2);struct.pack_into('<H',boot,48,1);struct.pack_into('<H',boot,50,6)
    info=bytearray(512);info[:4]=b'RRaA';info[484:488]=b'rrAa';info[508:512]=b'\x00\x00\x55\xaa'
    with Path(path).open('wb') as stream:
        stream.truncate(64*1024**2);stream.seek(0);stream.write(boot)
        stream.seek(512);stream.write(info);stream.seek(6*512);stream.write(boot)


class FreeBSDImageGuards(unittest.TestCase):
    def test_fat32_geometry_counts_reserved_entries_and_rejects_undersized_table(self):
        for mib in (64,256,8192):
            geometry=assembler.efi_geometry(mib)
            data=geometry['total_sectors']-32-2*geometry['sectors_per_fat']
            self.assertGreaterEqual(geometry['sectors_per_fat']*128,data+2)
        self.assertEqual(1009,assembler.efi_geometry(64)['sectors_per_fat'])
        with self.assertRaises(ValueError):assembler.efi_geometry(32)
        with tempfile.TemporaryDirectory() as tmp:
            fat=Path(tmp)/'efi.fat';fat_header_fixture(fat,1008)
            with self.assertRaisesRegex(ValueError,'two reserved entries'):assembler.validate_efi_geometry(fat)
            fat_header_fixture(fat,1009)
            self.assertTrue(assembler.validate_efi_geometry(fat)['passed'])

    def test_all_five_native_checks_are_read_only_and_bound_to_unchanged_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);fat_header_fixture(work/'efi.fat')
            for name in ('root','var','calibration','photos'):(work/(name+'.ufs')).write_bytes(b'checker mock fixture')
            result=assembler.subprocess.CompletedProcess([],0)
            with patch.object(assembler.subprocess,'run',return_value=result) as run:
                report=assembler.validate_filesystems(work)
            self.assertTrue(report['passed']);self.assertEqual(5,len(report['checks']))
            self.assertEqual(5,run.call_count)
            self.assertEqual(['/sbin/fsck_msdosfs','-M','-n',str(work/'efi.fat')],run.call_args_list[0].args[0])
            self.assertTrue(all(check['unchanged'] and check['sha256_before']==check['sha256_after'] for check in report['checks']))
            self.assertTrue((work/'filesystem-validation.json').is_file())

    def test_native_checker_failure_or_crash_aborts_before_gpt_and_success_receipt(self):
        for returncode in (8,-11):
            with self.subTest(returncode=returncode),tempfile.TemporaryDirectory() as tmp:
                work=Path(tmp);fat_header_fixture(work/'efi.fat')
                argv=['build','--base-state','unused','--runtime','unused','--project','unused',
                      '--native-evidence','unused','--native-command','unused','--work-dir',str(work)]
                result=assembler.subprocess.CompletedProcess([],returncode)
                with patch.object(assembler,'preflight',return_value={'work':work}),\
                     patch.object(assembler,'commands',return_value=[{'cwd':str(work),'argv':['/usr/bin/mkimg']}]),\
                     patch.object(assembler,'require_freebsd'),patch.object(assembler,'prepare',return_value=({},{})),\
                     patch.object(assembler.subprocess,'run',return_value=result) as run,\
                     patch('sys.stderr',new=io.StringIO()),patch('sys.stdout',new=io.StringIO()) as stdout:
                    self.assertEqual(1,assembler.main(argv))
                self.assertEqual(1,run.call_count)
                self.assertEqual('/sbin/fsck_msdosfs',run.call_args.args[0][0])
                self.assertNotIn('DaOS_IMAGE_ASSEMBLED',stdout.getvalue())
                self.assertFalse((work/'image-evidence.json').exists());self.assertFalse((work/'DaOS-arm64.img').exists())
                self.assertFalse(json.loads((work/'filesystem-validation.json').read_text())['passed'])

    def test_source_hardlinks_are_preserved_only_inside_new_stage_and_budgeted_per_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'source';root=Path(tmp)/'new-stage';base.mkdir();root.mkdir()
            for name in ('rescue','var'):(base/name).mkdir()
            original=base/'rescue/rescue';original.write_bytes(b'one recovery executable')
            paths=('rescue/rescue','rescue/sh','rescue/cat','var/alias')
            for name in paths[1:]:os.link(original,base/name)
            before=original.stat()
            maps={'.':assembler.metadata('.','dir'),'rescue':assembler.metadata('rescue','dir'),
                  'var':assembler.metadata('var','dir')}
            maps.update({name:assembler.metadata(name,mode=0o555) for name in paths})
            source_usage=assembler.file_usage(base,maps)
            self.assertEqual(len(b'one recovery executable'),source_usage['unique_bytes'])
            self.assertEqual(4*source_usage['unique_bytes'],source_usage['logical_bytes'])
            root_usage=assembler.file_usage(base,maps,('var/',))
            var_usage=assembler.file_usage(base,{'var/alias':maps['var/alias']})
            self.assertEqual(source_usage['unique_bytes'],root_usage['unique_bytes'])
            self.assertEqual(source_usage['unique_bytes'],var_usage['unique_bytes'])
            assembler.copy_base(base,root,maps)
            staged={(root/name).stat().st_ino for name in paths}
            self.assertEqual(1,len(staged));self.assertNotIn(before.st_ino,staged)
            after=original.stat()
            self.assertEqual((before.st_ino,before.st_nlink,before.st_mode,before.st_mtime_ns),
                             (after.st_ino,after.st_nlink,after.st_mode,after.st_mtime_ns))
            self.assertEqual(b'one recovery executable',original.read_bytes())

    def test_overlay_detaches_hardlinked_master_passwd_before_write_or_chmod(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'source';root=Path(tmp)/'new-stage';(base/'etc').mkdir(parents=True);root.mkdir()
            original=base/'etc/master.passwd';original.write_bytes(b'original account text')
            os.link(original,base/'etc/account-alias')
            maps={'.':assembler.metadata('.','dir'),'etc':assembler.metadata('etc','dir'),
                  'etc/master.passwd':assembler.metadata('etc/master.passwd',mode=0o600),
                  'etc/account-alias':assembler.metadata('etc/account-alias',mode=0o600)}
            assembler.copy_base(base,root,maps)
            sibling=root/'etc/account-alias';before=sibling.stat();source_before=original.stat()
            assembler.install_file(root,maps,'etc/master.passwd',b'new locked account text',0o600)
            self.assertEqual(b'new locked account text',(root/'etc/master.passwd').read_bytes())
            self.assertEqual(b'original account text',sibling.read_bytes())
            self.assertNotEqual(before.st_ino,(root/'etc/master.passwd').stat().st_ino)
            self.assertEqual(before.st_mode,sibling.stat().st_mode)
            self.assertEqual((source_before.st_ino,source_before.st_nlink,source_before.st_mode,source_before.st_mtime_ns),
                             (original.stat().st_ino,original.stat().st_nlink,original.stat().st_mode,original.stat().st_mtime_ns))
            self.assertEqual(b'original account text',original.read_bytes())

    def test_conflicting_hardlink_intended_metadata_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'source';base.mkdir();(base/'one').write_bytes(b'one inode');os.link(base/'one',base/'two')
            good={'one':assembler.metadata('one',flags='none'),'two':assembler.metadata('two',flags='0')}
            self.assertEqual(1,assembler.file_usage(base,good)['unique_files'])
            for field,value in (('uid',1),('gid',1),('mode',0o600),('flags','schg')):
                entries={name:dict(item) for name,item in good.items()};entries['two'][field]=value
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError,'conflicting intended metadata'):
                        assembler.file_usage(base,entries)
                    root=Path(tmp)/('new-'+field);root.mkdir()
                    with self.assertRaisesRegex(ValueError,'conflicting intended metadata'):
                        assembler.copy_base(base,root,entries)
                    self.assertEqual([],list(root.iterdir()))

    def test_runtime_static_verifier_identity_does_not_replace_arm_source(self):
        source={'system':'FreeBSD','release':'15.1-RELEASE-p6','machine':'arm64'}
        verifier={'system':'FreeBSD','release':'15.1-RELEASE-p6','machine':'amd64'}
        manifest={'host':source,'source_host':source,'verification_host':verifier,
            'collection_kind':'offline-verified-transport','offline_declared_checksums_verified':True,
            'status':'completed','completed_runtime':True,'payload_content_verified':True,
            'target_execution_validated':False,'physical_hardware_validated':False,
            'source_provisional_sha256':'a'*64,'source_code_sha256':{'exporter':'b'*64},
            'verifier_source_code_sha256':{'verifier':'c'*64},
            'offline_validation':{'format':'DaOS-freebsd-runtime-offline-validation-v1','prior_run_promoted':False,
                'all_retained_regular_file_declared_checksums_verified':True,
                'target_execution_validated':False,'physical_hardware_validated':False}}
        result=assembler.runtime_verification_boundary(manifest,'arm64')
        self.assertEqual('arm64',result['source_host']['machine'])
        self.assertEqual('amd64',result['verification_host']['machine'])
        self.assertFalse(result['target_execution_validated'])
        for key,value in [('payload_content_verified',False),('target_execution_validated',True),
                          ('verification_host',None),('source_provisional_sha256','missing')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                assembler.runtime_verification_boundary(dict(manifest,**{key:value}),'arm64')

    def test_non_freebsd_collection_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'DaOS-runtime-guard'
            with patch.object(common.platform,'system',return_value='Linux'):
                with self.assertRaises(ValueError):
                    collector.collect(argparse.Namespace(architecture='arm64',output=str(output),package=['python312']))
            self.assertFalse(output.exists())

    def test_new_destinations_never_replace_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'DaOS-image-existing';output.mkdir();(output/'owner').write_text('preserved')
            with self.assertRaises(ValueError):common.new_path(output,'DaOS-image')
            self.assertEqual('preserved',(output/'owner').read_text())
            with self.assertRaises(ValueError):common.new_path(Path(tmp)/'..'/'usr'/'DaOS-image-owner','DaOS-image')

    def test_runtime_paths_are_prefix_bounded(self):
        self.assertEqual('usr/local/lib/libQt5Core.so',collector.package_path('/usr/local/lib/libQt5Core.so'))
        for value in ('/etc/master.passwd','/root/.ssh/id_ed25519','/usr/local/../../etc/passwd',
                      '/usr/local/etc/auth.json','/usr/local/lib/evil\nfile'):
            with self.subTest(value=value),self.assertRaises(ValueError):collector.package_path(value)

    def test_dependency_closure_handles_cycles_and_missing_packages(self):
        packages={'a':{},'b':{},'c':{}}
        edges={'a':['b'],'b':['a','c'],'c':[]}
        self.assertEqual(['a','b','c'],collector.closure(['a'],packages,edges.__getitem__))
        with self.assertRaises(ValueError):collector.closure(['missing'],packages,edges.__getitem__)

    def test_native_elf_cannot_mix_target_architecture_or_osabi(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'program';header=bytearray(64);header[:8]=b'\x7fELF\x02\x01\x01\x09'
            struct.pack_into('<H',header,18,183);binary.write_bytes(header)
            self.assertEqual(183,common.elf(binary,'arm64')['machine'])
            with self.assertRaises(ValueError):common.elf(binary,'amd64')
            header[7]=0;binary.write_bytes(header)
            with self.assertRaises(ValueError):common.elf(binary,'arm64')
            self.assertEqual(0,common.elf(binary,'arm64',kernel=True)['osabi'])
            with self.assertRaises(ValueError):common.elf(binary,'amd64',kernel=True)

    def test_uefi_loader_must_match_arm64_not_amd64(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'loader.efi';header=bytearray(128);header[:2]=b'MZ';struct.pack_into('<I',header,60,64)
            header[64:68]=b'PE\0\0';struct.pack_into('<H',header,68,0xaa64);binary.write_bytes(header)
            self.assertEqual(0xaa64,common.pe(binary,'arm64')['pe_machine'])
            with self.assertRaises(ValueError):common.pe(binary,'amd64')

    def test_incomplete_completed_records_do_not_skip_provenance(self):
        with self.assertRaises(ValueError):assembler.validate_base_record({'status':'completed','artifact_sha256':{}},'arm64')
        with self.assertRaises(ValueError):assembler.validate_native_record({'status':'completed','passed':True},'arm64','0'*64)

    def test_cross_build_requires_explicit_actual_target_validation(self):
        record={'format':'DaOS-freebsd-arm64-target-validation-v1','status':'completed','passed':True,
            'target_runtime_validated':True,'uid':1000,'execution_kind':'actual_target','build_kind':'cross',
            'cross_evidence_sha256':'a'*64,'cross_build':{'format':'DaOS-freebsd-arm64-cross-build-v1','status':'completed',
                'passed':True,'target':{'architecture':'arm64'},'artifacts':{'hf-camerad':{'sha256':'c'*64}}},
            'uname':{'sysname':'FreeBSD','machine':'arm64'},
            'ctest':{'passed':True,'ran':4,'targets':sorted(assembler.CTEST_TARGETS)},
            'python_tests':{'passed':True,'ran':2,'passed_count':1,'successful_tests':['case'],
                            'failures':[],'errors':[],'skipped':[{'test':'optional','reason':'unavailable'}]},
            'source_input_sha256':dict.fromkeys(assembler.NATIVE_ANCHORS,'b'*64),
            'artifacts':{'hf-camerad':{'sha256':'c'*64}}}
        assembler.validate_native_record(record,'arm64','c'*64)
        record['uname']['machine']='amd64'
        with self.assertRaises(ValueError):assembler.validate_native_record(record,'arm64','c'*64)

    def test_no_root_metalog_ownership_is_not_replaced_by_builder_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp);(stage/'etc').mkdir()
            (stage/'etc/master.passwd').write_text('root:*:0:0::0:0:root:/root:/bin/sh\ncamera:*:1000:1000::0:0:camera:/var/DaOS:/bin/sh\n')
            (stage/'etc/group').write_text('wheel:*:0:\ncamera:*:1000:\n')
            names=('.', './bin/sh','./sbin/init','./boot/kernel/kernel')
            (stage/'METALOG').write_text('\n'.join(name+' type='+('dir' if name=='.' else 'file')+' uname=root gname=wheel mode=0555' for name in names)+'\n')
            entries=assembler.read_metalog(stage/'METALOG',stage)
            self.assertEqual(0,entries['bin/sh']['uid']);self.assertEqual(0,entries['bin/sh']['gid'])
            out=stage/'output.mtree';assembler.write_mtree(stage,entries,out)
            self.assertIn('./bin/sh type=file uid=0 gid=0 mode=0555',out.read_text())

    def test_metalog_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp);(stage/'etc').mkdir()
            (stage/'etc/master.passwd').write_text('root:*:0:0::0:0:r:/root:/bin/sh\n')
            (stage/'etc/group').write_text('wheel:*:0:\n')
            (stage/'METALOG').write_text('./../owner type=file uid=0 gid=0 mode=0644\n')
            with self.assertRaises(ValueError):assembler.read_metalog(stage/'METALOG',stage)

    def test_makefs_consumes_manifests_without_mount_or_pkg(self):
        args=argparse.Namespace(root_mib=1536,var_mib=128,calibration_mib=32,photos_mib=256,efi_mib=64,epoch=0,architecture='arm64')
        plan=assembler.commands({'work':Path('/var/DaOS-image-test')},args)
        self.assertEqual(6,len(plan))
        self.assertEqual('/usr/sbin/makefs',plan[0]['argv'][0])
        self.assertTrue(plan[0]['argv'][-1].endswith('root.mtree'))
        self.assertNotIn('-F',plan[0]['argv'])
        self.assertTrue(any('efi/DaOS-efi:=' in item for item in plan[-1]['argv']))
        self.assertFalse(any('gptboot' in item or 'mount' in item or 'pkg' in item for step in plan for item in step['argv']))

    def test_stable_copy_is_create_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.write_bytes(b'owned bytes')
            target=Path(tmp)/'target';common.stable_copy(source,target)
            with self.assertRaises(FileExistsError):common.stable_copy(source,target)
            self.assertEqual(b'owned bytes',target.read_bytes())

    def test_overlay_excludes_host_python_bytecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'__pycache__').mkdir()
            (root/'app.py').write_text('pass\n');(root/'__pycache__/app.cpython-312.pyc').write_bytes(b'host-private-path')
            (root/'other.pyc').write_bytes(b'host bytecode')
            self.assertEqual(['app.py'],[path.relative_to(root).as_posix() for path in assembler.overlay_files(root)])

    def test_manifest_descendants_never_follow_an_image_symlink(self):
        entries={'.':assembler.metadata('.', 'dir'),
                 'escape':assembler.metadata('escape','link',link='/outside'),
                 'escape/owner':assembler.metadata('escape/owner')}
        with self.assertRaises(ValueError):common.validate_tree(entries)

    @unittest.skipUnless(sys.platform!='win32','POSIX symlink fixture')
    def test_copy_rejects_redirected_destination_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'outside').mkdir();(root/'private').mkdir()
            (root/'private'/'escape').symlink_to(root/'outside',target_is_directory=True)
            source=root/'source';source.write_bytes(b'owned')
            with self.assertRaises(ValueError):common.stable_copy(source,root/'private/escape/owner')
            self.assertFalse((root/'outside/owner').exists())


if __name__=='__main__':unittest.main()

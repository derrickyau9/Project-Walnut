"""Target recipe safety and evidence rejection; fixtures are not guest boots."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from tools import target_build as target


def boot_fixture(phase='first'):
    rid='27110500-0000-4000-8000-000000000001'; elf={'elf_machine':183,'sha256':'e'*64}
    guest={'passed':True,'phase':phase,'simulated':True,'execution_mode':'qemu-system','effective_uid':1000,
        'machine':'aarch64','journal_rows':1,'native_process_closed':True,'physical_display_validated':False,
        'sensor_acquisition_validated':False,'capture_status':'complete','python_elf':elf,'native_authority':elf,
        'qt_extensions':{name:dict(elf) for name in ('QtCore','QtGui','QtQml','QtQuick','QtTest')},
        'request_id':rid,'capture_id':rid,'frame_id':rid,'dng_sha256':'d'*64,'export_sha256':'d'*64,
        'dng_bytes':25000,'export_bytes':25000,'restart_same_id_without_recapture':phase=='resume'}
    return {'format':'hf-arm64-image-boot-v1','passed':True,'phase':phase,'request_id':rid,'rootfs_unchanged':True,
        'rootfs_sha256_before':'a'*64,'rootfs_sha256_after':'a'*64,'data_sha256_before':'b'*64,
        'data_sha256_after':'c'*64,'guest_report':guest,'persistent_same_request_id':phase=='resume'}


class TargetRecipeTests(unittest.TestCase):
    def test_plan_is_argv_only_and_offline_requires_explicit_verified_caches(self):
        value=target.profile()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'new target'
            with self.assertRaisesRegex(ValueError,'offline'):
                target.command_plan(value,output,offline=True)
            plan=target.command_plan(value,output,native_dir=root/'native',kernel_dir=root/'kernel',
                                     inputs_dir=root/'inputs',offline=True)
            self.assertEqual([step['id'] for step in plan],['rootfs-inputs','rootfs-stage','image','first','resume'])
            self.assertTrue(all(step['command'][:3]==[sys.executable,'-B','-m'] for step in plan))
            self.assertIn('--offline',plan[0]['command'])
            images=[Path(step['command'][step['command'].index('--build-dir')+1]) for step in plan if step['id'] in ('image','first','resume')]
            self.assertEqual(images,[target.image_path(output)]*3)
            self.assertEqual(images[0].parent,target.ROOT/'build')
            self.assertNotEqual(target.image_path(output),target.image_path(root/'another'))
            self.assertFalse(output.exists())
        for value in ('../outside','/absolute','camera/../outside','camera\\file','disk:device'):
            with self.assertRaises(ValueError): target.source_path(value)

    def test_new_workspace_cannot_pollute_source_camera_or_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for candidate in (root/'camera/image',root/'system/new'):
                with self.assertRaises(ValueError): target.work_path(candidate,root=root)
                self.assertFalse(candidate.exists())
            for name in ('.git','.codex','.aws','unrelated'):
                with self.assertRaisesRegex(ValueError,'beneath build'):
                    target.work_path(root/name/'new',root=root)
            data=root/'data'; data.mkdir(); (data/'journal.sqlite3').write_bytes(b'owner data')
            with self.assertRaisesRegex(ValueError,'camera data'): target.work_path(data/'child',root=root)
            release=root/'release'; release.mkdir(); (release/'manifest.json').write_text(json.dumps({'format':'hf-static-arm64-sim-package-v1'}))
            with self.assertRaisesRegex(ValueError,'immutable release'): target.work_path(release/'child',root=root)
            self.assertEqual((data/'journal.sqlite3').read_bytes(),b'owner data')
            stage=root/'build'/'staged'; stage.mkdir(parents=True)
            (stage/'stage-manifest.json').write_text(json.dumps({'format':'hf-arm64-qt-rootfs-v1'}))
            with self.assertRaisesRegex(ValueError,'staged rootfs'):
                target.work_path(stage/'rootfs'/'nested',root=root)

    def test_plan_expands_home_consistently_without_creating_output(self):
        expanded=Path('~/hf-target-no-create').expanduser().resolve()
        value=target.profile()
        plan=target.command_plan(value,Path('~/hf-target-no-create'),native_dir=target.ROOT/'build/native',
            kernel_dir=target.ROOT/'build/kernel',inputs_dir=target.ROOT/'build/inputs',offline=True)
        stage=next(item for item in plan if item['id']=='rootfs-stage')
        self.assertEqual(Path(stage['command'][stage['command'].index('--output')+1]),expanded/'rootfs-tree')

    def test_boot_verification_rejects_host_fake_wrong_abi_and_lost_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'; first=boot_fixture()
            path.write_text(json.dumps(first)); target.verify_boot(path,'first',base_hash='a'*64)
            bad=[]
            for key,value in [('execution_mode','qemu-user'),('machine','x86_64'),('effective_uid',0),('journal_rows',True),
                              ('physical_display_validated',True),('export_sha256','f'*64),('dng_sha256','')]:
                sample=copy.deepcopy(first); sample['guest_report'][key]=value; bad.append(sample)
            sample=copy.deepcopy(first); sample['guest_report']['qt_extensions']['QtQuick']['elf_machine']=62; bad.append(sample)
            for sample in bad:
                path.write_text(json.dumps(sample))
                with self.assertRaises(ValueError): target.verify_boot(path,'first',base_hash='a'*64)
            resume=boot_fixture('resume'); resume['data_sha256_before']=first['data_sha256_after']
            path.write_text(json.dumps(resume)); target.verify_boot(path,'resume',base_hash='a'*64,previous=first)
            resume['guest_report']['frame_id']=str(uuid.uuid4()); path.write_text(json.dumps(resume))
            with self.assertRaisesRegex(ValueError,'persistent'): target.verify_boot(path,'resume',base_hash='a'*64,previous=first)

    @unittest.skipUnless(sys.platform=='linux','Linux owned process-group cancellation')
    def test_stage_timeout_stops_its_own_process_group_and_retains_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'timeout.log'
            code='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"]); print(p.pid,flush=True); time.sleep(10)'
            stage={'id':'timeout-fixture','command':[sys.executable,'-c',code],'timeout':.5}
            with self.assertRaisesRegex(RuntimeError,'deadline'):
                target.run_stage(stage,log,cwd=tmp)
            pid=int(log.read_text().strip()); proc=Path('/proc')/str(pid)/'stat'
            # A dead orphan may briefly remain a zombie until the host reaps it.
            self.assertTrue(not proc.exists() or proc.read_text().split()[2]=='Z')

    @unittest.skipUnless(sys.platform=='linux','Linux owned subprocess log bound')
    def test_stage_output_bound_is_not_a_successful_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'bounded.log'
            stage={'id':'output-fixture','command':[sys.executable,'-c','import time; print("x"*8192,flush=True); time.sleep(5)'],'timeout':2}
            with mock.patch.object(target,'MAX_STAGE_LOG',1024):
                with self.assertRaisesRegex(RuntimeError,'log exceeded'): target.run_stage(stage,log,cwd=tmp)
            self.assertGreater(log.stat().st_size,1024)

    @unittest.skipUnless(sys.platform=='linux','Linux fast-exit subprocess bounds')
    def test_fast_exit_log_overflow_is_rejected_and_descendant_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'fast-output.log'
            stage={'id':'fast-output','command':[sys.executable,'-c','print("x"*8192,flush=True)'],'timeout':2}
            with mock.patch.object(target,'MAX_STAGE_LOG',1024):
                with self.assertRaisesRegex(RuntimeError,'log exceeded'): target.run_stage(stage,log,cwd=tmp)
            log=Path(tmp)/'descendant.log'
            code='import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"]); print(p.pid,flush=True)'
            target.run_stage({'id':'descendant','command':[sys.executable,'-c',code],'timeout':2},log,cwd=tmp)
            pid=int(log.read_text().strip()); proc=Path('/proc')/str(pid)/'stat'
            import time
            for _ in range(20):
                if not proc.exists() or proc.read_text().split()[2]=='Z': break
                time.sleep(.01)
            self.assertTrue(not proc.exists() or proc.read_text().split()[2]=='Z')


if __name__=='__main__': unittest.main()

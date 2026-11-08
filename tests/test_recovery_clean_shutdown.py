"""Execute the shutdown transaction with real marker I/O and harmless mounts."""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('clean_boot_config',ROOT/'system/adb/recovery/boot_config.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
LABELS='''  gpt/DaOS-efi  N/A  mmcsd0p1
gptid/efi-uuid N/A mmcsd0p1
gpt/DaOS-root N/A mmcsd0p2
gpt/DaOS-state N/A mmcsd0p3
'''
MOUNTS='''/dev/gpt/DaOS-root / ufs ro,noatime 1 1
devfs /dev devfs rw 0 0
/dev/gpt/DaOS-state /var ufs rw 2 2
'''


@unittest.skipIf(os.name=='nt','Execute the real shutdown shell function under POSIX sh')
class CleanShutdownTests(unittest.TestCase):
    def run_hook(self,*,labels=LABELS,mounts=MOUNTS,marker=b'\1',mode='ok',recovery='NO'):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);efi=root/'efi';(efi/'DaOS-recovery').mkdir(parents=True)
            flag=efi/'DaOS-recovery/normal.running'
            if marker is not None:flag.write_bytes(marker)
            (root/'labels').write_text(labels);(root/'mounts').write_text(mounts.replace('/DaOS-recovery-efi',str(efi)))
            log=root/'log'
            prefix=f'''qa_root={shlex.quote(str(root))}
qa_mode={shlex.quote(mode)}
qa_recovery={shlex.quote(recovery)}
qa_sync_count=0
qa_umount_count=0
qa_kenv() {{ printf '%s\\n' "$qa_recovery"; }}
qa_geom() {{ /bin/cat "$qa_root/labels"; }}
qa_mount() {{
    if [ "$1" = -p ]; then /bin/cat "$qa_root/mounts"; return; fi
    printf 'MOUNT:%s\\n' "$*" >> "$qa_root/log"
    [ "$qa_mode" != mount-failed ]
}}
qa_umount() {{
    qa_umount_count=$((qa_umount_count+1))
    printf 'UMOUNT:%s\\n' "$*" >> "$qa_root/log"
    [ "$qa_mode" != umount-failed ] || [ "$qa_umount_count" -gt 1 ]
}}
qa_sync() {{
    qa_sync_count=$((qa_sync_count+1))
    printf 'SYNC\\n' >> "$qa_root/log"
    [ "$qa_mode" != sync-failed ] || [ "$qa_sync_count" -gt 1 ]
}}
'''
            function=boot.CLEAN_SHUTDOWN.split('DaOS_rescue_clear() {',1)[1].split('\nload_rc_config',1)[0]
            script='DaOS_rescue_clear() {'+function+'\nDaOS_rescue_clear\n'
            for old,new in (('/bin/kenv','qa_kenv'),('/sbin/mount','qa_mount'),('/sbin/geom','qa_geom'),('/sbin/umount','qa_umount'),('/bin/sync','qa_sync')):
                script=script.replace(old,new)
            script=script.replace('/DaOS-recovery-efi',str(efi))
            result=subprocess.run(['/bin/sh','-c',prefix+script],capture_output=True,text=True,timeout=5)
            return result,flag.read_bytes() if flag.exists() else None,log.read_text() if log.exists() else ''

    def test_normal_shutdown_clears_one_byte_after_alias_resolution(self):
        result,marker,log=self.run_hook()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(marker,b'\0')
        self.assertIn('MOUNT:-t msdosfs -o rw /dev/gpt/DaOS-efi ',log)
        self.assertNotIn('rw,noatime',log)
        self.assertEqual(log.count('UMOUNT:'),1)

    def test_direct_provider_root_is_accepted_and_other_usb_fat_is_unrelated(self):
        mounts=MOUNTS.replace('/dev/gpt/DaOS-root','/dev/mmcsd0p2')+'/dev/da0p1 /media/usb msdosfs rw 0 0\n'
        result,marker,_=self.run_hook(mounts=mounts)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(marker,b'\0')

    def test_duplicate_provider_or_alias_refuses_second_fat_mount(self):
        for source in ('/dev/mmcsd0p1','/dev/gpt/DaOS-efi','/dev/gptid/efi-uuid','/dev/extra/efi','/dev/mmcsd0'):
            labels=LABELS+'extra/efi N/A gptid/efi-uuid\n'
            with self.subTest(source=source):
                result,marker,log=self.run_hook(labels=labels,mounts=MOUNTS+source+' /other msdosfs rw 0 0\n')
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertEqual(marker,b'\1');self.assertEqual(log,'')

    def test_missing_ambiguous_cyclic_or_foreign_identity_is_rejected(self):
        for labels,mounts in (('',MOUNTS),(LABELS+'gpt/DaOS-efi N/A mmcsd1p1\n',MOUNTS),
                (LABELS+'cycle/a N/A cycle/b\ncycle/b N/A cycle/a\n',MOUNTS),
                (LABELS.replace('mmcsd0p1','mmcsd1p1'),MOUNTS),
                (LABELS,MOUNTS.replace('/dev/gpt/DaOS-root','/dev/md0')),
                (LABELS,MOUNTS+'invalid mount table\n'),(LABELS,MOUNTS+MOUNTS.splitlines(True)[0])):
            with self.subTest(labels=labels,mounts=mounts):
                result,marker,log=self.run_hook(labels=labels,mounts=mounts)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertEqual(marker,b'\1');self.assertEqual(log,'')

    def test_existing_mountpoint_is_rejected_even_if_other_provider(self):
        result,marker,log=self.run_hook(mounts=MOUNTS+'/dev/da0p1 /DaOS-recovery-efi msdosfs rw 0 0\n')
        self.assertNotEqual(result.returncode,0,result.stderr);self.assertEqual(marker,b'\1');self.assertEqual(log,'')

    def test_recovery_shutdown_skips_without_inspecting_or_writing_fat(self):
        result,marker,log=self.run_hook(labels='invalid',mounts='invalid',recovery='YES')
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(marker,b'\1');self.assertEqual(log,'')

    def test_write_transaction_failure_rearms_marker_and_unmounts(self):
        for mode in ('sync-failed','umount-failed'):
            with self.subTest(mode=mode):
                result,marker,log=self.run_hook(mode=mode)
                self.assertNotEqual(result.returncode,0,result.stderr);self.assertEqual(marker,b'\1')
                self.assertIn('UMOUNT:',log)

    def test_mount_failure_or_missing_marker_never_reports_clean(self):
        result,marker,log=self.run_hook(mode='mount-failed')
        self.assertNotEqual(result.returncode,0);self.assertEqual(marker,b'\1');self.assertNotIn('UMOUNT:',log)
        result,marker,log=self.run_hook(marker=None)
        self.assertNotEqual(result.returncode,0);self.assertIsNone(marker);self.assertIn('UMOUNT:',log)


if __name__=='__main__':unittest.main()

"""Versioned native/helpers activation and candidate-only Wi-Fi staging."""
import hashlib,importlib.util,json,os
from pathlib import Path
import shutil,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
installer=load('app004_installer','system/freebsd/raspberrypi/deploy_camera_app.py')
wifi=load('wifi004_installer','system/freebsd/raspberrypi/deploy_wifi_004.py')
from tools.camera_experience_bundle import build

class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.bundle=cls.root/'camera-experience-004.tar.gz'
        # Packaging/rollback tests do not execute ARM code. Use explicit ELF
        # metadata fixtures so a clean checkout needs no private build outputs.
        native=cls.root/'native-fixtures';native.mkdir()
        header=bytearray(64);header[:8]=b'\x7fELF\x02\x01\x01\x09'
        header[18:20]=(183).to_bytes(2,'little')
        for name in ('hf-camerad','hf-imager'):
            (native/name).write_bytes(header+name.encode())
        cls.receipt=build(cls.bundle,'camera-experience-004',native_dir=native)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def previous(self,root):
        for name,data in [(installer.ENTRY_PATH,b'old main'),(installer.ACTIVE_PATH,b'{"version":"camera-experience-003","manifest_sha256":"old"}'),('boot/kernel/kernel',b'stable-kernel')]:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        for name in installer.SERVICE_ENTRIES:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# previous '+name)
    def test_complete_native_payload_install_then_exact_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root';root.mkdir();self.previous(root);work=Path(directory)/'work';work.mkdir()
            incoming,manifest=installer.unpack(self.bundle,self.receipt['archive_sha256'],work)
            before={name:(root/name).read_bytes() for name in (installer.ENTRY_PATH,installer.ACTIVE_PATH,*installer.SERVICE_ENTRIES)}
            installer.install_files(root,incoming,manifest,work)
            release=root/'opt/hasslefree/releases/camera-experience-004'
            for name in ('hf-camerad','hf-imager'):
                self.assertEqual(183,int.from_bytes((release/'bin'/name).read_bytes()[18:20],'little'))
                if os.name!='nt':self.assertEqual(0o755,(release/'bin'/name).stat().st_mode&0o777)
            self.assertTrue((release/'support/DaOS-system-ops.py').is_file());self.assertTrue((release/'ui/on-device/remote.html').is_file())
            self.assertTrue((release/'camera/processing/licenses/OpenCV-Apache-2.0.txt').is_file())
            self.assertEqual('camera-experience-004',json.loads((root/installer.ACTIVE_PATH).read_text())['version'])
            self.assertEqual(b'stable-kernel',(root/'boot/kernel/kernel').read_bytes())
            installer.rollback_files(root,work)
            self.assertEqual(before,{name:(root/name).read_bytes() for name in before})
    def test_interruption_keeps_old_selector_until_every_file_and_helper_is_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root';root.mkdir();self.previous(root);work=Path(directory)/'work';work.mkdir()
            incoming,manifest=installer.unpack(self.bundle,self.receipt['archive_sha256'],work)
            actual=installer.write_file;activated=[]
            def write(path,source,mode=0o644):
                if path==root/installer.ACTIVE_PATH:
                    release=root/'opt/hasslefree/releases/camera-experience-004'
                    for entry in manifest['files']:
                        local=entry['path'].removeprefix('opt/hasslefree/') if entry['path'].startswith('opt/hasslefree/') else 'support/camera-app-installer.py'
                        self.assertEqual(entry['sha256'],installer.sha(release/local))
                    for service in installer.SERVICE_ENTRIES:self.assertIn('version selector',(root/service).read_text())
                    activated.append(True);raise OSError('injected power interruption before activation')
                actual(path,source,mode)
            with patch.object(installer,'write_file',write),self.assertRaises(OSError):installer.install_files(root,incoming,manifest,work)
            self.assertEqual([True],activated)
            self.assertEqual('camera-experience-003',json.loads((root/installer.ACTIVE_PATH).read_text())['version'])
            installer.rollback_files(root,work)
    def test_wifi_commit_rejects_incomplete_stale_or_other_boot_evidence(self):
        report={'format':'DaOS-wifi-004-validation-v1','kernel_sha256':wifi.KERNEL_SHA,'payload_sha256':wifi.PAYLOAD_SHA,'boot_time':'same','completed_at':1000,'checks':dict.fromkeys(wifi.CHECKS,True),'passed':True,'errors':[]}
        wifi.validate_commit(report,boot_time='same',now=1010)
        for changed in [dict(report,boot_time='other'),dict(report,completed_at=1),dict(report,checks={})]:
            with self.assertRaises(ValueError):wifi.validate_commit(changed,boot_time='same',now=2000)
    def test_wifi_archive_is_candidate_only_and_has_matching_kernel_modules(self):
        archive=ROOT/'build/DaOS_Camera_004/wifi-update-004.tar.gz'
        if not archive.exists():self.skipTest('004 candidate not built')
        with tempfile.TemporaryDirectory() as directory:
            incoming,manifest=wifi.unpack(archive,Path(directory))
            names={e['path'] for e in manifest['files']}
            for name in ('kernel','if_brcmfmac.ko','daos_adb_template.ko','rpi_ft5406.ko'):
                self.assertIn('root/boot/'+wifi.KERNEL+'/'+name,names)
            self.assertIn('root/usr/share/DaOS/wifi/source/sdio-f0-timeout.patch',names)
            self.assertFalse(any('ui/on-device' in name or 'DaOS-system-service' in name for name in names))
            self.assertFalse(manifest['physical_wifi_validated'])
if __name__=='__main__':unittest.main()

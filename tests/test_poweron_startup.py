"""Whole-boot candidate settings and pre-GUI display ownership barrier."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
startup=load('poweron_display','system/freebsd/image/overlay/usr/local/libexec/DaOS-camera-startup.py')
profile=load('poweron_profile','system/freebsd/raspberrypi/fastboot_profile.py')
uboot=load('poweron_uboot','system/adb/recovery/build_uboot_selector.py')
deploy=load('poweron_deploy','system/freebsd/raspberrypi/deploy_system_ui.py')


class PoweronStartupTests(unittest.TestCase):
    def test_fast_preload_pins_validated_active_release_instead_of_legacy_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);legacy=base/'ui/on-device/main.py';legacy.parent.mkdir(parents=True);legacy.write_text('# selector')
            self.assertEqual(startup.resolve_program(legacy),legacy)
            release=base/'releases/camera-experience-008';selected=release/'ui/on-device/main.py'
            selected.parent.mkdir(parents=True);selected.write_text('# new application')
            active={'version':'camera-experience-008','manifest_sha256':'a'*64}
            pointer=base/'camera-app-active.json';pointer.write_text(json.dumps(active))
            (release/'release-ready.json').write_text(json.dumps(active))
            pinned=startup.resolve_program(legacy);self.assertEqual(pinned,selected)
            pointer.write_text(json.dumps(dict(active,version='camera-experience-009')))
            self.assertEqual(pinned,selected,'An activation change must not change the already pinned entrypoint')
            with self.assertRaises(ValueError):startup.resolve_program(legacy)
            pointer.write_text(json.dumps(dict(active,manifest_sha256='b'*64)))
            with self.assertRaises(ValueError):startup.resolve_program(legacy)
            pointer.write_text(json.dumps(dict(active,version='../escape')))
            with self.assertRaises(ValueError):startup.resolve_program(legacy)

    def test_fast_activation_requires_all_root_components_and_preserves_boot_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root';incoming=Path(directory)/'incoming'
            with self.assertRaises(ValueError):deploy.apply(root,incoming,{'files':[]},'candidate',fast_boot=True)
            self.assertFalse(root.exists())
            (root/'boot').mkdir(parents=True);(root/'etc').mkdir()
            (root/'boot/loader.conf').write_text('autoboot_delay="1"\ninit_script="/rescue/early.sh"\n')
            (root/'etc/rc.conf').write_text('DaOS_usb_adb_enable="YES"\nroot_rw_mount="NO"\n')
            entries=[]
            for name in deploy.FAST_FILES:
                p=incoming/'payload'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'candidate fixture')
                entries.append({'path':name,'mode':0o644,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            with patch.object(deploy.os,'chown',create=True):
                changed=deploy.apply(root,incoming,{'files':entries},'candidate',fast_boot=True)
            self.assertEqual(len(changed),6)
            loader=(root/'boot/loader.conf').read_text();rc=(root/'etc/rc.conf').read_text()
            self.assertIn('init_script="/rescue/early.sh"',loader)
            self.assertIn('autoboot_delay="0"',loader)
            self.assertIn('DaOS_camera_fastboot="YES"',rc)
            self.assertIn('DaOS_usb_adb_enable="YES"',rc);self.assertIn('root_rw_mount="NO"',rc)
            self.assertNotIn('autoboot_delay',deploy.settings()[0])

    def test_normal_profile_keeps_storage_usb_and_recovery_configuration(self):
        loader='autoboot_delay="1"\ninit_script="/rescue/early.sh"\nusb_template_load="YES"\n'
        rc='root_rw_mount="NO"\nDaOS_usb_adb_enable="YES"\nDaOS_camera_fastboot="NO"\n'
        changed,new_rc=profile.normal_boot_configuration(loader,rc)
        self.assertIn('autoboot_delay="0"',changed);self.assertEqual(changed.count('autoboot_delay='),1)
        self.assertIn('init_script="/rescue/early.sh"',changed);self.assertIn('usb_template_load="YES"',changed)
        self.assertIn('root_rw_mount="NO"',new_rc);self.assertIn('DaOS_usb_adb_enable="YES"',new_rc)
        self.assertIn('DaOS_camera_fastboot="YES"',new_rc)
        self.assertEqual(profile.normal_boot_configuration(changed,new_rc),(changed,new_rc))

    def test_selector_fast_build_changes_only_command_and_countdown(self):
        baseline='CONFIG_USE_BOOTCOMMAND=y\nCONFIG_BOOTCOMMAND="bootflow scan"\nCONFIG_BOOTDELAY=2\nCONFIG_USB=y\n'
        stable=uboot.selector_configuration(baseline)
        fast=uboot.selector_configuration(baseline,fast_boot=True)
        self.assertIn('CONFIG_BOOTDELAY=2\n',stable);self.assertIn('CONFIG_BOOTDELAY=0\n',fast)
        a,b=uboot.configuration(baseline),uboot.configuration(fast)
        self.assertEqual({key for key in a if a[key]!=b[key]},{'CONFIG_BOOTDELAY','CONFIG_BOOTCOMMAND'})
        self.assertIn(uboot.BOOTCOMMAND,fast);self.assertIn('CONFIG_USB=y',fast)
        with self.assertRaises(ValueError):uboot.selector_configuration(baseline.replace('BOOTDELAY=2','BOOTDELAY=5'),fast_boot=True)

    def test_child_waits_for_root_marker_and_times_out_without_one(self):
        clock=[0.];sleeps=[];runtime=Path('/var/run/DaOS-camera');path=runtime/'display-0123456789abcdef'
        parent=SimpleNamespace(st_mode=stat.S_IFDIR|0o750,st_uid=0)
        flag=SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1,st_size=0)
        def lstat(value):
            if value==runtime:return parent
            if clock[0]<.1:raise FileNotFoundError
            return flag
        def sleep(value):clock[0]+=value;sleeps.append(value)
        startup.wait_for_display(path,clock=lambda:clock[0],sleep=sleep,lstat=lstat)
        self.assertGreaterEqual(clock[0],.1);self.assertTrue(sleeps)
        def missing(value):
            if value==runtime:return parent
            raise FileNotFoundError
        with self.assertRaises(TimeoutError):startup.wait_for_display(path,clock=lambda:clock[0],sleep=sleep,lstat=missing)
        self.assertLess(clock[0],30.2)

    def test_untrusted_marker_and_parent_are_rejected(self):
        runtime=Path('/var/run/DaOS-camera');path=runtime/'display-0123456789abcdef'
        parent=SimpleNamespace(st_mode=stat.S_IFDIR|0o750,st_uid=0)
        for fields in ({'st_uid':1000},{'st_mode':stat.S_IFLNK|0o777},
                       {'st_mode':stat.S_IFREG|0o666},{'st_nlink':2},{'st_size':1}):
            flag=SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1,st_size=0)
            for key,value in fields.items():setattr(flag,key,value)
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                startup.wait_for_display(path,lstat=lambda p:parent if p==runtime else flag)
        with self.assertRaises(ValueError):startup.wait_for_display(runtime/'../display-0123456789abcdef')
        with self.assertRaises(ValueError):startup.wait_for_display(path,lstat=lambda _:SimpleNamespace(st_mode=stat.S_IFDIR|0o777,st_uid=0))

    @unittest.skipUnless(importlib.util.find_spec('PyQt5'),'optional Qt runtime unavailable')
    def test_preloading_creates_no_app_camera_or_database(self):
        code='''import importlib.util,sys,sqlite3,subprocess
from pathlib import Path
from unittest.mock import patch
root=Path.cwd();sys.path.insert(0,str(root/'ui/on-device'))
spec=importlib.util.spec_from_file_location('startup',root/'system/freebsd/image/overlay/usr/local/libexec/DaOS-camera-startup.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with patch.object(sqlite3,'connect',side_effect=AssertionError('database during preload')),patch.object(subprocess,'Popen',side_effect=AssertionError('process during preload')):
 module.preload_modules()
from PyQt5.QtCore import QCoreApplication
assert QCoreApplication.instance() is None
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=20,
                              env=dict(os.environ,QT_QPA_PLATFORM='offscreen'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_preloading_uses_both_UI_and_camera_package_from_selected_tree(self):
        code='''import importlib.util,sys,tempfile,json
from pathlib import Path
root=Path.cwd()
spec=importlib.util.spec_from_file_location('startup',root/'system/freebsd/image/overlay/usr/local/libexec/DaOS-camera-startup.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as directory:
 base=Path(directory);legacy=base/'ui/on-device/main.py';legacy.parent.mkdir(parents=True);legacy.write_text('# selector')
 (legacy.parent/'boot_probe.py').write_text("raise AssertionError('legacy module imported')")
 release=base/'releases/camera-experience-008';program=release/'ui/on-device/main.py';program.parent.mkdir(parents=True);program.write_text('# selected')
 (release/'camera').mkdir();(release/'camera/__init__.py').write_text("flavor='selected'")
 (program.parent/'boot_probe.py').write_text('from camera import flavor\\nassert flavor == "selected"')
 record={'version':'camera-experience-008','manifest_sha256':'a'*64}
 (base/'camera-app-active.json').write_text(json.dumps(record));(release/'release-ready.json').write_text(json.dumps(record))
 sys.path.insert(0,str(legacy.parent));module.PRELOAD=('boot_probe',)
 selected=module.resolve_program(legacy);module.preload_modules(selected)
 import boot_probe,camera
 assert Path(boot_probe.__file__).parent == program.parent
 assert Path(camera.__file__).parent == release/'camera'
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

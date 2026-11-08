"""Host checks of isolation, ELF guards and the real read-only shell CLI.

Run with: python3 -m unittest discover -s system/bsd-buildroot/packages/tests -v
These checks never claim to execute NetBSD/aarch64 programs.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

PACKAGES = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


camera = module('camera_recipe', PACKAGES / 'daos-camera/build.py')
core = module('core_recipe', PACKAGES / 'daos-core/install.py')


def elf(interpreter='/libexec/ld.elf_so', note=b'NetBSD\0', machine=183):
    header=bytearray(64)
    header[:8]=b'\x7fELF\x02\x01\x01\x02'
    struct.pack_into('<HHIQQQIHHHHHH',header,16,2,machine,1,0,64,0,0,64,56,2,0,0,0)
    interp=interpreter.encode()+b'\0'
    notes=struct.pack('<III',len(note),4,1)+note+b'\0'*((-len(note))%4)+struct.pack('<I',1100000000)
    first=176;second=first+len(interp)
    ph1=struct.pack('<IIQQQQQQ',3,4,first,0,0,len(interp),len(interp),1)
    ph2=struct.pack('<IIQQQQQQ',4,4,second,0,0,len(notes),len(notes),4)
    return bytes(header)+ph1+ph2+interp+notes


class CameraGuards(unittest.TestCase):
    def test_command_graph_uses_only_selected_sysroot_and_netbsd_tools(self):
        compile_steps,link_steps=camera.commands(Path('/project'),Path('/output'),Path('/target'),'/tools/aarch64--netbsd-gcc','/tools/aarch64--netbsd-g++')
        self.assertEqual(len(compile_steps),12)
        self.assertEqual(len(link_steps),6)
        for command in compile_steps+link_steps:
            self.assertIn('--sysroot='+str(Path('/target')),command)
            self.assertNotIn('-ldl',command)
            self.assertNotIn('freebsd',' '.join(command).lower())
            self.assertNotIn('linux',' '.join(command).lower())
        self.assertTrue(any('sqlite3.c' in ' '.join(command) for command in compile_steps))
        self.assertEqual(set(Path(command[-1]).name for command in link_steps),set(camera.GROUPS))

    def test_netbsd_abi_is_required_in_addition_to_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'target'
            path.write_bytes(elf())
            facts=camera.target_elf(path)
            self.assertEqual(facts['NetBSD_version_note'],1100000000)
            path.write_bytes(elf('/usr/libexec/ld.elf_so'))
            self.assertEqual(camera.target_elf(path)['interpreter'],'/usr/libexec/ld.elf_so')
            for data in (elf('/libexec/ld-elf.so.1'),elf(note=b'FreeBSD\0'),elf(machine=62),b'not-elf'):
                path.write_bytes(data)
                with self.assertRaises(ValueError):camera.target_elf(path)

    def test_staging_cannot_replace_existing_files_or_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)
            for name in ('../escape','/absolute'):
                with self.assertRaises(ValueError):camera.destination_file(stage,name)
            path=camera.destination_file(stage,'usr/bin/test')
            path.write_bytes(b'existing')
            with self.assertRaises(ValueError):camera.destination_file(stage,'usr/bin/test')


class CorePackage(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.directory=Path(self.temporary.name)
        self.stage=self.directory/'root';self.stage.mkdir()
        self.release=self.directory/'release.json'
        self.release.write_text(json.dumps({'version':'test-source-build','base':{'name':'NetBSD','version':'11.1'},'profile':'qemu-aarch64'}))
        self.payload=self.stage/'usr/share/example.txt';self.payload.parent.mkdir(parents=True)
        self.payload.write_bytes(b'bounded component input\n')
        self.component=self.directory/'camera.json'
        self.record={'format':'DaOS-BSD-component-manifest-v1','name':'example','version':'1',
                     'target':{'system':'NetBSD','architecture':'aarch64'},
                     'files':[{'path':'usr/share/example.txt','type':'file','sha256':core.sha(self.payload)}]}
        self.component.write_text(json.dumps(self.record))
        self.args=argparse.Namespace(destdir=self.stage,release=self.release,component_manifest=[self.component],manifest=self.directory/'core.json')

    def tearDown(self):self.temporary.cleanup()

    def install(self):
        with contextlib.redirect_stdout(io.StringIO()):core.install(self.args)

    def test_install_records_source_facts_without_claiming_target_execution(self):
        self.install()
        data=json.loads((self.stage/'usr/share/daos/release.json').read_text())
        self.assertEqual(data['base']['name'],'NetBSD')
        self.assertEqual(data['version'],'test-source-build')
        self.assertEqual([c['name'] for c in data['components']],['example','daos-core'])
        manifest=json.loads(self.args.manifest.read_text())
        self.assertFalse(manifest['verification_scope']['target_execution_claimed'])
        with self.assertRaises(ValueError):self.install()

    def test_foreign_target_and_changed_input_are_rejected_before_install(self):
        self.record['target']['system']='FreeBSD'
        self.component.write_text(json.dumps(self.record))
        with self.assertRaises(ValueError):self.install()
        self.assertFalse((self.stage/'usr/sbin/daosctl').exists())
        self.record['target']['system']='NetBSD';self.component.write_text(json.dumps(self.record))
        self.payload.write_text('tampered')
        with self.assertRaises(ValueError):self.install()

    def test_manifest_path_guard(self):
        for name in ('../outside','/absolute','usr/../outside','usr//share','usr\\share','usr/line\nbreak'):
            with self.assertRaises(ValueError):core.relative(name)

    @unittest.skipUnless(os.name=='posix' and shutil.which('sh'),'Shell CLI host test needs POSIX sh')
    def test_real_cli_verifies_files_and_detects_tampering_without_writes(self):
        self.install()
        # Linux host adapter for NetBSD's documented sha256 -q interface.
        # This tests shell control flow; it is not a target execution claim.
        bindir=self.directory/'bin';bindir.mkdir()
        adapter=bindir/'sha256'
        adapter.write_text('#!/bin/sh\n[ "$1" = -q ] || exit 2\nshift\nsha256sum "$@" | cut -d " " -f 1\n')
        adapter.chmod(0o755)
        environment=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'])
        command=['sh',str(self.stage/'usr/sbin/daosctl'),'--root',str(self.stage)]
        before={p.relative_to(self.stage).as_posix():core.sha(p) for p in self.stage.rglob('*') if p.is_file()}
        result=subprocess.run(command+['verify'],env=environment,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('0 failures',result.stdout)
        info=subprocess.run(command+['info'],env=environment,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(info.stdout)['base']['version'],'11.1')
        sourceversion=subprocess.run(command+['sourceversion'],env=environment,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(sourceversion.stdout)['base']['name'],'NetBSD')
        profile=subprocess.run(command+['profile'],env=environment,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(profile.stdout),'qemu-aarch64')
        after={p.relative_to(self.stage).as_posix():core.sha(p) for p in self.stage.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.payload.write_text('tampered')
        result=subprocess.run(command+['verify'],env=environment,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('FAILED usr/share/example.txt',result.stderr)

    @unittest.skipUnless(os.name=='posix','Symlink rejection needs POSIX symlinks')
    def test_component_parent_symlink_is_rejected(self):
        actual=self.directory/'outside';actual.mkdir()
        (actual/'example.txt').write_bytes(self.payload.read_bytes())
        self.payload.unlink();self.payload.parent.rmdir()
        self.payload.parent.symlink_to(actual,target_is_directory=True)
        with self.assertRaises(ValueError):self.install()


if __name__=='__main__':unittest.main()

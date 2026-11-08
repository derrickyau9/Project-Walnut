"""Explicit SDK separation: no installed files or package tools are changed."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

IMAGE=Path(__file__).resolve().parents[1]/'system/freebsd/image'
sys.path.insert(0,str(IMAGE))
profile=importlib.import_module('runtime_profile')
collector=importlib.import_module('collect_runtime')


def entry(path,kind='file',**extra):
    return dict(path=path,type=kind,packages=['llvm19'],bytes=100 if kind=='file' else 0,**extra)


class RuntimeProfileTests(unittest.TestCase):
    def test_llvm_sdk_is_separate_from_shared_runtime(self):
        rows=[entry('usr/local/llvm19/lib/libLLVM.so.19.1'),entry('usr/local/llvm19/lib/libLLVMCore.a'),
              entry('usr/local/llvm19/bin/clang'),entry('usr/local/bin/clang19'),
              entry('usr/local/llvm19/include/llvm/IR/Module.h'),entry('usr/local/llvm19/lib/cmake/LLVM/LLVMConfig.cmake')]
        kept,omitted,stats=profile.curate(rows,'camera-runtime-v1')
        self.assertEqual(['usr/local/llvm19/lib/libLLVM.so.19.1'],[row['path'] for row in kept])
        self.assertEqual(5,len(omitted));self.assertEqual(500,stats['omitted_bytes'])

    def test_runtime_resources_and_legal_texts_win_over_sdk_patterns(self):
        paths=['usr/local/share/licenses/llvm19/LICENSE','usr/local/llvm19/share/doc/LICENSE.TXT',
               'usr/local/llvm19/share/doc/NOTICE','usr/local/llvm19/share/doc/COPYRIGHT',
               'usr/local/lib/qt5/qml/QtQuick/item.qml','usr/local/lib/qt5/plugins/imageformats/libqjpeg.so',
               'usr/local/lib/python3.12/os.py','usr/local/lib/python3.12/site-packages/module/data/header.h',
               'usr/local/share/fonts/dejavu/font.ttf','usr/local/share/qt5/resources/icudtl.dat',
               'usr/local/llvm19/lib/clang/19/include/opencl-c.h',
               'usr/local/llvm19/lib/clang/19/lib/freebsd/libclang_rt.builtins.a',
               'usr/local/lib/custom-resource/runtime.h']
        kept,omitted,_=profile.curate([entry(path) for path in paths],'camera-runtime-v1')
        self.assertEqual(set(paths),{row['path'] for row in kept});self.assertEqual([],omitted)

    def test_alias_to_removed_tool_is_removed_but_library_alias_survives(self):
        rows=[entry('usr/local/llvm19/bin/lldb-argdumper'),
              entry('usr/local/llvm19/lib/python3.12/site-packages/lldb/lldb-argdumper','link',link='../../../../bin/lldb-argdumper'),
              entry('usr/local/llvm19/lib/libLLVM.so.19.1'),
              entry('usr/local/lib/libLLVM-19.so','link',link='../llvm19/lib/libLLVM.so.19.1')]
        kept,omitted,_=profile.curate(rows,'camera-runtime-v1')
        self.assertIn('usr/local/lib/libLLVM-19.so',{row['path'] for row in kept})
        alias=next(row for row in omitted if '/site-packages/' in row['path'])
        self.assertEqual('sdk-alias-to-omitted-target',alias['reason'])

    def test_unrelated_commands_are_not_guessed_to_be_sdk_wrappers(self):
        row=entry('usr/local/bin/camera19');row['packages']=['camera-package']
        kept,omitted,_=profile.curate([row],'camera-runtime-v1')
        self.assertEqual(1,len(kept));self.assertEqual([],omitted)

    def test_full_profile_keeps_the_original_declared_contents(self):
        rows=[entry('usr/local/llvm19/bin/clang'),entry('usr/local/llvm19/lib/libCore.a')]
        kept,omitted,stats=profile.curate(rows,'full-package-v1')
        self.assertEqual(2,len(kept));self.assertEqual([],omitted);self.assertEqual(0,stats['omitted_bytes'])

    def test_curation_is_recheckable_from_kept_and_omitted_metadata(self):
        rows=[entry('usr/local/llvm19/include/header.h'),entry('usr/local/llvm19/lib/libLLVM.so.19')]
        kept,omitted,statistics=profile.curate(rows,'camera-runtime-v1')
        kept2,omitted2,statistics2=profile.curate(kept+omitted,'camera-runtime-v1')
        self.assertEqual(statistics,statistics2)
        self.assertEqual({row['path'] for row in kept},{row['path'] for row in kept2})
        self.assertEqual({row['path']:row['reason'] for row in omitted},{row['path']:row['reason'] for row in omitted2})

    def test_copyright_bearing_documentation_is_preserved_conservatively(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'guide.html';path.write_text('Manual body\nCopyright 2026 project\n')
            self.assertTrue(collector.documentation_legal_text(path))
            row=entry('usr/local/llvm19/share/doc/guide.html',legal_text_detected=True)
            self.assertEqual(1,len(profile.curate([row],'camera-runtime-v1')[0]))

    def test_reuse_hardlinks_only_after_installed_bytes_and_modes_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'installed';prior=root/'prior';target=root/'new'/'target'
            source.write_bytes(b'checked bytes');prior.write_bytes(b'checked bytes')
            source.chmod(0o644);prior.chmod(0o644)
            info=prior.stat();expected={'bytes':13,'mode':info.st_mode&0o777}
            value=collector.reuse_file(source,prior,target,expected)
            self.assertEqual(64,len(value));self.assertEqual(info.st_ino,target.stat().st_ino)
            self.assertEqual(info.st_mode,prior.stat().st_mode)
            self.assertEqual(info.st_mtime_ns,prior.stat().st_mtime_ns)
            with self.assertRaises(FileExistsError):collector.reuse_file(source,prior,target,expected)
            self.assertEqual(b'checked bytes',prior.read_bytes())

    def test_reuse_rejects_different_bytes_without_creating_new_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'installed';prior=root/'prior';target=root/'target'
            source.write_bytes(b'checked bytes');prior.write_bytes(b'changed bytes')
            source.chmod(0o644);prior.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'bytes differ'):
                collector.reuse_file(source,prior,target,{'bytes':13,'mode':prior.stat().st_mode&0o777})
            self.assertFalse(target.exists());self.assertEqual(b'changed bytes',prior.read_bytes())

    @unittest.skipUnless(os.name=='posix','POSIX permissions and symlink fixture')
    def test_reuse_root_requires_exact_profile_set_and_private_owned_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);payload=root/'DaOS-runtime-prior'/'payload';payload.mkdir(parents=True)
            payload.parent.chmod(0o700);payload.chmod(0o755)
            (payload/'file').write_bytes(b'data')
            rows=[entry('file')];output=root/'DaOS-runtime-new'
            self.assertEqual(payload,collector.reuse_payload_root(payload,rows,output))
            with self.assertRaisesRegex(ValueError,'disjoint'):
                collector.reuse_payload_root(payload,rows,payload/'DaOS-runtime-nested')
            with self.assertRaisesRegex(ValueError,'disjoint'):
                collector.reuse_payload_root(payload,rows,payload.parent/'DaOS-runtime-nested')
            self.assertFalse((payload/'DaOS-runtime-nested').exists())
            (payload/'foreign').write_text('not part of fresh package profile')
            with self.assertRaisesRegex(ValueError,'set differs'):
                collector.reuse_payload_root(payload,rows,output)
            (payload/'foreign').unlink();payload.chmod(0o777)
            with self.assertRaisesRegex(ValueError,'non-writable'):
                collector.reuse_payload_root(payload,rows,output)

    @unittest.skipUnless(os.name=='posix','POSIX symlink fixture')
    def test_reuse_rejects_redirected_input_and_keeps_foreign_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'installed';outside=root/'outside';prior=root/'prior'
            source.write_bytes(b'checked bytes');outside.write_bytes(b'checked bytes');prior.symlink_to(outside)
            with self.assertRaises(ValueError):
                collector.reuse_file(source,prior,root/'target',{'bytes':13,'mode':0o644})
            self.assertEqual(b'checked bytes',outside.read_bytes());self.assertFalse((root/'target').exists())


if __name__=='__main__':unittest.main()

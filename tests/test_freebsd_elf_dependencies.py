"""Synthetic ELF tests; no target executable is run."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'system/freebsd/image/elf_dependencies.py'
SPEC = importlib.util.spec_from_file_location('freebsd_image_elf_dependencies', SOURCE)
elf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(elf)


def fixture(path, needed=(), soname=None, rpath=None, runpath=None, machine=183, osabi=9, static=False):
    strings = bytearray(b'\0')
    tags = []
    for tag, values in ((1, needed), (14, [soname] if soname else []),
                        (15, [rpath] if rpath is not None else []),
                        (29, [runpath] if runpath is not None else [])):
        for value in values:
            tags.append((tag, len(strings)))
            strings.extend(value.encode() + b'\0')
    address = 0x400000
    string_offset = 512
    dynamic_offset = 176
    if static:
        tags = []
        phnum = 1
    else:
        tags += [(5, address + string_offset), (10, len(strings)), (0, 0)]
        phnum = 2
    size = string_offset + len(strings)
    ident = b'\x7fELF\x02\x01\x01' + bytes([osabi]) + b'\0' * 8
    header = struct.pack('<16sHHIQQQIHHHHHH', ident, 3, machine, 1, 0, 64, 0, 0, 64, 56, phnum, 0, 0, 0)
    load = struct.pack('<IIQQQQQQ', 1, 4, 0, address, 0, size, size, 4096)
    dynamic = struct.pack('<IIQQQQQQ', 2, 4, dynamic_offset, address + dynamic_offset, 0, len(tags) * 16, len(tags) * 16, 8)
    data = bytearray(size)
    data[:64] = header
    data[64:120] = load
    if not static:
        data[120:176] = dynamic
        for index, item in enumerate(tags):
            struct.pack_into('<qQ', data, dynamic_offset + index * 16, *item)
    data[string_offset:] = strings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def record(root, path, **kwargs):
    fixture(root / path, **kwargs)
    return {'path': path, 'type': 'file', 'sha256': hashlib.sha256((root / path).read_bytes()).hexdigest()}


class ElfParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_parse_dynamic_strings_and_metadata(self):
        path = self.root / 'sample'
        fixture(path, needed=['libc.so.7', 'libQt5Core.so.5'], soname='sample.so.1',
                rpath='/usr/local/lib', runpath='$ORIGIN/../lib:/usr/local/lib/qt5')
        self.assertEqual(elf.parse_elf(path), {
            'machine': 183, 'osabi': 9, 'needed': ['libc.so.7', 'libQt5Core.so.5'],
            'soname': 'sample.so.1', 'rpath': ['/usr/local/lib'],
            'runpath': ['$ORIGIN/../lib', '/usr/local/lib/qt5']})

    def test_nonelf_and_static(self):
        path = self.root / 'text'
        path.write_text('not an executable')
        self.assertIsNone(elf.parse_elf(path))
        fixture(path, static=True)
        self.assertEqual(elf.parse_elf(path)['needed'], [])

    def test_invalid_bounds_class_and_endian(self):
        path = self.root / 'bad'
        for position, value in ((4, 1), (5, 2)):
            data = fixture(path)
            data[position] = value
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                elf.parse_elf(path)
        data = fixture(path)
        struct.pack_into('<Q', data, 32, len(data) + 100)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'extent'):
            elf.parse_elf(path)

    def test_invalid_header_diagnostic_identifies_exact_file(self):
        path = self.root / 'resources' / 'unsupported-elf'
        data = fixture(path)
        data[4] = 1
        path.write_bytes(data)
        with self.assertRaises(ValueError) as caught:
            elf.parse_elf(path)
        self.assertIn(repr(str(path)), str(caught.exception))
        self.assertIn('class=1, data=1, ident_version=1', str(caught.exception))
        self.assertIn('header_size=64', str(caught.exception))

    def test_other_parser_errors_also_identify_file(self):
        path = self.root / 'truncated'
        path.write_bytes(b'\x7fELF')
        with self.assertRaises(ValueError) as caught:
            elf.parse_elf(path)
        self.assertIn(repr(str(path)), str(caught.exception))
        self.assertIn('size', str(caught.exception))

    def test_excessive_headers_and_unterminated_dynamic(self):
        path = self.root / 'bad'
        data = fixture(path)
        struct.pack_into('<H', data, 56, 65535)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'header'):
            elf.parse_elf(path)
        data = fixture(path)
        struct.pack_into('<qQ', data, 176 + 32, 123, 0)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'terminator'):
            elf.parse_elf(path)

    def test_bad_string_extent_and_duplicate_tags(self):
        path = self.root / 'bad'
        data = fixture(path, needed=['libc.so.7'])
        struct.pack_into('<Q', data, 176 + 16 + 8, 0xFFFFFFFF)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'string table'):
            elf.parse_elf(path)
        data = fixture(path)
        struct.pack_into('<qQ', data, 176 + 16, 5, 0x400200)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            elf.parse_elf(path)

    def test_closure_runpath_alias_and_base_transitive(self):
        app = record(self.root, 'usr/local/bin/app', needed=['libthing.so.1'], runpath='$ORIGIN/../lib/private')
        lib = record(self.root, 'usr/local/lib/private/libthing.so.1.2', needed=['libc.so.7'])
        alias = {'path': 'usr/local/lib/private/libthing.so.1', 'type': 'link', 'link': 'libthing.so.1.2'}
        (self.root / alias['path']).symlink_to(alias['link'])
        base = [{'path': 'lib/libc.so.7', 'type': 'file', 'sha256': 'a' * 64,
                 'machine': 183, 'osabi': 9, 'needed': ['libm.so.5'], 'soname': 'libc.so.7'},
                {'path': 'lib/libm.so.5', 'type': 'file', 'sha256': 'b' * 64,
                 'machine': 183, 'osabi': 0, 'needed': [], 'soname': 'libm.so.5'}]
        result = elf.verify_closure(self.root, [app, lib, alias], base)
        self.assertTrue(result['passed'])
        self.assertEqual(result['dependency_count'], 3)
        self.assertEqual(len(result['base_files_used']), 2)

    def test_no_arbitrary_basename_and_explicit_ldconfig(self):
        app = record(self.root, 'usr/local/bin/app', needed=['libelse.so.1'])
        lib = record(self.root, 'usr/local/llvm19/lib/libelse.so.1')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            elf.verify_closure(self.root, [app, lib], [])
        result = elf.verify_closure(self.root, [app, lib], [], extra_search_paths=['/usr/local/llvm19/lib'])
        self.assertEqual(result['extra_search_paths'], ['/usr/local/llvm19/lib'])

    def test_wrong_machine_hash_change_and_relative_search(self):
        app = record(self.root, 'usr/local/bin/app', machine=62)
        with self.assertRaisesRegex(ValueError, 'machine'):
            elf.verify_closure(self.root, [app], [])
        app = record(self.root, 'usr/local/bin/app')
        (self.root / app['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            elf.verify_closure(self.root, [app], [])
        app = record(self.root, 'usr/local/bin/app', needed=['libx.so'], runpath='relative')
        with self.assertRaisesRegex(ValueError, 'absolute'):
            elf.verify_closure(self.root, [app], [])

    def test_symlink_cycles_and_escape(self):
        app = record(self.root, 'usr/local/bin/app', needed=['libx.so'])
        a = {'path': 'usr/local/lib/libx.so', 'type': 'link', 'link': 'liby.so'}
        b = {'path': 'usr/local/lib/liby.so', 'type': 'link', 'link': 'libx.so'}
        (self.root / a['path']).parent.mkdir(parents=True, exist_ok=True)
        (self.root / a['path']).symlink_to(a['link'])
        (self.root / b['path']).symlink_to(b['link'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            elf.verify_closure(self.root, [app, a, b], [])
        a['link'] = '../../../../escape'
        (self.root / a['path']).unlink()
        (self.root / a['path']).symlink_to(a['link'])
        with self.assertRaisesRegex(ValueError, 'escapes'):
            elf.verify_closure(self.root, [app, a], [])

    def test_runtime_alias_must_exist_and_match(self):
        app = record(self.root, 'usr/local/bin/app', needed=['libx.so'])
        lib = record(self.root, 'usr/local/lib/libx.so.1')
        alias = {'path': 'usr/local/lib/libx.so', 'type': 'link', 'link': 'libx.so.1'}
        with self.assertRaisesRegex(ValueError, 'symlink'):
            elf.verify_closure(self.root, [app, lib, alias], [])
        (self.root / alias['path']).symlink_to('different')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            elf.verify_closure(self.root, [app, lib, alias], [])

    def test_drive_path_and_unknown_origin_token_refused(self):
        for value in ('C:/outside', 'C:relative', 'z:relative'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    elf.verify_closure(self.root, [{'path': value, 'type': 'file'}], [])
                self.assertIn('relative ELF path: ' + repr(value), str(caught.exception))
        app = record(self.root, 'usr/local/bin/app', needed=['libx.so'], runpath='$ORIGINfake')
        with self.assertRaisesRegex(ValueError, 'absolute'):
            elf.verify_closure(self.root, [app], [])

    def test_invalid_manifest_path_diagnostic_preserves_exact_value(self):
        for value in ('./', '../outside', 'line\nbreak', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    elf._relative(value)
                self.assertIn(repr(value), str(caught.exception))

    def test_posix_colons_in_resource_components_are_valid_manifest_paths(self):
        for value in ('usr/local/lib/perl5/5.42/perl/man/man3/App::Cpan.3.gz',
                      'usr/local/share/resource:variant', 'usr/local/share/Test2::Tools.3.gz'):
            with self.subTest(value=value):
                self.assertEqual(elf._relative(value), value)

    @unittest.skipIf(os.name == 'nt', 'POSIX colon filenames cannot be represented as Windows filenames')
    def test_posix_colon_nonelf_resource_does_not_affect_loader_closure(self):
        app = record(self.root, 'usr/local/bin/app')
        name = 'usr/local/lib/perl5/5.42/perl/man/man3/App::Cpan.3.gz'
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b'non-ELF documentation resource')
        resource = {'path': name, 'type': 'file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        result = elf.verify_closure(self.root, [app, resource], [])
        self.assertTrue(result['passed'])
        self.assertEqual(result['runtime_elf_count'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows-only alternate-stream guard')
    def test_windows_does_not_open_posix_colon_filename_as_alternate_stream(self):
        item = {'path': 'usr/local/lib/App::Cpan.3.gz', 'type': 'file', 'sha256': 'a' * 64}
        with self.assertRaisesRegex(ValueError, 'POSIX verification filesystem'):
            elf.verify_closure(self.root, [item], [])

    def test_base_collection_only_explicit_prefixes(self):
        fixture(self.root / 'lib/libc.so.7')
        fixture(self.root / 'usr/lib/private/libm.so.5')
        fixture(self.root / 'libexec/ld-elf.so.1')
        fixture(self.root / 'usr/local/lib/foreign.so')
        (self.root / 'lib/notelf.so').write_text('linker script')
        records = elf.base_manifest(self.root)
        self.assertEqual([item['path'] for item in records], ['lib/libc.so.7', 'libexec/ld-elf.so.1', 'usr/lib/private/libm.so.5'])
        self.assertTrue(all(len(item['sha256']) == 64 for item in records))

    def test_base_separate_debug_tree_and_symbol_suffix_are_not_loader_inputs(self):
        fixture(self.root / 'lib/libc.so.7')
        (self.root / 'usr/lib').mkdir(parents=True)
        for name in ('usr/lib/debug/libexec/ld-elf32.so.1.debug',
                     'usr/lib/debug/usr/lib32/libc.so.7.debug',
                     'usr/lib/debug/misleading.so.1', 'usr/lib/libelse.so.1.debug'):
            path = self.root / name
            data = fixture(path, machine=40)
            data[4] = 1
            path.write_bytes(data)
        records = elf.base_manifest(self.root)
        self.assertEqual([item['path'] for item in records], ['lib/libc.so.7'])

    def test_genuine_wrong_class_base_library_still_rejected(self):
        fixture(self.root / 'lib/libc.so.7')
        path = self.root / 'usr/lib/libwrong.so.1'
        data = fixture(path, machine=40)
        data[4] = 1
        path.write_bytes(data)
        with self.assertRaises(ValueError) as caught:
            elf.base_manifest(self.root)
        self.assertIn(repr(str(path)), str(caught.exception))
        self.assertIn('class=1', str(caught.exception))

    def test_runtime_debug_named_elf_does_not_get_base_exemption(self):
        path = 'usr/local/lib/debug/unsupported.so.1.debug'
        item = record(self.root, path, machine=40)
        data = bytearray((self.root / path).read_bytes())
        data[4] = 1
        (self.root / path).write_bytes(data)
        item['sha256'] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, 'class=1'):
            elf.verify_closure(self.root, [item], [])


if __name__ == '__main__':
    unittest.main()

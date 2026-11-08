import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from camera.dng.hf_dng import write_dng
from camera.dng.inspect import inspect_dng

RID = '00000000-0000-0000-0000-000000000001'


class DngInspectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'capture.dng'
        write_dng(self.path, 4, 4, [64, 100, 200, 300] * 4,
                  {'request_id': RID, 'actual_exposure_us': 10000})

    def entries(self):
        raw = self.path.read_bytes()
        count = struct.unpack_from('<H', raw, 8)[0]
        return {struct.unpack_from('<H', raw, 10 + 12 * n)[0]: 10 + 12 * n
                for n in range(count)}

    def mutate_entry(self, tag, *, kind=None, count=None, value=None):
        raw = bytearray(self.path.read_bytes())
        offset = self.entries()[tag]
        if kind is not None:
            struct.pack_into('<H', raw, offset + 2, kind)
        if count is not None:
            struct.pack_into('<I', raw, offset + 4, count)
        if value is not None:
            struct.pack_into('<I', raw, offset + 8, value)
        self.path.write_bytes(raw)

    def test_writer_round_trip_allowlisted_and_does_not_change_file(self):
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        result = inspect_dng(self.path)
        self.assertEqual({
            'width': 4, 'height': 4, 'bits_per_sample': 16,
            'samples_per_pixel': 1, 'compression': 1, 'cfa': 'RGGB',
            'black_level': 64, 'white_level': 16383,
            'request_id': RID, 'metadata_state': 'valid',
        }, {key: value for key, value in result.items()
            if key not in {'strip_offsets', 'strip_byte_counts'}})
        self.assertEqual([32], result['strip_byte_counts'])
        self.assertEqual(self.path.stat().st_size - 32, result['strip_offsets'][0])
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_caller_owned_pinned_stream_stays_open_on_success_and_failure(self):
        with self.path.open('rb') as source:
            result = inspect_dng(source)
            self.assertEqual(result['request_id'], RID)
            self.assertFalse(source.closed)
            source.seek(0)
            self.assertEqual(source.read(4), b'II*\x00')
        self.path.write_bytes(b'not a retained DNG')
        with self.path.open('rb') as source:
            with self.assertRaises(ValueError):
                inspect_dng(source)
            self.assertFalse(source.closed)

    def test_no_pixel_reads(self):
        strip_start = inspect_dng(self.path)['strip_offsets'][0]
        original = Path.open
        class Guard:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def __getattr__(self, name): return getattr(self.stream, name)
            def read(self, count=-1):
                if count < 0 or self.stream.tell() + count > strip_start:
                    raise AssertionError('inspector attempted to read pixels')
                return self.stream.read(count)
        with patch.object(Path, 'open', autospec=True,
                          side_effect=lambda path, *args, **kwargs: Guard(original(path, *args, **kwargs))):
            self.assertEqual(4, inspect_dng(self.path)['width'])

    def test_strip_overlapping_ifd_metadata_or_beyond_file_is_rejected(self):
        initial = self.path.read_bytes()
        description_entry = self.entries()[270]
        metadata_offset = struct.unpack_from('<I', initial, description_entry + 8)[0]
        for offset in (8, metadata_offset, len(initial) - 2, 0xffffffff):
            with self.subTest(offset=offset):
                self.path.write_bytes(initial)
                self.mutate_entry(273, value=offset)
                with self.assertRaises(ValueError): inspect_dng(self.path)

    def test_truncated_tables_values_pixels_and_invalid_ifd_are_rejected(self):
        initial = self.path.read_bytes()
        for raw in (initial[:7], initial[:11], initial[:-1],
                    initial[:4] + struct.pack('<I', len(initial) - 1) + initial[8:]):
            with self.subTest(length=len(raw)):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError): inspect_dng(self.path)

    def test_unsupported_compression_cfa_and_oversized_metadata_are_rejected(self):
        initial = self.path.read_bytes()
        changes = [(259, {'value': 7}), (33422, {'value': 0}),
                   (270, {'count': 0xffffffff}), (50706, {'value': 2})]
        for tag, values in changes:
            with self.subTest(tag=tag):
                self.path.write_bytes(initial)
                self.mutate_entry(tag, **values)
                with self.assertRaises(ValueError): inspect_dng(self.path)

    def test_cfa_plane_mapping_and_layout_must_describe_rectangular_rggb(self):
        original = self.path.read_bytes()
        for tag, changes in ((50710, {'value': 0x00000102}),
                             (50710, {'value': 0x00010000}),
                             (50710, {'count': 2}),
                             (33422, {'value': 0x03010100}),
                             (50711, {'value': 2}),
                             (50711, {'value': 7}),
                             (50711, {'kind': 1})):
            with self.subTest(tag=tag, changes=changes):
                self.path.write_bytes(original)
                self.mutate_entry(tag, **changes)
                with self.assertRaises(ValueError):
                    inspect_dng(self.path)
        # Reorder both the planes and their pattern indices. The actual color
        # mosaic is still RGGB, so this valid representation remains supported.
        self.path.write_bytes(original)
        self.mutate_entry(50710, value=0x00000102)  # B, G, R planes
        self.mutate_entry(33422, value=0x00010102)  # plane indices 2, 1, 1, 0
        self.assertEqual(inspect_dng(self.path)['cfa'], 'RGGB')
        # DNG's omitted plane/layout tags default to RGB and rectangular.
        raw = bytearray(original)
        for tag in (50710, 50711):
            struct.pack_into('<H', raw, self.entries()[tag], tag + 10000)
        self.path.write_bytes(raw)
        self.assertEqual(inspect_dng(self.path)['cfa'], 'RGGB')

    def test_overflowing_json_float_invalidates_capture_identity(self):
        write_dng(self.path, 4, 4, [64] * 16,
                  {'request_id': RID, 'actual_analogue_gain': 1e308})
        original = self.path.read_bytes()
        self.assertEqual(original.count(b'1e+308'), 1)
        self.assertEqual(inspect_dng(self.path)['metadata_state'], 'valid')
        for overflow in (b'1e+999', b'-1e999'):
            with self.subTest(overflow=overflow):
                self.path.write_bytes(original.replace(b'1e+308', overflow))
                result = inspect_dng(self.path)
                self.assertEqual(result['metadata_state'], 'invalid')
                self.assertIsNone(result['request_id'])

    def test_linearization_table_is_not_mistaken_for_linear_bayer_samples(self):
        raw = bytearray(self.path.read_bytes())
        count = struct.unpack_from('<H', raw, 8)[0]
        entries = [bytes(raw[10 + 12 * n:22 + 12 * n]) for n in range(count)]
        index = next(n for n, entry in enumerate(entries) if struct.unpack_from('<H', entry)[0] == 254)
        entries[index] = struct.pack('<HHII', 50712, 3, 16384, len(raw))
        entries.sort(key=lambda entry: struct.unpack_from('<H', entry)[0])
        raw[10:10 + count * 12] = b''.join(entries)
        raw.extend(struct.pack('<16384H', *(min(2 * value, 16383) for value in range(16384))))
        self.path.write_bytes(raw)
        if importlib.util.find_spec('tifffile'):
            import tifffile
            with tifffile.TiffFile(self.path) as image:
                self.assertEqual(int(image.pages[0].asarray().reshape(-1)[1]), 100)
                self.assertEqual(image.pages[0].tags['LinearizationTable'].value[100], 200)
        with self.assertRaises(ValueError):
            inspect_dng(self.path)

    def test_cyclic_exif_pointer_and_duplicate_tags_are_rejected(self):
        initial = self.path.read_bytes()
        self.mutate_entry(34665, value=8)
        with self.assertRaises(ValueError): inspect_dng(self.path)
        self.path.write_bytes(initial)
        raw = bytearray(initial)
        struct.pack_into('<H', raw, self.entries()[257], 256)
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError): inspect_dng(self.path)

    def test_invalid_or_missing_metadata_never_returns_raw_description(self):
        for metadata in ({'request_id': 'secret-token-do-not-echo'}, None):
            write_dng(self.path, 2, 2, [64] * 4, metadata)
            result = inspect_dng(self.path)
            self.assertIsNone(result['request_id'])
            self.assertEqual('invalid' if metadata else 'valid', result['metadata_state'])
            self.assertNotIn('secret-token', json.dumps(result))
        # Unknown ImageDescription tag means an absent capture envelope.
        raw = bytearray(self.path.read_bytes())
        struct.pack_into('<H', raw, self.entries()[270], 65000)
        self.path.write_bytes(raw)
        self.assertEqual('missing', inspect_dng(self.path)['metadata_state'])

    def test_big_endian_minimal_fixture_and_default_levels(self):
        fields = [(256, 4, 1, struct.pack('>I', 2)),
                  (257, 4, 1, struct.pack('>I', 2)),
                  (258, 3, 1, struct.pack('>H', 16)),
                  (259, 3, 1, struct.pack('>H', 1)),
                  (262, 3, 1, struct.pack('>H', 32803)),
                  (273, 4, 1, b''),
                  (277, 3, 1, struct.pack('>H', 1)),
                  (278, 4, 1, struct.pack('>I', 2)),
                  (279, 4, 1, struct.pack('>I', 8)),
                  (33421, 3, 2, struct.pack('>HH', 2, 2)),
                  (33422, 1, 4, bytes((0, 1, 1, 2))),
                  (50706, 1, 4, bytes((1, 4, 0, 0)))]
        pixel_start = 8 + 2 + 12 * len(fields) + 4
        raw = bytearray(b'MM' + struct.pack('>HIH', 42, 8, len(fields)))
        for tag, kind, count, value in fields:
            if tag == 273: value = struct.pack('>I', pixel_start)
            raw.extend(struct.pack('>HHI', tag, kind, count) + value.ljust(4, b'\0'))
        raw.extend(struct.pack('>I4H', 0, 64, 100, 200, 300))
        self.path.write_bytes(raw)
        result = inspect_dng(self.path)
        self.assertEqual((2, 2, 0, 65535, 'missing'),
                         tuple(result[key] for key in ('width', 'height', 'black_level',
                                                       'white_level', 'metadata_state')))

    def test_multiple_strips_must_match_rows_and_not_overlap(self):
        original = self.path.read_bytes()
        entries = self.entries()
        pixel_start = struct.unpack_from('<I', original, entries[273] + 8)[0]
        raw = bytearray(original)
        arrays_start = len(raw)
        raw.extend(struct.pack('<4I', pixel_start, pixel_start + 16, 16, 16))
        struct.pack_into('<II', raw, entries[273] + 4, 2, arrays_start)
        struct.pack_into('<II', raw, entries[279] + 4, 2, arrays_start + 8)
        struct.pack_into('<I', raw, entries[278] + 8, 2)
        self.path.write_bytes(raw)
        result = inspect_dng(self.path)
        self.assertEqual([pixel_start, pixel_start + 16], result['strip_offsets'])
        self.assertEqual([16, 16], result['strip_byte_counts'])
        struct.pack_into('<I', raw, arrays_start + 4, pixel_start + 8)
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError): inspect_dng(self.path)
        struct.pack_into('<I', raw, arrays_start + 4, pixel_start + 16)
        struct.pack_into('<I', raw, arrays_start + 12, 15)
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError): inspect_dng(self.path)

    def test_nonexistent_file_raises_oserror(self):
        with self.assertRaises(OSError): inspect_dng(self.path.with_name('missing.dng'))


if __name__ == '__main__': unittest.main()

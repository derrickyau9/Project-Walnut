import importlib.util
import io
import ctypes
import ctypes.util
from array import array
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from camera.dng.hf_dng import write_dng
from simulation.bayer import generate_bayer


class DngTests(unittest.TestCase):
    def test_caller_owned_binary_stream_is_not_closed(self):
        output = io.BytesIO()
        pixels = generate_bayer(9, 7)
        self.assertIs(write_dng(output, 9, 7, pixels), output)
        self.assertFalse(output.closed)
        data = output.getvalue()
        self.assertTrue(data.startswith(b'II*\x00'))
        self.assertEqual(struct.unpack_from('<63H', data, len(data) - 126), tuple(pixels))
        untouched = io.BytesIO()
        with self.assertRaises(ValueError):
            write_dng(untouched, 9, 7, [0])
        self.assertFalse(untouched.closed)
        self.assertEqual(untouched.getvalue(), b'')

    def test_deterministic_exposure_and_clipping(self):
        a = generate_bayer(32, 24, 10000, 1)
        self.assertEqual(a, generate_bayer(32, 24, 10000, 1))
        b = generate_bayer(32, 24, 20000, 1)
        self.assertTrue(all(x <= y for x, y in zip(a, b)))
        self.assertTrue(any(x < y for x, y in zip(a, b)))
        self.assertEqual(max(generate_bayer(8, 8, 10000000, 1)), 16383)

    def test_synthetic_dark_and_flat(self):
        self.assertEqual(set(generate_bayer(8, 8, scene='dark')), {64})
        flat = generate_bayer(8, 8, scene='flat')
        self.assertEqual(flat[0], flat[2])
        self.assertEqual(flat[0], flat[16])
        self.assertNotEqual(flat[0], flat[1])
        self.assertGreater(generate_bayer(8, 8, 20000, scene='flat')[0], flat[0])
        with self.assertRaises(ValueError):
            generate_bayer(8, 8, scene='unknown')

    def test_invalid_input(self):
        for width, height, exposure, gain in [(0, 2, 1, 1), (2, 2, 0, 1), (2, 2, 1, float('nan'))]:
            with self.assertRaises(ValueError):
                generate_bayer(width, height, exposure, gain)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'invalid.dng'
            with self.assertRaises(ValueError):
                write_dng(path, 2, 2, [0, 1])
            with self.assertRaises(ValueError):
                write_dng(path, 2, 2, [0, 1, 2, 16384])
            for metadata in ({'actual_exposure_us': 0}, {'actual_analogue_gain': float('nan')},
                             {'token': 'must-not-be-persisted'}, {'make': 'with\0nul'}):
                with self.assertRaises(ValueError):
                    write_dng(path, 2, 2, [0, 1, 2, 3], metadata)
            self.assertFalse(path.exists())

    def test_exif_actual_exposure_and_namespaced_capture_metadata(self):
        meta = {'requested_exposure_us': 10000, 'actual_exposure_us': 12500,
                'requested_analogue_gain': 1.0, 'actual_analogue_gain': 1.25,
                'request_id': 'request-1', 'capture_id': 'capture-1', 'frame_id': 'frame-1',
                'sensor_id': 'HF-Sim', 'calibration_id': 'synthetic-uncalibrated'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frame.dng'
            write_dng(path, 2, 2, [64, 100, 200, 300], meta)
            contents = path.read_bytes()
            count = struct.unpack_from('<H', contents, 8)[0]
            entries = {}
            for i in range(count):
                tag, typ, size, value = struct.unpack_from('<HHII', contents, 10 + 12 * i)
                entries[tag] = (typ, size, value)
            _, desc_len, desc_offset = entries[270]
            description = json.loads(contents[desc_offset:desc_offset + desc_len - 1])
            self.assertEqual(description['namespace'], 'org.hasslefree.hf-sim.capture.v1')
            self.assertTrue(description['simulated'])
            self.assertEqual(description['capture'], meta)
            exif_offset = entries[34665][2]
            self.assertEqual(struct.unpack_from('<H', contents, exif_offset)[0], 1)
            tag, typ, size, rational_offset = struct.unpack_from('<HHII', contents, exif_offset + 2)
            self.assertEqual((tag, typ, size), (33434, 5, 1))
            self.assertEqual(struct.unpack_from('<II', contents, rational_offset), (1, 80))

    @unittest.skipUnless(os.environ.get('HF_FULL_FRAME_TEST') == '1', 'set HF_FULL_FRAME_TEST=1 for 49 MB simulated full-frame I/O test')
    def test_full_frame_dimensions_and_sample_edges(self):
        width, height = 6064, 4040
        pixels = array('H', [64]) * (width * height)
        pixels[0], pixels[-1] = 0, 16383
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'full.dng'
            write_dng(path, width, height, pixels, {'actual_exposure_us': 10000})
            with path.open('rb') as f:
                header = f.read(4096)
                count = struct.unpack_from('<H', header, 8)[0]
                entries = {struct.unpack_from('<H', header, 10 + i * 12)[0]:
                           struct.unpack_from('<HHII', header, 10 + i * 12) for i in range(count)}
                offset, byte_count = entries[273][3], entries[279][3]
                self.assertEqual((entries[256][3], entries[257][3]), (width, height))
                self.assertEqual(byte_count, width * height * 2)
                f.seek(offset)
                self.assertEqual(struct.unpack('<H', f.read(2))[0], 0)
                f.seek(offset + byte_count - 2)
                self.assertEqual(struct.unpack('<H', f.read(2))[0], 16383)
                self.assertEqual(f.tell(), path.stat().st_size)

    def test_stdlib_ifd_and_pixels(self):
        pixels = generate_bayer(9, 7)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frame.dng'
            write_dng(path, 9, 7, pixels)
            contents = path.read_bytes()
            self.assertEqual(contents[:8], b'II\x2a\x00\x08\x00\x00\x00')
            count = struct.unpack_from('<H', contents, 8)[0]
            entries = {}
            for i in range(count):
                tag, typ, size, value = struct.unpack_from('<HHII', contents, 10 + 12 * i)
                entries[tag] = (typ, size, value)
            self.assertEqual(entries[256][2], 9)
            self.assertEqual(entries[257][2], 7)
            self.assertEqual(entries[262][2] & 0xffff, 32803)
            self.assertEqual(entries[279][2], len(pixels) * 2)
            offset = entries[273][2]
            self.assertEqual(list(struct.unpack_from('<63H', contents, offset)), list(pixels))

    @unittest.skipUnless(importlib.util.find_spec('tifffile'), 'optional independent tifffile decoder unavailable')
    def test_tifffile_round_trip(self):
        import tifffile
        pixels = generate_bayer(32, 24, 8000, 1.25)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frame.dng'
            write_dng(path, 32, 24, pixels, {'actual_exposure_us': 12500})
            with tifffile.TiffFile(path) as image:
                page = image.pages[0]
                self.assertEqual(page.shape, (24, 32))
                self.assertEqual(list(page.asarray().flatten()), list(pixels))
                self.assertEqual(tuple(page.tags['DNGVersion'].value), (1, 4, 0, 0))
                self.assertEqual(page.tags['CFAPattern'].value, b'\x00\x01\x01\x02')
                self.assertEqual(page.tags['WhiteLevel'].value, 16383)
                self.assertEqual(page.tags['ExifTag'].value['ExposureTime'], (1, 80))

    @unittest.skipUnless(ctypes.util.find_library('tiff'), 'optional libtiff unavailable')
    def test_libtiff_round_trip(self):
        # libtiff interprets the uncompressed TIFF strip, not DNG color rendering.
        library = ctypes.CDLL(ctypes.util.find_library('tiff'))
        library.TIFFOpen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        library.TIFFOpen.restype = ctypes.c_void_p
        library.TIFFReadScanline.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint16]
        library.TIFFReadScanline.restype = ctypes.c_int
        library.TIFFClose.argtypes = [ctypes.c_void_p]
        pixels = generate_bayer(32, 24)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frame.dng'
            write_dng(path, 32, 24, pixels)
            handle = library.TIFFOpen(str(path).encode(), b'r')
            self.assertTrue(handle)
            try:
                decoded = []
                for row in range(24):
                    line = (ctypes.c_uint16 * 32)()
                    self.assertEqual(library.TIFFReadScanline(handle, line, row, 0), 1)
                    decoded.extend(line)
                self.assertEqual(decoded, list(pixels))
            finally:
                library.TIFFClose(handle)


if __name__ == '__main__':
    unittest.main()

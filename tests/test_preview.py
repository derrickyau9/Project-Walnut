import io
import struct
import unittest
import zlib

from camera.pipeline.preview import PreviewStream, PreviewUnavailable

SETTINGS = {'requested_exposure_us': 10000, 'requested_analogue_gain': 1.0, 'sensor_mode': 'sim-small'}


class PreviewTests(unittest.TestCase):
    def test_rate_limit_settings_and_mode_transition(self):
        tick = [1.0]
        p = PreviewStream(32, 24, clock=lambda: tick[0], wall_clock=lambda: 123.0)
        with self.assertRaises(PreviewUnavailable):
            p.snapshot(SETTINGS)
        p.start()
        first = p.snapshot(SETTINGS)
        self.assertEqual(p.snapshot(SETTINGS), first)
        tick[0] += .3
        second = p.snapshot(SETTINGS)
        self.assertGreater(second['frame_id'], first['frame_id'])
        self.assertNotEqual(second['png'], first['png'])
        third = p.snapshot({**SETTINGS, 'requested_exposure_us': 20000})
        self.assertGreater(third['frame_id'], second['frame_id'])
        with self.assertRaisesRegex(PreviewUnavailable, 'paused'):
            p.snapshot(SETTINGS, capturing=True)
        self.assertGreater(p.snapshot(SETTINGS)['frame_id'], third['frame_id'])
        p.stop()
        with self.assertRaises(PreviewUnavailable):
            p.snapshot(SETTINGS)

    def test_png_crc_dimensions_and_pixel_payload(self):
        p = PreviewStream(32, 24)
        p.start()
        png = p.snapshot(SETTINGS)['png']
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        pos, chunks = 8, {}
        while pos < len(png):
            count = struct.unpack_from('>I', png, pos)[0]
            kind, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + count]
            self.assertEqual(struct.unpack_from('>I', png, pos + 8 + count)[0], zlib.crc32(kind + data) & 0xffffffff)
            chunks[kind] = data
            pos += count + 12
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[b'IHDR']), (32, 24, 8, 2, 0, 0, 0))
        self.assertEqual(len(zlib.decompress(chunks[b'IDAT'])), 24 * (32 * 3 + 1))
        try:
            from PIL import Image
        except ImportError:
            return
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (32, 24))
            image.load()

    def test_validation_and_deterministic_scene(self):
        for kwargs in ({'width': 3}, {'fps': float('nan')}, {'width': 10000}):
            with self.assertRaises(ValueError):
                PreviewStream(**kwargs)
        a, b = PreviewStream(32, 24), PreviewStream(32, 24)
        a.start(); b.start()
        self.assertEqual(a.snapshot(SETTINGS)['png'], b.snapshot(SETTINGS)['png'])
        with self.assertRaises(ValueError):
            a.snapshot({**SETTINGS, 'requested_analogue_gain': float('nan')})


if __name__ == '__main__':
    unittest.main()

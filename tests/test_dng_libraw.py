"""Optional real RAW decoder test; installation is separate from runtime deps."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from camera.dng.hf_dng import write_dng
from simulation.bayer import generate_bayer


@unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional LibRaw/rawpy decoder unavailable')
class LibRawTests(unittest.TestCase):
    def test_raw_pixels_and_rendered_rgb(self):
        import numpy as np
        import rawpy
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.dng'
            pixels = generate_bayer(128, 96)
            write_dng(path, 128, 96, pixels, {'actual_exposure_us': 10000, 'sensor_id': 'HF-Sim'})
            with rawpy.imread(str(path)) as raw:
                self.assertEqual(raw.raw_image_visible.shape, (96, 128))
                self.assertTrue(np.array_equal(raw.raw_image_visible.reshape(-1), np.frombuffer(pixels, dtype=np.uint16)))
                self.assertEqual(raw.white_level, 16383)
                self.assertEqual(raw.black_level_per_channel, [64, 64, 64, 64])
                rendered = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
                self.assertEqual(rendered.shape, (96, 128, 3))
                self.assertGreater(int(rendered.max()), int(rendered.min()))


if __name__ == '__main__':
    unittest.main()

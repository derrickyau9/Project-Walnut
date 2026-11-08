"""Actual RTL FIFO transfer followed by a gated synthetic DNG round trip."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import uuid

from camera.dng.hf_dng import write_dng
from camera.dng.inspect import inspect_dng
from simulation.bayer import generate_bayer

ROOT = Path(__file__).resolve().parents[1]


class FpgaDngTests(unittest.TestCase):
    def run_trace(self, directory, corrupt=False):
        from fpga.testbench.rtl_sim import discover_tools, run_iverilog, ToolUnavailable
        try:
            discover_tools()
        except ToolUnavailable as error:
            self.skipTest(str(error))
        pixels = generate_bayer(32, 24, 12500, 1.5)
        source, trace = directory / 'pixels.hex', directory / 'trace.txt'
        source.write_text(''.join(f'{value:04x}\n' for value in pixels), encoding='ascii')
        result = run_iverilog(ROOT / 'fpga/testbench/tb_dng_roundtrip.sv', top='tb_dng_roundtrip',
                              plusargs=[f'+INPUT={source.as_posix()}', f'+OUTPUT={trace.as_posix()}', f'+CORRUPT={int(corrupt)}'],
                              output_dir=directory / 'simulation')
        self.assertIn('PASS DNG_STREAM', result['stdout'])
        lines = [line.split() for line in trace.read_text().splitlines()]
        beats, summary = lines[:-1], lines[-1]
        self.assertEqual(len(beats), len(pixels))
        for index, fields in enumerate(beats):
            self.assertEqual(fields[0], 'P')
            self.assertEqual(list(map(int, fields[1:])), [42, pixels[index], int(index == 0), int(index % 32 == 31),
                                                        int(index == 767), int(corrupt and index == 35)])
        checksum = 14695981039346656037
        for value in pixels:
            for byte in (value & 255, value >> 8):
                checksum = ((checksum ^ byte) * 1099511628211) & ((1 << 64) - 1)
        self.assertEqual(summary, ['S', str(int(not corrupt)), '42', '768', '24', str(128 if corrupt else 0), str(checksum)])
        return pixels, [int(fields[2]) for fields in beats], summary[1] == '1'

    def test_bayer_through_rtl_then_dng_matches_independent_decoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            expected, received, good = self.run_trace(directory)
            self.assertTrue(good)
            path = directory / 'accepted.dng'
            request_id = str(uuid.uuid4())
            write_dng(path, 32, 24, received, {'request_id': request_id, 'frame_id': 'rtl-42',
                      'sensor_id': 'HF-Sim-RTL', 'calibration_id': 'synthetic-uncalibrated',
                      'requested_exposure_us': 12500, 'actual_exposure_us': 12500,
                      'requested_analogue_gain': 1.5, 'actual_analogue_gain': 1.5})
            self.assertEqual(inspect_dng(path)['request_id'], request_id)
            if importlib.util.find_spec('tifffile'):
                import tifffile
                self.assertEqual(tifffile.imread(path).reshape(-1).tolist(), list(expected))
                with tifffile.TiffFile(path) as image:
                    import json
                    metadata = json.loads(image.pages[0].tags['ImageDescription'].value)
                    self.assertEqual(metadata['capture']['frame_id'], 'rtl-42')

    def test_corrupt_frame_completion_is_rejected_before_dng(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, received, good = self.run_trace(directory, corrupt=True)
            self.assertFalse(good)
            self.assertEqual(len(received), 768, 'provisional pixels do not constitute a good frame')
            self.assertFalse(list(directory.glob('*.dng')))


if __name__ == '__main__':
    unittest.main()

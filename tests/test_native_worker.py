"""CLI integration tests for the dependency-free C++17 synthetic worker.

HF_NATIVE_WORKER selects a previously built executable. Otherwise use the
project build/native output, or build in a temporary directory using CMake.
Independent TIFF/LibRaw readers are optional test dependencies, never runtime
dependencies of the worker.
"""

import importlib.util
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import tempfile
import threading
import unittest

from simulation.bayer import generate_bayer
from camera.dng.inspect import inspect_dng
from tools.build_native import find_cmake


ROOT = Path(__file__).resolve().parents[1]


def _tiff(path):
    """Small independent stdlib inspection, separate from both DNG writers."""
    contents = path.read_bytes()
    if contents[:8] != b'II\x2a\x00\x08\x00\x00\x00':
        raise AssertionError('not a classic little-endian TIFF')
    count = struct.unpack_from('<H', contents, 8)[0]
    entries = {}
    for index in range(count):
        tag, kind, length, value = struct.unpack_from('<HHII', contents, 10 + index * 12)
        entries[tag] = (kind, length, value)
    offset, byte_count = entries[273][2], entries[279][2]
    pixels = struct.unpack_from('<' + str(byte_count // 2) + 'H', contents, offset)
    _, description_length, description_offset = entries[270]
    description = json.loads(contents[description_offset:description_offset + description_length - 1])
    return contents, entries, pixels, description


class NativeWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._build_directory = None
        configured = os.environ.get('HF_NATIVE_WORKER')
        if configured:
            cls.worker = Path(configured).resolve()
            if not cls.worker.is_file():
                raise RuntimeError('HF_NATIVE_WORKER is not an executable file: ' + str(cls.worker))
            return
        filename = 'hf-native-worker.exe' if os.name == 'nt' else 'hf-native-worker'
        candidates = [ROOT / 'build/native/Release' / filename, ROOT / 'build/native' / filename]
        for candidate in candidates:
            if candidate.is_file():
                cls.worker = candidate
                return
        try:
            cmake = find_cmake()
        except RuntimeError as error:
            raise unittest.SkipTest(str(error) + '; set HF_NATIVE_WORKER to a built worker')
        cls._build_directory = tempfile.TemporaryDirectory(prefix='hf-native-tests-')
        build = Path(cls._build_directory.name)
        configure = [str(cmake), '-S', str(ROOT / 'camera/native'), '-B', str(build),
                     '-DHF_BUILD_WORKER=ON', '-DCMAKE_BUILD_TYPE=Release']
        if os.name == 'nt':
            configure += ['-G', 'Visual Studio 17 2022', '-A', 'x64']
        for command in (configure, [str(cmake), '--build', str(build), '--config', 'Release',
                                    '--target', 'hf-native-worker', '--parallel']):
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if completed.returncode:
                cls._build_directory.cleanup()
                raise RuntimeError('native worker build failed:\n' + completed.stdout + completed.stderr)
        cls.worker = next((path for path in (build / 'Release' / filename, build / filename) if path.is_file()), None)
        if cls.worker is None:
            cls._build_directory.cleanup()
            raise RuntimeError('native worker build produced no executable')

    @classmethod
    def tearDownClass(cls):
        if cls._build_directory is not None:
            cls._build_directory.cleanup()

    def command(self, output, **changes):
        options = {'output': str(output), 'width': '128', 'height': '96', 'exposure-us': '12500',
                   'gain': '1.25', 'scene': 'scene', 'request-id': 'request-native-1',
                   'sensor-id': 'HF-Sim', 'calibration-id': 'synthetic-uncalibrated'}
        options.update(changes)
        command = [str(self.worker)]
        for option, value in options.items():
            if value is not None:
                command += ['--' + option, str(value)]
        return command

    def capture(self, output, **changes):
        completed = subprocess.run(self.command(output, **changes), capture_output=True, text=True,
                                   encoding='utf-8', timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, '')
        self.assertLess(len(completed.stdout.encode('utf-8')), 4096)
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        metadata = json.loads(completed.stdout)
        self.assertTrue(output.is_file())
        self.assertEqual(output.stat().st_size, metadata['output_bytes'])
        self.assertFalse({'pixels', 'bayer', 'dng', 'image', 'data'} & metadata.keys())
        return metadata

    def test_metadata_traceability_and_exact_reference_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'authority-supplied.dng.partial'
            metadata = self.capture(output, **{'capture-id': 'capture-native-1'})
            contents, entries, pixels, description = _tiff(output)
            self.assertEqual(pixels, tuple(generate_bayer(128, 96, 12500, 1.25)))
            self.assertEqual(set(Path(temporary).iterdir()), {output})
            self.assertEqual((metadata['width'], metadata['height'], metadata['bit_depth']), (128, 96, 14))
            self.assertEqual(metadata['cfa'], 'RGGB')
            self.assertEqual(metadata['source'], 'synthetic-native')
            self.assertTrue(metadata['simulated'])
            self.assertEqual(metadata['schema_version'], 1)
            self.assertEqual(metadata['request_id'], 'request-native-1')
            self.assertEqual(metadata['frame_id'], metadata['request_id'])
            self.assertEqual(metadata['capture_id'], 'capture-native-1')
            self.assertEqual(metadata['requested_exposure_us'], 12500)
            self.assertEqual(metadata['actual_exposure_us'], 12500)
            self.assertEqual(metadata['requested_analogue_gain'], 1.25)
            self.assertEqual(metadata['actual_analogue_gain'], 1.25)
            self.assertGreater(metadata['captured_at_unix_ns'], 0)
            self.assertEqual(description['namespace'], 'org.hasslefree.hf-sim.capture.v1')
            self.assertTrue(description['simulated'])
            for key, value in description['capture'].items():
                self.assertEqual(metadata[key], value, key)
            self.assertEqual(description['native']['captured_at_unix_ns'], metadata['captured_at_unix_ns'])
            self.assertEqual(str(description['native']['frame_sequence']), metadata['native_frame_sequence'])
            checksum = 14695981039346656037
            for byte in contents[entries[273][2]:]:
                checksum = ((checksum ^ byte) * 1099511628211) & 0xffffffffffffffff
            self.assertEqual(str(checksum), metadata['pixel_checksum'])
            self.assertEqual(entries[273][2] + entries[279][2], len(contents))

    @unittest.skipUnless(importlib.util.find_spec('tifffile'), 'optional independent tifffile decoder unavailable')
    def test_tifffile_cfa_crop_color_and_exif(self):
        import tifffile
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'frame.dng'
            self.capture(output)
            with tifffile.TiffFile(output) as image:
                page = image.pages[0]
                self.assertEqual(page.shape, (96, 128))
                self.assertEqual(page.asarray().reshape(-1).tolist(), list(generate_bayer(128, 96, 12500, 1.25)))
                self.assertEqual(page.bitspersample, 16)
                self.assertEqual(page.photometric, 32803)
                self.assertEqual(tuple(page.tags['DNGVersion'].value), (1, 4, 0, 0))
                self.assertEqual(tuple(page.tags['CFARepeatPatternDim'].value), (2, 2))
                self.assertEqual(page.tags['CFAPattern'].value, b'\x00\x01\x01\x02')
                self.assertEqual(page.tags['BlackLevel'].value, 64)
                self.assertEqual(page.tags['WhiteLevel'].value, 16383)
                self.assertEqual(tuple(page.tags['ActiveArea'].value), (0, 0, 96, 128))
                self.assertEqual(tuple(page.tags['DefaultCropOrigin'].value), (0, 0))
                self.assertEqual(tuple(page.tags['DefaultCropSize'].value), (128, 96))
                self.assertEqual(tuple(page.tags['AsShotNeutral'].value), (1, 1, 1, 1, 1, 1))
                self.assertEqual(tuple(page.tags['ColorMatrix1'].value), (1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1))
                self.assertEqual(page.tags['ExifTag'].value['ExposureTime'], (1, 80))

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional independent LibRaw/rawpy decoder unavailable')
    def test_libraw_raw_pixels_and_rendered_rgb(self):
        import numpy as np
        import rawpy
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'frame.dng'
            self.capture(output)
            with rawpy.imread(str(output)) as raw:
                self.assertEqual(raw.raw_image_visible.shape, (96, 128))
                self.assertTrue(np.array_equal(raw.raw_image_visible.reshape(-1),
                                               np.frombuffer(generate_bayer(128, 96, 12500, 1.25), dtype=np.uint16)))
                self.assertEqual(raw.white_level, 16383)
                self.assertEqual(raw.black_level_per_channel, [64, 64, 64, 64])
                # LibRaw releases may represent the second green as either
                # channel 1 or channel 3. Assert the actual color mosaic.
                self.assertEqual(bytes(raw.color_desc[int(index)] for index in raw.raw_pattern.reshape(-1)), b'RGGB')
                rendered = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
                self.assertEqual(rendered.shape, (96, 128, 3))
                self.assertGreater(int(rendered.max()), int(rendered.min()))

    @unittest.skipUnless(importlib.util.find_spec('rawpy'), 'optional independent LibRaw/rawpy decoder unavailable')
    def test_libraw_confirms_plane_permutations_and_unsupported_layouts(self):
        import numpy as np
        import rawpy
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'native.dng'
            self.capture(output)
            original = output.read_bytes()
            count = struct.unpack_from('<H', original, 8)[0]
            entries = {struct.unpack_from('<H', original, 10 + 12 * n)[0]: 10 + 12 * n for n in range(count)}
            with rawpy.imread(str(output)) as image:
                reference = image.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
            for name, changes, expected_pattern, accepted in (
                    ('swapped-planes', ((50710, 0x00000102),), b'BGGR', False),
                    ('equivalent-rggb', ((50710, 0x00000102), (33422, 0x00010102)), b'RGGB', True),
                    ('staggered-layout', ((50711, 2),), None, False)):
                with self.subTest(name=name):
                    changed = bytearray(original)
                    for tag, value in changes:
                        struct.pack_into('<I', changed, entries[tag] + 8, value)
                    path = Path(temporary) / (name + '.dng')
                    path.write_bytes(changed)
                    with rawpy.imread(str(path)) as image:
                        colors = bytes(image.color_desc[int(index)] for index in image.raw_pattern.reshape(-1))
                        rendered = image.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
                        if expected_pattern is not None:
                            self.assertEqual(colors, expected_pattern)
                        if accepted:
                            self.assertTrue(np.array_equal(rendered, reference))
                            self.assertEqual(inspect_dng(path)['cfa'], 'RGGB')
                        else:
                            self.assertFalse(np.array_equal(rendered, reference))
                            with self.assertRaises(ValueError):
                                inspect_dng(path)

    def test_deterministic_scenes_gain_and_clipping(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index, (scene, exposure, gain) in enumerate((('dark', 10000, 1), ('flat', 8000, 1.5),
                                                           ('scene', 10000, 1), ('scene', 20000, 2),
                                                           ('flat', 4294967295, 16))):
                with self.subTest(scene=scene, exposure=exposure, gain=gain):
                    output = Path(temporary) / (str(index) + '.dng.partial')
                    self.capture(output, width=9, height=7, scene=scene,
                                 **{'exposure-us': exposure, 'gain': gain})
                    self.assertEqual(_tiff(output)[2], tuple(generate_bayer(9, 7, exposure, gain, scene)))

    def test_invalid_cli_produces_no_success_or_output(self):
        cases = [{'width': 1}, {'height': -1}, {'width': 4294967296}, {'width': '2junk'},
                 {'width': 4294967295, 'height': 4294967295}, {'exposure-us': 0},
                 {'exposure-us': -1}, {'gain': 'nan'}, {'gain': 'inf'}, {'gain': 0},
                 {'gain': '1e309'}, {'gain': ' 1'}, {'scene': 'hardware'}, {'request-id': ''},
                 {'sensor-id': ''}, {'calibration-id': 'x' * 257}, {'height': None}]
        with tempfile.TemporaryDirectory() as temporary:
            for index, changes in enumerate(cases):
                with self.subTest(changes=changes):
                    output = Path(temporary) / (str(index) + '.dng.partial')
                    completed = subprocess.run(self.command(output, **changes), capture_output=True, timeout=30)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, b'')
                    self.assertTrue(completed.stderr)
                    self.assertFalse(output.exists())
            for suffix in (['--unknown', 'x'], ['--gain', '2'], ['--capture-id']):
                output = Path(temporary) / 'options.dng.partial'
                completed = subprocess.run(self.command(output) + suffix, capture_output=True, timeout=30)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, b'')
                self.assertFalse(output.exists())

    def test_output_errors_do_not_report_success_or_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / 'existing.dng.partial'
            existing.write_bytes(b'preserve existing authority file')
            for output in (existing, Path(temporary) / 'missing-parent' / 'new.dng.partial', Path(temporary)):
                with self.subTest(output=output):
                    completed = subprocess.run(self.command(output), capture_output=True, timeout=30)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, b'')
                    self.assertTrue(completed.stderr)
            self.assertEqual(existing.read_bytes(), b'preserve existing authority file')
            self.assertEqual(set(Path(temporary).iterdir()), {existing})

    def test_unicode_path_and_json_identifier_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'synthetic-\u76f8\u673a-\U0001f4f7.dng.partial'
            sensor = 'HF-Sim-\u76f8\u673a-\U0001f4f7'
            calibration = 'synthetic-\u00e9-"quoted"-\\-\n'
            metadata = self.capture(output, **{'sensor-id': sensor, 'calibration-id': calibration})
            description = _tiff(output)[3]
            self.assertEqual(metadata['sensor_id'], sensor)
            self.assertEqual(metadata['calibration_id'], calibration)
            self.assertEqual(description['capture']['sensor_id'], sensor)
            self.assertEqual(description['capture']['calibration_id'], calibration)

    def test_success_line_observes_complete_file_before_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'streamed.dng.partial'
            process = subprocess.Popen(self.command(output), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
            reader.start()
            try:
                line = lines.get(timeout=30)
                metadata = json.loads(line)
                self.assertTrue(output.is_file())
                self.assertEqual(output.stat().st_size, metadata['output_bytes'])
                contents, entries, _, _ = _tiff(output)
                self.assertEqual(entries[273][2] + entries[279][2], len(contents))
                self.assertEqual(process.wait(timeout=30), 0)
                self.assertEqual(process.stdout.read(), b'')
                self.assertEqual(process.stderr.read(), b'')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                reader.join(timeout=5)
                process.stdout.close()
                process.stderr.close()

    @unittest.skipUnless(os.environ.get('HF_FULL_FRAME_TEST') == '1', 'set HF_FULL_FRAME_TEST=1 for full-frame native I/O')
    def test_full_frame_native_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'full.dng.partial'
            metadata = self.capture(output, width=6064, height=4040, scene='dark')
            self.assertEqual(metadata['width'], 6064)
            self.assertEqual(metadata['height'], 4040)
            with output.open('rb') as stream:
                header = stream.read(8192)
                count = struct.unpack_from('<H', header, 8)[0]
                tags = {struct.unpack_from('<H', header, 10 + i * 12)[0]:
                        struct.unpack_from('<HHII', header, 10 + i * 12) for i in range(count)}
                self.assertEqual(tags[279][3], 6064 * 4040 * 2)
                stream.seek(tags[273][3])
                self.assertEqual(stream.read(2), b'\x40\x00')
                stream.seek(-2, os.SEEK_END)
                self.assertEqual(stream.read(2), b'\x40\x00')


if __name__ == '__main__':
    unittest.main()

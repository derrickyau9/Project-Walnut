"""Artifact-integrity and publication checks, without Qt/camera execution."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from tools import create_ui_review_gallery as gallery


def png(width=2, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (gallery.PNG_MAGIC + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\0' + b'\x10\x20\x30' * width) * height)) + chunk(b'IEND', b''))


class ReviewGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = png()
        self.report = {
            'format': 'DaOS-camera-app-shell-review-v1', 'passed': True, 'synthetic': True,
            'physical_camera_validated': False, 'physical_display_validated': False,
            'runtime': {'system': 'Windows', 'machine': 'AMD64', 'qpa': 'offscreen', 'qt': '5.15.2'},
            'request_id': 'original-request', 'shutter_actions': 1, 'journal_rows': 1,
            'settings': {'requested_exposure_us': 10000, 'requested_analogue_gain': 1},
            'actual_exposure_us': 10100, 'dng_metadata': {'width': 128, 'height': 96},
            'dng_sha256': 'a' * 64, 'export_dng_sha256': 'a' * 64,
            'private_report_path': r'C:\Users\operator\private-work\review.json',
            'source_sha256': {r'C:\Users\operator\private-source.py': 'b' * 64},
            'screenshots': [{'file': '01-camera-live.png', 'description': 'Synthetic native UI.',
                             'width': 2, 'height': 1, 'sha256': hashlib.sha256(self.image).hexdigest()}],
        }
        self.write()

    def write(self):
        (self.directory/'01-camera-live.png').write_bytes(self.image)
        (self.directory/'review.json').write_text(json.dumps(self.report), encoding='utf-8')

    def create(self):
        return gallery.create_gallery(self.directory, self.directory/'review.html')

    def test_embeds_exact_png_and_actual_runtime_without_private_report(self):
        result = self.create()
        data = (self.directory/'review.html').read_text(encoding='utf-8')
        encoded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', data)
        self.assertEqual([self.image], [base64.b64decode(value) for value in encoded])
        self.assertIn('Windows · AMD64', data)
        self.assertIn('Offscreen Qt', data)
        self.assertIn('此组截图不建立 ARM64 / FreeBSD 镜像启动结论', data)
        self.assertIn('10000 μs', data)
        self.assertIn('10100 μs', data)
        self.assertIn('请求模拟增益', data)
        self.assertNotIn('private-work', data)
        self.assertNotIn('private-source', data)
        self.assertEqual(1, result['image_count'])
        self.assertEqual(hashlib.sha256((self.directory/'review.html').read_bytes()).hexdigest(), result['sha256'])

    def test_existing_output_is_preserved(self):
        destination = self.directory/'review.html'
        destination.write_bytes(b'owner content')
        with self.assertRaisesRegex(ValueError, 'existing output'):
            self.create()
        self.assertEqual(b'owner content', destination.read_bytes())

    def test_hash_mismatch_refuses_without_output(self):
        self.report['screenshots'][0]['sha256'] = '0' * 64
        self.write()
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())

    def test_dimension_mismatch_refuses_without_output(self):
        self.report['screenshots'][0]['width'] = 3
        self.write()
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())

    def test_traversal_and_absolute_names_are_refused(self):
        for name in ('../outside.png', '/tmp/outside.png', r'C:\outside.png', 'child/image.png', 'a:stream.png'):
            with self.subTest(name=name):
                self.report['screenshots'][0]['file'] = name
                self.write()
                with self.assertRaisesRegex(ValueError, 'contained PNG'):
                    self.create()
                self.assertFalse((self.directory/'review.html').exists())

    def test_failed_or_nonsynthetic_reports_refused(self):
        for field, value in (('passed', False), ('synthetic', False), ('physical_camera_validated', True)):
            with self.subTest(field=field):
                original = self.report[field]
                self.report[field] = value
                self.write()
                with self.assertRaisesRegex(ValueError, 'synthetic-only'):
                    self.create()
                self.report[field] = original

    def test_duplicate_json_keys_and_nonfinite_json_refused(self):
        for content in ('{"passed":true,"passed":false}', '{"value":NaN}'):
            with self.subTest(content=content):
                (self.directory/'review.json').write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.create()

    def test_duplicate_casefold_filenames_refused(self):
        self.report['screenshots'].append(dict(self.report['screenshots'][0], file='01-CAMERA-LIVE.PNG'))
        self.write()
        with self.assertRaisesRegex(ValueError, 'duplicate screenshot'):
            self.create()

    def test_damaged_png_crc_refused_even_if_report_hash_matches(self):
        self.image = self.image[:-1] + bytes([self.image[-1] ^ 1])
        self.report['screenshots'][0]['sha256'] = hashlib.sha256(self.image).hexdigest()
        self.write()
        with self.assertRaisesRegex(ValueError, 'CRC mismatch'):
            self.create()

    def test_unknown_description_escaped_and_private_path_omitted(self):
        entry = self.report['screenshots'][0]
        entry['file'] = 'other.png'
        entry['description'] = '<script>alert("not code")</script>'
        self.write()
        (self.directory/'other.png').write_bytes(self.image)
        self.create()
        data = (self.directory/'review.html').read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;', data)
        self.assertNotIn('<script>alert(', data)
        self.assertEqual('本机路径已省略 / Local filesystem path omitted', gallery._text(r'C:\Users\secret\project'))

    def test_file_and_total_bounds_are_enforced(self):
        for constant in ('MAX_IMAGE_BYTES', 'MAX_TOTAL_IMAGE_BYTES'):
            with self.subTest(constant=constant), patch.object(gallery, constant, len(self.image) - 1):
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse((self.directory/'review.html').exists())

    def test_input_symlink_refused(self):
        image = self.directory/'01-camera-live.png'
        target = self.directory/'original.png'
        image.rename(target)
        try:
            image.symlink_to(target.name)
        except (OSError, NotImplementedError):
            target.rename(image)
            self.skipTest('platform cannot create an unprivileged symlink')
        with self.assertRaises(ValueError):
            self.create()

    def test_runtime_labels_cannot_smuggle_paths(self):
        self.report['runtime']['system'] = r'C:\Users\private'
        self.write()
        with self.assertRaisesRegex(ValueError, 'runtime identity'):
            self.create()

    def test_hardlinked_input_refused_without_touching_owner(self):
        owner = self.directory/'owner.png'
        os.link(self.directory/'01-camera-live.png', owner)
        with self.assertRaisesRegex(ValueError, 'without links'):
            self.create()
        self.assertEqual(self.image, owner.read_bytes())
        self.assertFalse((self.directory/'review.html').exists())

    def test_screenshot_count_bound_is_enforced_before_reading_images(self):
        self.report['screenshots'] = self.report['screenshots'] * (gallery.MAX_IMAGES + 1)
        self.write()
        with self.assertRaisesRegex(ValueError, 'bounded screenshot list'):
            self.create()

    def camera_first_fixture(self):
        self.report.update({
            'format': 'DaOS-camera-first-review-v1',
            'preview_source': 'Python synthetic Bayer PreviewStream; separate scene, not sensor readout',
            'capture_backend': 'C++ hf-camerad / HF-Sim',
            'actual_analogue_gain': 1.05,
            'source_sha256': {'ui/on-device/CameraPlayback.qml': 'b' * 64,
                              r'C:\Users\operator\private-source.py': 'c' * 64},
        })
        self.image = png(800, 600)
        self.report['screenshots'] = [{
            'file': '01-live-800.png', 'description': 'Actual camera-first 800 by 600 UI.',
            'width': 800, 'height': 600, 'sha256': hashlib.sha256(self.image).hexdigest(),
        }]
        self.write()
        (self.directory/'01-live-800.png').write_bytes(self.image)

    def test_camera_first_gallery_is_camera_operating_ui_with_distinct_sources(self):
        self.camera_first_fixture()
        result = self.create()
        data = (self.directory/'review.html').read_text(encoding='utf-8')
        self.assertEqual('camera-first', result['presentation'])
        self.assertEqual('DaOS-camera-first-review-v1', result['review_format'])
        self.assertIn('R12 · Camera operating UI', data)
        self.assertIn('摄影优先的<br>相机操作界面', data)
        self.assertIn('800 × 600 primary camera', data)
        self.assertIn('Python synthetic Bayer PreviewStream', data)
        self.assertIn('C++ hf-camerad / HF-Sim', data)
        self.assertIn('实际模拟增益 / Actual gain', data)
        self.assertIn('1.05 ×', data)
        self.assertIn('ui/on-device/CameraPlayback.qml', data)
        self.assertNotIn('private-source.py', data)
        self.assertNotIn('包含左侧导航', data)
        encoded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', data)
        self.assertEqual([self.image], [base64.b64decode(value) for value in encoded])

    def test_camera_first_displays_actual_freebsd_arm_runtime_without_boot_claim(self):
        self.camera_first_fixture()
        self.report['runtime'] = {'system': 'FreeBSD', 'machine': 'arm64',
                                  'qpa': 'offscreen', 'qt': '5.15.19', 'release': '15.1-RELEASE-p6'}
        self.write()
        self.create()
        data = (self.directory/'review.html').read_text(encoding='utf-8')
        self.assertIn('FreeBSD · arm64', data)
        self.assertIn('Offscreen Qt', data)
        self.assertIn('此组截图不建立 ARM64 / FreeBSD 镜像启动结论', data)
        self.assertNotIn('Windows · AMD64', data)

    def test_failed_camera_first_report_remains_refused(self):
        self.camera_first_fixture()
        self.report['passed'] = False
        self.write()
        with self.assertRaisesRegex(ValueError, 'synthetic-only'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())

    def test_camera_first_invalid_runtime_refused(self):
        self.camera_first_fixture()
        self.report['runtime']['machine'] = '/home/private/arm64'
        self.write()
        with self.assertRaisesRegex(ValueError, 'runtime identity'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())

    def test_camera_first_cannot_misattribute_independent_preview_to_native_capture(self):
        self.camera_first_fixture()
        self.report['preview_source'] = 'Native C++ sensor preview'
        self.write()
        with self.assertRaisesRegex(ValueError, 'independent Python preview/native capture'):
            self.create()
        self.assertFalse((self.directory/'review.html').exists())


if __name__ == '__main__':
    unittest.main()

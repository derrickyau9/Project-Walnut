"""Achromatic UI background contract and real, no-shutter Qt renders.

Photographic pixels are deliberately excluded: the requirement concerns UI
surfaces, not a monochrome transformation of the simulated or saved images.
The camera fixture is composed rather than subclassed, so its twelve existing
interaction cases are not rediscovered or counted again here.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT/'ui/on-device'


class NeutralPaletteContractTests(unittest.TestCase):
    def test_active_palette_is_opaque_achromatic_with_explicit_user_override(self):
        theme = json.loads((UI/'retail-theme.json').read_text(encoding='utf-8'))
        appearance = theme['cameraAppearance']
        self.assertEqual('achromatic-opaque-ui', appearance['policy'])
        for flag in ('userRequested', 'opaqueSurfaces', 'imagePixelsUnchanged'):
            self.assertIs(appearance[flag], True, flag)
        self.assertTrue(appearance['scope'])
        reference = theme['referencePalette']
        self.assertEqual(theme['source']['revision'], reference['sourceRevision'])
        self.assertEqual('10d233e316e1c258e8aa365f6a6de4d82d10c7f5', reference['sourceRevision'])
        # These retain the inspected reference, not the active camera palette.
        self.assertEqual('#c52b25', reference['light']['accent'])
        self.assertEqual('#151a22', reference['dark']['background'])
        required = {'background', 'surface', 'raised', 'layer', 'text', 'muted',
                    'line', 'accent', 'accentHover', 'softAccent', 'rail'}
        for mode in ('light', 'dark'):
            palette = theme[mode]
            self.assertTrue(required <= set(palette), mode)
            for role, value in palette.items():
                with self.subTest(mode=mode, role=role):
                    self.assertIsNotNone(re.fullmatch(r'#[0-9a-fA-F]{6}', value),
                                         'Active roles must be opaque RGB colors.')
                    red, green, blue = (int(value[start:start+2], 16) for start in (1, 3, 5))
                    self.assertEqual((red, red, red), (red, green, blue), value)


try:
    from PyQt5.QtCore import QPointF, PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt5.QtGui import QImage
    from PyQt5.QtTest import QTest
    from PyQt5 import QtQml, QtQuick
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

if QT_AVAILABLE:
    # Only absence of the optional Qt runtime is a skip. Fixture/application
    # import failures must fail discovery rather than silently skipping checks.
    fixture_api = (sys.modules.get('tests.test_camera_first_ui')
                   or sys.modules.get('test_camera_first_ui'))
    if fixture_api is None:
        fixture_spec = importlib.util.spec_from_file_location(
            'hf_neutral_camera_fixture', ROOT/'tests/test_camera_first_ui.py')
        fixture_api = importlib.util.module_from_spec(fixture_spec)
        sys.modules[fixture_spec.name] = fixture_api
        fixture_spec.loader.exec_module(fixture_api)


@unittest.skipUnless(QT_AVAILABLE, 'optional PyQt5/Qt Quick runtime is not installed')
class NeutralRenderedBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_api.CameraFirstUITests.setUpClass()
        cls.evidence_directory = None
        cls.saved_frames = []
        cls.observed_capture_rows = {}
        cls.source_files = [path for path in UI.iterdir()
                            if path.is_file() and path.suffix in ('.qml', '.py', '.json')]
        cls.source_files.extend((ROOT/'camera/pipeline/preview.py', ROOT/'simulation/bayer.py'))
        cls.source_before = cls.source_hashes()
        output = os.environ.get('HF_NEUTRAL_SCREENSHOT_DIR')
        if output:
            cls.evidence_directory = Path(output).absolute()
            cls.evidence_directory.mkdir(parents=True, exist_ok=False)

    @classmethod
    def source_hashes(cls):
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in cls.source_files}

    @classmethod
    def tearDownClass(cls):
        after = cls.source_hashes()
        if cls.evidence_directory is not None:
            report = {'format': 'DaOS-neutral-ui-render-evidence-v1',
                      'synthetic': True, 'planned_shutter_actions': 0,
                      'observed_capture_rows': cls.observed_capture_rows,
                      'backend': 'Python reference authority; no planned capture',
                      'preview_source': 'Python synthetic Bayer PreviewStream, separate from capture',
                      'physical_camera_validated': False, 'physical_display_validated': False,
                      'appliance_image_boot_validated': False,
                      'runtime': {'system': platform.system(), 'machine': platform.machine(),
                                  'python': platform.python_version(), 'qt': QT_VERSION_STR,
                                  'pyqt': PYQT_VERSION_STR,
                                  'qpa': fixture_api.CameraFirstUITests.app.platformName()},
                      'source_sha256_before': cls.source_before, 'source_sha256_after': after,
                      'source_unchanged': cls.source_before == after,
                      'screenshots': cls.saved_frames,
                      'suite_result': 'Use the separate unittest result; render metadata does not assert a pass.'}
            with (cls.evidence_directory/'renders.json').open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(report, stream, indent=2)
                stream.write('\n')
        if cls.source_before != after:
            raise AssertionError('UI source changed during the neutral render checks.')

    def setUp(self):
        self.case = fixture_api.CameraFirstUITests()
        self.case.setUp()

    def tearDown(self):
        try:
            if (self.case.directory/'journal.sqlite3').is_file():
                self.__class__.observed_capture_rows[self._testMethodName] = len(self.case.capture_rows())
        finally:
            self.case.tearDown()

    def frame(self, evidence_name=None, mode=None):
        result = self.case.window.contentItem().grabToImage()
        self.assertIsNotNone(result)
        ready = []
        result.ready.connect(lambda: ready.append(True))
        self.case.wait(lambda: bool(ready))
        image = result.image().convertToFormat(QImage.Format_RGBA8888)
        self.assertFalse(image.isNull())
        if evidence_name and self.evidence_directory is not None:
            path = self.evidence_directory/(evidence_name+'.png')
            self.assertFalse(path.exists(), 'Render evidence is create-only.')
            self.assertTrue(image.save(str(path)))
            self.saved_frames.append({'file': path.name, 'theme': mode,
                                      'width': image.width(), 'height': image.height(),
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                      'route': self.case.window.property('currentScreen')})
        return image

    def assert_flat_regions_are_neutral(self, image, *, minimum=300):
        """Inspect flat 3x3 rendered patches, excluding text/edge antialiasing."""
        data = image.bits().asstring(image.byteCount())
        stride = image.bytesPerLine()
        count = 0
        for y in range(3, image.height()-3, 10):
            for x in range(3, image.width()-3, 10):
                offset = (y-1)*stride + (x-1)*4
                pixel = data[offset:offset+4]
                row = pixel*3
                if all(data[offset+line*stride:offset+line*stride+12] == row for line in range(3)):
                    count += 1
                    self.assertEqual((pixel[0], pixel[0], pixel[0]), tuple(pixel[:3]),
                                     'Chromatic rendered flat UI patch at '+str((x, y)))
                    self.assertEqual(255, pixel[3], 'Final UI frame must be opaque at '+str((x, y)))
        self.assertGreaterEqual(count, minimum, 'Too few actual flat UI regions were inspected.')

    def assert_background_patch(self, image, scene_x, scene_y):
        scale_x = image.width()/self.case.window.width()
        scale_y = image.height()/self.case.window.height()
        x, y = round(scene_x*scale_x), round(scene_y*scale_y)
        self.assertTrue(1 <= x < image.width()-1 and 1 <= y < image.height()-1)
        pixels = [image.pixelColor(x+dx, y+dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        self.assertEqual(1, len({color.rgba() for color in pixels}),
                         'Background sample unexpectedly intersects content/antialiasing: '+str((x, y)))
        color = pixels[0]
        self.assertEqual((color.red(), color.red(), color.red()),
                         (color.red(), color.green(), color.blue()), str((x, y, color.name())))
        self.assertEqual(255, color.alpha())

    def assert_control_background(self, image, name):
        item = self.case.item(name)
        point = item.mapToScene(QPointF(7, 7))
        self.assert_background_patch(image, point.x(), point.y())

    def finish_without_capture(self, settings_before):
        self.assertEqual(settings_before, self.case.controller._client.get_state()['settings'])
        self.assertEqual([], self.case.capture_rows())
        self.assertEqual('', self.case.controller.lastRequestId)
        self.assertEqual([], self.case.warnings)

    def menu_frames(self, mode):
        camera = self.case.launch()
        before = camera._client.get_state()['settings']
        self.assertTrue(self.case.shell.setPreference('theme', mode))
        self.case.click('menuButton')
        self.case.wait(lambda: self.case.window.property('currentScreen') == 'menu')
        for group in ('exposure', 'image', 'display', 'storage', 'system', 'tools'):
            self.case.click('menuCategory-'+group)
            image = self.frame(mode+'-menu-exposure' if group == 'exposure' else None, mode)
            self.assert_flat_regions_are_neutral(image)
        self.finish_without_capture(before)

    def test_dark_menu_backgrounds_render_achromatically_without_capture(self):
        self.menu_frames('dark')

    def test_light_menu_backgrounds_render_achromatically_without_capture(self):
        self.menu_frames('light')

    def test_quick_controls_and_shooting_bar_are_neutral_in_both_modes(self):
        camera = self.case.launch()
        before = camera._client.get_state()['settings']
        self.case.wait(lambda: camera.previewActive and bool(camera.previewUrl))
        for mode in ('dark', 'light'):
            with self.subTest(mode=mode):
                self.assertTrue(self.case.shell.setPreference('theme', mode))
                self.case.click('quickButton')
                popup = self.case.popup('quickControlsDialog')
                self.case.wait(lambda: popup.property('visible'))
                image = self.frame(mode+'-quick-controls', mode)
                x, y = float(popup.property('x')), float(popup.property('y'))
                width, height = float(popup.property('width')), float(popup.property('height'))
                # Interior margins have no glyphs; photographic pixels and the
                # rounded popup corners are deliberately outside these samples.
                for dx, dy in ((10, 80), (width-10, 80), (width/2, height-10)):
                    self.assert_background_patch(image, x+dx, y+dy)
                for name in ('qGainIncrease', 'qGainDecrease', 'qExposureCombo'):
                    self.assert_control_background(image, name)
                self.case.click('qCloseButton')
                self.case.wait(lambda: not popup.property('visible'))
                image = self.frame()
                bar = image.copy(0, round(image.height()*0.90), image.width(), round(image.height()*0.10))
                self.assert_flat_regions_are_neutral(bar, minimum=150)
        self.finish_without_capture(before)

    def test_preview_retains_source_color_while_shooting_hud_is_achromatic(self):
        camera = self.case.launch()
        before = camera._client.get_state()['settings']
        self.case.wait(lambda: camera.previewActive and bool(camera.previewUrl))
        preview = self.case.item('viewfinderImage')
        self.case.wait(lambda: preview.isVisible() and preview.property('status') == 1
                       and preview.property('paintedWidth') > 0 and preview.property('paintedHeight') > 0)
        image = self.frame('live-color-preview', 'dark')
        center = preview.mapToScene(QPointF(preview.width()/2, preview.height()/2))
        scale_x, scale_y = image.width()/self.case.window.width(), image.height()/self.case.window.height()
        crop = image.copy(round((center.x()-40)*scale_x), round((center.y()-40)*scale_y),
                          round(80*scale_x), round(80*scale_y))
        data = crop.bits().asstring(crop.byteCount())
        self.assertTrue(data[0::4] != data[1::4] or data[1::4] != data[2::4],
                        'UI neutralization must not globally desaturate photographic pixels.')
        bar = image.copy(0, round(image.height()*0.90), image.width(), round(image.height()*0.10))
        self.assert_flat_regions_are_neutral(bar, minimum=150)
        area = self.case.item('viewfinderArea')
        found = set()
        for background in area.childItems():
            if background.property('color') is None:
                continue
            for child in background.childItems():
                text = child.property('text')
                if isinstance(text, str) and text.replace(' ', '') in ('MRAW14', 'HF-Sim'):
                    point = background.mapToScene(QPointF(3, background.height()/2))
                    self.assert_background_patch(image, point.x(), point.y())
                    found.add(text.replace(' ', ''))
        self.assertEqual({'MRAW14', 'HF-Sim'}, found, 'Both actual shooting badges must be inspected.')
        shutter = self.case.item('shootButton')
        background = shutter.property('background')
        self.assertIsNotNone(background)
        point = background.mapToScene(QPointF(background.width()/2, 6))
        self.assert_background_patch(image, point.x(), point.y())
        self.finish_without_capture(before)

    def test_optional_tool_surfaces_are_neutral_without_changing_camera_settings(self):
        camera = self.case.launch()
        before = camera._client.get_state()['settings']
        for mode in ('dark', 'light'):
            self.assertTrue(self.case.shell.setPreference('theme', mode))
            for app in ('diagnostics', 'recipes', 'devices', 'system', 'photos'):
                with self.subTest(mode=mode, app=app):
                    self.assertTrue(self.case.shell.navigate(app))
                    route = 'playback' if app == 'photos' else 'tool'
                    self.case.wait(lambda: self.case.window.property('currentScreen') == route)
                    QTest.qWait(40)
                    image = self.frame(mode+'-tool-'+app if app != 'photos' else None, mode)
                    self.assert_flat_regions_are_neutral(image)
        self.finish_without_capture(before)


if __name__ == '__main__':
    unittest.main()

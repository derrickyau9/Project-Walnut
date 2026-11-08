"""Display-only sample photographs must never become synthetic capture pixels."""
from pathlib import Path
import hashlib
import importlib
import os
import struct
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QColor,QGuiApplication,QImage
from PyQt5.QtTest import QTest
from camera.dng.inspect import inspect_dng
from simulation.bayer import generate_bayer
from localization import install_localization
from sample_preview import image_url

fixture=sys.modules.get('test_camera_first_ui') or importlib.import_module('tests.test_camera_first_ui')


class SamplePreviewTests(unittest.TestCase):
    maxDiff=None
    @classmethod
    def setUpClass(cls):
        fixture.CameraFirstUITests.setUpClass()
        cls.app=QGuiApplication.instance()

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'sample.jpg'
        image=QImage(32,24,QImage.Format_RGB32);image.fill(QColor(220,40,70))
        self.assertTrue(image.save(str(self.path),'JPEG',95))

    def test_local_jpeg_selection_is_explicit_and_read_only(self):
        before=self.path.read_bytes()
        self.assertTrue(image_url().isEmpty())
        self.assertEqual(Path(image_url(self.path).toLocalFile()),self.path.resolve())
        self.assertEqual(self.path.read_bytes(),before)

    def test_non_jpeg_missing_and_non_file_inputs_are_refused(self):
        disguised=Path(self.temporary.name)/'not-a-jpeg.jpg'
        image=QImage(4,4,QImage.Format_RGB32);image.fill(QColor('red'))
        self.assertTrue(image.save(str(disguised),'PNG'))
        for value in (disguised,Path(self.temporary.name),self.path.with_name('missing.jpg'),'https://example.invalid/photo.jpg'):
            with self.subTest(value=str(value)),self.assertRaises(ValueError):image_url(value)

    def test_normal_preview_remains_the_default(self):
        case=fixture.CameraFirstUITests();case.setUp()
        try:
            camera=case.launch()
            case.wait(lambda:camera.previewActive and bool(camera.previewUrl))
            self.assertFalse(case.window.property('samplePreviewActive'))
            self.assertEqual(case.item('viewfinderImage').property('source').toString(),camera.previewUrl)
            self.assertEqual(case.capture_rows(),[])
        finally:case.tearDown()

    def test_sample_pixels_and_labels_render_without_changing_native_raw(self):
        if not fixture.native_command():self.skipTest('Build hf-camerad for native sample/capture isolation QA')
        previous=getattr(self.app,'_daos_localization',None)
        previous_language=previous.language if previous else None
        local=install_localization(self.app,'en_US')
        original=fixture.QQmlApplicationEngine
        def engine_factory():
            engine=original();local.bindEngine(engine)
            engine.rootContext().setContextProperty('samplePreviewSource',image_url(self.path))
            return engine
        fixture.QQmlApplicationEngine=engine_factory
        case=fixture.CameraFirstUITests();case.setUp()
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        try:
            camera=case.launch(backend='native')
            image=case.item('viewfinderImage')
            case.wait(lambda:image.property('status')==1 and image.property('paintedWidth')>0)
            QTest.qWait(300)
            self.assertTrue(case.window.property('samplePreviewActive'))
            self.assertFalse(camera.previewActive,'Sample mode must not run the synthetic animation')
            self.assertEqual(Path(image.property('source').toLocalFile()),self.path.resolve())
            self.assertEqual(case.item('previewSourceLabel').property('text'),'Sample preview')
            self.assertEqual(case.item('samplePreviewRawNotice').property('text'),'RAW: HF-Sim synthetic data')
            grab=case.window.contentItem().grabToImage();ready=[];grab.ready.connect(lambda:ready.append(True))
            case.wait(lambda:bool(ready))
            at=image.mapToScene(QPointF(image.width()/2,image.height()/2))
            pixel=grab.image().pixelColor(round(at.x()),round(at.y()))
            for actual,expected in zip((pixel.red(),pixel.green(),pixel.blue()),(220,40,70)):
                self.assertLessEqual(abs(actual-expected),6)
            expected=dict(camera.confirmedSettings)
            local.setLanguage('zh_CN');self.app.processEvents()
            self.assertEqual(case.item('previewSourceLabel').property('text'),'示例画面')
            self.assertEqual(dict(camera.confirmedSettings),expected)
            self.assertEqual(case.capture_rows(),[])
            case.click('shootButton')
            case.wait(lambda:camera.captureState=='complete')
            photos=list((case.directory/'photos').glob('*.dng'));self.assertEqual(len(photos),1)
            info=inspect_dng(photos[0]);self.assertEqual((info['width'],info['height']),(128,96))
            raw=photos[0].read_bytes()
            pixels=struct.unpack_from('<'+str(128*96)+'H',raw,info['strip_offsets'][0])
            self.assertEqual(pixels,tuple(generate_bayer(128,96,expected['requested_exposure_us'],expected['requested_analogue_gain'])))
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
            self.assertEqual(Path(image.property('source').toLocalFile()),self.path.resolve())
            self.assertEqual(case.warnings,[])
        finally:
            case.tearDown();fixture.QQmlApplicationEngine=original
            if previous is not None:local.setLanguage(previous_language)
            else:
                self.app.removeTranslator(local._translator)
                if getattr(self.app,'_daos_localization',None)is local:del self.app._daos_localization


if __name__=='__main__':unittest.main()

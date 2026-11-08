"""Actual Qt level overlay, unavailable states and independent preference switch."""
from pathlib import Path
import importlib
import json
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ui/on-device'))
from PyQt5.QtCore import QPointF
from PyQt5.QtTest import QTest
from level_controller import LevelController,FORMAT,MAX_AGE_NS
from localization import install_localization
fixture=sys.modules.get('test_camera_first_ui')or importlib.import_module('tests.test_camera_first_ui')


class LevelUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.CameraFirstUITests.setUpClass();cls.app=fixture.CameraFirstUITests.app

    def setUp(self):
        self.case=fixture.CameraFirstUITests();self.case.setUp();self.level=None
        self.previous=getattr(self.app,'_daos_localization',None)
        self.previous_language=self.previous.language if self.previous else None
        self.local=install_localization(self.app,'en_US')
        self.original=fixture.QQmlApplicationEngine

    def launch(self,**kwargs):
        self.level=LevelController(self.case.directory,poll_ms=0,**kwargs)
        def engine_factory():
            engine=self.original();self.local.bindEngine(engine)
            engine.rootContext().setContextProperty('levelControl',self.level);return engine
        fixture.QQmlApplicationEngine=engine_factory
        return self.case.launch()

    def tearDown(self):
        if self.level:self.level.close()
        self.case.tearDown();fixture.QQmlApplicationEngine=self.original
        if self.previous is not None:self.local.setLanguage(self.previous_language)
        else:
            self.app.removeTranslator(self.local._translator)
            if getattr(self.app,'_daos_localization',None)is self.local:del self.app._daos_localization

    def test_real_display_switch_defaults_off_and_keeps_old_preferences(self):
        camera=self.launch();before=dict(camera.confirmedSettings)
        self.case.shell.setPreference('grid_enabled',True)
        old=self.case.directory/'shell-preferences.json';original=old.read_bytes()
        self.assertFalse(self.level.enabled);self.assertFalse(self.case.item('levelOverlay').isVisible())
        self.case.click('menuButton');self.case.click('menuCategory-display');self.case.click('menuLevelSwitch')
        self.assertTrue(self.level.enabled)
        self.assertEqual(old.read_bytes(),original)
        self.case.click('menuBackButton')
        self.assertTrue(self.case.item('levelOverlay').isVisible())
        self.assertIn('unavailable',self.case.item('levelStateLabel').property('text'))
        self.assertIn('—',self.case.item('levelRollValue').property('text'))
        self.assertNotIn('0.0',self.case.item('levelRollValue').property('text'))
        self.case.click('menuButton');self.case.click('menuCategory-display');self.case.click('menuLevelSwitch');self.case.click('menuBackButton')
        self.assertFalse(self.case.item('levelOverlay').isVisible())
        self.assertEqual(dict(camera.confirmedSettings),before);self.assertEqual(self.case.capture_rows(),[])
        self.assertEqual(self.case.warnings,[])

    def test_demo_is_labeled_tilts_and_only_aligned_indicator_is_green(self):
        camera=self.launch();before=dict(camera.confirmedSettings)
        self.level.setEnabled(True,persist=False);self.assertTrue(self.level.setDemoAngles(-6,3));self.app.processEvents()
        indicator=self.case.item('levelRollIndicator')
        self.assertIn('DEMO',self.case.item('levelStateLabel').property('text'))
        self.assertIn('-6.0°',self.case.item('levelRollValue').property('text'))
        self.assertIn('+3.0°',self.case.item('levelPitchValue').property('text'))
        self.assertEqual(indicator.property('rotation'),6);self.assertNotEqual(indicator.property('color').name(),'#6de39a')
        self.level.setDemoAngles(0,0);self.app.processEvents()
        self.case.wait(lambda:self.case.window.property('pageArrival')==1)
        grab=self.case.window.contentItem().grabToImage();ready=[];grab.ready.connect(lambda:ready.append(True));self.case.wait(lambda:bool(ready))
        point=indicator.mapToScene(QPointF(indicator.width()/2,indicator.height()/2))
        self.assertEqual(grab.image().pixelColor(round(point.x()),round(point.y())).name(),'#6de39a')
        self.assertIn('DEMO',self.case.item('levelStateLabel').property('text'))
        self.level.clearDemo();self.app.processEvents()
        self.assertFalse(indicator.isVisible());self.assertIsNone(self.level.rollDeg)
        self.assertIn('—',self.case.item('levelPitchValue').property('text'))
        self.assertEqual(dict(camera.confirmedSettings),before);self.assertEqual(self.case.capture_rows(),[])
        self.assertEqual(self.case.warnings,[])

    def test_fresh_external_zero_becomes_unavailable_when_stale(self):
        clock=[1_000_000_000]
        path=self.case.directory/'attitude.json'
        path.write_text(json.dumps({'format':FORMAT,'valid':True,'monotonic_ns':clock[0],'roll_deg':0,'pitch_deg':0}))
        self.launch(attitude_file=path,clock_ns=lambda:clock[0]);self.level.setEnabled(True,persist=False);self.app.processEvents()
        self.assertTrue(self.level.aligned);self.assertFalse(self.level.isDemo)
        clock[0]+=MAX_AGE_NS+1;self.level.refresh();self.app.processEvents()
        self.assertFalse(self.level.available);self.assertFalse(self.case.item('levelRollIndicator').isVisible())
        self.assertIn('stale',self.case.item('levelStateLabel').property('text'))
        self.assertIn('—',self.case.item('levelRollValue').property('text'))
        self.assertEqual(self.case.warnings,[])


if __name__=='__main__':unittest.main()

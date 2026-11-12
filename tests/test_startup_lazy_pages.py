"""First-frame work and retained first-use page state, with actual Qt objects."""
import importlib
import sys
import unittest
from PyQt5.QtCore import QObject

fixture = sys.modules.get('test_camera_first_ui') or importlib.import_module('tests.test_camera_first_ui')


class StartupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.CameraFirstUITests.setUpClass()

    def test_hidden_pages_are_absent_then_menu_and_workspace_stay_loaded(self):
        case = fixture.CameraFirstUITests();case.setUp()
        try:
            case.launch()
            def loader(name):return case.window.findChild(QObject,name)
            for name in ('menuLoader','playbackLoader','toolsLoader','keyboardLoader','workspacePanelLoader'):
                self.assertIsNone(loader(name).property('item'),name)
            case.click('menuButton')
            menu=loader('menuLoader').property('item');self.assertIsNotNone(menu)
            case.click('menuBackButton')
            self.assertIs(loader('menuLoader').property('item'),menu)
            case.click('quickButton')
            panel=loader('workspacePanelLoader').property('item');self.assertIsNotNone(panel)
            self.assertEqual(panel.property('page'),'quick')
            self.assertEqual(case.capture_rows(),[])
            self.assertEqual(case.warnings,[])
        finally:case.tearDown()

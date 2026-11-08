"""Real input and camera-layout checks for the retractable icon taskbar.

These cases compose the existing camera fixture rather than inheriting its
twelve tests. Navigation, drags and resizing never request photographs. The
animation case deliberately submits one edge-triggered hardware shutter and
checks that animation cannot suppress it or create an additional request.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
try:
    from PyQt5.QtCore import QPoint, QPointF, Qt, QTimer
    from PyQt5.QtQml import QQmlEngine, QQmlProperty
    from PyQt5.QtTest import QTest
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

if QT_AVAILABLE:
    fixture_api = (sys.modules.get('tests.test_camera_first_ui')
                   or sys.modules.get('test_camera_first_ui'))
    if fixture_api is None:
        spec = importlib.util.spec_from_file_location(
            'hf_taskbar_camera_fixture', ROOT/'tests/test_camera_first_ui.py')
        fixture_api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = fixture_api
        spec.loader.exec_module(fixture_api)


@unittest.skipUnless(QT_AVAILABLE, 'optional PyQt5/Qt Quick runtime is not installed')
class CameraTaskbarUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_api.CameraFirstUITests.setUpClass()

    def setUp(self):
        self.case = fixture_api.CameraFirstUITests()
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def launch(self, *, expanded=True, **kwargs):
        kwargs.setdefault('taskbar_auto_hide',False)
        controller = self.case.launch(**kwargs)
        self.settle(not kwargs['taskbar_auto_hide'])
        if expanded and not self.case.window.property('taskbarExpanded'):
            self.raw_click('taskbarRevealHandle');self.settle(True)
        self.before_settings = controller._client.get_state()['settings']
        self.before_request = controller.lastRequestId
        return controller

    def progress(self):
        value = self.case.window.property('taskbarProgress')
        self.assertIsNotNone(value, 'camera taskbar progress is not exposed')
        return float(value)

    def settle(self, expanded):
        target = 1.0 if expanded else 0.0
        self.case.wait(lambda: abs(self.progress()-target) < 0.001
                       and not self.case.window.property('taskbarAnimating'), timeout=3000)
        self.assertEqual(expanded, self.case.window.property('taskbarExpanded'))

    def scene(self, control, x=None, y=None):
        item = self.case.item(control) if isinstance(control, str) else control
        point = item.mapToScene(QPointF(item.width()/2 if x is None else x,
                                      item.height()/2 if y is None else y))
        return QPoint(round(point.x()), round(point.y()))

    def raw_click(self, name):
        control = self.case.item(name)
        self.assertTrue(control.isVisible(), name+' hidden')
        self.assertTrue(control.isEnabled(), name+' disabled')
        QTest.mouseClick(self.case.window, Qt.LeftButton, Qt.NoModifier, self.scene(control))

    def drag(self, start, end, *, steps=6):
        window = self.case.window
        QTest.mouseMove(window, start, 10)
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
        for index in range(1, steps+1):
            point = QPoint(round(start.x()+(end.x()-start.x())*index/steps),
                           round(start.y()+(end.y()-start.y())*index/steps))
            QTest.mouseMove(window, point, 16)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
        QTest.qWait(10)

    def hide_by_swipe(self):
        # The 64px rail is now a vertically scrollable viewport. Start on an
        # actual icon and prove horizontal swipe resolves without activation.
        surface = self.case.reveal_control('taskbar-camera')
        start = self.scene(surface, surface.width()-4, surface.height()/2)
        self.drag(start, QPoint(max(1, start.x()-62), start.y()))
        self.settle(False)

    def reveal_by_swipe(self):
        area = self.case.item('taskbarRevealGesture')
        start = self.scene(area, min(8, area.width()/2), area.height()/2)
        self.drag(start, QPoint(start.x()+110, start.y()))
        self.settle(True)

    def untouched(self):
        self.assertEqual(self.before_request, self.case.controller.lastRequestId)
        self.assertEqual(self.before_settings, self.case.controller._client.get_state()['settings'])
        self.assertEqual([], self.case.capture_rows(), 'taskbar interaction submitted a photograph')
        self.assertEqual([], self.case.warnings)

    def ready_image(self):
        image = self.case.item('viewfinderImage')
        self.case.wait(lambda: image.property('status') == 1
                       and float(image.property('paintedWidth')) > 0
                       and float(image.property('paintedHeight')) > 0)
        return image

    def assert_fit(self):
        image = self.ready_image()
        size = image.property('sourceSize')
        painted_width, painted_height = float(image.property('paintedWidth')), float(image.property('paintedHeight'))
        self.assertGreater(size.width(), 0)
        self.assertGreater(size.height(), 0)
        self.assertAlmostEqual(size.width()/size.height(), painted_width/painted_height, places=3)
        self.assertLessEqual(painted_width, image.width()+0.5, 'image is cropped horizontally')
        self.assertLessEqual(painted_height, image.height()+0.5, 'image is cropped vertically')
        return painted_width, painted_height

    def test_default_collapsed_taskbar_reveals_seven_scrollable_64px_icons_without_labels(self):
        self.launch(expanded=False,taskbar_auto_hide=True)
        self.assertAlmostEqual(0,self.progress(),delta=.001)
        handle=self.case.item('taskbarRevealHandle')
        self.assertGreaterEqual(handle.width(),64);self.assertGreaterEqual(handle.height(),64)
        self.reveal_by_swipe()
        rail = self.case.item('cameraTaskbar')
        self.assertTrue(rail.isVisible())
        self.assertAlmostEqual(72, rail.width(), delta=1)
        titles = {item['id']: item.get('label') or item['title'] for item in self.case.shell.apps}
        for app in ('camera','photos','recipes','settings','devices','diagnostics','system'):
            name = 'taskbar-'+app
            self.case.assert_in_view(name)
            button = self.case.item(name)
            self.assertGreaterEqual(button.width(), 64)
            self.assertGreaterEqual(button.height(), 64)
            context = QQmlEngine.contextForObject(button)
            self.assertIsNotNone(context, name+' has no QML creation context')
            self.assertEqual(titles[app], QQmlProperty(button, 'Accessible.name', context).read())
        visible_labels = []
        def visit(parent):
            for child in parent.childItems():
                if child.isVisible() and child.inherits('QQuickText') and child.property('text'):
                    visible_labels.append(child.property('text'))
                visit(child)
        visit(rail)
        self.assertEqual([], visible_labels, 'taskbar must render icons rather than text labels')
        self.untouched()

    def test_left_swipe_hides_and_right_edge_swipe_reveals_without_camera_actions(self):
        self.launch()
        self.hide_by_swipe()
        self.assertAlmostEqual(0, self.case.window.property('taskbarInset'), delta=0.1)
        self.assertTrue(self.case.item('taskbarRevealHandle').isVisible())
        self.reveal_by_swipe()
        self.assertAlmostEqual(72, self.case.window.property('taskbarInset'), delta=0.1)
        self.assertEqual('camera', self.case.shell.currentApp)
        self.untouched()

    def test_swiping_an_icon_does_not_also_navigate_to_that_application(self):
        self.launch()
        button = self.case.item('taskbar-photos')
        start = self.scene(button, button.width()-4, button.height()/2)
        self.drag(start, QPoint(max(1, start.x()-58), start.y()))
        self.settle(False)
        self.assertEqual('camera', self.case.shell.currentApp, 'completed icon swipe also activated its app')
        self.reveal_by_swipe()
        self.untouched()

    def test_vertical_drag_short_jitter_and_returned_drag_do_not_become_navigation(self):
        self.launch()
        surface = self.case.item('taskbarSwipeSurface')
        start = self.scene(surface, surface.width()-3, surface.height()/2)
        self.drag(start, QPoint(start.x()-5, start.y()+78))
        self.settle(True)
        self.drag(start, QPoint(start.x()-9, start.y()+3))
        self.settle(True)
        # A dragged icon must not turn into a tap simply because the pointer
        # returns close to its origin before release.
        button = self.case.item('taskbar-photos')
        start = self.scene(button)
        QTest.mousePress(self.case.window, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(self.case.window, QPoint(start.x(), start.y()+45), 25)
        QTest.mouseMove(self.case.window, start, 25)
        QTest.mouseRelease(self.case.window, Qt.LeftButton, Qt.NoModifier, start)
        self.settle(True)
        self.assertEqual('camera', self.case.shell.currentApp)
        self.untouched()

    def test_vertical_icon_drag_scrolls_to_all_seven_64px_targets_without_navigation(self):
        self.launch()
        photo=self.case.item('taskbar-photos');viewport=photo.parentItem()
        while viewport is not None and viewport.property('contentY') is None:viewport=viewport.parentItem()
        self.assertIsNotNone(viewport,'64px icon list must provide a scroll viewport')
        names=('camera','photos','recipes','settings','devices','diagnostics','system')
        def reachable():
            result=set()
            for name in names:
                button=self.case.item('taskbar-'+name);top=button.mapToItem(viewport,QPointF(0,0)).y()
                self.assertGreaterEqual(button.width(),64);self.assertGreaterEqual(button.height(),64)
                if top>=-1 and top+button.height()<=viewport.height()+1:result.add(name)
            return result
        seen=reachable();start=self.scene(photo)
        self.drag(start,QPoint(start.x(),3),steps=10)
        self.case.wait(lambda:not bool(viewport.property('moving')))
        self.assertGreater(float(viewport.property('contentY')),60,'vertical icon drag did not scroll')
        seen.update(reachable());self.assertEqual(set(names),seen,'some icon targets need wheel-only access')
        recipe=self.case.item('taskbar-recipes');start=self.scene(recipe)
        self.drag(start,QPoint(start.x(),start.y()+160),steps=10)
        self.case.wait(lambda:not bool(viewport.property('moving')))
        self.assertAlmostEqual(0,float(viewport.property('contentY')),delta=1)
        self.assertEqual('camera',self.case.shell.currentApp)
        self.untouched()

    def test_animation_preserves_proportional_preview_and_accepts_one_hardware_shutter(self):
        self.launch()
        expanded_size = self.assert_fit()
        # Image.Ready describes the source, not completion of the first UI
        # render. A user gestures on a drawn interface. Also keep tree walks
        # out of the timed observer so the observer does not stall that UI.
        area = self.case.item('viewfinderArea')
        shutter = self.case.item('shootButton')
        collapse = self.case.item('taskbarCollapseButton')
        first_frame = self.case.window.contentItem().grabToImage()
        self.assertIsNotNone(first_frame)
        frame_ready = []
        first_frame.ready.connect(lambda: frame_ready.append(True))
        self.case.wait(lambda: bool(frame_ready))
        self.assertFalse(first_frame.image().isNull())
        self.assertEqual((800,480), (first_frame.image().width(),first_frame.image().height()))
        samples = []
        sent_space = []
        timer = QTimer()
        timer.setInterval(8)
        def sample():
            progress = self.progress()
            point = area.mapToScene(QPointF(0,0))
            samples.append((progress, area.width(), area.height(), point.x(),
                            shutter.isEnabled(),self.case.controller.canCapture))
            if 0.05 < progress < 0.95 and not sent_space:
                sent_space.append(True)
                self.case.camera_input.dispatch('shutter',True)
                self.case.camera_input.dispatch('shutter',True)
        timer.timeout.connect(sample)
        timer.start()
        try:
            QTest.mouseClick(self.case.window, Qt.LeftButton, Qt.NoModifier, self.scene(collapse))
            self.settle(False)
        finally:
            timer.stop()
            self.case.camera_input.dispatch('shutter',False)
        middle = [row for row in samples if 0.001 < row[0] < 0.999]
        self.assertTrue(middle, 'taskbar jumped directly between endpoints without animation')
        self.assertTrue(sent_space, 'no animated frame was available for the transition shutter guard')
        width = self.case.window.width()
        available_height = self.case.window.height()-self.case.item('shootingStatusBar').height()
        for progress, area_width, area_height, scene_x, shutter_enabled, can_capture in middle:
            inset = 72*progress
            self.assertAlmostEqual(width-inset, area_width, delta=1)
            self.assertAlmostEqual(inset, scene_x, delta=1)
            self.assertAlmostEqual(area_width/width, area_height/available_height, delta=0.002)
            if can_capture:self.assertTrue(shutter_enabled,'animation blocked a ready camera shutter')
        collapsed_size = self.assert_fit()
        self.assertGreater(collapsed_size[0], expanded_size[0])
        self.assertGreater(collapsed_size[1], expanded_size[1])
        self.assertAlmostEqual(expanded_size[0]/collapsed_size[0],
                               expanded_size[1]/collapsed_size[1], places=3)
        self.case.wait(lambda:self.case.controller.captureState=='complete')
        identity=self.case.controller.lastRequestId
        self.assertTrue(identity)
        self.assertEqual([(identity,'complete')],self.case.capture_rows(),'repeated down during animation took another photograph')
        self.assertEqual(self.before_settings,self.case.controller._client.get_state()['settings'])
        self.assertEqual([],self.case.warnings)

    def test_icon_navigation_and_keyboard_activation_never_become_a_global_shutter(self):
        self.launch()
        for app in ('settings','photos','recipes','devices','diagnostics','system'):
            name = 'taskbar-'+app
            self.case.click(name)
            self.case.wait(lambda: self.case.shell.currentApp == app)
            self.case.item(name).forceActiveFocus()
            for key in (Qt.Key_Space, Qt.Key_Return):
                QTest.keyClick(self.case.window, key)
            self.assertEqual(app, self.case.shell.currentApp)
            self.assertEqual([], self.case.capture_rows(), app+' icon keys took a photograph')
        # Returning to the shooting screen makes Space a deliberate shutter
        # shortcut again; the icon tap itself must never submit a photograph.
        self.case.click('taskbar-camera')
        self.case.wait(lambda:self.case.window.property('currentScreen')=='capture')
        self.untouched()

    def test_unknown_request_stays_held_across_taskbar_gestures_and_app_switching(self):
        original = str(uuid.uuid4())
        controller = self.launch(saved_request=original)
        self.assertEqual('unknown', controller.captureState)
        self.hide_by_swipe()
        self.reveal_by_swipe()
        self.case.click('taskbar-diagnostics')
        self.case.wait(lambda: self.case.shell.currentApp == 'diagnostics')
        self.case.click('taskbar-camera')
        self.case.wait(lambda: self.case.window.property('currentScreen') == 'capture')
        self.assertFalse(self.case.item('shootButton').isEnabled())
        self.case.click('queryOriginalButton')
        self.case.wait(lambda: controller._pending == 0)
        self.assertEqual('unknown', controller.captureState)
        self.assertEqual(original, controller.lastRequestId)
        QTest.keyClick(self.case.window, Qt.Key_Space)
        self.untouched()

    def test_repeated_close_reveal_and_window_resize_settle_without_stale_geometry(self):
        self.launch()
        for _ in range(3):
            self.raw_click('taskbarCollapseButton')
            # The left reveal control must work while the closing animation
            # is still in flight; it must not activate an underlying app.
            QTest.qWait(35)
            self.raw_click('taskbarRevealHandle')
            self.settle(True)
            self.assertEqual('camera', self.case.shell.currentApp)
        self.case.window.resize(1280,800)
        QTest.qWait(60)
        self.settle(True)
        self.assert_fit()
        area = self.case.item('viewfinderArea')
        self.assertAlmostEqual(1208, area.width(), delta=1)
        self.hide_by_swipe()
        self.assert_fit()
        self.assertAlmostEqual(1280, area.width(), delta=1)
        self.reveal_by_swipe()
        self.assert_fit()
        self.untouched()


if __name__ == '__main__':
    unittest.main()

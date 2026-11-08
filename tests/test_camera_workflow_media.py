"""Real local authority handoff, UUID preservation and reversible media edits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
if os.name == 'nt': os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
try:
    from PyQt5.QtCore import QObject, QPoint, QPointF, Qt, QUrl, QTimer, QMetaObject
    from PyQt5.QtGui import QGuiApplication, QImage, QFont
    from PyQt5.QtQml import QQmlApplicationEngine
    from PyQt5.QtQuick import QQuickItem
    from PyQt5.QtTest import QTest
except ImportError:
    raise unittest.SkipTest('optional PyQt5 is unavailable')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ui' / 'on-device'))
from apps_controller import CameraAppsController
from controller import CameraController, make_client_factory
from media_library import MediaStore
from image_pipeline import FrameImageProvider
from camera.camerad.service import CameraService, CameraError


class WorkflowMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setFont(QFont('Microsoft YaHei UI' if os.name == 'nt' else 'sans-serif', 11))
        from ui_palette import apply_monochrome_palette
        apply_monochrome_palette(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.camera = CameraController(make_client_factory('reference', self.root / 'camera'),
                                       backend='reference', state_path=self.root/'ui.json')
        self.apps = CameraAppsController(self.camera, self.root / 'state')
        self.wait(lambda: self.camera.connectionState == 'ready' and self.apps.workflow.available)

    def tearDown(self):
        self.apps.close()
        if not self.camera._closed: self.wait(lambda: not self.camera.workflowActive)
        self.camera.shutdown()
        QTest.qWait(50)
        self.temp.cleanup()

    def wait(self, condition, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition(): return
            QTest.qWait(10)
        self.fail('timed out: ' + self.camera.statusText + '; ' + self.apps.workflow.errorText)

    def run_recipe_when_ready(self):
        self.wait(lambda: self.apps.workflow.canStart)
        self.assertTrue(self.apps.workflow.canStart)
        self.apps.runSelectedRecipe()

    def test_pause_resume_keeps_committed_ids_and_restores_ui_control(self):
        self.apps.saveCaptureRecipe('Interval', 'interval', 3, 500)
        self.run_recipe_when_ready()
        self.wait(lambda: self.apps.workflow.completed >= 1 and self.camera.workflowActive)
        self.apps.workflow.pause()
        self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'paused' and self.apps.workflow.canResume)
        before = [row['requestId'] for row in self.apps.workflow.steps]
        self.assertTrue(before)
        self.assertTrue(self.apps.workflow.canResume)
        self.assertTrue(self.apps.workflow.resume())
        self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'completed')
        after = [row['requestId'] for row in self.apps.workflow.steps]
        self.assertEqual(before, after[:len(before)])
        self.assertEqual(3, len(after))
        self.assertEqual(3, len(set(after)))
        self.assertTrue(all(Path(row['filePath']).is_file() for row in self.apps.workflow.steps))
        self.assertTrue(self.camera.canCapture)

    def test_pending_state_rpc_refuses_workflow_actions_without_changing_paused_ids(self):
        entered, release = threading.Event(), threading.Event()
        injected = False
        def after_paused(result, error):
            nonlocal injected
            if injected or not isinstance(result, dict) or result.get('status') != 'paused':
                return
            injected = True
            def pending_state():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('private state-RPC fixture gate was not released')
                time.sleep(.15)
                return self.camera._read_state()
            # An actual controller RPC remains in flight at the handoff boundary.
            self.camera._submit('state', pending_state)
        self.camera.workflowFinished.connect(after_paused)
        self.apps.saveCaptureRecipe('Pending state interval', 'interval', 3, 500)
        self.run_recipe_when_ready()
        self.wait(lambda: self.apps.workflow.completed >= 1 and self.camera.workflowActive)
        self.apps.workflow.pause()
        try:
            self.wait(lambda: entered.is_set() and not self.camera.workflowActive and self.apps.workflow.status == 'paused')
            workflow = self.apps.workflow
            before = [row['requestId'] for row in workflow.steps]
            identity, desired, cancel_event = workflow.jobId, workflow.desired, workflow._cancel
            self.assertTrue(before)
            self.assertTrue(self.camera.canCapture)
            self.assertFalse(self.camera.canRunExclusiveWorkflow())
            self.assertFalse(workflow.canStart)
            self.assertFalse(workflow.canResume)
            self.assertFalse(workflow.canCancel)
            from daos.capability_runtime.bracket import BracketRunner
            journal = BracketRunner(None, self.root/'state/apps/workflows.sqlite3')
            checkpoint = journal.inspect(identity)
            with patch.object(self.camera._client, 'capture', wraps=self.camera._client.capture) as submissions:
                self.assertFalse(workflow.start(checkpoint['plan']))
                self.assertFalse(workflow.resume())
                self.assertFalse(workflow.cancel())
                self.assertEqual('paused', workflow.status)
                self.assertEqual(identity, workflow.jobId)
                self.assertEqual(desired, workflow.desired)
                self.assertIs(cancel_event, workflow._cancel)
                self.assertFalse(cancel_event.is_set())
                self.assertEqual(before, [row['requestId'] for row in workflow.steps])
                persisted = journal.inspect(identity)
                self.assertEqual('paused', persisted['status'])
                self.assertEqual([row['request_id'] for row in checkpoint['steps']], [row['request_id'] for row in persisted['steps']])
                submissions.assert_not_called()
                release.set()
                self.wait(lambda: workflow.canResume)
                QTest.qWait(450)  # Include the workflow's periodic refresh; no automatic retry.
                self.assertFalse(self.camera.workflowActive)
                self.assertEqual('paused', workflow.status)
                self.assertEqual(before, [row['requestId'] for row in workflow.steps])
                submissions.assert_not_called()
                self.wait(lambda: workflow.canResume)
                self.assertTrue(workflow.resume())
                self.wait(lambda: not self.camera.workflowActive and workflow.status == 'completed')
                after = [row['requestId'] for row in workflow.steps]
                self.assertEqual(before, after[:len(before)])
                self.assertEqual(3, len(after))
                self.assertEqual(3, len(set(after)))
                self.assertEqual(3-len(before), submissions.call_count)
                self.assertTrue(all(Path(row['filePath']).is_file() for row in workflow.steps))
        finally:
            release.set()

    def test_cancel_stops_unsubmitted_shots_and_journal_reopens(self):
        self.apps.saveCaptureRecipe('Cancel interval', 'interval', 5, 1000)
        self.run_recipe_when_ready()
        self.wait(lambda: self.apps.workflow.completed >= 1 and self.camera.workflowActive)
        identity = self.apps.workflow.jobId
        self.apps.workflow.cancel()
        self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'cancelled')
        saved_ids = [row['requestId'] for row in self.apps.workflow.steps]
        self.assertLess(self.apps.workflow.completed, 5)
        self.apps.close()
        self.apps = CameraAppsController(self.camera, self.root / 'state')
        self.wait(lambda: self.apps.workflow.available and self.apps.workflow.jobId == identity)
        self.assertEqual('cancelled', self.apps.workflow.status)
        self.assertEqual(saved_ids, [row['requestId'] for row in self.apps.workflow.steps])

    def test_lost_reply_then_temporary_query_failure_resumes_without_recapture(self):
        self.apps.close(); self.camera.shutdown()
        class LostReply(CameraService):
            def __init__(self, *args):
                super().__init__(*args); self.hide_query = False; self.submissions = []
            def capture(self, request):
                self.submissions.append(request['request_id'])
                record = super().capture(request)
                deadline = time.monotonic() + 5
                while record['status'] not in {'complete','failed','cancelled','unknown'}:
                    if time.monotonic() >= deadline: raise AssertionError('fixture capture timed out')
                    time.sleep(.01); record = super().query_capture(request['request_id'])
                self.hide_query = True
                raise CameraError('outcome_unknown', 'capture reply lost in fixture')
            def query_capture(self, request_id):
                if self.hide_query: raise CameraError('unavailable', 'temporary fixture query failure')
                return super().query_capture(request_id)
        service = LostReply(self.root/'uncertain-camera')
        self.camera = CameraController(lambda: service, backend='reference', state_path=self.root/'uncertain-ui.json')
        self.apps = CameraAppsController(self.camera, self.root/'uncertain-state')
        self.wait(lambda: self.camera.connectionState == 'ready' and self.apps.workflow.available)
        self.apps.saveCaptureRecipe('Lost reply', 'interval', 1, 0)
        self.run_recipe_when_ready()
        self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'unknown')
        identity = self.apps.workflow.steps[0]['requestId']
        self.assertEqual('unknown', self.camera.captureState)
        self.assertEqual(identity, self.camera.lastRequestId)
        self.assertFalse(self.camera.canCapture)
        self.assertEqual([identity], service.submissions)
        service.hide_query = False
        self.wait(lambda: self.apps.workflow.canResume)
        self.assertTrue(self.apps.workflow.resume())
        self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'completed')
        self.assertEqual([identity], service.submissions)
        self.assertEqual(identity, self.apps.workflow.steps[0]['requestId'])
        self.assertTrue(self.camera.canCapture)

    def test_shutdown_cancels_active_long_interval_without_more_exposures(self):
        self.apps.saveCaptureRecipe('Long interval', 'interval', 100, 60000)
        self.run_recipe_when_ready()
        self.wait(lambda: self.apps.workflow.completed == 1 and self.camera.workflowActive)
        identity = self.apps.workflow.jobId
        start = time.monotonic()
        self.camera.shutdown()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3)
        self.assertTrue(self.apps._closed)
        from daos.capability_runtime.bracket import BracketRunner
        record = BracketRunner(None, self.root/'state/apps/workflows.sqlite3').inspect(identity)
        self.assertEqual('cancelled', record['status'])
        self.assertEqual(1, len(record['steps']))
        self.assertEqual('completed', record['steps'][0]['status'])

    def test_rating_protection_trash_and_non_destructive_proof_export(self):
        self.camera.shutter()
        self.wait(lambda: self.camera.captureState == 'complete')
        original = Path(self.camera.dngPath)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        self.camera.refreshLibrary()
        self.wait(lambda: not self.camera.libraryLoading and bool(self.camera.libraryItems))
        identity = self.camera.libraryItems[0]['request_id']
        self.camera.selectCapture(identity)
        self.wait(lambda: not self.camera.libraryLoading and bool(self.camera.reviewUrl))
        media = self.apps.media
        self.wait(lambda: media.ready)
        media.setRating(4); self.wait(lambda: not media.busy and media.rating == 4)
        media.setProtected(True); self.wait(lambda: not media.busy and media.protected)
        media.moveToTrash()
        self.assertFalse(media.trashed)
        self.assertIn('Protected', media.message)
        media.setProtected(False); self.wait(lambda: not media.busy and not media.protected)
        media.moveToTrash(); self.wait(lambda: not media.busy and media.trashed); media.setFilter('trash')
        self.assertEqual(identity, media.items[0]['request_id'])
        media.restore(); self.wait(lambda: not media.busy and not media.trashed); media.setFilter('rated')
        self.assertEqual(4, media.rating)
        media.setEdit('exposure', .5)
        self.wait(lambda: not media.busy and MediaStore(self.root/'state/apps/media').read(identity)['edits']['exposure'] == .5)
        media.rotate()
        self.wait(lambda: (MediaStore(self.root/'state/apps/media').read(identity)['edits']['rotation'] == 90))
        self.wait(lambda: not media.busy)
        media.setCrop('1:1')
        self.wait(lambda: not media.busy and MediaStore(self.root/'state/apps/media').read(identity)['edits']['crop'] is not None)
        self.wait(lambda: bool(media.previewUrl))
        sidecar = MediaStore(self.root/'state/apps/media').read(identity)
        self.assertEqual(.5, sidecar['edits']['exposure'])
        self.assertEqual(90, sidecar['edits']['rotation'])
        destination = self.root/'proof.jpg'
        media.exportJpeg(str(destination))
        self.wait(lambda: destination.is_file())
        image = QImage(str(destination))
        self.assertFalse(image.isNull())
        self.assertLessEqual(max(image.width(), image.height()), 2048)
        self.assertEqual(image.width(), image.height())
        self.assertEqual(digest, hashlib.sha256(original.read_bytes()).hexdigest())
        self.wait(lambda: not media._export_pending)
        media.exportJpeg(str(destination))
        self.wait(lambda: 'failed' in media.message)
        self.assertEqual(digest, hashlib.sha256(original.read_bytes()).hexdigest())

    def test_slow_sidecar_write_keeps_qt_running_and_failure_keeps_prior_state(self):
        self.camera.shutter(); self.wait(lambda: self.camera.captureState == 'complete')
        self.camera.refreshLibrary(); self.wait(lambda: not self.camera.libraryLoading and bool(self.camera.libraryItems))
        self.camera.selectCapture(self.camera.libraryItems[0]['request_id'])
        media = self.apps.media
        self.wait(lambda: media.ready)
        entered, release = threading.Event(), threading.Event()
        actual_write = media._store.write
        def slow_write(*args):
            entered.set(); release.wait(3)
            return actual_write(*args)
        ticks = []
        timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(time.monotonic())); timer.start()
        try:
            with patch.object(media._store, 'write', side_effect=slow_write):
                start = time.monotonic(); media.setRating(5)
                self.assertLess(time.monotonic()-start, .1)
                self.wait(entered.is_set)
                QTest.qWait(130)
                self.assertTrue(media.busy)
                self.assertEqual(0, media.rating)
                self.assertGreaterEqual(len(ticks), 5)
                media.setProtected(True)  # Not queued while the first write is unconfirmed.
                self.assertFalse(media.protected)
                release.set()
                self.wait(lambda: not media.busy and media.rating == 5)
            with patch.object(media._store, 'write', side_effect=OSError('fixture full media')):
                media.setRating(2)
                self.wait(lambda: not media.busy)
                self.assertEqual(5, media.rating)
                self.assertIn('保存失败', media.message)
            render_gate = threading.Event()
            media._render_executor.submit(render_gate.wait, 3)
            try:
                media.setProtected(True)
                self.wait(lambda: not media.busy and media.protected)
            finally: render_gate.set()
        finally:
            release.set(); timer.stop()

    def test_touch_workflow_and_playback_views_at_800x480(self):
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('camera', self.camera)
        engine.rootContext().setContextProperty('cameraApps', self.apps)
        engine.addImageProvider('daosframes', FrameImageProvider(self.camera.frames))
        errors = []
        engine.warnings.connect(lambda warnings: errors.extend(w.description() for w in warnings))
        qml = b'''import QtQuick 2.15
import QtQuick.Controls 2.15
ApplicationWindow { width: 800; height: 480; visible: true
    RecipeWorkspace { id: plans; anchors.fill: parent; cameraController: camera; apps: cameraApps }
    CameraPlayback { id: photos; objectName: "photoScene"; anchors.fill: parent; cameraController: camera; apps: cameraApps; visible: false }
    CapabilityPage { objectName: "capabilityScene"; anchors.fill: parent; cameraController: camera; visible: false }
}'''
        engine.loadData(qml, QUrl.fromLocalFile(str(ROOT/'ui/on-device/MediaReviewHarness.qml')))
        self.assertTrue(engine.rootObjects(), errors)
        window = engine.rootObjects()[0]
        def click(name):
            button = window.findChild(QQuickItem, name)
            self.assertIsNotNone(button, name)
            point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
            self.assertGreaterEqual(point.x(), 0); self.assertLess(point.x(), 800)
            self.assertGreaterEqual(point.y(), 0); self.assertLess(point.y(), 480)
            QTest.mouseClick(window, Qt.LeftButton, pos=point); QTest.qWait(30)
        artifact = ROOT/'build/DaOS_Camera_Experience_001'
        artifact.mkdir(parents=True, exist_ok=True)
        def screenshot(name):
            self.app.processEvents(); QTest.qWait(60)
            grab = window.contentItem().grabToImage()
            self.assertIsNotNone(grab)
            self.wait(lambda: not grab.image().isNull())
            self.assertTrue(grab.image().save(str(artifact/name)))
        try:
            QTest.qWait(60)
            click('workflowNewPlanButton')
            screenshot('workflow-plan-editor.png')
            field = window.findChild(QObject, 'appRecipeNameInput')
            field.setProperty('text', '晚霞包围')
            click('appRecipeSaveButton')
            self.assertEqual(1, len(self.apps.recipes))
            click('workflowRunButton')
            self.wait(lambda: not self.camera.workflowActive and self.apps.workflow.status == 'completed')
            screenshot('workflow-completed-ui.png')
            self.camera.refreshLibrary()
            self.wait(lambda: not self.camera.libraryLoading and len(self.camera.libraryItems) == 3)
            self.camera.selectCapture(self.camera.libraryItems[0]['request_id'])
            self.wait(lambda: not self.camera.libraryLoading and bool(self.camera.reviewOriginalUrl))
            scene = window.findChild(QQuickItem, 'photoScene'); scene.setVisible(True)
            QTest.qWait(80)
            screenshot('playback-fit-ui.png')
            click('playbackZoomButton')
            self.wait(lambda: bool(self.camera.reviewTileUrl))
            screenshot('playback-original-100pct.png')
            click('playbackZoomButton')
            click('playbackEditButton')
            screenshot('playback-edit-ui.png')
            scene.closeOverlay()
            click('playbackRateButton')
            screenshot('playback-rate-ui.png')
            scene.closeOverlay()
            capabilities = window.findChild(QQuickItem, 'capabilityScene'); capabilities.setVisible(True)
            screenshot('capabilities-ui.png')
            capabilities.setProperty('performanceTab', True)
            screenshot('performance-empty-samples-ui.png')
            self.assertEqual([], errors)
        finally:
            window.close(); engine.deleteLater(); QTest.qWait(30)

    def test_whole_library_real_editor_styles_white_balance_curve_and_tiles(self):
        binary=ROOT/'build/DaOS_Camera_004/processing/worker-build/Release/hf-imager.exe'
        if not binary.exists():self.skipTest('native image worker not built')
        self.apps.saveCaptureRecipe('图库编辑', 'interval', 2, 100)
        self.run_recipe_when_ready();self.wait(lambda:not self.camera.workflowActive and self.apps.workflow.status=='completed')
        gallery=self.apps.media.gallery;gallery.refresh();self.wait(lambda:gallery.totalCount==2 and len(gallery.items)==2)
        engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('cameraApps',self.apps)
        from soft_keyboard import SoftKeyboardController
        keyboard=SoftKeyboardController(self.app);engine.rootContext().setContextProperty('softKeyboard',keyboard)
        engine.addImageProvider('daosmedia',gallery.imageProvider);errors=[]
        engine.warnings.connect(lambda warnings:errors.extend(w.description()for w in warnings))
        engine.loadData(b'''import QtQuick 2.15
import QtQuick.Controls 2.15
MonochromeWindow { width:800;height:480;visible:true
MediaGallery { objectName:"testGallery";anchors.fill:parent;gallery:cameraApps.media.gallery;taskService:cameraApps.tasks }
SoftKeyboard { parent:Overlay.overlay;anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom }
}''',QUrl.fromLocalFile(str(ROOT/'ui/on-device/Gallery004Harness.qml')))
        self.assertTrue(engine.rootObjects(),errors);window=engine.rootObjects()[0];page=window.findChild(QQuickItem,'testGallery')
        artifacts=ROOT/'build/DaOS_Camera_004';artifacts.mkdir(exist_ok=True,parents=True)
        def click(name):
            item=window.findChild(QQuickItem,name)
            def visual(parent):
                if parent.objectName()==name:return parent
                for child in parent.childItems():
                    found=visual(child)
                    if found:return found
                return None
            item=item or visual(window.contentItem());self.assertIsNotNone(item,name)
            point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
            self.assertTrue(0<=point.x()<800 and 0<=point.y()<480,(name,point))
            QTest.mouseClick(window,Qt.LeftButton,pos=point);QTest.qWait(50)
        def screenshot(name):
            grab=window.contentItem().grabToImage();self.wait(lambda:not grab.image().isNull());self.assertTrue(grab.image().save(str(artifacts/name)))
            if 'keyboard'in name or name=='004-curve-editor.png':
                # Opaque editor panel contains no photo pixels: focus rings,
                # selections and slider accents must all stay neutral.
                image=grab.image()
                for y in range(0,210):
                    for x in range(442,800):
                        color=image.pixelColor(x,y)
                        self.assertLessEqual(max(color.red(),color.green(),color.blue())-min(color.red(),color.green(),color.blue()),2,(name,x,y))
        try:
            self.wait(lambda:any(item.get('thumbUrl')for item in gallery.items),20);QTest.qWait(100);screenshot('004-library-wall.png')
            click('galleryPhotoCell0');self.wait(lambda:bool(gallery.reviewUrl),20)
            original=self.camera._client.get_library_asset(gallery.currentId)
            with self.camera._client.open_library_asset(gallery.currentId)as stream:before=hashlib.file_digest(stream,'sha256').hexdigest()
            click('galleryEditOrSelectButton');click('galleryEditorTabwhiteBalance');screenshot('004-white-balance-editor.png')
            click('galleryWhiteBalancePicker');self.assertTrue(page.property('whiteBalancePicker'))
            QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(390,210));self.wait(lambda:'wb'in gallery.edits,20)
            self.assertTrue(all(.1<=gain<=8 for gain in gallery.edits['wb']))
            gallery.setWhiteBalanceChannel(0,1.25);gallery.setWhiteBalanceChannel(2,.8)
            click('galleryEditorTabcurve');gallery.setCurvePoint(1,.15);gallery.setCurvePoint(2,.65);gallery.setCurvePoint(3,.85)
            self.wait(lambda:not gallery._pending_ui_edits and not gallery._edit_inflight,20);screenshot('004-curve-editor.png')
            self.assertEqual([0,0],gallery.edits['curve'][0]);self.assertEqual([1,1],gallery.edits['curve'][-1])
            click('galleryEditorTabstyles');field=window.findChild(QQuickItem,'galleryStyleName');click('galleryStyleName')
            self.wait(lambda:keyboard.isOpen);QTest.qWait(120)
            editor_point=field.mapToScene(QPointF(0,0));self.assertGreaterEqual(field.height(),64);self.assertLessEqual(editor_point.y()+field.height(),228)
            screenshot('004-style-keyboard-avoidance.png')
            field.setProperty('text','灰点曲线风格');QMetaObject.invokeMethod(field,'selectAll');QTest.qWait(50)
            screenshot('004-style-keyboard-selection.png');click('gallerySaveStyle');self.wait(lambda:not keyboard.isOpen)
            self.wait(lambda:any(style['name']=='灰点曲线风格'for style in gallery.styles))
            screenshot('004-custom-styles.png');style=next(style for style in gallery.styles if style['name']=='灰点曲线风格')
            gallery.setCrop('1:1');gallery.rotate();self.wait(lambda:not gallery._pending_ui_edits and not gallery._edit_inflight)
            geometry={key:gallery.edits.get(key)for key in ('crop','rotation')};gallery.setEdit('exposure',1);gallery.applyStyle(style['id'])
            self.wait(lambda:not gallery._pending_ui_edits and not gallery._edit_inflight)
            self.assertEqual(geometry,{key:gallery.edits.get(key)for key in ('crop','rotation')});self.assertEqual(1.25,gallery.edits['wb'][0])
            gallery.deleteStyle(style['id']);self.wait(lambda:not any(s['id']==style['id']for s in gallery.styles))
            click('galleryEditDone');click('galleryExportButton');click('galleryExportDirectory');self.wait(lambda:keyboard.isOpen);QTest.qWait(100)
            export_field=window.findChild(QQuickItem,'galleryExportDirectory');self.assertGreaterEqual(export_field.height(),64)
            self.assertLessEqual(export_field.mapToScene(QPointF(0,0)).y()+export_field.height(),228)
            screenshot('004-export-keyboard-avoidance.png');page.closeOverlays();self.wait(lambda:not keyboard.isOpen)
            gallery.loadTile(5,7,64,48);self.wait(lambda:bool(gallery.tileUrl),20)
            first=gallery.currentId;second=next(row['photo_uuid']for row in gallery.items if row['photo_uuid']!=first)
            gallery.setCompare(second);gallery.loadCompareTile(5,7,64,48);self.wait(lambda:bool(gallery.compareUrl),20)
            page.setProperty('compareMode',True);page.setProperty('nativeZoom',True);QTest.qWait(150);screenshot('004-original-dual-tiles.png')
            with self.camera._client.open_library_asset(first)as stream:self.assertEqual(before,hashlib.file_digest(stream,'sha256').hexdigest())
            self.assertEqual(original['original_request_uuid'],self.camera._client.get_library_asset(first)['original_request_uuid'])
            self.assertEqual([],errors)
        finally:
            keyboard.done();keyboard.timer.stop();self.app.removeEventFilter(keyboard);self.app.focusObjectChanged.disconnect(keyboard.refresh)
            window.close();engine.deleteLater();keyboard.deleteLater();QTest.qWait(100)


if __name__ == '__main__': unittest.main()

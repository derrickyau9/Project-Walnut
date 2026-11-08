"""Opt-in camera-first Qt review with exactly one new native HF-Sim photograph.

Run only after the UI revision is frozen and a planned review capture is
authorized. A new output directory is required. No shutter retry or unknown
outcome replacement exists. Live preview is the separate Python synthetic
Bayer PreviewStream, not a native sensor readout. Screenshots identify the
actual running host and do not certify a BSD/ARM64 image boot or real hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='new directory; existing reviews are never overwritten')
    parser.add_argument('--native-command', required=True, help='explicit current hf-camerad executable')
    parser.add_argument('--timeout-seconds', type=int, default=180, help='total bounded review deadline, 30..240')
    parser.add_argument('--stage-timeout-seconds', type=int, default=20, help='per-condition deadline, 5..20')
    args = parser.parse_args(argv)
    if not 30 <= args.timeout_seconds <= 240 or not 5 <= args.stage_timeout_seconds <= 20:
        parser.error('total timeout must be 30..240 and each stage timeout 5..20 seconds')
    native = Path(args.native_command).resolve(strict=True)
    if not native.is_file():
        parser.error('--native-command must identify an existing regular executable')
    output = Path(args.output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    if os.name == 'nt':
        os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(UI))
    from PyQt5 import QtCore, QtGui, QtQml, QtQuick, QtTest
    from camera.dng.inspect import inspect_dng
    from controller import CameraController, make_client_factory
    from shell_model import ShellController
    from apps_controller import CameraAppsController

    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    app.setFont(QtGui.QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif', 10))
    app.setApplicationName('DaOS Camera first review')
    app.setOrganizationName('HassleFree')
    controller = engine = window = None
    screenshots, warnings = [], []
    shutter_actions = 0
    global_deadline = time.monotonic() + args.timeout_seconds
    source_paths = sorted(path.relative_to(ROOT).as_posix() for path in UI.iterdir()
                          if path.is_file() and path.suffix in ('.py', '.qml', '.json'))
    source_paths.extend(('camera/camerad/native_client.py', 'camera/camerad/calibration_context.py',
                         'camera/pipeline/preview.py', 'simulation/bayer.py'))
    sources = {name: sha(ROOT/name) for name in source_paths}
    theme = json.loads((UI/'retail-theme.json').read_text(encoding='utf-8'))
    runtime_label = platform.system() + ' UI preview / ' + platform.machine()
    report = {
        'format': 'DaOS-camera-first-review-v1', 'passed': False, 'synthetic': True,
        'physical_camera_validated': False, 'physical_display_validated': False,
        'bsd_image_boot_validated': False, 'arm64_image_boot_validated': False,
        'capture_backend': 'C++ hf-camerad / HF-Sim',
        'preview_source': 'Python synthetic Bayer PreviewStream; separate scene, not sensor readout',
        'native_sha256': sha(native),
        'native_command': native.relative_to(ROOT).as_posix() if native.is_relative_to(ROOT) else native.name,
        'source_sha256': sources, 'screenshots': screenshots, 'qml_warnings': warnings,
        'runtime': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
                    'python': platform.python_version(), 'qt': QtCore.QT_VERSION_STR,
                    'pyqt': QtCore.PYQT_VERSION_STR, 'qpa': app.platformName(), 'ui_label': runtime_label},
    }

    def pump(milliseconds=50):
        QtTest.QTest.qWait(milliseconds)

    def wait(predicate, stage):
        print('DaOS_CAMERA_REVIEW_STAGE ' + stage, flush=True)
        deadline = min(global_deadline, time.monotonic() + args.stage_timeout_seconds)
        while time.monotonic() < deadline:
            if predicate():
                return
            pump(20)
        state = '' if controller is None else controller.statusText + '; ' + controller.errorText
        raise RuntimeError('review condition timed out: ' + stage + '; ' + state)

    def find(name):
        found = window.findChild(QtQuick.QQuickItem, name)
        if found is not None:
            return found

        def visual(parent):
            for child in parent.childItems():
                if child.objectName() == name:
                    return child
                value = visual(child)
                if value is not None:
                    return value
            return None
        return visual(window.contentItem())

    def item(name):
        wait(lambda: find(name) is not None, 'control ' + name)
        return find(name)

    def reveal(control):
        ancestor = control.parentItem()
        while ancestor is not None:
            if ancestor.property('contentY') is not None and ancestor.property('contentHeight') is not None:
                for _ in range(30):
                    top = control.mapToItem(ancestor, QtCore.QPointF()).y()
                    if top >= 0 and top + control.height() <= ancestor.height():
                        break
                    point = ancestor.mapToScene(QtCore.QPointF(ancestor.width()/2, ancestor.height()/2))
                    event = QtGui.QWheelEvent(point, point, QtCore.QPoint(),
                                             QtCore.QPoint(0, 360 if top < 0 else -360),
                                             QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
                    QtCore.QCoreApplication.sendEvent(window, event)
                    pump(40)
                wait(lambda: not bool(ancestor.property('moving')), 'scroll stopped')
            ancestor = ancestor.parentItem()

    def click(name):
        nonlocal shutter_actions
        control = item(name)
        reveal(control)
        if not control.isVisible() or not control.isEnabled():
            raise RuntimeError('unavailable review control: ' + name)
        point = control.mapToScene(QtCore.QPointF(control.width()/2, control.height()/2))
        if not 0 <= point.x() < window.width() or not 0 <= point.y() < window.height():
            raise RuntimeError('review control is outside the viewport: ' + name)
        if name == 'shootButton':
            if shutter_actions:
                raise RuntimeError('a second shutter dispatch is forbidden')
            shutter_actions += 1
        QtTest.QTest.mouseClick(window, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                               QtCore.QPoint(round(point.x()), round(point.y())))
        pump(80)

    def screenshot(name, description):
        pump(120)
        result = window.contentItem().grabToImage()
        if result is None:
            raise RuntimeError('Qt rendered-image grab is unavailable')
        ready = []
        result.ready.connect(lambda: ready.append(True))
        wait(lambda: bool(ready), 'render ' + name)
        image, path = result.image(), output/name
        if path.exists() or image.isNull() or not image.save(str(path)):
            raise RuntimeError('review PNG creation failed: ' + name)
        screenshots.append({'file': name, 'width': image.width(), 'height': image.height(),
                            'sha256': sha(path), 'description': description})

    def type_text(name, value):
        control = item(name)
        reveal(control)
        control.forceActiveFocus()
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        for character in value:
            for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                QtCore.QCoreApplication.sendEvent(window, QtGui.QKeyEvent(kind, 0, QtCore.Qt.NoModifier, character))
        pump(30)
        if control.property('text') != value:
            raise RuntimeError('review text did not reach ' + name)

    def return_to_capture():
        for _ in range(3):
            if window.property('currentScreen') == 'capture':
                return
            QtTest.QTest.keyClick(window, QtCore.Qt.Key_Escape)
            pump(100)
        wait(lambda: window.property('currentScreen') == 'capture', 'return to shooting')

    try:
        session = output/'session'
        controller = CameraController(make_client_factory('native', session, command=[str(native)]),
                                      backend='native', state_path=output/'device-ui.json', poll_ms=100)
        shell = ShellController(output/'shell-preferences.json', parent=app)
        if not shell.setPreference('theme', 'dark'):
            raise RuntimeError('fresh camera theme could not match the camera launcher default')
        apps = CameraAppsController(controller, output, parent=app)
        engine = QtQml.QQmlApplicationEngine()
        engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
        for name, value in (('camera', controller), ('cameraShell', shell), ('cameraApps', apps),
                            ('retailTheme', theme), ('runtimeOSLabel', runtime_label)):
            engine.rootContext().setContextProperty(name, value)
        engine.load(QtCore.QUrl.fromLocalFile(str(UI/'Main.qml')))
        if len(engine.rootObjects()) != 1:
            raise RuntimeError('camera-first Main.qml did not create a window: ' + '; '.join(warnings))
        window = engine.rootObjects()[0]
        if window.objectName() != 'cameraFirstWindow':
            raise RuntimeError('this review requires the R12 camera-first window, not the earlier desktop console')
        window.resize(800, 600)
        window.requestActivate()
        pump(100)
        wait(lambda: window.isActive(), 'actual window active')
        wait(lambda: controller.connectionState == 'ready' and controller.canCapture, 'native authority ready')
        wait(lambda: controller._pending == 0, 'initial native queue complete')
        initial = controller._client.get_state()
        if initial.get('settings_persistent') is not True or initial.get('settings_format') != 'hf-device-settings-v1':
            raise RuntimeError('native executable does not confirm the current persistent-settings contract; no shutter taken')
        if controller.lastRequestId:
            raise RuntimeError('fresh review unexpectedly contains an old request; no new shutter action allowed')
        restored_shell = ShellController(output/'shell-preferences.json')
        restored_preferences = restored_shell.preferences
        restored_shell.deleteLater()
        resolved_theme = window.property('menuTheme')
        if hasattr(resolved_theme, 'toVariant'):
            resolved_theme = resolved_theme.toVariant()
        if (shell.theme != 'dark' or restored_preferences != shell.preferences
                or not isinstance(resolved_theme, dict)
                or resolved_theme.get('background') != theme['dark']['background']):
            raise RuntimeError('camera theme was not applied in QML and preserved across model recreation')
        report['preferences'] = shell.preferences
        report['preferences_recreated'] = restored_preferences
        report['qml_theme_background'] = resolved_theme['background']
        wait(lambda: window.property('currentScreen') == 'capture' and bool(controller.previewUrl),
             'automatic Python HF-Sim preview')
        screenshot('01-live-800.png', 'Actual 800 by 600 camera-first live composition; Python HF-Sim synthetic preview.')

        click('quickButton')
        combo = item('qExposureCombo')
        combo.forceActiveFocus()
        before = controller.exposureUs
        direction = QtCore.Qt.Key_Down if combo.property('currentIndex') < combo.property('count') - 1 else QtCore.Qt.Key_Up
        QtTest.QTest.keyClick(window, direction)
        wait(lambda: controller.exposureUs != before and controller.canCapture, 'Q exposure confirmed')
        gain = item('qGainInput')
        gain.forceActiveFocus()
        before_gain = controller.analogueGain
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_Up)
        wait(lambda: controller.analogueGain > before_gain and controller.canCapture, 'Q gain confirmed')
        screenshot('02-quick-800.png', 'Quick controls over the camera scene; requested exposure and gain confirmed by the native authority.')
        click('qCloseButton')
        wait(lambda: window.property('currentScreen') == 'capture', 'quick controls closed')

        click('menuButton')
        wait(lambda: window.property('currentScreen') == 'menu', 'camera MENU')
        click('menuCategory-exposure')
        screenshot('03-menu-exposure-800.png', 'Grouped camera exposure settings; no desktop navigation dock.')
        click('menuCategory-display')
        screenshot('04-menu-display-800.png', 'Camera display preferences inside MENU, separate from captured RAW.')
        click('menuCategory-tools')
        screenshot('05-menu-tools-800.png', 'Optional local apps are behind MENU rather than on the shooting screen.')
        return_to_capture()
        wait(lambda: controller.canCapture and bool(controller.previewUrl), 'shooting restored after MENU')
        wait(lambda: controller._pending == 0, 'capture settings queue complete')
        requested = controller._client.get_state()['settings']
        write_new(output/'shutter-intent.json', {
            'format': 'DaOS-camera-first-one-shutter-intent-v1', 'maximum_shutter_actions': 1,
            'native_sha256': report['native_sha256'], 'requested_settings': requested,
            'instruction': 'Retain the original UUID after uncertainty; never retry this shutter or rerun this directory.'})
        click('shootButton')
        wait(lambda: controller.captureState in ('complete', 'failed', 'cancelled', 'unknown'), 'original capture terminal state')
        request_id = controller.lastRequestId
        report['request_id'] = request_id
        if controller.captureState != 'complete' or str(uuid.UUID(request_id)) != request_id:
            raise RuntimeError('original capture was not confirmed complete; preserve and query that UUID without another shutter')
        screenshot('06-saved-800.png', 'Exactly one completed native HF-Sim DNG; shooting face remains free of paths and request logs.')
        click('playbackButton')
        wait(lambda: window.property('currentScreen') == 'playback', 'single-image playback')
        wait(lambda: not controller.libraryLoading and len(controller.libraryItems) == 1, 'original retained capture listed')
        # Select through the public read-only UI model if playback did not auto-select
        # the sole retained photograph. This does not submit or retry a capture.
        if controller.selectedCaptureId != request_id:
            controller.selectCapture(request_id)
        wait(lambda: controller.selectedCaptureId == request_id and bool(controller.reviewUrl)
             and not controller.libraryLoading, 'original retained DNG display conversion')
        screenshot('07-playback-800.png', 'Actual single-image playback decoded from the retained DNG, not the independent live preview.')
        exported = output/'export.dng'
        click('playbackExportButton')
        type_text('playbackExportPathInput', str(exported))
        click('playbackExportConfirmButton')
        wait(lambda: exported.is_file() and not controller.libraryLoading, 'original retained DNG export')
        return_to_capture()
        window.resize(1280, 800)
        wait(lambda: bool(controller.previewUrl), 'secondary development preview')
        screenshot('08-live-1280.png', 'Secondary actual 1280 by 800 host preview of the same camera-first layout.')

        wait(lambda: controller._pending == 0, 'native queue drained')
        record = controller._client.query_capture(request_id)
        state = controller._client.get_state()
        photo = Path(record['file_path'])
        metadata = inspect_dng(photo)
        with sqlite3.connect((session/'journal.sqlite3').as_uri() + '?mode=ro', uri=True) as journal:
            rows = journal.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()
        if (rows != [(request_id, 'complete')] or shutter_actions != 1 or controller.lastRequestId != request_id
                or metadata['request_id'] != request_id
                or sha(photo) != sha(exported)
                or record['actual_exposure_us'] != requested['requested_exposure_us']
                or record['actual_analogue_gain'] != requested['requested_analogue_gain']
                or state['settings'] != requested):
            raise RuntimeError('capture identity, one-shot journal count or requested/actual parameters changed')
        if warnings:
            raise RuntimeError('QML emitted warnings: ' + '; '.join(warnings))
        if {name: sha(ROOT/name) for name in source_paths} != sources:
            raise RuntimeError('UI source changed during review; screenshots do not identify one frozen revision')
        report.update({'passed': True, 'request_id': request_id, 'capture_id': record['capture_id'],
                       'frame_id': record['frame_id'], 'journal_rows': len(rows), 'settings': state['settings'],
                       'actual_exposure_us': record['actual_exposure_us'],
                       'actual_analogue_gain': record['actual_analogue_gain'], 'dng_sha256': sha(photo),
                       'export_dng_sha256': sha(exported),
                       'dng_metadata': metadata, 'initial_settings_persistent': initial['settings_persistent'],
                       'initial_settings_format': initial['settings_format'],
                       'unknown_capture_review': 'Not staged into this completed capture; recovery is covered by separate UI tests.'})
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        if controller is not None:
            report['request_id'], report['capture_state'] = controller.lastRequestId, controller.captureState
        raise
    finally:
        report['shutter_actions'] = shutter_actions
        if controller is not None:
            try:
                controller.shutdown()
            except Exception as error:
                report['passed'] = False
                report['native_shutdown_error'] = type(error).__name__ + ': ' + str(error)
            process = getattr(controller._client, 'process', None)
            report['native_process_closed'] = process is None or process.poll() is not None
            report['native_process_exit_code'] = None if process is None else process.poll()
            if report['native_process_closed'] is not True:
                report['passed'] = False
                report['error'] = 'native process did not close after review'
            elif report['native_process_exit_code'] not in (None, 0):
                report['passed'] = False
                report['native_shutdown_error'] = 'native process exited with code ' + str(report['native_process_exit_code'])
        write_new(output/'review.json', report)
        if engine is not None:
            engine.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    if report.get('native_process_closed') is not True or report.get('native_shutdown_error'):
        raise RuntimeError('native shutdown was not confirmed; see the preserved failure report')
    print('DaOS_CAMERA_FIRST_REVIEW_SUCCESS ' + json.dumps({'request_id': report['request_id'],
          'screenshots': len(screenshots), 'report_sha256': sha(output/'review.json')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

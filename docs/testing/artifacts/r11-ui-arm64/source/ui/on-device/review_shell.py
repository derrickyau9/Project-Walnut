"""Review the real DaOS app shell on the current host with one native HF-Sim capture.

This opt-in runner owns a new output directory and never retries a shutter action.
Screenshots are actual Qt renders; they are not evidence of a BSD/ARM64 image boot
or physical camera operation. Existing review output is never overwritten.
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
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='new directory; no resume or overwrite')
    parser.add_argument('--native-command', required=True, help='actual native hf-camerad executable')
    parser.add_argument('--timeout-seconds', type=int, default=120)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout_seconds <= 240:
        parser.error('--timeout-seconds must be between 30 and 240')
    native = Path(args.native_command).resolve(strict=True)
    if not native.is_file():
        parser.error('--native-command must be an existing regular executable')
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
    app.setFont(QtGui.QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif'))
    app.setApplicationName('DaOS Camera shell review')
    app.setOrganizationName('HassleFree')
    controller = None
    engine = None
    screenshots = []
    warnings = []
    shutter_actions = 0
    deadline = time.monotonic() + args.timeout_seconds
    source_paths = sorted(path.relative_to(ROOT).as_posix() for path in UI.iterdir()
                          if path.is_file() and path.suffix in ('.py', '.qml', '.json'))
    source_paths.extend(('camera/camerad/native_client.py', 'camera/camerad/calibration_context.py'))
    sources = {name: sha(ROOT/name) for name in source_paths}
    theme = json.loads((UI/'retail-theme.json').read_text(encoding='utf-8'))
    runtime_label = platform.system() + ' UI preview / ' + platform.machine()
    report = {
        'format': 'DaOS-camera-app-shell-review-v1', 'passed': False,
        'synthetic': True, 'physical_camera_validated': False,
        'physical_display_validated': False, 'bsd_image_boot_validated': False,
        'arm64_image_boot_validated': False, 'source_sha256': sources,
        'native_sha256': sha(native), 'native_backend': 'C++ hf-camerad / HF-Sim',
        'native_command': native.relative_to(ROOT).as_posix() if native.is_relative_to(ROOT) else native.name,
        'retail_source': theme.get('source', {}),
        'runtime': {'system': platform.system(), 'release': platform.release(),
                    'machine': platform.machine(), 'python': platform.python_version(),
                    'qt': QtCore.QT_VERSION_STR, 'pyqt': QtCore.PYQT_VERSION_STR,
                    'qpa': app.platformName(), 'ui_label': runtime_label},
        'screenshots': screenshots, 'qml_warnings': warnings,
    }

    def pump(milliseconds=50):
        QtTest.QTest.qWait(milliseconds)

    def wait(predicate, description):
        print('DaOS_REVIEW_STAGE ' + description, flush=True)
        action_deadline = min(deadline, time.monotonic() + 8)
        while time.monotonic() < action_deadline:
            if predicate():
                return
            pump(20)
        state = '' if controller is None else controller.statusText + '; ' + controller.errorText
        raise RuntimeError('review action deadline reached: ' + description + '; ' + state)

    def find(name):
        found = window.findChild(QtQuick.QQuickItem, name)
        if found is not None:
            return found

        def visual(parent):
            for child in parent.childItems():
                if child.objectName() == name:
                    return child
                result = visual(child)
                if result is not None:
                    return result
            return None
        return visual(window.contentItem())

    def item(name):
        wait(lambda: find(name) is not None, 'control ' + name)
        return find(name)

    def scroll_into_view(control):
        ancestors = []
        ancestor = control.parentItem()
        while ancestor is not None:
            if ancestor.property('contentY') is not None and ancestor.property('contentHeight') is not None:
                ancestors.append(ancestor)
            ancestor = ancestor.parentItem()
        for flickable in ancestors:
            for _ in range(35):
                top = control.mapToItem(flickable, QtCore.QPointF(0, 0)).y()
                bottom = top + control.height()
                if top >= 0 and bottom <= flickable.height():
                    break
                angle = 360 if top < 0 else -360
                point = flickable.mapToScene(QtCore.QPointF(flickable.width()/2, flickable.height()/2))
                event = QtGui.QWheelEvent(point, point, QtCore.QPoint(), QtCore.QPoint(0, angle),
                                         QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                                         QtCore.Qt.NoScrollPhase, False)
                QtCore.QCoreApplication.sendEvent(window, event)
                pump(40)
            wait(lambda: not bool(flickable.property('moving')), 'scroll gesture settled')

    def click(name):
        nonlocal shutter_actions
        control = item(name)
        scroll_into_view(control)
        if not control.isVisible() or not control.isEnabled():
            raise RuntimeError('review control unavailable: ' + name)
        point = control.mapToScene(QtCore.QPointF(control.width()/2, control.height()/2))
        if not 0 <= point.x() < window.width() or not 0 <= point.y() < window.height():
            raise RuntimeError('review control outside viewport: ' + name)
        if name == 'shutterButton':
            if shutter_actions:
                raise RuntimeError('a second shutter dispatch is forbidden')
            shutter_actions += 1
        QtTest.QTest.mouseClick(window, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                               QtCore.QPoint(round(point.x()), round(point.y())))
        pump(70)

    def text(name, value):
        control = item(name)
        scroll_into_view(control)
        control.forceActiveFocus()
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        for character in value:
            for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                QtCore.QCoreApplication.sendEvent(window, QtGui.QKeyEvent(kind, 0, QtCore.Qt.NoModifier, character))
        pump(30)
        if control.property('text') != value:
            raise RuntimeError('review text did not reach ' + name)

    def screenshot(name, description):
        pump(100)
        result = window.contentItem().grabToImage()
        if result is None:
            raise RuntimeError('Qt rendered-image grab unavailable')
        ready = []
        result.ready.connect(lambda: ready.append(True))
        wait(lambda: bool(ready), 'render ' + name)
        image = result.image()
        path = output/name
        if path.exists() or image.isNull() or not image.save(str(path)):
            raise RuntimeError('exclusive review PNG creation failed: ' + name)
        screenshots.append({'file': name, 'width': image.width(), 'height': image.height(),
                            'sha256': sha(path), 'description': description})

    try:
        session = output/'session'
        controller = CameraController(make_client_factory('native', session, command=[str(native)]),
                                      backend='native', state_path=output/'device-ui.json', poll_ms=100)
        shell = ShellController(output/'shell-preferences.json', parent=app)
        apps = CameraAppsController(controller, output, parent=app)
        engine = QtQml.QQmlApplicationEngine()
        engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
        for name, value in (('camera', controller), ('cameraShell', shell), ('cameraApps', apps),
                            ('retailTheme', theme), ('runtimeOSLabel', runtime_label)):
            engine.rootContext().setContextProperty(name, value)
        engine.load(QtCore.QUrl.fromLocalFile(str(UI/'Main.qml')))
        if len(engine.rootObjects()) != 1:
            raise RuntimeError('DaOS shell did not create its actual Qt window: ' + '; '.join(warnings))
        window = engine.rootObjects()[0]
        window.resize(1280, 800)
        window.requestActivate()
        wait(lambda: controller.connectionState == 'ready' and controller.canCapture, 'native authority ready')
        wait(lambda: find('nav-settings') is not None, 'new DaOS app shell')
        pump(100)
        wait(lambda: window.isActive(), 'actual review window active')
        wait(lambda: controller._pending == 0, 'initial native state complete')
        initial_state = controller._client.get_state()
        if (initial_state.get('settings_persistent') is not True
                or initial_state.get('settings_format') != 'hf-device-settings-v1'):
            raise RuntimeError('native executable does not confirm the required persistent settings contract; '
                               'rebuild the current native core before review, no shutter action taken')
        report['initial_settings_format'] = initial_state['settings_format']
        report['initial_settings_persistent'] = initial_state['settings_persistent']
        if controller.lastRequestId:
            raise RuntimeError('new review unexpectedly contains an earlier capture; no shutter action taken')
        exposure_control = item('exposureInput')
        exposure_control.forceActiveFocus()
        pump(20)
        wait(lambda: exposure_control.property('activeFocus') is True, 'actual exposure control focused')
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_Up)
        wait(lambda: controller.exposureUs == 10100 and controller.canCapture, 'requested exposure persisted')
        click('previewButton')
        wait(lambda: bool(controller.previewUrl), 'actual native synthetic preview')
        screenshot('01-camera-live.png', 'DaOS camera and real native HF-Sim preview on the current host; no sensor connected.')

        write_new(output/'shutter-intent.json', {
            'format': 'DaOS-review-one-shutter-intent-v1', 'maximum_shutter_actions': 1,
            'native_sha256': report['native_sha256'], 'requested_exposure_us': 10100,
            'instruction': 'Never rerun into this directory or replace the original request after uncertainty.'})
        click('shutterButton')
        wait(lambda: controller.captureState in ('complete', 'failed', 'cancelled', 'unknown'), 'original capture terminal state')
        request_id = controller.lastRequestId
        report['request_id'] = request_id
        if controller.captureState != 'complete' or not request_id:
            raise RuntimeError('original capture did not complete; query that UUID separately, never retry the shutter')
        click('captureDetailsButton')
        screenshot('02-camera-saved.png', 'Original single capture confirmed; requested settings remain distinct from per-frame evidence.')

        click('nav-photos')
        wait(lambda: not controller.libraryLoading and len(controller.libraryItems) == 1, 'retained photo list')
        click('appsPhotoItem-' + request_id)
        wait(lambda: not controller.libraryLoading and bool(controller.reviewUrl), 'saved DNG display conversion')
        screenshot('03-photos.png', 'Original saved synthetic DNG loaded through the photo app, separate from live preview.')
        click('appsPhotoExportButton')
        wait(lambda: not controller.libraryLoading and len(list(Path(apps.exportDirectory).glob('*.dng'))) == 1,
             'original photo export')
        exported = next(Path(apps.exportDirectory).glob('*.dng'))

        click('nav-settings')
        click('appSettingsGroup-exposure')
        screenshot('04-settings-exposure.png', 'Working requested exposure and gain controls; no additional capture.')
        click('appSettingsGroup-display')
        click('appGridSwitch')
        screenshot('05-settings-display.png', 'Persisted UI preferences: theme, framing grid and synthetic preview brightness.')

        click('nav-recipes')
        text('appRecipeNameInput', 'Review bracket')
        text('appRecipeExposureInput', '5000,10000,20000')
        click('appRecipeSaveButton')
        wait(lambda: len(apps.recipes) == 1 and not apps.errorText, 'validated portable recipe save')
        click('appRecipeExportButton')
        wait(lambda: len(list(Path(apps.exportDirectory).glob('recipe-*.json'))) == 1, 'portable recipe export')
        recipe_scroll = item('appRecipeNameInput').parentItem()
        while recipe_scroll is not None:
            if recipe_scroll.property('contentY') is not None and recipe_scroll.property('contentHeight') is not None:
                recipe_scroll.setProperty('contentY', 0)
                pump(80)
                wait(lambda: not bool(recipe_scroll.property('moving')), 'recipe review scroll settled')
                break
            recipe_scroll = recipe_scroll.parentItem()
        screenshot('06-recipes.png', 'Validated bracket recipe editor and portable export; workflow execution remains separate.')

        click('nav-diagnostics')
        screenshot('07-diagnostics.png', 'Read-only original request, runtime and filesystem facts; no measured hardware telemetry.')
        click('appDiagnosticExportButton')
        wait(lambda: len(list(Path(apps.exportDirectory).glob('diagnostics-*.json'))) == 1, 'diagnostics export')
        diagnostic_file = next(Path(apps.exportDirectory).glob('diagnostics-*.json'))
        diagnostics = json.loads(diagnostic_file.read_text(encoding='utf-8'))
        if diagnostics['camera']['request_id'] != request_id or diagnostics['synthetic'] is not True:
            raise RuntimeError('diagnostics changed original capture identity or synthetic status')
        click('nav-devices')
        screenshot('08-devices.png', 'Explicit HF-Sim capabilities and unvalidated physical camera boundaries.')
        click('nav-system')
        screenshot('09-system.png', 'Actual host process and runtime identity; this is not an ARM64/FreeBSD boot.')
        click('appLauncherButton')
        screenshot('10-launcher.png', 'DaOS launcher routes to implemented local apps; no privileged system commands.')
        click('appLaunch-camera')
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_K, QtCore.Qt.ControlModifier)
        wait(lambda: item('commandSearchInput').isVisible(), 'command palette open')
        text('commandSearchInput', 'settings')
        screenshot('11-command-palette.png', 'Keyboard command search contains only allowed app routes.')
        click('command-result-settings')
        wait(lambda: shell.currentApp == 'settings', 'command palette app route')
        window.resize(800, 600)
        click('appSettingsGroup-display')
        screenshot('12-compact-settings.png', 'Actual 800 by 600 display settings layout, not a resized screenshot.')
        click('appThemeDarkButton')
        click('nav-camera')
        if window.property('recordDetailsExpanded') is True:
            click('captureDetailsButton')
        if controller.previewActive is False:
            click('previewButton')
        wait(lambda: bool(controller.previewUrl), 'compact live preview')
        screenshot('13-compact-camera-dark.png', 'Actual compact dark DaOS camera with persisted framing-grid preference.')

        wait(lambda: controller._pending == 0, 'native request queue drained')
        record = controller._client.query_capture(request_id)
        state = controller._client.get_state()
        photo = Path(record['file_path'])
        metadata = inspect_dng(photo)
        with sqlite3.connect((session/'journal.sqlite3').as_uri() + '?mode=ro', uri=True) as journal:
            rows = journal.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()
        if (rows != [(request_id, 'complete')] or record['actual_exposure_us'] != 10100
                or metadata['request_id'] != request_id or sha(photo) != sha(exported)
                or state['settings']['requested_exposure_us'] != 10100
                or controller.lastRequestId != request_id or shutter_actions != 1):
            raise RuntimeError('original capture, requested/actual settings or exported DNG identity mismatch')
        if warnings:
            raise RuntimeError('QML emitted warnings: ' + '; '.join(warnings))
        if {name: sha(ROOT/name) for name in source_paths} != sources:
            raise RuntimeError('source changed during review; screenshots cannot establish one frozen UI revision')
        restored = ShellController(output/'shell-preferences.json')
        if restored.theme != 'dark' or restored.gridEnabled is not True:
            raise RuntimeError('interface preferences did not survive model recreation')
        report.update({'passed': True, 'request_id': request_id, 'capture_id': record['capture_id'],
                       'frame_id': record['frame_id'], 'journal_rows': len(rows),
                       'actual_exposure_us': record['actual_exposure_us'],
                       'settings': state['settings'], 'dng_sha256': sha(photo),
                       'export_dng_sha256': sha(exported), 'dng_metadata': metadata,
                       'preferences': shell.preferences,
                       'diagnostics_sha256': sha(diagnostic_file)})
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        if controller is not None:
            report['request_id'] = controller.lastRequestId
            report['capture_state'] = controller.captureState
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
                report['error'] = 'native authority process did not close after review'
        write_new(output/'review.json', report)
        if engine is not None:
            engine.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    if report['native_process_closed'] is not True:
        raise RuntimeError('native authority process did not close after review')
    if report.get('native_shutdown_error'):
        raise RuntimeError(report['native_shutdown_error'])
    print('DaOS_SHELL_REVIEW_SUCCESS ' + json.dumps({'request_id': report['request_id'],
          'screenshots': len(screenshots), 'report_sha256': sha(output/'review.json')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

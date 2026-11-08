"""Actual ARM64 Qt Quick/native-authority end-to-end smoke.

This runner refuses non-Linux/non-ARM64 Python before creating output. It checks
AArch64 ELF identities for Python, loaded Qt extensions and the authority. It
never substitutes host Python or a reference camera. Offscreen rendering is
software evidence, not a physical display/sensor test.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / 'ui' / 'on-device'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def arm_elf(path):
    path = Path(path).resolve(strict=True)
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != 183):
        raise RuntimeError('actual little-endian AArch64 ELF64 required: ' + path.name)
    return {'file': path.name, 'elf_machine': 183, 'sha256': sha(path)}


def require_arm_python():
    if sys.platform != 'linux' or platform.machine().lower() not in {'aarch64', 'arm64'}:
        raise RuntimeError('requires actual Linux ARM64 Python; no host-Python fallback')
    if os.geteuid() == 0:
        raise RuntimeError('run the ARM64 Qt smoke as a nonroot guest user')
    return arm_elf(sys.executable)


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-daemon', required=True)
    parser.add_argument('--native-launcher', action='append', default=[],
                        help='explicit emulator argv prefix, only for qemu-user')
    parser.add_argument('--execution-mode', required=True,
                        choices=('qemu-user', 'qemu-system', 'arm64-host'))
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--phase', choices=('first', 'resume', 'all'), default='all')
    parser.add_argument('--timeout-seconds', type=int, default=90)
    args = parser.parse_args(argv)
    python_identity = require_arm_python()
    native = Path(args.native_daemon).resolve(strict=True)
    native_identity = arm_elf(native)
    if args.native_launcher and args.execution_mode != 'qemu-user':
        raise RuntimeError('an explicit emulator prefix is only permitted in qemu-user mode')
    if not 5 <= args.timeout_seconds <= 300:
        raise RuntimeError('timeout must be between 5 and 300 seconds')
    output = Path(args.output_dir).absolute()
    if args.phase in {'first', 'all'}:
        if output.exists():
            raise RuntimeError('first-phase output directory must be new')
        prior = None
    else:
        raw = (output / 'first.json').read_bytes()
        if len(raw) > 65536:
            raise RuntimeError('oversized first-phase metadata')
        prior = json.loads(raw)
        rid = prior.get('request_id')
        if (not isinstance(rid, str) or str(uuid.UUID(rid)) != rid
                or prior.get('format') != 'hf-arm64-qt-smoke-v1'
                or prior.get('phase') != 'first' or prior.get('passed') is not True):
            raise RuntimeError('invalid first-phase metadata')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    sys.dont_write_bytecode = True
    if (os.environ['QT_QPA_PLATFORM'] != 'offscreen'
            or os.environ['QT_QUICK_BACKEND'] != 'software'):
        raise RuntimeError('this smoke requires explicitly offscreen software rendering')
    from PyQt5 import QtCore, QtGui, QtQml, QtQuick, QtTest
    identities = {name: arm_elf(module.__file__) for name, module in (
        ('QtCore', QtCore), ('QtGui', QtGui), ('QtQml', QtQml),
        ('QtQuick', QtQuick), ('QtTest', QtTest))}
    sys.path.insert(0, str(ROOT))
    from camera.camerad.native_client import NativeClient
    from camera.dng.inspect import inspect_dng
    spec = importlib.util.spec_from_file_location('hf_arm_device_controller', UI / 'controller.py')
    controller_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller_module)
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    app.setApplicationName('Project Walnut ARM64 UI software smoke')
    app.setOrganizationName('Project Walnut')
    if app.platformName() != 'offscreen':
        raise RuntimeError('offscreen Qt platform was not selected')
    command = [*args.native_launcher, str(native)]
    factory = lambda: NativeClient(output / 'session', command=command, timeout_seconds=30)
    timeout = args.timeout_seconds
    sources = {path: sha(ROOT / path) for path in (
        'ui/on-device/controller.py', 'ui/on-device/Main.qml',
        'camera/camerad/native_client.py', 'camera/camerad/retained.py',
        'camera/dng/inspect.py', 'tests/ui/arm64_smoke.py','tests/ui/offline_smoke.py')}
    if prior and (prior.get('native_authority') != native_identity
                  or prior.get('source_sha256') != sources):
        raise RuntimeError('resume requires unchanged authority and UI smoke sources')

    def phase(name, previous=None):
        if name == 'first':
            output.mkdir(parents=True, exist_ok=False)
        controller = controller_module.CameraController(factory, backend='native',
                            state_path=output / 'device-ui.json', poll_ms=180)
        engine = QtQml.QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
        engine.rootContext().setContextProperty('camera', controller)
        engine.load(QtCore.QUrl.fromLocalFile(str(UI / 'Main.qml')))
        if not engine.rootObjects():
            controller.shutdown()
            raise RuntimeError('ARM Qt QML failed to create a window: ' + '; '.join(warnings[-5:]))
        window = engine.rootObjects()[0]
        window.requestActivate()
        def wait(condition):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if condition():
                    return
                QtTest.QTest.qWait(25)
            raise RuntimeError('ARM UI condition timed out: ' + controller.statusText + '; ' + controller.errorText)
        def visual(parent, target):
            for child in parent.childItems():
                if child.objectName() == target:
                    return child
                found = visual(child, target)
                if found is not None:
                    return found
            return None
        def item(target):
            found = window.findChild(QtQuick.QQuickItem, target)
            if found is None:
                found = visual(window.contentItem(), target)
            if found is None:
                raise RuntimeError('QML control missing: ' + target)
            return found
        def click(target):
            button = item(target)
            if not button.property('enabled'):
                raise RuntimeError('QML control unexpectedly disabled: ' + target)
            point = button.mapToScene(QtCore.QPointF(button.width() / 2, button.height() / 2))
            QtTest.QTest.mouseClick(window, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                                   QtCore.QPoint(round(point.x()), round(point.y())))
            QtTest.QTest.qWait(80)
        def screenshot(filename):
            grab = window.contentItem().grabToImage()
            if grab is None:
                raise RuntimeError('Qt Quick content grab was unavailable')
            ready = []
            grab.ready.connect(lambda: ready.append(True))
            wait(lambda: bool(ready))
            image = grab.image()
            if image.isNull() or (image.width(), image.height()) != (1024, 680):
                raise RuntimeError('incorrect offscreen render dimensions')
            colors = {image.pixel(x, y) for x in range(0, image.width(), 17)
                      for y in range(0, image.height(), 17)}
            if len(colors) < 10:
                raise RuntimeError('offscreen render appears blank')
            destination = output / filename
            if destination.exists() or not image.save(str(destination), 'PNG'):
                raise RuntimeError('offscreen PNG could not be saved create-only')
            return {'file': filename, 'sha256': sha(destination), 'width': image.width(),
                    'height': image.height(), 'sampled_colors': len(colors),
                    'rendering': 'ARM64 Qt Quick offscreen software; no physical display'}
        client = None
        try:
            wait(lambda: controller.connectionState != 'connecting')
            if controller.connectionState != 'ready':
                raise RuntimeError('native ARM authority did not become ready: ' + controller.errorText)
            QtTest.QTest.qWait(250)
            if name == 'first':
                exposure = item('exposureInput')
                exposure.forceActiveFocus()
                QtTest.QTest.keyClick(window, QtCore.Qt.Key_Up)
                wait(lambda: controller.exposureUs == 10100 and controller.canCapture)
                click('shutterButton')
                wait(lambda: controller.captureState == 'complete')
                rid = controller.lastRequestId
                if str(uuid.UUID(rid)) != rid:
                    raise RuntimeError('capture UUID is not canonical')
            else:
                rid = previous['request_id']
                wait(lambda: controller.captureState == 'complete')
                if controller.lastRequestId != rid:
                    raise RuntimeError('restart did not query the original UUID')
            client = controller._client
            saved = json.loads((output / 'device-ui.json').read_text(encoding='utf-8'))
            if saved['request_id'] != rid:
                raise RuntimeError('original UI UUID was not persisted')
            source = output / 'session' / 'photos' / (rid + '.dng')
            structure = inspect_dng(source)
            if (structure['request_id'] != rid or (structure['width'], structure['height']) != (128, 96)
                    or structure['white_level'] != 16383 or structure['cfa'] != 'RGGB'):
                raise RuntimeError('saved native ARM DNG identity/structure mismatch')
            source_hash = sha(source)
            if previous and source_hash != previous['dng_sha256']:
                raise RuntimeError('restart changed the committed DNG')
            ready_image = screenshot(name + '-complete.png')
            click('queryButton')
            wait(lambda: controller._pending == 0)
            if controller.lastRequestId != rid or controller.captureState != 'complete':
                raise RuntimeError('original-ID query changed capture identity')
            click('libraryButton')
            wait(lambda: not controller.libraryLoading and len(controller.libraryItems) == 1)
            if controller.libraryItems[0]['request_id'] != rid:
                raise RuntimeError('retained list differs from original capture')
            QtTest.QTest.qWait(250)
            click('capture-' + rid)
            wait(lambda: not controller.libraryLoading and bool(controller.reviewUrl))
            png = base64.b64decode(controller.reviewUrl.split(',', 1)[1], validate=True)
            if png != client.capture_thumbnail(rid):
                raise RuntimeError('review does not match actual saved DNG display conversion')
            thumbnail = QtGui.QImage.fromData(png, 'PNG')
            if thumbnail.isNull() or not 1 <= thumbnail.width() <= 320 or not 1 <= thumbnail.height() <= 240:
                raise RuntimeError('retained thumbnail was not decoded by ARM Qt')
            thumbnail_name = name + '-retained.png'
            with (output / thumbnail_name).open('xb') as stream:
                stream.write(png)
            export = output / (name + '-copy.dng')
            item('libraryExportPath').setProperty('text', str(export))
            click('libraryExportButton')
            wait(lambda: not controller.libraryLoading and controller.exportMessage.startswith('Exported '))
            if sha(export) != source_hash or controller.lastRequestId != rid:
                raise RuntimeError('export bytes or current capture ID changed')
            gallery_image = screenshot(name + '-gallery.png')
            record = client.query_capture(rid)
            if record.get('status') != 'complete' or record.get('actual_exposure_us') != 10100:
                raise RuntimeError('native capture record differs from submitted exposure')
            result = {'format': 'hf-arm64-qt-smoke-v1', 'phase': name, 'passed': True,
                'execution_mode': args.execution_mode, 'simulated': True,
                'machine': platform.machine(), 'python': platform.python_version(), 'effective_uid': os.geteuid(),
                'python_elf': python_identity, 'qt_extensions': identities,
                'qt_version': QtCore.QT_VERSION_STR, 'qt_runtime_version': QtCore.qVersion(),
                'pyqt_version': QtCore.PYQT_VERSION_STR,
                'qt_platform': app.platformName(), 'qt_quick_backend': os.environ['QT_QUICK_BACKEND'],
                'native_authority': native_identity, 'source_sha256': sources,
                'request_id': rid, 'capture_id': record['capture_id'], 'frame_id': record['frame_id'],
                'capture_status': record['status'], 'actual_exposure_us': record['actual_exposure_us'],
                'dng_sha256': source_hash, 'dng_bytes': source.stat().st_size,
                'export_sha256': sha(export), 'export_bytes': export.stat().st_size,
                'thumbnail': {'file': thumbnail_name, 'sha256': hashlib.sha256(png).hexdigest(),
                    'bytes': len(png), 'width': thumbnail.width(), 'height': thumbnail.height(),
                    'source': 'actual retained synthetic DNG', 'calibrated': False},
                'screenshots': [ready_image, gallery_image], 'physical_display_validated': False,
                'sensor_acquisition_validated': False, 'restart_same_id_without_recapture': name == 'resume'}
        finally:
            controller.shutdown()
            engine.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        if client is None or client.process.poll() is None:
            raise RuntimeError('UI shutdown left its native authority running')
        journal = output / 'session' / 'journal.sqlite3'
        with sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT request_id,status FROM captures').fetchall()
        if rows != [(rid, 'complete')]:
            raise RuntimeError('restart/query/export created extra or changed journal rows')
        result['journal_rows'] = len(rows)
        result['native_process_closed'] = True
        if not args.native_launcher:
            from offline_smoke import run as offline_run
            result['offline_camera'] = offline_run(native, output / 'offline', name)
        write_new(output / (name + '.json'), result)
        print('HF_ARM64_QT_' + name.upper() + '_PASS ' + json.dumps(result, sort_keys=True), flush=True)
        print('HF_IMAGE_UI_RESULT ' + json.dumps({'phase': name, 'passed': True,
            'request_id': rid, 'report': name + '.json',
            'report_sha256': sha(output / (name + '.json')),
            'journal_rows': 1, 'native_process_closed': True}, sort_keys=True), flush=True)
        return result

    if args.phase in {'first', 'all'}:
        prior = phase('first')
    if args.phase in {'resume', 'all'}:
        phase('resume', prior)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        print('ARM64 Qt smoke failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(2)

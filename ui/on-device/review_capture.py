"""Render review screenshots from actual Qt controls and a native HF-Sim session.

First makes one synthetic photograph; resume queries that original UUID without
another shutter action. No physical sensor, display or measured color is claimed.
Uses only Python's standard library and the deployed PyQt5 runtime (no QtTest).
"""
from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def os_release():
    values = {}
    path = next((candidate for candidate in (Path('/etc/os-release'), Path('/var/run/os-release')) if candidate.is_file()), None)
    if path is not None:
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                values[key.lower()] = value.strip('"\'')
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=('first', 'resume'), required=True)
    parser.add_argument('--native-command', required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--require-buildroot', action='store_true')
    target.add_argument('--require-freebsd', action='store_true')
    parser.add_argument('--timeout-seconds', type=int, default=180)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=800)
    args = parser.parse_args(argv)
    if not (10 <= args.timeout_seconds <= 240 and 800 <= args.width <= 1920 and 600 <= args.height <= 1200):
        parser.error('bounded timeout 10..240s and geometry 800..1920 by 600..1200 required')
    release = os_release()
    if args.require_buildroot and (sys.platform != 'linux' or release.get('id') != 'buildroot'
            or platform.machine().lower() not in ('aarch64', 'arm64') or os.geteuid() == 0):
        raise RuntimeError('requires actual nonroot Buildroot Linux ARM64; no host fallback')
    freebsd_machines = {'amd64': 62, 'x86_64': 62, 'arm64': 183, 'aarch64': 183}
    if args.require_freebsd and (platform.system() != 'FreeBSD'
            or platform.machine().lower() not in freebsd_machines or os.geteuid() == 0):
        raise RuntimeError('requires actual nonroot FreeBSD arm64 or amd64; no host fallback')
    native = Path(args.native_command).resolve(strict=True)
    native_header = native.read_bytes()[:64]
    if args.require_buildroot and (native_header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(native_header[18:20], 'little') != 183):
        raise RuntimeError('actual native little-endian ARM64 ELF required')
    if args.require_freebsd and (native_header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(native_header[18:20], 'little') != freebsd_machines[platform.machine().lower()]
            or native_header[7] != 9):
        raise RuntimeError('actual native FreeBSD-branded ELF matching the running architecture required')
    output = Path(args.output).absolute()
    if args.phase == 'first':
        output.mkdir(parents=True, exist_ok=False)
        previous = None
    else:
        previous = json.loads((output/'first.json').read_text(encoding='utf-8'))
        if previous.get('format') != 'hf-camera-ui-review-v1' or previous.get('passed') is not True:
            raise RuntimeError('missing successful original review')
        if (output/'resume.json').exists():
            raise RuntimeError('existing review phase is never overwritten')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    if os.name == 'nt':
        os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
    from PyQt5 import QtCore, QtGui, QtQml, QtQuick
    from camera.camerad.native_client import NativeClient
    from camera.dng.inspect import inspect_dng
    spec = importlib.util.spec_from_file_location('hf_review_controller', UI/'controller.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    app.setFont(QtGui.QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif', 10))
    app.setApplicationName('Project Walnut camera review')
    app.setOrganizationName('Project Walnut')
    controller = module.CameraController(lambda: NativeClient(output/'session', command=[str(native)]),
                    backend='native', state_path=output/'device-ui.json', poll_ms=150)
    if str(UI) not in sys.path:
        sys.path.insert(0, str(UI))
    from shell_model import ShellController
    from apps_controller import CameraAppsController
    shell = ShellController(output/'shell-preferences.json', parent=app)
    apps = CameraAppsController(controller, output, parent=app)
    engine = QtQml.QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
    engine.rootContext().setContextProperty('camera', controller)
    engine.rootContext().setContextProperty('cameraShell', shell)
    engine.rootContext().setContextProperty('cameraApps', apps)
    engine.rootContext().setContextProperty('retailTheme', json.loads((UI/'retail-theme.json').read_text(encoding='utf-8')))
    runtime_label = ('DaOS BSD Unix · FreeBSD ' + platform.release() + ' · ' + platform.machine()
                     if platform.system() == 'FreeBSD' else platform.system() + ' UI preview')
    engine.rootContext().setContextProperty('runtimeOSLabel', runtime_label)
    engine.load(QtCore.QUrl.fromLocalFile(str(UI/'Console.qml')))
    if not engine.rootObjects():
        controller.shutdown(); raise RuntimeError('QML did not create a window: '+'; '.join(warnings))
    window = engine.rootObjects()[0]
    window.resize(args.width, args.height); window.requestActivate()
    deadline = time.monotonic()+args.timeout_seconds
    screenshots = []
    def pump(milliseconds=50):
        end = time.monotonic()+milliseconds/1000
        while time.monotonic() < end:
            app.processEvents(); time.sleep(.005)
    def wait(predicate):
        while time.monotonic() < deadline:
            if predicate(): return
            pump(20)
        raise RuntimeError('review condition timed out: '+controller.statusText+'; '+controller.errorText)
    def find(name):
        def visual(parent):
            for child in parent.childItems():
                if child.objectName() == name:return child
                found = visual(child)
                if found is not None:return found
            return None
        found = window.findChild(QtQuick.QQuickItem, name)
        if found is None:found=visual(window.contentItem())
        if found is None:raise RuntimeError('QML item missing: '+name)
        return found
    def click(name):
        item=find(name)
        if not item.isVisible() or not item.isEnabled():raise RuntimeError('QML control unavailable: '+name)
        point=item.mapToScene(QtCore.QPointF(item.width()/2,item.height()/2))
        for kind, buttons in ((QtCore.QEvent.MouseButtonPress,QtCore.Qt.LeftButton),
                              (QtCore.QEvent.MouseButtonRelease,QtCore.Qt.NoButton)):
            event=QtGui.QMouseEvent(kind,point,QtCore.Qt.LeftButton,buttons,QtCore.Qt.NoModifier)
            QtCore.QCoreApplication.sendEvent(window,event)
        pump(80)
    def screenshot(name, description):
        pump(100)
        result=window.contentItem().grabToImage()
        if result is None:raise RuntimeError('actual Qt grab unavailable')
        ready=[];result.ready.connect(lambda:ready.append(True));wait(lambda:bool(ready))
        image=result.image();path=output/name
        if path.exists() or image.isNull() or not image.save(str(path)):raise RuntimeError('review PNG creation failed')
        screenshots.append({'file':name,'width':image.width(),'height':image.height(),
                            'sha256':sha(path),'description':description})
    source_paths=tuple(sorted(path.relative_to(ROOT).as_posix() for path in UI.iterdir()
                             if path.is_file() and path.suffix in ('.py', '.qml', '.json'))) + (
                  'camera/camerad/native_client.py','camera/camerad/calibration_context.py')
    sources={name:sha(ROOT/name) for name in source_paths}
    try:
        wait(lambda:controller.connectionState=='ready' and controller.canCapture)
        if previous and (previous['source_sha256']!=sources or previous['native_sha256']!=sha(native)):
            raise RuntimeError('resume requires the original UI/client/native sources')
        if args.phase=='first':
            controller.setExposureUs(12500)
            wait(lambda:controller.exposureUs==12500 and controller.canCapture)
            click('previewButton');wait(lambda:bool(controller.previewUrl))
            screenshot('01-live.png','Actual synthetic preview; manual exposure controls; no sensor connected.')
            click('shutterButton');wait(lambda:controller.captureState=='complete')
            request_id=controller.lastRequestId
            if not controller.previewActive:click('previewButton')
            wait(lambda:bool(controller.previewUrl))
            click('captureDetailsButton')
            screenshot('02-saved.png','Confirmed original capture, expanded immutable request/storage details, separate live synthetic preview.')
            click('libraryButton');wait(lambda:not controller.libraryLoading and len(controller.libraryItems)==1)
            click('capture-'+request_id);wait(lambda:not controller.libraryLoading and bool(controller.reviewUrl))
            screenshot('03-gallery.png','Display conversion read from the original retained synthetic DNG, not a live image.')
            dialog=window.findChild(QtCore.QObject,'libraryDialog')
            QtCore.QMetaObject.invokeMethod(dialog,'close');pump(50)
            controller.exportSelected(str(output/'export.dng'))
            wait(lambda:not controller.libraryLoading and (output/'export.dng').is_file())
            click('captureDetailsButton')
            window.resize(800,600);pump(100)
            screenshot('04-compact.png','Actual 800x600 camera layout with collapsible request details.')
            window.resize(args.width,args.height)
        else:
            wait(lambda:controller.captureState=='complete' and controller.lastRequestId==previous['request_id'])
            request_id=controller.lastRequestId
            click('previewButton');wait(lambda:bool(controller.previewUrl))
            click('captureDetailsButton')
            screenshot('05-restored.png','Original capture and persisted settings recovered after restart without another shutter action.')
        wait(lambda:controller._pending==0)
        record=controller._client.query_capture(request_id)
        state=controller._client.get_state()
        photo=Path(record['file_path']);metadata=inspect_dng(photo)
        with sqlite3.connect((output/'session/journal.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT request_id,status FROM captures ORDER BY request_id').fetchall()
        if (rows!=[(request_id,'complete')] or record['actual_exposure_us']!=12500
                or state.get('settings_persistent') is not True or state['settings']['requested_exposure_us']!=12500
                or metadata['request_id']!=request_id or sha(photo)!=sha(output/'export.dng')):
            raise RuntimeError('review capture/state/export identity mismatch')
        if previous and (previous['request_id']!=request_id or previous['dng_sha256']!=sha(photo)
                         or previous['settings']!=state['settings']):
            raise RuntimeError('resume changed an original capture or configured baseline')
        report={'format':'hf-camera-ui-review-v1','passed':True,'phase':args.phase,'simulated':True,
            'physical_camera_validated':False,'physical_display_validated':False,
            'runtime':{'os_release':release,'machine':platform.machine(),'system':platform.system(),
                'uid':os.geteuid() if hasattr(os,'geteuid') else None,'python':platform.python_version(),
                'qt':QtCore.QT_VERSION_STR,'pyqt':QtCore.PYQT_VERSION_STR,'qpa':app.platformName(),'ui_label':runtime_label},
            'request_id':request_id,'capture_id':record['capture_id'],'frame_id':record['frame_id'],
            'capture_status':record['status'],'actual_exposure_us':record['actual_exposure_us'],
            'journal_rows':len(rows),'dng_sha256':sha(photo),'export_sha256':sha(output/'export.dng'),
            'settings':state['settings'],'settings_persistent':state['settings_persistent'],
            'native_sha256':sha(native),'native_identity':{'format':'ELF' if native_header[:4]==b'\x7fELF' else 'PE' if native_header[:2]==b'MZ' else 'unknown',
                'elf_machine':int.from_bytes(native_header[18:20],'little') if native_header[:4]==b'\x7fELF' else None,
                'elf_osabi':native_header[7] if native_header[:4]==b'\x7fELF' else None},
            'source_sha256':sources,'screenshots':screenshots,
            'shutter_actions':1 if args.phase=='first' else 0,'qml_warnings':warnings}
    finally:
        controller.shutdown()
    report['native_process_closed']=controller._client is None or controller._client.process.poll() is not None
    if not report['native_process_closed']:raise RuntimeError('native camera process did not close')
    report_path=output/(args.phase+'.json');write_new(report_path,report)
    print('HF_UI_REVIEW_SUCCESS '+json.dumps({'phase':args.phase,'report_sha256':sha(report_path),'request_id':request_id}),flush=True)
    engine.deleteLater();QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
    return 0


if __name__=='__main__':
    raise SystemExit(main())

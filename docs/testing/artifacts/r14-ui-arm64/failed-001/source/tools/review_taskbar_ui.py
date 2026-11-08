"""Record real Qt Taskbar gestures and animation without taking a photograph.

Run only after the UI source is frozen and a review run is authorized. This
uses the Python reference camera and its independent synthetic preview. It
does not start a native daemon, validate a board/display or boot an image.
Every PNG is an unchanged Qt grab. Animation metadata records an observation
interval, not an invented exact render time or a playback/frame-rate promise.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT/'ui/on-device'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='new directory; never overwrite an earlier review')
    parser.add_argument('--timeout-seconds', type=int, default=90, help='total deadline, 30..120 seconds')
    args = parser.parse_args(argv)
    if not 30 <= args.timeout_seconds <= 120:
        parser.error('total timeout must be 30..120 seconds')
    output = Path(args.output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    if os.name == 'nt':
        os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'))
    sys.path.insert(0, str(ROOT))
    from PyQt5 import QtCore, QtGui, QtQml, QtQuick, QtTest

    # Compose the fixture; do not discover or execute its twelve test methods.
    spec = importlib.util.spec_from_file_location(
        'hf_taskbar_review_fixture', ROOT/'tests/test_camera_first_ui.py')
    fixture = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fixture
    spec.loader.exec_module(fixture)
    fixture.CameraFirstUITests.setUpClass()
    case = fixture.CameraFirstUITests()
    deadline = time.monotonic() + args.timeout_seconds
    started = time.monotonic()
    setup_started = False
    source_names = sorted(path.relative_to(ROOT).as_posix() for path in UI.iterdir()
                          if path.is_file() and path.suffix in ('.py', '.qml', '.json'))
    source_names += ['tests/test_camera_first_ui.py', 'tools/review_taskbar_ui.py',
                     'camera/pipeline/preview.py', 'simulation/bayer.py',
                     'camera/camerad/service.py']
    sources = {name: sha(ROOT/name) for name in source_names}
    report = {'format': 'DaOS-taskbar-ui-review-v1', 'passed': False,
              'synthetic': True, 'backend': 'Python reference authority; no native daemon',
              'preview_source': 'Python synthetic Bayer PreviewStream; separate from capture',
              'planned_shutter_actions': 0, 'physical_camera_validated': False,
              'physical_display_validated': False, 'appliance_image_boot_validated': False,
              'source_sha256_before': sources, 'screenshots': [], 'animation_frames': [],
              'runtime': {'system': platform.system(), 'release': platform.release(),
                          'machine': platform.machine(), 'python': platform.python_version(),
                          'qt': QtCore.QT_VERSION_STR, 'pyqt': QtCore.PYQT_VERSION_STR,
                          'qpa': fixture.CameraFirstUITests.app.platformName()},
              'timing_note': 'Each frame records request and ready observations. Its actual render instant lies within that interval; no exact geometry timestamp or FPS is claimed.'}

    def wait(predicate, stage='fixture', timeout=5):
        print('DaOS_TASKBAR_REVIEW_STAGE '+stage, flush=True)
        limit = min(deadline, time.monotonic()+min(float(timeout), 5))
        while time.monotonic() < limit:
            if predicate():
                return
            QtTest.QTest.qWait(2)
        raise RuntimeError('Taskbar review condition timed out: '+stage)

    def fixture_wait(predicate, timeout=8000):
        return wait(predicate, 'fixture condition', min(timeout/1000, 5))

    case.wait = fixture_wait

    def snapshot():
        window = case.window
        pane = case.item('cameraContentPane')
        area = case.item('viewfinderArea')
        image = case.item('viewfinderImage')
        position = area.mapToScene(QtCore.QPointF())
        size = image.property('sourceSize')
        return {'elapsed_ms': round((time.monotonic()-started)*1000, 3),
                'progress': float(window.property('taskbarProgress')),
                'expanded': bool(window.property('taskbarExpanded')),
                'animating': bool(window.property('taskbarAnimating')),
                'inset': float(window.property('taskbarInset')),
                'pane_x': float(pane.x()), 'pane_width': float(pane.width()),
                'viewfinder_scene_x': float(position.x()), 'viewfinder_scene_y': float(position.y()),
                'viewfinder_width': float(area.width()), 'viewfinder_height': float(area.height()),
                'painted_width': float(image.property('paintedWidth')),
                'painted_height': float(image.property('paintedHeight')),
                'source_width': size.width(), 'source_height': size.height(),
                'shutter_enabled': case.item('shootButton').isEnabled()}

    def validate_geometry(state):
        width, height = case.window.width(), case.window.height()
        base_height = height-case.item('shootingStatusBar').height()
        inset = 72*state['progress']
        if (abs(state['inset']-inset) > 1 or abs(state['pane_x']-inset) > 1
                or abs(state['viewfinder_scene_x']-inset) > 1
                or abs(state['pane_width']-(width-inset)) > 1
                or abs(state['viewfinder_width']-(width-inset)) > 1
                or abs(state['viewfinder_height']-base_height*(width-inset)/width) > 1):
            raise RuntimeError('Observed Taskbar/content/preview geometry is not linked proportionally')
        if state['source_width'] > 0 and state['source_height'] > 0 and state['painted_height'] > 0:
            if not math.isclose(state['source_width']/state['source_height'],
                                state['painted_width']/state['painted_height'], rel_tol=.002):
                raise RuntimeError('Painted preview aspect differs from its source')

    def grab(name, *, direction=None, requested_offset_ms=None, gesture_started=None):
        requested = snapshot()
        validate_geometry(requested)
        result = case.window.contentItem().grabToImage()
        if result is None:
            raise RuntimeError('Qt could not create the actual frame grab')
        ready = []
        def became_ready():
            ready.append(snapshot())
        result.ready.connect(became_ready)
        wait(lambda: bool(ready), 'actual Qt grab '+name)
        observed = ready[0]
        validate_geometry(observed)
        image = result.image()
        path = output/name
        if image.isNull() or path.exists() or not image.save(str(path)):
            raise RuntimeError('Actual Qt PNG could not be saved create-only: '+name)
        record = {'file': name, 'width': image.width(), 'height': image.height(),
                  'sha256': sha(path), 'requested_observation': requested,
                  'ready_observation': observed,
                  'route': case.window.property('currentScreen')}
        if direction is None:
            report['screenshots'].append(record)
        else:
            request_after_release = round((requested['elapsed_ms']/1000+started-gesture_started)*1000, 3)
            ready_after_release = round((observed['elapsed_ms']/1000+started-gesture_started)*1000, 3)
            record.update({'direction': direction, 'scheduled_offset_ms': requested_offset_ms,
                           'request_after_release_ms': request_after_release,
                           'ready_after_release_ms': ready_after_release,
                           'request_late_by_ms': round(max(0,request_after_release-requested_offset_ms),3),
                           'intermediate_observation_interval':
                               .01 < requested['progress'] < .99 and .01 < observed['progress'] < .99})
            report['animation_frames'].append(record)

    def scene(name, x=None, y=None):
        control = case.item(name)
        point = control.mapToScene(QtCore.QPointF(control.width()/2 if x is None else x,
                                                  control.height()/2 if y is None else y))
        return QtCore.QPoint(round(point.x()), round(point.y()))

    def drag(start, end):
        QtTest.QTest.mouseMove(case.window, start, 5)
        QtTest.QTest.mousePress(case.window, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        for index in range(1, 7):
            point = QtCore.QPoint(round(start.x()+(end.x()-start.x())*index/6),
                                 round(start.y()+(end.y()-start.y())*index/6))
            QtTest.QTest.mouseMove(case.window, point, 12)
        QtTest.QTest.mouseRelease(case.window, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
        return time.monotonic()

    def settle(expanded):
        target = 1 if expanded else 0
        wait(lambda: abs(float(case.window.property('taskbarProgress'))-target) < .001
             and not case.window.property('taskbarAnimating'), 'Taskbar settled '+str(expanded))
        if bool(case.window.property('taskbarExpanded')) != expanded:
            raise RuntimeError('Taskbar gesture did not produce the requested state')

    def animation(direction):
        if direction == 'collapse':
            surface = case.item('taskbarSwipeSurface')
            start = scene('taskbarSwipeSurface', surface.width()-3, surface.height()/2)
            end = QtCore.QPoint(max(1,start.x()-62),start.y())
        else:
            area = case.item('taskbarRevealGesture')
            start = scene('taskbarRevealGesture', 8, area.height()/2)
            end = QtCore.QPoint(start.x()+110,start.y())
        released = drag(start,end)
        for index, offset in enumerate((0,16,40,80,120,180,240)):
            wait(lambda: time.monotonic()-released >= offset/1000,
                 direction+' observation '+str(offset)+'ms')
            grab(direction+'-'+str(index).zfill(2)+'.png', direction=direction,
                 requested_offset_ms=offset, gesture_started=released)
        settle(direction == 'expand')
        frames = [frame for frame in report['animation_frames'] if frame['direction']==direction]
        observations = [frame[key]['progress'] for frame in frames
                        for key in ('requested_observation','ready_observation')]
        if any(not 0 <= value <= 1 for value in observations):
            raise RuntimeError('Animation progress escaped its actual endpoint range')
        sign = -1 if direction == 'collapse' else 1
        if any(sign*(after-before) < -.000001 for before,after in zip(observations,observations[1:])):
            raise RuntimeError('Animation observations reversed during the '+direction+' recording')
        if not any(frame['intermediate_observation_interval'] for frame in frames):
            raise RuntimeError('No real intermediate '+direction+' frame was observed; preserve this run without inventing animation frames')

    before = None
    failure = None
    try:
        setup_started = True
        case.setUp()
        controller = case.launch(backend='reference', width=800, height=600)
        settle(True)
        wait(lambda: controller.previewActive and bool(controller.previewUrl)
             and case.item('viewfinderImage').property('status') == 1
             and float(case.item('viewfinderImage').property('paintedWidth')) > 0,
             'actual synthetic image ready')
        before = {'request_id': controller.lastRequestId,
                  'settings': controller._client.get_state()['settings']}
        if before['request_id'] or case.capture_rows():
            raise RuntimeError('Fresh no-shutter review unexpectedly contains a prior capture')
        grab('01-expanded.png')
        animation('collapse')
        grab('02-collapsed.png')
        animation('expand')
        grab('03-reopened.png')
        case.click('taskbar-settings')
        wait(lambda: case.window.property('currentScreen')=='menu', 'icon opens MENU')
        grab('04-menu.png')
        case.click('taskbar-diagnostics')
        wait(lambda: case.shell.currentApp=='diagnostics', 'icon opens diagnostics tool')
        grab('05-tools.png')
        after = {'request_id': controller.lastRequestId,
                 'settings': controller._client.get_state()['settings']}
        rows = case.capture_rows()
        if after != before or rows or case.warnings:
            raise RuntimeError('Taskbar review changed camera identity/settings, captured a photo or emitted QML warnings')
        report.update({'camera_before':before,'camera_after':after,'journal_rows':len(rows),
                       'observed_shutter_actions':0,'qml_warnings':list(case.warnings),
                       'animation_frame_count':len(report['animation_frames']),
                       'observed_intermediate_intervals':{
                           direction:sum(frame['intermediate_observation_interval'] for frame in report['animation_frames'] if frame['direction']==direction)
                           for direction in ('collapse','expand')}})
    except Exception as error:
        failure = error
        report['error'] = type(error).__name__+': '+str(error)
        if case.controller is not None:
            report['last_request_id'] = case.controller.lastRequestId
            report['qml_warnings'] = list(case.warnings)
            try:
                report['journal_rows'] = len(case.capture_rows())
            except Exception as query_error:
                report['journal_query_error'] = type(query_error).__name__
    finally:
        try:
            if setup_started:
                case.tearDown()
            report['fixture_closed_cleanly'] = True
        except Exception as error:
            failure = failure or error
            report['fixture_closed_cleanly'] = False
            report['shutdown_error'] = type(error).__name__+': '+str(error)
        after_sources = {name:sha(ROOT/name) for name in source_names}
        report['source_sha256_after'] = after_sources
        report['source_unchanged'] = after_sources == sources
        if after_sources != sources:
            failure = failure or RuntimeError('Source changed during review')
            report['source_error'] = 'Source changed during review'
        if time.monotonic() > deadline:
            failure = failure or RuntimeError('Review exceeded its total deadline, including teardown')
            report['deadline_exceeded'] = True
        report['passed'] = failure is None and report['fixture_closed_cleanly'] and report['source_unchanged']
        report['duration_seconds'] = round(time.monotonic()-started,3)
        write_new(output/'review.json',report)
    if failure is not None:
        raise failure
    print('DaOS_TASKBAR_REVIEW_SUCCESS '+json.dumps({'screenshots':len(report['screenshots']),
          'animation_frames':len(report['animation_frames']),'intermediate_intervals':report['observed_intermediate_intervals'],
          'report_sha256':sha(output/'review.json')}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

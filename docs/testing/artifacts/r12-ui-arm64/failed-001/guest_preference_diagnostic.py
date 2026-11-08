from pathlib import Path
import hashlib,importlib.util,json,os,platform,sys,time
project=Path('/home/camera/DaOS-UI-R12-project-001');sys.path.insert(0,str(project));os.chdir(project)
assert platform.system()=='FreeBSD' and platform.machine() in ('arm64','aarch64') and os.geteuid()==1000
output=Path('/home/camera/DaOS-UI-R12-preference-diagnostic-001');output.mkdir(mode=0o700)
os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QSG_RENDER_LOOP='basic',QML_DISABLE_DISK_CACHE='1',PYTHONDONTWRITEBYTECODE='1',XDG_RUNTIME_DIR=str(project/'runtime'),TMPDIR=str(project/'tmp'))
spec=importlib.util.spec_from_file_location('diagnostic_camera_first_tests',project/'tests/test_camera_first_ui.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from PyQt5 import QtCore,QtTest
Case=module.CameraFirstUITests;Case.setUpClass();case=Case('test_display_preferences_survive_recreation_without_sensor_changes')
events=[];case.setUp()
def emit(stage,value):
    events.append({'stage':stage,'monotonic':time.monotonic(),'value':value});print(json.dumps(events[-1],sort_keys=True),flush=True)
def properties(item,names):return {name:item.property(name) for name in names}
def state(control):
    point=control.mapToScene(module.QPointF(control.width()/2,control.height()/2));focus=case.window.activeFocusItem()
    ancestors=[];parent=control.parentItem()
    while parent is not None:
        point0=parent.mapToScene(module.QPointF(0,0))
        ancestors.append({'name':parent.objectName(),'type':parent.metaObject().className(),'scene_xy':[point0.x(),point0.y()],
                          'size':[parent.width(),parent.height()],'clip':parent.clip(),
                          'properties':properties(parent,('contentY','contentHeight','moving','visible','enabled'))})
        parent=parent.parentItem()
    return {'control':properties(control,('x','y','width','height','visible','enabled','checked','pressed','down','activeFocus')),
            'center_scene':[point.x(),point.y()],'shell_grid':case.shell.gridEnabled,'shell_error':case.shell.errorText,
            'active_focus':None if focus is None else {'name':focus.objectName(),'type':focus.metaObject().className()},
            'window_active':case.window.isActive(),'screen':case.window.property('currentScreen'),'ancestors':ancestors}
def grab(name):
    result=case.window.contentItem().grabToImage();assert result is not None
    ready=[];result.ready.connect(lambda:ready.append(True));until=time.monotonic()+10
    while not ready and time.monotonic()<until:QtTest.QTest.qWait(20)
    assert ready and result.image().save(str(output/name))
try:
    case.launch();case.click('menuButton');case.click('menuCategory-display')
    control=case.item('menuGridSwitch');assert case.capture_rows()==[]
    emit('before-pointer',state(control))
    point=control.mapToScene(module.QPointF(control.width()/2,control.height()/2))
    pixel=module.QPoint(round(point.x()),round(point.y()))
    pressed_at=time.monotonic();QtTest.QTest.mousePress(case.window,module.Qt.LeftButton,module.Qt.NoModifier,pixel)
    emit('pointer-pressed',state(control))
    QtTest.QTest.mouseRelease(case.window,module.Qt.LeftButton,module.Qt.NoModifier,pixel)
    released_at=time.monotonic();QtTest.QTest.qWait(100)
    emit('after-pointer',dict(state(control),press_hold_seconds=released_at-pressed_at))
    grab('01-after-pointer.png')
    control.forceActiveFocus();emit('before-space',state(control))
    QtTest.QTest.keyClick(case.window,module.Qt.Key_Space);QtTest.QTest.qWait(100)
    emit('after-space',state(control));grab('02-after-space.png')
    assert case.capture_rows()==[]
    report={'format':'DaOS-r12-arm64-preference-diagnostic-v1','system':platform.system(),'machine':platform.machine(),
            'uid':os.geteuid(),'qt':QtCore.qVersion(),'qpa':case.app.platformName(),'size':[case.window.width(),case.window.height()],
            'events':events,'capture_rows':[],'qml_warnings':case.warnings,'product_source_changed':False}
    with (output/'diagnostic.json').open('x') as f:json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
finally:case.tearDown()

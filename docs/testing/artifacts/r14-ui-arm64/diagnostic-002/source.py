"""Bounded observational taskbar trace; never requests a photograph."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, platform, signal, sys, time

parser=argparse.ArgumentParser()
parser.add_argument('--project',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
project=Path(args.project).resolve(strict=True)
output=Path(args.output).absolute()
output.mkdir(parents=True,exist_ok=False)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
os.environ.setdefault('QSG_RENDER_LOOP','basic')
os.environ.setdefault('PYTHONDONTWRITEBYTECODE','1')
sys.path.insert(0,str(project))
from PyQt5.QtCore import QCoreApplication,QEvent,QTimer,QUrl,QT_VERSION_STR,PYQT_VERSION_STR
from PyQt5.QtQml import QQmlComponent
from PyQt5.QtTest import QTest

def hashes():
    names=['ui/on-device/Main.qml','ui/on-device/CameraTaskbar.qml',
           'tests/test_camera_taskbar_ui.py','ui/on-device/controller.py','camera/pipeline/preview.py']
    return {name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in names}

def unwrap(value):
    return value.toVariant() if hasattr(value,'toVariant') else value

before=hashes()
started=time.monotonic()
report={'format':'DaOS-taskbar-animation-observation-v1','camera_shutter_actions':0,
        'source_before':before,'phases':[],'diagnostic_not_acceptance':True,
        'qml_clock':'Date.now wall milliseconds; compare relative intervals only',
        'python_clock':'time.monotonic seconds','runtime':{'system':platform.system(),
        'machine':platform.machine(),'qt':QT_VERSION_STR,'pyqt':PYQT_VERSION_STR,
        'python':platform.python_version()}}
case=probe=None
if hasattr(signal,'SIGALRM'):
    signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(TimeoutError('45-second diagnostic bound')))
    signal.alarm(45)
try:
    spec=importlib.util.spec_from_file_location('taskbar_diagnostic_tests',project/'tests/test_camera_taskbar_ui.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    module.CameraTaskbarUITests.setUpClass()
    case=module.CameraTaskbarUITests();case.setUp();controller=case.launch()
    report['initial_painted_size']=case.assert_fit()
    fixture=case.case;window=fixture.window;engine=fixture.engine
    report['window']={'visible':window.isVisible(),'exposed':window.isExposed(),
                      'active':window.isActive(),'qpa':module.fixture_api.CameraFirstUITests.app.platformName()}
    settings=controller._client.get_state()['settings']
    assert controller.lastRequestId=='' and fixture.capture_rows()==[]
    context=engine.rootContext()
    context.setContextProperty('probeWindow',window)
    context.setContextProperty('probeContent',window.contentItem())
    context.setContextProperty('probeOutput',output.as_posix())
    component=QQmlComponent(engine)
    component.setData(b'''
import QtQuick 2.15
Item {
 id: probe
 property string phase: "baseline"
 property var events: []
 property var screenshots: []
 property bool captureMiddle: false
 property bool midRequested: false
 property int swappedFrames: 0
 property var observedImage: findVisual(probeContent,"viewfinderImage")
 function findVisual(item,name) {
  if(item.objectName===name) return item
  for(var i=0;i<item.children.length;i++) {
   var found=findVisual(item.children[i],name)
   if(found) return found
  }
  return null
 }
 onPhaseChanged: note("phaseStart")
 function note(kind) {
  if(!events || events.length>=4000) return
  var p=probeWindow.taskbarProgress
  events.push({kind:kind,phase:phase,wallMs:Date.now(),progress:p,
               inset:probeWindow.taskbarInset,animating:probeWindow.taskbarAnimating,
               swappedFrames:swappedFrames,imageStatus:observedImage ? observedImage.status : -1,
               paintedWidth:observedImage ? observedImage.paintedWidth : 0,
               paintedHeight:observedImage ? observedImage.paintedHeight : 0})
  if(kind==="progress" && captureMiddle && !midRequested && p>0.1 && p<0.9) {
   midRequested=true
   var requestP=p
   probeContent.grabToImage(function(result) {
    var name="qml-cached-middle.png"
    var saved=result.saveToFile(probeOutput+"/"+name)
    screenshots.push({file:name,saved:saved,requestProgress:requestP,
                      callbackProgress:probeWindow.taskbarProgress,wallMs:Date.now()})
   })
  }
 }
 Connections {
  target: probeWindow
  function onTaskbarProgressChanged() { probe.note("progress") }
  function onFrameSwapped() { probe.swappedFrames++; probe.note("frameSwapped") }
 }
}''',QUrl.fromLocalFile(str(output/'taskbar-animation-probe.qml')))
    ready_deadline=time.monotonic()+5
    while component.status() not in (QQmlComponent.Ready,QQmlComponent.Error) and time.monotonic()<ready_deadline:
        QTest.qWait(10)
    report['probe_component']={'status':int(component.status()),
                               'errors':[error.toString() for error in component.errors()]}
    if component.status()!=QQmlComponent.Ready:
        raise RuntimeError('diagnostic QML component did not become Ready: '+json.dumps(report['probe_component']))
    probe=component.create(context)
    if probe is None:raise RuntimeError('; '.join(error.toString() for error in component.errors()))
    probe.setParent(window.contentItem());probe.setParentItem(window.contentItem())
    cached_area=cached_shutter=None
    for phase in ('original_lookup_sampler','cached_item_sampler'):
        if phase=='cached_item_sampler':
            probe.setProperty('phase','setup_reveal')
            case.raw_click('taskbarRevealHandle');case.settle(True)
            cached_area=fixture.item('viewfinderArea');cached_shutter=fixture.item('shootButton')
            probe.setProperty('captureMiddle',True)
        probe.setProperty('phase',phase)
        samples=[];errors=[];image_intervals=[];last_url=['']
        timer=QTimer();timer.setInterval(8)
        def sample():
            entered=time.monotonic()
            try:
                area=fixture.item('viewfinderArea') if phase=='original_lookup_sampler' else cached_area
                p=case.progress()
                shutter=fixture.item('shootButton') if phase=='original_lookup_sampler' else cached_shutter
                url=controller.previewUrl
                if url!=last_url[0]:
                    image_intervals.append({'monotonic':entered,'url_changed':True})
                    last_url[0]=url
                samples.append({'entered':entered,'finished':time.monotonic(),'progress':p,
                                'width':area.width(),'height':area.height(),
                                'animating':window.property('taskbarAnimating'),
                                'shutter_enabled':shutter.isEnabled()})
            except Exception as error:
                errors.append(type(error).__name__+': '+str(error))
        timer.timeout.connect(sample);timer.start()
        action_start=time.monotonic()
        action_wall_ms=time.time()*1000
        try:
            case.raw_click('taskbarCollapseButton');case.settle(False)
            QTest.qWait(30)
        finally:
            timer.stop()
        report['phases'].append({'name':phase,'action_start':action_start,'action_wall_ms':action_wall_ms,
            'end':time.monotonic(),'samples':samples,'errors':errors,'image_intervals':image_intervals,
            'python_middle_samples':sum(0.001<sample['progress']<0.999 for sample in samples)})
    QTest.qWait(100)
    report['qml_events']=unwrap(probe.property('events'))
    report['qml_screenshots']=unwrap(probe.property('screenshots'))
    report['capture_rows']=fixture.capture_rows();report['request_id']=controller.lastRequestId
    report['settings_unchanged']=settings==controller._client.get_state()['settings']
    report['qml_warnings']=fixture.warnings
    assert report['capture_rows']==[] and report['request_id']=='' and report['settings_unchanged']
    report['complete']=True
except Exception as error:
    report['complete']=False;report['error']=type(error).__name__+': '+str(error)
finally:
    if probe is not None:
        probe.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    if case is not None:case.tearDown()
    if hasattr(signal,'SIGALRM'):signal.alarm(0)
    report['source_after']=hashes();report['source_unchanged']=before==report['source_after']
    report['seconds']=time.monotonic()-started
    for path in output.glob('*.png'):
        report.setdefault('screenshot_sha256',{})[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    with (output/'trace.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({'complete':report.get('complete'),'seconds':report['seconds'],
                     'phases':[{k:p[k] for k in ('name','python_middle_samples')} for p in report['phases']],
                     'qml_events':len(report.get('qml_events',[])),
                     'screenshots':report.get('qml_screenshots',[]),'error':report.get('error')}),flush=True)
raise SystemExit(0 if report.get('complete') and report['source_unchanged'] else 1)

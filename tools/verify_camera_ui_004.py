"""Desktop 800x480 input/animation trace with explicit GUI fault injection."""
import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ui/on-device'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software');os.environ.setdefault('QT_FORCE_STDERR_LOGGING','1')
from PyQt5.QtCore import QPoint,QTimer,Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlEngine
from PyQt5.QtTest import QTest
import main as launcher

def main():
    output=ROOT/'build/DaOS_Camera_004';original=QGuiApplication.exec_;errors=[];pending_grabs=[]
    def loop(app):
        def perform():
            try:
                window=next(w for w in app.topLevelWindows() if w.objectName()=='cameraFirstWindow');context=QQmlEngine.contextForObject(window)
                camera=context.contextProperty('camera');workspace=context.contextProperty('shootingWorkspace');shell=context.contextProperty('cameraShell');recorder=context.contextProperty('uiPerformance')
                assert camera.connectionState=='ready'
                original_id=camera.lastRequestId
                window.requestActivate();QTest.keyClick(window,Qt.Key_Q);QTest.qWait(320)
                QTest.mouseClick(window,Qt.LeftButton,pos=QPoint(300,155));QTest.qWait(80)
                assert workspace.selectedParameter=='gain'
                for key in [Qt.Key_Up]*25:QTest.keyClick(window,key);QTest.qWait(10)
                assert camera.analogueGain==16,camera.settingsView
                for key in [Qt.Key_Down]*25:QTest.keyClick(window,key);QTest.qWait(10)
                deadline=time.monotonic()+3
                while not camera.canCapture and time.monotonic()<deadline:QTest.qWait(10)
                assert camera.confirmedSettings['requested_analogue_gain']==1.0,camera.settingsView
                QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(240)
                for app_name in ('photos','settings','camera'):
                    shell.navigate(app_name);QTest.qWait(320)
                baseline=dict(recorder.metrics)
                injected=[];recorder.setActivity('explicit-fault-injection',True)
                for milliseconds in (300,1000):
                    start=time.perf_counter();time.sleep(milliseconds/1000);injected.append((time.perf_counter()-start)*1000);QTest.qWait(120)
                recorder.setActivity('explicit-fault-injection',False)
                assert recorder.metrics['longStallEvents']>=2,recorder.metrics
                assert recorder.metrics['maximumStallMs']>=950,recorder.metrics
                assert camera.lastRequestId==original_id
                report={'format':'DaOS-desktop-ui-004-validation','physical_device':False,'renderer':'Qt5.15 software/offscreen',
                    'viewport':[800,480],'input_sequence':'50 gain ticks via Qt key input; final confirmed gain1; Q and workspaces',
                    'capture_count_changed':False,'baseline_before_faults':baseline,'injected_stalls_ms':injected,'including_injected_faults':dict(recorder.metrics),
                    'target_hardware_60fps_validated':False}
                (output/'performance-action-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                grab=window.contentItem().grabToImage()
                if grab is None:raise ValueError('Desktop frame grab did not start')
                pending_grabs.append(grab)
                def saved():app.exit(0 if grab.image().save(str(output/'desktop-actions-800x480.png')) else 3)
                grab.ready.connect(saved)
            except BaseException:
                import traceback
                errors.append(traceback.format_exc());print(errors[-1],file=sys.stderr);app.exit(3)
        QTimer.singleShot(1000,perform);QTimer.singleShot(20000,lambda:app.exit(4))
        return original()
    QGuiApplication.exec_=loop
    try:
        return launcher.main(['--offscreen','--backend','native','--native-command',str(output/'native-media/Release/hf-camerad.exe'),
                    '--data-dir',str(output/'performance-camera'),'--ui-state-dir',str(output/'performance-state')])
    finally:QGuiApplication.exec_=original
if __name__=='__main__':raise SystemExit(main())

import json
from pathlib import Path
import sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ui/on-device'))
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtCore import QCoreApplication
from performance import PerformanceRecorder

class PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def test_300_and_1000_ms_stalls_and_overlapping_actions_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[0];recorder=PerformanceRecorder(Path(directory)/'performance.json',clock=lambda:now[0])
            recorder.setActivity('animation',True);recorder.frameSwapped();recorder._pulse()
            first=recorder.beginAction('parameter','exposure');second=recorder.beginAction('page','gallery')
            now[0]+=300_000_000;recorder.frameSwapped();recorder._pulse();recorder.stage(first,'ack');recorder.firstVisible(first)
            now[0]+=1_000_000_000;recorder.frameSwapped();recorder._pulse();recorder.firstVisible(second)
            recorder.finish(first,'confirmed');recorder.finish(second,'shown')
            self.assertEqual([300.,1000.],list(recorder.frames));self.assertEqual(1000,recorder.metrics['maximumStallMs'])
            self.assertGreaterEqual(recorder.metrics['longStallEvents'],3)
            self.assertEqual([300.,1300.],list(recorder.inputs));self.assertEqual(2,recorder.metrics['actionsCompleted'])
            recorder.close();saved=json.loads((Path(directory)/'performance.json').read_text())
            self.assertEqual(1000,saved['frameMaxMs']);self.assertEqual(2,len(saved['actions']))
    def test_idle_gaps_separate_and_storage_is_bounded(self):
        now=[0];recorder=PerformanceRecorder(clock=lambda:now[0]);recorder.frameSwapped()
        now[0]+=10_000_000_000;recorder.frameSwapped();self.assertEqual(1,recorder.metrics['idleSwapGaps'])
        for i in range(1000):
            identity=recorder.beginAction('test','safe-control');recorder.firstVisible(identity);recorder.finish(identity,'done')
        self.assertEqual(256,len(recorder.completed_actions));self.assertEqual(512,len(recorder.inputs));recorder.close()
    def test_shortcut_consumed_keys_are_counted_once_without_recording_key_text(self):
        from PyQt5.QtCore import QObject,QEvent,Qt
        from PyQt5.QtGui import QKeyEvent
        now=[0];recorder=PerformanceRecorder(clock=lambda:now[0]);target=QObject()
        for index in range(50):
            now[0]+=1_000_000
            recorder.eventFilter(target,QKeyEvent(QEvent.ShortcutOverride,Qt.Key_Up,Qt.NoModifier))
            recorder.eventFilter(target,QKeyEvent(QEvent.KeyPress,Qt.Key_Up,Qt.NoModifier))
            recorder.eventFilter(target,QKeyEvent(QEvent.KeyRelease,Qt.Key_Up,Qt.NoModifier))
        recorder.frameSwapped();self.assertEqual(50,len(recorder.inputs));self.assertEqual(50,len(recorder.completed_actions))
        self.assertNotIn('Key_Up',json.dumps(list(recorder.completed_actions)));recorder.close()
    def test_preview_pause_excludes_idle_time_but_active_stalls_are_kept(self):
        now=[0];recorder=PerformanceRecorder(clock=lambda:now[0]);recorder.previewPresented(0)
        now[0]+=33_000_000;recorder.previewPresented(now[0]);recorder.setPreviewActive(False)
        now[0]+=10_000_000_000;recorder.setPreviewActive(True);recorder.previewPresented(now[0])
        now[0]+=33_000_000;recorder.previewPresented(now[0]);self.assertAlmostEqual(30.3,recorder.metrics['previewFps'],places=1)
        now[0]+=1_000_000_000;recorder.previewPresented(now[0]);self.assertEqual(1000,recorder.metrics['previewIntervalMaxMs'])
        self.assertLess(recorder.metrics['previewFps'],10);recorder.close()
if __name__=='__main__':unittest.main()

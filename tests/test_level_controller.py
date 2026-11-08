"""Local attitude input is optional, fresh, bounded, and display-only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui/on-device'))
from PyQt5.QtGui import QGuiApplication
import level_controller as module

class LevelControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.file=self.root/'attitude.json';self.now=12_000_000_000
        self.other=self.root/'system-preferences.json';self.other.write_bytes(b'{"language":"en_US","timezone":"UTC","idle_seconds":60,"developer_mode":false}\n')
        self.other_before=self.other.read_bytes()
    def controller(self,**kwargs):
        value=module.LevelController(self.root,clock_ns=lambda:self.now,poll_ms=0,**kwargs)
        self.addCleanup(value.close);return value
    def record(self,**changes):
        value={'format':'Project-Walnut-attitude-v1','valid':True,'monotonic_ns':self.now,'roll_deg':0.0,'pitch_deg':0.0}
        value.update(changes);self.file.write_text(json.dumps(value),encoding='utf-8');return value
    def unavailable(self,level,state):
        self.assertFalse(level.available);self.assertFalse(level.aligned)
        self.assertIsNone(level.rollDeg);self.assertIsNone(level.pitchDeg);self.assertEqual(level.state,state)

    def test_default_is_disabled_without_guessing_angles_or_touching_shell_preferences(self):
        level=self.controller();self.assertFalse(level.enabled);self.assertFalse(level.isDemo)
        self.unavailable(level,'disabled');self.assertFalse((self.root/'level-preferences.json').exists())
        self.assertEqual(self.other.read_bytes(),self.other_before)

    def test_enabled_without_provider_is_explicitly_unavailable(self):
        level=self.controller();self.assertTrue(level.setEnabled(True,persist=False))
        self.unavailable(level,'unavailable');self.assertEqual(self.other.read_bytes(),self.other_before)

    def test_enable_persists_only_independent_level_preferences(self):
        self.record();level=self.controller(attitude_file=self.file);self.assertTrue(level.setEnabled(True))
        settings=json.loads((self.root/'level-preferences.json').read_text())
        self.assertEqual(settings,{'format':'Project-Walnut-level-preferences-v1','enabled':True})
        again=self.controller(attitude_file=self.file);self.assertTrue(again.enabled);self.assertTrue(again.available)
        self.assertEqual(self.other.read_bytes(),self.other_before)

    def test_fresh_file_reports_real_source_angles_and_alignment(self):
        self.record();level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        self.assertTrue(level.available);self.assertTrue(level.aligned);self.assertFalse(level.isDemo);self.assertEqual(level.state,'ready')
        self.record(roll_deg=7.25,pitch_deg=-3.5);level.refresh()
        self.assertEqual((level.rollDeg,level.pitchDeg),(7.25,-3.5));self.assertFalse(level.aligned)

    def test_exact_500ms_boundary_then_stale_clears_previous_angles(self):
        self.record(monotonic_ns=self.now-500_000_000,roll_deg=1,pitch_deg=2)
        level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        self.assertTrue(level.available);self.now+=1;level.refresh();self.unavailable(level,'stale')

    def test_missing_provider_after_valid_sample_clears_values(self):
        self.record(roll_deg=4,pitch_deg=5);level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        self.assertTrue(level.available);self.file.unlink();level.refresh();self.unavailable(level,'unavailable')

    def test_invalid_flags_timestamps_and_nonfinite_or_out_of_range_angles_refused(self):
        level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        cases=[{'valid':False},{'valid':1},{'format':'foreign-attitude-v1'},
            {'monotonic_ns':0},{'monotonic_ns':self.now+1},{'monotonic_ns':float(self.now)},{'monotonic_ns':2**63},
            {'roll_deg':True},{'pitch_deg':False},{'roll_deg':float('nan')},{'pitch_deg':float('inf')},
            {'roll_deg':180.01},{'pitch_deg':-90.01},{'roll_deg':'0'},{'extra':1}]
        for change in cases:
            with self.subTest(change=change):self.record(**change);level.refresh();self.unavailable(level,'invalid')

    def test_boolean_timestamp_is_not_integer_even_when_age_would_be_fresh(self):
        self.now=2;self.record(monotonic_ns=True)
        level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False);self.unavailable(level,'invalid')

    def test_duplicate_json_key_is_refused(self):
        raw=json.dumps(self.record()).replace('"valid": true','"valid": true, "valid": true')
        self.file.write_text(raw);level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        self.unavailable(level,'invalid')

    def test_oversized_empty_and_directory_provider_never_produce_angles(self):
        level=self.controller(attitude_file=self.file);level.setEnabled(True,persist=False)
        for raw in (b'',b' '*4097):
            self.file.write_bytes(raw);level.refresh();self.unavailable(level,'invalid')
        self.file.unlink();self.file.mkdir();level.refresh();self.unavailable(level,'invalid')

    def test_corrupt_level_preferences_do_not_rewrite_any_configuration(self):
        path=self.root/'level-preferences.json';path.write_bytes(b'{broken')
        level=self.controller();self.assertTrue(level.settingsError);self.unavailable(level,'disabled')
        self.assertEqual(path.read_bytes(),b'{broken');self.assertEqual(self.other.read_bytes(),self.other_before)

    def test_failed_save_retains_previous_enabled_state_and_old_bytes(self):
        level=self.controller();self.assertTrue(level.setEnabled(False));path=self.root/'level-preferences.json';before=path.read_bytes()
        with patch.object(module,'atomic',side_effect=OSError('fixture read-only state')):
            self.assertFalse(level.setEnabled(True))
        self.assertFalse(level.enabled);self.assertTrue(level.settingsError);self.unavailable(level,'disabled')
        self.assertEqual(path.read_bytes(),before);self.assertEqual(self.other.read_bytes(),self.other_before)

    def test_demo_is_explicit_nonexpiring_and_never_persisted_as_measurement(self):
        level=self.controller(demo_attitude=(5.5,-2));level.setEnabled(True,persist=False)
        self.assertTrue(level.isDemo);self.assertTrue(level.available);self.assertEqual(level.state,'demo')
        self.now+=60_000_000_000;level.refresh();self.assertEqual((level.rollDeg,level.pitchDeg),(5.5,-2.0))
        self.assertFalse(level.setDemoAngles(float('nan'),0));self.assertEqual((level.rollDeg,level.pitchDeg),(5.5,-2.0))
        self.assertFalse((self.root/'level-preferences.json').exists());level.clearDemo();self.assertFalse(level.isDemo);self.unavailable(level,'unavailable')

    def test_clearing_demo_restores_fresh_file_provider(self):
        self.record(roll_deg=3,pitch_deg=-4);level=self.controller(attitude_file=self.file,demo_attitude=(9,8));level.setEnabled(True,persist=False)
        self.assertTrue(level.isDemo);level.clearDemo();self.assertFalse(level.isDemo)
        self.assertEqual((level.rollDeg,level.pitchDeg),(3.0,-4.0));self.assertTrue(level.available)

    def test_symbolic_link_provider_is_refused(self):
        self.record();link=self.root/'linked.json'
        try:link.symlink_to(self.file)
        except (OSError,NotImplementedError) as error:self.skipTest('Host does not permit private fixture symlinks: '+type(error).__name__)
        level=self.controller(attitude_file=link);level.setEnabled(True,persist=False);self.unavailable(level,'invalid')

    @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX named-pipe fixture only')
    def test_fifo_without_writer_is_rejected_without_blocking(self):
        fifo=self.root/'fifo';os.mkfifo(fifo);done=threading.Event();errors=[]
        def read():
            try:module.read_bounded(fifo)
            except Exception as error:errors.append(error)
            finally:done.set()
        thread=threading.Thread(target=read,daemon=True);thread.start();quick=done.wait(.4)
        if not quick:
            # Unblock a regressed blocking implementation so the fixture itself
            # does not leave an unfinished reader behind.
            try:
                descriptor=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK)
                try:os.write(descriptor,b'{}')
                finally:os.close(descriptor)
            except OSError:pass
            done.wait(.5)
        thread.join(timeout=.1);self.assertTrue(quick,'Attitude refresh blocked on a pipe')
        self.assertTrue(errors);self.assertIsInstance(errors[0],(ValueError,OSError))

if __name__=='__main__':unittest.main()

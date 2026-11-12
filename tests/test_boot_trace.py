import unittest
from tools.analyze_boot_trace import analyze


class BootTraceTests(unittest.TestCase):
    def test_actual_frame_and_session_deltas_do_not_claim_power_on_time(self):
        data=['DaOS_GUI_EVENT {"event":"session-start","monotonic_ns":1000000000}',
              'DaOS_GUI_EVENT {"event":"Xorg-started","monotonic_ns":1100000000}',
              'DaOS_BOOT_MARK {"event":"python-preload-start","monotonic_ns":1200000000}',
              'DaOS_BOOT_MARK {"event":"python-preload-end","monotonic_ns":1500000000}',
              'DaOS_GUI_EVENT {"event":"display-ready","monotonic_ns":1600000000}',
              'DaOS_BOOT_MARK {"event":"camera-frame-presented","monotonic_ns":1800000000,"authority_ready":true}']
        report=analyze(data)
        self.assertEqual(report['session_to_camera_frame_ms'],800)
        self.assertEqual(report['Xorg_to_display_ready_ms'],500)
        self.assertEqual(report['python_preload_ms'],300)
        self.assertIsNone(report['power_on_to_UI_ms'])
        self.assertTrue(report['authority_ready_at_frame'])

    def test_new_session_does_not_reuse_an_old_UI_marker(self):
        report=analyze(['DaOS_BOOT_MARK {"event":"camera-frame-presented","monotonic_ns":9000}',
                        'DaOS_GUI_EVENT {"event":"session-start","monotonic_ns":1}',
                        'DaOS_BOOT_MARK {"event":"camera-frame-presented","monotonic_ns":true}'])
        self.assertFalse(report['camera_frame_presented'])
        self.assertIsNone(report['session_to_camera_frame_ms'])

    def test_reversed_clocks_are_rejected(self):
        with self.assertRaises(ValueError):analyze([
            'DaOS_GUI_EVENT {"event":"session-start","monotonic_ns":10}',
            'DaOS_BOOT_MARK {"event":"camera-frame-presented","monotonic_ns":1}'])

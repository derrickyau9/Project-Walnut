import unittest
from frame_oracle import FrameOracle


class FrameOracleTests(unittest.TestCase):
    def test_valid_expected_pixels(self):
        oracle = FrameOracle(3, 2, 14)
        oracle.start(17, [[0, 1, 2], [3, 4, 16383]])
        oracle.line([0, 1, 2])
        oracle.line([3, 4, 16383])
        report = oracle.finish()
        self.assertTrue(report.valid)
        self.assertEqual((report.frame_id, report.lines_seen, report.pixels_seen), (17, 2, 6))

    def test_corruption_and_partial_frame(self):
        oracle = FrameOracle(3, 2, 14)
        oracle.start(1, [[1, 2, 3], [4, 5, 6]])
        oracle.line([1, -1])
        report = oracle.finish("link_lost")
        self.assertFalse(report.valid)
        self.assertEqual(set(report.errors), {"line_length", "line_count", "pixel_range", "pixel_mismatch", "link_lost"})

    def test_new_frame_reports_previous_interrupted_and_clears_state(self):
        oracle = FrameOracle(1, 1, 8)
        oracle.start(4)
        old = oracle.start(5)
        self.assertEqual(set(old.errors), {"interrupted", "line_count"})
        oracle.line([127])
        self.assertTrue(oracle.finish().valid)

    def test_extra_lines_and_outside_frame(self):
        oracle = FrameOracle(1, 1, 8)
        with self.assertRaises(RuntimeError):
            oracle.line([0])
        oracle.start(1)
        oracle.line([1])
        oracle.line([2])
        self.assertIn("line_count", oracle.finish().errors)


if __name__ == "__main__":
    unittest.main()

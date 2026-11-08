import unittest
from bandwidth import estimate


class BandwidthTests(unittest.TestCase):
    def test_full_frame_payload(self):
        result = estimate(6064, 4040, 14, 1, 8)
        self.assertEqual(result["pixels"], 24498560)
        self.assertEqual(result["packed_bytes_per_frame"], 42872480)
        self.assertEqual(result["byte_aligned_bytes_per_frame"], 48997120)
        self.assertEqual(result["byte_aligned_buffer_bytes"], 391976960)

    def test_round_up_packed(self):
        self.assertEqual(estimate(1, 1, 14, 1, 1)["packed_bytes_per_frame"], 2)

    def test_idealized_burst(self):
        self.assertEqual(estimate(100, 100, 16, 1, 1, 100, 25)["idealized_peak_backlog_bytes"], 15000)
        self.assertEqual(estimate(100, 100, 16, 1, 1, 100, 100)["idealized_peak_backlog_bytes"], 0)

    def test_rejects_invalid_inputs(self):
        for args in [(0, 1, 14, 1, 1), (1, 1, 14, float("nan"), 1), (1, 1, 14, 1, 1, 10, None), (1, 1, 14, 1, 1, 10, -1)]:
            with self.assertRaises(ValueError):
                estimate(*args)


if __name__ == "__main__":
    unittest.main()

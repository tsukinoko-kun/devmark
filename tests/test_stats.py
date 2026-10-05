import math
import unittest

from devmark.stats import Sampling, StopReason, summarize


class StatisticsTests(unittest.TestCase):
    def test_reports_all_samples_including_outliers(self):
        stats = summarize([10, 20, 30, 40, 1000])
        self.assertEqual(stats["runs"], 5)
        self.assertEqual(stats["min_ms"], 10)
        self.assertEqual(stats["max_ms"], 1000)
        self.assertEqual(stats["median_ms"], 30)
        self.assertEqual(stats["average_ms"], 220)

    def test_student_t_interval(self):
        stats = summarize([10, 20, 30, 40, 50])
        self.assertAlmostEqual(stats["ci95_half_width_ms"], 2.776 * math.sqrt(250) / math.sqrt(5))

    def test_stable_data_still_requires_minimum_runs(self):
        sampling = Sampling()
        self.assertIsNone(sampling.stop([100] * 4))
        self.assertEqual(sampling.stop([100] * 5), StopReason.PRECISION)

    def test_noisy_data_hits_hard_limit(self):
        sampling = Sampling(min_runs=3, max_runs=6)
        self.assertIsNone(sampling.stop([1, 1000, 1]))
        self.assertEqual(sampling.stop([1, 1000] * 3), StopReason.MAX_RUNS)

    def test_rejects_invalid_policies_and_samples(self):
        for minimum, maximum in ((2, 10), (6, 5), (5, 31)):
            with self.assertRaises(ValueError):
                Sampling(minimum, maximum)
        for error in (0, 1, float("nan")):
            with self.assertRaises(ValueError):
                Sampling(relative_error=error)
        for samples in ([], [0], [-1], [float("nan")], [float("inf")]):
            with self.assertRaises(ValueError):
                summarize(samples)


if __name__ == "__main__":
    unittest.main()

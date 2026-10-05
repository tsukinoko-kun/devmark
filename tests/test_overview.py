import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from devmark.overview import combined_scores, generate, render_svg


def save(directory, filename, cpu, timings, cores=8):
    path = directory / filename
    path.write_text(json.dumps({
        "hardware": {"cpu": {"name": cpu, "cores": cores}},
        "results": {key: {"median_ms": value} for key, value in timings.items()},
    }), encoding="utf-8")
    return path


class OverviewTests(unittest.TestCase):
    def test_workloads_have_equal_weight_despite_different_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", "A", {"clone": 10, "build": 10000})
            save(root, "b.json", "B", {"clone": 40, "build": 10000})
            scores, workloads = combined_scores(root)
        self.assertEqual(workloads, ["build", "clone"])
        self.assertEqual([score.cpu for score in scores], ["A", "B"])
        self.assertAlmostEqual(scores[0].value, 100)
        self.assertAlmostEqual(scores[1].value, 50)

    def test_repeated_measurements_use_median_and_only_shared_workloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a1.json", "A", {"clone": 10, "build": 1})
            save(root, "a2.json", "A", {"clone": 20})
            save(root, "a3.json", "A", {"clone": 1000})
            save(root, "b.json", "B", {"clone": 40})
            save(root, "empty.json", "Unavailable", {})
            scores, workloads = combined_scores(root)
        self.assertEqual(workloads, ["clone"])
        self.assertEqual([(score.cpu, score.measurements) for score in scores], [("A", 3), ("B", 1)])
        self.assertAlmostEqual(scores[1].value, 50)

    def test_cpu_variants_with_different_core_counts_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", "CPU", {"build": 10}, cores=8)
            save(root, "b.json", "CPU", {"build": 5}, cores=16)
            scores, _ = combined_scores(root)
        self.assertEqual([score.cores for score in scores], [16, 8])
        self.assertAlmostEqual(scores[0].value, 100)
        self.assertAlmostEqual(scores[1].value, 50)

    def test_empty_or_disjoint_results_produce_valid_empty_chart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(combined_scores(root), ([], []))
            save(root, "a.json", "A", {"clone": 10})
            save(root, "b.json", "B", {"build": 10})
            scores, _ = combined_scores(root)
        svg = ET.fromstring(render_svg(scores))
        self.assertIn("No comparable results", "".join(svg.itertext()))

    def test_svg_escapes_labels_and_contains_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", 'CPU <fast> & "new"', {"build&clone": 10})
            output = root / "docs" / "overview.svg"
            generate(root, output)
            svg = ET.fromstring(output.read_text(encoding="utf-8"))
        text = "".join(svg.itertext())
        self.assertIn('CPU <fast> & "new"', text)
        self.assertIn("100.0", text)

    def test_invalid_measurements_report_file_and_preserve_existing_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "overview.svg"
            output.write_text("existing chart", encoding="utf-8")
            for value in (0, -1, float("nan"), float("inf"), "10", True):
                with self.subTest(value=value):
                    save(root, "invalid.json", "A", {"build": value})
                    with self.assertRaisesRegex(ValueError, "invalid.json.*build.*median_ms"):
                        generate(root, output)
                    self.assertEqual(output.read_text(encoding="utf-8"), "existing chart")


if __name__ == "__main__":
    unittest.main()

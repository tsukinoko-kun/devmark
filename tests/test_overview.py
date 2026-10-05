import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from devmark.overview import Category, Summary, combined_scores, generate, render_svg


def save(directory, filename, cpu, timings, cores=8, kernel="Linux", version="6.16"):
    path = directory / filename
    path.write_text(json.dumps({
        "hardware": {"cpu": {"name": cpu, "cores": cores},
                     "kernel": {"name": kernel, "version": version}},
        "results": {key: {"median_ms": value} for key, value in timings.items()},
    }), encoding="utf-8")
    return path


class OverviewTests(unittest.TestCase):
    def test_svg_separates_file_system_and_cpu_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", "A", {"git-clone-web": 10, "git-clone-rust": 10,
                                      "git-clone-go": 10, "pnpm-install": 10,
                                      "vite-build": 40, "cargo-build": 40, "go-build": 40}, kernel="Linux")
            save(root, "b.json", "B", {"git-clone-web": 40, "git-clone-rust": 40,
                                      "git-clone-go": 40, "pnpm-install": 40,
                                      "vite-build": 10, "cargo-build": 10, "go-build": 10}, kernel="Darwin")
            output = root / "overview.svg"
            generate(root, output)
            svg = ET.fromstring(output.read_text(encoding="utf-8"))
        text = "".join(svg.itertext())
        self.assertIn("File system scores", text)
        self.assertIn("CPU scores", text)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        file_system_section = svg.find(".//svg:g[@id='file-system']", namespace)
        cpu_section = svg.find(".//svg:g[@id='cpu']", namespace)
        assert file_system_section is not None
        assert cpu_section is not None
        file_system = "".join(file_system_section.itertext())
        cpu = "".join(cpu_section.itertext())
        self.assertLess(file_system.index("Linux"), file_system.index("Darwin"))
        self.assertLess(cpu.index("\nB\n"), cpu.index("\nA\n"))
        self.assertIn("25.0", file_system)
        self.assertIn("25.0", cpu)

    def test_workloads_have_equal_weight_despite_different_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", "A", {"vite-build": 10, "cargo-build": 10000})
            save(root, "b.json", "B", {"vite-build": 40, "cargo-build": 10000})
            scores, workloads = combined_scores(root, Category.CPU)
        self.assertEqual(workloads, ["vite-build", "cargo-build"])
        self.assertEqual([score.name for score in scores], ["A", "B"])
        self.assertAlmostEqual(scores[0].value, 100)
        self.assertAlmostEqual(scores[1].value, 50)

    def test_repeated_measurements_use_median_and_only_shared_workloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a1.json", "A", {"vite-build": 10, "cargo-build": 1})
            save(root, "a2.json", "A", {"vite-build": 20})
            save(root, "a3.json", "A", {"vite-build": 1000})
            save(root, "b.json", "B", {"vite-build": 40})
            save(root, "empty.json", "Unavailable", {})
            scores, workloads = combined_scores(root, Category.CPU)
        self.assertEqual(workloads, ["vite-build"])
        self.assertEqual([(score.name, score.measurements) for score in scores], [("A", 3), ("B", 1)])
        self.assertAlmostEqual(scores[1].value, 50)

    def test_cpu_variants_with_different_core_counts_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", "CPU", {"vite-build": 10}, cores=8)
            save(root, "b.json", "CPU", {"vite-build": 5}, cores=16)
            scores, _ = combined_scores(root, Category.CPU)
        self.assertEqual([score.detail for score in scores], ["16 cores", "8 cores"])
        self.assertAlmostEqual(scores[0].value, 100)
        self.assertAlmostEqual(scores[1].value, 50)

    def test_empty_or_disjoint_results_produce_valid_empty_chart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(combined_scores(root, Category.CPU), ([], []))
            save(root, "a.json", "A", {"vite-build": 10})
            save(root, "b.json", "B", {"cargo-build": 10})
            scores, workloads = combined_scores(root, Category.CPU)
        svg = ET.fromstring(render_svg([Summary(Category.CPU, scores, workloads)]))
        self.assertIn("No comparable results", "".join(svg.itertext()))

    def test_svg_escapes_labels_and_contains_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a.json", 'CPU <fast> & "new"', {"vite-build": 10},
                 kernel="Kernel <fast> & new", version="1 & 2")
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
                    save(root, "invalid.json", "A", {"vite-build": value})
                    with self.assertRaisesRegex(ValueError, "invalid.json.*vite-build.*median_ms"):
                        generate(root, output)
                    self.assertEqual(output.read_text(encoding="utf-8"), "existing chart")

    def test_file_system_groups_by_kernel_and_version_across_cpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root, "a1.json", "A", {"git-clone-web": 10}, version="6.16")
            save(root, "a2.json", "B", {"git-clone-web": 30}, version="6.16")
            save(root, "b.json", "A", {"git-clone-web": 40}, version="6.17")
            save(root, "build-only.json", "C", {"vite-build": 1}, kernel="Darwin")
            scores, workloads = combined_scores(root, Category.FILE_SYSTEM)
        self.assertEqual(workloads, ["git-clone-web"])
        self.assertEqual([(score.name, score.detail, score.measurements) for score in scores],
                         [("Linux", "6.16", 2), ("Linux", "6.17", 1)])
        self.assertAlmostEqual(scores[1].value, 50)


if __name__ == "__main__":
    unittest.main()

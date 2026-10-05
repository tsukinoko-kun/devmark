import argparse
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console

import devmark.__main__ as cli
from devmark.workloads import Workload


class RunTests(unittest.TestCase):
    def test_interrupt_cancels_run_and_cleans_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(
                only=["cargo-build", "go-build"], min_runs=3, max_runs=3,
                relative_error=0.05, timeout=5, keep_workspace=False, list=False,
            )
            output = io.StringIO()
            def prepare(test):
                workspace = next((root / ".devmark" / "runs").iterdir()) / "sources"
                workspace.mkdir()
                return Workload([], workspace, {}, lambda: None)
            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "arguments", return_value=args),
                patch.object(cli, "collect", return_value={}),
                patch.object(cli, "Console", return_value=Console(file=output)),
                patch.object(cli.Suite, "prepare", side_effect=prepare) as prepared,
                patch.object(cli.Runner, "run", side_effect=KeyboardInterrupt),
                patch.object(cli, "generate") as generate,
            ):
                self.assertEqual(cli.main(), 130)
            prepared.assert_called_once_with("cargo-build")
            generate.assert_not_called()
            self.assertEqual(list((root / "measurments").glob("*.json*")), [])
            run_dir = next((root / ".devmark" / "runs").iterdir())
            self.assertFalse((run_dir / "sources").exists())
            self.assertTrue((run_dir / "logs").exists())
            self.assertIn("interrupted", output.getvalue())

    def test_success_saves_measurement_then_generates_overview(self):
        for graph_error in (None, ValueError("Invalid saved measurement")):
            with self.subTest(graph_error=graph_error), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = argparse.Namespace(
                    only=["git-clone-web"], min_runs=3, max_runs=3,
                    relative_error=0.05, timeout=5, keep_workspace=False, list=False,
                )
                measurement = {
                    "hardware": {"cpu": {"name": "Test CPU", "cores": 8}},
                    "results": {"git-clone-web": {"median_ms": 100}},
                }
                output = io.StringIO()
                workload = Workload([], root, {}, lambda: None)
                with (
                    patch.object(cli, "ROOT", root),
                    patch.object(cli, "arguments", return_value=args),
                    patch.object(cli, "collect", return_value={}),
                    patch.object(cli, "measurement", return_value=measurement),
                    patch.object(cli, "Console", return_value=Console(file=output)),
                    patch.object(cli.Suite, "prepare", return_value=workload),
                    patch.object(cli.Runner, "run", return_value=100),
                    patch.object(cli, "generate", wraps=cli.generate, side_effect=graph_error) as generate,
                ):
                    self.assertEqual(cli.main(), 1 if graph_error else 0)
                self.assertEqual(len(list((root / "measurments").glob("*.json"))), 1)
                generate.assert_called_once_with(root / "measurments", root / "docs" / "overview.svg")
                if graph_error:
                    self.assertIn("Measurement saved, but cannot generate overview", output.getvalue())
                else:
                    self.assertIn("100.0", (root / "docs" / "overview.svg").read_text())

    def test_command_failure_aborts_remaining_workloads_without_measurement_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = []

            def prepare(test):
                prepared.append(test)
                code = "raise SystemExit(7)" if test == "pnpm-install" else "pass"
                return Workload([sys.executable, "-c", code], root, {}, lambda: None)

            args = argparse.Namespace(
                only=["git-clone-web", "pnpm-install", "vite-build"],
                min_runs=3, max_runs=3, relative_error=0.05, timeout=5,
                keep_workspace=False, list=False,
            )
            output = io.StringIO()
            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "arguments", return_value=args),
                patch.object(cli, "collect", return_value={}),
                patch.object(cli, "Console", return_value=Console(file=output)),
                patch.object(cli.Suite, "prepare", side_effect=prepare),
                patch.object(cli, "generate") as generate,
            ):
                self.assertEqual(cli.main(), 1)
            self.assertEqual(prepared, ["git-clone-web", "pnpm-install"])
            self.assertEqual(list((root / "measurments").glob("*.json*")), [])
            self.assertIn("Exit 7", output.getvalue())
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

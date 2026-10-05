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
            ):
                self.assertEqual(cli.main(), 1)
            self.assertEqual(prepared, ["git-clone-web", "pnpm-install"])
            self.assertEqual(list((root / "measurments").glob("*.json*")), [])
            self.assertIn("Exit 7", output.getvalue())


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import sys
import tempfile
import time
import unittest

from devmark.process import CommandError, Runner


class ProcessTests(unittest.TestCase):
    def test_measures_wall_time_and_captures_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = Runner(root / "logs", timeout=5)
            elapsed = runner.run([sys.executable, "-c", "import time; print('hello'); time.sleep(0.05)"], root)
            self.assertGreaterEqual(elapsed, 50)
            self.assertIn("hello", next((root / "logs").iterdir()).read_text())

    def test_failure_preserves_exit_code_and_log_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(CommandError, "Exit 7") as error:
                Runner(root / "logs").run([sys.executable, "-c", "print('explanation'); raise SystemExit(7)"], root)
            self.assertIn("explanation", str(error.exception))

    def test_timeout_stops_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = time.monotonic()
            with self.assertRaisesRegex(CommandError, "Timed out"):
                Runner(root / "logs", timeout=0.1).run([sys.executable, "-c", "import time; time.sleep(60)"], root)
            self.assertLess(time.monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()

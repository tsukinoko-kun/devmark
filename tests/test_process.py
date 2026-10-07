from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

from devmark.process import CommandError, Runner, probe


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

    def test_probe_captures_output_and_handles_failure(self):
        self.assertEqual(probe([sys.executable, "-c", "print('version')"]), "version")
        self.assertIsNone(probe([sys.executable, "-c", "raise SystemExit(7)"]))

    def test_interrupt_stops_command_and_descendants(self):
        for mode in ("run", "probe"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                grandchild = "import os, pathlib, time; pathlib.Path('grandchild.pid').write_text(str(os.getpid())); time.sleep(60)"
                child = (
                    "import os, pathlib, subprocess, sys, time; "
                    "pathlib.Path('child.pid').write_text(str(os.getpid())); "
                    f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
                )
                controller = f"""
import os, signal, sys, threading, time
from pathlib import Path
from devmark.process import Runner, probe
root = Path({str(root)!r})
def interrupt():
    deadline = time.monotonic() + 5
    while not (root / 'grandchild.pid').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if os.name == 'nt':
        signal.raise_signal(signal.SIGINT)
    else:
        os.kill(os.getpid(), signal.SIGINT)
thread = threading.Thread(target=interrupt, daemon=True)
thread.start()
try:
    command = [sys.executable, '-c', {child!r}]
    if {mode!r} == 'run':
        Runner(root / 'logs').run(command, root)
    else:
        probe(command, cwd=root)
except KeyboardInterrupt:
    print('cancelled', flush=True)
    thread.join()
    sys.exit(130)
sys.exit('Interrupt was not handled')
"""
                processes = []
                try:
                    start = time.monotonic()
                    result = subprocess.run(
                        [sys.executable, "-c", controller], capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 130, result.stderr)
                    self.assertIn("cancelled", result.stdout)
                    self.assertLess(time.monotonic() - start, 5)
                    for name in ("child.pid", "grandchild.pid"):
                        pid = int((root / name).read_text())
                        try:
                            process = psutil.Process(pid)
                        except psutil.NoSuchProcess:
                            continue
                        processes.append(process)
                    _, alive = psutil.wait_procs(processes, timeout=2)
                    self.assertFalse([p for p in alive if p.status() != psutil.STATUS_ZOMBIE])
                finally:
                    for name in ("child.pid", "grandchild.pid"):
                        if (root / name).exists():
                            try:
                                psutil.Process(int((root / name).read_text())).kill()
                            except psutil.NoSuchProcess:
                                pass


if __name__ == "__main__":
    unittest.main()

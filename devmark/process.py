import os
from pathlib import Path
import signal
import subprocess
import threading
import time


class CommandError(RuntimeError):
    pass


def kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Runner:
    def __init__(self, log_dir: Path, timeout: float = 7200) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.counter = 0

    def run(self, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> float:
        self.counter += 1
        log_path = self.log_dir / f"{self.counter:04d}-{Path(command[0]).stem}.log"
        done = threading.Event()
        timed_out = threading.Event()
        environment = os.environ | (env or {})
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"cwd: {cwd}\ncommand: {command!r}\n\n")
            log.flush()
            start = time.perf_counter_ns()
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, env=environment, stdout=log,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            except OSError as error:
                raise CommandError(f"Cannot start {command[0]}: {error}. Log: {log_path}") from error

            def watchdog() -> None:
                if not done.wait(self.timeout):
                    timed_out.set()
                    kill_tree(process)

            watcher = threading.Thread(target=watchdog, daemon=True)
            watcher.start()
            try:
                # An infinite Windows wait delays Python's Ctrl+C handler until
                # the child exits. Bounded waits let pending signals run.
                if os.name == "nt":
                    while True:
                        try:
                            returncode = process.wait(timeout=0.1)
                            break
                        except subprocess.TimeoutExpired:
                            pass
                else:
                    returncode = process.wait()
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
            except BaseException:
                kill_tree(process)
                process.wait()
                raise
            finally:
                done.set()
                watcher.join()
        if timed_out.is_set() or returncode:
            with log_path.open("rb") as log:
                log.seek(max(0, log_path.stat().st_size - 4000))
                tail = log.read().decode("utf-8", errors="replace")
            reason = f"Timed out after {self.timeout:g}s" if timed_out.is_set() else f"Exit {returncode}"
            raise CommandError(f"{reason}: {command!r}\nLog: {log_path}\n{tail}")
        return elapsed


def probe(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str | None:
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=os.environ | (env or {}), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError:
        return None
    deadline = time.monotonic() + 30
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_tree(process)
                process.communicate()
                return None
            try:
                stdout, _ = process.communicate(timeout=min(0.1, remaining))
                return stdout.strip() if process.returncode == 0 else None
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        kill_tree(process)
        process.communicate()
        raise

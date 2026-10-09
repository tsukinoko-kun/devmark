import argparse
from datetime import datetime, timezone
from enum import StrEnum
import json
import math
from pathlib import Path
import shutil
import time
import uuid

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .hardware import collect
from .overview import generate
from .process import Runner
from .stats import Sampling, summarize
from .workloads import ROOT, TESTS, Skipped, Suite, remove_owned


class Status(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    WARMUP = "warmup"
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NOT_RUN = "not_run"


def measurement(hardware: dict, records: list[dict]) -> dict:
    return {
        "hardware": {
            "computer_model": hardware["computer_model"],
            "cpu": {
                "name": hardware["cpu"]["name"],
                "cores": hardware["cpu"]["physical_cores"],
                "clock_mhz": hardware["cpu"]["clock_mhz"],
            },
            "gpus": [{key: gpu[key] for key in ("name", "vram_bytes")} for gpu in hardware["gpus"]],
            "ram": {key: hardware["ram"][key] for key in ("name", "size_bytes", "speed", "standard")},
            "os_name": hardware["os_name"],
            "kernel": {key: hardware["kernel"][key] for key in ("name", "version")},
        },
        "results": {
            record["id"]: {key: record["statistics"][key] for key in ("min_ms", "max_ms", "median_ms", "average_ms")}
            for record in records if record["statistics"] is not None
        },
    }


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Display:
    def __init__(self, records: list[dict], maximum: int) -> None:
        self.records = records
        self.maximum = maximum
        self.phase = "Collect hardware information"
        self.started = time.monotonic()

    def update(self, phase: str) -> None:
        self.phase = phase

    def __rich__(self):
        table = Table(expand=True)
        for label in ("Workload", "Status", "Runs", "Median ms"):
            table.add_column(label, justify="right" if label in ("Runs", "Median ms") else "left")
        for record in self.records:
            status = record["status"]
            style = {Status.OK: "green", Status.SKIPPED: "yellow", Status.FAILED: "red", Status.INTERRUPTED: "red"}.get(status, "cyan")
            samples = record["samples_ms"]
            median = f"{summarize(samples)['median_ms']:,.1f}" if samples else "-"
            table.add_row(Text(record["name"]), Text(status, style=style), f"{len(samples)}/{self.maximum}", median)
        elapsed = time.monotonic() - self.started
        return Panel(Group(
            table, Spinner("dots", text=Text(self.phase)),
            Text(f"Elapsed {elapsed / 60:.1f} min", style="dim"),
        ), title="devmark", border_style="blue")


def save_locks(workload, run_dir: Path, test: str) -> None:
    for name in ("pnpm-lock.yaml", "pnpm-workspace.yaml", "Cargo.lock", "go.mod", "go.sum"):
        source = workload.cwd / name
        if not source.is_file():
            continue
        destination = run_dir / "locks" / test / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def summary(console: Console, records: list[dict], path: Path | None, run_dir: Path) -> None:
    table = Table(title="Results · milliseconds", expand=True)
    for label in ("Workload", "Status", "Runs", "Min", "Max", "Median", "Average"):
        table.add_column(label, justify="left" if label in ("Workload", "Status") else "right")
    for record in records:
        stats = record.get("statistics")
        numbers = [f"{stats[key]:,.1f}" for key in ("min_ms", "max_ms", "median_ms", "average_ms")] if stats else ["-"] * 4
        table.add_row(Text(record["name"]), record["status"], str(len(record["samples_ms"])), *numbers)
    console.print(table)
    for record in records:
        if record.get("reason"):
            console.print(Text(f"{record['name']}: {record['reason']}"))
        if record.get("stop_reason") == "max_runs_reached":
            console.print(Text(f"{record['name']}: maximum runs reached before the precision target.", style="yellow"))
    if path is not None:
        console.print(Text(f"JSON: {path}"))
    console.print(Text(f"Logs and dependency locks: {run_dir}", style="dim"))


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark clean developer workloads with warm dependency caches.")
    parser.add_argument("--only", choices=list(TESTS), nargs="+", help="Run only these workloads.")
    parser.add_argument("--min-runs", type=int, default=5)
    parser.add_argument("--max-runs", type=int, default=10, help="Hard limit per workload, at most 30.")
    parser.add_argument("--relative-error", type=float, default=0.05, help="Target 95%% interval half-width divided by mean.")
    parser.add_argument("--timeout", type=float, default=7200, help="Maximum seconds for each command.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep source checkouts and build outputs for inspection.")
    parser.add_argument("--list", action="store_true", help="List workloads without downloading source or probing hardware.")
    args = parser.parse_args()
    try:
        Sampling(args.min_runs, args.max_runs, args.relative_error)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be a finite positive number of seconds.")
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    args = arguments()
    console = Console()
    if args.list:
        for key, (name, _) in TESTS.items():
            console.print(Text(f"{key:18} {name}"))
        return 0
    policy = Sampling(args.min_runs, args.max_runs, args.relative_error)
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = ROOT / ".devmark" / "runs" / identity
    run_dir.mkdir(parents=True)
    output_dir = ROOT / "measurements"
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{identity}.json"
    records: list[dict] = [{
        "id": key, "name": name,
        "status": Status.PENDING, "samples_ms": [], "statistics": None,
    } for key, (name, _) in TESTS.items() if args.only is None or key in args.only]
    display = Display(records, policy.max_runs)
    runner = Runner(run_dir / "logs", args.timeout)
    suite = Suite(ROOT / ".devmark", run_dir, runner, display.update)
    current = None
    exit_code = 0
    try:
        with Live(display, console=console, refresh_per_second=4, auto_refresh=console.is_terminal):
            suite.install({record["id"] for record in records})
            display.update("Collect hardware information")
            hardware = collect()
            for record in records:
                current = record
                record["status"] = Status.PREPARING
                display.update(f"Prepare {record['name']} · not timed")
                try:
                    workload = suite.prepare(record["id"])
                    save_locks(workload, run_dir, record["id"])
                    record["status"] = Status.WARMUP
                    display.update(f"Warm up {record['name']} · not timed")
                    workload.reset()
                    runner.run(workload.command, workload.cwd, workload.env)
                    record["status"] = Status.RUNNING
                    while True:
                        display.update(f"Clean outputs for {record['name']} · not timed")
                        workload.reset()
                        display.update(f"{record['name']} · measured run {len(record['samples_ms']) + 1}/{policy.max_runs}")
                        elapsed = runner.run(workload.command, workload.cwd, workload.env)
                        record["samples_ms"].append(elapsed)
                        record["statistics"] = summarize(record["samples_ms"])
                        stop = policy.stop(record["samples_ms"])
                        if stop:
                            record["status"] = Status.OK
                            record["stop_reason"] = stop
                        if stop:
                            break
                except Skipped as error:
                    record["status"] = Status.SKIPPED
                    record["reason"] = str(error)
            display.update("Finished")
    except KeyboardInterrupt:
        if current:
            current["status"] = Status.INTERRUPTED
            current["reason"] = "Interrupted by user."
        exit_code = 130
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        if current:
            current["status"] = Status.FAILED
            current["reason"] = reason
        else:
            console.print(Text(reason, style="red"))
        exit_code = 1
    finally:
        for record in records:
            if record["status"] == Status.PENDING:
                record["status"] = Status.NOT_RUN
        try:
            for child in run_dir.iterdir():
                if child.name not in ("logs", "locks") and (not args.keep_workspace or child.name == "toolchains"):
                    remove_owned(child, run_dir)
        except OSError as error:
            console.print(Text(f"Cleanup failed: {error}", style="red"))
            if exit_code == 0:
                exit_code = 1
    if exit_code == 0:
        try:
            write_json(path, measurement(hardware, records))
        except (OSError, ValueError) as error:
            console.print(Text(f"Cannot save measurement: {error}", style="red"))
            exit_code = 1
    summary(console, records, path if exit_code == 0 else None, run_dir)
    if exit_code == 0:
        overview = ROOT / "docs" / "overview.svg"
        try:
            generate(output_dir, overview)
            console.print(Text(f"SVG: {overview}"))
        except (OSError, ValueError) as error:
            console.print(Text(f"Measurement saved, but cannot generate overview: {error}", style="red"))
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

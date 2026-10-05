import argparse
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
import json
import math
from pathlib import Path
import statistics
import textwrap
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parent.parent


class Category(StrEnum):
    FILE_SYSTEM = "file-system"
    CPU = "cpu"


WORKLOADS = {
    Category.FILE_SYSTEM: ("git-clone-web", "git-clone-rust", "git-clone-go", "pnpm-install"),
    Category.CPU: ("vite-build", "cargo-build", "go-build"),
}


@dataclass(frozen=True)
class Score:
    name: str
    detail: str
    value: float
    measurements: int


@dataclass(frozen=True)
class Summary:
    category: Category
    scores: list[Score]
    workloads: list[str]


def combined_scores(directory: Path, category: Category) -> tuple[list[Score], list[str]]:
    if not directory.is_dir():
        raise ValueError(f"Measurement directory does not exist: {directory}")
    timings = defaultdict(lambda: defaultdict(list))
    counts = defaultdict(int)
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results = data["results"]
            if not isinstance(results, dict):
                raise ValueError("Results must be an object.")
            for workload, result in results.items():
                median = result["median_ms"]
                if type(median) not in (int, float) or not math.isfinite(median) or median <= 0:
                    raise ValueError(f"{workload}: median_ms must be finite positive milliseconds.")
            relevant = {workload: results[workload] for workload in WORKLOADS[category] if workload in results}
            if not relevant:
                continue
            if category == Category.CPU:
                cpu = data["hardware"]["cpu"]
                name = cpu["name"] if cpu["name"] is not None else "Unknown CPU"
                cores = cpu["cores"]
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("CPU name must be a nonempty string or null.")
                if cores is not None and (type(cores) is not int or cores <= 0):
                    raise ValueError("CPU cores must be a positive integer or null.")
                detail = f"{cores} cores" if cores is not None else "Unknown core count"
            else:
                kernel = data["hardware"]["kernel"]
                name = kernel["name"] if kernel["name"] is not None else "Unknown kernel"
                version = kernel["version"]
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Kernel name must be a nonempty string or null.")
                if version is not None and (not isinstance(version, str) or not version.strip()):
                    raise ValueError("Kernel version must be a nonempty string or null.")
                detail = version.strip() if version is not None else "Unknown kernel version"
            key = (name.strip(), detail)
            for workload, result in relevant.items():
                timings[key][workload].append(result["median_ms"])
            counts[key] += 1
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid measurement {path}: {error}") from error

    if not timings:
        return [], []
    shared = set.intersection(*(set(results) for results in timings.values()))
    workloads = [workload for workload in WORKLOADS[category] if workload in shared]
    if not workloads:
        return [], []
    medians = {
        key: {workload: statistics.median(results[workload]) for workload in workloads}
        for key, results in timings.items()
    }
    fastest = {workload: min(results[workload] for results in medians.values()) for workload in workloads}
    combined = {
        key: statistics.mean(math.log(fastest[workload]) - math.log(results[workload]) for workload in workloads)
        for key, results in medians.items()
    }
    best = max(combined.values())
    scores = [Score(name, detail, 100 * math.exp(value - best), counts[(name, detail)])
              for (name, detail), value in combined.items()]
    scores.sort(key=lambda score: (-score.value, score.name, score.detail))
    return scores, workloads


def render_svg(summaries: list[Summary]) -> str:
    width = 1100
    chart_x = 360
    chart_width = 660
    y = 100
    sections = []
    for summary in summaries:
        top = y
        y += 124
        rows = []
        for score in summary.scores:
            lines = textwrap.wrap(score.name, width=34)
            details = textwrap.wrap(score.detail, width=44)
            row_height = max(76, len(lines) * 20 + len(details) * 16 + 24)
            rows.append((score, lines, details, y, row_height))
            y += row_height
        chart_bottom = y if rows else y + 70
        sections.append((summary, top, rows, chart_bottom))
        y = chart_bottom + 24
    height = y
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
        '<title id="title">Devmark file system and CPU scores</title>',
        f'<rect width="{width}" height="{height}" rx="16" fill="#0f172a"/>',
        '<g font-family="system-ui, sans-serif" fill="#e2e8f0">',
        '<text x="32" y="46" font-size="26" font-weight="700">Devmark scores</text>',
        '<text x="32" y="74" font-size="14" fill="#94a3b8">Equal-weight geometric mean of relative speeds. Best in each section = 100. Higher is better.</text>',
    ]
    for summary, top, rows, chart_bottom in sections:
        title = "CPU scores" if summary.category == Category.CPU else "File system scores"
        description = ("Build tasks grouped by CPU and core count." if summary.category == Category.CPU else
                       "Clone and install tasks grouped by kernel and version. Storage and other hardware also affect results.")
        svg += [
            f'<g id="{summary.category}">',
            f'<text x="32" y="{top + 36}" font-size="22" font-weight="700">{title}</text>',
            f'<text x="32" y="{top + 60}" font-size="14" fill="#94a3b8">{description}</text>',
            f'<text x="32" y="{top + 82}" font-size="12" fill="#94a3b8">Shared workloads: {escape(", ".join(summary.workloads)) or "none"}</text>',
        ]
        if rows:
            for tick in (0, 25, 50, 75, 100):
                x = chart_x + chart_width * tick / 100
                svg += [
                    f'<line x1="{x}" y1="{top + 114}" x2="{x}" y2="{chart_bottom}" stroke="#334155"/>',
                    f'<text x="{x}" y="{top + 104}" text-anchor="middle" font-size="12" fill="#94a3b8">{tick}</text>',
                ]
            for score, lines, details, row_top, row_height in rows:
                center = row_top + row_height / 2
                label_y = center - (len(lines) * 20 + len(details) * 16) / 2 + 14
                for index, line in enumerate(lines):
                    svg.append(f'<text x="32" y="{label_y + index * 20}" font-size="16" font-weight="600">{escape(line)}</text>')
                for index, detail in enumerate(details):
                    svg.append(f'<text x="32" y="{label_y + len(lines) * 20 + index * 16}" font-size="12" fill="#94a3b8">{escape(detail)}</text>')
                color = "#34d399" if summary.category == Category.FILE_SYSTEM else "#38bdf8"
                svg += [
                    f'<rect x="{chart_x}" y="{center - 17}" width="{chart_width * score.value / 100:.2f}" height="34" rx="5" fill="{color}"/>',
                    f'<text x="1068" y="{center + 5}" text-anchor="end" font-size="16" font-weight="600">{score.value:.1f}</text>',
                ]
        else:
            svg.append(f'<text x="32" y="{top + 150}" font-size="16">No comparable results.</text>')
        svg.append('</g>')
    svg += ["</g>", "</svg>"]
    return "\n".join(svg) + "\n"


def generate(directory: Path, output: Path) -> None:
    summaries = [Summary(category, *combined_scores(directory, category)) for category in Category]
    svg = render_svg(summaries)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(svg, encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate file system and CPU score summaries from saved Devmark measurements.")
    parser.add_argument("--measurements", type=Path, default=ROOT / "measurments")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "overview.svg")
    args = parser.parse_args()
    try:
        generate(args.measurements, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot generate overview: {error}\n")
    print(f"SVG: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import textwrap
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Score:
    cpu: str
    cores: int | None
    value: float
    measurements: int


def combined_scores(directory: Path) -> tuple[list[Score], list[str]]:
    if not directory.is_dir():
        raise ValueError(f"Measurement directory does not exist: {directory}")
    timings = defaultdict(lambda: defaultdict(list))
    counts = defaultdict(int)
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cpu = data["hardware"]["cpu"]
            name = cpu["name"] if cpu["name"] is not None else "Unknown CPU"
            cores = cpu["cores"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("CPU name must be a nonempty string or null.")
            if cores is not None and (type(cores) is not int or cores <= 0):
                raise ValueError("CPU cores must be a positive integer or null.")
            key = (name.strip(), cores)
            results = data["results"]
            if not isinstance(results, dict):
                raise ValueError("Results must be an object.")
            for workload, result in results.items():
                median = result["median_ms"]
                if type(median) not in (int, float) or not math.isfinite(median) or median <= 0:
                    raise ValueError(f"{workload}: median_ms must be finite positive milliseconds.")
                timings[key][workload].append(median)
            if results:
                counts[key] += 1
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid measurement {path}: {error}") from error

    if not timings:
        return [], []
    workloads = sorted(set.intersection(*(set(results) for results in timings.values())))
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
    scores = [Score(name, cores, 100 * math.exp(value - best), counts[(name, cores)])
              for (name, cores), value in combined.items()]
    scores.sort(key=lambda score: (-score.value, score.cpu, score.cores or 0))
    return scores, workloads


def render_svg(scores: list[Score]) -> str:
    width = 1100
    chart_x = 360
    chart_width = 660
    y = 110
    rows = []
    for score in scores:
        lines = textwrap.wrap(score.cpu, width=34)
        row_height = max(76, len(lines) * 20 + 40)
        rows.append((score, lines, y, row_height))
        y += row_height
    chart_bottom = y if rows else y + 70
    height = chart_bottom + 24
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
        '<title id="title">Devmark combined scores per CPU</title>',
        f'<rect width="{width}" height="{height}" rx="16" fill="#0f172a"/>',
        '<g font-family="system-ui, sans-serif" fill="#e2e8f0">',
        '<text x="32" y="46" font-size="26" font-weight="700">Combined CPU scores</text>',
    ]
    if rows:
        for tick in (0, 25, 50, 75, 100):
            x = chart_x + chart_width * tick / 100
            svg += [
                f'<line x1="{x}" y1="96" x2="{x}" y2="{chart_bottom}" stroke="#334155"/>',
                f'<text x="{x}" y="86" text-anchor="middle" font-size="12" fill="#94a3b8">{tick}</text>',
            ]
        for score, lines, top, row_height in rows:
            center = top + row_height / 2
            label_y = center - (len(lines) - 1) * 10 - 4
            for index, line in enumerate(lines):
                svg.append(f'<text x="32" y="{label_y + index * 20}" font-size="16" font-weight="600">{escape(line)}</text>')
            cores = f"{score.cores} cores" if score.cores is not None else "Unknown core count"
            svg += [
                f'<text x="32" y="{label_y + len(lines) * 20}" font-size="12" fill="#94a3b8">{cores}</text>',
                f'<rect x="{chart_x}" y="{center - 17}" width="{chart_width * score.value / 100:.2f}" height="34" rx="5" fill="#38bdf8"/>',
                f'<text x="1068" y="{center + 5}" text-anchor="end" font-size="16" font-weight="600">{score.value:.1f}</text>',
            ]
    else:
        svg.append('<text x="32" y="135" font-size="16">No comparable results.</text>')
    svg += ["</g>", "</svg>"]
    return "\n".join(svg) + "\n"


def generate(directory: Path, output: Path) -> None:
    scores, _ = combined_scores(directory)
    svg = render_svg(scores)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(svg, encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a CPU overview SVG from saved Devmark measurements.")
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

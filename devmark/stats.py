from dataclasses import dataclass
from enum import StrEnum
import math
import statistics


# Two-sided 95% Student's t quantiles, degrees of freedom 1 through 29.
T_95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306,
    2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120,
    2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064,
    2.060, 2.056, 2.052, 2.048, 2.045,
)


class StopReason(StrEnum):
    PRECISION = "precision_reached"
    MAX_RUNS = "max_runs_reached"


@dataclass(frozen=True)
class Sampling:
    min_runs: int = 5
    max_runs: int = 10
    relative_error: float = 0.05

    def __post_init__(self) -> None:
        if not 3 <= self.min_runs <= self.max_runs <= 30:
            raise ValueError("Require 3 <= min-runs <= max-runs <= 30.")
        if not 0 < self.relative_error < 1:
            raise ValueError("Relative error must be between 0 and 1.")

    def stop(self, samples: list[float]) -> StopReason | None:
        if len(samples) >= self.min_runs:
            summary = summarize(samples)
            if summary["relative_ci_half_width"] <= self.relative_error:
                return StopReason.PRECISION
        if len(samples) >= self.max_runs:
            return StopReason.MAX_RUNS
        return None


def summarize(samples: list[float]) -> dict:
    if not samples or any(not math.isfinite(x) or x <= 0 for x in samples):
        raise ValueError("Samples must be finite positive milliseconds.")
    count = len(samples)
    mean = statistics.mean(samples)
    deviation = statistics.stdev(samples) if count > 1 else None
    half_width = (
        T_95[min(count - 2, len(T_95) - 1)] * deviation / math.sqrt(count)
        if deviation is not None else None
    )
    return {
        "runs": count,
        "min_ms": min(samples),
        "max_ms": max(samples),
        "median_ms": statistics.median(samples),
        "average_ms": mean,
        "stddev_ms": deviation,
        "ci95_half_width_ms": half_width,
        "relative_ci_half_width": half_width / mean if half_width is not None else None,
    }

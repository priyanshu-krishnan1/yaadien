"""
benchmarks/common/timing.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Latency measurement helpers shared by the latency/cost and isolation-under-load
suites.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@contextmanager
def timed():
    """Context manager yielding a single-element list; after the block exits,
    ``holder[0]`` holds the elapsed wall-clock time in milliseconds.

    Usage::

        with timed() as t:
            do_thing()
        elapsed_ms = t[0]
    """
    holder: list[float] = [0.0]
    start = time.perf_counter()
    try:
        yield holder
    finally:
        holder[0] = (time.perf_counter() - start) * 1000.0


@dataclass
class LatencySamples:
    """Accumulates per-call latency samples (milliseconds) and reports percentiles."""

    label: str
    samples_ms: list[float] = field(default_factory=list)

    def record(self, elapsed_ms: float) -> None:
        self.samples_ms.append(elapsed_ms)

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    def percentile(self, pct: float) -> float:
        """Nearest-rank percentile (no interpolation) — simple and dependency-free."""
        if not self.samples_ms:
            return 0.0
        ordered = sorted(self.samples_ms)
        rank = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1)))))
        return ordered[rank]

    def summary(self) -> dict[str, float | int | str]:
        if not self.samples_ms:
            return {"label": self.label, "count": 0}
        return {
            "label": self.label,
            "count": self.count,
            "mean_ms": round(sum(self.samples_ms) / self.count, 3),
            "p50_ms": round(self.percentile(50), 3),
            "p95_ms": round(self.percentile(95), 3),
            "p99_ms": round(self.percentile(99), 3),
            "max_ms": round(max(self.samples_ms), 3),
        }

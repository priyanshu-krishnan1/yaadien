"""
benchmarks/common/resource_sampler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
RSS / CPU instrumentation for the benchmark suite (EPIC-13, BM-6).

Provides a context-manager-based ``ResourceSampler`` backed by ``psutil``
that samples the process's resident-set size (RSS) and CPU percentage in a
background daemon thread.  Works from both pytest benchmark bodies and Locust
``User`` classes (see EPIC-15, BM-14), so the same instrumentation answers:

* "Is ``export_scope()`` really streaming?" — peak RSS stays flat across
  any corpus size when the generator is consumed lazily.
* "Does a 60-minute Locust soak leak?" — per-VU RSS growth is the leak
  signal when attached to ``on_start`` / ``on_stop``.

Usage (pytest benchmark body)
------------------------------
::

    from benchmarks.common.resource_sampler import ResourceSampler

    def test_export_streams(benchmark, benchmark_scope, memory_store):
        def _run():
            with ResourceSampler(interval_s=0.05) as s:
                count = sum(1 for _ in memory_store.export_scope(benchmark_scope))
            return count, s.snapshot()

        count, snap = benchmark(_run)
        assert snap.peak_rss_bytes < 200 * 1024 * 1024  # 200 MB

Usage (Locust User class)
--------------------------
::

    from benchmarks.common.resource_sampler import ResourceSampler

    class ExportUser(User):
        def on_start(self):
            self._sampler = ResourceSampler(interval_s=0.1).__enter__()

        @task
        def export(self):
            for _ in store.export_scope(scope):
                pass

        def on_stop(self):
            snap = self._sampler.snapshot()
            self._sampler.__exit__(None, None, None)
            # report peak_rss_bytes via Locust's request event as a custom metric

Overhead
--------
At ``interval_s=0.05`` (20 samples/second) the sampler adds < 2 % wall-clock
overhead for a benchmark body that runs ≥ 2.5 s.  A time-gate in the loop
drops samples if the previous one took longer than ``interval_s * 2``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# SamplerSnapshot — immutable result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SamplerSnapshot:
    """Point-in-time summary produced by a ``ResourceSampler`` after it stops."""

    peak_rss_bytes: int
    """Peak resident-set size observed during the sampling window, in bytes."""

    mean_cpu_pct: float
    """Mean CPU percentage observed (0.0–100.0 per logical core)."""

    duration_s: float
    """Wall-clock duration of the sampling window, in seconds."""

    sample_count: int
    """Number of samples taken (0 if psutil is unavailable)."""

    psutil_available: bool
    """False when psutil is not installed — all measurements are zero."""


# ---------------------------------------------------------------------------
# ResourceSampler
# ---------------------------------------------------------------------------


class ResourceSampler:
    """Context manager that samples RSS and CPU in a background daemon thread.

    Args:
        interval_s: Seconds between samples.  Default 0.05 (20 Hz).
    """

    def __init__(self, interval_s: float = 0.05) -> None:
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_ts: float = 0.0
        self._stop_ts: float = 0.0

        # Accumulated measurements
        self._peak_rss: int = 0
        self._cpu_samples: list[float] = []

    # ------------------------------------------------------------------
    # Context manager interface
    # ------------------------------------------------------------------

    def __enter__(self) -> ResourceSampler:
        self._stop.clear()
        self._peak_rss = 0
        self._cpu_samples = []
        self._start_ts = time.perf_counter()

        self._thread = threading.Thread(
            target=self._sample_loop,
            name="resource-sampler",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 10)
        self._stop_ts = time.perf_counter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> SamplerSnapshot:
        """Return a ``SamplerSnapshot`` for the current (or completed) window.

        Safe to call while the sampler is still running (reads are atomic
        for ``int`` and ``list`` on CPython's GIL).
        """
        cpu_samples = list(self._cpu_samples)  # copy for thread safety
        return SamplerSnapshot(
            peak_rss_bytes=self._peak_rss,
            mean_cpu_pct=(sum(cpu_samples) / len(cpu_samples)) if cpu_samples else 0.0,
            duration_s=(self._stop_ts or time.perf_counter()) - self._start_ts,
            sample_count=len(cpu_samples),
            psutil_available=_PSUTIL_AVAILABLE,
        )

    # ------------------------------------------------------------------
    # Background sampling loop
    # ------------------------------------------------------------------

    def _sample_loop(self) -> None:
        if not _PSUTIL_AVAILABLE:
            return

        proc = _psutil.Process()
        deadline = time.perf_counter() + self._interval

        while not self._stop.is_set():
            now = time.perf_counter()
            if now < deadline:
                # Wait for the next sample window, checking stop event frequently.
                self._stop.wait(timeout=max(0.0, deadline - now))
                continue

            # Time-gate: drop this sample if we're already behind by more than
            # one full interval (keeps overhead bounded under load).
            if now > deadline + self._interval * 2:
                deadline = now + self._interval
                continue

            try:
                mem = proc.memory_info()
                cpu = proc.cpu_percent(interval=None)
                rss = mem.rss
                if rss > self._peak_rss:
                    self._peak_rss = rss
                self._cpu_samples.append(cpu)
            except _psutil.NoSuchProcess:
                break

            deadline += self._interval


# ---------------------------------------------------------------------------
# Convenience decorator
# ---------------------------------------------------------------------------


def sample_resources(interval_s: float = 0.05):  # type: ignore[return]
    """Decorator that wraps a function with a ``ResourceSampler``.

    After the call, ``func.last_snapshot`` holds the ``SamplerSnapshot``
    from the most recent invocation.

    Example::

        @sample_resources(interval_s=0.05)
        def run_export(store, scope):
            return list(store.export_scope(scope))

        result = run_export(store, scope)
        print(run_export.last_snapshot.peak_rss_bytes)
    """
    import functools

    def decorator(func):  # type: ignore[no-untyped-def]
        @functools.wraps(func)
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            with ResourceSampler(interval_s=interval_s) as s:
                result = func(*args, **kwargs)
            wrapper.last_snapshot = s.snapshot()
            return result

        wrapper.last_snapshot = None
        return wrapper

    return decorator

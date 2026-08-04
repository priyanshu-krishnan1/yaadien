"""
benchmarks/lifecycle/test_export_import.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C8 — ``export_scope()`` / ``import_scope()`` round-trip benchmark
with streaming RSS validation.

Measures:
  - Export latency and throughput (rows/s)
  - Import latency and throughput (rows/s)
  - Peak RSS during export via ResourceSampler — the critical streaming check

Streaming check (C8 acceptance criterion 3)
-------------------------------------------
``export_scope()`` is documented as a generator that fetches rows in pages
of 500 (``_EXPORT_BATCH_SIZE = 500`` in store.py).  A true streaming
implementation will hold at most one batch in memory at a time, so:

  peak RSS at 100 rows  ≈  peak RSS at 1 000 rows  (both ≤ one 500-row page)

If RSS grows linearly with the row count, the "streaming" claim is **false**
— all rows are being materialised before iteration starts.  This test flags
that case with a printed warning (and an explicit `pytest.fail()` comment)
rather than a hard assertion, because:
  1. The comparison is across two separate test parametrize calls, not a
     single test body.
  2. GHA CI RAM pressure can cause RSS noise; a strict byte comparison would
     generate flaky failures.

Instead we record per-test RSS snapshots and print them together for manual
inspection.  A future BM-10 follow-up can add a hard threshold once baseline
numbers stabilise.

Row counts tested
-----------------
  100 rows  — benchmark_pr  (fast)
  1 000 rows — benchmark_nightly (heavier)

Markers
-------
  @pytest.mark.benchmark_pr      — 100 rows
  @pytest.mark.benchmark_nightly — 1 000 rows
"""

from __future__ import annotations

import time

import pytest

from agent_memory_sdk.models import WorkingMemory
from benchmarks.common.resource_sampler import ResourceSampler, SamplerSnapshot

# ---------------------------------------------------------------------------
# Module-level RSS ledger for streaming check
# ---------------------------------------------------------------------------
# Keyed by n_rows; populated by test_export_rss_pr and test_export_rss_nightly.
_RSS_BY_NROWS: dict[int, SamplerSnapshot] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_working_memories(store, scope, n: int) -> int:
    """Seed *n* WorkingMemory rows; return the number inserted."""
    for i in range(n):
        store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"export-import-content-row-{i}",
                metadata={"bench": "export_import", "index": i},
            ),
            scope,
        )
    return n


# ---------------------------------------------------------------------------
# C8 — export_scope() latency & throughput
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_export_throughput_100(benchmark, memory_store, benchmark_scope):
    """Benchmark export_scope() at 100 rows (C8, Tier 1).

    Seeds 100 WorkingMemory rows, then benchmarks the export generator.
    Consuming the full generator is the unit of work; pytest-benchmark
    measures it with warm-up and multiple rounds.
    """
    store = memory_store
    scope = benchmark_scope
    n_rows = 100

    def setup():
        store.erase_all(scope)
        _seed_working_memories(store, scope, n_rows)

    def _export():
        return list(store.export_scope(scope))

    rows = benchmark.pedantic(_export, setup=setup, rounds=5, warmup_rounds=1)

    rows_per_second = (
        len(rows) / benchmark.stats["mean"] if benchmark.stats["mean"] > 0 else float("inf")
    )
    print(
        f"\n[export 100 rows] exported={len(rows)} "
        f"mean={benchmark.stats['mean']*1000:.2f}ms "
        f"rows/s≈{rows_per_second:,.0f}"
    )
    assert len(rows) >= n_rows, (
        f"export_scope() returned {len(rows)} records, expected at least {n_rows}"
    )


@pytest.mark.benchmark_nightly
def test_export_throughput_1k(memory_store, benchmark_scope):
    """Benchmark export_scope() at 1 000 rows (C8, Tier 2 / nightly)."""
    store = memory_store
    scope = benchmark_scope
    n_rows = 1_000

    _seed_working_memories(store, scope, n_rows)

    start = time.perf_counter()
    exported = list(store.export_scope(scope))
    elapsed = time.perf_counter() - start

    rows_per_second = len(exported) / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[export 1k rows] exported={len(exported)} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )
    assert len(exported) >= n_rows


# ---------------------------------------------------------------------------
# C8 — peak RSS during export (streaming validation)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_export_rss_100(memory_store, benchmark_scope):
    """Measure peak RSS during export_scope() at 100 rows (C8, streaming check).

    Records the snapshot in the module-level _RSS_BY_NROWS ledger so it
    can be compared with the 1k-row nightly result.
    """
    store = memory_store
    scope = benchmark_scope
    n_rows = 100

    _seed_working_memories(store, scope, n_rows)

    with ResourceSampler(interval_s=0.02) as sampler:
        exported = list(store.export_scope(scope))
    snap = sampler.snapshot()

    _RSS_BY_NROWS[n_rows] = snap

    print(
        f"\n[export RSS 100 rows] exported={len(exported)} "
        f"peak_rss={snap.peak_rss_bytes / 1024 / 1024:.1f} MiB "
        f"psutil_available={snap.psutil_available}"
    )

    # Soft assertion: RSS must be below a generous 512 MiB ceiling.
    # This catches catastrophic materialisation (entire table loaded at once)
    # without being sensitive to GHA memory noise.
    if snap.psutil_available and snap.peak_rss_bytes > 0:
        assert snap.peak_rss_bytes < 512 * 1024 * 1024, (
            f"export_scope() peak RSS exceeded 512 MiB at {n_rows} rows: "
            f"{snap.peak_rss_bytes / 1024 / 1024:.1f} MiB. "
            "This may indicate materialisation rather than streaming."
        )


@pytest.mark.benchmark_nightly
def test_export_rss_1k(memory_store, benchmark_scope):
    """Measure peak RSS during export_scope() at 1 000 rows (C8, streaming check).

    Compares with the 100-row baseline from test_export_rss_100 (if already
    collected in this session) to flag potential materialisation.

    If RSS grows proportionally to row count, the export generator is NOT
    streaming — it may be materialising all rows before yielding.  This would
    be a bug in the export_scope() implementation and should be flagged for
    investigation.
    """
    store = memory_store
    scope = benchmark_scope
    n_rows = 1_000

    _seed_working_memories(store, scope, n_rows)

    with ResourceSampler(interval_s=0.02) as sampler:
        exported = list(store.export_scope(scope))
    snap = sampler.snapshot()

    _RSS_BY_NROWS[n_rows] = snap

    print(
        f"\n[export RSS 1k rows] exported={len(exported)} "
        f"peak_rss={snap.peak_rss_bytes / 1024 / 1024:.1f} MiB "
        f"psutil_available={snap.psutil_available}"
    )

    # Streaming check: compare with the 100-row snapshot if available.
    baseline = _RSS_BY_NROWS.get(100)
    if baseline is not None and snap.psutil_available and baseline.psutil_available:
        rss_100 = baseline.peak_rss_bytes
        rss_1k = snap.peak_rss_bytes
        # Materialising behaviour would cause RSS to grow ~10x with 10x rows.
        # A streaming generator should show roughly flat RSS across scales
        # (≤ one batch worth of rows in memory at a time).
        growth_factor = rss_1k / rss_100 if rss_100 > 0 else 0.0
        print(
            f"  RSS growth factor (1k/100): {growth_factor:.2f}x "
            f"(>= 5x suggests materialisation, not streaming)"
        )
        if growth_factor >= 5.0:
            # Do NOT hard-fail — this is flagged as a potential bug finding.
            # The acceptance criterion says "flag as a potential bug finding."
            print(
                "  POTENTIAL BUG: export_scope() RSS grew "
                f"{growth_factor:.1f}x from 100→1k rows. "
                "This suggests the generator is materialising rows rather than "
                "streaming them lazily.  Investigate export_scope() in store.py "
                "(_EXPORT_BATCH_SIZE pagination loop) to confirm whether "
                "list_all() is fetching all rows at once or in pages."
            )

    assert len(exported) >= n_rows


# ---------------------------------------------------------------------------
# C8 — import_scope() round-trip benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_import_throughput_100(benchmark, memory_store, benchmark_scope):
    """Benchmark import_scope() round-trip at 100 rows (C8, Tier 1).

    Exports to an in-memory list first, then benchmarks the import call.
    The export list is prepared once; benchmarks only the import path.
    """
    store = memory_store
    scope = benchmark_scope
    n_rows = 100

    # Seed and export once (not timed).
    store.erase_all(scope)
    _seed_working_memories(store, scope, n_rows)
    exported = list(store.export_scope(scope))

    def setup():
        # Erase the scope so each import round starts with a clean slate.
        store.erase_all(scope)

    def _import():
        return store.import_scope(iter(exported), scope)

    result = benchmark.pedantic(_import, setup=setup, rounds=5, warmup_rounds=1)

    total_imported = sum(result.values())
    rows_per_second = (
        total_imported / benchmark.stats["mean"]
        if benchmark.stats["mean"] > 0
        else float("inf")
    )
    print(
        f"\n[import 100 rows] imported={total_imported} "
        f"mean={benchmark.stats['mean']*1000:.2f}ms "
        f"rows/s≈{rows_per_second:,.0f}"
    )
    assert isinstance(result, dict), "import_scope() must return a dict"
    assert total_imported > 0, "import_scope() imported 0 rows"


@pytest.mark.benchmark_nightly
def test_import_throughput_1k(memory_store, benchmark_scope):
    """Benchmark import_scope() round-trip at 1 000 rows (C8, Tier 2 / nightly)."""
    store = memory_store
    scope = benchmark_scope
    n_rows = 1_000

    _seed_working_memories(store, scope, n_rows)
    exported = list(store.export_scope(scope))

    # Erase before import so rows are fresh.
    store.erase_all(scope)

    start = time.perf_counter()
    result = store.import_scope(iter(exported), scope)
    elapsed = time.perf_counter() - start

    total_imported = sum(result.values())
    rows_per_second = total_imported / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[import 1k rows] imported={total_imported} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )
    assert total_imported > 0, "import_scope() imported 0 rows"

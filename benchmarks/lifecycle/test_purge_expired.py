"""
benchmarks/lifecycle/test_purge_expired.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C2 — ``purge_expired()`` hard-delete benchmark.

Measures throughput (rows/s) for ``MemoryStore.purge_expired()`` over:
  - 10 rows   (benchmark_pr)
  - 100 rows  (benchmark_pr)
  - 1 000 rows (benchmark_nightly)

``purge_expired()`` only removes rows that are **both**:
  1. expired  (``expires_at`` is set and is in the past), AND
  2. tombstoned (``deleted_at IS NOT NULL``).

So the seeding flow is:
  1. ``remember()``  — insert with expires_at in the past.
  2. ``forget()``    — tombstone each row (sets deleted_at).
  3. ``purge_expired()`` — hard-delete the tombstoned+expired rows.

Markers
-------
  @pytest.mark.benchmark_pr      — 10 and 100 rows (fast, every PR)
  @pytest.mark.benchmark_nightly — 1 000 rows (heavier, nightly only)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from agent_memory_sdk.models import WorkingMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_expired_tombstoned(store, scope, n: int) -> int:
    """Seed *n* WorkingMemory rows that are expired AND tombstoned.

    Returns the number of rows seeded.
    """
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ids: list[str] = []

    for i in range(n):
        record = store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"purge-bench-content-{i}",
                metadata={"bench": "purge_expired"},
                expires_at=past,
            ),
            scope,
        )
        ids.append(record.id)

    # Tombstone each row so purge_expired() picks it up.
    for record_id in ids:
        store.forget(record_id, "working", scope)

    return len(ids)


# ---------------------------------------------------------------------------
# Parametrized throughput tests
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("n_rows", [10, 100], ids=["10rows", "100rows"])
def test_purge_expired_throughput_pr(benchmark, memory_store, benchmark_scope, n_rows):
    """Benchmark purge_expired() throughput at small scale (C2, Tier 1).

    Seeds *n_rows* expired+tombstoned WorkingMemory rows, then benchmarks
    a single purge_expired() call.  Reports rows/s in the benchmark output.
    """
    store = memory_store
    scope = benchmark_scope

    # ------------------------------------------------------------------
    # pytest-benchmark setup: seed once, then measure the purge repeatedly.
    # We re-seed in setup() so each round has fresh rows to delete.
    # ------------------------------------------------------------------
    seeded_holder: list[int] = []

    def setup():
        n = _seed_expired_tombstoned(store, scope, n_rows)
        seeded_holder.clear()
        seeded_holder.append(n)

    def _purge():
        return store.purge_expired(scope)

    result = benchmark.pedantic(_purge, setup=setup, rounds=5, warmup_rounds=1)

    # result is a dict of {table_name: rows_deleted}.
    total_deleted = sum(result.values())
    rows_per_second = (
        total_deleted / benchmark.stats["mean"] if benchmark.stats["mean"] > 0 else float("inf")
    )
    print(
        f"\n[purge_expired n={n_rows}] deleted={total_deleted} "
        f"mean={benchmark.stats['mean']*1000:.2f}ms "
        f"rows/s≈{rows_per_second:,.0f}"
    )
    # We seeded n_rows per round; at minimum we expect some rows were deleted.
    assert isinstance(result, dict), "purge_expired() must return a dict"


@pytest.mark.benchmark_nightly
def test_purge_expired_throughput_1k(memory_store, benchmark_scope):
    """Benchmark purge_expired() throughput at 1 000 rows (C2, Tier 2).

    This heavier test is gated to nightly runs.  Rows/s is reported and
    can be compared across nightly runs to detect regressions.
    """
    store = memory_store
    scope = benchmark_scope

    n_rows = 1_000
    _seed_expired_tombstoned(store, scope, n_rows)

    start = time.perf_counter()
    result = store.purge_expired(scope)
    elapsed = time.perf_counter() - start

    total_deleted = sum(result.values())
    rows_per_second = total_deleted / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[purge_expired 1k] deleted={total_deleted} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )

    assert isinstance(result, dict), "purge_expired() must return a dict"
    # At 1k scale we expect rows to actually be deleted (the seeding worked).
    assert total_deleted > 0, (
        "purge_expired() deleted 0 rows — check that seeding set expires_at in "
        "the past AND called forget() to tombstone rows before purging."
    )

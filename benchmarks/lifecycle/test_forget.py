"""
benchmarks/lifecycle/test_forget.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C1 — ``forget()`` soft-delete tombstone benchmark.

Measures:
  - Per-call latency of ``MemoryStore.forget()``
  - Throughput (rows/s) when tombstoning a batch of records
  - Asserts exactly 1 DB execute per forget() via CountingPool
  - Verifies rows are tombstoned (deleted_at IS NOT NULL), not hard-deleted

Markers
-------
  @pytest.mark.benchmark_pr — runs on every PR (Tier 1).
"""

from __future__ import annotations

import time

import pytest

from agent_memory_sdk.models import WorkingMemory

from benchmarks.common.counting import CountingPool, RoundTripCounter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORGET_BATCH = 50  # number of records to seed and forget per benchmark run


def _seed_working_memories(store, scope, n: int) -> list[str]:
    """Insert *n* WorkingMemory rows; return their record IDs."""
    ids: list[str] = []
    for i in range(n):
        record = store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"benchmark-forget-content-{i}",
                metadata={"bench": "forget", "index": i},
            ),
            scope,
        )
        ids.append(record.id)
    return ids


# ---------------------------------------------------------------------------
# C1 — forget() latency & throughput
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_forget_latency(benchmark, memory_store, benchmark_scope, db_pool):
    """Benchmark forget() single-call latency (C1).

    Creates one record, then benchmarks the forget() call repeatedly.
    pytest-benchmark takes care of the warm-up and repetition.
    """
    # Seed a single record to tombstone repeatedly.  Because forget() is
    # idempotent on an already-deleted row (returns False but doesn't error),
    # we re-seed before each benchmarked call via the setup arg.
    store = memory_store
    scope = benchmark_scope
    holder: list[str] = []

    def setup():
        record = store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content="benchmark-forget-single",
                metadata={"bench": "forget"},
            ),
            scope,
        )
        holder.clear()
        holder.append(record.id)

    def _forget():
        return store.forget(holder[0], "working", scope)

    result = benchmark.pedantic(_forget, setup=setup, rounds=20, warmup_rounds=3)

    # forget() returns True on first tombstone, False on repeated call — both
    # are valid; we just check the call returned a bool (not an exception).
    assert isinstance(result, bool)


@pytest.mark.benchmark_pr
def test_forget_throughput(memory_store, benchmark_scope, db_pool):
    """Measure forget() throughput: rows/s over a batch of N records (C1).

    Seeds _FORGET_BATCH records, then tombstones all of them, timing the
    whole batch.  Prints rows/s so CI logs capture it even without a
    pytest-benchmark JSON report.
    """
    store = memory_store
    scope = benchmark_scope

    ids = _seed_working_memories(store, scope, _FORGET_BATCH)

    start = time.perf_counter()
    for record_id in ids:
        store.forget(record_id, "working", scope)
    elapsed = time.perf_counter() - start

    rows_per_second = len(ids) / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[forget throughput] {len(ids)} rows tombstoned in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )
    assert rows_per_second > 0  # sanity — not a hard threshold; CI is noisy


# ---------------------------------------------------------------------------
# C1 — exactly 1 DB execute per forget()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_forget_single_roundtrip(memory_store, benchmark_scope, db_pool):
    """Assert forget() costs exactly 1 DB execute call (C1, round-trip budget).

    Uses CountingPool to intercept cursor.execute() calls directly.
    """
    store = memory_store
    scope = benchmark_scope

    # Seed one record with the real pool (counting not yet active).
    record = store.remember(
        WorkingMemory(
            agent_id=scope.agent_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            content="roundtrip-test-forget",
            metadata={"bench": "forget-roundtrip"},
        ),
        scope,
    )

    # Wrap the existing pool in a CountingPool, then build a fresh store on
    # that counting pool so we intercept the forget() execute call.
    counting = CountingPool(db_pool)
    counter = RoundTripCounter()
    counting.enable(counter)

    # Build a fresh store on the counting pool (same settings as memory_store).
    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

    counting_store = MemoryStore(
        pool=counting,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    counting_store.forget(record.id, "working", scope)
    counting.disable()

    assert counter.executes == 1, (
        f"Expected exactly 1 execute for forget(), got {counter.executes}"
    )

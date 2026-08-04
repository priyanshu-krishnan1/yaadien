"""
benchmarks/write/test_update.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A4 & A6 — Benchmark ``update()`` with and without chunk rewrite.

Variants
--------
Short update (A4, no chunk rewrite):
  Content ≤ CHUNK_THRESHOLD (2000 chars).  ``update()`` issues a single
  UPDATE statement — 1 execute.  No chunk rows are written or deleted.

Long update (A6, with chunk rewrite):
  Content > CHUNK_THRESHOLD.  ``update()`` issues:
    1. UPDATE on the parent row (1 execute).
    2. delete_by_source on memory_chunks for stale chunks (1 execute).
    3. N INSERT statements — one per new chunk (N executes).
  Total: 1 + 1 + N executes (N ≥ 1).

Contention case (A6 variant):
  A StaleWriteError is raised when the version field is out of date.  This
  test verifies the error is raised correctly and measures the cost of the
  failed UPDATE attempt (1 execute).

All variants use CountingPool + round_trips to record and assert execute
counts. Latency benchmarks record P50/P95/P99 for each variant.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, WorkingMemory
from agent_memory_sdk.repositories.base import CHUNK_THRESHOLD
from agent_memory_sdk.store import MemoryStore, StaleWriteError
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

from benchmarks.common.counting import CountingPool, RoundTripsFixture
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

pytestmark = pytest.mark.benchmark_pr

_BENCH_TENANT = "bm8-a4a6"

# Short content: below CHUNK_THRESHOLD → no chunk rewrite.
_SHORT_CONTENT = "Short update content — well below the chunk threshold."

# Long content: above CHUNK_THRESHOLD (2000 chars) → chunk rewrite path.
_LONG_CONTENT = (
    "Long update content — this exceeds the chunk threshold and triggers "
    "the ORC-2 chunk rewrite path.  "
) * 80  # ~6600 characters → exceeds CHUNK_THRESHOLD


def _make_scope(variant: str, run_id: str) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"bm8-update-{variant}-{run_id}",
    )


def _make_store_no_chunking(pool: Any) -> MemoryStore:
    """Store with chunking disabled (short-update variant)."""
    return MemoryStore(
        pool=pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )


def _make_store_with_chunking(pool: Any) -> MemoryStore:
    """Store with chunking enabled (long-update variant)."""
    return MemoryStore(
        pool=pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=HashingEmbeddingProvider(),
        enable_chunking=True,
        chunk_threshold=CHUNK_THRESHOLD,
        chunk_size=800,
        chunk_overlap=200,
    )


# ---------------------------------------------------------------------------
# Round-trip count assertions
# ---------------------------------------------------------------------------


def test_short_update_costs_one_round_trip(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Short content update (no chunk rewrite): 1 execute (the UPDATE statement)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("short-rt", run_id)
    store = _make_store_no_chunking(counting_pool)

    try:
        record = WorkingMemory(agent_id=scope.agent_id, content=_SHORT_CONTENT)
        created = store.remember(record, scope)

        # Fresh unique content for the update (also short).
        created.content = f"Updated short content {run_id}"
        round_trips.reset()
        store.working.update(created, scope)
        round_trips.assert_round_trips(1)  # single UPDATE execute
    finally:
        store.erase_all(scope)


def test_long_update_costs_more_round_trips_than_short(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Long content update (with chunk rewrite): >1 executes (UPDATE + delete_by_source + INSERTs)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("long-rt", run_id)
    store = _make_store_with_chunking(counting_pool)

    try:
        # Create a parent row with short initial content (avoids chunking on create).
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content=f"Initial short content {run_id}",
        )
        created = store.remember(record, scope)

        # Now update with long content — triggers chunk rewrite.
        created.content = _LONG_CONTENT
        created.embedding = []  # let the provider recompute

        round_trips.reset()
        store.working.update(created, scope)
        actual = round_trips.counter.executes

        # Must be strictly more than the short-update baseline (1).
        # Minimum: 1 (UPDATE) + 1 (delete_by_source) + 1 (at least 1 chunk INSERT).
        assert actual >= 3, (
            f"Long update expected ≥ 3 executes (UPDATE + delete_by_source + chunk INSERTs), "
            f"got {actual}."
        )
    finally:
        store.erase_all(scope)


def test_stale_write_raises_and_costs_one_round_trip(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Contention case: stale version → StaleWriteError, 1 execute (failed UPDATE)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("contention", run_id)
    store = _make_store_no_chunking(counting_pool)

    try:
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content=f"Contention test content {run_id}",
        )
        created = store.remember(record, scope)

        # Simulate a concurrent writer by forcing a stale version number.
        stale = created.model_copy()
        stale.content = "Stale update — should raise StaleWriteError"
        stale.version = created.version - 1  # type: ignore[operator]

        round_trips.reset()
        with pytest.raises(StaleWriteError):
            store.working.update(stale, scope)
        round_trips.assert_round_trips(1)  # 1 execute (the failed UPDATE)
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Latency benchmarks
# ---------------------------------------------------------------------------


def test_benchmark_short_update(benchmark: Any, db_pool: Any) -> None:
    """Benchmark update() with short content (no chunk rewrite)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-short", run_id)
    store = _make_store_no_chunking(db_pool)

    record = WorkingMemory(
        agent_id=scope.agent_id,
        content=f"Initial short content {run_id}",
    )

    try:
        created = store.remember(record, scope)
        iter_counter = [0]

        def _update() -> None:
            created.content = f"Updated short {run_id} iter={iter_counter[0]}"
            iter_counter[0] += 1
            # Refetch to get the current version after each update.
            current = store.working.get_by_id(created.id, scope)
            if current is None:
                return
            current.content = created.content
            store.working.update(current, scope)

        benchmark(_update)
    finally:
        store.erase_all(scope)


def test_benchmark_long_update(benchmark: Any, db_pool: Any) -> None:
    """Benchmark update() with long content (with chunk rewrite path)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-long", run_id)
    store = _make_store_with_chunking(db_pool)

    record = WorkingMemory(
        agent_id=scope.agent_id,
        content=f"Initial short content {run_id}",
    )

    try:
        created = store.remember(record, scope)
        iter_counter = [0]

        def _update() -> None:
            iter_counter[0] += 1
            current = store.working.get_by_id(created.id, scope)
            if current is None:
                return
            current.content = _LONG_CONTENT + f" iter={iter_counter[0]}"
            current.embedding = []
            store.working.update(current, scope)

        benchmark(_update)
    finally:
        store.erase_all(scope)

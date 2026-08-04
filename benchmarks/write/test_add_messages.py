"""
benchmarks/write/test_add_messages.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A10 — Benchmark ``add_messages()`` batch-size sweep.

Parametrized over batch sizes: 1, 10, 100, 1000.

``add_messages()`` iterates over the list and calls ``remember()`` per
message — it is a serial loop, not a bulk INSERT — so round-trip count
scales linearly with batch size.

For WorkingMemory (dedup OFF), each message = 1 INSERT execute, so:
  batch_size=1   → 1  execute
  batch_size=10  → 10 executes
  batch_size=100 → 100 executes
  batch_size=1000 → 1000 executes

This test captures both the round-trip counts (correctness) and the
end-to-end wall-clock time (latency) for each batch size, letting the
caller compute effective rows/s from ``benchmark.stats["mean"]``.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

from benchmarks.common.counting import CountingPool, RoundTripsFixture

pytestmark = pytest.mark.benchmark_pr

_BATCH_SIZES = [1, 10, 100, 1000]
_BENCH_TENANT = "bm8-a10"


def _make_scope(batch_size: int, run_id: str) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"bm8-addmsg-{batch_size}-{run_id}",
    )


def _make_messages(batch_size: int, run_id: str) -> list[dict[str, Any]]:
    """Build *batch_size* distinct message dicts with unique content."""
    return [
        {
            "role": "user",
            "content": f"Message {i} of {batch_size} — run {run_id} — {uuid.uuid4().hex}",
        }
        for i in range(batch_size)
    ]


# ---------------------------------------------------------------------------
# Round-trip count assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("batch_size", _BATCH_SIZES, ids=[str(b) for b in _BATCH_SIZES])
def test_add_messages_round_trip_count(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
    batch_size: int,
) -> None:
    """Assert execute count == batch_size (1 INSERT per message, dedup OFF)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope(batch_size, run_id)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    messages = _make_messages(batch_size, run_id)

    try:
        round_trips.reset()
        ids = store.add_messages(messages, scope, extract_memories=False)
        round_trips.assert_round_trips(batch_size)
        assert len(ids) == batch_size, (
            f"Expected {batch_size} returned ids, got {len(ids)}."
        )
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Latency benchmarks — one per batch size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("batch_size", _BATCH_SIZES, ids=[str(b) for b in _BATCH_SIZES])
def test_benchmark_add_messages(benchmark: Any, db_pool: Any, batch_size: int) -> None:
    """Benchmark add_messages() end-to-end for each batch size.

    Because add_messages() is a serial loop over remember(), each iteration
    of the benchmark writes *batch_size* rows and the mean latency divided by
    batch_size gives the effective per-message cost.

    Uses a fresh unique run_id per benchmark call (injected via a closure
    counter) so repeated benchmark iterations don't trigger the dedup path
    even if the benchmark harness reuses the setup.
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope(batch_size, run_id)

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    iter_counter = [0]

    def _add_batch() -> None:
        batch_run = f"{run_id}-{iter_counter[0]}"
        iter_counter[0] += 1
        messages = _make_messages(batch_size, batch_run)
        store.add_messages(messages, scope, extract_memories=False)

    try:
        benchmark(_add_batch)
    finally:
        store.erase_all(scope)

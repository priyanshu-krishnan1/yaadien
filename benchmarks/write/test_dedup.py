"""
benchmarks/write/test_dedup.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A2 & A3 — Benchmark ``create()`` with content-hash dedup on vs. off.

Protocol
--------
Dedup ON  (SemanticFact — ``_DEDUP_ON_WRITE = True``):
  • First write of a content hash:  SELECT dedup check (miss) + INSERT → 2 executes.
  • Second write of the same hash:  SELECT dedup check (hit, returns existing) → 1 execute.

Dedup OFF (WorkingMemory — ``_DEDUP_ON_WRITE = False``):
  • Every write:  INSERT only, no pre-SELECT → 1 execute.
  • Two writes of identical content each cost 1 execute (dedup is never checked).

This test uses ``CountingPool`` + ``round_trips`` to assert these counts
exactly, then benchmarks the full write-twice cycle for each variant so the
overhead delta is visible in the P50/P95/P99 report.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler
from benchmarks.common.counting import CountingPool, RoundTripsFixture

pytestmark = pytest.mark.benchmark_pr

_BENCH_TENANT = "bm8-a2a3"
_BENCH_AGENT_ON = "bm8-dedup-on"
_BENCH_AGENT_OFF = "bm8-dedup-off"

# Fixed content used for both writes so the second write is guaranteed to hit
# the dedup cache (dedup-ON) or produce a duplicate row (dedup-OFF).
_FIXED_CONTENT = "The user prefers Python over Java. This fact should be deduped."


def _make_scope(variant: str, run_id: str) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"bm8-dedup-{variant}-{run_id}",
    )


# ---------------------------------------------------------------------------
# Round-trip count assertions (not benchmarks — pure correctness checks)
# ---------------------------------------------------------------------------


def test_dedup_on_first_write_costs_two_round_trips(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """First write with dedup ON: 1 SELECT (miss) + 1 INSERT = 2 executes."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("on-first", run_id)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    try:
        fact = SemanticFact(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        round_trips.reset()
        store.remember(fact, scope)
        round_trips.assert_round_trips(2)  # SELECT + INSERT
    finally:
        store.erase_all(scope)


def test_dedup_on_second_write_costs_one_round_trip(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Second write of identical content with dedup ON: 1 SELECT (hit) = 1 execute."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("on-second", run_id)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    try:
        fact = SemanticFact(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        # First write (not measured)
        store.remember(fact, scope)
        # Second write with same content — dedup hit
        fact2 = SemanticFact(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        round_trips.reset()
        store.remember(fact2, scope)
        round_trips.assert_round_trips(1)  # SELECT only (dedup hit, no INSERT)
    finally:
        store.erase_all(scope)


def test_dedup_off_write_costs_one_round_trip(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """WorkingMemory (dedup OFF): every write = 1 INSERT, no pre-SELECT."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("off", run_id)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    try:
        msg = WorkingMemory(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        round_trips.reset()
        store.remember(msg, scope)
        round_trips.assert_round_trips(1)  # INSERT only, no dedup SELECT
    finally:
        store.erase_all(scope)


def test_dedup_off_duplicate_content_costs_one_round_trip_each(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """WorkingMemory: two writes of identical content each cost 1 execute (dedup skipped)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("off-dup", run_id)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    try:
        msg = WorkingMemory(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        store.remember(msg, scope)  # first write (not measured)
        msg2 = WorkingMemory(agent_id=scope.agent_id, content=_FIXED_CONTENT)
        round_trips.reset()
        store.remember(msg2, scope)
        round_trips.assert_round_trips(1)  # INSERT only, no SELECT
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Latency benchmarks — write-twice cycle (dedup overhead quantified as delta)
# ---------------------------------------------------------------------------


def test_benchmark_dedup_on(benchmark: Any, db_pool: Any) -> None:
    """Benchmark two writes of identical SemanticFact content (dedup ON).

    Iteration 1: SELECT (miss) + INSERT.  Iteration 2+: SELECT (hit) only.
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-on", run_id)

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    content = f"Dedup-ON benchmark content {run_id}"

    def _write_twice() -> None:
        fact1 = SemanticFact(agent_id=scope.agent_id, content=content)
        store.remember(fact1, scope)
        fact2 = SemanticFact(agent_id=scope.agent_id, content=content)
        store.remember(fact2, scope)

    try:
        benchmark(_write_twice)
    finally:
        store.erase_all(scope)


def test_benchmark_dedup_off(benchmark: Any, db_pool: Any) -> None:
    """Benchmark two writes of identical WorkingMemory content (dedup OFF).

    Both writes are INSERT-only — no SELECT overhead.
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-off", run_id)

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    content = f"Dedup-OFF benchmark content {run_id}"

    def _write_twice() -> None:
        msg1 = WorkingMemory(agent_id=scope.agent_id, content=content)
        store.remember(msg1, scope)
        msg2 = WorkingMemory(agent_id=scope.agent_id, content=content)
        store.remember(msg2, scope)

    try:
        benchmark(_write_twice)
    finally:
        store.erase_all(scope)

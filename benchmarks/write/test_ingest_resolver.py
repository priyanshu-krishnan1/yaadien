"""
benchmarks/write/test_ingest_resolver.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A7 — Benchmark ``remember()`` with ingest resolver on vs. off.

Protocol
--------
Resolver OFF (``NoOpIngestResolver``, the default):
  Fast path — ``remember()`` calls ``repo.create()`` directly, with no
  similarity search.  WorkingMemory dedup-OFF: 1 execute (INSERT only).

Resolver ON (``AlwaysAddIngestResolver``):
  ``_resolve_and_act()`` is invoked.  Before writing, it calls
  ``repo.search(embedding, scope, top_k=5)`` (1 execute), then
  ``repo.create()`` (1 execute for WorkingMemory dedup-OFF INSERT).
  Total: ≥ 2 executes (1 search + 1 insert).

``AlwaysAddIngestResolver`` is a simple deterministic resolver that always
returns ``IngestDecision(action=IngestAction.ADD)`` — it exercises the
full resolver code path (similarity search round-trip) with zero LLM cost.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, WorkingMemory
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import (
    IngestAction,
    IngestDecision,
    NoOpConsolidator,
    NoOpIngestResolver,
    NoOpReconciler,
)
from benchmarks.common.counting import CountingPool, RoundTripsFixture
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

pytestmark = pytest.mark.benchmark_pr

_BENCH_TENANT = "bm8-a7"


# ---------------------------------------------------------------------------
# Deterministic "always ADD" resolver — exercises PIPE-2 without an LLM
# ---------------------------------------------------------------------------


class AlwaysAddIngestResolver:
    """Deterministic resolver that always returns ADD.

    Exercises the full ``_resolve_and_act()`` path in MemoryStore — including
    the similarity search round-trip — without calling any LLM or scoring
    the candidate's similarity to neighbors.  This isolates the overhead
    introduced by PIPE-2 (the extra search round-trip) from any LLM latency.
    """

    def __call__(self, candidate: Any, similar: list[Any]) -> IngestDecision:
        return IngestDecision(action=IngestAction.ADD)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(variant: str, run_id: str) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"bm8-resolver-{variant}-{run_id}",
    )


def _make_store_resolver_off(pool: Any) -> MemoryStore:
    """Resolver OFF — NoOpIngestResolver (default), no embedding needed."""
    return MemoryStore(
        pool=pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
        ingest_resolver=NoOpIngestResolver(),
    )


def _make_store_resolver_on(pool: Any) -> MemoryStore:
    """Resolver ON — AlwaysAddIngestResolver with HashingEmbeddingProvider."""
    return MemoryStore(
        pool=pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=HashingEmbeddingProvider(),
        enable_chunking=False,
        ingest_resolver=AlwaysAddIngestResolver(),
        resolver_k=5,
    )


# ---------------------------------------------------------------------------
# Round-trip count assertions
# ---------------------------------------------------------------------------


def test_resolver_off_costs_one_round_trip(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Resolver OFF: 1 execute (WorkingMemory INSERT, no search, no dedup)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("off", run_id)
    store = _make_store_resolver_off(counting_pool)

    try:
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content=f"Resolver-off test content {run_id}",
        )
        round_trips.reset()
        store.remember(record, scope)
        round_trips.assert_round_trips(1)  # INSERT only
    finally:
        store.erase_all(scope)


def test_resolver_on_costs_more_than_resolver_off(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
) -> None:
    """Resolver ON: search execute + INSERT execute → at least 2 executes.

    The exact count depends on how search() fetches results (1 execute for
    the SELECT, then 1 INSERT).  Regardless, it must be > 1 (the resolver-OFF
    baseline).
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("on", run_id)
    store = _make_store_resolver_on(counting_pool)

    try:
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content=f"Resolver-on test content {run_id}",
        )
        round_trips.reset()
        store.remember(record, scope)
        actual = round_trips.counter.executes
        assert actual >= 2, (
            f"Resolver ON expected ≥ 2 execute calls (search + insert), "
            f"got {actual}. Resolver may not be exercising the search path."
        )
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Latency benchmarks
# ---------------------------------------------------------------------------


def test_benchmark_resolver_off(benchmark: Any, db_pool: Any) -> None:
    """Benchmark remember() with resolver OFF (baseline — INSERT only)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-off", run_id)
    store = _make_store_resolver_off(db_pool)

    iter_counter = [0]

    def _remember() -> None:
        content = f"Resolver-off content {run_id} iter={iter_counter[0]}"
        iter_counter[0] += 1
        record = WorkingMemory(agent_id=scope.agent_id, content=content)
        store.remember(record, scope)

    try:
        benchmark(_remember)
    finally:
        store.erase_all(scope)


def test_benchmark_resolver_on(benchmark: Any, db_pool: Any) -> None:
    """Benchmark remember() with resolver ON (search + INSERT overhead)."""
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope("bench-on", run_id)
    store = _make_store_resolver_on(db_pool)

    iter_counter = [0]

    def _remember() -> None:
        content = f"Resolver-on content {run_id} iter={iter_counter[0]}"
        iter_counter[0] += 1
        record = WorkingMemory(agent_id=scope.agent_id, content=content)
        store.remember(record, scope)

    try:
        benchmark(_remember)
    finally:
        store.erase_all(scope)

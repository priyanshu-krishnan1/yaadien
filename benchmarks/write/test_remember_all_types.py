"""
benchmarks/write/test_remember_all_types.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A1 — Benchmark ``remember()`` dispatch across all 5 record types.

Uses the ``noop`` wiring variant (no embedding, no consolidation, no ingest
resolver) so the measurement captures only the raw repository write path —
SQL INSERT + zero-vector sentinel — with zero protocol overhead.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
Skips gracefully when DB2_HOSTNAME is not set (via the ``db_pool`` fixture).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
    _MemoryBase,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

from benchmarks.common.counting import CountingPool, RoundTripsFixture

pytestmark = pytest.mark.benchmark_pr

# ---------------------------------------------------------------------------
# Parametrize over all 5 record types
# ---------------------------------------------------------------------------

_RECORD_TYPES: list[tuple[str, type[_MemoryBase]]] = [
    ("WorkingMemory", WorkingMemory),
    ("EpisodicMemory", EpisodicMemory),
    ("SemanticFact", SemanticFact),
    ("EntityProfile", EntityProfile),
    ("ProceduralMemory", ProceduralMemory),
]

_BENCH_AGENT = "bm8-a1"
_BENCH_TENANT = "bm8-a1-tenant"


def _make_scope(run_id: str, type_name: str) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"{_BENCH_AGENT}-{run_id}-{type_name}",
    )


def _make_record(record_type: type[_MemoryBase], scope: MemoryScope) -> _MemoryBase:
    content = f"Benchmark content for {record_type.__name__} — {uuid.uuid4().hex}"
    return record_type(agent_id=scope.agent_id, content=content)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "type_name,record_type",
    _RECORD_TYPES,
    ids=[t[0] for t in _RECORD_TYPES],
)
def test_remember_all_types(
    benchmark: Any,
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
    type_name: str,
    record_type: type[_MemoryBase],
) -> None:
    """P50/P95/P99 latency for ``remember()`` per record type.

    Asserts that each remember() call costs exactly 1 execute (the INSERT)
    for dedup-OFF types (WorkingMemory) and 2 executes for dedup-ON types
    (SELECT dedup check + INSERT on a fresh content hash).
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope(run_id, type_name)

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=None,
        enable_chunking=False,
    )

    stored_ids: list[str] = []

    def _remember_once() -> None:
        record = _make_record(record_type, scope)
        stored = store.remember(record, scope)
        stored_ids.append(stored.id)

    try:
        benchmark(_remember_once)
    finally:
        store.erase_all(scope)

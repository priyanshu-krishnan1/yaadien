"""
benchmarks/lifecycle/test_erase_all.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C3 — ``erase_all()`` compliance erasure benchmark.

Asserts completeness across all 6 tables and measures throughput.

What is seeded
--------------
Data is inserted across **all 6 tables** that erase_all() must cover:
  1. working_memory      — WorkingMemory records
  2. episodic_memory     — EpisodicMemory records
  3. semantic_facts      — SemanticFact records
  4. entity_profiles     — EntityProfile records
  5. procedural_memory   — ProceduralMemory records
  6. memory_chunks       — chunk fragments (via a chunking-enabled MemoryStore)

Assertions
----------
  - ErasureReport.rows_deleted contains all 6 expected table keys.
  - ErasureReport.total_deleted >= total rows seeded.
  - Throughput (rows/s) is reported.

Markers
-------
  @pytest.mark.benchmark_pr — runs on every PR (Tier 1).
"""

from __future__ import annotations

import time

import pytest

from agent_memory_sdk.db.connection import ConnectionPool
from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ErasureReport, NoOpConsolidator, NoOpReconciler

from benchmarks.common.embedding_providers import HashingEmbeddingProvider


# ---------------------------------------------------------------------------
# Expected table keys in every ErasureReport
# ---------------------------------------------------------------------------

_EXPECTED_TABLES = {
    "working_memory",
    "episodic_memory",
    "semantic_facts",
    "entity_profiles",
    "procedural_memory",
    "memory_chunks",
}

# Number of rows to seed per memory type.
_ROWS_PER_TYPE = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_all_tables(store: MemoryStore, scope) -> int:
    """Seed one row in each of the 5 memory-type tables; return total seeded.

    memory_chunks rows are written implicitly when enable_chunking=True and
    content exceeds chunk_threshold.  We use a long-content record in the
    working_memory table to guarantee at least one chunk fragment is written.
    """
    total = 0

    # 1. working_memory — long content to trigger chunking
    for i in range(_ROWS_PER_TYPE):
        long_content = " ".join(
            [f"chunk-word-{j}" for j in range(200)]  # ~2 000+ chars
        )
        store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=long_content,
                metadata={"bench": "erase_all", "type": "working", "index": i},
            ),
            scope,
        )
        total += 1

    # 2. episodic_memory
    for i in range(_ROWS_PER_TYPE):
        store.remember(
            EpisodicMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"erase-episodic-{i}",
                metadata={"bench": "erase_all", "type": "episodic"},
            ),
            scope,
        )
        total += 1

    # 3. semantic_facts
    for i in range(_ROWS_PER_TYPE):
        store.remember(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"erase-fact-{i}",
                metadata={"bench": "erase_all", "type": "facts"},
            ),
            scope,
        )
        total += 1

    # 4. entity_profiles
    for i in range(_ROWS_PER_TYPE):
        store.remember(
            EntityProfile(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"erase-profile-{i}",
                metadata={"bench": "erase_all", "type": "profiles"},
            ),
            scope,
        )
        total += 1

    # 5. procedural_memory
    for i in range(_ROWS_PER_TYPE):
        store.remember(
            ProceduralMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                content=f"erase-procedure-{i}",
                metadata={"bench": "erase_all", "type": "procedures"},
            ),
            scope,
        )
        total += 1

    return total


# ---------------------------------------------------------------------------
# C3 — erase_all() completeness and throughput
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_erase_all_completeness(db_pool, benchmark_scope):
    """Assert ErasureReport covers all 6 tables with no zero-counts omitted (C3).

    Seeds data across all 5 memory-type tables using a chunking-enabled
    MemoryStore so memory_chunks rows are also written.  Then calls
    erase_all() and verifies:
      - all 6 table keys are present in rows_deleted
      - total_deleted >= rows seeded
      - the report carries an erased_at timestamp
    """
    # Build a chunking-enabled store so memory_chunks rows are also written.
    embedding_provider = HashingEmbeddingProvider()
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=embedding_provider,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=True,
        chunk_threshold=500,  # lower threshold to ensure chunks are created
        chunk_size=200,
        chunk_overlap=50,
    )

    scope = benchmark_scope

    rows_seeded = _seed_all_tables(store, scope)

    start = time.perf_counter()
    report = store.erase_all(scope)
    elapsed = time.perf_counter() - start

    rows_per_second = report.total_deleted / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[erase_all completeness] seeded={rows_seeded} "
        f"deleted={report.total_deleted} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )
    print(f"  rows_deleted breakdown: {report.rows_deleted}")

    # --- Acceptance criteria (C3) ----------------------------------------
    # 1. ErasureReport must be the right type.
    assert isinstance(report, ErasureReport), (
        f"erase_all() must return an ErasureReport; got {type(report)!r}"
    )

    # 2. All 6 table keys must be present — even if their count is 0.
    missing = _EXPECTED_TABLES - set(report.rows_deleted.keys())
    assert not missing, (
        f"ErasureReport.rows_deleted is missing keys for tables: {sorted(missing)}. "
        f"erase_all() must report all 6 tables, even those with 0 rows deleted."
    )

    # 3. total_deleted must match the sum of per-table counts.
    computed_total = sum(report.rows_deleted.values())
    assert report.total_deleted == computed_total, (
        f"ErasureReport.total_deleted={report.total_deleted} does not match "
        f"sum(rows_deleted)={computed_total}"
    )

    # 4. At least as many rows deleted as we seeded across the 5 main tables.
    assert report.total_deleted >= rows_seeded, (
        f"erase_all() deleted {report.total_deleted} rows but we seeded "
        f"{rows_seeded} — some rows may not have been erased."
    )

    # 5. Erasure timestamp must be set.
    assert report.erased_at is not None, "ErasureReport.erased_at must be set"

    # 6. Throughput sanity — not a hard SLO; records the metric for history.
    assert rows_per_second > 0


@pytest.mark.benchmark_pr
def test_erase_all_throughput(benchmark, db_pool, benchmark_scope):
    """Benchmark erase_all() throughput with pytest-benchmark (C3).

    Seeds a fixed number of rows, then benchmarks a single erase_all() call.
    Re-seeds in setup() so each round has fresh rows.
    """
    embedding_provider = HashingEmbeddingProvider()
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=embedding_provider,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=True,
        chunk_threshold=500,
        chunk_size=200,
        chunk_overlap=50,
    )
    scope = benchmark_scope

    def setup():
        _seed_all_tables(store, scope)

    def _erase():
        return store.erase_all(scope)

    report = benchmark.pedantic(_erase, setup=setup, rounds=5, warmup_rounds=1)

    assert isinstance(report, ErasureReport)
    assert _EXPECTED_TABLES.issubset(set(report.rows_deleted.keys())), (
        "erase_all() must return rows_deleted entries for all 6 tables"
    )

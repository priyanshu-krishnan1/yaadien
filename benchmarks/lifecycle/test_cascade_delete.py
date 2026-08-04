"""
benchmarks/lifecycle/test_cascade_delete.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-10 C4 — ``delete_user()`` / ``delete_agent()`` cascade benchmark.

Measures throughput of the two cascade-delete operations over a
deliberately deep hierarchy:

  - ``delete_user()``  — erases all memory for one user within one agent
  - ``delete_agent()`` — erases all memory for an entire agent (all users,
                          all threads)

Both delegate to ``erase_all()`` when ``cascade=True`` (the default), and
both return an ``ErasureReport``; this test verifies:
  1. The report covers all 6 tables.
  2. At least as many rows are deleted as were seeded.
  3. Throughput (rows/s) is reported.

Markers
-------
  @pytest.mark.benchmark_pr — runs on every PR (Tier 1).
"""

from __future__ import annotations

import time

import pytest

from agent_memory_sdk.models import (
    EpisodicMemory,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ErasureReport, NoOpConsolidator, NoOpReconciler

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


# ---------------------------------------------------------------------------
# Deep-hierarchy seeding helpers
# ---------------------------------------------------------------------------

#: Depth knobs: users × threads × rows-per-thread
_N_USERS = 3
_N_THREADS = 4
_ROWS_PER_THREAD = 5  # rows per memory type per thread


def _seed_hierarchy(store: MemoryStore, agent_id: str, tenant_id: str | None) -> int:
    """Seed a deep hierarchy: multiple users × threads × memory types.

    Returns the total number of rows seeded (memory_chunks not counted as
    they depend on content length and chunking config).
    """
    from agent_memory_sdk.models import EntityProfile, MemoryScope

    total = 0
    for u in range(_N_USERS):
        for t in range(_N_THREADS):
            scope = MemoryScope(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=f"cascade-user-{u}",
                thread_id=f"cascade-thread-{t}",
            )
            for i in range(_ROWS_PER_THREAD):
                store.remember(
                    WorkingMemory(
                        agent_id=scope.agent_id,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                        thread_id=scope.thread_id,
                        content=f"cascade-working-u{u}-t{t}-i{i}",
                    ),
                    scope,
                )
                store.remember(
                    EpisodicMemory(
                        agent_id=scope.agent_id,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                        thread_id=scope.thread_id,
                        content=f"cascade-episodic-u{u}-t{t}-i{i}",
                    ),
                    scope,
                )
                store.remember(
                    SemanticFact(
                        agent_id=scope.agent_id,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                        thread_id=scope.thread_id,
                        content=f"cascade-fact-u{u}-t{t}-i{i}",
                    ),
                    scope,
                )
                total += 3

        # One profile per user (agent-scoped, seeded once outside the
        # thread loop so the row count matches what is actually inserted).
        # EntityProfile uses dedup-on-write, so inserting the same content
        # multiple times within the same agent scope is idempotent — only
        # one row lands per user.  Counting one here keeps total consistent.
        profile_scope = MemoryScope(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=f"cascade-user-{u}",
        )
        store.remember(
            EntityProfile(
                agent_id=agent_id,
                tenant_id=tenant_id,
                user_id=f"cascade-user-{u}",
                content=f"cascade-profile-u{u}",
            ),
            profile_scope,
        )
        total += 1

        # One procedural memory per agent (no user scoping).
        agent_scope = MemoryScope(
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        store.remember(
            ProceduralMemory(
                agent_id=agent_id,
                tenant_id=tenant_id,
                content=f"cascade-procedure-u{u}",
            ),
            agent_scope,
        )
        total += 1

    return total


# ---------------------------------------------------------------------------
# C4 — delete_user() cascade
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_delete_user_cascade(db_pool, benchmark_scope):
    """Benchmark delete_user() cascade erasure over a deep hierarchy (C4).

    Seeds _N_USERS × _N_THREADS × _ROWS_PER_THREAD rows, then calls
    delete_user() for a single user and verifies the ErasureReport.
    """
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    scope = benchmark_scope
    agent_id = scope.agent_id
    tenant_id = scope.tenant_id

    # Pick one user to measure the delete on.
    target_user_id = "cascade-user-0"

    rows_seeded = _seed_hierarchy(store, agent_id, tenant_id)

    start = time.perf_counter()
    report = store.delete_user(
        user_id=target_user_id,
        agent_id=agent_id,
        tenant_id=tenant_id,
        cascade=True,
    )
    elapsed = time.perf_counter() - start

    rows_per_second = report.total_deleted / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[delete_user cascade] seeded_total={rows_seeded} "
        f"deleted={report.total_deleted} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )

    # Assertions
    assert isinstance(report, ErasureReport)
    missing = _EXPECTED_TABLES - set(report.rows_deleted.keys())
    assert not missing, (
        f"delete_user() ErasureReport missing table keys: {sorted(missing)}"
    )
    assert report.total_deleted >= 1, (
        "delete_user() deleted 0 rows — seeding may have failed"
    )
    assert report.erased_at is not None


# ---------------------------------------------------------------------------
# C4 — delete_agent() cascade
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_delete_agent_cascade(db_pool, benchmark_scope):
    """Benchmark delete_agent() cascade erasure over a deep hierarchy (C4).

    Seeds _N_USERS × _N_THREADS × _ROWS_PER_THREAD rows, then calls
    delete_agent() and verifies the ErasureReport covers all 6 tables and
    deleted at least as many rows as were seeded.
    """
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    scope = benchmark_scope
    agent_id = scope.agent_id
    tenant_id = scope.tenant_id

    rows_seeded = _seed_hierarchy(store, agent_id, tenant_id)

    start = time.perf_counter()
    report = store.delete_agent(
        agent_id=agent_id,
        tenant_id=tenant_id,
        cascade=True,
    )
    elapsed = time.perf_counter() - start

    rows_per_second = report.total_deleted / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[delete_agent cascade] seeded_total={rows_seeded} "
        f"deleted={report.total_deleted} in {elapsed:.4f}s "
        f"({rows_per_second:,.0f} rows/s)"
    )

    # Assertions
    assert isinstance(report, ErasureReport)
    missing = _EXPECTED_TABLES - set(report.rows_deleted.keys())
    assert not missing, (
        f"delete_agent() ErasureReport missing table keys: {sorted(missing)}"
    )
    assert report.total_deleted >= rows_seeded, (
        f"delete_agent() deleted {report.total_deleted} rows but {rows_seeded} were seeded"
    )
    assert report.erased_at is not None

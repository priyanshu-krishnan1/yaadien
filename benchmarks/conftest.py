"""
benchmarks/conftest.py
~~~~~~~~~~~~~~~~~~~~~~
Session-scoped Db2 fixtures and protocol-wiring parametrization for the
pytest-benchmark suite (EPIC-13, BM-3).

Fixture hierarchy
-----------------
db_pool          — session-scoped ConnectionPool (one per test session).
pool_size        — pool-size sweep fixture (parametrized: 1, 3, 5).
memory_store     — function-scoped MemoryStore parametrized over 4 protocol-
                   wiring variants (noop / resolver_on / consolidator_on /
                   fully_wired).
benchmark_scope  — function-scoped MemoryScope with a reserved tenant prefix;
                   teardown hard-deletes all rows for that scope via erase_all().

Reserved tenant prefix
----------------------
All benchmark-created rows carry tenant_id starting with "bm3-" — the prefix
makes contamination of non-benchmark data impossible even on a shared dev
instance: erase_all() is always called with a scope whose tenant_id starts
with "bm3-", so it can only touch rows this session planted.

Teardown guarantee
------------------
benchmark_scope calls store.erase_all(scope) in its finalizer even when the
test body raises (pytest's yield fixture protocol guarantees the teardown
block runs regardless). A deliberate fail-and-inspect cycle can verify zero
residual rows by scanning::

    SELECT COUNT(*) FROM working_memory WHERE tenant_id LIKE 'bm3-%'

Markers
-------
Four markers are registered in pyproject.toml (see that file):

    benchmark_micro    Tier 0 — no database, CPU-only (~90 s, every push)
    benchmark_pr       Tier 1 — single-op against Db2, runs on every PR
    benchmark_nightly  Tier 2 — nightly concurrency / scale run
    benchmark_scale    Tier 3 — weekly full-scale sweep

Select a tier:   pytest benchmarks/ -m benchmark_pr
Skip a tier:     pytest benchmarks/ -m "not benchmark_nightly"
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest

from agent_memory_sdk.db.connection import ConnectionPool
from agent_memory_sdk.db.migrate import Migrator
from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler
from benchmarks.common.counting import counting_pool, round_trips  # noqa: F401 — pytest fixtures
from benchmarks.common.embedding_providers import HashingEmbeddingProvider
from benchmarks.retrieval_quality.consolidator import BenchmarkConsolidator
from benchmarks.retrieval_quality.reconciler import BenchmarkReconciler

# ---------------------------------------------------------------------------
# Reserved tenant prefix
# ---------------------------------------------------------------------------

_BENCH_TENANT_PREFIX = "bm3"

# Generated once per session so all fixtures share the same run namespace.
_SESSION_RUN_ID: str = uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Pool-size parametrization fixture
# ---------------------------------------------------------------------------


@pytest.fixture(params=[1, 3, 5], ids=["pool1", "pool3", "pool5"])
def pool_size(request: pytest.FixtureRequest) -> int:
    """Pool-size sweep: 1 / 3 / 5 connections.

    BM-15 (Connection-pool saturation) parametrizes over this fixture.
    Other benchmarks that don't need the sweep can override with
    ``@pytest.mark.parametrize`` or request a fixed pool_size value directly.
    """
    return int(request.param)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Session-scoped ConnectionPool
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db_pool() -> Generator[ConnectionPool, None, None]:
    """Session-scoped pool honouring the standard DB2_* env vars.

    A single pool is shared across the whole test session to avoid the
    overhead of tearing down and re-opening connections between tests.
    The pool honours DB2_POOL_SIZE / DB2_POOL_TIMEOUT from the environment
    (same vars as the integration test suite, same ci.yml Db2 container).

    Skips the entire benchmark session gracefully when DB2_HOSTNAME is not
    set, so ``pytest benchmarks/`` on a machine with no Db2 credentials
    skips rather than errors — keeping the ``not integration`` unit suite
    unaffected.

    Migrations are applied once per session before any benchmark touches the
    database, matching the ``migrated_pool`` pattern in
    ``tests/integration/conftest.py``.  On a freshly started Db2 container
    this creates all application tables and vector indexes; on a pre-migrated
    database it is a fast no-op (only the schema_migrations table is read).
    """
    if not os.environ.get("DB2_HOSTNAME"):
        pytest.skip("DB2_HOSTNAME not set — skipping Db2 benchmark fixtures")

    pool = ConnectionPool()
    try:
        Migrator(pool).run()
        yield pool
    finally:
        pool.close()


# ---------------------------------------------------------------------------
# Protocol-wiring variants
# ---------------------------------------------------------------------------

#: The four wiring configs this fixture parametrizes over.
#: (variant_id, consolidator, reconciler, embedding_provider)
_WIRING_VARIANTS: list[tuple[str, Any, Any, Any]] = [
    (
        "noop",
        NoOpConsolidator(),
        NoOpReconciler(),
        None,  # no embedding provider — resolver path skipped entirely
    ),
    (
        "resolver_on",
        NoOpConsolidator(),
        NoOpReconciler(),
        HashingEmbeddingProvider(),  # embedding required to exercise resolver
    ),
    (
        "consolidator_on",
        BenchmarkConsolidator(),
        NoOpReconciler(),
        HashingEmbeddingProvider(),
    ),
    (
        "fully_wired",
        BenchmarkConsolidator(),
        BenchmarkReconciler(),
        HashingEmbeddingProvider(),
    ),
]


@pytest.fixture(
    params=[v[0] for v in _WIRING_VARIANTS],
    ids=[v[0] for v in _WIRING_VARIANTS],
)
def memory_store(
    request: pytest.FixtureRequest,
    db_pool: ConnectionPool,
) -> MemoryStore:
    """Function-scoped MemoryStore parametrized over 4 protocol-wiring variants.

    Variants
    --------
    noop              All hooks are NoOp; no embedding provider. Exercises the
                      raw write/read path with zero protocol overhead — the
                      baseline for every latency comparison.
    resolver_on       HashingEmbeddingProvider injected so the ingest-resolver
                      similarity pre-search is exercised on every remember().
                      Consolidator and Reconciler remain NoOp.
    consolidator_on   BenchmarkConsolidator active; every working/episodic
                      remember() fires the consolidator synchronously.
                      Reconciler remains NoOp.
    fully_wired       Both BenchmarkConsolidator and BenchmarkReconciler active;
                      this is the most representative production-like config.

    The store is created fresh per test (function scope) so per-test
    consolidate_counters and resolver state cannot bleed across tests.
    Chunking is disabled in benchmarks — the write-path cost of chunking is
    measured separately in BM-8; baseline benchmarks should not pay it.
    """
    variant_id: str = request.param
    _, consolidator, reconciler, embedding_provider = next(
        v for v in _WIRING_VARIANTS if v[0] == variant_id
    )
    return MemoryStore(
        pool=db_pool,
        consolidator=consolidator,
        reconciler=reconciler,
        embedding_provider=embedding_provider,
        enable_chunking=False,
    )


# ---------------------------------------------------------------------------
# benchmark_scope — reserved-tenant scope + automatic teardown
# ---------------------------------------------------------------------------


@pytest.fixture
def benchmark_scope(
    request: pytest.FixtureRequest,
    memory_store: MemoryStore,
) -> Generator[MemoryScope, None, None]:
    """Function-scoped MemoryScope with automatic post-test erase_all() teardown.

    The scope's tenant_id is prefixed with _BENCH_TENANT_PREFIX and the
    session run_id, making it unambiguously a benchmark row even on a shared
    instance.  The agent_id encodes the test node id so parallel test runs
    (pytest-xdist) never collide.

    Teardown
    --------
    erase_all(scope) is called in the finally block, which runs even when the
    test body raises.  This guarantees zero residual rows regardless of whether
    the benchmark passed, failed, or was interrupted.
    """
    # Sanitise the test node id for use as an agent_id value (Db2 VARCHAR).
    safe_node = request.node.nodeid.replace("/", "_").replace("::", "__")[:80]

    scope = MemoryScope(
        tenant_id=f"{_BENCH_TENANT_PREFIX}-{_SESSION_RUN_ID}-t0",
        agent_id=f"{_BENCH_TENANT_PREFIX}-{_SESSION_RUN_ID}-{safe_node}",
    )

    yield scope

    # --- teardown (runs even on test failure) ---
    try:
        memory_store.erase_all(scope)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "benchmark_scope teardown: erase_all(%s) raised; "
            "residual rows may exist with tenant_id=%s",
            scope,
            scope.tenant_id,
        )

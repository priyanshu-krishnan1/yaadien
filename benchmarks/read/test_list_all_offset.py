"""
benchmarks/read/test_list_all_offset.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B7: ``list_all()`` offset sweep: 0, 100, 500, 1 000.

The offset=0 path emits a plain ``FETCH FIRST ? ROWS ONLY`` SQL shape.
The offset>0 path wraps the query in a ROW_NUMBER() subquery to implement
pagination — a fundamentally different SQL shape.  This benchmark makes the
cost difference between the two shapes visible in the charts.

Round-trip counts are asserted to always be exactly 1 execute (regardless of
offset) since both paths issue a single SQL statement.

Acceptance criteria covered
----------------------------
* AC-2 (round-trip count always == 1 asserted)
* AC-3 (embed-vs-DB split — no embed for list_all; embed_ms=0 stored)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import uuid

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.counting import CountingPool, round_trips  # noqa: F401 – fixtures
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_DIM = 1536
# Seed enough rows so the largest offset (1 000) still has rows after it.
_SEED_ROWS = 1_200
_PAGE_SIZE = 50  # rows to fetch per list_all() call

# Offsets to sweep.  The offset=0 and offset>0 SQL shapes are explicitly
# different (FETCH FIRST vs. ROW_NUMBER subquery — see base.py list_all()).
_OFFSETS = [0, 100, 500, 1_000]

_SAMPLE_CONTENT = "Deterministic content for offset-sweep benchmark row-{i}."


# ---------------------------------------------------------------------------
# Module-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def offset_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _SEED_ROWS facts; yield (store, scope)."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-offset-{run_id}",
        agent_id=f"bm9-offset-agent-{run_id}",
    )
    for i in range(_SEED_ROWS):
        content = _SAMPLE_CONTENT.format(i=i)
        store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=content,
                embedding=provider(content),
            ),
            scope,
        )
    yield store, scope
    store.erase_all(scope)


# ---------------------------------------------------------------------------
# B7: parametrized offset sweep
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("offset", _OFFSETS, ids=[f"offset_{o}" for o in _OFFSETS])
def test_list_all_offset(benchmark, offset_store_and_scope, offset):  # type: ignore[no-untyped-def]
    """B7: Benchmark list_all() at each offset value.

    offset=0  → FETCH FIRST SQL shape (no subquery overhead).
    offset>0  → ROW_NUMBER() subquery (more complex SQL plan).

    The per-call result count is stored in extra_info alongside the offset
    so a chart can be annotated with the actual result size.
    """
    store, scope = offset_store_and_scope

    # list_all() has no embed step — record 0 ms for consistency with AC-3.
    benchmark.extra_info["embed_ms"] = 0
    benchmark.extra_info["offset"] = offset
    benchmark.extra_info["page_size"] = _PAGE_SIZE
    benchmark.extra_info["seed_rows"] = _SEED_ROWS
    # Note which SQL shape is exercised (important for reading the chart).
    benchmark.extra_info["sql_shape"] = "fetch_first" if offset == 0 else "row_number_subquery"

    def _list():
        return store.facts.list_all(scope, limit=_PAGE_SIZE, offset=offset)

    results = benchmark(_list)
    assert isinstance(results, list)
    benchmark.extra_info["result_count"] = len(results)


# ---------------------------------------------------------------------------
# B7-rt: round-trip count assertion — always exactly 1 execute per call
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("offset", _OFFSETS, ids=[f"rt_offset_{o}" for o in _OFFSETS])
def test_list_all_offset_round_trips(
    offset_store_and_scope, counting_pool, round_trips, offset  # noqa: F811
):  # type: ignore[no-untyped-def]
    """B7-rt: Assert list_all() always issues exactly 1 execute, regardless of offset.

    Both the FETCH FIRST path (offset=0) and the ROW_NUMBER subquery path
    (offset>0) are single SQL statements — they must never issue more than
    one execute.
    """
    store, scope = offset_store_and_scope

    # Build a counting store pointing at the counting_pool.
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    counting_store = MemoryStore(
        pool=counting_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )

    round_trips.reset()
    counting_store.facts.list_all(scope, limit=_PAGE_SIZE, offset=offset)
    round_trips.assert_round_trips(1)

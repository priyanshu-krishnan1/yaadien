"""
benchmarks/read/test_search_modes.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
B1-B3: ``repo.search()`` swept across the 3 search modes (DEFAULT / EXACT /
APPROX) and 4 distance metrics (COSINE / EUCLIDEAN / DOT / MANHATTAN).

Each test seeds ~100 SemanticFact rows into a benchmark-scoped tenant, then
benchmarks ONLY the DB round-trip (the query-embedding time is measured
separately and stored in ``benchmark.extra_info["embed_ms"]``).

Query-embedding isolation
-------------------------
The embedding call is timed with ``time.perf_counter()`` *outside* the
``benchmark()`` call, so an embedding-provider regression cannot masquerade as
a DB regression in the charts.  The result is stored in
``benchmark.extra_info["embed_ms"]``.

Acceptance criteria covered
----------------------------
* AC-3 (embed-vs-DB split explicit in every result)
* AC-5 (``@pytest.mark.benchmark_pr`` on every test)
* AC-6 (skips gracefully when DB2_HOSTNAME not set — via ``db_pool`` fixture)
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import DistanceMetric, SearchMode
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SEED_ROWS = 100
_TOP_K = 10
_EMBED_DIM = 1536

_SEARCH_MODES = [SearchMode.DEFAULT, SearchMode.EXACT, SearchMode.APPROX]
_DISTANCE_METRICS = [
    DistanceMetric.COSINE,
    DistanceMetric.EUCLIDEAN,
    DistanceMetric.DOT,
    DistanceMetric.MANHATTAN,
]

_SAMPLE_CONTENTS = [
    "The agent stored a semantic fact about user preferences.",
    "Memory consolidation reduces episodic records into facts.",
    "Vector search enables approximate nearest-neighbour lookup.",
    "The reconciler detects contradictory facts in long-term memory.",
    "Working memory holds the active conversation context.",
]


# ---------------------------------------------------------------------------
# Session-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def search_modes_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _SEED_ROWS facts; yield (store, scope); erase on teardown."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-modes-{run_id}",
        agent_id=f"bm9-modes-agent-{run_id}",
    )
    # Seed
    for i in range(_SEED_ROWS):
        content = _SAMPLE_CONTENTS[i % len(_SAMPLE_CONTENTS)] + f" row-{i}"
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
# B1 – search modes
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("mode", _SEARCH_MODES, ids=[m.value for m in _SEARCH_MODES])
def test_search_mode(benchmark, search_modes_store_and_scope, mode):  # type: ignore[no-untyped-def]
    """B1: Latency of facts.search() across DEFAULT / EXACT / APPROX modes.

    Query-embedding time is isolated and stored in benchmark.extra_info so
    an embedding regression is never attributed to DB latency.
    """
    store, scope = search_modes_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "agent memory semantic fact"

    # --- isolate embed time ---
    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0
    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["mode"] = mode.value
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    # --- benchmark DB call only ---
    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=mode,
            search_chunks=False,
        )

    results = benchmark(_db_search)
    # Sanity: results must be a list (may be empty on empty APPROX index)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# B2 – distance metrics
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize(
    "metric", _DISTANCE_METRICS, ids=[m.value for m in _DISTANCE_METRICS]
)
def test_search_metric(benchmark, search_modes_store_and_scope, metric):  # type: ignore[no-untyped-def]
    """B2: Latency of facts.search() across COSINE / EUCLIDEAN / DOT / MANHATTAN.

    Note: APPROX index on all tables is built WITH DISTANCE COSINE; non-COSINE
    metrics fall back to a full scan automatically, so this intentionally
    benchmarks the scan path for non-COSINE metrics.

    Query-embedding time is isolated and stored in benchmark.extra_info.
    """
    store, scope = search_modes_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "vector search retrieval"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0
    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["metric"] = metric.value
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            metric=metric,
            mode=SearchMode.EXACT,
            search_chunks=False,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# B3 – mode × metric cross
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize("mode", _SEARCH_MODES, ids=[m.value for m in _SEARCH_MODES])
@pytest.mark.parametrize(
    "metric", _DISTANCE_METRICS, ids=[m.value for m in _DISTANCE_METRICS]
)
def test_search_mode_metric_cross(benchmark, search_modes_store_and_scope, mode, metric):  # type: ignore[no-untyped-def]
    """B3: Cross-product of mode × metric so the interaction is captured.

    APPROX × non-COSINE falls back to a full scan — that cost is explicitly
    reported as a separate curve point here rather than hidden in averages.
    """
    store, scope = search_modes_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "episodic memory context agent"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0
    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["mode"] = mode.value
    benchmark.extra_info["metric"] = metric.value

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            metric=metric,
            mode=mode,
            search_chunks=False,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)

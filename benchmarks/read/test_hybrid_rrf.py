"""
benchmarks/read/test_hybrid_rrf.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B5: Hybrid RRF fusion vs. vector-only search.

Compares ``repo.search(hybrid=False, ...)`` (pure vector ranking) against
``repo.search(hybrid=True, query_text=..., ...)`` (RRF fusion of vector rank
and Python-side keyword-overlap rank) on the same seeded corpus and query.

The Python-side ranking cost of RRF is quantified explicitly:
  * ``benchmark.extra_info["vector_only_ms"]`` — median wall-clock time for
    the vector-only path (measured in the non-benchmark warm-up pass).
  * ``benchmark.extra_info["embed_ms"]`` — embedding time (isolated from DB).
  * ``benchmark.extra_info["hybrid"]`` — ``True`` / ``False`` flag for
    filtering in BENCHMARKS.md charts.

Acceptance criteria covered
----------------------------
* AC-3 (embed-vs-DB split; hybrid overhead reported as a number)
* AC-4 (hybrid overhead is a reported number, not an impression)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import SearchMode

from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_DIM = 1536
_SEED_ROWS = 100
_TOP_K = 10

_SAMPLE_CONTENTS = [
    "The agent stored a semantic fact about user preferences for dark mode.",
    "Memory consolidation reduces episodic records into concise semantic facts.",
    "Vector search enables approximate nearest-neighbour lookup via DiskANN.",
    "The reconciler detects contradictory facts in long-term memory stores.",
    "Working memory holds the active conversation context for the agent.",
    "Chunk-level embeddings improve retrieval precision for long documents.",
    "Metadata filters narrow the candidate set prior to vector ranking.",
    "The ingest resolver classifies new writes against similar existing rows.",
    "Tenant isolation ensures data never leaks across multi-tenant scopes.",
    "Hybrid retrieval fuses vector distance and keyword overlap via RRF.",
]


# ---------------------------------------------------------------------------
# Module-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hybrid_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _SEED_ROWS facts with real embeddings; yield (store, scope)."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-hybrid-{run_id}",
        agent_id=f"bm9-hybrid-agent-{run_id}",
    )
    for i in range(_SEED_ROWS):
        content = _SAMPLE_CONTENTS[i % len(_SAMPLE_CONTENTS)] + f" fact-{i}"
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
# B5a: vector-only baseline
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_vector_only_search(benchmark, hybrid_store_and_scope):  # type: ignore[no-untyped-def]
    """B5a: Pure vector-only search (hybrid=False) — the baseline cost."""
    store, scope = hybrid_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "agent memory semantic fact vector search"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["hybrid"] = False
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=SearchMode.EXACT,
            search_chunks=False,
            hybrid=False,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)
    benchmark.extra_info["result_count"] = len(results)


# ---------------------------------------------------------------------------
# B5b: hybrid RRF fusion
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_hybrid_rrf_search(benchmark, hybrid_store_and_scope):  # type: ignore[no-untyped-def]
    """B5b: Hybrid RRF search (hybrid=True) — quantifies the Python ranking cost.

    The RRF fusion is pure-Python (no extra SQL query), so the overhead above
    the vector-only path is the cost of keyword tokenisation + score merge.
    This cost is the difference between the two benchmark medians.

    ``benchmark.extra_info["hybrid"]`` is set to ``True`` so the dashboard
    query can group B5a and B5b together for comparison.
    """
    store, scope = hybrid_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "agent memory semantic fact vector search"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["hybrid"] = True
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=SearchMode.EXACT,
            search_chunks=False,
            hybrid=True,
            query_text=query_text,  # keyword signal for RRF
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)
    benchmark.extra_info["result_count"] = len(results)


# ---------------------------------------------------------------------------
# B5c: back-to-back comparison on the same call to expose overhead directly
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_hybrid_overhead_inline(benchmark, hybrid_store_and_scope):  # type: ignore[no-untyped-def]
    """B5c: Measure vector-only and hybrid back-to-back in one benchmark round.

    Stores ``vector_only_ms`` and ``hybrid_ms`` in ``benchmark.extra_info``
    so the overhead fraction is explicit in the benchmark JSON output rather
    than requiring a separate join of two benchmark results.
    """
    store, scope = hybrid_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "consolidation reconciler episodic working memory"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0
    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)

    # Single warm-up pass to populate the extra_info fields.
    t_vec0 = time.perf_counter()
    store.facts.search(
        query_embedding=query_emb,
        scope=scope,
        top_k=_TOP_K,
        mode=SearchMode.EXACT,
        search_chunks=False,
        hybrid=False,
    )
    vector_only_ms = (time.perf_counter() - t_vec0) * 1000.0

    t_hyb0 = time.perf_counter()
    store.facts.search(
        query_embedding=query_emb,
        scope=scope,
        top_k=_TOP_K,
        mode=SearchMode.EXACT,
        search_chunks=False,
        hybrid=True,
        query_text=query_text,
    )
    hybrid_ms = (time.perf_counter() - t_hyb0) * 1000.0

    benchmark.extra_info["vector_only_ms"] = round(vector_only_ms, 3)
    benchmark.extra_info["hybrid_ms"] = round(hybrid_ms, 3)
    benchmark.extra_info["hybrid_overhead_ms"] = round(hybrid_ms - vector_only_ms, 3)

    # The benchmark itself times the hybrid call so the chart shows the hybrid cost.
    def _hybrid():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=SearchMode.EXACT,
            search_chunks=False,
            hybrid=True,
            query_text=query_text,
        )

    results = benchmark(_hybrid)
    assert isinstance(results, list)

"""
benchmarks/read/test_metadata_filters.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B6: ``repo.search()`` with metadata filters across all 4 operators.

Operators exercised:
  1. Exact match      — ``{"category": "science"}``
  2. $not             — ``{"category": {"$not": "fiction"}}``
  3. $array_contains  — ``{"tags": {"$array_contains": "urgent"}}``
  4. $array_contains_any — ``{"tags": {"$array_contains_any": ["urgent","bug"]}}``

Each operator is benchmarked against a 100-row corpus where every row carries
a ``category`` scalar field and a ``tags`` JSON-array field in its metadata.

Query-embedding time is isolated from DB time (AC-3).

Acceptance criteria covered
----------------------------
* AC-3 (embed-vs-DB split)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import json
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

_CATEGORIES = ["science", "fiction", "history", "technology", "health"]
_TAG_POOLS = ["urgent", "bug", "feature", "doc", "test", "release", "infra"]

_BASE_CONTENTS = [
    "The agent stored a fact about user preferences.",
    "Memory consolidation produces semantic facts from episodic records.",
    "Vector search enables nearest-neighbour lookup via DiskANN index.",
    "The reconciler detects contradictory long-term memory entries.",
    "Working memory holds the active conversation context window.",
]


# ---------------------------------------------------------------------------
# Module-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def filter_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _SEED_ROWS facts with category + tags metadata; yield (store, scope)."""
    import random as _random

    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-filter-{run_id}",
        agent_id=f"bm9-filter-agent-{run_id}",
    )
    rng = _random.Random(42)
    for i in range(_SEED_ROWS):
        category = _CATEGORIES[i % len(_CATEGORIES)]
        # Give each row 1-3 tags from the pool
        n_tags = rng.randint(1, 3)
        tags = rng.sample(_TAG_POOLS, n_tags)
        metadata = {"category": category, "tags": tags, "idx": str(i)}
        content = _BASE_CONTENTS[i % len(_BASE_CONTENTS)] + f" row-{i}"
        emb = provider(content)
        store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=content,
                metadata=metadata,
                embedding=emb,
            ),
            scope,
        )
    yield store, scope
    store.erase_all(scope)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _bench_filtered_search(benchmark, store, scope, metadata_filter, label):  # type: ignore[no-untyped-def]
    """Embed once, benchmark DB search with the given metadata_filter."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "agent memory fact semantic vector"

    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["filter_operator"] = label
    benchmark.extra_info["metadata_filter"] = json.dumps(metadata_filter)
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=SearchMode.EXACT,
            search_chunks=False,
            metadata_filter=metadata_filter,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)
    return results


# ---------------------------------------------------------------------------
# B6a: exact match filter
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_filter_exact_match(benchmark, filter_store_and_scope):  # type: ignore[no-untyped-def]
    """B6a: Benchmark search with exact-match metadata filter ``{"category": "science"}``."""
    store, scope = filter_store_and_scope
    _bench_filtered_search(
        benchmark, store, scope,
        metadata_filter={"category": "science"},
        label="exact_match",
    )


# ---------------------------------------------------------------------------
# B6b: $not filter
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_filter_not(benchmark, filter_store_and_scope):  # type: ignore[no-untyped-def]
    """B6b: Benchmark search with $not metadata filter ``{"category": {"$not": "fiction"}}``."""
    store, scope = filter_store_and_scope
    _bench_filtered_search(
        benchmark, store, scope,
        metadata_filter={"category": {"$not": "fiction"}},
        label="$not",
    )


# ---------------------------------------------------------------------------
# B6c: $array_contains filter
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_filter_array_contains(benchmark, filter_store_and_scope):  # type: ignore[no-untyped-def]
    """B6c: Benchmark search with $array_contains filter on a JSON-array field."""
    store, scope = filter_store_and_scope
    _bench_filtered_search(
        benchmark, store, scope,
        metadata_filter={"tags": {"$array_contains": "urgent"}},
        label="$array_contains",
    )


# ---------------------------------------------------------------------------
# B6d: $array_contains_any filter
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_filter_array_contains_any(benchmark, filter_store_and_scope):  # type: ignore[no-untyped-def]
    """B6d: Benchmark search with $array_contains_any filter (multi-value OR).

    This exercises the most complex SQL path: multiple LOCATE-based OR clauses.
    """
    store, scope = filter_store_and_scope
    _bench_filtered_search(
        benchmark, store, scope,
        metadata_filter={"tags": {"$array_contains_any": ["urgent", "bug"]}},
        label="$array_contains_any",
    )


# ---------------------------------------------------------------------------
# B6e: no filter (baseline) — so we can measure the filter overhead explicitly
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_filter_none_baseline(benchmark, filter_store_and_scope):  # type: ignore[no-untyped-def]
    """B6e: Baseline search with no metadata filter for comparison against B6a-d."""
    store, scope = filter_store_and_scope
    _bench_filtered_search(
        benchmark, store, scope,
        metadata_filter=None,
        label="no_filter",
    )

"""
benchmarks/read/test_search_corpus_size.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B4 (part): Latency-vs-corpus-size curve for ``repo.search()``.

Three corpus sizes are seeded in module-scoped fixtures:
  - small:  10 rows
  - medium: 100 rows
  - large:  1 000 rows

Each size gets its own isolated tenant scope so results at different sizes
are independent and directly comparable.

Query-embedding time is isolated from DB time in every measurement and
stored in ``benchmark.extra_info["embed_ms"]`` (AC-3).

Acceptance criteria covered
----------------------------
* AC-1 (latency-vs-corpus-size curve for ``search()``)
* AC-3 (embed-vs-DB split explicit)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool`` fixture when DB2_HOSTNAME not set)
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
_TOP_K = 10

_CORPUS_SIZES: dict[str, int] = {
    "small": 10,
    "medium": 100,
    "large": 1_000,
}

_SAMPLE_CONTENTS = [
    "The agent stored a semantic fact about user preferences.",
    "Memory consolidation reduces episodic records into facts.",
    "Vector search enables approximate nearest-neighbour lookup.",
    "The reconciler detects contradictory facts in long-term memory.",
    "Working memory holds the active conversation context.",
    "Chunk-level embeddings improve retrieval for long documents.",
    "Metadata filters narrow the candidate set before ranking.",
    "The ingest resolver classifies new writes against similar rows.",
    "Tenant isolation ensures data never leaks across scopes.",
    "Hybrid RRF fuses vector and keyword rankings into a single list.",
]


# ---------------------------------------------------------------------------
# Helper: build a seeded store + scope for a given corpus size
# ---------------------------------------------------------------------------


def _make_seeded_fixture(size_label: str):
    """Return a pytest fixture that seeds 'size' rows and tears them down."""

    @pytest.fixture(scope="module")
    def _fixture(db_pool):  # type: ignore[no-untyped-def]
        n_rows = _CORPUS_SIZES[size_label]
        provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
        store = MemoryStore(
            pool=db_pool,
            embedding_provider=provider,
            embedding_dim=_EMBED_DIM,
            enable_chunking=False,
        )
        run_id = uuid.uuid4().hex[:12]
        scope = MemoryScope(
            tenant_id=f"bm9-corpus-{size_label}-{run_id}",
            agent_id=f"bm9-corpus-agent-{size_label}-{run_id}",
        )
        for i in range(n_rows):
            content = _SAMPLE_CONTENTS[i % len(_SAMPLE_CONTENTS)] + f" idx-{i}"
            store.facts.create(
                SemanticFact(
                    agent_id=scope.agent_id,
                    tenant_id=scope.tenant_id,
                    content=content,
                    embedding=provider(content),
                ),
                scope,
            )
        yield store, scope, n_rows
        store.erase_all(scope)

    return _fixture


# Materialise the three fixtures at module level so pytest discovers them.
corpus_small = _make_seeded_fixture("small")
corpus_medium = _make_seeded_fixture("medium")
corpus_large = _make_seeded_fixture("large")


# ---------------------------------------------------------------------------
# B4-style: search() latency curve across corpus sizes
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_search_corpus_small(benchmark, corpus_small):  # type: ignore[no-untyped-def]
    """Baseline: search() on a 10-row corpus (near-zero scan time)."""
    store, scope, n_rows = corpus_small
    _run_search_benchmark(benchmark, store, scope, n_rows, label="small")


@pytest.mark.benchmark_pr
def test_search_corpus_medium(benchmark, corpus_medium):  # type: ignore[no-untyped-def]
    """Mid-range: search() on a 100-row corpus."""
    store, scope, n_rows = corpus_medium
    _run_search_benchmark(benchmark, store, scope, n_rows, label="medium")


@pytest.mark.benchmark_pr
def test_search_corpus_large(benchmark, corpus_large):  # type: ignore[no-untyped-def]
    """Scale: search() on a 1 000-row corpus — the primary latency data point."""
    store, scope, n_rows = corpus_large
    _run_search_benchmark(benchmark, store, scope, n_rows, label="large")


# ---------------------------------------------------------------------------
# Shared runner
# ---------------------------------------------------------------------------


def _run_search_benchmark(benchmark, store, scope, n_rows, label):  # type: ignore[no-untyped-def]
    """Embed once outside benchmark; time the DB round-trip only."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "agent memory semantic fact vector search"

    # Isolate embed time (AC-3)
    t0 = time.perf_counter()
    query_emb = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["corpus_size_label"] = label
    benchmark.extra_info["corpus_rows"] = n_rows
    benchmark.extra_info["mode"] = SearchMode.EXACT.value

    def _db_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_TOP_K,
            mode=SearchMode.EXACT,
            search_chunks=False,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)
    # Results ≤ min(n_rows, top_k)
    assert len(results) <= min(n_rows, _TOP_K)

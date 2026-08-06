"""
benchmarks/read/test_approx_recall.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-24: APPROX vs EXACT recall and vector index characterization.

Measures Recall@10(APPROX) / Recall@10(EXACT) at 1k/50k/500k rows and
sweeps embedding dimension (384/768/1536/3072).  A recall ratio below 0.95
fails the nightly Tier 2 gate (benchmark_nightly tier).

Recall definition
-----------------
Recall@K(APPROX) = |APPROX top-K ∩ EXACT top-K| / K

With ``HashingEmbeddingProvider``, APPROX and EXACT frequently agree at
100 % because DiskANN traverses enough graph neighbours on small corpora to
surface all candidates — recall = 1.0 trivially satisfies the >=0.95 floor.
On very large corpora the index's beam-search may miss a fraction of
neighbours; the nightly tier asserts recall >= 0.95 at 50 k rows.

Guideline
---------
* recall >= 0.95  → "APPROX safe: recall >= 0.95"
* recall < 0.95   → "FORCE EXACT: recall below 0.95 floor"

The assertion is only enforced at n_rows >= 50 000 (Tier 2+) because APPROX
on tiny corpora is known to have highly variable recall before the DiskANN
graph is sufficiently populated.

Markers
-------
benchmark_pr       1k corpus  — Tier 1, every PR
benchmark_nightly  50k corpus — Tier 2, nightly
benchmark_scale    500k/1M    — Tier 3, weekly
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

_RECALL_K = 10
_N_QUERIES = 20      # number of distinct query vectors to average recall over
_RECALL_FLOOR = 0.95  # BM-24 acceptance criterion (Phase 5.1 invariant table)
_DIMS = [384, 768, 1536, 3072]

_DEFAULT_DIM = 1536

# Corpus sizes for each tier
_N_ROWS_1K = 1_000
_N_ROWS_50K = 50_000
_N_ROWS_500K = 500_000

# Sample content pool — diverse vocabulary so hash-projection produces
# spread across the embedding space.
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
    "DiskANN builds an approximate graph index over embedding vectors.",
    "EXACT mode performs a brute-force full scan for ground truth.",
    "Recall at K measures overlap between approximate and exact results.",
    "Embedding dimension affects both recall quality and build time.",
    "The 0.95 recall floor is the BM-24 nightly gate threshold.",
    "Cosine distance is the metric used by the DiskANN vector index.",
    "Scale benchmarks cover 1k, 50k, 500k, and 1M row corpora.",
    "Benchmark fixtures seed deterministic rows using HashingEmbeddingProvider.",
    "The scope isolates each corpus so sizes are independently measurable.",
    "Agent memory SDK stores facts, episodes, and working memory rows.",
]

# Query texts: one per query index (deterministic — same provider → same vec)
_QUERY_TEXTS = [
    f"agent memory semantic fact vector search recall benchmark dim-sweep query-{i}"
    for i in range(_N_QUERIES)
]


# ---------------------------------------------------------------------------
# Recall helper
# ---------------------------------------------------------------------------


def _compute_recall(
    approx_ids: set[str],
    exact_ids: set[str],
    k: int,
) -> float:
    """Recall@K = |APPROX ∩ EXACT| / K.

    Uses ``k`` (not len(exact_ids)) as the denominator so a corpus that
    returns fewer than K results still produces a well-defined number (the
    overlap cannot exceed what was returned).
    """
    denominator = max(k, 1)
    return len(approx_ids & exact_ids) / denominator


# ---------------------------------------------------------------------------
# Fixture factory
# ---------------------------------------------------------------------------


def _make_recall_fixture(n_rows: int, dim: int = _DEFAULT_DIM):
    """Return a module-scoped pytest fixture that seeds *n_rows* rows.

    The fixture yields ``(store, scope, n_rows, dim)`` and erases the corpus
    on teardown.  Using a UUID-suffixed run_id prevents collisions between
    parallel or repeated benchmark runs.
    """

    @pytest.fixture(scope="module")
    def _fixture(db_pool):  # type: ignore[no-untyped-def]
        provider = HashingEmbeddingProvider(dim=dim)
        store = MemoryStore(
            pool=db_pool,
            embedding_provider=provider,
            embedding_dim=dim,
            enable_chunking=False,
        )
        run_id = uuid.uuid4().hex[:12]
        scope = MemoryScope(
            tenant_id=f"bm24-{n_rows}-{run_id}",
            agent_id=f"bm24-{n_rows}-agent-{run_id}",
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
        yield store, scope, n_rows, dim
        store.erase_all(scope)

    return _fixture


# Materialise the 1k fixture at module level so pytest discovers it.
corpus_1k = _make_recall_fixture(_N_ROWS_1K, dim=_DEFAULT_DIM)


# ---------------------------------------------------------------------------
# Shared recall-measurement runner
# ---------------------------------------------------------------------------


def _run_recall_benchmark(
    benchmark,  # pytest-benchmark fixture
    store: MemoryStore,
    scope: MemoryScope,
    n_rows: int,
    dim: int,
    provider: HashingEmbeddingProvider,
    *,
    assert_floor: bool,
) -> float:
    """Core measurement loop used by every test in this module.

    Computes EXACT ground truth for each query vector once (outside the
    benchmark timer), then benchmarks the APPROX search (DB round-trip only).
    Returns the average Recall@K across all _N_QUERIES query vectors.
    """
    # --- pre-compute query embeddings (isolated from both timers) ---
    query_embeddings = [provider(q) for q in _QUERY_TEXTS]

    # --- EXACT ground truth (outside benchmark timer) ---
    exact_id_sets: list[set[str]] = []
    for q_emb in query_embeddings:
        exact_results = store.facts.search(
            query_embedding=q_emb,
            scope=scope,
            top_k=_RECALL_K,
            metric=DistanceMetric.COSINE,
            mode=SearchMode.EXACT,
            search_chunks=False,
        )
        exact_id_sets.append({r.id for r in exact_results})

    # --- APPROX search (benchmarked — DB round-trip only) ---
    # We cycle through all query vectors; pytest-benchmark calls _approx()
    # multiple times, so we rotate through queries deterministically.
    _call_counter = [0]

    def _approx():
        idx = _call_counter[0] % _N_QUERIES
        _call_counter[0] += 1
        return store.facts.search(
            query_embedding=query_embeddings[idx],
            scope=scope,
            top_k=_RECALL_K,
            metric=DistanceMetric.COSINE,
            mode=SearchMode.APPROX,
            search_chunks=False,
        )

    # Run benchmark (times only the APPROX DB round-trip).
    benchmark(_approx)

    # --- compute recall from a single non-benchmark pass ---
    t0 = time.perf_counter()
    recall_samples: list[float] = []
    for i, q_emb in enumerate(query_embeddings):
        approx_results = store.facts.search(
            query_embedding=q_emb,
            scope=scope,
            top_k=_RECALL_K,
            metric=DistanceMetric.COSINE,
            mode=SearchMode.APPROX,
            search_chunks=False,
        )
        approx_ids = {r.id for r in approx_results}
        recall_samples.append(
            _compute_recall(approx_ids, exact_id_sets[i], _RECALL_K)
        )
    approx_latency_ms = (time.perf_counter() - t0) * 1000.0 / _N_QUERIES

    avg_recall = sum(recall_samples) / len(recall_samples) if recall_samples else 0.0

    # --- store metadata ---
    benchmark.extra_info["recall_at_10"] = round(avg_recall, 4)
    benchmark.extra_info["recall_floor"] = _RECALL_FLOOR
    benchmark.extra_info["n_rows"] = n_rows
    benchmark.extra_info["dim"] = dim
    benchmark.extra_info["n_queries"] = _N_QUERIES
    benchmark.extra_info["approx_avg_latency_ms"] = round(approx_latency_ms, 3)
    benchmark.extra_info["guideline"] = (
        "APPROX safe: recall >= 0.95"
        if avg_recall >= _RECALL_FLOOR
        else "FORCE EXACT: recall below 0.95 floor"
    )

    if assert_floor:
        assert avg_recall >= _RECALL_FLOOR, (
            f"BM-24 recall regression: Recall@{_RECALL_K}(APPROX) = {avg_recall:.4f} "
            f"< {_RECALL_FLOOR} floor at n_rows={n_rows}, dim={dim}. "
            "Force EXACT search or investigate index health."
        )

    return avg_recall


# ---------------------------------------------------------------------------
# Tier 1 — benchmark_pr (1k rows, runs on every PR)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_approx_vs_exact_recall_1k(benchmark, corpus_1k, db_pool):  # type: ignore[no-untyped-def]
    """BM-24-Tier1: APPROX recall@10 vs EXACT ground truth at 1k rows.

    At this corpus size, DiskANN may not be fully saturated and recall can be
    variable — the floor assertion is therefore intentionally skipped here.
    The recall value is recorded in benchmark.extra_info["recall_at_10"] for
    trending; a regression only fails the nightly (Tier 2) gate at 50k rows.
    """
    store, scope, n_rows, dim = corpus_1k
    provider = HashingEmbeddingProvider(dim=dim)

    _run_recall_benchmark(
        benchmark, store, scope, n_rows, dim, provider,
        assert_floor=False,  # 1k rows: observe, don't gate
    )


# ---------------------------------------------------------------------------
# Tier 1 — APPROX vs EXACT latency ratio at 1k rows
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_approx_vs_exact_latency_ratio_1k(benchmark, corpus_1k, db_pool):  # type: ignore[no-untyped-def]
    """BM-24-Tier1: APPROX vs EXACT search latency ratio at 1k rows.

    Benchmarks EXACT mode for comparison with test_approx_vs_exact_recall_1k.
    The ratio approx_ms / exact_ms is stored in extra_info.
    """
    store, scope, n_rows, dim = corpus_1k
    provider = HashingEmbeddingProvider(dim=dim)
    query_emb = provider(_QUERY_TEXTS[0])

    # Measure EXACT latency as a baseline (timed outside benchmark for ratio)
    t0 = time.perf_counter()
    exact_results = store.facts.search(
        query_embedding=query_emb,
        scope=scope,
        top_k=_RECALL_K,
        metric=DistanceMetric.COSINE,
        mode=SearchMode.EXACT,
        search_chunks=False,
    )
    exact_ms = (time.perf_counter() - t0) * 1000.0

    def _approx_search():
        return store.facts.search(
            query_embedding=query_emb,
            scope=scope,
            top_k=_RECALL_K,
            metric=DistanceMetric.COSINE,
            mode=SearchMode.APPROX,
            search_chunks=False,
        )

    approx_results = benchmark(_approx_search)

    assert isinstance(approx_results, list)
    assert isinstance(exact_results, list)

    # Latency ratio: lower means APPROX is faster (expected on large corpora)
    approx_ms = benchmark.stats.get("mean", 0.0) * 1000.0 if hasattr(benchmark, "stats") else 0.0
    ratio = (approx_ms / exact_ms) if exact_ms > 0 else None

    benchmark.extra_info["mode"] = "APPROX"
    benchmark.extra_info["n_rows"] = n_rows
    benchmark.extra_info["dim"] = dim
    benchmark.extra_info["exact_latency_ms"] = round(exact_ms, 3)
    benchmark.extra_info["approx_vs_exact_latency_ratio"] = (
        round(ratio, 4) if ratio is not None else "N/A"
    )


# ---------------------------------------------------------------------------
# Tier 2 — benchmark_nightly (50k rows, nightly gate)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
def test_approx_vs_exact_recall_nightly(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-24-Tier2: APPROX recall at 50k rows.

    Recall@10 must be >= 0.95 (BM-24 acceptance criterion).  A failure here
    blocks the nightly Tier 2 workflow (BM-21).

    The 50k corpus is expected to have been pre-seeded by ``seed_corpus.py``
    with ``--size 50k --seed 42``.  The scope pattern matches what
    ``make_scope(run_id="seed-42-50k", tenant_index=0, agent_index=0)``
    produces.  If the pre-seeded corpus is absent, this test seeds a fresh
    50k corpus inline (slower, but self-contained).
    """
    dim = _DEFAULT_DIM
    provider = HashingEmbeddingProvider(dim=dim)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=dim,
        enable_chunking=False,
    )

    # Try to use the pre-seeded corpus first (avoids inline seeding cost).
    # Scope matches seed_corpus.py's make_scope("seed-42-50k", 0, 0).
    scope = MemoryScope(
        tenant_id="bench-seed-42-50k-tenant-0",
        agent_id="bench-seed-42-50k-tenant-0-agent-0",
    )

    # Probe: if the pre-seeded corpus has no data, seed inline.
    probe = store.facts.search(
        query_embedding=provider(_QUERY_TEXTS[0]),
        scope=scope,
        top_k=1,
        mode=SearchMode.EXACT,
        search_chunks=False,
    )
    inline_scope: MemoryScope | None = None
    if not probe:
        # Pre-seeded corpus absent — seed 50k rows inline.
        run_id = uuid.uuid4().hex[:12]
        inline_scope = MemoryScope(
            tenant_id=f"bm24-50k-{run_id}",
            agent_id=f"bm24-50k-agent-{run_id}",
        )
        scope = inline_scope
        for i in range(_N_ROWS_50K):
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

    try:
        _run_recall_benchmark(
            benchmark, store, scope, _N_ROWS_50K, dim, provider,
            assert_floor=True,  # 50k rows: enforce the 0.95 gate
        )
    finally:
        if inline_scope is not None:
            store.erase_all(inline_scope)


# ---------------------------------------------------------------------------
# Tier 2 — dimension sweep (benchmark_nightly)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
@pytest.mark.parametrize("dim", _DIMS, ids=[f"dim{d}" for d in _DIMS])
def test_approx_recall_dim_sweep(benchmark, dim, db_pool):  # type: ignore[no-untyped-def]
    """BM-24: APPROX recall vs EXACT at dim 384 / 768 / 1536 / 3072.

    Seeds a fresh 1k corpus at the given dimension, measures Recall@10, and
    records latency.  The 0.95 floor is asserted at every dimension since a
    dimension mismatch that breaks the index would immediately surface here.

    Index build-time characterization
    ----------------------------------
    Because timing a ``CREATE VECTOR INDEX`` inside pytest is invasive
    (it would require dropping and re-creating the production index), this
    test instead measures APPROX vs EXACT *latency ratio* as a proxy for
    whether DiskANN is engaged — if APPROX is faster, the index was used;
    if APPROX ~= EXACT, the index fell back to a full scan.
    """
    provider = HashingEmbeddingProvider(dim=dim)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=dim,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm24-dim{dim}-{run_id}",
        agent_id=f"bm24-dim{dim}-agent-{run_id}",
    )

    # Seed 1k rows at this dimension.
    for i in range(_N_ROWS_1K):
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

    try:
        _run_recall_benchmark(
            benchmark, store, scope, _N_ROWS_1K, dim, provider,
            # Assert at every dim: a dimension mismatch crashes the index
            # and would produce recall = 0.0, which must fail immediately.
            assert_floor=True,
        )
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Tier 3 — benchmark_scale (500k rows, weekly)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_scale
def test_approx_vs_exact_recall_500k(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-24-Tier3: APPROX recall at 500k rows (weekly scale gate).

    Uses the pre-seeded ``seed-42-500k`` corpus if available; otherwise
    skips with an informative message rather than attempting an inline seed
    (500k rows inline would time-out a benchmark run).
    """
    dim = _DEFAULT_DIM
    provider = HashingEmbeddingProvider(dim=dim)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=dim,
        enable_chunking=False,
    )

    scope = MemoryScope(
        tenant_id="bench-seed-42-500k-tenant-0",
        agent_id="bench-seed-42-500k-tenant-0-agent-0",
    )

    probe = store.facts.search(
        query_embedding=provider(_QUERY_TEXTS[0]),
        scope=scope,
        top_k=1,
        mode=SearchMode.EXACT,
        search_chunks=False,
    )
    if not probe:
        pytest.skip(
            "Pre-seeded 500k corpus not found. "
            "Run: python benchmarks/seed_corpus.py --size 500k --seed 42"
        )

    _run_recall_benchmark(
        benchmark, store, scope, _N_ROWS_500K, dim, provider,
        assert_floor=True,  # 500k rows: enforce the 0.95 gate
    )

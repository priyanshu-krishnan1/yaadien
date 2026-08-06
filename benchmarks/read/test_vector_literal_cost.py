"""
benchmarks/read/test_vector_literal_cost.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-25: Vector-literal inlining cost breakdown.

Quantifies the SQL0901N-workaround cost of inlining a vector literal per
statement, split by source:

  client_build_ms      — _vec_to_str(embedding) wall-clock time (client-side)
  statement_size_bytes — UTF-8 bytes of the inlined literal string
  server_parse_ms_est  — estimated server-side parse time for the literal
                         (total_round_trip_ms - client_build_ms at dim=1536;
                          linearly extrapolated for other dims)

Dimensions swept: 384 / 768 / 1536 / 3072

At dim=1536 the literal is ~20 KB per INSERT and per VECTOR_DISTANCE query.

Recommendation and fixpack note stored in benchmark.extra_info["recommendation"]
and benchmark.extra_info["fixpack_note"].

Depends on: BM-7 (test_vec_to_str micro), BM-8 (test_add_messages write path).

Markers
-------
benchmark_micro  — CPU-only _vec_to_str measurement (no DB), Tier 0
benchmark_pr     — Db2 round-trip INSERT + SEARCH at dim=1536, Tier 1
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.repositories.base import _vec_to_str
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DIMS = [384, 768, 1536, 3072]
_EMBED_DIM_DEFAULT = 1536  # Db2 schema column dimension — cannot be changed at runtime
_SAMPLE_TEXT = "agent memory semantic fact vector search benchmark"
_SEED_ROWS = 10
_TOP_K = 5

# ---------------------------------------------------------------------------
# Helper: cost attribution and recommendation
# ---------------------------------------------------------------------------


def _dim_recommendation(dim: int, total_round_trip_ms: float) -> str:
    """Generate a concrete embedding-dim recommendation based on measured cost.

    Args:
        dim:                  The vector dimension being evaluated.
        total_round_trip_ms:  Measured (or extrapolated) end-to-end INSERT
                              latency in milliseconds.

    Returns:
        A human-readable recommendation string suitable for DECISIONS.md.
    """
    if dim <= 768:
        return (
            f"dim={dim}: Low literal overhead (~{total_round_trip_ms:.1f} ms round-trip). "
            "Preferred if retrieval quality is acceptable. "
            "text-embedding-3-small at dim=768 gives strong quality/cost tradeoff."
        )
    elif dim == 1536:
        return (
            f"dim={dim}: Default OpenAI text-embedding-ada-002 / text-embedding-3-small "
            f"dimension. ~20 KB literal per operation (~{total_round_trip_ms:.1f} ms "
            "round-trip). Acceptable for production; consider dim=768 if write latency "
            "is a bottleneck."
        )
    else:  # 3072
        return (
            f"dim={dim}: High literal overhead (~{total_round_trip_ms:.1f} ms round-trip). "
            "Only justified for highest-quality embeddings (text-embedding-3-large). "
            "Prefer dim=1536 or lower unless retrieval quality delta is proven critical."
        )


_FIXPACK_NOTE = (
    "Re-test TO_VECTOR(?) parameter binding on Db2 >= 12.1.5 fp1. "
    "SQL0901N is a known Db2 12.1.5 fp0 regression; if resolved on a newer "
    "fixpack, vector-literal inlining is no longer necessary and this entire "
    "workaround (and its ~20 KB per-statement overhead) can be removed."
)

# ---------------------------------------------------------------------------
# Module-scoped fixture: seeded corpus at dim=1536 for the SEARCH path test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vector_cost_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _SEED_ROWS facts at dim=1536; yield (store, scope); erase on teardown.

    The corpus is seeded once per module at the schema's native dimension
    (1536) so the SEARCH test can issue real VECTOR_DISTANCE queries without
    DDL changes.
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM_DEFAULT)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM_DEFAULT,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm25-vcost-{run_id}",
        agent_id=f"bm25-vcost-agent-{run_id}",
    )
    try:
        for i in range(_SEED_ROWS):
            content = _SAMPLE_TEXT + f" seed-row-{i}"
            store.facts.create(
                SemanticFact(
                    tenant_id=scope.tenant_id,
                    agent_id=scope.agent_id,
                    content=content,
                    embedding=provider(content),
                ),
                scope,
            )
        yield store, scope
    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Tier 0 (benchmark_micro): client-side _vec_to_str cost + statement size
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
@pytest.mark.parametrize("dim", _DIMS, ids=[f"dim{d}" for d in _DIMS])
def test_vec_to_str_statement_size(benchmark, dim):  # type: ignore[no-untyped-def]
    """BM-25 client-side: time _vec_to_str and report literal byte size per dim.

    Extends BM-7 (test_vec_to_str) with the "economics" framing: the focus is
    on the statement_size_bytes column to let readers see exactly how many bytes
    hit the wire per INSERT/SEARCH at each dimension.

    No DB required — pure CPU measurement, Tier 0.
    """
    embedding = [float(i) / float(dim) for i in range(dim)]

    vec_str = benchmark(_vec_to_str, embedding)

    statement_size_bytes = len(vec_str.encode("utf-8"))

    benchmark.extra_info["dim"] = dim
    benchmark.extra_info["statement_size_bytes"] = statement_size_bytes
    benchmark.extra_info["fixpack_note"] = _FIXPACK_NOTE
    benchmark.extra_info["recommendation"] = _dim_recommendation(
        dim, total_round_trip_ms=0.0  # no DB data at this tier
    )


# ---------------------------------------------------------------------------
# Tier 1 (benchmark_pr): INSERT round-trip cost attribution at dim=1536
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_insert_literal_cost_attribution(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-25-INSERT: End-to-end INSERT latency split into client-build + server-parse.

    Measures the overhead of inlining a 1536-dim vector literal on the write
    path and attributes it to three sources:

      client_build_ms      — time spent in _vec_to_str() on the client
      statement_size_bytes — UTF-8 bytes of the inlined literal (network cost)
      server_parse_ms_est  — total_round_trip_ms - client_build_ms (server cost)

    dim=1536 is used because it matches the Db2 schema's VECTOR(1536, FLOAT32)
    column; other dimensions would cause a Db2 dimension-mismatch error.

    The extrapolation to other dims is: server_parse_ms_est(dim) ≈
    server_parse_ms_est(1536) × (dim / 1536), since parse time scales roughly
    linearly with statement size.
    """
    dim = _EMBED_DIM_DEFAULT
    provider = HashingEmbeddingProvider(dim=dim)
    embedding = provider(_SAMPLE_TEXT + f" dim={dim}")

    # 1. Measure client-side string-building time BEFORE the benchmark loop.
    t0 = time.perf_counter()
    vec_str = _vec_to_str(embedding)
    client_build_ms = (time.perf_counter() - t0) * 1000.0
    statement_size_bytes = len(vec_str.encode("utf-8"))

    benchmark.extra_info["dim"] = dim
    benchmark.extra_info["client_build_ms"] = round(client_build_ms, 4)
    benchmark.extra_info["statement_size_bytes"] = statement_size_bytes

    # 2. Create a fresh isolated scope for this test.
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm25-insert-{run_id}",
        agent_id=f"bm25-insert-agent-{run_id}",
    )
    store = MemoryStore(pool=db_pool, embedding_dim=dim, enable_chunking=False)

    iter_counter = [0]

    def _insert() -> None:
        iter_counter[0] += 1
        store.facts.create(
            SemanticFact(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                content=_SAMPLE_TEXT + f" dim={dim} iter={iter_counter[0]}",
                embedding=embedding,
            ),
            scope,
        )

    try:
        # 3. Benchmark the full INSERT round-trip.
        benchmark(_insert)
    finally:
        store.erase_all(scope)

    # 4. Attribute the cost components.
    total_ms = benchmark.stats["mean"] * 1000.0
    server_parse_ms_est = max(0.0, total_ms - client_build_ms)

    benchmark.extra_info["total_round_trip_ms"] = round(total_ms, 3)
    benchmark.extra_info["server_parse_ms_est"] = round(server_parse_ms_est, 3)

    # 5. Record extrapolation table and recommendation.
    benchmark.extra_info["extrapolation"] = {
        str(d): round(server_parse_ms_est * (d / dim), 3) for d in _DIMS
    }
    benchmark.extra_info["recommendation"] = _dim_recommendation(dim, total_ms)
    benchmark.extra_info["fixpack_note"] = _FIXPACK_NOTE


# ---------------------------------------------------------------------------
# Tier 1 (benchmark_pr): SEARCH round-trip cost attribution at dim=1536
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_search_literal_cost_attribution(  # type: ignore[no-untyped-def]
    benchmark, vector_cost_store_and_scope, db_pool
):
    """BM-25-SEARCH: VECTOR_DISTANCE search latency split into client-build + server-parse.

    Same three-way attribution as the INSERT test, but for the query vector
    that is inlined into the VECTOR_DISTANCE expression on every search.

    The pre-seeded corpus (vector_cost_store_and_scope) contains _SEED_ROWS
    rows at dim=1536 — no per-test seeding overhead.

    client_build_ms      — _vec_to_str(query_embedding) time
    statement_size_bytes — byte size of the inlined query-vector literal
    server_parse_ms_est  — total_round_trip_ms - client_build_ms
    """
    store, scope = vector_cost_store_and_scope
    dim = _EMBED_DIM_DEFAULT
    provider = HashingEmbeddingProvider(dim=dim)
    query_embedding = provider(_SAMPLE_TEXT)

    # 1. Measure client-side query-vector serialization time.
    t0 = time.perf_counter()
    vec_str = _vec_to_str(query_embedding)
    client_build_ms = (time.perf_counter() - t0) * 1000.0
    statement_size_bytes = len(vec_str.encode("utf-8"))

    benchmark.extra_info["dim"] = dim
    benchmark.extra_info["client_build_ms"] = round(client_build_ms, 4)
    benchmark.extra_info["statement_size_bytes"] = statement_size_bytes
    benchmark.extra_info["seed_rows"] = _SEED_ROWS

    # 2. Benchmark the full SEARCH round-trip (query vector already built).
    def _db_search():
        return store.facts.search(
            query_embedding=query_embedding,
            scope=scope,
            top_k=_TOP_K,
            search_chunks=False,
        )

    results = benchmark(_db_search)
    assert isinstance(results, list)

    # 3. Attribute the cost components.
    total_ms = benchmark.stats["mean"] * 1000.0
    server_parse_ms_est = max(0.0, total_ms - client_build_ms)

    benchmark.extra_info["total_round_trip_ms"] = round(total_ms, 3)
    benchmark.extra_info["server_parse_ms_est"] = round(server_parse_ms_est, 3)

    # 4. Extrapolation and recommendation.
    benchmark.extra_info["extrapolation"] = {
        str(d): round(server_parse_ms_est * (d / dim), 3) for d in _DIMS
    }
    benchmark.extra_info["recommendation"] = _dim_recommendation(dim, total_ms)
    benchmark.extra_info["fixpack_note"] = _FIXPACK_NOTE

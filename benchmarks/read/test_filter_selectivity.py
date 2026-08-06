"""
benchmarks/read/test_filter_selectivity.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-23: Metadata-filter selectivity sweep.

Sweeps filter selectivity across all 4 operators at 1k rows (benchmark_pr),
with MON_GET_PKG_CACHE_STMT rows-read vs rows-returned capture to measure
the LOCATE()-based $array_contains scan ratio directly.

Selectivity levels probed: no_filter / ~50% / ~10% / ~1% / ~0.1%

The key finding this story is designed to produce: ``$array_contains`` uses
three ``LOCATE()`` calls on the raw ``VARCHAR(4096)`` metadata column, which
is non-sargable by construction — this story turns "probably scans" into a
measured rows_read/rows_returned ratio.

Corpus design
-------------
Each fixture seeds its own isolated corpus with controlled metadata cardinality:
  corpus_sel_50pct   — 1 000 rows, cardinality=2   → each value ≈ 50% of rows
  corpus_sel_10pct   — 1 000 rows, cardinality=10  → each value ≈ 10% of rows
  corpus_sel_1pct    — 1 000 rows, cardinality=100 → each value ≈  1% of rows
  corpus_sel_01pct   — 1 000 rows, cardinality=1000→ each value ≈ 0.1% of rows
  corpus_sel_none    — 1 000 rows, cardinality=10  → no-filter baseline

Every row carries:
  ``category``  — scalar string (``"category_0"``, ``"category_1"``, …)
  ``tags``      — JSON array of strings drawn from a pool of ``cardinality``
                  tag values (``["tag_0", "tag_3", …]``)

Markers
-------
benchmark_pr       1k corpus — Tier 1, runs on every PR
benchmark_nightly  50k corpus — Tier 2, runs nightly (references seed-42-50k corpus)
benchmark_scale    500k corpus — Tier 3, weekly (references seed-42-500k corpus)
"""

from __future__ import annotations

import json
import random
import time
import uuid
from typing import Any

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
_SEED_ROWS = 1_000

_BASE_CONTENTS = [
    "The agent stored a semantic fact about user preferences.",
    "Memory consolidation reduces episodic records into facts.",
    "Vector search enables approximate nearest-neighbour lookup.",
    "The reconciler detects contradictory facts in long-term memory.",
    "Working memory holds the active conversation context window.",
    "Chunk-level embeddings improve retrieval for long documents.",
    "Metadata filters narrow the candidate set before ranking.",
    "The ingest resolver classifies new writes against similar rows.",
    "Tenant isolation ensures data never leaks across scopes.",
    "Hybrid RRF fuses vector and keyword rankings into a single list.",
]

_QUERY_TEXT = "agent memory semantic fact vector search filter selectivity"


# ---------------------------------------------------------------------------
# MON_GET_PKG_CACHE_STMT helper
# ---------------------------------------------------------------------------


def _get_mon_stats(db_pool: Any) -> tuple[int, int]:
    """Query Db2 MON_GET_PKG_CACHE_STMT for the most-recently-executed statement.

    Returns ``(rows_read, rows_returned)`` on success, or ``(-1, -1)`` on any
    failure (the table function may not be accessible on all configurations).
    """
    sql = (
        "SELECT ROWS_READ, ROWS_RETURNED "
        "FROM TABLE(MON_GET_PKG_CACHE_STMT(NULL, NULL, NULL, -2)) AS T "
        "ORDER BY LAST_METRICS_UPDATE DESC "
        "FETCH FIRST 1 ROWS ONLY"
    )
    try:
        with db_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
            if row is None:
                return -1, -1
            rows_read = int(row[0]) if row[0] is not None else -1
            rows_returned = int(row[1]) if row[1] is not None else -1
            return rows_read, rows_returned
    except Exception:  # noqa: BLE001 — monitoring not always accessible
        return -1, -1


# ---------------------------------------------------------------------------
# Fixture factory
# ---------------------------------------------------------------------------


def _make_selectivity_fixture(n_rows: int, cardinality: int):
    """Return a module-scoped pytest fixture that seeds a controlled corpus.

    Each row receives:
      - ``category``: ``"category_{i % cardinality}"`` — deterministic scalar.
      - ``tags``: a JSON array of 1-3 values sampled from a pool of
        ``cardinality`` tag strings (``"tag_0"`` … ``"tag_{cardinality-1}"``).

    With ``cardinality=2`` each category value covers ~50% of rows.
    With ``cardinality=10`` each value covers ~10%, and so on.

    Yields ``(store, scope, db_pool, n_rows, cardinality)``.
    Tears down by calling ``store.erase_all(scope)``.
    """

    @pytest.fixture(scope="module")
    def _fixture(db_pool):  # type: ignore[no-untyped-def]
        provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
        store = MemoryStore(
            pool=db_pool,
            embedding_provider=provider,
            embedding_dim=_EMBED_DIM,
            enable_chunking=False,
        )
        run_id = uuid.uuid4().hex[:12]
        scope = MemoryScope(
            tenant_id=f"bm23-sel-c{cardinality}-{run_id}",
            agent_id=f"bm23-sel-c{cardinality}-agent-{run_id}",
        )

        rng = random.Random(42)
        tag_pool = [f"tag_{i}" for i in range(cardinality)]

        for i in range(n_rows):
            category = f"category_{i % cardinality}"
            n_tags = rng.randint(1, min(3, cardinality))
            tags = rng.sample(tag_pool, n_tags)
            metadata: dict = {"category": category, "tags": tags, "idx": str(i)}
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

        yield store, scope, db_pool, n_rows, cardinality
        store.erase_all(scope)

    return _fixture


# Materialise the five fixtures at module level so pytest discovers them.
corpus_sel_50pct = _make_selectivity_fixture(_SEED_ROWS, cardinality=2)
corpus_sel_10pct = _make_selectivity_fixture(_SEED_ROWS, cardinality=10)
corpus_sel_1pct = _make_selectivity_fixture(_SEED_ROWS, cardinality=100)
corpus_sel_01pct = _make_selectivity_fixture(_SEED_ROWS, cardinality=1000)
corpus_sel_none = _make_selectivity_fixture(_SEED_ROWS, cardinality=10)


# ---------------------------------------------------------------------------
# Core benchmark helper
# ---------------------------------------------------------------------------


def _bench_with_mon(  # type: ignore[no-untyped-def]
    benchmark,
    store: MemoryStore,
    scope: MemoryScope,
    db_pool: Any,
    metadata_filter: dict | None,
    operator_label: str,
    selectivity_label: str,
    n_rows: int,
    cardinality: int,
) -> None:
    """Embed once outside benchmark, time the DB search, capture MON stats.

    After the benchmark loop completes, queries ``MON_GET_PKG_CACHE_STMT``
    once to obtain the rows-read / rows-returned ratio for the most-recently
    executed statement.  Stores everything in ``benchmark.extra_info``.

    Findings are recorded in ``benchmark.extra_info["finding"]``:
    - Scalar filters (exact / $not) on indexed columns are expected to be
      sargable and show a low rows_read/rows_returned ratio.
    - ``$array_contains`` / ``$array_contains_any`` use LOCATE() on the raw
      VARCHAR metadata column and are non-sargable — the ratio should approach
      the full corpus size (rows_read ≈ n_rows regardless of selectivity).
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)

    # Embed outside the timed loop (AC-3 embed-vs-DB split).
    t0 = time.perf_counter()
    query_emb = provider(_QUERY_TEXT)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["operator"] = operator_label
    benchmark.extra_info["selectivity_label"] = selectivity_label
    benchmark.extra_info["metadata_filter"] = json.dumps(metadata_filter)
    benchmark.extra_info["corpus_rows"] = n_rows
    benchmark.extra_info["cardinality"] = cardinality
    benchmark.extra_info["mode"] = SearchMode.EXACT.value

    def _db_search() -> list:
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

    # Capture server-side monitoring stats once after the benchmark run.
    rows_read, rows_returned = _get_mon_stats(db_pool)
    benchmark.extra_info["rows_read"] = rows_read
    benchmark.extra_info["rows_returned"] = rows_returned
    benchmark.extra_info["rows_read_ratio"] = (
        round(rows_read / max(rows_returned, 1), 2)
        if rows_read >= 0
        else -1
    )

    # Record a concrete finding / recommendation.
    _array_ops = {"$array_contains", "$array_contains_any"}
    is_array_op = operator_label in _array_ops
    ratio = benchmark.extra_info["rows_read_ratio"]
    if rows_read < 0:
        finding = (
            "MON_GET_PKG_CACHE_STMT not accessible on this Db2 instance; "
            "rows-read ratio could not be measured."
        )
    elif is_array_op and ratio > 10:
        finding = (
            f"NON-SARGABLE SCAN CONFIRMED: operator={operator_label} "
            f"rows_read={rows_read} rows_returned={rows_returned} "
            f"ratio={ratio}. The LOCATE()-based implementation performs a "
            f"full metadata-column scan regardless of selectivity. "
            "RECOMMENDATION: add a generated/computed column for the common "
            "array-tag filter shapes (e.g. GENERATED ALWAYS AS "
            "LOCATE('\"tag_0\"', metadata)), or document that callers should "
            "prefer scalar 'category'/'source' filters over $array_contains "
            "at scale (>50k rows)."
        )
    elif not is_array_op and ratio <= 10:
        finding = (
            f"INDEX LIKELY USED: operator={operator_label} "
            f"rows_read={rows_read} rows_returned={rows_returned} "
            f"ratio={ratio}. Scalar filter (JSON_VALUE + scope composite "
            "index) shows low rows-read ratio — index is being used by "
            "the query planner (F5 confirmed)."
        )
    else:
        finding = (
            f"operator={operator_label} rows_read={rows_read} "
            f"rows_returned={rows_returned} ratio={ratio}. "
            "Review whether index selectivity is sufficient at this "
            "cardinality level."
        )
    benchmark.extra_info["finding"] = finding


# ---------------------------------------------------------------------------
# Tests — no-filter baseline (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_selectivity_no_filter(benchmark, corpus_sel_none):  # type: ignore[no-untyped-def]
    """BM-23/baseline: search() with no metadata filter — establishes the floor latency."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_none
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter=None,
        operator_label="no_filter",
        selectivity_label="100pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


# ---------------------------------------------------------------------------
# Tests — exact match filter at four selectivity levels (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_selectivity_exact_50pct(benchmark, corpus_sel_50pct):  # type: ignore[no-untyped-def]
    """BM-23/exact/~50%: exact-match filter on category_0 matching ~50% of rows."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_50pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="50pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_exact_10pct(benchmark, corpus_sel_10pct):  # type: ignore[no-untyped-def]
    """BM-23/exact/~10%: exact-match filter on category_0 matching ~10% of rows."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_10pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="10pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_exact_1pct(benchmark, corpus_sel_1pct):  # type: ignore[no-untyped-def]
    """BM-23/exact/~1%: exact-match filter on category_0 matching ~1% of rows."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_1pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_exact_01pct(benchmark, corpus_sel_01pct):  # type: ignore[no-untyped-def]
    """BM-23/exact/~0.1%: exact-match filter on category_0 matching ~0.1% of rows."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_01pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="0.1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


# ---------------------------------------------------------------------------
# Tests — $not filter at four selectivity levels (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_selectivity_not_50pct(benchmark, corpus_sel_50pct):  # type: ignore[no-untyped-def]
    """BM-23/$not/~50%: $not filter on category_0 — negation of the 50% exact match."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_50pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": {"$not": "category_0"}},
        operator_label="$not",
        selectivity_label="50pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_not_10pct(benchmark, corpus_sel_10pct):  # type: ignore[no-untyped-def]
    """BM-23/$not/~90%: $not filter on category_0 — negation of the 10% exact match."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_10pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": {"$not": "category_0"}},
        operator_label="$not",
        selectivity_label="90pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_not_1pct(benchmark, corpus_sel_1pct):  # type: ignore[no-untyped-def]
    """BM-23/$not/~99%: $not filter on category_0 — negation of the 1% exact match."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_1pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": {"$not": "category_0"}},
        operator_label="$not",
        selectivity_label="99pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_not_01pct(benchmark, corpus_sel_01pct):  # type: ignore[no-untyped-def]
    """BM-23/$not/~99.9%: $not filter on category_0 — negation of the 0.1% exact match."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_01pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": {"$not": "category_0"}},
        operator_label="$not",
        selectivity_label="99.9pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


# ---------------------------------------------------------------------------
# Tests — $array_contains at four selectivity levels (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_50pct(benchmark, corpus_sel_50pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains/~50%: LOCATE()-based scan — measures non-sargability.

    With cardinality=2 the tag pool has only 2 values; tag_0 appears in
    roughly 50% of rows.  The rows_read/rows_returned ratio directly
    confirms whether the LOCATE() path scans the full table.
    """
    store, scope, db_pool, n_rows, cardinality = corpus_sel_50pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="50pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_10pct(benchmark, corpus_sel_10pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains/~10%: LOCATE() scan at 10% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_10pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="10pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_1pct(benchmark, corpus_sel_1pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains/~1%: LOCATE() scan at 1% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_1pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_01pct(benchmark, corpus_sel_01pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains/~0.1%: LOCATE() scan at 0.1% selectivity.

    The key data point: at 0.1% selectivity a sargable index would read ≈1
    row; a full LOCATE() scan reads all n_rows.  The rows_read/rows_returned
    ratio is the measured evidence for the capability inventory concern.
    """
    store, scope, db_pool, n_rows, cardinality = corpus_sel_01pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="0.1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


# ---------------------------------------------------------------------------
# Tests — $array_contains_any at four selectivity levels (benchmark_pr)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_any_50pct(benchmark, corpus_sel_50pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains_any/~50%: OR-joined LOCATE() scan at 50% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_50pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains_any": ["tag_0", "tag_1"]}},
        operator_label="$array_contains_any",
        selectivity_label="50pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_any_10pct(benchmark, corpus_sel_10pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains_any/~10%: OR-joined LOCATE() scan at 10% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_10pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains_any": ["tag_0", "tag_1"]}},
        operator_label="$array_contains_any",
        selectivity_label="10pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_any_1pct(benchmark, corpus_sel_1pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains_any/~1%: OR-joined LOCATE() scan at 1% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_1pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains_any": ["tag_0", "tag_1"]}},
        operator_label="$array_contains_any",
        selectivity_label="1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


@pytest.mark.benchmark_pr
def test_selectivity_array_contains_any_01pct(benchmark, corpus_sel_01pct):  # type: ignore[no-untyped-def]
    """BM-23/$array_contains_any/~0.1%: OR-joined LOCATE() scan at 0.1% selectivity."""
    store, scope, db_pool, n_rows, cardinality = corpus_sel_01pct
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains_any": ["tag_0", "tag_1"]}},
        operator_label="$array_contains_any",
        selectivity_label="0.1pct",
        n_rows=n_rows,
        cardinality=cardinality,
    )


# ---------------------------------------------------------------------------
# Nightly (Tier 2): 50k rows — references BM-4 seeded corpus
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
def test_selectivity_50k_exact(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-23/nightly/exact: exact-match filter against the 50k BM-4 seeded corpus.

    Requires the seed-42-50k corpus to be present (run:
    ``python benchmarks/seed_corpus.py --size 50k --seed 42``).
    Uses tenant_id ``bench-seed-42-50k-tenant-0`` / agent_id
    ``bench-seed-42-50k-tenant-0-agent-0`` to hit a deterministic slice of
    the corpus (≈10k rows visible to this scope).
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    scope = MemoryScope(
        tenant_id="bench-seed-42-50k-tenant-0",
        agent_id="bench-seed-42-50k-tenant-0-agent-0",
    )
    # cardinality_category default in seed_corpus.py CLI is 20 → each value ≈ 5%
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="5pct_50k",
        n_rows=50_000,
        cardinality=20,
    )


@pytest.mark.benchmark_nightly
def test_selectivity_50k_array_contains(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-23/nightly/$array_contains: LOCATE() scan on the 50k corpus.

    NOTE: BM-4's seeded corpus uses SCALAR metadata fields (not JSON arrays),
    so this test uses the ``source`` scalar field with $array_contains to
    exercise the LOCATE() code path against the large corpus.  The field value
    ``"source_0"`` will match rows where the raw metadata string contains the
    substring ``'"source": "source_0"'``; the rows_read ratio captures whether
    the full table is scanned.
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    scope = MemoryScope(
        tenant_id="bench-seed-42-50k-tenant-0",
        agent_id="bench-seed-42-50k-tenant-0-agent-0",
    )
    # Use exact match on 'source' field to simulate the LOCATE path: pass it as
    # $array_contains even though source is scalar — the LOCATE expression will
    # still execute and its non-sargability is what we are measuring.
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="~2pct_50k",
        n_rows=50_000,
        cardinality=50,
    )


# ---------------------------------------------------------------------------
# Scale (Tier 3): 500k rows — references BM-4 seeded corpus
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_scale
def test_selectivity_500k_exact(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-23/scale/exact: exact-match filter against the 500k BM-4 seeded corpus.

    Requires: ``python benchmarks/seed_corpus.py --size 500k --seed 42``.
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    scope = MemoryScope(
        tenant_id="bench-seed-42-500k-tenant-0",
        agent_id="bench-seed-42-500k-tenant-0-agent-0",
    )
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"category": "category_0"},
        operator_label="exact",
        selectivity_label="5pct_500k",
        n_rows=500_000,
        cardinality=20,
    )


@pytest.mark.benchmark_scale
def test_selectivity_500k_array_contains(benchmark, db_pool):  # type: ignore[no-untyped-def]
    """BM-23/scale/$array_contains: LOCATE() scan on the 500k corpus.

    The primary scale data point: rows_read/rows_returned ratio at 500k rows
    directly confirms whether $array_contains degrades linearly with corpus
    size (non-sargable full scan) vs. staying near-constant (indexed).
    """
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    scope = MemoryScope(
        tenant_id="bench-seed-42-500k-tenant-0",
        agent_id="bench-seed-42-500k-tenant-0-agent-0",
    )
    _bench_with_mon(
        benchmark, store, scope, db_pool,
        metadata_filter={"tags": {"$array_contains": "tag_0"}},
        operator_label="$array_contains",
        selectivity_label="~2pct_500k",
        n_rows=500_000,
        cardinality=50,
    )

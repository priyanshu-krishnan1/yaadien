"""
benchmarks/read/test_multi_type_search.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B9: Multi-type facade ``store.search()`` fan-out.

``MemoryStore.search()`` fans out across up to 5 repository types
(working / episodic / facts / profiles / procedures).  This benchmark:

1. Seeds a mix of all 5 record types.
2. Calls ``store.search()`` with ``record_types`` set to subsets of
   1, 2, 3, and 5 types.
3. Asserts that the number of DB execute calls equals the number of
   record types requested (one ``search()`` execute per repo, plus one for
   the embedding call — but embedding is done in Python, not SQL).

Round-trip count assertion (AC-2):
  ``store.search()`` embeds the query once in Python, then calls
  ``repo.search()`` once per requested ``record_types`` entry.  Each
  ``repo.search()`` issues exactly 2 executes internally (ID-ranking pass +
  full-row-fetch pass).  Therefore:
    round_trips.executes == 2 × len(record_types)

Acceptance criteria covered
----------------------------
* AC-2 (round-trip count == n_types × 2 asserted)
* AC-3 (embed_ms stored; no DB embed call)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.counting import CountingPool, round_trips  # noqa: F401 – fixtures
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_DIM = 1536
_ROWS_PER_TYPE = 20  # rows seeded per memory type
_MAX_RESULTS = 10

# Subsets of record_types to parametrize the fan-out benchmark over.
_RECORD_TYPE_SUBSETS: list[tuple[str, list[str]]] = [
    ("1type_facts", ["facts"]),
    ("2types", ["working", "facts"]),
    ("3types", ["working", "episodic", "facts"]),
    ("5types_all", ["working", "episodic", "facts", "profiles", "procedures"]),
]

_SAMPLE_CONTENTS = {
    "working":    "Working memory turn: user asked about Python syntax.",
    "episodic":   "Episodic memory: user completed onboarding on Tuesday.",
    "facts":      "Semantic fact: the user's preferred language is Python.",
    "profiles":   "Entity profile: user is a senior software engineer.",
    "procedures": "Procedural memory: always format code with black before commit.",
}


# ---------------------------------------------------------------------------
# Module-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def multi_type_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed _ROWS_PER_TYPE records per memory type; yield (store, scope)."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-multi-{run_id}",
        agent_id=f"bm9-multi-agent-{run_id}",
    )
    for i in range(_ROWS_PER_TYPE):
        sfx = f" row-{i}"
        emb = lambda txt: provider(txt)  # noqa: E731
        store.working.create(
            WorkingMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=_SAMPLE_CONTENTS["working"] + sfx,
                embedding=emb(_SAMPLE_CONTENTS["working"] + sfx),
            ),
            scope,
        )
        store.episodic.create(
            EpisodicMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=_SAMPLE_CONTENTS["episodic"] + sfx,
                embedding=emb(_SAMPLE_CONTENTS["episodic"] + sfx),
            ),
            scope,
        )
        store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=_SAMPLE_CONTENTS["facts"] + sfx,
                embedding=emb(_SAMPLE_CONTENTS["facts"] + sfx),
            ),
            scope,
        )
        store.profiles.create(
            EntityProfile(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=_SAMPLE_CONTENTS["profiles"] + sfx,
                embedding=emb(_SAMPLE_CONTENTS["profiles"] + sfx),
            ),
            scope,
        )
        store.procedures.create(
            ProceduralMemory(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=_SAMPLE_CONTENTS["procedures"] + sfx,
                embedding=emb(_SAMPLE_CONTENTS["procedures"] + sfx),
            ),
            scope,
        )
    yield store, scope
    store.erase_all(scope)


# ---------------------------------------------------------------------------
# B9: fan-out latency benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize(
    "label,record_types",
    _RECORD_TYPE_SUBSETS,
    ids=[label for label, _ in _RECORD_TYPE_SUBSETS],
)
def test_multi_type_search_fanout(benchmark, multi_type_store_and_scope, label, record_types):  # type: ignore[no-untyped-def]
    """B9: Latency of store.search() fan-out as the number of record_types grows.

    Query-embedding time is isolated and stored in benchmark.extra_info.
    The total wall-clock time therefore isolates the DB fan-out cost.
    """
    store, scope = multi_type_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "user Python preference working memory semantic fact"

    # Isolate embed time (AC-3): store.search() calls _embedding_provider
    # internally, so we measure one call here to report the Python-side cost.
    t0 = time.perf_counter()
    _ = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["record_types"] = record_types
    benchmark.extra_info["n_types"] = len(record_types)
    benchmark.extra_info["rows_per_type"] = _ROWS_PER_TYPE

    def _search():
        return store.search(
            query=query_text,
            scope=scope,
            record_types=record_types,
            max_results=_MAX_RESULTS,
        )

    results = benchmark(_search)
    assert isinstance(results, list)
    benchmark.extra_info["result_count"] = len(results)


# ---------------------------------------------------------------------------
# B9-rt: round-trip count == 2 × n_types
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize(
    "label,record_types",
    _RECORD_TYPE_SUBSETS,
    ids=[f"rt_{label}" for label, _ in _RECORD_TYPE_SUBSETS],
)
def test_multi_type_search_round_trips(
    multi_type_store_and_scope, counting_pool, round_trips, label, record_types  # noqa: F811
):  # type: ignore[no-untyped-def]
    """B9-rt: Assert round-trip count == 2 × n_types (one two-step search per repo).

    ``repo.search()`` internally runs two SQL steps (ID-ranking + row-fetch),
    so ``store.search()`` across n_types issues exactly 2×n_types executes.
    """
    store, scope = multi_type_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)

    counting_store = MemoryStore(
        pool=counting_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )

    round_trips.reset()
    counting_store.search(
        query="user Python preference working memory semantic fact",
        scope=scope,
        record_types=record_types,
        max_results=_MAX_RESULTS,
    )
    expected = 2 * len(record_types)
    round_trips.assert_round_trips(expected)

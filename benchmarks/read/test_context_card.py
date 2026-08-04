"""
benchmarks/read/test_context_card.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B11: ``get_context_card()`` with and without ``include_long_term``.

Two variants are benchmarked:
  * ``include_long_term=False`` (default / ORC-1 path) — fetches only working
    memory; no embedding call, no facts/profiles lookup.
  * ``include_long_term=True`` — embeds the query, then fans out to
    ``facts.search()`` + ``profiles.search()`` in addition to the working
    memory ``list_all()``.

Round-trip assertions (AC-2):
  * Without long-term: 1 execute (working.list_all).
  * With long-term: 1 + 2 + 2 = 5 executes
    (working.list_all + facts two-step search + profiles two-step search).

Query-embedding time is isolated and stored in ``benchmark.extra_info``
(embed_ms is non-zero only for the include_long_term=True variant).

Acceptance criteria covered
----------------------------
* AC-2 (round-trip fan-out asserted for get_context_card)
* AC-3 (embed-vs-DB split)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    MemoryScope,
    SemanticFact,
)
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.counting import CountingPool, round_trips  # noqa: F401 – fixtures
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_DIM = 1536
_N_TURNS = 20       # working memory turns to seed
_N_FACTS = 10       # semantic facts to seed
_N_PROFILES = 5     # entity profiles to seed
_MAX_TURNS = 10     # max_turns for get_context_card

_TURN_CONTENTS = [
    "User said: tell me about memory consolidation in AI agents.",
    "Assistant replied: memory consolidation extracts durable facts from episodic turns.",
    "User asked: what is vector search used for?",
    "Assistant explained: vector search enables semantic similarity ranking.",
    "User mentioned: I prefer Python and dark mode.",
]
_FACT_CONTENTS = [
    "The user's preferred programming language is Python.",
    "The user prefers dark mode in all applications.",
    "The user works as a senior software engineer.",
    "The user lives in San Francisco.",
    "The user has 10 years of experience.",
]
_PROFILE_CONTENTS = [
    "User profile: software engineer, Python enthusiast.",
    "User entity: member since 2015, active contributor.",
    "User role: administrator on three Db2 instances.",
    "User interest: vector databases and AI agent memory.",
    "User preference: always include code examples in responses.",
]


# ---------------------------------------------------------------------------
# Module-scoped seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def context_card_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed working-memory turns + facts + profiles; yield (store, scope)."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-ctxcard-{run_id}",
        agent_id=f"bm9-ctxcard-agent-{run_id}",
        thread_id=f"bm9-ctxcard-thread-{run_id}",
    )
    # Seed working memory (conversation turns)
    for i in range(_N_TURNS):
        content = _TURN_CONTENTS[i % len(_TURN_CONTENTS)] + f" turn-{i}"
        store.add_messages(
            [{"role": "user" if i % 2 == 0 else "assistant", "content": content}],
            scope,
        )
    # Seed semantic facts
    for i, content in enumerate(_FACT_CONTENTS * (_N_FACTS // len(_FACT_CONTENTS) + 1)):
        if i >= _N_FACTS:
            break
        store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=content + f" fact-{i}",
                embedding=provider(content),
            ),
            scope,
        )
    # Seed entity profiles
    for i, content in enumerate(_PROFILE_CONTENTS * (_N_PROFILES // len(_PROFILE_CONTENTS) + 1)):
        if i >= _N_PROFILES:
            break
        store.profiles.create(
            EntityProfile(
                agent_id=scope.agent_id,
                tenant_id=scope.tenant_id,
                content=content + f" profile-{i}",
                embedding=provider(content),
            ),
            scope,
        )
    yield store, scope
    store.erase_all(scope)


# ---------------------------------------------------------------------------
# B11a: without long-term (ORC-1 baseline)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_context_card_no_long_term(benchmark, context_card_store_and_scope):  # type: ignore[no-untyped-def]
    """B11a: get_context_card() without long-term blending — ORC-1 path.

    No embedding call; no facts/profiles lookup.  Cost = 1 working.list_all.
    embed_ms is stored as 0 for chart consistency (AC-3).
    """
    store, scope = context_card_store_and_scope

    benchmark.extra_info["embed_ms"] = 0
    benchmark.extra_info["include_long_term"] = False
    benchmark.extra_info["n_turns"] = _N_TURNS

    def _get_card():
        return store.get_context_card(scope=scope, max_turns=_MAX_TURNS)

    card = benchmark(_get_card)
    assert card.turns is not None
    assert card.relevant_facts is None
    assert card.relevant_profiles is None
    benchmark.extra_info["turn_count"] = card.turn_count


# ---------------------------------------------------------------------------
# B11b: with long-term (PIPE-4 path)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_context_card_with_long_term(benchmark, context_card_store_and_scope):  # type: ignore[no-untyped-def]
    """B11b: get_context_card() with long-term blending — PIPE-4 path.

    Includes embedding + facts.search() + profiles.search().
    embed_ms is measured separately and stored in extra_info (AC-3).
    """
    store, scope = context_card_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    query_text = "Python preferences memory consolidation"

    # Isolate embed time
    t0 = time.perf_counter()
    _ = provider(query_text)
    embed_ms = (time.perf_counter() - t0) * 1000.0

    benchmark.extra_info["embed_ms"] = round(embed_ms, 3)
    benchmark.extra_info["include_long_term"] = True
    benchmark.extra_info["n_turns"] = _N_TURNS
    benchmark.extra_info["n_facts"] = _N_FACTS
    benchmark.extra_info["n_profiles"] = _N_PROFILES

    def _get_card():
        return store.get_context_card(
            scope=scope,
            max_turns=_MAX_TURNS,
            query=query_text,
            include_long_term=True,
        )

    card = benchmark(_get_card)
    assert card.turns is not None
    assert card.relevant_facts is not None
    assert card.relevant_profiles is not None
    benchmark.extra_info["turn_count"] = card.turn_count
    benchmark.extra_info["facts_count"] = len(card.relevant_facts)
    benchmark.extra_info["profiles_count"] = len(card.relevant_profiles)


# ---------------------------------------------------------------------------
# B11-rt: round-trip count assertions
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_context_card_no_long_term_round_trips(
    context_card_store_and_scope, counting_pool, round_trips  # noqa: F811
):  # type: ignore[no-untyped-def]
    """B11-rt-a: Without long-term: exactly 1 execute (working.list_all)."""
    store, scope = context_card_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)

    counting_store = MemoryStore(
        pool=counting_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )

    round_trips.reset()
    counting_store.get_context_card(scope=scope, max_turns=_MAX_TURNS)
    round_trips.assert_round_trips(1)


@pytest.mark.benchmark_pr
def test_context_card_with_long_term_round_trips(
    context_card_store_and_scope, counting_pool, round_trips  # noqa: F811
):  # type: ignore[no-untyped-def]
    """B11-rt-b: With long-term: 5 executes.

    1 (working.list_all) + 2 (facts two-step search) + 2 (profiles two-step search)
    = 5 total executes.
    """
    store, scope = context_card_store_and_scope
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)

    counting_store = MemoryStore(
        pool=counting_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )

    round_trips.reset()
    counting_store.get_context_card(
        scope=scope,
        max_turns=_MAX_TURNS,
        query="Python preferences",
        include_long_term=True,
    )
    # 1 working.list_all + 2 facts.search + 2 profiles.search = 5
    round_trips.assert_round_trips(5)

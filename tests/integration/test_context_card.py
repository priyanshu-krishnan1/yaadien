"""
tests/integration/test_context_card.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-4: context cards (v1 raw-turns + v2 long-term blending).

Part 1 — ORC-1 raw-turns behavior:
  1. Seeding max_turns+5 rows returns exactly max_turns in chronological order.
  2. latest_at matches the newest row's created_at.
  3. Empty scope returns an empty card without raising.
  4. A fixed Summarizer writes card.summary.
  5. A raising Summarizer falls back to summary=None.

Part 2 — PIPE-4 long-term blending + per-type backfill:
  6. IndexEmbeddingProvider wires a deterministic, no-external-service embedder.
  7. relevant_facts populated from real store.facts rows.
  8. relevant_profiles populated from real store.profiles rows.
  9. Per-type minimum backfill pulls in additional real rows when search hits
     are below min_results_by_type['facts'].
 10. query=None → relevant_facts and relevant_profiles are both None.
 11. include_long_term=False → relevant_facts and relevant_profiles are both None.

All tests are skipped automatically when DB2_DATABASE is not set.
Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers / stubs
# ---------------------------------------------------------------------------

class FixedSummarizer:
    """Returns a fixed string regardless of input — used to test card.summary."""

    SUMMARY = "This is a fixed summary for testing."

    def __call__(self, turns: list) -> str:  # type: ignore[type-arg]
        return self.SUMMARY


class RaisingSummarizer:
    """Always raises RuntimeError — tests the summarizer failure-fallback path."""

    def __call__(self, turns: list) -> str:  # type: ignore[type-arg]
        raise RuntimeError("Summarizer intentionally broken")


class IndexEmbeddingProvider:
    """Deterministic embedding provider — no external service needed.

    Returns make_unit_vec(1536, hash(text) % 1500).  Two texts with the same
    hash collide (cosine distance 0); different hashes are orthogonal (cosine
    distance 1), so vector search is fully deterministic in tests.
    """

    DIM = 1536

    def __call__(self, text: str) -> list[float]:
        return make_unit_vec(self.DIM, hash(text) % 1500)


# ---------------------------------------------------------------------------
# Part 1 — ORC-1 raw-turns behavior
# ---------------------------------------------------------------------------


class TestContextCardRawTurns:
    """ORC-1: get_context_card returns chronological recent turns."""

    def test_max_turns_slices_and_orders_chronologically(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Seeding max_turns+5 rows → exactly max_turns returned, oldest first."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-cc-1")
        store = MemoryStore(migrated_pool)
        max_turns = 7

        # Seed more rows than max_turns.
        for i in range(max_turns + 5):
            store.remember(
                WorkingMemory(
                    agent_id=unique_agent_id,
                    user_id="user-cc-1",
                    content=f"turn {i}",
                    metadata={"index": i},
                ),
                scope,
            )

        card = store.get_context_card(scope, max_turns=max_turns)

        assert card.turn_count == max_turns
        assert len(card.turns) == max_turns

        # Chronological order: each turn must be no older than the next.
        # strict=False: the two slices are intentionally offset by one, so
        # they always differ in length by design (pairwise-adjacent scan).
        for a, b in zip(card.turns, card.turns[1:], strict=False):
            assert a.created_at <= b.created_at, (
                "Turns must be in chronological (oldest-first) order"
            )

    def test_latest_at_matches_newest_turn(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """latest_at must equal the created_at of the most-recently created turn."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-cc-lat")
        store = MemoryStore(migrated_pool)

        for i in range(5):
            store.remember(
                WorkingMemory(agent_id=unique_agent_id, content=f"msg {i}"),
                scope,
            )

        card = store.get_context_card(scope, max_turns=5)

        assert card.latest_at is not None
        # latest_at must match the newest turn (last in chronological list).
        assert card.latest_at == card.turns[-1].created_at

    def test_empty_scope_returns_empty_card(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """An empty scope must return a zero-turn card without raising."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-cc-empty")
        store = MemoryStore(migrated_pool)

        card = store.get_context_card(scope, max_turns=20)

        assert card.turn_count == 0
        assert card.turns == []
        assert card.latest_at is None

    def test_fixed_summarizer_sets_card_summary(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """A configured Summarizer's return value must appear in card.summary."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-cc-sum")
        store = MemoryStore(migrated_pool, summarizer=FixedSummarizer())

        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="a turn"),
            scope,
        )

        card = store.get_context_card(scope, max_turns=5)

        assert card.summary == FixedSummarizer.SUMMARY

    def test_raising_summarizer_falls_back_to_none(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """A Summarizer that raises must not propagate; card.summary must be None."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-cc-raise")
        store = MemoryStore(migrated_pool, summarizer=RaisingSummarizer())

        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="a turn"),
            scope,
        )

        # Must not raise even though the summarizer always raises internally.
        card = store.get_context_card(scope, max_turns=5)

        assert card.summary is None


# ---------------------------------------------------------------------------
# Part 2 — PIPE-4 long-term blending + per-type backfill
# ---------------------------------------------------------------------------


class TestContextCardLongTermBlending:
    """PIPE-4: get_context_card with include_long_term=True populates
    relevant_facts and relevant_profiles via real vector search."""

    # ------------------------------------------------------------------
    # Fixture: MemoryStore with deterministic IndexEmbeddingProvider
    # ------------------------------------------------------------------

    @pytest.fixture()
    def lt_store(self, migrated_pool):
        """A MemoryStore equipped with the deterministic IndexEmbeddingProvider."""
        from agent_memory_sdk import MemoryStore

        return MemoryStore(
            migrated_pool,
            embedding_provider=IndexEmbeddingProvider(),
            enable_chunking=False,  # keep tests fast; no chunk fragmentation
        )

    @pytest.fixture()
    def lt_scope(self, unique_agent_id):
        """A unique MemoryScope for each long-term blending test."""
        from agent_memory_sdk.models import MemoryScope

        return MemoryScope(agent_id=unique_agent_id, user_id="user-lt-1")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seed_facts(self, lt_store, lt_scope, texts: list[str]):
        """Write SemanticFact rows whose embeddings come from IndexEmbeddingProvider."""
        from agent_memory_sdk.models import SemanticFact

        stored = []
        for text in texts:
            embedding = IndexEmbeddingProvider()(text)
            fact = SemanticFact(
                agent_id=lt_scope.agent_id,
                user_id=lt_scope.user_id,
                content=text,
                embedding=embedding,
            )
            stored.append(lt_store.remember(fact, lt_scope))
        return stored

    def _seed_profiles(self, lt_store, lt_scope, texts: list[str]):
        """Write EntityProfile rows with deterministic embeddings."""
        from agent_memory_sdk.models import EntityProfile

        stored = []
        for text in texts:
            embedding = IndexEmbeddingProvider()(text)
            profile = EntityProfile(
                agent_id=lt_scope.agent_id,
                user_id=lt_scope.user_id,
                content=text,
                embedding=embedding,
            )
            stored.append(lt_store.remember(profile, lt_scope))
        return stored

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_relevant_facts_populated(self, lt_store, lt_scope) -> None:
        """relevant_facts must contain real SemanticFact rows from the DB."""
        from agent_memory_sdk.models import SemanticFact, WorkingMemory

        query_text = "user preference python"
        self._seed_facts(lt_store, lt_scope, [query_text, "some other fact"])
        lt_store.remember(
            WorkingMemory(agent_id=lt_scope.agent_id, content="hi"),
            lt_scope,
        )

        card = lt_store.get_context_card(
            lt_scope,
            query=query_text,
            include_long_term=True,
        )

        assert card.relevant_facts is not None
        assert len(card.relevant_facts) >= 1
        assert all(isinstance(f, SemanticFact) for f in card.relevant_facts)
        # The exact-match fact must rank first (cosine distance = 0).
        assert card.relevant_facts[0].content == query_text

    def test_relevant_profiles_populated(self, lt_store, lt_scope) -> None:
        """relevant_profiles must contain real EntityProfile rows from the DB."""
        from agent_memory_sdk.models import EntityProfile, WorkingMemory

        query_text = "power python developer dark mode"
        self._seed_profiles(lt_store, lt_scope, [query_text, "another profile"])
        lt_store.remember(
            WorkingMemory(agent_id=lt_scope.agent_id, content="hi"),
            lt_scope,
        )

        card = lt_store.get_context_card(
            lt_scope,
            query=query_text,
            include_long_term=True,
        )

        assert card.relevant_profiles is not None
        assert len(card.relevant_profiles) >= 1
        assert all(isinstance(p, EntityProfile) for p in card.relevant_profiles)
        assert card.relevant_profiles[0].content == query_text

    def test_per_type_backfill_pulls_additional_rows(
        self, lt_store, lt_scope
    ) -> None:
        """Backfill fills up to min_results_by_type['facts'] when search hits are fewer.

        Strategy: seed only 1 fact whose embedding perfectly matches the query
        (hot_index chosen so it collides with the query vector), plus 2 extra
        facts at completely different hot_indices.  Set long_term_top_k=1 so
        the vector search returns exactly 1 hit, then require min=3 via
        min_results_by_type — backfill must supply the remaining 2 rows.
        """
        from agent_memory_sdk.models import WorkingMemory

        query_text = "backfill_test_query_unique_xyz"
        # One perfectly-matched fact.
        self._seed_facts(lt_store, lt_scope, [query_text])
        # Two extra facts at unrelated hot_indices (will be picked up by backfill).
        self._seed_facts(
            lt_store,
            lt_scope,
            ["backfill_extra_row_a_xyz", "backfill_extra_row_b_xyz"],
        )
        lt_store.remember(
            WorkingMemory(agent_id=lt_scope.agent_id, content="context turn"),
            lt_scope,
        )

        card = lt_store.get_context_card(
            lt_scope,
            query=query_text,
            include_long_term=True,
            long_term_top_k=1,           # restrict vector search to 1 hit
            min_results_by_type={"facts": 3},  # require 3 total
        )

        assert card.relevant_facts is not None
        # Must have at least 3 total rows (1 from vector search + ≥2 from backfill).
        assert len(card.relevant_facts) >= 3
        # No duplicate ids.
        ids = [f.id for f in card.relevant_facts]
        assert len(ids) == len(set(ids)), "Backfill must not duplicate relevant results"
        # The vector-search hit (exact-match) must come first.
        assert card.relevant_facts[0].content == query_text

    def test_no_query_leaves_long_term_fields_none(
        self, lt_store, lt_scope
    ) -> None:
        """query=None must leave relevant_facts and relevant_profiles as None."""
        from agent_memory_sdk.models import WorkingMemory

        self._seed_facts(lt_store, lt_scope, ["some fact"])
        self._seed_profiles(lt_store, lt_scope, ["some profile"])
        lt_store.remember(
            WorkingMemory(agent_id=lt_scope.agent_id, content="a turn"),
            lt_scope,
        )

        card = lt_store.get_context_card(lt_scope, query=None)

        assert card.relevant_facts is None
        assert card.relevant_profiles is None

    def test_include_long_term_false_leaves_fields_none(
        self, lt_store, lt_scope
    ) -> None:
        """include_long_term=False must leave relevant_facts/profiles as None."""
        from agent_memory_sdk.models import WorkingMemory

        self._seed_facts(lt_store, lt_scope, ["some fact"])
        lt_store.remember(
            WorkingMemory(agent_id=lt_scope.agent_id, content="a turn"),
            lt_scope,
        )

        card = lt_store.get_context_card(
            lt_scope,
            query="something",
            include_long_term=False,
        )

        assert card.relevant_facts is None
        assert card.relevant_profiles is None

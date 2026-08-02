"""
tests/integration/test_hybrid_search.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-7: hybrid retrieval (RRF keyword + vector fusion).

Covers:
- Test 1  Pure vector search baseline: row B (hot_index=2) ranks first when
          querying with make_unit_vec(1536, 2).
- Test 2  Hybrid RRF changes ranking: row A (keyword match on 'apple') rises
          relative to the pure-vector order.
- Test 3  Independent RRF formula verification: replicate the _keyword_tokens /
          _rrf_fuse logic in the test and assert the SDK order matches exactly.
- Test 4  Empty query_text with hybrid=True degenerates to vector-only order.
- Test 5  Search succeeds without Db2 Text Search Extender (Python-side keyword
          scoring confirmed by success on a standard DB instance).

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import re

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants matching the implementation in base.py (PIPE-1 / DECISIONS.md)
# ---------------------------------------------------------------------------

_RRF_K: int = 60
_DIM: int = 1536


# ---------------------------------------------------------------------------
# Helpers — mirror the SDK's _keyword_tokens / _rrf_fuse so tests are
# self-contained and do not import private symbols directly.
# ---------------------------------------------------------------------------


def _keyword_tokens(text: str) -> frozenset[str]:
    """Replicate BaseRepository._keyword_tokens: lowercase alphanumeric tokens."""
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _rrf_score(vector_rank: int, keyword_rank: int, k: int = _RRF_K) -> float:
    """Compute a single RRF score contribution from two 1-based ranks."""
    return 1.0 / (k + vector_rank) + 1.0 / (k + keyword_rank)


def _rrf_order(
    vector_ids: list[str],
    keyword_ids: list[str],
    k: int = _RRF_K,
) -> list[str]:
    """Replicate _rrf_fuse: return IDs sorted by descending RRF score."""
    scores: dict[str, float] = {}
    for rank, id_ in enumerate(vector_ids, start=1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    for rank, id_ in enumerate(keyword_ids, start=1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


# ---------------------------------------------------------------------------
# Fixture: seed rows with deliberately distinguishing text + deterministic
# unit-vector embeddings so vector-rank and keyword-rank can disagree.
#
# Row A  content="apple fruit tropical"  embedding=unit_vec(1)
#   → highest keyword overlap for query "apple" (token 'apple' matches)
#   → low  vector match for query=unit_vec(2)  (hot_index differs)
#
# Row B  content="banana citrus juice"   embedding=unit_vec(2)
#   → no  keyword match for "apple"
#   → perfect vector match for query=unit_vec(2)  (cosine = 1.0)
#
# Row C  content="apple banana blend"    embedding=unit_vec(3)
#   → partial keyword match (token 'apple' + 'banana')
#   → low  vector match for query=unit_vec(2)
#
# Row D  content="mango tropical fruit"  embedding=unit_vec(4)
#   → no  keyword match for "apple"
#   → low  vector match for query=unit_vec(2)
# ---------------------------------------------------------------------------

_ROWS = [
    {"label": "A", "content": "apple fruit tropical", "hot_index": 1},
    {"label": "B", "content": "banana citrus juice",  "hot_index": 2},
    {"label": "C", "content": "apple banana blend",   "hot_index": 3},
    {"label": "D", "content": "mango tropical fruit", "hot_index": 4},
]


class TestHybridSearch:
    """Hybrid retrieval (RRF keyword + vector fusion) — LIVE-7."""

    # ------------------------------------------------------------------
    # Shared per-class setup: seed the four rows into semantic_facts.
    # Each test method re-seeds via the `seeded` fixture to guarantee
    # per-test isolation through the `unique_agent_id` / `scope` chain.
    # ------------------------------------------------------------------

    @pytest.fixture()
    def seeded(self, store, scope):
        """Seed the A/B/C/D rows and return a mapping label → stored id."""
        from agent_memory_sdk.models import SemanticFact

        stored: dict[str, str] = {}
        for row in _ROWS:
            fact = SemanticFact(
                agent_id=scope.agent_id,
                content=row["content"],
                embedding=make_unit_vec(_DIM, row["hot_index"]),
            )
            s = store.facts.create(fact, scope)
            stored[row["label"]] = s.id
        return stored

    # ------------------------------------------------------------------
    # Test 1 — pure vector search baseline
    # ------------------------------------------------------------------

    def test_pure_vector_row_b_ranks_first(self, store, scope, seeded):
        """query=unit_vec(2) must rank row B (hot_index=2) first in pure vector mode."""
        results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=False,
        )

        assert len(results) >= 1, "Expected at least one result from pure vector search"
        assert results[0].id == seeded["B"], (
            f"Expected row B (id={seeded['B']!r}) as the nearest neighbour for "
            f"query=unit_vec(2), got id={results[0].id!r} "
            f"(content={results[0].content!r})"
        )

    # ------------------------------------------------------------------
    # Test 2 — hybrid RRF changes the ranking
    # ------------------------------------------------------------------

    def test_hybrid_raises_row_a_versus_pure_vector(self, store, scope, seeded):
        """Hybrid mode with query_text='apple' must promote row A above pure-vector order.

        Pure vector order for query=unit_vec(2): B first, then A/C/D (all equal
        and far from unit_vec(2)).  Row A has 'apple' in its content, row B has
        none.  The RRF fusion must therefore give row A a better combined rank
        than pure-vector alone would assign it.
        """
        # --- pure vector results (reference baseline) ---
        vector_results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=False,
        )
        vector_ids = [r.id for r in vector_results]
        vector_rank_a = vector_ids.index(seeded["A"]) if seeded["A"] in vector_ids else len(vector_ids)

        # --- hybrid results ---
        hybrid_results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=True,
            query_text="apple",
        )
        assert len(hybrid_results) >= 1, "Hybrid search returned no results"

        hybrid_ids = [r.id for r in hybrid_results]
        hybrid_rank_a = hybrid_ids.index(seeded["A"]) if seeded["A"] in hybrid_ids else len(hybrid_ids)

        assert hybrid_rank_a < vector_rank_a, (
            f"Row A should rank higher (lower index) in hybrid mode than in pure-vector mode. "
            f"Pure-vector rank={vector_rank_a}, hybrid rank={hybrid_rank_a}. "
            f"Pure-vector order: {vector_ids}, hybrid order: {hybrid_ids}"
        )

    # ------------------------------------------------------------------
    # Test 3 — independent RRF formula verification
    # ------------------------------------------------------------------

    def test_hybrid_order_matches_independently_computed_rrf(
        self, store, scope, seeded
    ):
        """Hybrid result order must exactly match our independent RRF replication.

        Steps:
        1. Run pure vector search to get the vector ranking.
        2. Compute keyword overlap for each row over the actual stored content.
        3. Build the keyword order (descending overlap).
        4. Apply our local _rrf_order() function.
        5. Assert the SDK hybrid result matches our independently computed order.
        """
        query_text = "apple"
        query_tokens = _keyword_tokens(query_text)

        # Step 1 — vector ranking (over-fetch to match SDK's hybrid over-fetch
        # of top_k*4; top_k=4 → fetch_k=16 but we only have 4 rows so top_k=4
        # is sufficient here).
        vector_results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=False,
        )
        vector_ids = [r.id for r in vector_results]

        # Build a content map from the vector results (all four rows).
        content_map = {r.id: r.content for r in vector_results}

        # Step 2 — keyword overlap scores
        keyword_scores: dict[str, int] = {
            id_: len(query_tokens & _keyword_tokens(content_map[id_]))
            for id_ in vector_ids
        }

        # Step 3 — keyword order (descending overlap; stable sort)
        keyword_ids = sorted(
            vector_ids,
            key=lambda i: keyword_scores[i],
            reverse=True,
        )

        # Step 4 — RRF fusion
        expected_order = _rrf_order(vector_ids, keyword_ids)

        # Step 5 — SDK hybrid result
        hybrid_results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=True,
            query_text=query_text,
        )
        actual_order = [r.id for r in hybrid_results]

        assert actual_order == expected_order[:len(actual_order)], (
            f"Hybrid search order does not match independently-computed RRF order.\n"
            f"  SDK returned:  {actual_order}\n"
            f"  Expected (RRF): {expected_order}\n"
            f"  Vector order:   {vector_ids}\n"
            f"  Keyword order:  {keyword_ids}\n"
            f"  Keyword scores: {keyword_scores}"
        )

    # ------------------------------------------------------------------
    # Test 4 — empty query_text degenerates to pure-vector order
    # ------------------------------------------------------------------

    def test_hybrid_empty_query_text_matches_vector_order(
        self, store, scope, seeded
    ):
        """hybrid=True with query_text='' must return the same order as hybrid=False.

        When the query text is empty, _keyword_tokens('') returns an empty
        frozenset, every candidate's keyword overlap is zero, and the keyword
        order is identical to the vector order.  RRF then only contains the
        vector contribution and the final order is the same as pure vector.
        """
        query_embedding = make_unit_vec(_DIM, 2)

        vector_results = store.facts.search(
            query_embedding=query_embedding,
            scope=scope,
            top_k=4,
            hybrid=False,
        )
        hybrid_results = store.facts.search(
            query_embedding=query_embedding,
            scope=scope,
            top_k=4,
            hybrid=True,
            query_text="",
        )

        vector_ids = [r.id for r in vector_results]
        hybrid_ids = [r.id for r in hybrid_results]

        assert hybrid_ids == vector_ids, (
            f"hybrid=True with empty query_text must produce the same order as "
            f"hybrid=False.\n  pure-vector: {vector_ids}\n  hybrid(''): {hybrid_ids}"
        )

    # ------------------------------------------------------------------
    # Test 5 — no Db2 Text Search Extender required
    # ------------------------------------------------------------------

    def test_hybrid_search_succeeds_without_text_search_extender(
        self, store, scope, seeded
    ):
        """Hybrid search must succeed on a standard Db2 instance.

        DECISIONS.md specifies that hybrid search uses Python-side token-set
        overlap — no CONTAINS/SCORE SQL functions, no Db2 Text Search Extender.
        This is verified implicitly: if the implementation were using Db2 TS
        functions and the extender were absent, the call would raise an SQL
        exception.  A successful return with expected content confirms the
        Python-only code path is active.
        """
        results = store.facts.search(
            query_embedding=make_unit_vec(_DIM, 2),
            scope=scope,
            top_k=4,
            hybrid=True,
            query_text="apple",
        )

        assert isinstance(results, list), (
            "store.facts.search() must return a list (not raise)"
        )
        assert len(results) >= 1, (
            "Hybrid search must return at least one result from the seeded rows"
        )

        # Confirm we got back results from our seeded scope (content sanity check)
        returned_contents = {r.content for r in results}
        seeded_contents = {row["content"] for row in _ROWS}
        assert returned_contents.issubset(seeded_contents), (
            f"Hybrid results contain unexpected content: "
            f"{returned_contents - seeded_contents}"
        )

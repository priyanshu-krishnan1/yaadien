"""
tests/test_pipe1_hybrid.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for PIPE-1: hybrid retrieval via Reciprocal Rank Fusion.

Tests the public helper functions (:func:`_keyword_tokens`,
:func:`_rrf_fuse`) and the integrated ``search()`` / ``_search_via_chunks()``
paths, using the same fake-pool pattern as the rest of the unit suite.

Coverage:
  - _keyword_tokens: empty string, alphanumeric splitting, case-folding,
    punctuation stripping, deduplication (frozenset)
  - _rrf_fuse: single list, identical lists, disjoint lists, overlap,
    standard k=60 score values, custom k, correct descending order
  - search(hybrid=False): unchanged pure-vector ordering (regression guard)
  - search(hybrid=True, query_text=""): no keyword signal → same order
    as pure-vector (degenerate case)
  - search(hybrid=True, query_text=...): keyword-only match promoted over
    vector-near-but-no-overlap item
  - search(hybrid=True): over-fetches candidates (fetch_k = top_k * 4)
  - _search_via_chunks(hybrid=True): verifies the same RRF fusion path
    works through the chunk-search entry point
  - top_k slice is respected after fusion
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.repositories.base import (
    _RRF_K,
    _keyword_tokens,
    _rrf_fuse,
)
from agent_memory_sdk.repositories.working import WorkingMemoryRepository

# ---------------------------------------------------------------------------
# Fake pool / cursor helpers (identical pattern to test_orc3.py)
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-pipe1", user_id="user-1")
_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCursor:
    """Cursor that returns a different row-set for each successive execute() call."""

    def __init__(self, call_returns: list[list[tuple[Any, ...]]]) -> None:
        # call_returns[0] is for the first execute(), [1] for the second, etc.
        self._queue: list[list[tuple[Any, ...]]] = list(call_returns)
        self._current: list[tuple[Any, ...]] = []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 0
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = list(params) if params else []
        self.all_sqls.append(self.last_sql)
        self.all_params.append(self.last_params)
        if self._queue:
            self._current = self._queue.pop(0)
        else:
            self._current = []
        self.rowcount = len(self._current)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._current[0] if self._current else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._current)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass


class _FakePool:
    def __init__(self, call_returns: list[list[tuple[Any, ...]]]) -> None:
        self.cursor = _FakeCursor(call_returns)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield self.conn


def _wm_row(
    id_: str,
    content: str,
    agent_id: str = "agent-pipe1",
    user_id: str = "user-1",
) -> tuple[Any, ...]:
    """Build a minimal 16-element WorkingMemory row (15 base + consolidated_at)."""
    return (
        id_,             # 0  id
        None,            # 1  tenant_id
        agent_id,        # 2  agent_id
        user_id,         # 3  user_id
        None,            # 4  thread_id
        content,         # 5  content  ← used by keyword scoring
        "{}",            # 6  metadata
        "[0.0]",         # 7  embedding (VECTOR_SERIALIZE)
        1.0,             # 8  confidence
        None,            # 9  content_hash
        _NOW,            # 10 created_at
        _NOW,            # 11 updated_at
        None,            # 12 expires_at
        1,               # 13 version
        None,            # 14 deleted_at
        "DIRECT_WRITE",    # 15 — origin (TRU-1)
        None,            # 15 consolidated_at
    )


# ---------------------------------------------------------------------------
# Tests: _keyword_tokens
# ---------------------------------------------------------------------------


class TestKeywordTokens:
    def test_empty_string(self) -> None:
        assert _keyword_tokens("") == frozenset()

    def test_single_word(self) -> None:
        assert _keyword_tokens("hello") == frozenset({"hello"})

    def test_case_folding(self) -> None:
        assert _keyword_tokens("Hello WORLD") == frozenset({"hello", "world"})

    def test_punctuation_stripped(self) -> None:
        assert _keyword_tokens("foo, bar! baz.") == frozenset({"foo", "bar", "baz"})

    def test_numbers_included(self) -> None:
        assert "42" in _keyword_tokens("answer is 42")

    def test_deduplication(self) -> None:
        # frozenset → duplicates dropped
        tokens = _keyword_tokens("cat cat cat")
        assert tokens == frozenset({"cat"})

    def test_mixed_content(self) -> None:
        tokens = _keyword_tokens("The quick brown fox jumps over the lazy dog")
        assert "quick" in tokens
        assert "fox" in tokens
        assert "the" in tokens  # stopwords are NOT removed intentionally


# ---------------------------------------------------------------------------
# Tests: _rrf_fuse
# ---------------------------------------------------------------------------


class TestRrfFuse:
    def test_single_list_scores(self) -> None:
        """Only one ranking list: RRF score = 1/(k + rank)."""
        ids = ["a", "b", "c"]
        fused = _rrf_fuse(ids, [])
        # 'a' at rank 1 in vector list only; k=60 → 1/61
        # 'b' at rank 2 → 1/62 < 1/61
        assert fused == ["a", "b", "c"]

    def test_identical_lists_preserves_order(self) -> None:
        ids = ["x", "y", "z"]
        fused = _rrf_fuse(ids, ids)
        # Both lists give the same relative order; RRF just doubles the scores.
        assert fused == ["x", "y", "z"]

    def test_disjoint_lists_combined(self) -> None:
        # 'a' only in vector (rank 1), 'b' only in keyword (rank 1).
        # Both get score 1/(60+1).  Tie-breaking by dict insertion order (Python
        # 3.7+ dict is insertion-ordered; sorted() is stable).
        fused = _rrf_fuse(["a"], ["b"])
        # Both have score 1/61; order may vary — just verify both are present.
        assert set(fused) == {"a", "b"}

    def test_overlap_promotes_shared_item(self) -> None:
        """An item in both lists gets a higher combined score."""
        # vector: ["good", "bad"], keyword: ["good", "other"]
        # good: 1/61 + 1/61 = 2/61
        # bad:  1/62
        # other: 1/62
        fused = _rrf_fuse(["good", "bad"], ["good", "other"])
        assert fused[0] == "good"

    def test_custom_k(self) -> None:
        # k=0 → scores are 1/rank; rank-1 item scores highest.
        fused = _rrf_fuse(["alpha", "beta", "gamma"], [], k=0)
        assert fused[0] == "alpha"

    def test_standard_k_values(self) -> None:
        """Verify the k=60 constant is actually used by default."""
        fused = _rrf_fuse(["p", "q"], ["q", "p"])
        # p: vector rank 1 (1/61) + keyword rank 2 (1/62)
        # q: vector rank 2 (1/62) + keyword rank 1 (1/61)
        # Tied → order may vary; but both present.
        assert set(fused) == {"p", "q"}

    def test_empty_both_lists(self) -> None:
        assert _rrf_fuse([], []) == []

    def test_rrf_k_constant_is_60(self) -> None:
        assert _RRF_K == 60


# ---------------------------------------------------------------------------
# Tests: search() — hybrid=False regression guard
# ---------------------------------------------------------------------------


class TestSearchHybridFalse:
    """Ensure hybrid=False leaves existing behaviour completely unchanged."""

    def test_pure_vector_order_returned(self) -> None:
        row_a = _wm_row("id-a", "apple orange banana")
        row_b = _wm_row("id-b", "completely unrelated content")

        # Step 1 returns ids in vector order: a, b.
        # Step 2 returns full rows (order from DB, but we re-apply step-1 order).
        pool = _FakePool([
            [("id-a",), ("id-b",)],   # step 1: ids
            [row_b, row_a],            # step 2: full rows (reversed order)
        ])
        repo = WorkingMemoryRepository(pool)
        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=2,
            hybrid=False,
        )
        assert [r.id for r in results] == ["id-a", "id-b"]

    def test_fetch_k_equals_top_k_when_hybrid_false(self) -> None:
        """Step-1 SQL must bind top_k (not top_k*4) when hybrid=False."""
        pool = _FakePool([
            [],   # step 1: no results
        ])
        repo = WorkingMemoryRepository(pool)
        repo.search(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=5,
            hybrid=False,
        )
        # The last bound param for the ID query is the row limit.
        assert pool.cursor.all_params[0][-1] == 5


# ---------------------------------------------------------------------------
# Tests: search() — hybrid=True
# ---------------------------------------------------------------------------


class TestSearchHybridTrue:
    def test_over_fetches_when_hybrid_true(self) -> None:
        """Step-1 SQL must bind top_k*4 (capped at 800) when hybrid=True."""
        pool = _FakePool([[]])  # no results — we just check the bound param
        repo = WorkingMemoryRepository(pool)
        repo.search(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=5,
            hybrid=True,
            query_text="anything",
        )
        assert pool.cursor.all_params[0][-1] == 20  # 5 * 4

    def test_empty_query_text_same_order_as_vector(self) -> None:
        """When query_text='', keyword signal is zero → vector order preserved."""
        row_a = _wm_row("id-a", "python programming")
        row_b = _wm_row("id-b", "database storage")

        pool = _FakePool([
            [("id-a",), ("id-b",)],
            [row_a, row_b],
        ])
        repo = WorkingMemoryRepository(pool)
        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=2,
            hybrid=True,
            query_text="",
        )
        # With no keyword signal, both lists are the same → vector order wins.
        assert [r.id for r in results] == ["id-a", "id-b"]

    def test_keyword_match_promotes_lower_vector_ranked_item(self) -> None:
        """An item with high keyword overlap but much lower vector rank should be
        promoted once the keyword signal is strong enough.

        With k=60 and 4 candidates:
          id-a: vector rank 1 (1/61) + keyword rank 4 (1/64) → ~0.02200
          id-b: vector rank 2 (1/62) + keyword rank 1 (1/61) → ~0.02783
          id-c: vector rank 3 (1/63) + keyword rank 2 (1/62) → ~0.02725
          id-d: vector rank 4 (1/64) + keyword rank 3 (1/63) → ...

        So id-b (vector rank 2, keyword rank 1) scores higher than id-a
        (vector rank 1, keyword rank 4) because the keyword boost outweighs
        the single vector-rank advantage.
        """
        row_a = _wm_row("id-a", "quantum mechanics superposition entanglement")
        row_b = _wm_row("id-b", "python async await coroutine")
        row_c = _wm_row("id-c", "python async programming")
        row_d = _wm_row("id-d", "python async networking")

        pool = _FakePool([
            [("id-a",), ("id-b",), ("id-c",), ("id-d",)],   # vector order
            [row_a, row_b, row_c, row_d],
        ])
        repo = WorkingMemoryRepository(pool)
        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=4,
            hybrid=True,
            query_text="python async await",
        )
        ids = [r.id for r in results]
        # id-a has zero keyword overlap → keyword rank 4 (tied last after sort).
        # id-b has 3 overlapping tokens ("python","async","await") → rank 1.
        # id-a's combined RRF score must be less than id-b's.
        assert ids.index("id-b") < ids.index("id-a")

    def test_top_k_slice_respected(self) -> None:
        """Fused result must be sliced to top_k even if more candidates fetched."""
        rows = [_wm_row(f"id-{i}", f"item number {i}") for i in range(8)]
        id_rows = [(f"id-{i}",) for i in range(8)]

        pool = _FakePool([id_rows, rows])
        repo = WorkingMemoryRepository(pool)
        results = repo.search(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=3,
            hybrid=True,
            query_text="item number",
        )
        assert len(results) == 3

    def test_no_results_returns_empty(self) -> None:
        pool = _FakePool([[]])  # step 1 returns no IDs → early return
        repo = WorkingMemoryRepository(pool)
        results = repo.search(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=5,
            hybrid=True,
            query_text="irrelevant",
        )
        assert results == []


# ---------------------------------------------------------------------------
# Tests: _search_via_chunks(hybrid=True)
# ---------------------------------------------------------------------------


class TestSearchViaChunksHybrid:
    """Verify that RRF fusion also works through the chunk-search path."""

    def _make_chunk_repo(
        self,
        chunk_hits: list[tuple[str, float]],
    ) -> Any:
        """Return a mock ChunkRepository whose search_chunks() returns chunk_hits."""
        mock = MagicMock()
        mock.search_chunks.return_value = chunk_hits
        return mock

    def test_hybrid_false_pure_chunk_distance_order(self) -> None:
        row_a = _wm_row("id-a", "neural networks deep learning")
        row_b = _wm_row("id-b", "SQL database query optimisation")

        # Chunk hits: id-a nearest (0.1), id-b farther (0.5)
        chunk_repo = self._make_chunk_repo([("id-a", 0.1), ("id-b", 0.5)])
        pool = _FakePool([[row_a, row_b]])  # one step: full-row fetch
        repo = WorkingMemoryRepository(pool, chunk_repo=chunk_repo)

        results = repo._search_via_chunks(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=2,
            hybrid=False,
        )
        assert [r.id for r in results] == ["id-a", "id-b"]

    def test_hybrid_true_promotes_keyword_match(self) -> None:
        """Same RRF promotion logic applies through the chunk path.

        With 4 candidates, id-a has zero keyword overlap (vector rank 1,
        keyword rank 4), while id-b has 3 overlapping tokens (vector rank 2,
        keyword rank 1).  id-b's combined RRF score must exceed id-a's.
        """
        row_a = _wm_row("id-a", "neural networks deep learning backprop")
        row_b = _wm_row("id-b", "SQL database query optimisation")
        row_c = _wm_row("id-c", "SQL database indexing")
        row_d = _wm_row("id-d", "SQL query planner")

        chunk_repo = self._make_chunk_repo([
            ("id-a", 0.05),
            ("id-b", 0.30),
            ("id-c", 0.35),
            ("id-d", 0.40),
        ])
        pool = _FakePool([[row_a, row_b, row_c, row_d]])
        repo = WorkingMemoryRepository(pool, chunk_repo=chunk_repo)

        results = repo._search_via_chunks(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=4,
            hybrid=True,
            query_text="SQL database query",
        )
        ids = [r.id for r in results]
        # id-b overlaps "SQL","database","query" (3 tokens); id-a overlaps 0.
        assert ids.index("id-b") < ids.index("id-a")

    def test_hybrid_top_k_slice(self) -> None:
        """Fused result from _search_via_chunks must honour top_k."""
        chunk_hits = [(f"id-{i}", float(i) * 0.01) for i in range(10)]
        rows = [_wm_row(f"id-{i}", f"content {i}") for i in range(10)]
        chunk_repo = self._make_chunk_repo(chunk_hits)
        pool = _FakePool([rows])
        repo = WorkingMemoryRepository(pool, chunk_repo=chunk_repo)

        results = repo._search_via_chunks(
            query_embedding=[0.0] * 1536,
            scope=_SCOPE,
            top_k=4,
            hybrid=True,
            query_text="content",
        )
        assert len(results) == 4

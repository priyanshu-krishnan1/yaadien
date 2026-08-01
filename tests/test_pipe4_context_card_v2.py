"""
tests/test_pipe4_context_card_v2.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for PIPE-4: blending durable long-term memory (facts/profiles)
into ContextCard, with per-type minimum-result backfill balancing.

Coverage:
  - Default (no query) path is byte-for-byte unchanged from ORC-1:
    relevant_facts/relevant_profiles stay None, no facts/profiles repo touched.
  - query + include_long_term=True populates relevant_facts/relevant_profiles
    from store.facts.search() / store.profiles.search().
  - Per-type min_results_by_type backfill: when search() returns fewer than
    the configured minimum, the section is topped up with that type's most-
    recent records (via list_all()), skipping ids already present.
  - Backfill is skipped when the search already meets/exceeds the minimum.
  - Unknown min_results_by_type keys / negative values raise ValueError.
  - Missing embedding_provider + include_long_term=True raises ValueError.
  - embedding_provider raising an exception degrades gracefully to a
    recency-only view instead of propagating.
  - long_term_top_k validation (>= 1).
  - include_long_term=True with query=None (or empty string) is a no-op,
    same as the default path.

Uses the same queue-based fake-pool pattern as test_pipe1_hybrid.py — each
successive cursor.execute() call is fed its own canned row-set, since
get_context_card() with long-term blending active issues multiple SQL
statements in sequence (working.list_all, facts.search's id+row fetch,
optional facts.list_all backfill, profiles.search's id+row fetch, optional
profiles.list_all backfill).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import EntityProfile, MemoryScope, SemanticFact
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ContextCard

# ---------------------------------------------------------------------------
# Fake pool / cursor — queue-based, one row-set per successive execute() call
# (identical pattern to test_pipe1_hybrid.py / test_orc3.py)
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-pipe4", user_id="user-1")
_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


class _FakeCursor:
    """Cursor that returns a different row-set for each successive execute() call."""

    def __init__(self, call_returns: list[list[tuple[Any, ...]]]) -> None:
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


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _wm_row(id_: str, content: str = "hello") -> tuple[Any, ...]:
    """16-element WorkingMemory row (15 base cols + consolidated_at)."""
    return (
        id_, None, "agent-pipe4", "user-1", None,
        content, "{}",
        "[0.0]",
        1.0, None,
        _NOW, _NOW, None, 1, None,
        None,
    )


def _fact_row(id_: str, content: str = "fact") -> tuple[Any, ...]:
    """18-element SemanticFact row (15 base cols + 3 supersession cols)."""
    return (
        id_, None, "agent-pipe4", "user-1", None,
        content, "{}",
        "[0.0]",
        1.0, None,
        _NOW, _NOW, None, 1, None,
        None, None, None,
    )


def _profile_row(id_: str, content: str = "profile") -> tuple[Any, ...]:
    """15-element EntityProfile row (base cols only)."""
    return (
        id_, None, "agent-pipe4", "user-1", None,
        content, "{}",
        "[0.0]",
        1.0, None,
        _NOW, _NOW, None, 1, None,
    )


def _echo_embedder(text: str) -> list[float]:
    """Trivial embedding_provider stub — value doesn't matter for these tests."""
    return [0.1, 0.2, 0.3]


def _search_id_rows(ids: list[str]) -> list[tuple[Any, ...]]:
    """Row-set for the id-ranking SELECT step of BaseRepository.search()."""
    return [(i,) for i in ids]


# ---------------------------------------------------------------------------
# Default path unchanged (ORC-1 compatibility)
# ---------------------------------------------------------------------------

class TestDefaultPathUnchanged:
    def test_no_query_leaves_relevant_fields_none(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(_SCOPE, max_turns=1)

        assert isinstance(card, ContextCard)
        assert card.relevant_facts is None
        assert card.relevant_profiles is None
        # Only the single working-memory list_all() call was issued.
        assert len(pool.cursor.all_sqls) == 1
        assert "working_memory" in pool.cursor.all_sqls[0]

    def test_query_without_include_long_term_is_noop(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(_SCOPE, max_turns=1, query="python preferences")

        assert card.relevant_facts is None
        assert card.relevant_profiles is None
        assert len(pool.cursor.all_sqls) == 1

    def test_include_long_term_without_query_is_noop(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(_SCOPE, max_turns=1, include_long_term=True)

        assert card.relevant_facts is None
        assert card.relevant_profiles is None
        assert len(pool.cursor.all_sqls) == 1

    def test_empty_string_query_is_noop(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE, max_turns=1, query="", include_long_term=True
        )

        assert card.relevant_facts is None
        assert card.relevant_profiles is None
        assert len(pool.cursor.all_sqls) == 1


# ---------------------------------------------------------------------------
# Long-term blending: basic population
# ---------------------------------------------------------------------------

class TestLongTermBlending:
    def test_populates_relevant_facts_and_profiles(self) -> None:
        pool = _FakePool([
            [_wm_row("turn-1")],                       # working.list_all
            _search_id_rows(["fact-1", "fact-2"]),     # facts.search: ids
            [_fact_row("fact-1"), _fact_row("fact-2")],  # facts.search: rows
            _search_id_rows(["prof-1"]),                # profiles.search: ids
            [_profile_row("prof-1")],                   # profiles.search: rows
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="what does the user like",
            include_long_term=True,
        )

        assert card.relevant_facts is not None
        assert [f.id for f in card.relevant_facts] == ["fact-1", "fact-2"]
        assert all(isinstance(f, SemanticFact) for f in card.relevant_facts)

        assert card.relevant_profiles is not None
        assert [p.id for p in card.relevant_profiles] == ["prof-1"]
        assert all(isinstance(p, EntityProfile) for p in card.relevant_profiles)

    def test_raw_turns_untouched_by_long_term_blending(self) -> None:
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows(["fact-1"]),
            [_fact_row("fact-1")],
            _search_id_rows([]),
            [],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE, max_turns=1, query="q", include_long_term=True
        )

        assert [t.id for t in card.turns] == ["turn-1"]
        assert card.turn_count == 1


# ---------------------------------------------------------------------------
# Per-type minimum-result backfill balancing
# ---------------------------------------------------------------------------

class TestMinResultsBackfill:
    def test_backfill_when_search_below_minimum(self) -> None:
        # facts.search() returns only 1 hit; min_results_by_type requires 2 ->
        # backfill with 1 more via facts.list_all() (most-recent).
        pool = _FakePool([
            [_wm_row("turn-1")],                     # working.list_all
            _search_id_rows(["fact-1"]),              # facts.search: ids
            [_fact_row("fact-1")],                    # facts.search: rows
            [_fact_row("fact-1"), _fact_row("fact-2")],  # facts.list_all backfill
            _search_id_rows([]),                       # profiles.search: ids (none)
            [],                                        # (no rows step needed, but keep queue aligned)
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"facts": 2},
        )

        assert card.relevant_facts is not None
        ids = [f.id for f in card.relevant_facts]
        # Relevant result first, backfilled (deduped) result appended after.
        assert ids == ["fact-1", "fact-2"]

    def test_no_backfill_when_search_meets_minimum(self) -> None:
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows(["fact-1", "fact-2"]),
            [_fact_row("fact-1"), _fact_row("fact-2")],
            _search_id_rows([]),
            [],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"facts": 2},
        )

        assert [f.id for f in card.relevant_facts] == ["fact-1", "fact-2"]
        # 4 execute() calls consumed: working.list_all (1) + facts.search's
        # two-step id/row fetch (2) + profiles.search's id step, which
        # short-circuits on an empty id list (1) — no extra backfill
        # list_all() call for facts was issued.
        assert len(pool.cursor.all_sqls) == 4

    def test_backfill_dedupes_against_relevant_results(self) -> None:
        # facts.search() returns fact-1; the recency backfill list_all() would
        # naturally return fact-1 again (it's still the most recent) plus
        # fact-2 — fact-1 must not be duplicated in the final list.
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows(["fact-1"]),
            [_fact_row("fact-1")],
            [_fact_row("fact-1"), _fact_row("fact-2")],  # backfill list_all
            _search_id_rows([]),
            [],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"facts": 2},
        )

        ids = [f.id for f in card.relevant_facts]
        assert ids == ["fact-1", "fact-2"]
        assert len(ids) == len(set(ids))

    def test_backfill_capped_when_fewer_records_exist_than_minimum(self) -> None:
        # min=3, search returns 0, only 1 record exists total for backfill.
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows([]),        # facts.search: no ids -> [] immediately
            [_fact_row("fact-only")],   # facts.list_all backfill (only 1 exists)
            _search_id_rows([]),
            [],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"facts": 3},
        )

        assert [f.id for f in card.relevant_facts] == ["fact-only"]

    def test_profiles_min_results_independent_of_facts(self) -> None:
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows(["fact-1", "fact-2"]),   # facts meets min=2, no backfill
            [_fact_row("fact-1"), _fact_row("fact-2")],
            _search_id_rows([]),                      # profiles.search: none
            [_profile_row("prof-1")],                 # profiles.list_all backfill
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"facts": 2, "profiles": 1},
        )

        assert [f.id for f in card.relevant_facts] == ["fact-1", "fact-2"]
        assert [p.id for p in card.relevant_profiles] == ["prof-1"]

    def test_alternate_key_spellings_accepted(self) -> None:
        pool = _FakePool([
            [_wm_row("turn-1")],
            _search_id_rows([]),
            [_fact_row("fact-1")],   # backfill via semantic_facts alias
            _search_id_rows([]),
            [],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        card = store.get_context_card(
            _SCOPE,
            max_turns=1,
            query="q",
            include_long_term=True,
            min_results_by_type={"semantic_facts": 1},
        )

        assert [f.id for f in card.relevant_facts] == ["fact-1"]

    def test_unrecognized_min_results_key_raises(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        with pytest.raises(ValueError, match="Unknown min_results_by_type key"):
            store.get_context_card(
                _SCOPE,
                query="q",
                include_long_term=True,
                min_results_by_type={"episodes": 1},
            )

    def test_negative_min_results_value_raises(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        with pytest.raises(ValueError, match="must be >= 0"):
            store.get_context_card(
                _SCOPE,
                query="q",
                include_long_term=True,
                min_results_by_type={"facts": -1},
            )


# ---------------------------------------------------------------------------
# Configuration / failure-mode edge cases
# ---------------------------------------------------------------------------

class TestConfigAndFailureModes:
    def test_missing_embedding_provider_raises(self) -> None:
        pool = _FakePool([[_wm_row("turn-1")]])
        store = MemoryStore(pool)  # no embedding_provider configured

        with pytest.raises(ValueError, match="embedding_provider"):
            store.get_context_card(_SCOPE, query="q", include_long_term=True)

    def test_embedding_provider_exception_degrades_to_recency_only(self, caplog) -> None:
        def _boom(text: str) -> list[float]:
            raise RuntimeError("embedding service down")

        pool = _FakePool([
            [_wm_row("turn-1")],
            [_fact_row("fact-recent")],      # facts backfill (no search attempted)
            [_profile_row("prof-recent")],   # profiles backfill (no search attempted)
        ])
        store = MemoryStore(pool, embedding_provider=_boom)

        with caplog.at_level("ERROR"):
            card = store.get_context_card(
                _SCOPE,
                max_turns=1,
                query="q",
                include_long_term=True,
                min_results_by_type={"facts": 1, "profiles": 1},
            )

        assert "embedding_provider raised" in caplog.text
        assert [f.id for f in card.relevant_facts] == ["fact-recent"]
        assert [p.id for p in card.relevant_profiles] == ["prof-recent"]

    def test_embedding_provider_exception_without_minimum_yields_empty_sections(
        self,
    ) -> None:
        def _boom(text: str) -> list[float]:
            raise RuntimeError("embedding service down")

        pool = _FakePool([
            [_wm_row("turn-1")],
        ])
        store = MemoryStore(pool, embedding_provider=_boom)

        card = store.get_context_card(
            _SCOPE, max_turns=1, query="q", include_long_term=True
        )

        # No minimum configured (defaults to 0) -> no backfill list_all() call,
        # sections come back empty (not None — long-term blending was active).
        assert card.relevant_facts == []
        assert card.relevant_profiles == []

    def test_long_term_top_k_rejects_less_than_one(self) -> None:
        store = MemoryStore(_FakePool([[]]), embedding_provider=_echo_embedder)

        with pytest.raises(ValueError, match="long_term_top_k"):
            store.get_context_card(
                _SCOPE, query="q", include_long_term=True, long_term_top_k=0
            )

    def test_search_exception_falls_back_to_recency_backfill(self, caplog) -> None:
        """If facts.search() itself raises, degrade to recency-only for that type."""
        pool = _FakePool([
            [_wm_row("turn-1")],
        ])
        store = MemoryStore(pool, embedding_provider=_echo_embedder)

        def _boom_search(*args: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("db unavailable")

        store.facts.search = _boom_search  # type: ignore[method-assign]
        store.profiles.search = _boom_search  # type: ignore[method-assign]

        # No further rows queued for list_all backfill since min defaults to 0.
        with caplog.at_level("ERROR"):
            card = store.get_context_card(
                _SCOPE, max_turns=1, query="q", include_long_term=True
            )

        assert card.relevant_facts == []
        assert card.relevant_profiles == []
        assert "search() raised" in caplog.text

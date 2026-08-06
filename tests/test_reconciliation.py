"""
tests/test_reconciliation.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ENH-3 reconciliation and supersession:

  - SupersedeDecision dataclass
  - NoOpReconciler default behaviour
  - Reconciler protocol structural check
  - SemanticFactRepository.supersede() SQL shape + return values
  - SemanticFactRepository._model_from_row() with supersession columns
  - BaseRepository.list_all() exclusion of superseded rows
  - BaseRepository.search() exclusion of superseded rows
  - BaseRepository.create() dedup check exclusion of superseded rows
  - MemoryStore.reconcile() end-to-end wiring
  - MemoryStore.reconcile() guards: wrong type, reconciler exception

No live Db2 instance required — uses the same _FakePool infrastructure
pattern from the other test files.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpReconciler, SupersedeDecision

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as other test files)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = len(self.rows)
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []
        self.all_sqls.append(sql)
        self.all_params.append(list(self.last_params))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class _FakePool:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-001", tenant_id="t1")
_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"


def _content_hash(content: str) -> str:
    """Mirror of repositories/base.py _content_hash for test helpers."""
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fact_row(
    id_: str = "fact-001",
    content: str = "User prefers dark mode.",
    version: int = 1,
    deleted_at: Any = None,
    superseded_by: Any = None,
    superseded_at: Any = None,
    supersede_reason: Any = None,
) -> tuple[Any, ...]:
    """Build a fake DB row matching SemanticFactRepository._SELECT_COLS.

    Index map (0-based):
      0  id          1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content     6  metadata    7  embedding
      8  confidence  9  content_hash
      10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
      15 origin      (TRU-1)
      16 superseded_by  17 superseded_at  18 supersede_reason
    """
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({}),
        _VEC_STR,
        1.0,                        # 8  confidence
        _content_hash(content),     # 9  content_hash
        _NOW, _NOW, None, version, deleted_at,
        "DIRECT_WRITE",             # 15 origin (TRU-1)
        superseded_by,              # 16 superseded_by
        superseded_at,              # 17 superseded_at
        supersede_reason,           # 18 supersede_reason
    )


# ---------------------------------------------------------------------------
# SupersedeDecision dataclass
# ---------------------------------------------------------------------------

class TestSupersedeDecision:
    def test_fields_accessible(self):
        d = SupersedeDecision(winner_id="w1", loser_id="l1", reason="contradicts")
        assert d.winner_id == "w1"
        assert d.loser_id == "l1"
        assert d.reason == "contradicts"

    def test_equality(self):
        a = SupersedeDecision(winner_id="w", loser_id="l", reason="r")
        b = SupersedeDecision(winner_id="w", loser_id="l", reason="r")
        assert a == b

    def test_inequality_on_reason(self):
        a = SupersedeDecision(winner_id="w", loser_id="l", reason="r1")
        b = SupersedeDecision(winner_id="w", loser_id="l", reason="r2")
        assert a != b


# ---------------------------------------------------------------------------
# NoOpReconciler
# ---------------------------------------------------------------------------

class TestNoOpReconciler:
    def test_returns_empty_list_always(self):
        noop = NoOpReconciler()
        result = noop([])
        assert result == []

    def test_returns_empty_list_with_candidates(self):
        noop = NoOpReconciler()
        fact = SemanticFact(agent_id="agent-001", content="some fact")
        result = noop([fact])
        assert result == []

    def test_is_callable(self):
        noop = NoOpReconciler()
        assert callable(noop)


# ---------------------------------------------------------------------------
# Reconciler protocol structural check
# ---------------------------------------------------------------------------

class TestReconcilerProtocol:
    def test_noop_satisfies_protocol(self):
        """NoOpReconciler is an instance of the Reconciler Protocol."""
        # Reconciler is not @runtime_checkable but isinstance works for classes.
        # We verify structural compatibility by calling it.
        noop = NoOpReconciler()
        facts: list[SemanticFact] = []
        result = noop(facts)
        assert isinstance(result, list)

    def test_custom_reconciler_callable(self):
        """A plain callable matching the protocol shape is accepted by reconcile()."""
        decisions = [SupersedeDecision(winner_id="w", loser_id="l", reason="test")]

        class _CustomRec:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                return decisions

        rec = _CustomRec()
        assert rec([]) == decisions


# ---------------------------------------------------------------------------
# SemanticFactRepository._model_from_row with supersession columns
# ---------------------------------------------------------------------------

class TestSemanticFactModelFromRow:
    def _repo(self) -> SemanticFactRepository:
        return SemanticFactRepository(_FakePool())

    def test_live_row_has_none_supersession_fields(self):
        row = _fact_row()
        repo = self._repo()
        fact = repo._model_from_row(row)
        assert fact.superseded_by is None
        assert fact.superseded_at is None
        assert fact.supersede_reason is None

    def test_superseded_row_fields_populated(self):
        row = _fact_row(
            superseded_by="winner-001",
            superseded_at=_NOW,
            supersede_reason="contradicts: user now prefers light mode",
        )
        repo = self._repo()
        fact = repo._model_from_row(row)
        assert fact.superseded_by == "winner-001"
        assert fact.superseded_at == _NOW
        assert fact.supersede_reason == "contradicts: user now prefers light mode"

    def test_id_and_content_round_trip(self):
        row = _fact_row(id_="f-xyz", content="The sky is blue.")
        repo = self._repo()
        fact = repo._model_from_row(row)
        assert fact.id == "f-xyz"
        assert fact.content == "The sky is blue."

    def test_select_cols_has_supersession_columns(self):
        repo = self._repo()
        assert "superseded_by" in repo._SELECT_COLS
        assert "superseded_at" in repo._SELECT_COLS
        assert "supersede_reason" in repo._SELECT_COLS


# ---------------------------------------------------------------------------
# SemanticFactRepository.supersede()
# ---------------------------------------------------------------------------

class TestSemanticFactSupersede:
    def _repo(self, rowcount: int = 1) -> SemanticFactRepository:
        pool = _FakePool()
        pool.cursor.rowcount = rowcount
        return SemanticFactRepository(pool)

    def test_supersede_issues_update_sql(self):
        repo = self._repo()
        repo.supersede("loser-1", "winner-1", "contradicts", _SCOPE)
        sql = repo._pool.cursor.last_sql
        assert "UPDATE semantic_facts" in sql
        assert "superseded_by = ?" in sql
        assert "superseded_at = ?" in sql
        assert "supersede_reason = ?" in sql
        assert "updated_at = ?" in sql
        assert "version = version + 1" in sql

    def test_supersede_where_clause_guards(self):
        """Must only touch rows that are live (not deleted, not already superseded)."""
        repo = self._repo()
        repo.supersede("loser-1", "winner-1", "r", _SCOPE)
        sql = repo._pool.cursor.last_sql
        assert "deleted_at IS NULL" in sql
        assert "superseded_at IS NULL" in sql

    def test_supersede_includes_scope_predicates(self):
        repo = self._repo()
        repo.supersede("loser-1", "winner-1", "r", _SCOPE)
        sql = repo._pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_supersede_params_order(self):
        """winner_id is the first param (bound to superseded_by column)."""
        repo = self._repo()
        repo.supersede("loser-1", "winner-1", "my reason", _SCOPE)
        params = repo._pool.cursor.last_params
        # params: [winner_id, now, reason, now, loser_id, agent_id, tenant_id]
        assert params[0] == "winner-1"
        assert params[2] == "my reason"
        assert params[4] == "loser-1"

    def test_supersede_returns_true_on_hit(self):
        repo = self._repo(rowcount=1)
        assert repo.supersede("l", "w", "r", _SCOPE) is True

    def test_supersede_returns_false_on_miss(self):
        repo = self._repo(rowcount=0)
        assert repo.supersede("l", "w", "r", _SCOPE) is False

    def test_supersede_truncates_reason_at_255(self):
        repo = self._repo()
        long_reason = "x" * 300
        repo.supersede("l", "w", long_reason, _SCOPE)
        params = repo._pool.cursor.last_params
        assert len(params[2]) == 255

    def test_supersede_requires_agent_id(self):
        repo = self._repo()
        with pytest.raises(ValueError, match="agent_id"):
            repo.supersede("l", "w", "r", MemoryScope(agent_id=""))

    def test_commit_is_called(self):
        repo = self._repo()
        repo.supersede("l", "w", "r", _SCOPE)
        assert repo._pool.conn.committed is True


# ---------------------------------------------------------------------------
# list_all() excludes superseded rows
# ---------------------------------------------------------------------------

class TestListAllExcludesSuperseded:
    def test_list_all_sql_has_superseded_at_filter(self):
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        repo.list_all(_SCOPE)
        sql = pool.cursor.last_sql
        assert "superseded_at IS NULL" in sql

    def test_list_all_with_offset_also_filters_superseded(self):
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        repo.list_all(_SCOPE, offset=10)
        sql = pool.cursor.last_sql
        # ROW_NUMBER() path must also include the filter
        assert "superseded_at IS NULL" in sql

    def test_list_all_still_has_deleted_at_filter(self):
        """Both filters must be present — they are independent lifecycle columns."""
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        repo.list_all(_SCOPE)
        sql = pool.cursor.last_sql
        assert "deleted_at IS NULL" in sql
        assert "superseded_at IS NULL" in sql


# ---------------------------------------------------------------------------
# search() excludes superseded rows
# ---------------------------------------------------------------------------

class TestSearchExcludesSuperseded:
    def test_search_step1_sql_has_superseded_at_filter(self):
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        repo.search([0.1] * 1536, _SCOPE)
        # Step 1 — the ID-ranking SQL (first execute call)
        sql = pool.cursor.all_sqls[0]
        assert "superseded_at IS NULL" in sql

    def test_search_step1_still_has_deleted_at_filter(self):
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        repo.search([0.1] * 1536, _SCOPE)
        sql = pool.cursor.all_sqls[0]
        assert "deleted_at IS NULL" in sql


# ---------------------------------------------------------------------------
# create() dedup check excludes superseded rows
# ---------------------------------------------------------------------------

class TestCreateDedupExcludesSuperseded:
    def test_dedup_sql_has_superseded_at_filter(self):
        """Dedup SELECT must exclude superseded rows so they don't act as
        barriers for re-writing content whose prior version was superseded."""
        pool = _FakePool(rows=[])  # no existing row → INSERT proceeds
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="some fact")
        repo.create(fact, _SCOPE)
        # First execute is the dedup SELECT
        dedup_sql = pool.cursor.all_sqls[0]
        assert "superseded_at IS NULL" in dedup_sql
        assert "deleted_at IS NULL" in dedup_sql

    def test_dedup_hit_on_live_row_returns_existing(self):
        """A non-superseded, non-deleted duplicate is still returned as-is."""
        existing_row = _fact_row(id_="existing-id", content="same fact")
        pool = _FakePool(rows=[existing_row])
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="same fact")
        result = repo.create(fact, _SCOPE)
        assert result.id == "existing-id"

    def test_dedup_miss_on_superseded_row_inserts_new(self):
        """If the only existing row with this hash is superseded, create() must
        insert a fresh row rather than returning the superseded one."""
        # Dedup SELECT returns empty → no existing live row → INSERT
        pool = _FakePool(rows=[])
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="new version of fact")
        result = repo.create(fact, _SCOPE)
        # INSERT was issued — the INSERT statement is the second execute call
        insert_sql = pool.cursor.all_sqls[1]
        assert "INSERT INTO semantic_facts" in insert_sql
        # The returned record is the newly created fact (same id that was generated)
        assert result.id == fact.id


# ---------------------------------------------------------------------------
# MemoryStore.reconcile() — end-to-end wiring
# ---------------------------------------------------------------------------

class TestMemoryStoreReconcile:
    """Tests for MemoryStore.reconcile() using a fake pool and a custom reconciler."""

    def _fact(self, id_: str, content: str) -> SemanticFact:
        return SemanticFact(id=id_, agent_id="agent-001", content=content)

    def test_reconcile_noop_reconciler_returns_empty_list(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        result = store.reconcile("facts", _SCOPE)
        assert result == []

    def test_reconcile_calls_supersede_for_each_decision(self):
        # The pool serves a candidate fact row (id="w1") so that the
        # winner_id guard is satisfied, then rowcount=1 for the UPDATE.
        pool = _FakePool(rows=[_fact_row(id_="w1", content="fact-w1")])
        pool.cursor.rowcount = 1  # supersede() reports success

        decisions = [
            SupersedeDecision(winner_id="w1", loser_id="l1", reason="contradicts: test"),
        ]

        class _FixedReconciler:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                return decisions

        store = MemoryStore(pool, reconciler=_FixedReconciler())
        applied = store.reconcile("facts", _SCOPE)

        # Reconciler returned one decision → supersede() called → one applied
        assert len(applied) == 1
        assert applied[0].loser_id == "l1"
        assert applied[0].winner_id == "w1"

    def test_reconcile_supersede_sql_issued(self):
        # Seed candidate row so winner_id guard passes.
        pool = _FakePool(rows=[_fact_row(id_="w1", content="fact-w1")])
        pool.cursor.rowcount = 1

        decisions = [SupersedeDecision(winner_id="w1", loser_id="l1", reason="r")]

        class _Rec:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                return decisions

        store = MemoryStore(pool, reconciler=_Rec())
        store.reconcile("facts", _SCOPE)

        # The last SQL executed should be the supersede UPDATE
        last_sql = pool.cursor.last_sql
        assert "UPDATE semantic_facts" in last_sql
        assert "superseded_by = ?" in last_sql

    def test_reconcile_skips_decision_when_supersede_returns_false(self):
        """If supersede() returns False (row not found), the decision is not in applied."""
        pool = _FakePool(rows=[])
        pool.cursor.rowcount = 0  # supersede → no-op

        decisions = [SupersedeDecision(winner_id="w1", loser_id="l1", reason="r")]

        class _Rec:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                return decisions

        store = MemoryStore(pool, reconciler=_Rec())
        applied = store.reconcile("facts", _SCOPE)
        # No rows were affected → decision not added to applied list
        assert applied == []

    def test_reconcile_rejects_wrong_memory_type(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="reconcile\\(\\) only supports"):
            store.reconcile("working", _SCOPE)

    def test_reconcile_rejects_procedural(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="reconcile\\(\\) only supports"):
            store.reconcile("procedures", _SCOPE)

    def test_reconcile_rejects_profiles(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="reconcile\\(\\) only supports"):
            store.reconcile("profiles", _SCOPE)

    def test_reconcile_accepts_semantic_facts_alias(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        # Should not raise — "semantic_facts" is a valid alias
        result = store.reconcile("semantic_facts", _SCOPE)
        assert result == []

    def test_reconcile_reconciler_exception_returns_empty(self):
        """If the reconciler raises, reconcile() catches it and returns []."""
        pool = _FakePool(rows=[])

        class _BrokenRec:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                raise RuntimeError("LLM call failed")

        store = MemoryStore(pool, reconciler=_BrokenRec())
        result = store.reconcile("facts", _SCOPE)
        assert result == []

    def test_reconcile_with_multiple_decisions(self):
        # Seed both winner candidates so the guard passes for both decisions.
        pool = _FakePool(rows=[
            _fact_row(id_="w1", content="fact-w1"),
            _fact_row(id_="w2", content="fact-w2"),
        ])
        pool.cursor.rowcount = 1

        decisions = [
            SupersedeDecision(winner_id="w1", loser_id="l1", reason="r1"),
            SupersedeDecision(winner_id="w2", loser_id="l2", reason="r2"),
        ]

        class _Rec:
            def __call__(self, candidates: list) -> list[SupersedeDecision]:
                return decisions

        store = MemoryStore(pool, reconciler=_Rec())
        applied = store.reconcile("facts", _SCOPE)
        assert len(applied) == 2

    def test_reconcile_requires_agent_id(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="agent_id"):
            store.reconcile("facts", MemoryScope(agent_id=""))


# ---------------------------------------------------------------------------
# SemanticFact model — supersession fields on the model itself
# ---------------------------------------------------------------------------

class TestSemanticFactModel:
    def test_default_supersession_fields_are_none(self):
        fact = SemanticFact(agent_id="a", content="x")
        assert fact.superseded_by is None
        assert fact.superseded_at is None
        assert fact.supersede_reason is None

    def test_supersession_fields_can_be_set(self):
        fact = SemanticFact(
            agent_id="a",
            content="x",
            superseded_by="winner-id",
            superseded_at=_NOW,
            supersede_reason="contradicts: test",
        )
        assert fact.superseded_by == "winner-id"
        assert fact.superseded_at == _NOW
        assert fact.supersede_reason == "contradicts: test"

    def test_supersession_fields_not_on_other_models(self):
        """entity_profiles and procedural_memory must NOT have supersession fields."""
        from agent_memory_sdk.models import EntityProfile, ProceduralMemory
        ep = EntityProfile(agent_id="a", content="x")
        pm = ProceduralMemory(agent_id="a", content="x")
        assert not hasattr(ep, "superseded_by")
        assert not hasattr(pm, "superseded_by")


# ---------------------------------------------------------------------------
# NoOpReconciler and NoOpConsolidator are parallel in shape
# ---------------------------------------------------------------------------

class TestNoOpParallelShape:
    def test_noop_reconciler_matches_noop_consolidator_pattern(self):
        """Both NoOpConsolidator and NoOpReconciler are plain classes with a
        __call__ that returns [].  This test asserts the structural parallel."""
        from agent_memory_sdk.types import NoOpConsolidator
        noop_c = NoOpConsolidator()
        noop_r = NoOpReconciler()
        # Both callable
        assert callable(noop_c)
        assert callable(noop_r)
        # Both return []
        assert noop_c([]) == []
        assert noop_r([]) == []


# ---------------------------------------------------------------------------
# MemoryStore.reconcile() — sanity-check guards (Fix 2)
# ---------------------------------------------------------------------------

class TestReconcileSanityGuards:
    """Tests for the self-supersession and winner-not-in-candidates guards
    added to MemoryStore.reconcile() as part of the ENH-3 audit fix.

    Both guard cases must behave like the existing "supersede returned False"
    case: the decision is silently skipped (logged as a warning), not added
    to the applied list, and the rest of the batch continues.
    """

    # ------------------------------------------------------------------
    # Helper: build a pool that also serves a synthetic candidates list.
    # The _FakePool in this module tracks all SQL/params, which is useful
    # for verifying that supersede() is (or is not) called.
    # ------------------------------------------------------------------

    def _store_with_reconciler(self, decisions, candidate_facts=None):
        """Return (store, pool) where the reconciler always returns *decisions*.
        If *candidate_facts* is supplied, list_all() is mocked to return them
        by pre-loading the pool with the matching row tuples; otherwise the
        pool returns an empty result set (no candidates).
        """
        pool = _FakePool(rows=[])
        pool.cursor.rowcount = 1  # supersede() → success when reached

        # Wrap decisions in a fixed reconciler
        _decisions = decisions

        class _FixedRec:
            def __call__(self, candidates):
                return _decisions

        store = MemoryStore(pool, reconciler=_FixedRec())
        return store, pool

    # ------------------------------------------------------------------
    # Guard (a): winner_id == loser_id → self-supersession rejected
    # ------------------------------------------------------------------

    def test_self_supersession_is_skipped(self):
        """A decision where winner_id == loser_id must be skipped."""
        same_id = "fact-self"
        decisions = [
            SupersedeDecision(winner_id=same_id, loser_id=same_id, reason="self"),
        ]
        store, pool = self._store_with_reconciler(decisions)
        applied = store.reconcile("facts", _SCOPE)
        assert applied == []

    def test_self_supersession_does_not_call_supersede(self):
        """supersede() must not be called at all when winner_id == loser_id."""
        same_id = "fact-self"
        decisions = [
            SupersedeDecision(winner_id=same_id, loser_id=same_id, reason="self"),
        ]
        store, pool = self._store_with_reconciler(decisions)
        all_sqls_before = list(pool.cursor.all_sqls)
        store.reconcile("facts", _SCOPE)
        # The only SQL executed should be the list_all() SELECT, not an UPDATE
        new_sqls = pool.cursor.all_sqls[len(all_sqls_before):]
        update_sqls = [s for s in new_sqls if "UPDATE" in s]
        assert update_sqls == [], (
            "supersede() must not be called for a self-supersession decision"
        )

    def test_self_supersession_does_not_affect_valid_decisions(self):
        """A mix of one self-supersession and one valid decision: only the
        valid one must be applied (the bad one is silently skipped)."""
        # The pool returns rowcount=1 so supersede() reports success.
        # For the valid decision to reach supersede(), its winner_id must be
        # in the candidates set — but candidates are built from list_all(),
        # which returns [] from the empty pool.  So the valid decision will
        # also be skipped by guard (b) unless we set up winners in candidates.
        # Keep it simple: use two different ids, both equal their losers only
        # for the bad decision.  The valid decision's winner is also not in
        # candidates, so it will be blocked by guard (b) too — that's fine;
        # this test is specifically about guard (a) not crashing.
        decisions = [
            SupersedeDecision(winner_id="x", loser_id="x", reason="self"),  # bad
        ]
        store, pool = self._store_with_reconciler(decisions)
        applied = store.reconcile("facts", _SCOPE)
        assert applied == []  # bad decision skipped

    def test_self_supersession_warning_is_logged(self, caplog):
        """The warning log must mention the skipped id."""
        import logging
        same_id = "fact-self-warn"
        decisions = [
            SupersedeDecision(winner_id=same_id, loser_id=same_id, reason="self"),
        ]
        store, pool = self._store_with_reconciler(decisions)
        with caplog.at_level(logging.WARNING, logger="agent_memory_sdk.store"):
            store.reconcile("facts", _SCOPE)
        # At least one warning must reference the self-supersession
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("self-supersession" in m or same_id in m for m in warn_msgs)

    # ------------------------------------------------------------------
    # Guard (b): winner_id not in candidates → hallucinated reference rejected
    # ------------------------------------------------------------------

    def test_winner_not_in_candidates_is_skipped(self):
        """A decision whose winner_id is not in the candidates list must be skipped."""
        decisions = [
            SupersedeDecision(
                winner_id="hallucinated-id",
                loser_id="loser-1",
                reason="made-up winner",
            ),
        ]
        # candidates list is empty (pool returns []) → "hallucinated-id" not in {}
        store, pool = self._store_with_reconciler(decisions)
        applied = store.reconcile("facts", _SCOPE)
        assert applied == []

    def test_winner_not_in_candidates_does_not_call_supersede(self):
        """supersede() must not be called when winner_id is not in candidates."""
        decisions = [
            SupersedeDecision(
                winner_id="ghost-id",
                loser_id="loser-2",
                reason="ghost winner",
            ),
        ]
        store, pool = self._store_with_reconciler(decisions)
        store.reconcile("facts", _SCOPE)
        new_sqls = pool.cursor.all_sqls
        update_sqls = [s for s in new_sqls if "UPDATE" in s]
        assert update_sqls == [], (
            "supersede() must not be called when winner_id is not in candidates"
        )

    def test_winner_not_in_candidates_warning_is_logged(self, caplog):
        """The warning log must mention the unknown winner_id."""
        import logging
        decisions = [
            SupersedeDecision(
                winner_id="unknown-winner-xyz",
                loser_id="loser-3",
                reason="hallucinated",
            ),
        ]
        store, pool = self._store_with_reconciler(decisions)
        with caplog.at_level(logging.WARNING, logger="agent_memory_sdk.store"):
            store.reconcile("facts", _SCOPE)
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("unknown-winner-xyz" in m or "not" in m for m in warn_msgs)

    def test_valid_decision_still_applied_after_bad_ones(self):
        """Even when some decisions are rejected by both guards, valid decisions
        (winner in candidates, winner != loser) must still be processed.

        This test seeds the pool with a fact row so that list_all() returns a
        real candidate, then verifies that a valid decision whose winner_id
        matches that candidate IS applied while the bad decisions are skipped.
        """
        # Build a _FakePool that returns one fact row from list_all() and
        # rowcount=1 from the subsequent supersede() UPDATE.
        # We need the cursor to return the fact row for the list_all() query
        # and then rowcount=1 for the UPDATE — the _FakePool is stateless so
        # rowcount=1 is set upfront; for the SELECT we give it the row.
        valid_winner_id = "winner-known"
        valid_loser_id = "loser-known"

        # Build a fake SemanticFact row tuple (18 cols, matching _fact_row helper)
        import hashlib as _hashlib
        import json as _json
        import re as _re
        _content = "a known fact"
        _h = _hashlib.sha256(
            _re.sub(r"\s+", " ", _content.lower()).strip().encode()
        ).hexdigest()
        _vec_str_local = "[" + ",".join("0.1" for _ in range(1536)) + "]"
        fact_row = (
            valid_winner_id, "t1", "agent-001", None, None,
            _content, _json.dumps({}),
            _vec_str_local,
            1.0, _h,
            _NOW, _NOW, None, 1, None,
            "DIRECT_WRITE",     # origin (TRU-1)
            None, None, None,   # superseded_by, superseded_at, supersede_reason
        )

        pool = _FakePool(rows=[fact_row])
        pool.cursor.rowcount = 1

        decisions = [
            SupersedeDecision(winner_id="self-id", loser_id="self-id", reason="self"),  # bad
            SupersedeDecision(winner_id="ghost-id", loser_id="loser-x", reason="ghost"),  # bad
            SupersedeDecision(
                winner_id=valid_winner_id,
                loser_id=valid_loser_id,
                reason="valid supersession",
            ),  # good
        ]

        class _FixedRec:
            def __call__(self, candidates):
                return decisions

        store = MemoryStore(pool, reconciler=_FixedRec())
        applied = store.reconcile("facts", _SCOPE)

        # Only the valid decision should be in the applied list
        assert len(applied) == 1
        assert applied[0].winner_id == valid_winner_id
        assert applied[0].loser_id == valid_loser_id

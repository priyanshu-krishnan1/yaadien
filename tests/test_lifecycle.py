"""
tests/test_lifecycle.py
~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for Step-4 lifecycle features:
  - forget() / soft_delete() tombstoning
  - update() with optimistic concurrency
  - purge_expired() maintenance hard-delete
  - Consolidator protocol: NoOpConsolidator + custom consolidator
  - MemoryStore.remember() consolidation wiring
  - MemoryStore.forget() facade
  - MemoryStore.purge_expired() facade

No live Db2 instance required — uses the same _FakePool infrastructure
from test_repositories.py (inlined here for test isolation).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.exceptions import StaleWriteError
from agent_memory_sdk.models import (
    EpisodicMemory,
    MemoryScope,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ContextCard, NoOpConsolidator, NoOpSummarizer

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as test_repositories.py)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = len(self.rows)
        # Track all execute calls in order (for multi-execute tests)
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []
        self.all_sqls.append(sql)
        self.all_params.append(self.last_params)

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
# Shared fixtures
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-001", tenant_id="t1")
_NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
_VEC = [0.1] * 1536


def _row(
    id_: str = "row-uuid",
    content: str = "hello",
    version: int = 1,
    deleted_at: Any = None,
) -> tuple[Any, ...]:
    """Minimal fake DB row matching WorkingMemoryRepository._SELECT_COLS order.

    Index map (0-based):
      0  id           1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content      6  metadata    7  embedding
      8  confidence   9  content_hash
      10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
      15 consolidated_at  (ENH-4 — None = not yet consolidated)
    """
    import hashlib
    import re as _re
    vec_str = "[" + ",".join("0.1" for _ in range(1536)) + "]"
    h = hashlib.sha256(_re.sub(r"\s+", " ", content.lower()).strip().encode()).hexdigest()
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({}),
        vec_str,
        1.0,               # 8 — confidence (ENH-1)
        h,                 # 9 — content_hash (ENH-2)
        _NOW, _NOW, None, version, deleted_at,
        None,              # 15 — consolidated_at (ENH-4)
    )


# ---------------------------------------------------------------------------
# forget() — repository level
# ---------------------------------------------------------------------------

class TestForget:
    def test_forget_issues_update_with_deleted_at(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        result = repo.forget("row-1", _SCOPE)
        assert result is True
        sql = pool.cursor.last_sql
        assert "UPDATE working_memory" in sql
        assert "deleted_at = ?" in sql
        assert "version = version + 1" in sql

    def test_forget_returns_false_when_not_found(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        result = repo.forget("nonexistent", _SCOPE)
        assert result is False

    def test_forget_scope_predicate_in_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        repo.forget("row-1", _SCOPE)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_forget_requires_agent_id(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        with pytest.raises(ValueError, match="agent_id"):
            repo.forget("x", MemoryScope(agent_id=""))

    def test_soft_delete_is_alias_for_forget(self):
        """soft_delete() must delegate to forget() — same SQL."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        result = repo.soft_delete("row-1", _SCOPE)
        assert result is True
        sql = pool.cursor.last_sql
        assert "deleted_at = ?" in sql

    def test_forget_filters_already_deleted(self):
        """SQL must include AND deleted_at IS NULL so it's idempotent."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.forget("row-1", _SCOPE)
        sql = pool.cursor.last_sql
        assert "deleted_at IS NULL" in sql


# ---------------------------------------------------------------------------
# update() — optimistic concurrency
# ---------------------------------------------------------------------------

class TestUpdate:
    def _record(self, version: int = 1) -> WorkingMemory:
        return WorkingMemory(
            id="row-001",
            agent_id="agent-001",
            tenant_id="t1",
            content="updated content",
            version=version,
        )

    def test_update_success_issues_update_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=1)
        result = repo.update(record, _SCOPE)
        sql = pool.cursor.last_sql
        assert "UPDATE working_memory" in sql
        assert "content = ?" in sql
        assert "metadata = ?" in sql
        assert "CAST(" in sql
        assert "AS VECTOR(" in sql
        assert "version = ?" in sql
        assert result is record

    def test_update_success_increments_version(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=3)
        repo.update(record, _SCOPE)
        assert record.version == 4

    def test_update_success_refreshes_updated_at(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=1)
        old_updated_at = record.updated_at
        repo.update(record, _SCOPE)
        assert record.updated_at is not None
        assert record.updated_at != old_updated_at

    def test_update_stale_write_raises(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0   # 0 rows affected → stale
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=1)
        with pytest.raises(StaleWriteError, match="version=1"):
            repo.update(record, _SCOPE)

    def test_update_requires_agent_id(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        record = self._record()
        with pytest.raises(ValueError, match="agent_id"):
            repo.update(record, MemoryScope(agent_id=""))

    def test_update_sql_includes_version_check(self):
        """The WHERE clause must include AND version = ? for optimistic lock."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=2)
        repo.update(record, _SCOPE)
        sql = pool.cursor.last_sql
        # Both SET version = ? (new) and WHERE version = ? (old) must appear
        assert sql.count("version") >= 2

    def test_update_scope_predicate_in_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=1)
        repo.update(record, _SCOPE)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_update_version_param_value(self):
        """The optimistic lock value sent as param must be record.version (old)."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        record = self._record(version=5)
        repo.update(record, _SCOPE)
        # The last param in the list is the old version (optimistic lock)
        assert 5 in pool.cursor.last_params


# ---------------------------------------------------------------------------
# purge_expired() — hard-delete
# ---------------------------------------------------------------------------

class TestPurgeExpired:
    def test_purge_issues_delete_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 3
        repo = WorkingMemoryRepository(pool)
        count = repo.purge_expired(_SCOPE)
        sql = pool.cursor.last_sql
        assert "DELETE FROM working_memory" in sql
        assert "deleted_at IS NOT NULL" in sql
        assert count == 3

    def test_purge_scope_predicate_in_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.purge_expired(_SCOPE)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_purge_requires_agent_id(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        with pytest.raises(ValueError, match="agent_id"):
            repo.purge_expired(MemoryScope(agent_id=""))

    def test_purge_returns_zero_when_nothing_to_delete(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        result = repo.purge_expired(_SCOPE)
        assert result == 0

    def test_purge_never_deletes_live_rows(self):
        """SQL must only target deleted_at IS NOT NULL rows."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.purge_expired(_SCOPE)
        sql = pool.cursor.last_sql
        # Must NOT have 'deleted_at IS NULL' (that would kill live rows)
        assert "deleted_at IS NULL" not in sql
        assert "deleted_at IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# NoOpConsolidator
# ---------------------------------------------------------------------------

class TestNoOpConsolidator:
    def test_returns_empty_list(self):
        c = NoOpConsolidator()
        wm = WorkingMemory(agent_id="agent-001", content="hello")
        result = c([wm])
        assert result == []

    def test_empty_input(self):
        c = NoOpConsolidator()
        assert c([]) == []


# ---------------------------------------------------------------------------
# MemoryStore.remember() — consolidation wiring
# ---------------------------------------------------------------------------

class TestMemoryStoreRemember:
    def test_remember_working_calls_consolidator(self):
        """Consolidator must be called once after a working memory write."""
        calls: list[list] = []

        class SpyConsolidator:
            def __call__(self, raw_memories):
                calls.append(raw_memories)
                return []

        pool = _FakePool()
        store = MemoryStore(pool, consolidator=SpyConsolidator())
        scope = MemoryScope(agent_id="agent-001")
        record = WorkingMemory(agent_id="agent-001", content="turn 1")
        store.remember(record, scope)
        assert len(calls) == 1
        assert calls[0][0].content == "turn 1"

    def test_remember_episodic_calls_consolidator(self):
        calls: list[list] = []

        class SpyConsolidator:
            def __call__(self, raw_memories):
                calls.append(raw_memories)
                return []

        pool = _FakePool()
        store = MemoryStore(pool, consolidator=SpyConsolidator())
        scope = MemoryScope(agent_id="agent-001")
        store.remember(EpisodicMemory(agent_id="agent-001", content="episode"), scope)
        assert len(calls) == 1

    def test_remember_facts_does_not_call_consolidator(self):
        """Consolidator must NOT fire for semantic/profiles/procedural writes."""
        calls: list[list] = []

        class SpyConsolidator:
            def __call__(self, raw_memories):
                calls.append(raw_memories)
                return []

        pool = _FakePool()
        store = MemoryStore(pool, consolidator=SpyConsolidator())
        scope = MemoryScope(agent_id="agent-001")
        store.remember(SemanticFact(agent_id="agent-001", content="fact"), scope)
        assert calls == []

    def test_remember_persists_derived_memories(self):
        """Derived records returned by consolidator must be persisted."""
        class CaptureSqlPool:
            """Pool that records every executed SQL statement."""
            def __init__(self):
                self.cursor = _FakeCursor()
                self.conn = _FakeConn(self.cursor)

            @contextmanager
            def get_connection(self):
                yield self.conn

        class FactConsolidator:
            def __call__(self, raw_memories):
                return [
                    SemanticFact(
                        agent_id="agent-001",
                        content="derived fact",
                    )
                ]

        pool = CaptureSqlPool()
        store = MemoryStore(pool, consolidator=FactConsolidator())
        scope = MemoryScope(agent_id="agent-001")
        store.remember(WorkingMemory(agent_id="agent-001", content="turn"), scope)

        # Both working_memory INSERT and semantic_facts INSERT must have run.
        all_sqls = pool.cursor.all_sqls
        assert any("INSERT INTO working_memory" in s for s in all_sqls)
        assert any("INSERT INTO semantic_facts" in s for s in all_sqls)

    def test_remember_noop_consolidator_by_default(self):
        """Default store must use NoOpConsolidator (no derived inserts)."""
        pool = _FakePool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")
        store.remember(WorkingMemory(agent_id="agent-001", content="turn"), scope)
        # Only one INSERT (working_memory) — no derived INSERT
        all_sqls = pool.cursor.all_sqls
        assert sum(1 for s in all_sqls if "INSERT" in s) == 1

    def test_remember_consolidator_exception_does_not_propagate(self):
        """A crash in the consolidator must not fail the remember() call."""
        class BrokenConsolidator:
            def __call__(self, raw_memories):
                raise RuntimeError("LLM call failed")

        pool = _FakePool()
        store = MemoryStore(pool, consolidator=BrokenConsolidator())
        scope = MemoryScope(agent_id="agent-001")
        # Must not raise
        result = store.remember(
            WorkingMemory(agent_id="agent-001", content="turn"), scope
        )
        assert result.content == "turn"

    def test_remember_unknown_type_raises_typeerror(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")

        class UnknownMemory:
            pass

        with pytest.raises(TypeError, match="Unknown memory type"):
            store.remember(UnknownMemory(), scope)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MemoryStore.forget() — facade
# ---------------------------------------------------------------------------

class TestMemoryStoreForget:
    def test_forget_working(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        result = store.forget("row-1", "working", _SCOPE)
        assert result is True
        assert "UPDATE working_memory" in pool.cursor.last_sql

    def test_forget_episodic(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        result = store.forget("row-1", "episodic", _SCOPE)
        assert result is True
        assert "UPDATE episodic_memory" in pool.cursor.last_sql

    def test_forget_facts_alias(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        result = store.forget("row-1", "semantic_facts", _SCOPE)
        assert result is True
        assert "UPDATE semantic_facts" in pool.cursor.last_sql

    def test_forget_unknown_type_raises(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="Unknown memory_type"):
            store.forget("row-1", "unknown_type", _SCOPE)

    def test_forget_returns_false_when_not_found(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        result = store.forget("nonexistent", "working", _SCOPE)
        assert result is False


# ---------------------------------------------------------------------------
# MemoryStore.purge_expired() — facade
# ---------------------------------------------------------------------------

class TestMemoryStorePurgeExpired:
    def test_purge_returns_dict_of_counts(self):
        pool = _FakePool()
        pool.cursor.rowcount = 2
        store = MemoryStore(pool)
        results = store.purge_expired(_SCOPE)
        assert isinstance(results, dict)
        assert set(results.keys()) == {
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
        }

    def test_purge_issues_delete_on_all_tables(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.purge_expired(_SCOPE)
        tables = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(tables) == 5
        table_names = " ".join(tables)
        for t in (
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
        ):
            assert t in table_names

    def test_purge_total_is_sum_of_all(self):
        pool = _FakePool()
        pool.cursor.rowcount = 3   # each call returns 3
        store = MemoryStore(pool)
        results = store.purge_expired(_SCOPE)
        assert sum(results.values()) == 15   # 5 tables × 3


# ---------------------------------------------------------------------------
# get_context_card() — structured recent-turns view + optional summarizer
# ---------------------------------------------------------------------------

class TestContextCard:
    def test_get_context_card_returns_chronological_turns(self):
        pool = _FakePool(rows=[
            _row(id_="turn-3", content="third"),
            _row(id_="turn-2", content="second"),
            _row(id_="turn-1", content="first"),
        ])
        store = MemoryStore(pool)

        card = store.get_context_card(_SCOPE, max_turns=3)

        assert isinstance(card, ContextCard)
        assert [turn.id for turn in card.turns] == ["turn-1", "turn-2", "turn-3"]
        assert card.turn_count == 3
        assert card.latest_at == _NOW
        assert card.summary is None

    def test_get_context_card_uses_max_turns_as_list_limit(self):
        pool = _FakePool(rows=[_row(id_="turn-2"), _row(id_="turn-1")])
        store = MemoryStore(pool)

        store.get_context_card(_SCOPE, max_turns=2)

        assert "FETCH FIRST ? ROWS ONLY" in pool.cursor.last_sql
        assert pool.cursor.last_params[-1] == 2

    def test_get_context_card_empty_scope_returns_empty_card(self):
        pool = _FakePool(rows=[])
        store = MemoryStore(pool)

        card = store.get_context_card(_SCOPE)

        assert card.turns == []
        assert card.turn_count == 0
        assert card.latest_at is None
        assert card.summary is None

    def test_get_context_card_rejects_max_turns_less_than_one(self):
        store = MemoryStore(_FakePool())

        with pytest.raises(ValueError, match="max_turns"):
            store.get_context_card(_SCOPE, max_turns=0)

    def test_get_context_card_calls_summarizer_with_chronological_turns(self):
        pool = _FakePool(rows=[
            _row(id_="turn-2", content="second"),
            _row(id_="turn-1", content="first"),
        ])
        calls: list[list[str]] = []

        class _Summarizer:
            def __call__(self, turns: list[WorkingMemory]) -> str:
                calls.append([turn.id for turn in turns])
                return "summary text"

        store = MemoryStore(pool, summarizer=_Summarizer())

        card = store.get_context_card(_SCOPE, max_turns=2)

        assert calls == [["turn-1", "turn-2"]]
        assert card.summary == "summary text"

    def test_get_context_card_summarizer_exception_falls_back_to_none(self, caplog):
        pool = _FakePool(rows=[_row(id_="turn-1", content="first")])

        class _Boom:
            def __call__(self, turns: list[WorkingMemory]) -> str:
                raise RuntimeError("boom")

        store = MemoryStore(pool, summarizer=_Boom())

        with caplog.at_level("ERROR"):
            card = store.get_context_card(_SCOPE)

        assert card.summary is None
        assert "Summarizer raised an exception" in caplog.text

    def test_noop_summarizer_returns_empty_string(self):
        noop = NoOpSummarizer()
        result = noop([WorkingMemory(agent_id="agent-001", content="hello")])
        assert result == ""

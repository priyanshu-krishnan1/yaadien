"""
tests/test_repositories.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the five repository classes and the MemoryStore facade.

No live Db2 instance is required.  We use a fake connection pool whose
cursor records every ``execute()`` call and returns configurable rows on
``fetchone()`` / ``fetchall()``.  This validates:
  - Correct SQL structure (scope predicates, FETCH clauses, APPROX suffix)
  - Parameter ordering (vector string, scope values)
  - Model round-trips (create → _model_from_row → field values)
  - Scope enforcement (missing agent_id raises ValueError)
  - soft_delete semantics
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.repositories.base import (
    _parse_vector,
    _scope_predicates,
    _vec_to_str,
)
from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import SearchMode

# ---------------------------------------------------------------------------
# Fake connection pool
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Records calls; fetchone/fetchall return configurable canned data.

    ``rowcount`` defaults to ``len(rows)`` at construction time.  Tests that
    need a specific post-execute rowcount (e.g. UPDATE returning 1 affected
    row) should set ``cursor.rowcount`` *after* construction and the value is
    preserved across ``execute()`` calls.  This matches the DB-API 2.0 spec
    which says rowcount reflects the last execute result — we don't override
    it in execute so the pre-set value is visible to the caller.
    """

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = len(self.rows)

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []
        # rowcount is NOT reset here — tests pre-set it; real cursors update
        # it after execute, but our fake keeps whatever was set.

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakePool:
    """Fake pool that yields a single _FakeConn."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-001", user_id="user-42", tenant_id="t1")
_SCOPE_AGENT_ONLY = MemoryScope(agent_id="agent-001")

_NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
_VEC = [0.1] * 1536


def _row(
    id_: str = "row-uuid",
    content: str = "hello",
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    confidence: float = 1.0,
) -> tuple[Any, ...]:
    """Build a fake DB row matching _SELECT_COLS order (includes confidence at index 8)."""
    meta = metadata or {}
    vec = embedding or _VEC
    vec_str = "[" + ",".join(str(f) for f in vec) + "]"
    return (
        id_,           # 0  id
        "t1",          # 1  tenant_id
        "agent-001",   # 2  agent_id
        "user-42",     # 3  user_id
        None,          # 4  thread_id
        content,       # 5  content
        json.dumps(meta),  # 6  metadata (JSON string)
        vec_str,       # 7  embedding (VECTOR_SERIALIZE output)
        confidence,    # 8  confidence
        _NOW,          # 9  created_at
        _NOW,          # 10 updated_at
        None,          # 11 expires_at
        1,             # 12 version
        None,          # 13 deleted_at
    )


# ---------------------------------------------------------------------------
# _vec_to_str
# ---------------------------------------------------------------------------

class TestVecToStr:
    def test_round_trip(self):
        v = [0.1, 0.2, 0.3]
        s = _vec_to_str(v)
        assert s.startswith("[") and s.endswith("]")
        parsed = _parse_vector(s)
        assert len(parsed) == 3
        assert abs(parsed[0] - 0.1) < 1e-9

    def test_empty_list(self):
        assert _vec_to_str([]) == "[]"


# ---------------------------------------------------------------------------
# _scope_predicates
# ---------------------------------------------------------------------------

class TestScopePredicates:
    def test_agent_only(self):
        sql, params = _scope_predicates(MemoryScope(agent_id="a1"))
        assert "agent_id = ?" in sql
        assert "tenant_id" not in sql
        assert params == ["a1"]

    def test_full_scope(self):
        sql, params = _scope_predicates(
            MemoryScope(agent_id="a", tenant_id="t", user_id="u", thread_id="th")
        )
        assert "tenant_id = ?" in sql
        assert "user_id = ?" in sql
        assert "thread_id = ?" in sql
        assert len(params) == 4

    def test_partial_scope_no_thread(self):
        sql, params = _scope_predicates(MemoryScope(agent_id="a", user_id="u"))
        assert "user_id = ?" in sql
        assert "thread_id" not in sql
        assert len(params) == 2


# ---------------------------------------------------------------------------
# WorkingMemoryRepository
# ---------------------------------------------------------------------------

class TestWorkingMemoryRepository:
    def _repo(self, rows=None):
        return WorkingMemoryRepository(_FakePool(rows))

    def test_create_returns_model(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="test content")
        result = repo.create(wm, _SCOPE_AGENT_ONLY)
        assert isinstance(result, WorkingMemory)
        assert result.content == "test content"
        assert result.agent_id == "agent-001"
        assert result.created_at is not None

    def test_create_issues_insert(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="turn 1")
        repo.create(wm, _SCOPE_AGENT_ONLY)
        sql = pool.cursor.last_sql
        assert "INSERT INTO working_memory" in sql
        assert "CAST(" in sql
        assert "AS VECTOR(" in sql
        assert pool.conn.committed

    def test_create_requires_agent_id(self):
        repo = self._repo()
        with pytest.raises(ValueError, match="agent_id"):
            repo.create(
                WorkingMemory(agent_id="x", content="y"),
                MemoryScope(agent_id=""),   # empty → rejected
            )

    def test_create_sets_scope_from_scope_arg(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="agent-001", user_id="user-99", tenant_id="t")
        wm = WorkingMemory(agent_id="wrong-agent", content="c")
        result = repo.create(wm, scope)
        # Scope overwrites the model fields
        assert result.agent_id == "agent-001"
        assert result.user_id == "user-99"
        assert result.tenant_id == "t"

    def test_get_by_id_found(self):
        row = _row(id_="row-1", content="found")
        repo = self._repo([row])
        result = repo.get_by_id("row-1", _SCOPE)
        assert result is not None
        assert result.id == "row-1"
        assert result.content == "found"

    def test_get_by_id_not_found(self):
        repo = self._repo([])  # empty result
        result = repo.get_by_id("missing", _SCOPE)
        assert result is None

    def test_get_by_id_sql_includes_id_and_scope(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.get_by_id("some-id", _SCOPE)
        sql = pool.cursor.last_sql
        assert "WHERE id = ?" in sql
        assert "agent_id = ?" in sql
        assert "deleted_at IS NULL" in sql

    def test_get_by_id_requires_agent_id(self):
        repo = self._repo()
        with pytest.raises(ValueError, match="agent_id"):
            repo.get_by_id("x", MemoryScope(agent_id=""))

    def test_list_sql_structure(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE)
        sql = pool.cursor.last_sql
        assert "ORDER BY created_at DESC" in sql
        assert "FETCH FIRST" in sql
        assert "deleted_at IS NULL" in sql

    def test_list_excludes_expired_by_default(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE)
        sql = pool.cursor.last_sql
        assert "expires_at IS NULL" in sql

    def test_list_include_expired_flag(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, include_expired=True)
        sql = pool.cursor.last_sql
        # No expires_at filter when include_expired=True
        assert "expires_at IS NULL" not in sql

    def test_list_offset_uses_row_number(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, limit=10, offset=5)
        sql = pool.cursor.last_sql
        assert "ROW_NUMBER" in sql

    def test_list_returns_models(self):
        rows = [_row(id_=f"id-{i}", content=f"content-{i}") for i in range(3)]
        repo = self._repo(rows)
        results = repo.list_all(_SCOPE)
        assert len(results) == 3
        assert all(isinstance(r, WorkingMemory) for r in results)

    def test_soft_delete_sql(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        # Simulate the cursor returning 1 affected row
        repo.soft_delete("row-1", _SCOPE)
        sql = pool.cursor.last_sql
        assert "UPDATE working_memory" in sql
        assert "deleted_at = ?" in sql
        assert "version = version + 1" in sql

    def test_soft_delete_returns_false_when_not_found(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        result = repo.soft_delete("nonexistent", _SCOPE)
        assert result is False

    def test_soft_delete_returns_true_when_found(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        result = repo.soft_delete("row-1", _SCOPE)
        assert result is True

    def test_search_exact_sql(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5, mode=SearchMode.EXACT)
        sql = pool.cursor.last_sql
        assert "VECTOR_DISTANCE" in sql
        assert "COSINE" in sql
        assert "FETCH FIRST" in sql
        # EXACT should NOT have APPROX suffix
        assert "APPROX" not in sql

    def test_search_approx_sql(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5, mode=SearchMode.APPROX)
        sql = pool.cursor.last_sql
        assert "APPROX" in sql

    def test_search_top_k_capped_at_200(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=999)
        params = pool.cursor.last_params
        # top_k is the last positional param (after scope params + vec_str)
        assert params[-1] == 200

    def test_search_empty_embedding_raises(self):
        repo = self._repo()
        with pytest.raises(ValueError, match="non-empty"):
            repo.search([], _SCOPE)


    def test_search_non_numeric_element_raises(self):
        """SQL-injection guard: a non-float element must raise before reaching SQL.

        The Db2 12.1.5 fp0 fix inlines the vector as a SQL literal, so a
        crafted string element (e.g. ``"1) UNION SELECT ... --"``) in
        query_embedding would be interpolated verbatim unless _vec_to_str()
        coerces every element through float().  Verify the guard fires.
        """
        repo = self._repo()
        bad_embedding: list = [0.1] * 5 + ["1) UNION SELECT 1 --"] + [0.2] * 5
        with pytest.raises((ValueError, TypeError)):
            repo.search(bad_embedding, _SCOPE)  # type: ignore[arg-type]


    def test_search_requires_agent_id(self):
        repo = self._repo()
        with pytest.raises(ValueError, match="agent_id"):
            repo.search(_VEC, MemoryScope(agent_id=""))

    def test_search_returns_models(self):
        rows = [_row(id_=f"id-{i}") for i in range(3)]
        repo = self._repo(rows)
        results = repo.search(_VEC, _SCOPE, top_k=10)
        assert len(results) == 3
        assert all(isinstance(r, WorkingMemory) for r in results)

    def test_search_scope_predicate_in_sql(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="a", user_id="u")
        repo.search(_VEC, scope, top_k=5)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "user_id = ?" in sql


# ---------------------------------------------------------------------------
# Confidence scoring (ENH-1)
# ---------------------------------------------------------------------------

class TestConfidenceScoring:
    """Tests for ENH-1: confidence field on all memory types."""

    def test_default_confidence_is_1(self):
        from agent_memory_sdk.models import WorkingMemory
        wm = WorkingMemory(agent_id="a", content="c")
        assert wm.confidence == 1.0

    def test_custom_confidence_set_on_model(self):
        from agent_memory_sdk.models import SemanticFact
        fact = SemanticFact(agent_id="a", content="c", confidence=0.7)
        assert fact.confidence == 0.7

    def test_confidence_persisted_in_create_sql(self):
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="test", confidence=0.8)
        repo.create(wm, _SCOPE_AGENT_ONLY)
        sql = pool.cursor.last_sql
        params = pool.cursor.last_params
        assert "confidence" in sql
        # confidence must appear in params
        assert 0.8 in params

    def test_confidence_persisted_in_update_sql(self):
        pool = _FakePool()
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="test", version=1, confidence=0.6)
        wm.id = "some-id"
        repo.update(wm, _SCOPE_AGENT_ONLY)
        sql = pool.cursor.last_sql
        params = pool.cursor.last_params
        assert "confidence" in sql
        assert 0.6 in params

    def test_confidence_read_back_from_row(self):
        row = _row(confidence=0.75)
        repo = WorkingMemoryRepository(_FakePool([row]))
        result = repo.get_by_id("row-uuid", _SCOPE)
        assert result is not None
        assert abs(result.confidence - 0.75) < 1e-9

    def test_confidence_none_in_db_defaults_to_1(self):
        """Pre-migration rows that return NULL confidence must map to 1.0."""
        row = _row(confidence=None)
        repo = WorkingMemoryRepository(_FakePool([row]))
        result = repo.get_by_id("row-uuid", _SCOPE)
        assert result is not None
        assert result.confidence == 1.0

    def test_list_all_min_confidence_appends_predicate(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, min_confidence=0.7)
        sql = pool.cursor.last_sql
        params = pool.cursor.last_params
        assert "confidence >= ?" in sql
        assert 0.7 in params

    def test_list_all_min_confidence_zero_no_predicate(self):
        """Default (0.0) must NOT add a confidence >= ? WHERE predicate (backward compat)."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, min_confidence=0.0)
        sql = pool.cursor.last_sql
        assert "confidence >= ?" not in sql

    def test_search_min_confidence_appends_predicate(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5, min_confidence=0.9)
        sql = pool.cursor.last_sql
        params = pool.cursor.last_params
        assert "confidence >= ?" in sql
        assert 0.9 in params

    def test_search_min_confidence_zero_no_predicate(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5, min_confidence=0.0)
        sql = pool.cursor.last_sql
        assert "confidence >= ?" not in sql

    def test_list_all_min_confidence_with_offset_uses_row_number(self):
        """With offset > 0 and min_confidence, the ROW_NUMBER path must also
        include the confidence predicate inside the subquery."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, limit=5, offset=3, min_confidence=0.8)
        sql = pool.cursor.last_sql
        assert "ROW_NUMBER" in sql
        assert "confidence >= ?" in sql
        params = pool.cursor.last_params
        assert 0.8 in params


# ---------------------------------------------------------------------------
# EpisodicMemoryRepository
# ---------------------------------------------------------------------------

class TestEpisodicMemoryRepository:
    def test_create_returns_episodic_model(self):
        pool = _FakePool()
        repo = EpisodicMemoryRepository(pool)
        ep = EpisodicMemory(agent_id="agent-001", content="episode summary")
        result = repo.create(ep, _SCOPE_AGENT_ONLY)
        assert isinstance(result, EpisodicMemory)
        assert result.content == "episode summary"

    def test_table_name(self):
        assert EpisodicMemoryRepository._TABLE == "episodic_memory"


# ---------------------------------------------------------------------------
# SemanticFactRepository
# ---------------------------------------------------------------------------

class TestSemanticFactRepository:
    def test_create_returns_fact_model(self):
        pool = _FakePool()
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="User likes Python")
        result = repo.create(fact, _SCOPE_AGENT_ONLY)
        assert isinstance(result, SemanticFact)

    def test_table_name(self):
        assert SemanticFactRepository._TABLE == "semantic_facts"


# ---------------------------------------------------------------------------
# EntityProfileRepository
# ---------------------------------------------------------------------------

class TestEntityProfileRepository:
    def test_create_returns_profile_model(self):
        pool = _FakePool()
        repo = EntityProfileRepository(pool)
        profile = EntityProfile(agent_id="agent-001", content="Power developer")
        result = repo.create(profile, _SCOPE_AGENT_ONLY)
        assert isinstance(result, EntityProfile)

    def test_table_name(self):
        assert EntityProfileRepository._TABLE == "entity_profiles"


# ---------------------------------------------------------------------------
# ProceduralMemoryRepository
# ---------------------------------------------------------------------------

class TestProceduralMemoryRepository:
    def test_create_returns_procedural_model(self):
        pool = _FakePool()
        repo = ProceduralMemoryRepository(pool)
        skill = ProceduralMemory(agent_id="agent-001", content="Always check traceback")
        result = repo.create(skill, _SCOPE_AGENT_ONLY)
        assert isinstance(result, ProceduralMemory)

    def test_table_name(self):
        assert ProceduralMemoryRepository._TABLE == "procedural_memory"


# ---------------------------------------------------------------------------
# MemoryStore facade
# ---------------------------------------------------------------------------

class TestMemoryStore:
    def test_has_all_repos(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        assert isinstance(store.working, WorkingMemoryRepository)
        assert isinstance(store.episodic, EpisodicMemoryRepository)
        assert isinstance(store.facts, SemanticFactRepository)
        assert isinstance(store.profiles, EntityProfileRepository)
        assert isinstance(store.procedures, ProceduralMemoryRepository)

    def test_custom_embedding_dim_propagated(self):
        pool = _FakePool()
        store = MemoryStore(pool, embedding_dim=768)
        for repo in (
            store.working,
            store.episodic,
            store.facts,
            store.profiles,
            store.procedures,
        ):
            assert repo.EMBEDDING_DIM == 768

    def test_store_working_create_roundtrip(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")
        record = store.working.create(
            WorkingMemory(agent_id="agent-001", content="hello world"),
            scope,
        )
        assert record.content == "hello world"
        assert record.agent_id == "agent-001"
        assert record.created_at is not None

    def test_store_search_delegates_to_working_repo(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")
        results = store.working.search(_VEC, scope, top_k=3)
        assert results == []
        sql = pool.cursor.last_sql
        assert "VECTOR_DISTANCE" in sql


# ---------------------------------------------------------------------------
# Cross-scope isolation check
# ---------------------------------------------------------------------------

class TestScopeIsolation:
    """Asserts that the scope predicates are present on every query type,
    so that a caller cannot read another scope's rows even by guessing IDs."""

    def test_get_by_id_scope_predicate_in_where(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope_a = MemoryScope(agent_id="agent-A")
        repo.get_by_id("known-id", scope_a)
        sql = pool.cursor.last_sql
        # Must contain scope predicate alongside id lookup
        assert "agent_id = ?" in sql
        assert "id = ?" in sql

    def test_list_scope_predicate_in_where(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="agent-B", tenant_id="tenant-X")
        repo.list_all(scope)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_soft_delete_scope_predicate_in_where(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="agent-C")
        repo.soft_delete("some-id", scope)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql

    def test_search_scope_predicate_in_where(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="agent-D")
        repo.search(_VEC, scope)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql

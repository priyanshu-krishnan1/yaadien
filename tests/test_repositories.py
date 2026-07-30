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
  - ENH-2: write-time content-hash dedup
"""

from __future__ import annotations

import hashlib
import json
import re
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
    _content_hash,
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
    content_hash: str | None = None,
) -> tuple[Any, ...]:
    """Build a fake DB row matching _SELECT_COLS order for working/episodic repos.

    Index map (0-based):
      0  id            1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content       6  metadata    7  embedding
      8  confidence    9  content_hash
      10 created_at   11 updated_at  12 expires_at  13 version     14 deleted_at
      15 consolidated_at  (ENH-4 / migration 0005 — None = not yet consolidated)
    """
    meta = metadata or {}
    vec = embedding or _VEC
    vec_str = "[" + ",".join(str(f) for f in vec) + "]"
    h = content_hash if content_hash is not None else _content_hash(content)
    return (
        id_,                  # 0  id
        "t1",                 # 1  tenant_id
        "agent-001",          # 2  agent_id
        "user-42",            # 3  user_id
        None,                 # 4  thread_id
        content,              # 5  content
        json.dumps(meta),     # 6  metadata (JSON string)
        vec_str,              # 7  embedding (VECTOR_SERIALIZE output)
        confidence,           # 8  confidence
        h,                    # 9  content_hash
        _NOW,                 # 10 created_at
        _NOW,                 # 11 updated_at
        None,                 # 12 expires_at
        1,                    # 13 version
        None,                 # 14 deleted_at
        None,                 # 15 consolidated_at (ENH-4)
    )


def _fact_row(
    id_: str = "row-uuid",
    content: str = "hello",
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    confidence: float = 1.0,
    content_hash: str | None = None,
    superseded_by: Any = None,
    superseded_at: Any = None,
    supersede_reason: Any = None,
) -> tuple[Any, ...]:
    """Build a fake DB row for SemanticFactRepository._model_from_row (18 cols).

    Index map (0-based):
      0  id            1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content       6  metadata    7  embedding
      8  confidence    9  content_hash
      10 created_at   11 updated_at  12 expires_at  13 version     14 deleted_at
      15 superseded_by  16 superseded_at  17 supersede_reason
    """
    meta = metadata or {}
    vec = embedding or _VEC
    vec_str = "[" + ",".join(str(f) for f in vec) + "]"
    h = content_hash if content_hash is not None else _content_hash(content)
    return (
        id_, "t1", "agent-001", "user-42", None,
        content, json.dumps(meta),
        vec_str,
        confidence,
        h,
        _NOW, _NOW, None, 1, None,
        superseded_by,    # 15
        superseded_at,    # 16
        supersede_reason, # 17
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

    # -- Pydantic range enforcement (ENH-1 fix) --------------------------------

    def test_confidence_above_1_raises(self):
        """confidence > 1.0 must be rejected at construction time."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SemanticFact(agent_id="a", content="c", confidence=1.5)

    def test_confidence_at_1_is_valid(self):
        """Boundary value 1.0 must be accepted."""
        fact = SemanticFact(agent_id="a", content="c", confidence=1.0)
        assert fact.confidence == 1.0

    def test_confidence_at_0_is_valid(self):
        """Boundary value 0.0 must be accepted."""
        fact = SemanticFact(agent_id="a", content="c", confidence=0.0)
        assert fact.confidence == 0.0

    def test_confidence_negative_raises(self):
        """Negative confidence must be rejected at construction time."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WorkingMemory(agent_id="a", content="c", confidence=-0.1)

    def test_confidence_57_raises(self):
        """A wildly out-of-range value (e.g. 57.0) must be rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SemanticFact(agent_id="a", content="c", confidence=57.0)

    def test_confidence_constraint_applies_to_all_subtypes(self):
        """All five concrete model types inherit the constraint from _MemoryBase."""
        from pydantic import ValidationError
        for Model in (
            WorkingMemory,
            EpisodicMemory,
            SemanticFact,
            EntityProfile,
            ProceduralMemory,
        ):
            with pytest.raises(ValidationError, match="confidence"):
                Model(agent_id="a", content="c", confidence=2.0)


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


# ---------------------------------------------------------------------------
# ENH-2: content_hash normalization and write-time deduplication
# ---------------------------------------------------------------------------

class TestContentHash:
    """Unit tests for _content_hash() normalization and dedup logic in create()."""

    # ------------------------------------------------------------------
    # _content_hash() normalization
    # ------------------------------------------------------------------

    def test_lowercase(self):
        """Uppercase and mixed-case content must produce the same hash."""
        assert _content_hash("HELLO WORLD") == _content_hash("hello world")
        assert _content_hash("Hello") == _content_hash("hello")

    def test_whitespace_collapse_spaces(self):
        """Multiple consecutive spaces must be collapsed to one."""
        assert _content_hash("hello   world") == _content_hash("hello world")

    def test_whitespace_collapse_tabs_newlines(self):
        """Tabs, newlines, and mixed whitespace must collapse to a single space."""
        assert _content_hash("hello\t\tworld") == _content_hash("hello world")
        assert _content_hash("hello\nworld") == _content_hash("hello world")
        assert _content_hash("hello\r\nworld") == _content_hash("hello world")

    def test_leading_trailing_whitespace_stripped(self):
        """Leading/trailing whitespace must not affect the hash."""
        assert _content_hash("  hello world  ") == _content_hash("hello world")
        assert _content_hash("\nhello world\n") == _content_hash("hello world")

    def test_case_and_whitespace_combined(self):
        """Both normalization steps together must produce the same hash."""
        assert _content_hash("  Hello   WORLD\n") == _content_hash("hello world")

    def test_returns_64_hex_chars(self):
        """Result must be a 64-character lowercase hex string (SHA-256)."""
        h = _content_hash("some content")
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_content_different_hash(self):
        """Distinct normalized contents must produce distinct hashes."""
        assert _content_hash("apple") != _content_hash("orange")

    def test_known_value(self):
        """Cross-check against a direct hashlib computation."""
        content = "  User  prefers   Python.\n"
        normalized = re.sub(r"\s+", " ", content.lower()).strip()
        expected = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert _content_hash(content) == expected

    # ------------------------------------------------------------------
    # create() — WorkingMemory MUST NOT dedup (new correct behaviour)
    # ------------------------------------------------------------------

    def test_create_dedup_returns_existing_when_hit(self):
        """WorkingMemory.create() must always insert a new row even when
        content_hash matches an existing row — no dedup for append-only logs."""
        existing = _row(id_="existing-id", content="hello world")
        pool = _FakePool([existing])
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="hello world")
        result = repo.create(wm, _SCOPE_AGENT_ONLY)
        # Must NOT return the old existing row — must insert and return the new one
        assert result.id != "existing-id"
        # An INSERT must have been issued
        assert "INSERT INTO working_memory" in pool.cursor.last_sql

    def test_create_dedup_issues_select_before_insert(self):
        """WorkingMemory.create() must issue only an INSERT — no dedup SELECT."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="unique content xyz")
        repo.create(wm, _SCOPE_AGENT_ONLY)
        # The one and only SQL issued should be the INSERT — no dedup SELECT
        assert "INSERT INTO working_memory" in pool.cursor.last_sql
        assert "content_hash = ?" not in pool.cursor.last_sql

    def test_create_dedup_select_contains_content_hash_predicate(self):
        """SemanticFact.create() (dedup on) must issue a dedup SELECT before INSERT."""
        # Use a multi-cursor-tracking pool variant
        class _MultiCursorPool:
            """Records ALL executed SQL strings across all cursor() calls."""
            def __init__(self):
                self.sqls: list[str] = []
                self._conn = None

            @contextmanager
            def get_connection(self):
                conn = _MultiCursorConn(self.sqls)
                yield conn

        class _MultiCursorConn:
            def __init__(self, sqls):
                self._sqls = sqls
                self.committed = False
            def cursor(self):
                return _MultiCursorCursor(self._sqls)
            def commit(self):
                self.committed = True

        class _MultiCursorCursor:
            def __init__(self, sqls):
                self._sqls = sqls
                self.rowcount = 0
            def execute(self, sql, params=None):
                self._sqls.append(sql)
            def fetchone(self):
                return None
            def fetchall(self):
                return []

        pool = _MultiCursorPool()
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="test content")
        repo.create(fact, _SCOPE_AGENT_ONLY)
        # First SQL must be the dedup SELECT
        assert len(pool.sqls) >= 2
        dedup_sql = pool.sqls[0]
        assert "content_hash = ?" in dedup_sql
        assert "deleted_at IS NULL" in dedup_sql
        assert "FETCH FIRST 1 ROWS ONLY" in dedup_sql
        insert_sql = pool.sqls[1]
        assert "INSERT INTO semantic_facts" in insert_sql

    def test_create_content_hash_in_insert_params(self):
        """The computed hash must be included in the INSERT params."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="Test Content")
        repo.create(wm, _SCOPE_AGENT_ONLY)
        params = pool.cursor.last_params
        expected_hash = _content_hash("Test Content")
        assert expected_hash in params

    def test_create_content_hash_on_returned_model(self):
        """create() must set content_hash on the returned model."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="My Memory")
        result = repo.create(wm, _SCOPE_AGENT_ONLY)
        assert result.content_hash == _content_hash("My Memory")

    def test_create_dedup_normalized_equivalents_hit(self):
        """SemanticFact dedup: whitespace/case variants of same content must dedup."""
        # "  HELLO  WORLD  " normalizes to "hello world" — same hash
        normalized_content = "hello world"
        existing = _fact_row(
            id_="norm-id",
            content=normalized_content,
            content_hash=_content_hash("  HELLO  WORLD  "),
        )
        pool = _FakePool([existing])
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="  HELLO  WORLD  ")
        result = repo.create(fact, _SCOPE_AGENT_ONLY)
        assert result.id == "norm-id"

    def test_create_dedup_returns_existing_for_semantic_fact(self):
        """SemanticFact.create() must return the existing row on a dedup hit."""
        existing = _fact_row(id_="sf-existing-id", content="User prefers Python")
        pool = _FakePool([existing])
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="User prefers Python")
        result = repo.create(fact, _SCOPE_AGENT_ONLY)
        # Must return the existing row without inserting a new one
        assert result.id == "sf-existing-id"
        assert "INSERT" not in pool.cursor.last_sql

    def test_working_memory_dedup_on_write_is_false(self):
        """WorkingMemoryRepository._DEDUP_ON_WRITE must be False."""
        from agent_memory_sdk.repositories.working import WorkingMemoryRepository as WMR
        assert WMR._DEDUP_ON_WRITE is False

    def test_semantic_fact_dedup_on_write_is_true(self):
        """SemanticFactRepository._DEDUP_ON_WRITE must be True (inherits default)."""
        assert SemanticFactRepository._DEDUP_ON_WRITE is True

    def test_content_hash_read_back_from_row(self):
        """_model_from_row() must populate content_hash from column index 9."""
        h = _content_hash("hello")
        row = _row(id_="r1", content="hello", content_hash=h)
        repo = WorkingMemoryRepository(_FakePool([row]))
        result = repo.get_by_id("r1", _SCOPE)
        assert result is not None
        assert result.content_hash == h

    def test_content_hash_none_preserved_for_premigration_rows(self):
        """Rows written before migration 0003 may have NULL content_hash; must survive."""
        row = _row(id_="r2", content="old content", content_hash=None)
        # Manually override index 9 to None (the helper sets it by default)
        row_list = list(row)
        row_list[9] = None
        repo = WorkingMemoryRepository(_FakePool([tuple(row_list)]))
        result = repo.get_by_id("r2", _SCOPE)
        assert result is not None
        assert result.content_hash is None

    def test_update_recomputes_content_hash(self):
        """update() must persist the recomputed hash for the new content."""
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="New Content", version=1)
        wm.id = "update-id"
        repo.update(wm, _SCOPE_AGENT_ONLY)
        params = pool.cursor.last_params
        expected_hash = _content_hash("New Content")
        assert expected_hash in params

    def test_update_sets_content_hash_on_model(self):
        """update() must set content_hash on the returned (mutated) model."""
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="Updated text", version=1)
        wm.id = "update-id-2"
        result = repo.update(wm, _SCOPE_AGENT_ONLY)
        assert result.content_hash == _content_hash("Updated text")

    def test_update_sql_includes_content_hash_column(self):
        """The UPDATE SQL must SET content_hash = ?."""
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="c", version=1)
        wm.id = "u-id"
        repo.update(wm, _SCOPE_AGENT_ONLY)
        sql = pool.cursor.last_sql
        assert "content_hash = ?" in sql

    def test_create_insert_sql_includes_content_hash_column(self):
        """The INSERT SQL must list content_hash in the column list and VALUES."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        wm = WorkingMemory(agent_id="agent-001", content="some text")
        repo.create(wm, _SCOPE_AGENT_ONLY)
        sql = pool.cursor.last_sql
        assert "content_hash" in sql


# ---------------------------------------------------------------------------
# ENH-3 regression: _HAS_SUPERSESSION gate — non-facts repos must NEVER
# reference superseded_at; SemanticFactRepository must always include it.
# ---------------------------------------------------------------------------

class TestHasSupersessionFlag:
    """Regression tests for the _HAS_SUPERSESSION class-level gate.

    Verifies that list_all(), search(), and create()'s dedup SELECT never
    emit "superseded_at" for the four tables that lack the column
    (working_memory, episodic_memory, entity_profiles, procedural_memory),
    and that SemanticFactRepository still includes it everywhere.

    Background: migration 0004 added the supersession columns to
    semantic_facts only.  Referencing a nonexistent column in SQL is a
    compile-time error on Db2 (SQLCODE -206); it is NOT a vacuous truth.
    """

    # ------------------------------------------------------------------
    # Helpers — build one repo of each non-supersession type
    # ------------------------------------------------------------------

    def _working(self):
        return WorkingMemoryRepository(_FakePool([]))

    def _episodic(self):
        return EpisodicMemoryRepository(_FakePool([]))

    def _profiles(self):
        return EntityProfileRepository(_FakePool([]))

    def _procedural(self):
        return ProceduralMemoryRepository(_FakePool([]))

    def _facts(self):
        return SemanticFactRepository(_FakePool([]))

    # ------------------------------------------------------------------
    # Class-attribute checks
    # ------------------------------------------------------------------

    def test_working_has_supersession_false(self):
        assert WorkingMemoryRepository._HAS_SUPERSESSION is False

    def test_episodic_has_supersession_false(self):
        assert EpisodicMemoryRepository._HAS_SUPERSESSION is False

    def test_profiles_has_supersession_false(self):
        assert EntityProfileRepository._HAS_SUPERSESSION is False

    def test_procedural_has_supersession_false(self):
        assert ProceduralMemoryRepository._HAS_SUPERSESSION is False

    def test_facts_has_supersession_true(self):
        assert SemanticFactRepository._HAS_SUPERSESSION is True

    # ------------------------------------------------------------------
    # list_all() — no superseded_at for non-facts repos
    # ------------------------------------------------------------------

    def test_working_list_all_no_superseded_at(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_episodic_list_all_no_superseded_at(self):
        pool = _FakePool([])
        repo = EpisodicMemoryRepository(pool)
        repo.list_all(_SCOPE)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_profiles_list_all_no_superseded_at(self):
        pool = _FakePool([])
        repo = EntityProfileRepository(pool)
        repo.list_all(_SCOPE)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_procedural_list_all_no_superseded_at(self):
        pool = _FakePool([])
        repo = ProceduralMemoryRepository(pool)
        repo.list_all(_SCOPE)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_facts_list_all_has_superseded_at(self):
        pool = _FakePool([])
        repo = SemanticFactRepository(pool)
        repo.list_all(_SCOPE)
        assert "superseded_at IS NULL" in pool.cursor.last_sql

    # ------------------------------------------------------------------
    # list_all() with offset (ROW_NUMBER path) — same checks
    # ------------------------------------------------------------------

    def test_working_list_all_offset_no_superseded_at(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE, offset=5)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_facts_list_all_offset_has_superseded_at(self):
        pool = _FakePool([])
        repo = SemanticFactRepository(pool)
        repo.list_all(_SCOPE, offset=5)
        assert "superseded_at IS NULL" in pool.cursor.last_sql

    # ------------------------------------------------------------------
    # search() step-1 SQL — no superseded_at for non-facts repos
    # ------------------------------------------------------------------

    def test_working_search_no_superseded_at(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5)
        # all_sqls[0] is step-1 (ID-ranking); all_sqls is not on _FakePool cursor
        # but last_sql after the first execute is the step-1 SQL (pool returns
        # empty → step-2 is never reached)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_episodic_search_no_superseded_at(self):
        pool = _FakePool([])
        repo = EpisodicMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_profiles_search_no_superseded_at(self):
        pool = _FakePool([])
        repo = EntityProfileRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_procedural_search_no_superseded_at(self):
        pool = _FakePool([])
        repo = ProceduralMemoryRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5)
        assert "superseded_at" not in pool.cursor.last_sql

    def test_facts_search_step1_has_superseded_at(self):
        """SemanticFactRepository.search() step-1 SQL must contain superseded_at IS NULL."""
        # Need all_sqls tracking — use the multi-cursor pattern from the dedup test
        from contextlib import contextmanager

        class _TrackingPool:
            def __init__(self):
                self.sqls: list[str] = []
            @contextmanager
            def get_connection(self):
                yield _TrackingConn(self.sqls)

        class _TrackingConn:
            def __init__(self, sqls):
                self._sqls = sqls
                self.committed = False
            def cursor(self):
                return _TrackingCur(self._sqls)
            def commit(self):
                self.committed = True

        class _TrackingCur:
            def __init__(self, sqls):
                self._sqls = sqls
                self.rowcount = 0
            def execute(self, sql, params=None):
                self._sqls.append(sql)
            def fetchone(self):
                return None
            def fetchall(self):
                return []

        pool = _TrackingPool()
        repo = SemanticFactRepository(pool)
        repo.search(_VEC, _SCOPE, top_k=5)
        step1_sql = pool.sqls[0]
        assert "superseded_at IS NULL" in step1_sql

    # ------------------------------------------------------------------
    # create() dedup SELECT — no superseded_at for non-facts repos
    # ------------------------------------------------------------------

    def test_episodic_create_dedup_no_superseded_at(self):
        """EpisodicMemoryRepository has _DEDUP_ON_WRITE=True but _HAS_SUPERSESSION=False.
        Its dedup SELECT must not reference superseded_at."""
        from contextlib import contextmanager

        class _TrackingPool:
            def __init__(self):
                self.sqls: list[str] = []
            @contextmanager
            def get_connection(self):
                yield _TrackingConn(self.sqls)

        class _TrackingConn:
            def __init__(self, sqls):
                self._sqls = sqls
                self.committed = False
            def cursor(self):
                return _TrackingCur(self._sqls)
            def commit(self):
                self.committed = True

        class _TrackingCur:
            def __init__(self, sqls):
                self._sqls = sqls
                self.rowcount = 0
            def execute(self, sql, params=None):
                self._sqls.append(sql)
            def fetchone(self):
                return None  # no dedup hit → INSERT proceeds
            def fetchall(self):
                return []

        pool = _TrackingPool()
        repo = EpisodicMemoryRepository(pool)
        ep = EpisodicMemory(agent_id="agent-001", content="some episodic content")
        repo.create(ep, _SCOPE_AGENT_ONLY)
        # First SQL is the dedup SELECT
        dedup_sql = pool.sqls[0]
        assert "content_hash = ?" in dedup_sql
        assert "superseded_at" not in dedup_sql

    def test_facts_create_dedup_has_superseded_at(self):
        """SemanticFactRepository dedup SELECT must include superseded_at IS NULL."""
        from contextlib import contextmanager

        class _TrackingPool:
            def __init__(self):
                self.sqls: list[str] = []
            @contextmanager
            def get_connection(self):
                yield _TrackingConn(self.sqls)

        class _TrackingConn:
            def __init__(self, sqls):
                self._sqls = sqls
                self.committed = False
            def cursor(self):
                return _TrackingCur(self._sqls)
            def commit(self):
                self.committed = True

        class _TrackingCur:
            def __init__(self, sqls):
                self._sqls = sqls
                self.rowcount = 0
            def execute(self, sql, params=None):
                self._sqls.append(sql)
            def fetchone(self):
                return None
            def fetchall(self):
                return []

        pool = _TrackingPool()
        repo = SemanticFactRepository(pool)
        fact = SemanticFact(agent_id="agent-001", content="some semantic fact")
        repo.create(fact, _SCOPE_AGENT_ONLY)
        dedup_sql = pool.sqls[0]
        assert "superseded_at IS NULL" in dedup_sql

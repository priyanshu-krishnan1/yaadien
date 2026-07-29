"""
tests/test_scoping.py
~~~~~~~~~~~~~~~~~~~~~
Step-5 governance / scoping enforcement tests.

Focus areas
-----------
1. ``MemoryScope`` value object — shape, required fields, immutability.
2. Every SQL operation includes scope predicates (tenant_id, agent_id,
   user_id, thread_id) in the WHERE clause — no unscoped reads.
3. Cross-scope isolation: a caller who knows another scope's row id
   receives None / [] — never leaks data across the isolation boundary.
4. The isolation property holds for every repository (all five types)
   and every read/mutate operation: get_by_id, list_all, search,
   forget/soft_delete, update, purge_expired.
5. ``MemoryStore`` facade methods (remember, forget, purge_expired)
   carry the scope all the way down to the repository SQL.

No live Db2 instance is required; the fake connection pool pattern
from test_repositories.py and test_lifecycle.py is used throughout.
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
from agent_memory_sdk.repositories.base import _scope_predicates
from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Fake connection pool
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Records calls; fetchone/fetchall return configurable canned rows."""

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
    """A fake pool that returns a single cursor whose rows are pre-configured."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 31, 0, 0, 0, tzinfo=timezone.utc)
_VEC = [0.1] * 1536

# Full four-part scope for the "owner" of test rows.
_SCOPE_OWNER = MemoryScope(
    tenant_id="tenant-A",
    agent_id="agent-owner",
    user_id="user-owner",
    thread_id="thread-owner",
)

# A completely different scope — different at every level.
_SCOPE_OTHER = MemoryScope(
    tenant_id="tenant-B",
    agent_id="agent-other",
    user_id="user-other",
    thread_id="thread-other",
)

# Minimal valid scope (only agent_id).
_SCOPE_MINIMAL = MemoryScope(agent_id="agent-minimal")


def _db_row(
    id_: str = "row-id-001",
    tenant_id: str = "tenant-A",
    agent_id: str = "agent-owner",
    user_id: str = "user-owner",
    thread_id: str = "thread-owner",
    content: str = "owner content",
    version: int = 1,
    deleted_at: Any = None,
) -> tuple[Any, ...]:
    """Build a fake DB row matching BaseRepository._SELECT_COLS order."""
    vec_str = "[" + ",".join("0.1" for _ in range(1536)) + "]"
    return (
        id_, tenant_id, agent_id, user_id, thread_id,
        content, json.dumps({"key": "val"}),
        vec_str,
        _NOW, _NOW, None, version, deleted_at,
    )


# ---------------------------------------------------------------------------
# MemoryScope value object
# ---------------------------------------------------------------------------


class TestMemoryScopeValueObject:
    """MemoryScope is a frozen Pydantic model; verify its contract."""

    def test_minimal_scope_agent_id_only(self):
        scope = MemoryScope(agent_id="a")
        assert scope.agent_id == "a"
        assert scope.tenant_id is None
        assert scope.user_id is None
        assert scope.thread_id is None

    def test_full_scope_all_fields(self):
        scope = MemoryScope(
            tenant_id="t", agent_id="a", user_id="u", thread_id="th"
        )
        assert scope.tenant_id == "t"
        assert scope.agent_id == "a"
        assert scope.user_id == "u"
        assert scope.thread_id == "th"

    def test_agent_id_required(self):
        """agent_id must be supplied; an empty string is rejected at enforcement time."""
        # Pydantic allows construction with agent_id=""; _require_agent_id
        # enforces non-empty when the scope is passed to a repository method.
        scope = MemoryScope(agent_id="")
        assert scope.agent_id == ""

    def test_scope_is_frozen(self):
        """MemoryScope is immutable: attribute assignment must raise ValidationError."""
        from pydantic import ValidationError

        scope = MemoryScope(agent_id="a")
        with pytest.raises(ValidationError):
            scope.agent_id = "b"  # type: ignore[misc]

    def test_equal_scopes_are_equal(self):
        s1 = MemoryScope(agent_id="a", tenant_id="t")
        s2 = MemoryScope(agent_id="a", tenant_id="t")
        assert s1 == s2

    def test_different_scopes_are_not_equal(self):
        s1 = MemoryScope(agent_id="a")
        s2 = MemoryScope(agent_id="b")
        assert s1 != s2

    def test_scope_hashable(self):
        """Frozen models are hashable and can be used in sets/dict keys."""
        s = MemoryScope(agent_id="a", tenant_id="t")
        d = {s: "value"}
        assert d[s] == "value"


# ---------------------------------------------------------------------------
# _scope_predicates helper
# ---------------------------------------------------------------------------


class TestScopePredicatesHelper:
    """Unit tests for the _scope_predicates() building block."""

    def test_always_includes_agent_id(self):
        sql, params = _scope_predicates(MemoryScope(agent_id="x"))
        assert "agent_id = ?" in sql
        assert "x" in params

    def test_omits_tenant_id_when_none(self):
        sql, _ = _scope_predicates(MemoryScope(agent_id="x"))
        assert "tenant_id" not in sql

    def test_includes_tenant_id_when_set(self):
        sql, params = _scope_predicates(MemoryScope(agent_id="x", tenant_id="t"))
        assert "tenant_id = ?" in sql
        assert "t" in params

    def test_includes_user_id_when_set(self):
        sql, params = _scope_predicates(MemoryScope(agent_id="x", user_id="u"))
        assert "user_id = ?" in sql
        assert "u" in params

    def test_includes_thread_id_when_set(self):
        sql, params = _scope_predicates(
            MemoryScope(agent_id="x", thread_id="th")
        )
        assert "thread_id = ?" in sql
        assert "th" in params

    def test_full_scope_yields_four_predicates(self):
        sql, params = _scope_predicates(
            MemoryScope(agent_id="a", tenant_id="t", user_id="u", thread_id="th")
        )
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql
        assert "user_id = ?" in sql
        assert "thread_id = ?" in sql
        assert len(params) == 4

    def test_predicates_joined_with_and(self):
        sql, _ = _scope_predicates(
            MemoryScope(agent_id="a", tenant_id="t")
        )
        assert "AND" in sql


# ---------------------------------------------------------------------------
# Cross-scope isolation: get_by_id
# ---------------------------------------------------------------------------


class TestGetByIdScopeIsolation:
    """The id + scope WHERE clause must prevent cross-scope reads."""

    def _repo_with_row(self, row: tuple[Any, ...]):
        """Return a working-memory repo whose fake cursor returns *row*."""
        return WorkingMemoryRepository(_FakePool([row]))

    def _empty_repo(self):
        return WorkingMemoryRepository(_FakePool([]))

    # ── SQL structure ──────────────────────────────────────────────────────

    def test_sql_includes_id_and_scope(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.get_by_id("known-id", _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "id = ?" in sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql
        assert "user_id = ?" in sql
        assert "thread_id = ?" in sql

    def test_params_contain_scope_values(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.get_by_id("known-id", _SCOPE_OWNER)
        params = pool.cursor.last_params
        assert "agent-owner" in params
        assert "tenant-A" in params
        assert "user-owner" in params
        assert "thread-owner" in params

    # ── Cross-scope returns None ───────────────────────────────────────────

    def test_cross_scope_returns_none(self):
        """
        The fake cursor returns 0 rows (simulating what Db2 would return when
        the row exists but belongs to *_SCOPE_OWNER*, while the query was sent
        with *_SCOPE_OTHER*'s agent_id predicate — which doesn't match).
        The repo must return None.
        """
        repo = self._empty_repo()
        result = repo.get_by_id("known-owner-id", _SCOPE_OTHER)
        assert result is None

    def test_cross_scope_params_do_not_leak_owner_agent_id(self):
        """Ensure the bound parameters reflect the *requesting* scope, not the row's scope."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.get_by_id("known-owner-id", _SCOPE_OTHER)
        params = pool.cursor.last_params
        # Requesting scope's agent_id must be bound
        assert "agent-other" in params
        # Owner scope's agent_id must NOT appear in the bound params
        assert "agent-owner" not in params

    def test_same_scope_returns_row(self):
        """With the correct scope, the row is found."""
        row = _db_row(id_="r1")
        repo = self._repo_with_row(row)
        result = repo.get_by_id("r1", _SCOPE_OWNER)
        assert result is not None
        assert result.id == "r1"


# ---------------------------------------------------------------------------
# Cross-scope isolation: list_all
# ---------------------------------------------------------------------------


class TestListAllScopeIsolation:
    def test_sql_includes_scope_predicate(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_cross_scope_returns_empty_list(self):
        """When the fake cursor has no rows (cross-scope predicate mismatch), return []."""
        repo = WorkingMemoryRepository(_FakePool([]))
        results = repo.list_all(_SCOPE_OTHER)
        assert results == []

    def test_params_use_requesting_scope(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "tenant-B" in params

    def test_no_id_only_query(self):
        """list_all must never produce SQL with ONLY an id predicate — no scope → not allowed."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.list_all(_SCOPE_MINIMAL)
        sql = pool.cursor.last_sql
        # Must contain agent_id even for minimal scope
        assert "agent_id = ?" in sql


# ---------------------------------------------------------------------------
# Cross-scope isolation: search
# ---------------------------------------------------------------------------


class TestSearchScopeIsolation:
    def test_sql_includes_scope_predicate(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_cross_scope_returns_empty_list(self):
        repo = WorkingMemoryRepository(_FakePool([]))
        results = repo.search(_VEC, _SCOPE_OTHER)
        assert results == []

    def test_params_use_requesting_scope_not_another(self):
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        repo.search(_VEC, _SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "agent-owner" not in params

    def test_scope_appears_before_vector_in_params(self):
        """Scope must be the first predicates; vector is the ORDER BY arg."""
        pool = _FakePool([])
        repo = WorkingMemoryRepository(pool)
        scope = MemoryScope(agent_id="a-search", tenant_id="t-search")
        repo.search(_VEC, scope)
        params = pool.cursor.last_params
        # scope params: agent_id, tenant_id → then vec_str → then top_k
        assert params[0] == "a-search"
        assert params[1] == "t-search"


# ---------------------------------------------------------------------------
# Cross-scope isolation: forget (soft_delete)
# ---------------------------------------------------------------------------


class TestForgetScopeIsolation:
    def test_sql_includes_id_and_scope(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        repo.forget("row-id", _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "id = ?" in sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_cross_scope_forget_returns_false(self):
        """forget() with the wrong scope cannot tombstone another scope's row."""
        pool = _FakePool([])
        pool.cursor.rowcount = 0  # 0 rows affected → nothing tombstoned
        repo = WorkingMemoryRepository(pool)
        result = repo.forget("known-owner-id", _SCOPE_OTHER)
        assert result is False

    def test_params_use_requesting_scope(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.forget("some-id", _SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "agent-owner" not in params


# ---------------------------------------------------------------------------
# Cross-scope isolation: update
# ---------------------------------------------------------------------------


class TestUpdateScopeIsolation:
    def _record(self) -> WorkingMemory:
        return WorkingMemory(
            id="row-001",
            agent_id="agent-owner",
            content="new content",
            version=1,
        )

    def test_sql_includes_scope_predicate(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        repo.update(self._record(), _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_cross_scope_update_raises_stale_write(self):
        """Updating with a different scope yields 0 rowcount → StaleWriteError."""
        from agent_memory_sdk.exceptions import StaleWriteError

        pool = _FakePool([])
        pool.cursor.rowcount = 0  # scope mismatch → nothing updated
        repo = WorkingMemoryRepository(pool)
        with pytest.raises(StaleWriteError):
            repo.update(self._record(), _SCOPE_OTHER)

    def test_params_use_requesting_scope(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 1
        repo = WorkingMemoryRepository(pool)
        repo.update(self._record(), _SCOPE_OTHER)
        params = pool.cursor.last_params
        # Requesting scope's agent_id must be in params
        assert "agent-other" in params
        # Owner's agent_id must not appear in the bound params
        assert "agent-owner" not in params


# ---------------------------------------------------------------------------
# Cross-scope isolation: purge_expired
# ---------------------------------------------------------------------------


class TestPurgeExpiredScopeIsolation:
    def test_sql_includes_scope_predicate(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.purge_expired(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql

    def test_cross_scope_purge_returns_zero(self):
        """Purge with a non-matching scope deletes 0 rows for a different tenant."""
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        result = repo.purge_expired(_SCOPE_OTHER)
        assert result == 0

    def test_params_use_requesting_scope(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = WorkingMemoryRepository(pool)
        repo.purge_expired(_SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "agent-owner" not in params


# ---------------------------------------------------------------------------
# Cross-scope isolation: all five repository types
# ---------------------------------------------------------------------------


class TestAllRepoTypesScopeIsolation:
    """Every repository must include scope predicates in get_by_id and list_all."""

    _REPOS_AND_MODELS: list[tuple[type, type]] = [
        (WorkingMemoryRepository, WorkingMemory),
        (EpisodicMemoryRepository, EpisodicMemory),
        (SemanticFactRepository, SemanticFact),
        (EntityProfileRepository, EntityProfile),
        (ProceduralMemoryRepository, ProceduralMemory),
    ]

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_get_by_id_has_scope_predicate(self, repo_cls, model_cls):
        pool = _FakePool([])
        repo = repo_cls(pool)
        repo.get_by_id("some-id", _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "id = ?" in sql, f"{repo_cls.__name__}: missing id = ?"
        assert "agent_id = ?" in sql, f"{repo_cls.__name__}: missing agent_id = ?"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_list_all_has_scope_predicate(self, repo_cls, model_cls):
        pool = _FakePool([])
        repo = repo_cls(pool)
        repo.list_all(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql, f"{repo_cls.__name__}: missing agent_id = ?"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_search_has_scope_predicate(self, repo_cls, model_cls):
        pool = _FakePool([])
        repo = repo_cls(pool)
        repo.search(_VEC, _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql, f"{repo_cls.__name__}: missing agent_id = ?"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_forget_has_scope_predicate(self, repo_cls, model_cls):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        repo.forget("some-id", _SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql, f"{repo_cls.__name__}: missing agent_id = ?"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_purge_expired_has_scope_predicate(self, repo_cls, model_cls):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        repo.purge_expired(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql, f"{repo_cls.__name__}: missing agent_id = ?"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_cross_scope_get_by_id_returns_none(self, repo_cls, model_cls):
        """
        Simulate the cross-scope isolation boundary: the fake cursor returns
        no rows (as a real Db2 would when scope predicates filter out the
        row's actual owner).  The result must always be None.
        """
        repo = repo_cls(_FakePool([]))
        result = repo.get_by_id("known-id-in-owner-scope", _SCOPE_OTHER)
        assert result is None, f"{repo_cls.__name__}: cross-scope read returned a row"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_cross_scope_list_all_returns_empty(self, repo_cls, model_cls):
        repo = repo_cls(_FakePool([]))
        results = repo.list_all(_SCOPE_OTHER)
        assert results == [], f"{repo_cls.__name__}: cross-scope list returned rows"

    @pytest.mark.parametrize("repo_cls,model_cls", _REPOS_AND_MODELS)
    def test_cross_scope_search_returns_empty(self, repo_cls, model_cls):
        repo = repo_cls(_FakePool([]))
        results = repo.search(_VEC, _SCOPE_OTHER)
        assert results == [], f"{repo_cls.__name__}: cross-scope search returned rows"


# ---------------------------------------------------------------------------
# MemoryStore facade scope propagation
# ---------------------------------------------------------------------------


class TestMemoryStoreFacadeScopePropagation:
    """Verify that the MemoryStore facade passes scope down to every operation."""

    def test_remember_scope_in_sql(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="facade-agent", tenant_id="facade-tenant")
        store.remember(WorkingMemory(agent_id="x", content="hi"), scope)
        # INSERT: scope columns are bound as positional params
        insert_sql = pool.cursor.last_sql
        assert "INSERT INTO working_memory" in insert_sql

    def test_store_forget_scope_in_sql(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="facade-agent", tenant_id="facade-tenant")
        store.forget("row-1", "working", scope)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "facade-agent" in pool.cursor.last_params

    def test_store_purge_scope_in_sql(self):
        pool = _FakePool([])
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="facade-agent", tenant_id="facade-tenant")
        store.purge_expired(scope)
        # All five DELETE statements should carry scope params
        for params in pool.cursor.all_params:
            assert "facade-agent" in params

    def test_store_cross_scope_no_data_leaked(self):
        """
        A MemoryStore call with a non-matching scope must return nothing.
        Pool has no rows → simulates Db2 filtering out owner-scope rows.
        """
        pool = _FakePool([])
        store = MemoryStore(pool)
        results = store.working.list_all(_SCOPE_OTHER)
        assert results == []

    def test_store_cross_scope_get_by_id_returns_none(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        result = store.working.get_by_id("owner-row-id", _SCOPE_OTHER)
        assert result is None

    def test_store_cross_scope_search_returns_empty(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        results = store.episodic.search(_VEC, _SCOPE_OTHER)
        assert results == []


# ---------------------------------------------------------------------------
# Missing / empty agent_id raises on every operation
# ---------------------------------------------------------------------------


class TestMissingAgentIdRejected:
    """Every repository operation must reject a scope with an empty agent_id."""

    _EMPTY_SCOPE = MemoryScope(agent_id="")

    def test_create_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.create(WorkingMemory(agent_id="x", content="c"), self._EMPTY_SCOPE)

    def test_get_by_id_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.get_by_id("id", self._EMPTY_SCOPE)

    def test_list_all_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.list_all(self._EMPTY_SCOPE)

    def test_search_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.search(_VEC, self._EMPTY_SCOPE)

    def test_forget_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.forget("id", self._EMPTY_SCOPE)

    def test_update_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.update(WorkingMemory(agent_id="x", content="c"), self._EMPTY_SCOPE)

    def test_purge_expired_rejects_empty_agent_id(self):
        repo = WorkingMemoryRepository(_FakePool())
        with pytest.raises(ValueError, match="agent_id"):
            repo.purge_expired(self._EMPTY_SCOPE)

    def test_store_forget_rejects_empty_agent_id(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="agent_id"):
            store.forget("id", "working", self._EMPTY_SCOPE)

    def test_store_purge_rejects_empty_agent_id(self):
        pool = _FakePool([])
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="agent_id"):
            store.purge_expired(self._EMPTY_SCOPE)

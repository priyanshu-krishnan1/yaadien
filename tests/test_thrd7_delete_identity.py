"""
tests/test_thrd7_delete_identity.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-7: ``MemoryStore.delete_user()`` / ``delete_agent()``.

No live Db2 instance is required — uses the same fake pool pattern as
test_pipe5_erasure.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ErasureReport

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as test_pipe5_erasure.py)
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
# TestDeleteUserCascadeTrue
# ---------------------------------------------------------------------------


class TestDeleteUserCascadeTrue:
    def test_calls_erase_all_with_correct_scope(self):
        """cascade=True must delegate to erase_all() with user_id set."""
        pool = _FakePool()
        pool.cursor.rowcount = 2
        store = MemoryStore(pool)

        captured: list[MemoryScope] = []
        original_erase_all = store.erase_all

        def _spy(scope: MemoryScope) -> ErasureReport:
            captured.append(scope)
            return original_erase_all(scope)

        store.erase_all = _spy  # type: ignore[method-assign]

        store.delete_user(user_id="user-42", agent_id="agent-001")

        assert len(captured) == 1
        assert captured[0].user_id == "user-42"
        assert captured[0].agent_id == "agent-001"

    def test_returns_erasure_report(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.delete_user(user_id="user-42", agent_id="agent-001")
        assert isinstance(report, ErasureReport)

    def test_report_has_all_six_tables(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.delete_user(user_id="user-42", agent_id="agent-001")
        assert set(report.rows_deleted.keys()) == {
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
            "memory_chunks",
        }

    def test_erased_at_is_set(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        before = datetime.now(timezone.utc)
        report = store.delete_user(user_id="user-42", agent_id="agent-001")
        after = datetime.now(timezone.utc)
        assert report.erased_at is not None
        assert before <= report.erased_at <= after

    def test_issues_six_deletes(self):
        """cascade=True must touch all 6 tables."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.delete_user(user_id="user-42", agent_id="agent-001")
        deletes = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(deletes) == 6


# ---------------------------------------------------------------------------
# TestDeleteUserCascadeFalse
# ---------------------------------------------------------------------------


class TestDeleteUserCascadeFalse:
    def test_only_removes_entity_profiles(self):
        """cascade=False must issue exactly one DELETE — on entity_profiles."""
        pool = _FakePool()
        pool.cursor.rowcount = 3
        store = MemoryStore(pool)
        store.delete_user(user_id="user-42", agent_id="agent-001", cascade=False)
        deletes = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(deletes) == 1
        assert "entity_profiles" in deletes[0]

    def test_returns_erasure_report_instance(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.delete_user(
            user_id="user-42", agent_id="agent-001", cascade=False
        )
        assert isinstance(report, ErasureReport)

    def test_only_entity_profiles_count_is_nonzero(self):
        pool = _FakePool()
        pool.cursor.rowcount = 5
        store = MemoryStore(pool)
        report = store.delete_user(
            user_id="user-42", agent_id="agent-001", cascade=False
        )
        assert report.rows_deleted["entity_profiles"] == 5
        assert report.rows_deleted["working_memory"] == 0
        assert report.rows_deleted["episodic_memory"] == 0
        assert report.rows_deleted["semantic_facts"] == 0
        assert report.rows_deleted["procedural_memory"] == 0
        assert report.rows_deleted["memory_chunks"] == 0
        assert report.total_deleted == 5

    def test_erased_at_is_set(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        before = datetime.now(timezone.utc)
        report = store.delete_user(
            user_id="user-42", agent_id="agent-001", cascade=False
        )
        after = datetime.now(timezone.utc)
        assert report.erased_at is not None
        assert before <= report.erased_at <= after


# ---------------------------------------------------------------------------
# TestDeleteAgentCascadeTrue
# ---------------------------------------------------------------------------


class TestDeleteAgentCascadeTrue:
    def test_calls_erase_all_with_agent_scope_no_user_id(self):
        """cascade=True must delegate to erase_all() with no user_id set."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)

        captured: list[MemoryScope] = []
        original_erase_all = store.erase_all

        def _spy(scope: MemoryScope) -> ErasureReport:
            captured.append(scope)
            return original_erase_all(scope)

        store.erase_all = _spy  # type: ignore[method-assign]

        store.delete_agent(agent_id="agent-001")

        assert len(captured) == 1
        assert captured[0].agent_id == "agent-001"
        assert captured[0].user_id is None

    def test_returns_erasure_report(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.delete_agent(agent_id="agent-001")
        assert isinstance(report, ErasureReport)

    def test_erased_at_is_set(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        before = datetime.now(timezone.utc)
        report = store.delete_agent(agent_id="agent-001")
        after = datetime.now(timezone.utc)
        assert report.erased_at is not None
        assert before <= report.erased_at <= after

    def test_issues_six_deletes(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.delete_agent(agent_id="agent-001")
        deletes = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(deletes) == 6


# ---------------------------------------------------------------------------
# TestDeleteAgentCascadeFalse
# ---------------------------------------------------------------------------


class TestDeleteAgentCascadeFalse:
    def test_only_removes_entity_profiles(self):
        pool = _FakePool()
        pool.cursor.rowcount = 7
        store = MemoryStore(pool)
        store.delete_agent(agent_id="agent-001", cascade=False)
        deletes = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(deletes) == 1
        assert "entity_profiles" in deletes[0]

    def test_returns_erasure_report_instance(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.delete_agent(agent_id="agent-001", cascade=False)
        assert isinstance(report, ErasureReport)

    def test_only_entity_profiles_count_is_nonzero(self):
        pool = _FakePool()
        pool.cursor.rowcount = 4
        store = MemoryStore(pool)
        report = store.delete_agent(agent_id="agent-001", cascade=False)
        assert report.rows_deleted["entity_profiles"] == 4
        assert report.rows_deleted["working_memory"] == 0
        assert report.total_deleted == 4

    def test_erased_at_is_set(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        before = datetime.now(timezone.utc)
        report = store.delete_agent(agent_id="agent-001", cascade=False)
        after = datetime.now(timezone.utc)
        assert report.erased_at is not None
        assert before <= report.erased_at <= after


# ---------------------------------------------------------------------------
# TestTenantIdPropagation
# ---------------------------------------------------------------------------


class TestTenantIdPropagation:
    def test_delete_user_passes_tenant_id_in_scope(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)

        captured: list[MemoryScope] = []
        original = store.erase_all

        def _spy(scope: MemoryScope) -> ErasureReport:
            captured.append(scope)
            return original(scope)

        store.erase_all = _spy  # type: ignore[method-assign]
        store.delete_user(
            user_id="user-42", agent_id="agent-001", tenant_id="tenant-A"
        )

        assert captured[0].tenant_id == "tenant-A"

    def test_delete_agent_passes_tenant_id_in_scope(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)

        captured: list[MemoryScope] = []
        original = store.erase_all

        def _spy(scope: MemoryScope) -> ErasureReport:
            captured.append(scope)
            return original(scope)

        store.erase_all = _spy  # type: ignore[method-assign]
        store.delete_agent(agent_id="agent-001", tenant_id="tenant-B")

        assert captured[0].tenant_id == "tenant-B"

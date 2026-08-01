"""
tests/test_pipe5_erasure.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for PIPE-5: ``MemoryStore.erase_all(scope) -> ErasureReport``.

Focus areas
-----------
1. ``BaseRepository.erase_all()`` — per-table hard-delete primitive.
   SQL structure (unconditional DELETE scoped only by *scope*, no
   ``deleted_at`` / ``expires_at`` gating), agent_id enforcement, and
   parametrized coverage across all five repository types.
2. ``ChunkRepository.erase_by_scope()`` — the ``memory_chunks`` equivalent.
3. ``MemoryStore.erase_all()`` facade — issues a DELETE on all six tables
   (five repositories + memory_chunks), returns an ``ErasureReport`` with
   correct per-table counts / total / timestamp, and never touches a scope
   other than the one requested (cross-scope isolation, matching the
   discipline established in test_scoping.py for forget()/purge_expired()).

No live Db2 instance is required — uses the same fake connection pool
pattern as test_lifecycle.py / test_scoping.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.repositories.chunks import ChunkRepository
from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ErasureReport

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as test_lifecycle.py / test_scoping.py)
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
# Shared fixtures
# ---------------------------------------------------------------------------

_SCOPE_OWNER = MemoryScope(
    tenant_id="tenant-A",
    agent_id="agent-owner",
    user_id="user-owner",
    thread_id="thread-owner",
)

_SCOPE_OTHER = MemoryScope(
    tenant_id="tenant-B",
    agent_id="agent-other",
    user_id="user-other",
    thread_id="thread-other",
)

_ALL_TABLE_REPOS: list[tuple[type, str]] = [
    (WorkingMemoryRepository, "working_memory"),
    (EpisodicMemoryRepository, "episodic_memory"),
    (SemanticFactRepository, "semantic_facts"),
    (EntityProfileRepository, "entity_profiles"),
    (ProceduralMemoryRepository, "procedural_memory"),
]


# ---------------------------------------------------------------------------
# BaseRepository.erase_all() — repository level
# ---------------------------------------------------------------------------


class TestEraseAllRepoLevel:
    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_issues_delete_on_correct_table(self, repo_cls, table_name):
        pool = _FakePool()
        pool.cursor.rowcount = 2
        repo = repo_cls(pool)
        count = repo.erase_all(_SCOPE_OWNER)
        assert f"DELETE FROM {table_name}" in pool.cursor.last_sql
        assert count == 2

    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_no_deleted_at_or_expires_at_gating(self, repo_cls, table_name):
        """erase_all() must bypass the tombstone lifecycle entirely — unlike
        purge_expired(), it must NOT restrict itself to already-tombstoned
        or expired rows."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        repo.erase_all(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "deleted_at" not in sql
        assert "expires_at" not in sql

    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_scope_predicate_present(self, repo_cls, table_name):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        repo.erase_all(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql
        assert "user_id = ?" in sql
        assert "thread_id = ?" in sql

    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_requires_agent_id(self, repo_cls, table_name):
        pool = _FakePool()
        repo = repo_cls(pool)
        with pytest.raises(ValueError, match="agent_id"):
            repo.erase_all(MemoryScope(agent_id=""))

    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_params_use_requesting_scope_not_another(self, repo_cls, table_name):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        repo.erase_all(_SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "agent-owner" not in params

    @pytest.mark.parametrize("repo_cls,table_name", _ALL_TABLE_REPOS)
    def test_returns_zero_when_nothing_matches(self, repo_cls, table_name):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = repo_cls(pool)
        result = repo.erase_all(_SCOPE_OWNER)
        assert result == 0


# ---------------------------------------------------------------------------
# ChunkRepository.erase_by_scope()
# ---------------------------------------------------------------------------


class TestChunkRepositoryEraseByScope:
    def test_issues_delete_on_memory_chunks(self):
        pool = _FakePool()
        pool.cursor.rowcount = 4
        repo = ChunkRepository(pool)
        count = repo.erase_by_scope(_SCOPE_OWNER)
        assert "DELETE FROM memory_chunks" in pool.cursor.last_sql
        assert count == 4

    def test_scope_predicate_present(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = ChunkRepository(pool)
        repo.erase_by_scope(_SCOPE_OWNER)
        sql = pool.cursor.last_sql
        assert "agent_id = ?" in sql
        assert "tenant_id = ?" in sql
        assert "user_id = ?" in sql
        assert "thread_id = ?" in sql

    def test_requires_agent_id(self):
        pool = _FakePool()
        repo = ChunkRepository(pool)
        with pytest.raises(ValueError, match="agent_id"):
            repo.erase_by_scope(MemoryScope(agent_id=""))

    def test_params_use_requesting_scope_not_another(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = ChunkRepository(pool)
        repo.erase_by_scope(_SCOPE_OTHER)
        params = pool.cursor.last_params
        assert "agent-other" in params
        assert "agent-owner" not in params

    def test_returns_zero_when_nothing_matches(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        repo = ChunkRepository(pool)
        result = repo.erase_by_scope(_SCOPE_OWNER)
        assert result == 0


# ---------------------------------------------------------------------------
# ErasureReport dataclass
# ---------------------------------------------------------------------------


class TestErasureReportDataclass:
    def test_defaults(self):
        report = ErasureReport()
        assert report.rows_deleted == {}
        assert report.total_deleted == 0
        assert report.erased_at is None

    def test_construct_with_values(self):
        now = datetime(2026, 8, 2, tzinfo=timezone.utc)
        report = ErasureReport(
            rows_deleted={"working_memory": 3, "memory_chunks": 1},
            total_deleted=4,
            erased_at=now,
        )
        assert report.rows_deleted["working_memory"] == 3
        assert report.total_deleted == 4
        assert report.erased_at == now


# ---------------------------------------------------------------------------
# MemoryStore.erase_all() — facade
# ---------------------------------------------------------------------------


class TestMemoryStoreEraseAll:
    def test_returns_erasure_report(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.erase_all(_SCOPE_OWNER)
        assert isinstance(report, ErasureReport)

    def test_report_has_all_six_tables(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        report = store.erase_all(_SCOPE_OWNER)
        assert set(report.rows_deleted.keys()) == {
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
            "memory_chunks",
        }

    def test_issues_delete_on_all_six_tables(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.erase_all(_SCOPE_OWNER)
        deletes = [s for s in pool.cursor.all_sqls if "DELETE FROM" in s]
        assert len(deletes) == 6
        joined = " ".join(deletes)
        for table in (
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
            "memory_chunks",
        ):
            assert table in joined

    def test_total_is_sum_of_all_six(self):
        pool = _FakePool()
        pool.cursor.rowcount = 3  # every DELETE reports 3 rows affected
        store = MemoryStore(pool)
        report = store.erase_all(_SCOPE_OWNER)
        assert report.total_deleted == 18  # 6 tables x 3
        assert sum(report.rows_deleted.values()) == report.total_deleted

    def test_erased_at_is_utc_datetime(self):
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        before = datetime.now(timezone.utc)
        report = store.erase_all(_SCOPE_OWNER)
        after = datetime.now(timezone.utc)
        assert isinstance(report.erased_at, datetime)
        assert before <= report.erased_at <= after

    def test_no_deleted_at_or_expires_at_gating_anywhere(self):
        """None of the six DELETEs may restrict to tombstoned/expired rows —
        erase_all() is a full bypass of the soft-delete lifecycle."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.erase_all(_SCOPE_OWNER)
        for sql in pool.cursor.all_sqls:
            assert "deleted_at" not in sql
            assert "expires_at" not in sql

    def test_requires_agent_id(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        with pytest.raises(ValueError, match="agent_id"):
            store.erase_all(MemoryScope(agent_id=""))

    def test_missing_agent_id_performs_no_deletes(self):
        """A rejected scope must not have caused any partial hard-delete."""
        pool = _FakePool()
        store = MemoryStore(pool)
        with pytest.raises(ValueError):
            store.erase_all(MemoryScope(agent_id=""))
        assert pool.cursor.all_sqls == []

    def test_cross_scope_params_do_not_leak(self):
        """Every DELETE issued for _SCOPE_OTHER must carry that scope's
        agent_id, never the owner scope's — the isolation boundary that
        matters even for an irreversible hard-delete operation."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)
        store.erase_all(_SCOPE_OTHER)
        for params in pool.cursor.all_params:
            assert "agent-other" in params
            assert "agent-owner" not in params

    def test_uses_existing_chunk_repo_when_chunking_enabled(self):
        """When the store was constructed with chunking enabled, erase_all()
        must reuse store.chunks rather than building a throwaway repo."""

        class _StubEmbedding:
            def __call__(self, text: str) -> list[float]:
                return [0.1] * 8

        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool, embedding_provider=_StubEmbedding(), embedding_dim=8)
        assert store.chunks is not None
        report = store.erase_all(_SCOPE_OWNER)
        assert "memory_chunks" in report.rows_deleted

    def test_reaches_memory_chunks_even_when_chunking_disabled(self):
        """erase_all() must still hard-delete memory_chunks rows even when
        this MemoryStore instance has no active ChunkRepository (e.g. legacy
        rows from an earlier configuration with chunking enabled)."""
        pool = _FakePool()
        pool.cursor.rowcount = 0
        store = MemoryStore(pool)  # no embedding_provider -> store.chunks is None
        assert store.chunks is None
        store.erase_all(_SCOPE_OWNER)
        assert any("DELETE FROM memory_chunks" in s for s in pool.cursor.all_sqls)

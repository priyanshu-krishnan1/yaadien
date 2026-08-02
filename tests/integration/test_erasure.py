"""
tests/integration/test_erasure.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Live-Db2 integration tests for PIPE-5: erase_all() / ErasureReport accuracy.

LIVE-10 — Epic-10

Verifies:
  - ErasureReport.rows_deleted counts match pre-call raw COUNT(*) per table
  - Hard delete: every targeted row is actually gone (SELECT after returns 0)
  - Scope-safety: a sibling scope's rows are untouched
  - delete_thread() (THRD-6 thin wrapper over erase_all) respects thread scoping
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_TABLES = (
    "working_memory",
    "episodic_memory",
    "semantic_facts",
    "entity_profiles",
    "procedural_memory",
    "memory_chunks",
)


def _count(pool, table: str, agent_id: str, user_id: str | None = None) -> int:
    """Return the live row count in *table* for the given scope (including soft-deleted rows)."""
    if user_id:
        sql = f"SELECT COUNT(*) FROM {table} WHERE agent_id = ? AND user_id = ?"  # noqa: S608
        params = (agent_id, user_id)
    else:
        sql = f"SELECT COUNT(*) FROM {table} WHERE agent_id = ?"  # noqa: S608
        params = (agent_id,)
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _count_thread(pool, table: str, agent_id: str, thread_id: str) -> int:
    """Count rows scoped to a specific thread_id (incl. all users)."""
    sql = (
        f"SELECT COUNT(*) FROM {table} "  # noqa: S608
        "WHERE agent_id = ? AND thread_id = ?"
    )
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, (agent_id, thread_id))
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEraseAllCountsAndHardDelete:
    """ErasureReport counts match reality and rows are truly gone after the call."""

    @pytest.fixture()
    def primary_scope(self, unique_agent_id):
        from agent_memory_sdk.models import MemoryScope

        return MemoryScope(agent_id=unique_agent_id, user_id="erase-user-1")

    @pytest.fixture()
    def seeded(self, store, migrated_pool, primary_scope):
        """Seed rows across all six tables in primary_scope; return expected counts."""
        from agent_memory_sdk.models import (
            EntityProfile,
            EpisodicMemory,
            ProceduralMemory,
            SemanticFact,
            WorkingMemory,
        )
        from agent_memory_sdk.repositories.chunks import ChunkRepository

        dim = 1536
        zero = [0.0] * dim

        # 2 WorkingMemory
        store.working.create(
            WorkingMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="wm-1"),
            primary_scope,
        )
        store.working.create(
            WorkingMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="wm-2"),
            primary_scope,
        )
        # 2 EpisodicMemory
        store.episodic.create(
            EpisodicMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="ep-1"),
            primary_scope,
        )
        store.episodic.create(
            EpisodicMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="ep-2"),
            primary_scope,
        )
        # 2 SemanticFact
        store.facts.create(
            SemanticFact(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="sf-1"),
            primary_scope,
        )
        store.facts.create(
            SemanticFact(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="sf-2"),
            primary_scope,
        )
        # 1 EntityProfile
        store.profiles.create(
            EntityProfile(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="ep-profile"),
            primary_scope,
        )
        # 1 ProceduralMemory
        store.procedures.create(
            ProceduralMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content="proc-1"),
            primary_scope,
        )
        # 2 chunks
        chunk_repo = ChunkRepository(pool=migrated_pool, embedding_dim=dim)
        source_id = str(uuid.uuid4())
        chunk_repo.insert_chunk(
            source_table="semantic_facts",
            source_id=source_id,
            chunk_index=0,
            chunk_text="chunk text 0",
            embedding=zero,
            scope=primary_scope,
        )
        chunk_repo.insert_chunk(
            source_table="semantic_facts",
            source_id=source_id,
            chunk_index=1,
            chunk_text="chunk text 1",
            embedding=zero,
            scope=primary_scope,
        )
        return {
            "working_memory": 2,
            "episodic_memory": 2,
            "semantic_facts": 2,
            "entity_profiles": 1,
            "procedural_memory": 1,
            "memory_chunks": 2,
        }

    def test_report_counts_match_pre_call_counts(self, store, migrated_pool, primary_scope, seeded):
        """ErasureReport.rows_deleted for every table matches independently counted rows."""
        # Verify pre-call counts match what seeded fixture says
        for table, expected in seeded.items():
            actual = _count(migrated_pool, table, primary_scope.agent_id, primary_scope.user_id)
            assert actual == expected, f"Pre-call count mismatch in {table}: expected {expected}, got {actual}"

        report = store.erase_all(primary_scope)

        for table, expected in seeded.items():
            assert report.rows_deleted[table] == expected, (
                f"ErasureReport mismatch in {table}: expected {expected}, got {report.rows_deleted[table]}"
            )

    def test_report_total_matches_sum(self, store, migrated_pool, primary_scope, seeded):
        """ErasureReport.total_deleted equals the sum of all per-table counts."""
        report = store.erase_all(primary_scope)
        expected_total = sum(seeded.values())
        assert report.total_deleted == expected_total

    def test_erased_at_is_a_datetime(self, store, migrated_pool, primary_scope, seeded):
        """ErasureReport.erased_at is a datetime, not None."""
        from datetime import datetime

        report = store.erase_all(primary_scope)
        assert isinstance(report.erased_at, datetime)

    def test_rows_truly_gone_after_erase(self, store, migrated_pool, primary_scope, seeded):
        """Hard delete: every targeted row is gone from Db2 (not just tombstoned)."""
        store.erase_all(primary_scope)

        for table in _ALL_TABLES:
            count = _count(migrated_pool, table, primary_scope.agent_id, primary_scope.user_id)
            assert count == 0, f"Expected 0 rows in {table} after erase_all, got {count}"

    def test_sdk_list_also_returns_empty_after_erase(self, store, migrated_pool, primary_scope, seeded):
        """SDK-layer verification: list_all() returns nothing after erase_all."""
        store.erase_all(primary_scope)
        assert store.working.list_all(primary_scope) == []
        assert store.facts.list_all(primary_scope) == []


class TestEraseAllScopeSafety:
    """Sibling scope rows must be completely untouched after erase_all()."""

    def test_sibling_scope_rows_intact(self, store, migrated_pool, unique_agent_id):
        """Rows in a sibling scope are not affected by erase_all on the primary scope."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory

        primary_scope = MemoryScope(agent_id=unique_agent_id + "-primary", user_id="user-primary")
        sibling_scope = MemoryScope(agent_id=unique_agent_id + "-sibling", user_id="user-sibling")

        # Seed both scopes
        for i in range(3):
            store.working.create(
                WorkingMemory(agent_id=primary_scope.agent_id, user_id=primary_scope.user_id, content=f"primary-wm-{i}"),
                primary_scope,
            )
        for i in range(4):
            store.facts.create(
                SemanticFact(agent_id=sibling_scope.agent_id, user_id=sibling_scope.user_id, content=f"sibling-sf-{i}"),
                sibling_scope,
            )

        # Erase primary only
        store.erase_all(primary_scope)

        # Primary should be empty
        assert _count(migrated_pool, "working_memory", primary_scope.agent_id) == 0
        # Sibling should be intact
        sibling_count = _count(migrated_pool, "semantic_facts", sibling_scope.agent_id)
        assert sibling_count == 4, f"Expected 4 sibling rows, got {sibling_count}"
        assert len(store.facts.list_all(sibling_scope)) == 4


class TestDeleteThread:
    """delete_thread() is a thin wrapper over erase_all() scoped to a thread_id."""

    def test_delete_thread_erases_correct_thread_only(self, store, migrated_pool, unique_agent_id):
        """delete_thread(scope_with_thread_id) removes only that thread's rows."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory

        thread_1_scope = MemoryScope(
            agent_id=unique_agent_id,
            user_id="user-t",
            thread_id="thread-del-1",
        )
        thread_2_scope = MemoryScope(
            agent_id=unique_agent_id,
            user_id="user-t",
            thread_id="thread-del-2",
        )

        # Seed both threads
        for i in range(2):
            store.working.create(
                WorkingMemory(agent_id=unique_agent_id, user_id="user-t", thread_id="thread-del-1", content=f"t1-wm-{i}"),
                thread_1_scope,
            )
            store.facts.create(
                SemanticFact(agent_id=unique_agent_id, user_id="user-t", thread_id="thread-del-2", content=f"t2-sf-{i}"),
                thread_2_scope,
            )

        # Delete thread-1 only
        report = store.delete_thread(thread_1_scope)
        assert report.total_deleted >= 2

        # Thread-1 working memory gone
        assert _count_thread(migrated_pool, "working_memory", unique_agent_id, "thread-del-1") == 0
        # Thread-2 facts intact
        assert _count_thread(migrated_pool, "semantic_facts", unique_agent_id, "thread-del-2") == 2

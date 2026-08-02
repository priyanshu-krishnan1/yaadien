"""
tests/integration/test_thread_facade.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-15: Thread facade end-to-end.

Covers:
  TestCreateThread        — create_thread returns Thread; schema-less by default; add_messages writes rows
  TestGetThread           — get_thread re-opens existing thread; returns empty handle for nonexistent
  TestDeleteThread        — delete_thread hard-deletes all rows for a scope
  TestThreadPassThroughMethods  — Each pass-through method (add_messages, get_messages, etc.)
  TestScopeCorrectness    — Thread scope isolation; delete one thread does not affect another

All tests are skipped automatically when DB2_DATABASE is not set.
Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers for raw DB queries
# ---------------------------------------------------------------------------


def _count_working_memory(pool, agent_id: str, thread_id: str | None) -> int:
    """Count WorkingMemory rows for agent_id and optional thread_id."""
    sql = "SELECT COUNT(*) FROM working_memory WHERE agent_id = ?"
    params = [agent_id]
    if thread_id:
        sql += " AND thread_id = ?"
        params.append(thread_id)
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _count_semantic_facts(pool, agent_id: str, thread_id: str | None) -> int:
    """Count SemanticFact rows for agent_id and optional thread_id."""
    sql = "SELECT COUNT(*) FROM semantic_facts WHERE agent_id = ?"
    params = [agent_id]
    if thread_id:
        sql += " AND thread_id = ?"
        params.append(thread_id)
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _count_all_memory_tables(pool, agent_id: str, thread_id: str) -> dict[str, int]:
    """Count rows in all memory tables for a given scope."""
    tables = ["working_memory", "episodic_memory", "semantic_facts", "entity_profiles", "procedural_memory"]
    counts = {}
    for table in tables:
        sql = f"SELECT COUNT(*) FROM {table} WHERE agent_id = ? AND thread_id = ?"
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, [agent_id, thread_id])
            row = cur.fetchone()
        counts[table] = int(row[0]) if row else 0
    return counts


# ---------------------------------------------------------------------------
# TestCreateThread
# ---------------------------------------------------------------------------


class TestCreateThread:
    """LIVE-15: create_thread returns Thread; schema-less by default."""

    def test_create_thread_returns_thread_object(self, store, unique_agent_id):
        """Call store.create_thread() — assert a Thread object is returned."""
        from agent_memory_sdk.thread import Thread

        thread = store.create_thread(
            thread_id="thread-xyz",
            agent_id=unique_agent_id,
        )

        assert isinstance(thread, Thread)
        assert thread.scope.thread_id == "thread-xyz"
        assert thread.scope.agent_id == unique_agent_id

    def test_create_thread_is_schema_less_no_rows_initially(
        self, store, migrated_pool, unique_agent_id
    ):
        """Thread is schema-less by default: no rows exist yet."""
        store.create_thread(
            thread_id="thread-xyz",
            agent_id=unique_agent_id,
        )

        # Verify zero rows in all memory tables for this thread
        counts = _count_all_memory_tables(migrated_pool, unique_agent_id, "thread-xyz")
        for table, count in counts.items():
            assert count == 0, f"Expected 0 rows in {table}, found {count}"

    def test_create_thread_add_messages_writes_real_rows(self, store, unique_agent_id):
        """add_messages writes real WorkingMemory rows scoped to thread."""
        thread = store.create_thread(
            thread_id="thread-xyz",
            agent_id=unique_agent_id,
        )

        ids = thread.add_messages(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            extract_memories=False,  # Don't trigger extraction; we just want messages
        )

        assert len(ids) == 2
        assert all(isinstance(id_, str) and id_ for id_ in ids)

    def test_create_thread_get_messages_returns_added_rows(self, store, unique_agent_id):
        """get_messages returns the messages that were written."""
        thread = store.create_thread(
            thread_id="thread-xyz",
            agent_id=unique_agent_id,
        )

        ids = thread.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
            extract_memories=False,
        )

        messages = thread.get_messages()

        assert len(messages) == 2
        assert messages[0].id == ids[0]
        assert messages[0].content == "msg1"
        assert messages[1].id == ids[1]
        assert messages[1].content == "msg2"


# ---------------------------------------------------------------------------
# TestGetThread
# ---------------------------------------------------------------------------


class TestGetThread:
    """LIVE-15: get_thread re-opens existing thread; returns empty for nonexistent."""

    def test_get_thread_reopens_existing_thread(self, store, unique_agent_id):
        """Write rows via thread facade, then re-open and read back."""
        # Create and write
        thread_1 = store.create_thread(
            thread_id="thread-abc",
            agent_id=unique_agent_id,
        )
        ids = thread_1.add_messages(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
            extract_memories=False,
        )

        # Re-open
        thread_2 = store.get_thread(
            thread_id="thread-abc",
            agent_id=unique_agent_id,
        )

        messages = thread_2.get_messages()

        assert len(messages) == 2
        assert messages[0].id == ids[0]
        assert messages[0].content == "hello"
        assert messages[1].id == ids[1]
        assert messages[1].content == "world"

    def test_get_thread_returns_empty_handle_for_nonexistent_thread(
        self, store, unique_agent_id
    ):
        """get_thread('nonexistent-thread', agent_id) returns empty Thread without raising."""
        thread = store.get_thread(
            thread_id="nonexistent-thread",
            agent_id=unique_agent_id,
        )

        # Must return a Thread handle
        assert thread is not None
        assert thread.scope.thread_id == "nonexistent-thread"

        # But it has no messages
        messages = thread.get_messages()
        assert len(messages) == 0


# ---------------------------------------------------------------------------
# TestDeleteThread
# ---------------------------------------------------------------------------


class TestDeleteThread:
    """LIVE-15: delete_thread hard-deletes all rows for a scope."""

    def test_delete_thread_returns_erasure_report(self, store, unique_agent_id):
        """delete_thread returns ErasureReport."""
        from agent_memory_sdk.types import ErasureReport

        thread = store.create_thread(
            thread_id="thread-to-delete",
            agent_id=unique_agent_id,
        )
        thread.add_messages(
            [{"role": "user", "content": "message"}],
            extract_memories=False,
        )

        report = store.delete_thread(thread.scope)

        assert isinstance(report, ErasureReport)
        assert report.erased_at is not None

    def test_delete_thread_erases_all_rows(
        self, store, migrated_pool, unique_agent_id
    ):
        """All thread rows are gone after delete_thread."""
        thread = store.create_thread(
            thread_id="thread-to-delete",
            agent_id=unique_agent_id,
        )
        thread.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
            extract_memories=False,
        )

        # Verify rows exist before delete
        counts_before = _count_all_memory_tables(
            migrated_pool, unique_agent_id, "thread-to-delete"
        )
        assert counts_before["working_memory"] > 0, "Expected messages to be written"

        # Delete
        store.delete_thread(thread.scope)

        # Verify all rows are gone
        counts_after = _count_all_memory_tables(
            migrated_pool, unique_agent_id, "thread-to-delete"
        )
        for table, count in counts_after.items():
            assert count == 0, f"Expected 0 rows in {table} after delete, found {count}"


# ---------------------------------------------------------------------------
# TestThreadPassThroughMethods
# ---------------------------------------------------------------------------


class TestThreadPassThroughMethods:
    """LIVE-15: Each pass-through method delegates to store correctly."""

    def test_add_messages_writes_to_db(self, store, migrated_pool, unique_agent_id):
        """add_messages writes WorkingMemory rows."""
        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )

        ids = thread.add_messages(
            [{"role": "user", "content": "hello"}],
            extract_memories=False,
        )

        assert len(ids) == 1
        count = _count_working_memory(migrated_pool, unique_agent_id, "thread-methods")
        assert count == 1

    def test_get_messages_returns_messages(self, store, unique_agent_id):
        """get_messages returns the added messages."""
        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )
        thread.add_messages(
            [{"role": "user", "content": "test msg"}],
            extract_memories=False,
        )

        messages = thread.get_messages()

        assert len(messages) == 1
        assert messages[0].content == "test msg"

    def test_add_memory_writes_to_db(self, store, migrated_pool, unique_agent_id):
        """add_memory writes SemanticFact rows."""
        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )

        fact_id = thread.add_memory("Important fact")

        assert isinstance(fact_id, str) and fact_id
        count = _count_semantic_facts(migrated_pool, unique_agent_id, "thread-methods")
        assert count == 1

    def test_get_summary_returns_summary(self, store, unique_agent_id):
        """get_summary returns a Summary with message_count > 0."""
        from agent_memory_sdk.types import Summary

        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )
        thread.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
            extract_memories=False,
        )

        summary = thread.get_summary()

        assert isinstance(summary, Summary)
        assert summary.message_count == 2

    def test_get_context_card_returns_context_card(self, store, unique_agent_id):
        """get_context_card returns a ContextCard with turn_count > 0."""
        from agent_memory_sdk.types import ContextCard

        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )
        thread.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
            extract_memories=False,
        )

        card = thread.get_context_card()

        assert isinstance(card, ContextCard)
        assert card.turn_count == 2

    def test_delete_message_soft_deletes(self, store, unique_agent_id):
        """delete_message returns 1 (one row affected)."""
        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )
        ids = thread.add_messages(
            [{"role": "user", "content": "to delete"}],
            extract_memories=False,
        )

        result = thread.delete_message(ids[0])

        assert result == 1

    def test_delete_memory_soft_deletes(self, store, unique_agent_id):
        """delete_memory returns 1 (one row affected)."""
        thread = store.create_thread(
            thread_id="thread-methods",
            agent_id=unique_agent_id,
        )
        fact_id = thread.add_memory("Fact to delete")

        result = thread.delete_memory(fact_id)

        assert result == 1


# ---------------------------------------------------------------------------
# TestScopeCorrectness
# ---------------------------------------------------------------------------


class TestScopeCorrectness:
    """LIVE-15: Thread scope isolation; operations on one thread don't affect another."""

    def test_two_threads_same_agent_isolated(self, store, unique_agent_id):
        """Create thread-1 and thread-2; write only to thread-1; thread-2 is empty."""
        thread_1 = store.create_thread(
            thread_id="thread-1",
            agent_id=unique_agent_id,
        )
        thread_2 = store.create_thread(
            thread_id="thread-2",
            agent_id=unique_agent_id,
        )

        # Write only to thread-1
        thread_1.add_messages(
            [{"role": "user", "content": "msg for thread-1"}],
            extract_memories=False,
        )

        # Thread-2 must still be empty
        messages_t2 = thread_2.get_messages()
        assert len(messages_t2) == 0

    def test_delete_thread_1_does_not_affect_thread_2(
        self, store, migrated_pool, unique_agent_id
    ):
        """Delete thread-1; verify thread-2's data survives."""
        thread_1 = store.create_thread(
            thread_id="thread-1",
            agent_id=unique_agent_id,
        )
        thread_2 = store.create_thread(
            thread_id="thread-2",
            agent_id=unique_agent_id,
        )

        # Write to both
        thread_1.add_messages(
            [{"role": "user", "content": "thread-1 msg"}],
            extract_memories=False,
        )
        ids_2 = thread_2.add_messages(
            [{"role": "user", "content": "thread-2 msg"}],
            extract_memories=False,
        )

        # Delete thread-1
        store.delete_thread(thread_1.scope)

        # Thread-2's data must survive
        messages_t2 = thread_2.get_messages()
        assert len(messages_t2) == 1
        assert messages_t2[0].id == ids_2[0]
        assert messages_t2[0].content == "thread-2 msg"

        # Verify counts
        count_t1 = _count_working_memory(migrated_pool, unique_agent_id, "thread-1")
        count_t2 = _count_working_memory(migrated_pool, unique_agent_id, "thread-2")
        assert count_t1 == 0, "Thread-1 should have no rows after delete"
        assert count_t2 == 1, "Thread-2 should have 1 row"

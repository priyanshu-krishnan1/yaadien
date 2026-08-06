"""
tests/test_thrd6_thread.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-6: Thread facade — bound-scope convenience object.

Coverage:
  1.  Thread instantiation and scope property
  2.  Thread.add_messages() delegates to store with correct scope
  3.  Thread.get_messages() delegates with start/end slicing
  4.  Thread.delete_message() delegates correctly
  5.  Thread.add_memory() delegates correctly (content stored in facts)
  6.  Thread.delete_memory() delegates correctly
  7.  Thread.search() delegates correctly (needs embedding_provider)
  8.  Thread.get_summary() delegates correctly
  9.  Thread.get_context_card() delegates correctly
  10. store.create_thread() returns a Thread with correct scope bound
  11. store.get_thread() returns a Thread with correct scope bound
  12. store.delete_thread() calls erase_all() (verified via SQL)
  13. Thread is exported from agent_memory_sdk
  14. __repr__ includes agent_id, user_id, thread_id
  15. Thread handle for empty thread (no rows) is returned without error

No live Db2 instance required — uses a fake pool and MagicMock store.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import agent_memory_sdk
from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.thread import Thread
from agent_memory_sdk.types import ContextCard, ErasureReport, SearchResult, Summary

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(
    agent_id="agent-thrd6",
    user_id="user-42",
    thread_id="t-1",
    tenant_id="ten1",
)


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fake DB infrastructure (same pattern as test_thrd1_messages.py)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows: list[tuple[Any, ...]] = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 1
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


def _make_store(rows: list[tuple[Any, ...]] | None = None) -> tuple[MemoryStore, _FakePool]:
    """Return a (store, pool) pair backed by a fake pool."""
    pool = _FakePool(rows)
    store = MemoryStore(pool)
    return store, pool


def _working_row(
    id_: str,
    content: str = "msg",
    agent_id: str = "agent-thrd6",
    created_at: datetime = _NOW,
    deleted_at: Any = None,
    metadata: dict | None = None,
) -> tuple[Any, ...]:
    """Build a fake 16-column DB row for working_memory."""
    return (
        id_, "ten1", agent_id, "user-42", "t-1",
        content, json.dumps(metadata or {}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        created_at, created_at, None, 1, deleted_at,
        "DIRECT_WRITE",  # origin (TRU-1)
        None,  # consolidated_at (ENH-4)
    )


def _fact_row(
    id_: str,
    content: str = "a fact",
    agent_id: str = "agent-thrd6",
) -> tuple[Any, ...]:
    """Build a fake 15-column DB row for semantic_facts."""
    return (
        id_, "ten1", agent_id, "user-42", "t-1",
        content, json.dumps({}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        _NOW, _NOW, None, 1, None,
    )


# ---------------------------------------------------------------------------
# Helper: build a Thread backed by a MagicMock store
# ---------------------------------------------------------------------------

def _make_mock_thread(scope: MemoryScope = _SCOPE) -> tuple[Thread, MagicMock]:
    """Return (thread, mock_store) — no real DB needed."""
    mock_store = MagicMock(spec=MemoryStore)
    thread = Thread(mock_store, scope)
    return thread, mock_store


# ---------------------------------------------------------------------------
# 1. Thread instantiation and scope property
# ---------------------------------------------------------------------------


class TestThreadInstantiation:
    def test_scope_property_returns_bound_scope(self):
        """Thread.scope returns exactly the MemoryScope passed at construction."""
        thread, _ = _make_mock_thread(_SCOPE)
        assert thread.scope is _SCOPE

    def test_scope_fields_match(self):
        """Bound scope carries the exact field values from construction."""
        thread, _ = _make_mock_thread(_SCOPE)
        assert thread.scope.agent_id == "agent-thrd6"
        assert thread.scope.user_id == "user-42"
        assert thread.scope.thread_id == "t-1"
        assert thread.scope.tenant_id == "ten1"

    def test_thread_stores_store_reference(self):
        """Thread keeps a reference to the backing store."""
        mock_store = MagicMock(spec=MemoryStore)
        thread = Thread(mock_store, _SCOPE)
        assert thread._store is mock_store


# ---------------------------------------------------------------------------
# 2. Thread.add_messages() delegates to store with correct scope
# ---------------------------------------------------------------------------


class TestThreadAddMessages:
    def test_delegates_to_store_add_messages(self):
        """Thread.add_messages() calls store.add_messages() with the bound scope."""
        thread, mock_store = _make_mock_thread()
        mock_store.add_messages.return_value = ["id-1", "id-2"]

        messages = [{"content": "Hello"}, {"content": "World"}]
        result = thread.add_messages(messages)

        mock_store.add_messages.assert_called_once_with(
            messages, _SCOPE, extract_memories=True
        )
        assert result == ["id-1", "id-2"]

    def test_extract_memories_false_forwarded(self):
        """extract_memories=False is forwarded to store.add_messages."""
        thread, mock_store = _make_mock_thread()
        mock_store.add_messages.return_value = ["id-a"]

        thread.add_messages([{"content": "msg"}], extract_memories=False)

        mock_store.add_messages.assert_called_once_with(
            [{"content": "msg"}], _SCOPE, extract_memories=False
        )

    def test_returns_ids_from_store(self):
        """The list of IDs returned by the store is passed through unchanged."""
        thread, mock_store = _make_mock_thread()
        expected = ["uuid-1", "uuid-2", "uuid-3"]
        mock_store.add_messages.return_value = expected

        result = thread.add_messages([{"content": "a"}, {"content": "b"}, {"content": "c"}])
        assert result == expected

    def test_add_messages_real_store_inserts(self):
        """Integration: add_messages via Thread reaches working_memory INSERT."""
        store, pool = _make_store()
        thread = Thread(store, _SCOPE)
        ids = thread.add_messages([{"content": "Hello from thread"}])

        assert isinstance(ids, list)
        assert len(ids) == 1
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert insert_sqls


# ---------------------------------------------------------------------------
# 3. Thread.get_messages() delegates with start/end slicing
# ---------------------------------------------------------------------------


class TestThreadGetMessages:
    def test_delegates_with_defaults(self):
        """Thread.get_messages() passes scope and default start/end to store."""
        thread, mock_store = _make_mock_thread()
        mock_store.get_messages.return_value = []

        thread.get_messages()

        mock_store.get_messages.assert_called_once_with(_SCOPE, start=0, end=None)

    def test_start_end_forwarded(self):
        """start and end parameters are forwarded correctly."""
        thread, mock_store = _make_mock_thread()
        mock_store.get_messages.return_value = []

        thread.get_messages(start=2, end=5)

        mock_store.get_messages.assert_called_once_with(_SCOPE, start=2, end=5)

    def test_returns_store_result(self):
        """get_messages() passes through what the store returns."""
        thread, mock_store = _make_mock_thread()
        wm = WorkingMemory(agent_id="agent-thrd6", content="hi")
        mock_store.get_messages.return_value = [wm]

        result = thread.get_messages()
        assert result == [wm]

    def test_real_store_returns_chronological_order(self):
        """Integration: get_messages via Thread returns oldest-first."""
        _T1 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        _T2 = datetime(2026, 9, 1, 10, 1, 0, tzinfo=timezone.utc)
        rows = [
            _working_row("id-2", content="second", created_at=_T2),
            _working_row("id-1", content="first", created_at=_T1),
        ]
        store, _ = _make_store(rows=rows)
        thread = Thread(store, _SCOPE)

        msgs = thread.get_messages()
        assert [m.id for m in msgs] == ["id-1", "id-2"]

    def test_empty_thread_returns_empty_list(self):
        """Thread handle over an empty thread returns [] from get_messages()."""
        store, _ = _make_store(rows=[])
        thread = Thread(store, _SCOPE)
        msgs = thread.get_messages()
        assert msgs == []


# ---------------------------------------------------------------------------
# 4. Thread.delete_message() delegates correctly
# ---------------------------------------------------------------------------


class TestThreadDeleteMessage:
    def test_delegates_with_scope(self):
        """Thread.delete_message() passes message_id and bound scope to store."""
        thread, mock_store = _make_mock_thread()
        mock_store.delete_message.return_value = 1

        result = thread.delete_message("msg-uuid-abc")

        mock_store.delete_message.assert_called_once_with("msg-uuid-abc", _SCOPE)
        assert result == 1

    def test_returns_0_when_not_found(self):
        """Returns 0 when the store returns 0 (message not found)."""
        thread, mock_store = _make_mock_thread()
        mock_store.delete_message.return_value = 0

        result = thread.delete_message("unknown-id")
        assert result == 0

    def test_real_store_issues_update(self):
        """Integration: delete_message via Thread issues UPDATE on working_memory."""
        store, pool = _make_store()
        pool.cursor.rowcount = 1
        thread = Thread(store, _SCOPE)

        result = thread.delete_message("some-msg-id")
        assert result == 1
        assert any("UPDATE working_memory" in s for s in pool.cursor.all_sqls)


# ---------------------------------------------------------------------------
# 5. Thread.add_memory() delegates correctly (content stored in facts)
# ---------------------------------------------------------------------------


class TestThreadAddMemory:
    def test_delegates_to_store_add_memory(self):
        """Thread.add_memory() calls store.add_memory with bound scope."""
        thread, mock_store = _make_mock_thread()
        mock_store.add_memory.return_value = "fact-uuid-1"

        result = thread.add_memory("User prefers dark mode")

        mock_store.add_memory.assert_called_once_with(
            "User prefers dark mode", _SCOPE, memory_id=None, metadata=None
        )
        assert result == "fact-uuid-1"

    def test_memory_id_forwarded(self):
        """memory_id keyword argument is forwarded correctly."""
        thread, mock_store = _make_mock_thread()
        mock_store.add_memory.return_value = "my-id"

        thread.add_memory("some fact", memory_id="my-id")

        mock_store.add_memory.assert_called_once_with(
            "some fact", _SCOPE, memory_id="my-id", metadata=None
        )

    def test_metadata_forwarded(self):
        """metadata keyword argument is forwarded correctly."""
        thread, mock_store = _make_mock_thread()
        mock_store.add_memory.return_value = "x"

        thread.add_memory("fact", metadata={"source": "test"})

        mock_store.add_memory.assert_called_once_with(
            "fact", _SCOPE, memory_id=None, metadata={"source": "test"}
        )

    def test_real_store_inserts_into_semantic_facts(self):
        """Integration: add_memory via Thread produces INSERT INTO semantic_facts."""
        store, pool = _make_store()
        thread = Thread(store, _SCOPE)
        result = thread.add_memory("User prefers Python")

        assert isinstance(result, str)
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert insert_sqls


# ---------------------------------------------------------------------------
# 6. Thread.delete_memory() delegates correctly
# ---------------------------------------------------------------------------


class TestThreadDeleteMemory:
    def test_delegates_to_store_delete_memory(self):
        """Thread.delete_memory() calls store.delete_memory with bound scope."""
        thread, mock_store = _make_mock_thread()
        mock_store.delete_memory.return_value = 1

        result = thread.delete_memory("fact-uuid-abc")

        mock_store.delete_memory.assert_called_once_with("fact-uuid-abc", _SCOPE)
        assert result == 1

    def test_returns_0_when_not_found(self):
        """Returns 0 when the store reports the record was not found."""
        thread, mock_store = _make_mock_thread()
        mock_store.delete_memory.return_value = 0

        result = thread.delete_memory("nope")
        assert result == 0


# ---------------------------------------------------------------------------
# 7. Thread.search() delegates correctly (needs embedding_provider)
# ---------------------------------------------------------------------------


class TestThreadSearch:
    def _make_search_thread(self) -> tuple[Thread, MagicMock]:
        """Thread backed by a mock store that supports search()."""
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.search.return_value = []
        return Thread(mock_store, _SCOPE), mock_store

    def test_delegates_with_scope_and_query(self):
        """Thread.search() calls store.search() with the bound scope."""
        thread, mock_store = self._make_search_thread()

        thread.search("user preferences")

        mock_store.search.assert_called_once_with(
            "user preferences",
            _SCOPE,
            record_types=None,
            max_results=10,
            metadata_filter=None,
        )

    def test_record_types_forwarded(self):
        """record_types argument is forwarded to store.search()."""
        thread, mock_store = self._make_search_thread()

        thread.search("query", record_types=["facts", "profiles"])

        mock_store.search.assert_called_once_with(
            "query",
            _SCOPE,
            record_types=["facts", "profiles"],
            max_results=10,
            metadata_filter=None,
        )

    def test_max_results_forwarded(self):
        """max_results argument is forwarded to store.search()."""
        thread, mock_store = self._make_search_thread()

        thread.search("query", max_results=5)

        mock_store.search.assert_called_once_with(
            "query",
            _SCOPE,
            record_types=None,
            max_results=5,
            metadata_filter=None,
        )

    def test_metadata_filter_forwarded(self):
        """metadata_filter argument is forwarded to store.search()."""
        thread, mock_store = self._make_search_thread()

        thread.search("query", metadata_filter={"role": "user"})

        mock_store.search.assert_called_once_with(
            "query",
            _SCOPE,
            record_types=None,
            max_results=10,
            metadata_filter={"role": "user"},
        )

    def test_returns_search_results(self):
        """Thread.search() passes through the SearchResult list."""
        thread, mock_store = self._make_search_thread()
        sr = SearchResult(id="x", content="y", record_type="facts", distance=0.1, record=None)
        mock_store.search.return_value = [sr]

        result = thread.search("something")
        assert result == [sr]

    def test_real_store_with_embedding_provider(self):
        """Integration: search via Thread with an embedding_provider configured."""
        _VEC = [0.1] * 1536
        embedding_provider = lambda text: _VEC  # noqa: E731
        pool = _FakePool()
        store = MemoryStore(pool, embedding_provider=embedding_provider, enable_chunking=False)
        # Mock repo.search() to return controlled results
        fact = SemanticFact(agent_id="agent-thrd6", content="a fact")
        fact.thread_id = "t-1"
        store.facts.search = MagicMock(return_value=[fact])
        store.working.search = MagicMock(return_value=[])
        store.episodic.search = MagicMock(return_value=[])
        store.profiles.search = MagicMock(return_value=[])
        store.procedures.search = MagicMock(return_value=[])

        thread = Thread(store, _SCOPE)
        results = thread.search("user preferences", record_types=["facts"])

        assert len(results) == 1
        assert results[0].record_type == "facts"
        assert results[0].content == "a fact"


# ---------------------------------------------------------------------------
# 8. Thread.get_summary() delegates correctly
# ---------------------------------------------------------------------------


class TestThreadGetSummary:
    def test_delegates_with_scope_and_defaults(self):
        """Thread.get_summary() calls store.get_summary() with bound scope."""
        thread, mock_store = _make_mock_thread()
        summary = Summary(content="", message_count=0, truncated=False)
        mock_store.get_summary.return_value = summary

        result = thread.get_summary()

        mock_store.get_summary.assert_called_once_with(
            _SCOPE, except_last=0, token_budget=None
        )
        assert result is summary

    def test_except_last_forwarded(self):
        """except_last is forwarded to store.get_summary()."""
        thread, mock_store = _make_mock_thread()
        mock_store.get_summary.return_value = Summary(content="", message_count=0, truncated=False)

        thread.get_summary(except_last=3)

        mock_store.get_summary.assert_called_once_with(
            _SCOPE, except_last=3, token_budget=None
        )

    def test_token_budget_forwarded(self):
        """token_budget is forwarded to store.get_summary()."""
        thread, mock_store = _make_mock_thread()
        mock_store.get_summary.return_value = Summary(content="", message_count=0, truncated=False)

        thread.get_summary(token_budget=500)

        mock_store.get_summary.assert_called_once_with(
            _SCOPE, except_last=0, token_budget=500
        )

    def test_real_store_empty_thread(self):
        """Integration: get_summary on empty thread returns empty Summary."""
        store, _ = _make_store(rows=[])
        thread = Thread(store, _SCOPE)

        summary = thread.get_summary()
        assert isinstance(summary, Summary)
        assert summary.content == ""
        assert summary.message_count == 0
        assert summary.truncated is False


# ---------------------------------------------------------------------------
# 9. Thread.get_context_card() delegates correctly
# ---------------------------------------------------------------------------


class TestThreadGetContextCard:
    def test_delegates_with_scope_and_defaults(self):
        """Thread.get_context_card() calls store.get_context_card() with bound scope."""
        thread, mock_store = _make_mock_thread()
        card = ContextCard()
        mock_store.get_context_card.return_value = card

        result = thread.get_context_card()

        mock_store.get_context_card.assert_called_once_with(
            _SCOPE,
            max_turns=20,
            query=None,
            include_long_term=False,
            min_results_by_type=None,
            long_term_top_k=5,
        )
        assert result is card

    def test_all_kwargs_forwarded(self):
        """All keyword arguments are forwarded to store.get_context_card()."""
        thread, mock_store = _make_mock_thread()
        card = ContextCard()
        mock_store.get_context_card.return_value = card

        thread.get_context_card(
            max_turns=10,
            query="what did the user want?",
            include_long_term=True,
            min_results_by_type={"facts": 2},
            long_term_top_k=3,
        )

        mock_store.get_context_card.assert_called_once_with(
            _SCOPE,
            max_turns=10,
            query="what did the user want?",
            include_long_term=True,
            min_results_by_type={"facts": 2},
            long_term_top_k=3,
        )

    def test_real_store_empty_thread_returns_card(self):
        """Integration: get_context_card on empty thread returns a valid ContextCard."""
        store, _ = _make_store(rows=[])
        thread = Thread(store, _SCOPE)

        card = thread.get_context_card()
        assert isinstance(card, ContextCard)
        assert card.turns == []
        assert card.turn_count == 0
        assert card.latest_at is None


# ---------------------------------------------------------------------------
# 10. store.create_thread() returns a Thread with correct scope
# ---------------------------------------------------------------------------


class TestStoreCreateThread:
    def test_returns_thread_instance(self):
        """create_thread() returns a Thread object."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="t-100", agent_id="agent-A")
        assert isinstance(thread, Thread)

    def test_scope_agent_id_set(self):
        """create_thread() binds the correct agent_id."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="t-1", agent_id="agent-Z")
        assert thread.scope.agent_id == "agent-Z"

    def test_scope_thread_id_set(self):
        """create_thread() binds the correct thread_id."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="my-thread", agent_id="agent-X")
        assert thread.scope.thread_id == "my-thread"

    def test_scope_user_id_set(self):
        """create_thread() propagates user_id when provided."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="t-2", agent_id="ag-1", user_id="u-99")
        assert thread.scope.user_id == "u-99"

    def test_scope_tenant_id_set(self):
        """create_thread() propagates tenant_id when provided."""
        store, _ = _make_store()
        thread = store.create_thread(
            thread_id="t-3", agent_id="ag-1", tenant_id="tenant-42"
        )
        assert thread.scope.tenant_id == "tenant-42"

    def test_scope_optional_fields_default_none(self):
        """Without optional args, user_id and tenant_id are None."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="t-bare", agent_id="ag-bare")
        assert thread.scope.user_id is None
        assert thread.scope.tenant_id is None

    def test_store_reference_is_correct(self):
        """Thread._store is the same MemoryStore that created it."""
        store, _ = _make_store()
        thread = store.create_thread(thread_id="t-ref", agent_id="ag-ref")
        assert thread._store is store


# ---------------------------------------------------------------------------
# 11. store.get_thread() returns a Thread with correct scope
# ---------------------------------------------------------------------------


class TestStoreGetThread:
    def test_returns_thread_instance(self):
        """get_thread() returns a Thread object."""
        store, _ = _make_store()
        thread = store.get_thread(thread_id="t-existing", agent_id="agent-B")
        assert isinstance(thread, Thread)

    def test_scope_matches_arguments(self):
        """get_thread() constructs scope from the provided arguments."""
        store, _ = _make_store()
        thread = store.get_thread(
            thread_id="existing-t",
            agent_id="ag-b",
            user_id="usr-7",
            tenant_id="ten-3",
        )
        assert thread.scope.thread_id == "existing-t"
        assert thread.scope.agent_id == "ag-b"
        assert thread.scope.user_id == "usr-7"
        assert thread.scope.tenant_id == "ten-3"

    def test_empty_thread_no_error(self):
        """get_thread() for a thread with no rows does not raise."""
        store, _ = _make_store(rows=[])
        thread = store.get_thread(thread_id="ghost-thread", agent_id="ag-ghost")
        # Should silently return an empty Thread handle
        msgs = thread.get_messages()
        assert msgs == []

    def test_store_reference_is_correct(self):
        """Thread._store is the same MemoryStore that created it."""
        store, _ = _make_store()
        thread = store.get_thread(thread_id="t-ref2", agent_id="ag-ref2")
        assert thread._store is store


# ---------------------------------------------------------------------------
# 12. store.delete_thread() calls erase_all() (verified via SQL)
# ---------------------------------------------------------------------------


class TestStoreDeleteThread:
    def test_returns_erasure_report(self):
        """delete_thread() returns an ErasureReport."""
        store, _ = _make_store()
        scope = MemoryScope(agent_id="agent-del", thread_id="t-del")
        report = store.delete_thread(scope)
        assert isinstance(report, ErasureReport)

    def test_issues_delete_sql_for_working_memory(self):
        """delete_thread() issues a DELETE FROM working_memory SQL statement."""
        store, pool = _make_store()
        scope = MemoryScope(agent_id="agent-del2", thread_id="t-del2")
        store.delete_thread(scope)
        assert any("DELETE FROM working_memory" in s for s in pool.cursor.all_sqls)

    def test_issues_delete_for_all_tables(self):
        """delete_thread() touches all five memory tables (delegates to erase_all)."""
        store, pool = _make_store()
        scope = MemoryScope(agent_id="agent-del3", thread_id="t-del3")
        store.delete_thread(scope)
        all_sqls_joined = " ".join(pool.cursor.all_sqls)
        assert "working_memory" in all_sqls_joined
        assert "episodic_memory" in all_sqls_joined
        assert "semantic_facts" in all_sqls_joined
        assert "entity_profiles" in all_sqls_joined
        assert "procedural_memory" in all_sqls_joined

    def test_report_has_correct_structure(self):
        """ErasureReport has rows_deleted, total_deleted, erased_at."""
        store, _ = _make_store()
        scope = MemoryScope(agent_id="agent-del4")
        report = store.delete_thread(scope)
        assert isinstance(report.rows_deleted, dict)
        assert isinstance(report.total_deleted, int)
        assert report.erased_at is not None

    def test_delegates_to_erase_all(self):
        """delete_thread() is a thin wrapper over erase_all()."""
        store, _ = _make_store()
        scope = MemoryScope(agent_id="ag-wrap")

        with patch.object(store, "erase_all", wraps=store.erase_all) as mock_erase:
            store.delete_thread(scope)
            mock_erase.assert_called_once_with(scope)


# ---------------------------------------------------------------------------
# 13. Thread is exported from agent_memory_sdk
# ---------------------------------------------------------------------------


class TestThreadExport:
    def test_thread_in_module(self):
        """Thread is importable from agent_memory_sdk top-level."""
        assert hasattr(agent_memory_sdk, "Thread")

    def test_thread_in_all(self):
        """Thread is listed in agent_memory_sdk.__all__."""
        assert "Thread" in agent_memory_sdk.__all__

    def test_is_correct_class(self):
        """agent_memory_sdk.Thread is the same class as agent_memory_sdk.thread.Thread."""
        from agent_memory_sdk.thread import Thread as DirectThread

        assert agent_memory_sdk.Thread is DirectThread


# ---------------------------------------------------------------------------
# 14. __repr__ includes agent_id, user_id, thread_id
# ---------------------------------------------------------------------------


class TestThreadRepr:
    def test_repr_contains_agent_id(self):
        """Thread.__repr__() contains the agent_id."""
        thread, _ = _make_mock_thread(_SCOPE)
        assert "agent-thrd6" in repr(thread)

    def test_repr_contains_user_id(self):
        """Thread.__repr__() contains the user_id."""
        thread, _ = _make_mock_thread(_SCOPE)
        assert "user-42" in repr(thread)

    def test_repr_contains_thread_id(self):
        """Thread.__repr__() contains the thread_id."""
        thread, _ = _make_mock_thread(_SCOPE)
        assert "t-1" in repr(thread)

    def test_repr_format(self):
        """Thread.__repr__() has the expected 'Thread(agent_id=..., ...)' shape."""
        thread, _ = _make_mock_thread(_SCOPE)
        r = repr(thread)
        assert r.startswith("Thread(")
        assert "agent_id=" in r
        assert "user_id=" in r
        assert "thread_id=" in r

    def test_repr_none_fields(self):
        """Repr handles None user_id and thread_id gracefully."""
        scope = MemoryScope(agent_id="ag-bare2")
        thread, _ = _make_mock_thread(scope)
        r = repr(thread)
        assert "None" in r  # user_id and thread_id are None


# ---------------------------------------------------------------------------
# 15. Thread handle for empty thread (no rows) returned without error
# ---------------------------------------------------------------------------


class TestEmptyThreadHandle:
    def test_create_thread_no_error_no_rows(self):
        """create_thread() succeeds even when the thread has no existing rows."""
        store, _ = _make_store(rows=[])
        thread = store.create_thread(thread_id="brand-new", agent_id="ag-new")
        assert isinstance(thread, Thread)

    def test_get_thread_no_error_no_rows(self):
        """get_thread() succeeds even when the thread has no existing rows."""
        store, _ = _make_store(rows=[])
        thread = store.get_thread(thread_id="brand-new", agent_id="ag-new")
        assert isinstance(thread, Thread)

    def test_get_messages_empty_thread(self):
        """get_messages() on empty thread returns []."""
        store, _ = _make_store(rows=[])
        thread = store.get_thread(thread_id="empty", agent_id="ag-empty")
        assert thread.get_messages() == []

    def test_get_summary_empty_thread(self):
        """get_summary() on empty thread returns empty Summary."""
        store, _ = _make_store(rows=[])
        thread = store.get_thread(thread_id="empty2", agent_id="ag-empty2")
        summary = thread.get_summary()
        assert summary.message_count == 0

    def test_get_context_card_empty_thread(self):
        """get_context_card() on empty thread returns ContextCard with no turns."""
        store, _ = _make_store(rows=[])
        thread = store.get_thread(thread_id="empty3", agent_id="ag-empty3")
        card = thread.get_context_card()
        assert card.turn_count == 0
        assert card.turns == []

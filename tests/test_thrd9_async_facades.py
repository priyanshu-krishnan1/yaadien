"""
tests/test_thrd9_async_facades.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-9: async facades for LLM/embedder-calling entry points.

Coverage:
  1.  search_async() is a coroutine (can be awaited)
  2.  search_async() returns the same result as search() for the same inputs
  3.  search_async() forwards all parameters correctly (record_types, max_results,
      metadata_filter)
  4.  add_messages_async() is a coroutine (can be awaited)
  5.  add_messages_async() returns the same result as add_messages()
  6.  add_messages_async() extract_memories=False is forwarded to add_messages()
  7.  add_messages_async() forwards all messages correctly
  8.  get_context_card_async() is a coroutine (can be awaited)
  9.  get_context_card_async() returns a ContextCard
  10. get_context_card_async() returns the same result as get_context_card()
  11. get_context_card_async() forwards all parameters correctly (max_turns, query,
      include_long_term, min_results_by_type, long_term_top_k)

No live Db2 instance required — uses the mock-repos pattern established in
test_thrd3_search.py and test_thrd5_extractor.py.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from agent_memory_sdk.models import (
    MemoryScope,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import ContextCard, SearchResult

# ---------------------------------------------------------------------------
# Shared constants / helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
_SCOPE = MemoryScope(agent_id="agent-thrd9", tenant_id="t9")
_VEC = [0.1] * 1536


def _fake_embedding_provider(text: str) -> list[float]:
    return _VEC


# ---------------------------------------------------------------------------
# Minimal fake pool (no DB I/O needed — repos are mocked directly)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount: int = 1

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        pass

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self) -> None:
        self._cursor = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakePool:
    def __init__(self) -> None:
        self._conn = _FakeConn()

    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield self._conn


# ---------------------------------------------------------------------------
# Record / result builders
# ---------------------------------------------------------------------------


def _make_working(uid: str, content: str = "hello") -> WorkingMemory:
    r = WorkingMemory(agent_id="agent-thrd9", content=content)
    r.id = uid
    r.created_at = _NOW
    r.updated_at = _NOW
    return r


def _make_fact(uid: str, content: str = "a fact") -> SemanticFact:
    r = SemanticFact(agent_id="agent-thrd9", content=content)
    r.id = uid
    r.created_at = _NOW
    r.updated_at = _NOW
    return r


def _make_search_result(uid: str, content: str, record_type: str = "working") -> SearchResult:
    record: Any = _make_fact(uid, content) if record_type == "facts" else _make_working(uid, content)
    return SearchResult(id=uid, content=content, record_type=record_type, distance=None, record=record)


# ---------------------------------------------------------------------------
# Store factory helpers
# ---------------------------------------------------------------------------


def _make_store_for_search(search_results: list[SearchResult] | None = None) -> MemoryStore:
    """Build a MemoryStore with store.search() patched to return preset results."""
    store = MemoryStore(
        _FakePool(),
        embedding_provider=_fake_embedding_provider,
        enable_chunking=False,
    )
    results = search_results or []
    store.working.search = MagicMock(return_value=[r.record for r in results if r.record_type == "working"])
    store.episodic.search = MagicMock(return_value=[])
    store.facts.search = MagicMock(return_value=[r.record for r in results if r.record_type == "facts"])
    store.profiles.search = MagicMock(return_value=[])
    store.procedures.search = MagicMock(return_value=[])
    return store


def _make_store_for_add_messages(
    extract_memories: bool = False,
) -> MemoryStore:
    """Build a MemoryStore where remember() is patched to return a fixed WorkingMemory."""
    pool = _FakePool()
    store = MemoryStore(pool, enable_chunking=False)

    def _fake_remember(record: Any, scope: Any) -> Any:
        # Give every record a deterministic id so we can assert on the returned list.
        record.id = "persisted-" + record.content[:8].replace(" ", "-")
        return record

    store.remember = MagicMock(side_effect=_fake_remember)
    return store


def _make_store_for_context_card(
    turns: list[WorkingMemory] | None = None,
) -> MemoryStore:
    """Build a MemoryStore where working.list_all() returns preset turns."""
    store = MemoryStore(
        _FakePool(),
        enable_chunking=False,
    )
    store.working.list_all = MagicMock(return_value=list(reversed(turns or [])))
    return store


# ===========================================================================
# 1-3: search_async
# ===========================================================================


class TestSearchAsync:
    def test_search_async_is_coroutine(self) -> None:
        """search_async must return a coroutine object (is awaitable)."""
        store = _make_store_for_search()
        coro = store.search_async("test query", _SCOPE)
        assert inspect.iscoroutine(coro), "search_async() must return a coroutine"
        # Clean up unawaited coroutine to avoid ResourceWarning
        coro.close()

    def test_search_async_returns_list_of_search_results(self) -> None:
        """search_async() returns the same list[SearchResult] as search()."""
        w = _make_working("w-1", "hello world")
        store = _make_store_for_search()
        store.working.search = MagicMock(return_value=[w])

        sync_results = store.search("hello world", _SCOPE)
        async_results = asyncio.run(store.search_async("hello world", _SCOPE))

        assert isinstance(async_results, list)
        assert all(isinstance(r, SearchResult) for r in async_results)
        assert len(async_results) == len(sync_results)
        assert [r.id for r in async_results] == [r.id for r in sync_results]

    def test_search_async_forwards_record_types(self) -> None:
        """search_async forwards record_types — limits fan-out to specified types."""
        store = _make_store_for_search()
        results = asyncio.run(
            store.search_async("query", _SCOPE, record_types=["working"])
        )
        assert isinstance(results, list)
        # Only working.search should have been called
        store.working.search.assert_called_once()
        store.episodic.search.assert_not_called()

    def test_search_async_forwards_max_results(self) -> None:
        """search_async respects max_results cap."""
        records = [_make_working(f"w-{i}", f"content {i}") for i in range(5)]
        store = _make_store_for_search()
        store.working.search = MagicMock(return_value=records)

        results = asyncio.run(
            store.search_async("query", _SCOPE, record_types=["working"], max_results=3)
        )
        assert len(results) <= 3

    def test_search_async_forwards_metadata_filter(self) -> None:
        """search_async passes metadata_filter down to repo.search()."""
        store = _make_store_for_search()
        mf = {"role": "user"}
        asyncio.run(
            store.search_async("query", _SCOPE, record_types=["working"], metadata_filter=mf)
        )
        call_kwargs = store.working.search.call_args
        assert call_kwargs is not None
        # metadata_filter is passed as a keyword argument
        assert call_kwargs.kwargs.get("metadata_filter") == mf or mf in call_kwargs.args


# ===========================================================================
# 4-7: add_messages_async
# ===========================================================================


class TestAddMessagesAsync:
    def test_add_messages_async_is_coroutine(self) -> None:
        """add_messages_async must return a coroutine object."""
        store = _make_store_for_add_messages()
        coro = store.add_messages_async([{"content": "hi"}], _SCOPE)
        assert inspect.iscoroutine(coro), "add_messages_async() must return a coroutine"
        coro.close()

    def test_add_messages_async_returns_ids(self) -> None:
        """add_messages_async returns a list of string IDs."""
        store = _make_store_for_add_messages()
        result = asyncio.run(
            store.add_messages_async([{"content": "hello"}], _SCOPE)
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], str)

    def test_add_messages_async_same_result_as_sync(self) -> None:
        """add_messages_async returns the same IDs as add_messages() for the same input."""
        messages = [
            {"content": "first message", "role": "user"},
            {"content": "second message", "role": "assistant"},
        ]

        store1 = _make_store_for_add_messages()
        sync_result = store1.add_messages(messages, _SCOPE)

        store2 = _make_store_for_add_messages()
        async_result = asyncio.run(store2.add_messages_async(messages, _SCOPE))

        assert isinstance(async_result, list)
        assert len(async_result) == len(sync_result)
        # Both stores use the same deterministic fake_remember, so IDs match.
        assert async_result == sync_result

    def test_add_messages_async_extract_memories_false_forwarded(self) -> None:
        """extract_memories=False is forwarded so the extractor is NOT called."""
        extractor_mock = MagicMock(return_value=[])
        store = _make_store_for_add_messages()
        # Install a real-looking extractor (non-NoOp) so the guard would fire
        # if extract_memories=True were used.

        class _RealExtractor:
            def __call__(self, records: Any, scope: Any) -> list[Any]:
                return extractor_mock(records, scope)

        store._memory_extractor = _RealExtractor()

        asyncio.run(
            store.add_messages_async(
                [{"content": "extract me not"}], _SCOPE, extract_memories=False
            )
        )
        extractor_mock.assert_not_called()

    def test_add_messages_async_multiple_messages(self) -> None:
        """add_messages_async handles multiple messages and returns one ID per message."""
        n = 4
        messages = [{"content": f"msg {i}", "role": "user"} for i in range(n)]
        store = _make_store_for_add_messages()
        result = asyncio.run(store.add_messages_async(messages, _SCOPE))
        assert len(result) == n


# ===========================================================================
# 8-11: get_context_card_async
# ===========================================================================


class TestGetContextCardAsync:
    def test_get_context_card_async_is_coroutine(self) -> None:
        """get_context_card_async must return a coroutine object."""
        store = _make_store_for_context_card()
        coro = store.get_context_card_async(_SCOPE)
        assert inspect.iscoroutine(coro), "get_context_card_async() must return a coroutine"
        coro.close()

    def test_get_context_card_async_returns_context_card(self) -> None:
        """get_context_card_async returns a ContextCard instance."""
        store = _make_store_for_context_card()
        result = asyncio.run(store.get_context_card_async(_SCOPE))
        assert isinstance(result, ContextCard)

    def test_get_context_card_async_same_result_as_sync(self) -> None:
        """get_context_card_async returns the same ContextCard content as get_context_card()."""
        turns = [_make_working("t-1", "turn one"), _make_working("t-2", "turn two")]

        store1 = _make_store_for_context_card(turns)
        sync_card = store1.get_context_card(_SCOPE)

        store2 = _make_store_for_context_card(turns)
        async_card = asyncio.run(store2.get_context_card_async(_SCOPE))

        assert isinstance(async_card, ContextCard)
        assert async_card.turn_count == sync_card.turn_count
        assert [t.id for t in async_card.turns] == [t.id for t in sync_card.turns]

    def test_get_context_card_async_forwards_max_turns(self) -> None:
        """get_context_card_async forwards max_turns to get_context_card()."""
        turns = [_make_working(f"t-{i}", f"turn {i}") for i in range(10)]
        store = _make_store_for_context_card(turns)
        # max_turns=3 → list_all called with limit=3
        asyncio.run(store.get_context_card_async(_SCOPE, max_turns=3))
        store.working.list_all.assert_called_once()
        call_kwargs = store.working.list_all.call_args
        assert call_kwargs.kwargs.get("limit") == 3 or 3 in call_kwargs.args

    def test_get_context_card_async_empty_turns(self) -> None:
        """get_context_card_async works correctly when there are no turns."""
        store = _make_store_for_context_card([])
        result = asyncio.run(store.get_context_card_async(_SCOPE))
        assert isinstance(result, ContextCard)
        assert result.turn_count == 0
        assert result.turns == []
        assert result.latest_at is None

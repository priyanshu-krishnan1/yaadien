"""
tests/test_thrd3_search.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-3: MemoryStore.search() — raw-text fan-out facade.

No live Db2 needed — uses a per-call queued fake pool identical to the
pattern established in test_pipe4_context_card_v2.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import SearchResult

# ---------------------------------------------------------------------------
# Fake pool — per-call queued rows (same pattern as test_pipe4_context_card_v2)
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-thrd3")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_VEC = [0.1] * 1536


class _FakeCursor:
    def __init__(self, call_returns: list[list[tuple[Any, ...]]] | None = None) -> None:
        self._queue: list[list[tuple[Any, ...]]] = list(call_returns or [])
        self._current: list[tuple[Any, ...]] = []
        self.rowcount: int = 0
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.all_sqls.append(sql)
        self.all_params.append(list(params) if params else [])
        self._current = self._queue.pop(0) if self._queue else []
        self.rowcount = len(self._current)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._current[0] if self._current else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._current)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass


class _FakePool:
    def __init__(self, call_returns: list[list[tuple[Any, ...]]] | None = None) -> None:
        self.cursor = _FakeCursor(call_returns)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield self.conn


# ---------------------------------------------------------------------------
# Row builders for each model type
# Columns: id, tenant_id, agent_id, user_id, thread_id, content, metadata,
#          embedding, confidence, content_hash, created_at, updated_at,
#          expires_at, version, deleted_at
# (WorkingMemory / EpisodicMemory also have consolidated_at at index 15)
# ---------------------------------------------------------------------------

_META = "{}"
_HASH = "abc123"
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"


def _working_row(uid: str, content: str) -> tuple[Any, ...]:
    return (
        uid, None, "agent-thrd3", None, None,
        content, _META, _VEC_STR,
        1.0, _HASH, _NOW, _NOW, None, 1, None,
        None,  # consolidated_at
    )


def _episodic_row(uid: str, content: str) -> tuple[Any, ...]:
    return (
        uid, None, "agent-thrd3", None, None,
        content, _META, _VEC_STR,
        1.0, _HASH, _NOW, _NOW, None, 1, None,
        None,  # consolidated_at
    )


def _fact_row(uid: str, content: str) -> tuple[Any, ...]:
    # SemanticFact has extra supersession columns; _model_from_row handles them
    # by index — the SELECT_COLS are the first 15 columns for non-superseded repos.
    return (
        uid, None, "agent-thrd3", None, None,
        content, _META, _VEC_STR,
        1.0, _HASH, _NOW, _NOW, None, 1, None,
    )


def _profile_row(uid: str, content: str) -> tuple[Any, ...]:
    return (
        uid, None, "agent-thrd3", None, None,
        content, _META, _VEC_STR,
        1.0, _HASH, _NOW, _NOW, None, 1, None,
    )


def _procedure_row(uid: str, content: str) -> tuple[Any, ...]:
    return (
        uid, None, "agent-thrd3", None, None,
        content, _META, _VEC_STR,
        1.0, _HASH, _NOW, _NOW, None, 1, None,
    )


# ---------------------------------------------------------------------------
# Helper: build a store whose per-type repo.search() is mocked to return
# controlled lists of model instances directly (bypasses DB entirely).
# ---------------------------------------------------------------------------

def _make_store_with_mocked_repos(
    working_results: list | None = None,
    episodic_results: list | None = None,
    facts_results: list | None = None,
    profiles_results: list | None = None,
    procedures_results: list | None = None,
    embedding_provider: Any = None,
) -> MemoryStore:
    """Build a MemoryStore with fake pool and mocked repo.search() returns."""
    pool = _FakePool()  # no DB calls needed — repos are mocked below
    if embedding_provider is None:
        embedding_provider = lambda text: _VEC  # noqa: E731

    store = MemoryStore(pool, embedding_provider=embedding_provider, enable_chunking=False)

    store.working.search = MagicMock(return_value=working_results or [])
    store.episodic.search = MagicMock(return_value=episodic_results or [])
    store.facts.search = MagicMock(return_value=facts_results or [])
    store.profiles.search = MagicMock(return_value=profiles_results or [])
    store.procedures.search = MagicMock(return_value=procedures_results or [])

    return store


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_working(uid: str, content: str) -> WorkingMemory:
    r = WorkingMemory(agent_id="agent-thrd3", content=content)
    r.id = uid
    return r


def _make_fact(uid: str, content: str) -> SemanticFact:
    r = SemanticFact(agent_id="agent-thrd3", content=content)
    r.id = uid
    return r


def _make_episodic(uid: str, content: str) -> EpisodicMemory:
    r = EpisodicMemory(agent_id="agent-thrd3", content=content)
    r.id = uid
    return r


def _make_profile(uid: str, content: str) -> EntityProfile:
    r = EntityProfile(agent_id="agent-thrd3", content=content)
    r.id = uid
    return r


def _make_procedure(uid: str, content: str) -> ProceduralMemory:
    r = ProceduralMemory(agent_id="agent-thrd3", content=content)
    r.id = uid
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSearchResultDataclass:
    """SearchResult dataclass basic checks."""

    def test_fields(self):
        rec = _make_fact("f1", "hello")
        sr = SearchResult(id="f1", content="hello", record_type="facts", distance=None, record=rec)
        assert sr.id == "f1"
        assert sr.content == "hello"
        assert sr.record_type == "facts"
        assert sr.distance is None
        assert sr.record is rec

    def test_distance_can_be_float(self):
        rec = _make_fact("f2", "world")
        sr = SearchResult(id="f2", content="world", record_type="facts", distance=0.25, record=rec)
        assert sr.distance == 0.25


class TestSearchValidation:
    """Validation errors raised before any DB/embedding call."""

    def test_no_embedding_provider_raises(self):
        pool = _FakePool()
        store = MemoryStore(pool, enable_chunking=False)  # no embedding_provider
        with pytest.raises(ValueError, match="embedding_provider"):
            store.search("hello", _SCOPE)

    def test_empty_query_raises(self):
        store = _make_store_with_mocked_repos()
        with pytest.raises(ValueError, match="non-empty query"):
            store.search("", _SCOPE)

    def test_whitespace_only_query_raises(self):
        store = _make_store_with_mocked_repos()
        with pytest.raises(ValueError, match="non-empty query"):
            store.search("   ", _SCOPE)

    def test_unknown_record_type_raises(self):
        store = _make_store_with_mocked_repos()
        with pytest.raises(ValueError, match="Unknown record_type"):
            store.search("hello", _SCOPE, record_types=["working", "bogus"])

    def test_unknown_record_type_message_contains_name(self):
        store = _make_store_with_mocked_repos()
        with pytest.raises(ValueError, match="'bogus'"):
            store.search("hello", _SCOPE, record_types=["bogus"])


class TestSearchFanOut:
    """search() fans out to the correct repositories."""

    def test_all_five_repos_called_by_default(self):
        store = _make_store_with_mocked_repos()
        store.search("hello", _SCOPE)
        store.working.search.assert_called_once()
        store.episodic.search.assert_called_once()
        store.facts.search.assert_called_once()
        store.profiles.search.assert_called_once()
        store.procedures.search.assert_called_once()

    def test_only_facts_repo_called_when_restricted(self):
        store = _make_store_with_mocked_repos()
        store.search("hello", _SCOPE, record_types=["facts"])
        store.working.search.assert_not_called()
        store.episodic.search.assert_not_called()
        store.facts.search.assert_called_once()
        store.profiles.search.assert_not_called()
        store.procedures.search.assert_not_called()

    def test_working_and_facts_only(self):
        store = _make_store_with_mocked_repos()
        store.search("hello", _SCOPE, record_types=["working", "facts"])
        store.working.search.assert_called_once()
        store.episodic.search.assert_not_called()
        store.facts.search.assert_called_once()
        store.profiles.search.assert_not_called()
        store.procedures.search.assert_not_called()

    def test_embedding_called_once(self):
        calls = []
        def provider(text: str) -> list[float]:
            calls.append(text)
            return _VEC

        store = _make_store_with_mocked_repos(embedding_provider=provider)
        store.search("my query", _SCOPE)
        assert calls == ["my query"]  # exactly once, regardless of how many types


class TestSearchResults:
    """Correct SearchResult objects are built from repo records."""

    def test_returns_searchresult_objects(self):
        f1 = _make_fact("fact-1", "Paris is the capital of France")
        store = _make_store_with_mocked_repos(facts_results=[f1])
        results = store.search("capital", _SCOPE, record_types=["facts"])
        assert len(results) == 1
        sr = results[0]
        assert isinstance(sr, SearchResult)
        assert sr.id == "fact-1"
        assert sr.content == "Paris is the capital of France"
        assert sr.record_type == "facts"
        assert sr.distance is None
        assert sr.record is f1

    def test_record_type_label_matches_requested_type(self):
        w1 = _make_working("w-1", "session content")
        store = _make_store_with_mocked_repos(working_results=[w1])
        results = store.search("session", _SCOPE, record_types=["working"])
        assert results[0].record_type == "working"

    def test_results_from_multiple_types_combined(self):
        w1 = _make_working("w-1", "working content")
        f1 = _make_fact("f-1", "fact content")
        store = _make_store_with_mocked_repos(working_results=[w1], facts_results=[f1])
        results = store.search("content", _SCOPE, record_types=["working", "facts"])
        ids = [r.id for r in results]
        assert "w-1" in ids
        assert "f-1" in ids

    def test_ordering_is_working_episodic_facts_profiles_procedures(self):
        w1 = _make_working("w-1", "a")
        e1 = _make_episodic("e-1", "b")
        f1 = _make_fact("f-1", "c")
        p1 = _make_profile("p-1", "d")
        pr1 = _make_procedure("pr-1", "e")
        store = _make_store_with_mocked_repos(
            working_results=[w1],
            episodic_results=[e1],
            facts_results=[f1],
            profiles_results=[p1],
            procedures_results=[pr1],
        )
        results = store.search("query", _SCOPE, max_results=10)
        assert [r.id for r in results] == ["w-1", "e-1", "f-1", "p-1", "pr-1"]

    def test_all_five_type_labels_present(self):
        store = _make_store_with_mocked_repos(
            working_results=[_make_working("w-1", "w")],
            episodic_results=[_make_episodic("e-1", "e")],
            facts_results=[_make_fact("f-1", "f")],
            profiles_results=[_make_profile("p-1", "p")],
            procedures_results=[_make_procedure("pr-1", "pr")],
        )
        results = store.search("query", _SCOPE, max_results=10)
        types = [r.record_type for r in results]
        assert types == ["working", "episodic", "facts", "profiles", "procedures"]


class TestSearchMaxResults:
    """max_results truncation."""

    def test_max_results_truncates_to_3(self):
        facts = [_make_fact(f"f-{i}", f"fact {i}") for i in range(5)]
        store = _make_store_with_mocked_repos(facts_results=facts)
        results = store.search("query", _SCOPE, record_types=["facts"], max_results=3)
        assert len(results) == 3

    def test_max_results_across_types_truncates(self):
        # 2 working + 2 facts = 4 total; max_results=3 truncates to 3
        working = [_make_working(f"w-{i}", f"w {i}") for i in range(2)]
        facts = [_make_fact(f"f-{i}", f"f {i}") for i in range(2)]
        store = _make_store_with_mocked_repos(working_results=working, facts_results=facts)
        results = store.search("query", _SCOPE, record_types=["working", "facts"], max_results=3)
        assert len(results) == 3

    def test_fewer_results_than_max_returns_all(self):
        facts = [_make_fact("f-1", "only one")]
        store = _make_store_with_mocked_repos(facts_results=facts)
        results = store.search("query", _SCOPE, record_types=["facts"], max_results=10)
        assert len(results) == 1

    def test_empty_results_returns_empty_list(self):
        store = _make_store_with_mocked_repos()  # all repos return []
        results = store.search("query", _SCOPE)
        assert results == []


class TestSearchMetadataFilter:
    """metadata_filter is forwarded to per-type search() calls."""

    def test_metadata_filter_passed_through(self):
        store = _make_store_with_mocked_repos()
        mf = {"source": "support"}
        store.search("hello", _SCOPE, record_types=["facts"], metadata_filter=mf)
        _, kwargs = store.facts.search.call_args
        assert kwargs.get("metadata_filter") == mf

    def test_none_metadata_filter_passed_as_none(self):
        store = _make_store_with_mocked_repos()
        store.search("hello", _SCOPE, record_types=["facts"], metadata_filter=None)
        _, kwargs = store.facts.search.call_args
        assert kwargs.get("metadata_filter") is None

    def test_metadata_filter_forwarded_to_all_types(self):
        store = _make_store_with_mocked_repos()
        mf = {"status": "active"}
        store.search("hello", _SCOPE, metadata_filter=mf)
        for mock in (
            store.working.search,
            store.episodic.search,
            store.facts.search,
            store.profiles.search,
            store.procedures.search,
        ):
            _, kwargs = mock.call_args
            assert kwargs.get("metadata_filter") == mf


class TestSearchTopK:
    """max_results is forwarded as top_k to each repo.search()."""

    def test_max_results_forwarded_as_top_k(self):
        store = _make_store_with_mocked_repos()
        store.search("hello", _SCOPE, record_types=["facts"], max_results=7)
        _, kwargs = store.facts.search.call_args
        assert kwargs.get("top_k") == 7


class TestSearchEmbeddingFailure:
    """Embedding provider raising propagates to caller."""

    def test_embedding_error_propagates(self):
        def bad_provider(text: str) -> list[float]:
            raise RuntimeError("embedding service down")

        store = _make_store_with_mocked_repos(embedding_provider=bad_provider)
        with pytest.raises(RuntimeError, match="embedding service down"):
            store.search("hello", _SCOPE)


class TestSearchPublicExport:
    """SearchResult is exported from the top-level package."""

    def test_searchresult_importable_from_package(self):
        from agent_memory_sdk import SearchResult as SR  # noqa: F401
        assert SR is SearchResult

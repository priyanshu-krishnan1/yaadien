"""
tests/test_thrd5_extractor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-5: MemoryExtractor — automatic LLM-driven memory
extraction on message ingest.

Coverage:
  1.  NoOpMemoryExtractor always returns []
  2.  MemoryStore(memory_extractor=None) defaults to NoOpMemoryExtractor
  3.  add_messages(extract_memories=True) with a real extractor: derived
      records are persisted via create()
  4.  add_messages(extract_memories=False) with a real extractor: extractor
      is NOT called
  5.  Extractor exception: caught and logged, does NOT propagate, original
      messages were already written
  6.  Extractor returning unknown type: logged warning, skipped (not an error)
  7.  add_messages() default extract_memories=True with NoOp extractor does
      nothing (fast path — extractor never called)
  8.  MemoryExtractor and NoOpMemoryExtractor are exported from agent_memory_sdk
  9.  Derived SemanticFact from extractor goes to the facts repo (INSERT INTO
      semantic_facts)

No live Db2 instance required — uses the fake-pool pattern from
tests/test_thrd1_messages.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

import agent_memory_sdk
from agent_memory_sdk.models import (
    MemoryScope,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpMemoryExtractor

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(agent_id="agent-thrd5", tenant_id="t5")


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _working_row(
    id_: str,
    content: str = "msg",
    created_at: datetime = _NOW,
    deleted_at: Any = None,
    metadata: dict | None = None,
) -> tuple[Any, ...]:
    """Build a fake 16-column DB row for working_memory (same shape as THRD-1)."""
    return (
        id_, "t5", "agent-thrd5", None, None,
        content, json.dumps(metadata or {}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        created_at, created_at, None, 1, deleted_at,
        "DIRECT_WRITE",  # origin (TRU-1)
        None,  # consolidated_at (ENH-4)
    )


class _FakeCursor:
    """Minimal fake cursor that records SQL/params and returns preset rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows: list[tuple[Any, ...]] = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 1
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = list(params) if params else []
        self.all_sqls.append(self.last_sql)
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
    """Fake pool that always returns the same cursor (configurable rows)."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


def _make_store(
    rows: list[tuple[Any, ...]] | None = None,
    memory_extractor: Any | None = None,
) -> tuple[MemoryStore, _FakePool]:
    """Return (store, pool) backed by a fake pool, optionally with a real extractor."""
    pool = _FakePool(rows)
    store = MemoryStore(pool, memory_extractor=memory_extractor)
    return store, pool


# ---------------------------------------------------------------------------
# 1. NoOpMemoryExtractor always returns []
# ---------------------------------------------------------------------------


class TestNoOpMemoryExtractor:
    def test_returns_empty_list_for_empty_input(self):
        extractor = NoOpMemoryExtractor()
        result = extractor([], _SCOPE)
        assert result == []

    def test_returns_empty_list_for_nonempty_input(self):
        extractor = NoOpMemoryExtractor()
        wm = WorkingMemory(agent_id="a", content="hello")
        result = extractor([wm], _SCOPE)
        assert result == []

    def test_return_type_is_list(self):
        extractor = NoOpMemoryExtractor()
        result = extractor([], _SCOPE)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 2. MemoryStore(memory_extractor=None) defaults to NoOpMemoryExtractor
# ---------------------------------------------------------------------------


class TestMemoryStoreDefaultExtractor:
    def test_default_extractor_is_noop(self):
        pool = _FakePool()
        store = MemoryStore(pool)
        assert isinstance(store._memory_extractor, NoOpMemoryExtractor)

    def test_explicit_none_gives_noop(self):
        pool = _FakePool()
        store = MemoryStore(pool, memory_extractor=None)
        assert isinstance(store._memory_extractor, NoOpMemoryExtractor)

    def test_real_extractor_stored(self):
        pool = _FakePool()
        extractor = NoOpMemoryExtractor()  # a real (non-None) object
        store = MemoryStore(pool, memory_extractor=extractor)
        assert store._memory_extractor is extractor


# ---------------------------------------------------------------------------
# 3. add_messages(extract_memories=True) — derived records persisted
# ---------------------------------------------------------------------------


class TestExtractorCalledAndPersists:
    def test_extractor_called_with_stored_records_and_scope(self):
        """The extractor receives the written WorkingMemory records and scope."""
        calls: list[tuple[list, Any]] = []

        def fake_extractor(messages, scope):
            calls.append((messages, scope))
            return []

        store, _ = _make_store(memory_extractor=fake_extractor)
        store.add_messages(
            [{"content": "hello"}, {"content": "world"}],
            _SCOPE,
            extract_memories=True,
        )
        assert len(calls) == 1
        messages_arg, scope_arg = calls[0]
        assert scope_arg is _SCOPE
        assert len(messages_arg) == 2
        assert all(isinstance(m, WorkingMemory) for m in messages_arg)

    def test_derived_semantic_fact_is_inserted(self):
        """When extractor returns a SemanticFact, it is inserted into semantic_facts."""
        derived_fact = SemanticFact(
            agent_id="agent-thrd5",
            content="User likes Python",
            metadata={"source": "extractor"},
        )

        def fake_extractor(messages, scope):
            return [derived_fact]

        store, pool = _make_store(memory_extractor=fake_extractor)
        store.add_messages([{"content": "I love Python!"}], _SCOPE, extract_memories=True)

        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert insert_sqls, "Expected INSERT INTO semantic_facts from extractor"

    def test_extractor_not_called_when_noop(self):
        """With NoOpMemoryExtractor the extractor body is never reached (fast path)."""
        called = []
        # Subclass NoOpMemoryExtractor and override __call__ to track calls.
        # Since isinstance(..., NoOpMemoryExtractor) is True the branch is skipped.
        class TrackingNoOp(NoOpMemoryExtractor):
            def __call__(self, messages, scope):
                called.append(True)
                return []

        store, _ = _make_store(memory_extractor=TrackingNoOp())
        store.add_messages([{"content": "hi"}], _SCOPE, extract_memories=True)
        # TrackingNoOp IS a NoOpMemoryExtractor → branch skipped → not called
        assert called == []


# ---------------------------------------------------------------------------
# 4. add_messages(extract_memories=False) — extractor NOT called
# ---------------------------------------------------------------------------


class TestExtractMemoriesFalse:
    def test_extractor_not_called_when_flag_false(self):
        called = []

        def fake_extractor(messages, scope):
            called.append(True)
            return []

        store, _ = _make_store(memory_extractor=fake_extractor)
        store.add_messages([{"content": "hi"}], _SCOPE, extract_memories=False)
        assert called == [], "Extractor should NOT be called when extract_memories=False"

    def test_original_messages_still_written(self):
        """Even with extract_memories=False the working-memory rows are written."""
        store, pool = _make_store()
        store.add_messages([{"content": "test"}], _SCOPE, extract_memories=False)
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert insert_sqls


# ---------------------------------------------------------------------------
# 5. Extractor exception: caught and logged, does NOT propagate
# ---------------------------------------------------------------------------


class TestExtractorException:
    def test_exception_does_not_propagate(self):
        """An extractor that raises must not cause add_messages to raise."""
        def bad_extractor(messages, scope):
            raise RuntimeError("LLM unavailable")

        store, _ = _make_store(memory_extractor=bad_extractor)
        # Should not raise:
        ids = store.add_messages([{"content": "safe"}], _SCOPE, extract_memories=True)
        assert isinstance(ids, list)
        assert len(ids) == 1

    def test_original_messages_written_despite_extractor_exception(self):
        """Working-memory rows are persisted even if the extractor raises."""
        def bad_extractor(messages, scope):
            raise ValueError("boom")

        store, pool = _make_store(memory_extractor=bad_extractor)
        store.add_messages([{"content": "safe-msg"}], _SCOPE, extract_memories=True)
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert insert_sqls, "Working-memory row must be written before extractor is called"

    def test_exception_logged(self, caplog):
        """Extractor exception is logged at ERROR level."""
        def bad_extractor(messages, scope):
            raise RuntimeError("oops")

        store, _ = _make_store(memory_extractor=bad_extractor)
        with caplog.at_level(logging.ERROR, logger="agent_memory_sdk.store"):
            store.add_messages([{"content": "x"}], _SCOPE, extract_memories=True)
        assert any("MemoryExtractor" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 6. Extractor returning unknown type: logged warning, skipped (not an error)
# ---------------------------------------------------------------------------


class TestExtractorUnknownType:
    def test_unknown_type_does_not_raise(self):
        """Returning an object of an unknown type must not raise."""
        class WeirdMemory:
            pass

        def weird_extractor(messages, scope):
            return [WeirdMemory()]

        store, _ = _make_store(memory_extractor=weird_extractor)
        # Should not raise:
        ids = store.add_messages([{"content": "hi"}], _SCOPE, extract_memories=True)
        assert len(ids) == 1

    def test_unknown_type_logged_as_warning(self, caplog):
        """Unknown returned type emits a WARNING log."""
        class WeirdMemory:
            pass

        def weird_extractor(messages, scope):
            return [WeirdMemory()]

        store, _ = _make_store(memory_extractor=weird_extractor)
        with caplog.at_level(logging.WARNING, logger="agent_memory_sdk.store"):
            store.add_messages([{"content": "hi"}], _SCOPE, extract_memories=True)
        assert any("unknown type" in r.message for r in caplog.records)

    def test_known_type_after_unknown_type_still_persisted(self):
        """A valid record after an unknown-type record is still persisted."""
        class WeirdMemory:
            pass

        derived_fact = SemanticFact(
            agent_id="agent-thrd5",
            content="Valid fact",
            metadata={},
        )

        def mixed_extractor(messages, scope):
            return [WeirdMemory(), derived_fact]

        store, pool = _make_store(memory_extractor=mixed_extractor)
        store.add_messages([{"content": "hi"}], _SCOPE, extract_memories=True)
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert insert_sqls, "Known type after unknown type should still be inserted"


# ---------------------------------------------------------------------------
# 7. Default extract_memories=True is still a no-op with NoOp extractor
# ---------------------------------------------------------------------------


class TestDefaultExtractMemoriesWithNoOp:
    def test_noop_extractor_no_extra_sql(self):
        """With the default NoOpMemoryExtractor, no extra inserts happen."""
        store, pool = _make_store()  # uses default NoOpMemoryExtractor
        store.add_messages([{"content": "msg"}], _SCOPE)  # default extract_memories=True
        # Only working_memory inserts, nothing in facts/profiles/procedures
        non_working_inserts = [
            s for s in pool.cursor.all_sqls
            if "INSERT INTO" in s and "working_memory" not in s
        ]
        assert non_working_inserts == []

    def test_returns_correct_ids_with_noop(self):
        store, _ = _make_store()
        ids = store.add_messages(
            [{"content": "a"}, {"content": "b"}],
            _SCOPE,
        )
        assert len(ids) == 2
        assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# 8. MemoryExtractor and NoOpMemoryExtractor exported from agent_memory_sdk
# ---------------------------------------------------------------------------


class TestPublicExports:
    def test_memory_extractor_in_all(self):
        assert "MemoryExtractor" in agent_memory_sdk.__all__

    def test_noop_memory_extractor_in_all(self):
        assert "NoOpMemoryExtractor" in agent_memory_sdk.__all__

    def test_memory_extractor_importable(self):
        import agent_memory_sdk as sdk
        assert sdk.MemoryExtractor is not None

    def test_noop_memory_extractor_importable(self):
        from agent_memory_sdk import NoOpMemoryExtractor  # noqa: F401
        assert NoOpMemoryExtractor is not None


# ---------------------------------------------------------------------------
# 9. Derived SemanticFact goes to facts repo (INSERT INTO semantic_facts)
# ---------------------------------------------------------------------------


class TestDerivedFactGoesToFactsRepo:
    def test_semantic_fact_inserted_into_semantic_facts_table(self):
        fact = SemanticFact(
            agent_id="agent-thrd5",
            content="The user prefers dark mode",
            metadata={"source": "extractor"},
        )

        def extractor(messages, scope):
            return [fact]

        store, pool = _make_store(memory_extractor=extractor)
        store.add_messages(
            [{"role": "user", "content": "I prefer dark mode"}],
            _SCOPE,
            extract_memories=True,
        )
        facts_inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert len(facts_inserts) >= 1

    def test_fact_content_in_insert_params(self):
        """The fact's content string appears in the INSERT params."""
        fact_content = "User prefers dark mode — unique string xyz123"
        fact = SemanticFact(
            agent_id="agent-thrd5",
            content=fact_content,
            metadata={},
        )

        def extractor(messages, scope):
            return [fact]

        store, pool = _make_store(memory_extractor=extractor)
        store.add_messages([{"content": "dark mode please"}], _SCOPE, extract_memories=True)

        # Find params for the semantic_facts INSERT
        for sql, params in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False):
            if "INSERT INTO semantic_facts" in sql:
                assert fact_content in params, (
                    f"Expected fact content in INSERT params; got {params}"
                )
                return
        pytest.fail("No INSERT INTO semantic_facts found")

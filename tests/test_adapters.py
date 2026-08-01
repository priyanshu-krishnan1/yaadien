"""
tests/test_adapters.py
~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the three framework adapters (Step 6).

No live Db2 instance, no LangChain, no openai-agents, no mcp package
required — all external frameworks are mocked or bypassed via the
adapters' own import-guard mechanisms.

Strategy
--------
Because each adapter has a deferred ``_require_<framework>()`` guard,
we patch it with a no-op (or provide a lightweight stub module) so the
adapter code can be imported and exercised without the real framework.

The fake pool / cursor infrastructure is copied inline here (same pattern
as test_repositories.py and test_lifecycle.py) so this file is self-
contained.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_memory_sdk.models import (
    MemoryScope,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as other test modules)
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


_NOW = datetime(2026, 7, 31, 0, 0, 0, tzinfo=timezone.utc)
_SCOPE = MemoryScope(agent_id="test-agent", thread_id="sess-1")
_VEC = [0.1] * 1536


def _row(
    id_: str = "row-1",
    content: str = '{"role": "user", "content": "hello"}',
    metadata: dict[str, Any] | None = None,
    version: int = 1,
    confidence: float = 1.0,
) -> tuple[Any, ...]:
    """Build a fake DB row matching _SELECT_COLS order for working_memory rows.

    Index map (0-based):
      0  id           1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content      6  metadata    7  embedding
      8  confidence   9  content_hash
      10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
      15 consolidated_at  (ENH-4 — None = not yet consolidated)
    """
    import hashlib
    import re as _re
    meta = metadata or {}
    vec_str = "[" + ",".join("0.1" for _ in range(1536)) + "]"
    h = hashlib.sha256(_re.sub(r"\s+", " ", content.lower()).strip().encode()).hexdigest()
    return (
        id_, "tenant-x", "test-agent", None, "sess-1",
        content, json.dumps(meta),
        vec_str,
        confidence,        # 8 — confidence (ENH-1)
        h,                 # 9 — content_hash (ENH-2)
        _NOW, _NOW, None, version, None,
        None,              # 15 — consolidated_at (ENH-4)
    )


def _fact_row(
    id_: str = "row-1",
    content: str = "some fact",
    metadata: dict[str, Any] | None = None,
    version: int = 1,
    confidence: float = 1.0,
) -> tuple[Any, ...]:
    """Build a fake DB row for SemanticFactRepository._model_from_row (18 cols).

    Index map (0-based):
      0  id           1  tenant_id   2  agent_id    3  user_id     4  thread_id
      5  content      6  metadata    7  embedding
      8  confidence   9  content_hash
      10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
      15 superseded_by  16 superseded_at  17 supersede_reason
    """
    import hashlib
    import re as _re
    meta = metadata or {}
    vec_str = "[" + ",".join("0.1" for _ in range(1536)) + "]"
    h = hashlib.sha256(_re.sub(r"\s+", " ", content.lower()).strip().encode()).hexdigest()
    return (
        id_, "tenant-x", "test-agent", None, "sess-1",
        content, json.dumps(meta),
        vec_str,
        confidence,  # 8 — confidence
        h,           # 9 — content_hash
        _NOW, _NOW, None, version, None,
        None,        # 15 — superseded_by
        None,        # 16 — superseded_at
        None,        # 17 — supersede_reason
    )


# ---------------------------------------------------------------------------
# OpenAI Agents adapter tests
# ---------------------------------------------------------------------------

class TestDb2Session:
    """Tests for agent_memory_sdk.adapters.openai_agents.Db2Session."""

    def _make_session(self, rows: list[tuple[Any, ...]] | None = None):
        """Return a Db2Session with a fake store and patched import guard."""
        pool = _FakePool(rows)
        store = MemoryStore(pool)

        # Patch the guard so we don't need openai-agents installed
        with patch(
            "agent_memory_sdk.adapters.openai_agents._require_openai_agents",
            return_value=None,
        ):
            from agent_memory_sdk.adapters.openai_agents import Db2Session
            session = Db2Session(
                store=store,
                agent_id="test-agent",
                session_id="sess-1",
            )
        session._store = store
        session._pool = pool  # keep a ref for inspection
        return session, pool

    def test_add_items_writes_working_memory(self):
        session, pool = self._make_session()
        asyncio.run(session.add_items([{"role": "user", "content": "Hello!"}]))
        last_sql = pool.cursor.last_sql
        assert "INSERT INTO working_memory" in last_sql

    def test_add_items_stores_role_in_metadata(self):
        session, pool = self._make_session()
        asyncio.run(session.add_items([{"role": "assistant", "content": "Hi!"}]))
        # Params include the JSON-serialised metadata string
        metadata_param = pool.cursor.last_params[6]  # metadata column
        meta = json.loads(metadata_param)
        assert meta["role"] == "assistant"

    def test_add_items_content_is_json_serialised(self):
        """Message dict must be stored as JSON so it round-trips."""
        session, pool = self._make_session()
        msg = {"role": "user", "content": "ping"}
        asyncio.run(session.add_items([msg]))
        content_param = pool.cursor.last_params[5]  # content column
        parsed = json.loads(content_param)
        assert parsed["content"] == "ping"
        assert parsed["role"] == "user"

    def test_add_items_persists_multiple(self):
        """add_items must persist every item in the list."""
        session, pool = self._make_session()
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        asyncio.run(session.add_items(msgs))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 2

    def test_get_items_deserialises_rows(self):
        msg = {"role": "user", "content": "hello"}
        row = _row(id_="r1", content=json.dumps(msg))
        session, pool = self._make_session(rows=[row])
        messages = asyncio.run(session.get_items())
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"
        assert messages[0]["role"] == "user"

    def test_get_items_returns_empty_when_no_rows(self):
        session, pool = self._make_session(rows=[])
        assert asyncio.run(session.get_items()) == []

    def test_get_items_limit_returns_most_recent(self):
        """get_items(limit=N) should return the last N items in chron order."""
        # The fake cursor returns rows in the order given; the real DB returns
        # newest-first (ORDER BY created_at DESC), so we pass them newest-first
        # here to match what list_all would produce.
        rows = [
            _row(id_="r2", content=json.dumps({"role": "user", "content": "newest"})),
            _row(id_="r1", content=json.dumps({"role": "assistant", "content": "middle"})),
            _row(id_="r0", content=json.dumps({"role": "user", "content": "oldest"})),
        ]
        session, pool = self._make_session(rows=rows)
        # get_items reverses (→ oldest, middle, newest) then slices the tail
        messages = asyncio.run(session.get_items(limit=2))
        assert len(messages) == 2
        # The two most-recent (chronologically) should be middle and newest
        contents = [m["content"] for m in messages]
        assert "newest" in contents
        assert "middle" in contents
        assert "oldest" not in contents

    def test_clear_session_soft_deletes_all_rows(self):
        rows = [_row(id_=f"r{i}") for i in range(3)]
        session, pool = self._make_session(rows=rows)
        asyncio.run(session.clear_session())
        # Each row triggers an UPDATE (forget = soft-delete)
        update_sqls = [s for s in pool.cursor.all_sqls if "UPDATE working_memory" in s]
        assert len(update_sqls) == 3

    def test_pop_item_returns_most_recent(self):
        """pop_item() should return the newest row's content."""
        msg = {"role": "assistant", "content": "latest"}
        row = _row(id_="r1", content=json.dumps(msg))
        session, pool = self._make_session(rows=[row])
        result = asyncio.run(session.pop_item())
        assert result is not None
        assert result["content"] == "latest"
        assert result["role"] == "assistant"

    def test_pop_item_tombstones_the_row(self):
        """After pop_item(), the row must be soft-deleted (UPDATE issued)."""
        row = _row(id_="r1", content=json.dumps({"role": "user", "content": "hi"}))
        session, pool = self._make_session(rows=[row])
        asyncio.run(session.pop_item())
        update_sqls = [s for s in pool.cursor.all_sqls if "UPDATE working_memory" in s]
        assert len(update_sqls) >= 1

    def test_pop_item_returns_none_when_empty(self):
        """pop_item() on an empty session must return None."""
        session, pool = self._make_session(rows=[])
        result = asyncio.run(session.pop_item())
        assert result is None

    def test_scope_uses_session_id_as_thread_id(self):
        session, _ = self._make_session()
        assert session.scope.thread_id == "sess-1"
        assert session.scope.agent_id == "test-agent"

    def test_recall_episodes_searches_episodic(self):
        """recall_episodes must issue a VECTOR_DISTANCE query on episodic_memory."""
        session, pool = self._make_session(rows=[])
        session.recall_episodes(query_embedding=_VEC, top_k=3)
        sql = pool.cursor.last_sql
        assert "episodic_memory" in sql
        assert "VECTOR_DISTANCE" in sql

    def test_import_guard_raises_helpful_error(self):
        """Without openai-agents installed, ImportError should mention the extra."""
        with patch.dict(sys.modules, {"agents": None}):
            # Remove cached module to force re-import
            mod_name = "agent_memory_sdk.adapters.openai_agents"
            cached = sys.modules.pop(mod_name, None)
            try:
                from agent_memory_sdk.adapters.openai_agents import (
                    _require_openai_agents,
                )
                with pytest.raises(ImportError, match="openai-agents"):
                    _require_openai_agents()
            finally:
                if cached is not None:
                    sys.modules[mod_name] = cached


# ---------------------------------------------------------------------------
# LangChain adapter tests
# ---------------------------------------------------------------------------

class TestDb2ChatMessageHistory:
    """Tests for Db2ChatMessageHistory without requiring langchain installed."""

    def _make_history(self, rows: list[tuple[Any, ...]] | None = None):
        pool = _FakePool(rows)
        store = MemoryStore(pool)

        with patch(
            "agent_memory_sdk.adapters.langchain._require_langchain",
            return_value=MagicMock(),
        ):
            from agent_memory_sdk.adapters.langchain import Db2ChatMessageHistory
            history = Db2ChatMessageHistory(store=store, scope=_SCOPE)
        history._store = store
        history._pool = pool
        return history, pool

    def test_add_message_inserts_to_working_memory(self):
        history, pool = self._make_history()
        # Simulate a LangChain HumanMessage stub
        fake_msg = MagicMock()
        fake_msg.content = "Hello LangChain"
        fake_msg.additional_kwargs = {}
        fake_msg.response_metadata = {}
        fake_msg.tool_calls = []
        fake_msg.tool_call_id = None
        fake_msg.id = "msg-001"
        type(fake_msg).__name__ = "HumanMessage"
        history.add_message(fake_msg)
        assert "INSERT INTO working_memory" in pool.cursor.last_sql

    def test_add_messages_batch(self):
        history, pool = self._make_history()
        msgs = []
        for role, txt in [("HumanMessage", "Hi"), ("AIMessage", "Hello!")]:
            m = MagicMock()
            m.content = txt
            m.additional_kwargs = {}
            m.response_metadata = {}
            m.tool_calls = []
            m.tool_call_id = None
            m.id = None
            type(m).__name__ = role
            msgs.append(m)
        history.add_messages(msgs)
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 2

    def test_messages_property_returns_list(self):
        """messages property calls list_all and attempts deserialisation."""
        row = _row(
            content="Hello world",
            metadata={"lc_type": "HumanMessage", "role": "human", "additional_kwargs": {}, "response_metadata": {}},
        )
        history, pool = self._make_history(rows=[row])
        # Patch the deserialization function to avoid needing real LangChain
        with patch(
            "agent_memory_sdk.adapters.langchain._metadata_to_message",
            side_effect=lambda c, m: {"_stub": True, "content": c},
        ):
            msgs = history.messages
        assert isinstance(msgs, list)
        assert len(msgs) == 1

    def test_clear_soft_deletes_rows(self):
        rows = [_row(id_=f"r{i}") for i in range(2)]
        history, pool = self._make_history(rows=rows)
        history.clear()
        update_sqls = [s for s in pool.cursor.all_sqls if "UPDATE working_memory" in s]
        assert len(update_sqls) == 2

    def test_scope_propagated_to_queries(self):
        history, pool = self._make_history(rows=[])
        fake_msg = MagicMock()
        fake_msg.content = "test"
        fake_msg.additional_kwargs = {}
        fake_msg.response_metadata = {}
        fake_msg.tool_calls = []
        fake_msg.tool_call_id = None
        fake_msg.id = None
        type(fake_msg).__name__ = "HumanMessage"
        history.add_message(fake_msg)
        params = pool.cursor.last_params
        # agent_id should be in the params
        assert "test-agent" in params

    def test_import_guard_raises_without_langchain(self):
        from agent_memory_sdk.adapters.langchain import _require_langchain

        with patch.dict(sys.modules, {"langchain_core": None}), pytest.raises(ImportError, match="langchain"):
            _require_langchain()


class TestDb2MemoryStore:
    """Tests for Db2MemoryStore without requiring langchain installed."""

    def _make_memstore(self, namespace: str = "facts", rows: list[tuple[Any, ...]] | None = None):
        pool = _FakePool(rows)
        store = MemoryStore(pool)

        with patch(
            "agent_memory_sdk.adapters.langchain._require_langchain",
            return_value=MagicMock(),
        ):
            from agent_memory_sdk.adapters.langchain import Db2MemoryStore
            ms = Db2MemoryStore(store=store, scope=_SCOPE, namespace=namespace)
        ms._pool = pool
        return ms, pool

    def test_mset_creates_fact_row(self):
        ms, pool = self._make_memstore(namespace="facts")
        ms.mset([("key1", "val1")])
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert len(inserts) == 1

    def test_mset_creates_profile_row_for_non_facts(self):
        ms, pool = self._make_memstore(namespace="profiles")
        ms.mset([("key2", "val2")])
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO entity_profiles" in s]
        assert len(inserts) == 1

    def test_mget_returns_none_when_not_found(self):
        ms, pool = self._make_memstore(rows=[])
        result = ms.mget(["missing-key"])
        assert result == [None]

    def test_mget_returns_value_when_found(self):
        meta = {"store_key": "key1"}
        row = _fact_row(content="my-value", metadata=meta)
        ms, pool = self._make_memstore(rows=[row])
        result = ms.mget(["key1"])
        assert result == ["my-value"]

    def test_mdelete_soft_deletes_row(self):
        meta = {"store_key": "to-delete"}
        row = _fact_row(id_="r-del", content="value", metadata=meta)
        ms, pool = self._make_memstore(rows=[row])
        ms.mdelete(["to-delete"])
        update_sqls = [s for s in pool.cursor.all_sqls if "UPDATE semantic_facts" in s]
        assert len(update_sqls) >= 1

    def test_mdelete_no_op_when_key_not_found(self):
        ms, pool = self._make_memstore(rows=[])
        # Should not raise
        ms.mdelete(["nonexistent-key"])

    def test_yield_keys_returns_stored_keys(self):
        rows = [
            _fact_row(id_="r1", metadata={"store_key": "alpha"}),
            _fact_row(id_="r2", metadata={"store_key": "beta"}),
        ]
        ms, _ = self._make_memstore(rows=rows)
        keys = ms.yield_keys()
        assert "alpha" in keys
        assert "beta" in keys

    def test_yield_keys_prefix_filter(self):
        rows = [
            _fact_row(id_="r1", metadata={"store_key": "user:1"}),
            _fact_row(id_="r2", metadata={"store_key": "user:2"}),
            _fact_row(id_="r3", metadata={"store_key": "system:x"}),
        ]
        ms, _ = self._make_memstore(rows=rows)
        keys = ms.yield_keys(prefix="user:")
        assert all(k.startswith("user:") for k in keys)
        assert len(keys) == 2


# ---------------------------------------------------------------------------
# MCP adapter tests
# ---------------------------------------------------------------------------

class TestMcpAdapter:
    """Tests for agent_memory_sdk.adapters.mcp_server."""

    def _make_store(self, rows: list[tuple[Any, ...]] | None = None):
        pool = _FakePool(rows)
        store = MemoryStore(pool)
        return store, pool

    # ---- import guard -------------------------------------------------------

    def test_create_server_requires_mcp(self):
        """Without mcp installed, create_server raises ImportError."""
        from agent_memory_sdk.adapters.mcp_server import _require_mcp

        with patch.dict(sys.modules, {"mcp": None}), pytest.raises(ImportError, match="mcp"):
            _require_mcp()

    # ---- _build_scope -------------------------------------------------------

    def test_build_scope_minimal(self):
        from agent_memory_sdk.adapters.mcp_server import _build_scope

        scope = _build_scope("agent-1")
        assert scope.agent_id == "agent-1"
        assert scope.user_id is None
        assert scope.thread_id is None
        assert scope.tenant_id is None

    def test_build_scope_full(self):
        from agent_memory_sdk.adapters.mcp_server import _build_scope

        scope = _build_scope("a", user_id="u", thread_id="t", tenant_id="ten")
        assert scope.user_id == "u"
        assert scope.thread_id == "t"
        assert scope.tenant_id == "ten"

    # ---- _record_to_dict ----------------------------------------------------

    def test_record_to_dict_structure(self):
        from agent_memory_sdk.adapters.mcp_server import _record_to_dict

        record = WorkingMemory(
            id="id-123",
            agent_id="a1",
            content="hello",
            metadata={"k": "v"},
            version=2,
        )
        d = _record_to_dict(record)
        assert d["id"] == "id-123"
        assert d["content"] == "hello"
        assert d["metadata"] == {"k": "v"}
        assert d["version"] == 2
        assert d["memory_type"] == "WorkingMemory"

    # ---- _tool_* helpers (async coroutines) --------------------------------
    # Use asyncio.run() (Python 3.7+) instead of get_event_loop() which is
    # deprecated/removed in Python 3.14.

    def _run(self, coro):  # type: ignore[no-untyped-def]
        import asyncio
        return asyncio.run(coro)

    def _fake_text_content(self):
        """Return a fake _TextContent callable and a tracker for call inspection."""
        calls: list[dict] = []

        def fake_tc(**kw):  # type: ignore[no-untyped-def]
            calls.append(kw)
            obj = MagicMock()
            obj.type = "text"
            obj.text = kw.get("text", "")
            return obj

        return fake_tc, calls

    def test_tool_remember_inserts_working(self):
        """_tool_remember must issue an INSERT for memory_type=working."""
        from agent_memory_sdk.adapters.mcp_server import _tool_remember

        store, pool = self._make_store()
        args = {"agent_id": "agent-1", "content": "hello", "memory_type": "working"}
        fake_tc, _ = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_remember(store, args))

        assert "INSERT INTO working_memory" in pool.cursor.last_sql

    def test_tool_remember_unknown_type_returns_error(self):
        from agent_memory_sdk.adapters.mcp_server import _tool_remember

        store, pool = self._make_store()
        args = {"agent_id": "agent-1", "content": "x", "memory_type": "invalid"}
        fake_tc, calls = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_remember(store, args))

        call_text = calls[-1].get("text", "") if calls else ""
        assert "invalid" in call_text or "Unknown" in call_text

    def test_tool_recall_uses_list_when_no_embedding(self):
        """When query_embedding is absent, _tool_recall falls back to list_all."""
        from agent_memory_sdk.adapters.mcp_server import _tool_recall

        store, pool = self._make_store(rows=[])
        args = {"agent_id": "agent-1", "memory_type": "working"}
        fake_tc, _ = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_recall(store, args))

        assert all("VECTOR_DISTANCE" not in s for s in pool.cursor.all_sqls)
        assert any("SELECT" in s for s in pool.cursor.all_sqls)

    def test_tool_recall_uses_search_with_embedding(self):
        """When query_embedding is present, _tool_recall issues VECTOR_DISTANCE."""
        from agent_memory_sdk.adapters.mcp_server import _tool_recall

        store, pool = self._make_store(rows=[])
        args = {"agent_id": "agent-1", "memory_type": "working", "query_embedding": _VEC}
        fake_tc, _ = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_recall(store, args))

        assert any("VECTOR_DISTANCE" in s for s in pool.cursor.all_sqls)

    def test_tool_forget_calls_store_forget(self):
        from agent_memory_sdk.adapters.mcp_server import _tool_forget

        store, pool = self._make_store()
        pool.cursor.rowcount = 1
        args = {"agent_id": "agent-1", "record_id": "row-1", "memory_type": "working"}
        fake_tc, _ = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_forget(store, args))

        assert any("UPDATE working_memory" in s for s in pool.cursor.all_sqls)

    def test_tool_list_calls_list_all(self):
        from agent_memory_sdk.adapters.mcp_server import _tool_list

        store, pool = self._make_store(rows=[])
        args = {"agent_id": "agent-1", "memory_type": "facts", "limit": 10}
        fake_tc, _ = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_list(store, args))

        assert any("semantic_facts" in s for s in pool.cursor.all_sqls)

    def test_tool_list_unknown_type_returns_error(self):
        from agent_memory_sdk.adapters.mcp_server import _tool_list

        store, pool = self._make_store()
        args = {"agent_id": "agent-1", "memory_type": "bogus"}
        fake_tc, calls = self._fake_text_content()

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_tc):
            self._run(_tool_list(store, args))

        call_text = calls[-1].get("text", "") if calls else ""
        assert "Unknown" in call_text or "bogus" in call_text


# ---------------------------------------------------------------------------
# Microsoft Agent Framework adapter tests
# ---------------------------------------------------------------------------
#
# agent_framework is not installed in this environment, so
# MemoryStoreContextProvider/MemoryStoreHistoryProvider fall back to
# subclassing `object` (see the module's try/except import guard). To
# exercise the real before_run/after_run/get_messages/save_messages
# behaviour as they'd run against the real base classes, the af_module
# fixture below installs a minimal fake `agent_framework` module in
# sys.modules and force-reimports the adapter module against it, then
# restores the original (uninstalled) state afterward.

class TestAgentFrameworkAdapter:
    """Tests for agent_memory_sdk.adapters.agent_framework."""

    @pytest.fixture
    def af(self):
        """Reimport the adapter module with a fake agent_framework 'installed'."""
        import types

        class _FakeContextProvider:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        class _FakeHistoryProvider(_FakeContextProvider):
            pass

        fake_mod = types.ModuleType("agent_framework")
        fake_mod.ContextProvider = _FakeContextProvider  # type: ignore[attr-defined]
        fake_mod.HistoryProvider = _FakeHistoryProvider  # type: ignore[attr-defined]

        mod_name = "agent_memory_sdk.adapters.agent_framework"
        cached_af_mod = sys.modules.pop(mod_name, None)
        cached_fw_mod = sys.modules.get("agent_framework")
        sys.modules["agent_framework"] = fake_mod
        try:
            import agent_memory_sdk.adapters.agent_framework as af_mod

            assert af_mod._AGENT_FRAMEWORK_AVAILABLE is True
            yield af_mod
        finally:
            sys.modules.pop(mod_name, None)
            if cached_fw_mod is not None:
                sys.modules["agent_framework"] = cached_fw_mod
            else:
                sys.modules.pop("agent_framework", None)
            if cached_af_mod is not None:
                sys.modules[mod_name] = cached_af_mod

    def _make_context_provider(self, af, rows=None, agent_id="test-agent", **kwargs):
        pool = _FakePool(rows)
        store = MemoryStore(pool)
        provider = af.MemoryStoreContextProvider(store=store, agent_id=agent_id, **kwargs)
        return provider, store, pool

    def _make_history_provider(self, af, rows=None, agent_id="test-agent"):
        pool = _FakePool(rows)
        store = MemoryStore(pool)
        provider = af.MemoryStoreHistoryProvider(store=store, agent_id=agent_id)
        return provider, store, pool

    # ---- MemoryStoreContextProvider.before_run -----------------------------

    def test_before_run_injects_working_turns(self, af):
        row = _row(content=json.dumps({"content": "hi there"}), metadata={"role": "user"})
        provider, store, pool = self._make_context_provider(af, rows=[row])
        context = MagicMock()
        state: dict[str, Any] = {"thread_id": "sess-1"}
        asyncio.run(provider.before_run(agent=None, session=None, context=context, state=state))
        calls = [c.args for c in context.extend_instructions.call_args_list]
        assert any(c[0] == "agent-memory-sdk:working" for c in calls)

    def test_before_run_no_turns_does_not_inject_working(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        asyncio.run(
            provider.before_run(agent=None, session=None, context=context, state={"thread_id": "s"})
        )
        calls = [c.args for c in context.extend_instructions.call_args_list]
        assert not any(c[0] == "agent-memory-sdk:working" for c in calls)

    def test_before_run_injects_facts_when_query_embedding_present(self, af):
        fact_row = _fact_row(content="user prefers dark mode")
        provider, store, pool = self._make_context_provider(af, rows=[fact_row])
        context = MagicMock()
        state: dict[str, Any] = {"thread_id": "sess-1", "query_embedding": _VEC}
        asyncio.run(provider.before_run(agent=None, session=None, context=context, state=state))
        assert any("VECTOR_DISTANCE" in s for s in pool.cursor.all_sqls)
        calls = [c.args for c in context.extend_instructions.call_args_list]
        assert any(c[0] == "agent-memory-sdk:facts" for c in calls)

    def test_before_run_skips_facts_without_query_embedding(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        state: dict[str, Any] = {"thread_id": "sess-1"}
        asyncio.run(provider.before_run(agent=None, session=None, context=context, state=state))
        assert all("VECTOR_DISTANCE" not in s for s in pool.cursor.all_sqls)
        calls = [c.args for c in context.extend_instructions.call_args_list]
        assert not any(c[0] == "agent-memory-sdk:facts" for c in calls)

    def test_before_run_scope_uses_state_thread_id_not_instance(self, af):
        """The same provider instance, called with two different session
        states, must scope each call to that call's thread_id."""
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        asyncio.run(
            provider.before_run(agent=None, session=None, context=context, state={"thread_id": "sess-A"})
        )
        params_a = pool.cursor.last_params
        asyncio.run(
            provider.before_run(agent=None, session=None, context=context, state={"thread_id": "sess-B"})
        )
        params_b = pool.cursor.last_params
        assert "sess-A" in params_a
        assert "sess-B" in params_b

    def test_before_run_falls_back_to_session_id_attribute(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        session = MagicMock()
        session.session_id = "sess-from-session-obj"
        session.id = None
        asyncio.run(provider.before_run(agent=None, session=session, context=context, state={}))
        assert "sess-from-session-obj" in pool.cursor.last_params

    # ---- MemoryStoreContextProvider.after_run ------------------------------

    def test_after_run_persists_messages_list(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        state = {
            "thread_id": "sess-1",
            "messages": [
                {"role": "user", "text": "hello"},
                {"role": "assistant", "text": "hi!"},
            ],
        }
        asyncio.run(provider.after_run(agent=None, session=None, context=context, state=state))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 2

    def test_after_run_persists_request_response(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        state = {
            "thread_id": "sess-1",
            "request": {"role": "user", "text": "ping"},
            "response": {"role": "assistant", "text": "pong"},
        }
        asyncio.run(provider.after_run(agent=None, session=None, context=context, state=state))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 2

    def test_after_run_reads_messages_from_context_fallback(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        context.messages = [{"role": "user", "text": "from context"}]
        state: dict[str, Any] = {"thread_id": "sess-1"}
        asyncio.run(provider.after_run(agent=None, session=None, context=context, state=state))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 1

    def test_after_run_no_messages_is_a_noop(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock(spec=[])  # no .messages attribute at all
        state: dict[str, Any] = {"thread_id": "sess-1"}
        asyncio.run(provider.after_run(agent=None, session=None, context=context, state=state))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 0

    def test_after_run_persists_role_in_metadata(self, af):
        provider, store, pool = self._make_context_provider(af, rows=[])
        context = MagicMock()
        state = {"thread_id": "sess-1", "messages": [{"role": "assistant", "text": "hi!"}]}
        asyncio.run(provider.after_run(agent=None, session=None, context=context, state=state))
        metadata_param = pool.cursor.last_params[6]
        meta = json.loads(metadata_param)
        assert meta["role"] == "assistant"

    # ---- MemoryStoreHistoryProvider ----------------------------------------

    def test_get_messages_returns_chronological_order(self, af):
        rows = [
            _row(id_="r2", content=json.dumps({"role": "user", "text": "newest"})),
            _row(id_="r1", content=json.dumps({"role": "assistant", "text": "oldest"})),
        ]
        provider, store, pool = self._make_history_provider(af, rows=rows)
        messages = asyncio.run(provider.get_messages("sess-1", state={}))
        assert len(messages) == 2
        assert messages[0]["text"] == "oldest"
        assert messages[1]["text"] == "newest"

    def test_get_messages_returns_empty_when_no_rows(self, af):
        provider, store, pool = self._make_history_provider(af, rows=[])
        messages = asyncio.run(provider.get_messages("sess-1", state={}))
        assert messages == []

    def test_get_messages_scopes_by_session_id(self, af):
        provider, store, pool = self._make_history_provider(af, rows=[])
        asyncio.run(provider.get_messages("sess-xyz", state={}))
        assert "sess-xyz" in pool.cursor.last_params

    def test_save_messages_persists_all(self, af):
        provider, store, pool = self._make_history_provider(af, rows=[])
        messages = [
            {"role": "user", "text": "first"},
            {"role": "assistant", "text": "second"},
        ]
        asyncio.run(provider.save_messages("sess-1", messages, state={}))
        inserts = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert len(inserts) == 2

    def test_save_messages_scope_uses_session_id_param_not_instance_state(self, af):
        """Same provider instance, two different session_ids — scope must
        follow the call argument, never something fixed on the instance."""
        provider, store, pool = self._make_history_provider(af, rows=[])
        asyncio.run(provider.save_messages("sess-A", [{"role": "user", "text": "x"}], state={}))
        params_a = pool.cursor.last_params
        asyncio.run(provider.save_messages("sess-B", [{"role": "user", "text": "y"}], state={}))
        params_b = pool.cursor.last_params
        assert "sess-A" in params_a
        assert "sess-B" in params_b

    def test_save_messages_reads_user_id_from_state(self, af):
        provider, store, pool = self._make_history_provider(af, rows=[])
        asyncio.run(
            provider.save_messages(
                "sess-1", [{"role": "user", "text": "x"}], state={"user_id": "user-42"}
            )
        )
        assert "user-42" in pool.cursor.last_params


# ---------------------------------------------------------------------------
# Core package importable without any adapter deps
# ---------------------------------------------------------------------------

class TestCoreImportableWithoutAdapters:
    """Verify the core SDK is importable without any adapter extras."""

    def test_core_imports_without_langchain(self):
        """Core package must import cleanly even when langchain_core is absent."""
        with patch.dict(sys.modules, {"langchain_core": None}):
            # Force reimport of core modules (they don't import langchain at
            # module level, so this should be a no-op).
            import agent_memory_sdk  # noqa: F401
            from agent_memory_sdk import MemoryScope, MemoryStore  # noqa: F401

    def test_core_imports_without_openai_agents(self):
        with patch.dict(sys.modules, {"agents": None}):
            import agent_memory_sdk  # noqa: F401

    def test_core_imports_without_mcp(self):
        with patch.dict(sys.modules, {"mcp": None}):
            import agent_memory_sdk  # noqa: F401

    def test_core_imports_without_agent_framework(self):
        with patch.dict(sys.modules, {"agent_framework": None}):
            import agent_memory_sdk  # noqa: F401

    def test_adapter_module_importable_without_langchain(self):
        """The adapter *module* can be imported; instantiation fails without dep."""
        from agent_memory_sdk.adapters import langchain as lc_mod  # noqa: F401

    def test_adapter_module_importable_without_openai_agents(self):
        from agent_memory_sdk.adapters import openai_agents as oa_mod  # noqa: F401

    def test_adapter_module_importable_without_mcp(self):
        from agent_memory_sdk.adapters import mcp_server as mcp_mod  # noqa: F401

    def test_adapter_module_importable_without_agent_framework(self):
        """Unlike the other adapters, this module's classes actually subclass
        the framework's base classes; when the package is absent the
        try/except fallback binds them to ``object`` instead, so the module
        import itself must still succeed (import guard fires at
        instantiation time, in __init__ — see the module docstring)."""
        mod_name = "agent_memory_sdk.adapters.agent_framework"
        cached = sys.modules.pop(mod_name, None)
        try:
            with patch.dict(sys.modules, {"agent_framework": None}):
                from agent_memory_sdk.adapters import (
                    agent_framework as af_mod,  # noqa: F401
                )
                assert af_mod._AGENT_FRAMEWORK_AVAILABLE is False
        finally:
            sys.modules.pop(mod_name, None)
            if cached is not None:
                sys.modules[mod_name] = cached

    def test_instantiation_fails_with_helpful_message_langchain(self):
        from agent_memory_sdk.adapters.langchain import _require_langchain

        with patch.dict(sys.modules, {"langchain_core": None}), pytest.raises(ImportError, match="langchain"):
            _require_langchain()

    def test_instantiation_fails_with_helpful_message_openai_agents(self):
        from agent_memory_sdk.adapters.openai_agents import _require_openai_agents

        with patch.dict(sys.modules, {"agents": None}), pytest.raises(ImportError, match="openai-agents"):
            _require_openai_agents()

    def test_instantiation_fails_with_helpful_message_mcp(self):
        from agent_memory_sdk.adapters.mcp_server import _require_mcp

        with patch.dict(sys.modules, {"mcp": None}), pytest.raises(ImportError, match="mcp"):
            _require_mcp()

    def test_instantiation_fails_with_helpful_message_agent_framework(self):
        """With agent_framework unavailable, constructing either provider
        class must raise a helpful ImportError (not, say, an AttributeError
        from calling a method that doesn't exist on ``object``)."""
        mod_name = "agent_memory_sdk.adapters.agent_framework"
        cached = sys.modules.pop(mod_name, None)
        try:
            with patch.dict(sys.modules, {"agent_framework": None}):
                from agent_memory_sdk.adapters.agent_framework import (
                    MemoryStoreContextProvider,
                    MemoryStoreHistoryProvider,
                    _require_agent_framework,
                )

                with pytest.raises(ImportError, match="agent-framework"):
                    _require_agent_framework()
                with pytest.raises(ImportError, match="agent-framework"):
                    MemoryStoreContextProvider(store=MagicMock(), agent_id="a")
                with pytest.raises(ImportError, match="agent-framework"):
                    MemoryStoreHistoryProvider(store=MagicMock(), agent_id="a")
        finally:
            sys.modules.pop(mod_name, None)
            if cached is not None:
                sys.modules[mod_name] = cached


# ---------------------------------------------------------------------------
# Message serialisation round-trip tests (no framework required)
# ---------------------------------------------------------------------------

class TestMessageSerialisation:
    """Round-trip tests for OpenAI Agents SDK message serialisation."""

    def test_roundtrip_user_message(self):
        from agent_memory_sdk.adapters.openai_agents import (
            _content_to_msg,
            _msg_to_content,
        )

        msg = {"role": "user", "content": "Hello world!"}
        serialised = _msg_to_content(msg)
        restored = _content_to_msg(serialised)
        assert restored["role"] == "user"
        assert restored["content"] == "Hello world!"

    def test_roundtrip_assistant_message(self):
        from agent_memory_sdk.adapters.openai_agents import (
            _content_to_msg,
            _msg_to_content,
        )

        msg = {"role": "assistant", "content": "I can help with that.", "extra": 42}
        serialised = _msg_to_content(msg)
        restored = _content_to_msg(serialised)
        assert restored["role"] == "assistant"
        assert restored["extra"] == 42

    def test_corrupted_content_falls_back_to_user(self):
        from agent_memory_sdk.adapters.openai_agents import _content_to_msg

        # Not valid JSON — should return a fallback dict
        restored = _content_to_msg("plain text fallback")
        assert restored["content"] == "plain text fallback"

    def test_content_to_str_scalar(self):
        from agent_memory_sdk.adapters.langchain import _content_to_str

        assert _content_to_str("hello") == "hello"

    def test_content_to_str_structured(self):
        from agent_memory_sdk.adapters.langchain import _content_to_str

        content = [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": "..."}]
        result = _content_to_str(content)
        parsed = json.loads(result)
        assert parsed[0]["type"] == "text"

    # ---- agent_framework message helpers (dict + object duck-typing) ------

    def test_af_roundtrip_dict_message(self):
        from agent_memory_sdk.adapters.agent_framework import (
            _content_to_message_dict,
            _message_to_content,
        )

        msg = {"role": "user", "text": "Hello world!"}
        serialised = _message_to_content(msg)
        restored = _content_to_message_dict(serialised)
        assert restored["role"] == "user"
        assert restored["text"] == "Hello world!"

    def test_af_roundtrip_dict_message_content_key(self):
        """dict messages using 'content' instead of 'text' are also accepted."""
        from agent_memory_sdk.adapters.agent_framework import (
            _content_to_message_dict,
            _message_to_content,
        )

        msg = {"role": "assistant", "content": "I can help with that."}
        serialised = _message_to_content(msg)
        restored = _content_to_message_dict(serialised)
        assert restored["role"] == "assistant"
        assert restored["text"] == "I can help with that."

    def test_af_roundtrip_object_message(self):
        """Object-style messages (attrs instead of dict keys) are duck-typed
        the same way LangChain BaseMessage objects are in adapters/langchain.py."""
        from agent_memory_sdk.adapters.agent_framework import (
            _content_to_message_dict,
            _message_to_content,
        )

        fake_msg = MagicMock()
        fake_msg.role = "assistant"
        fake_msg.text = "from an object"
        serialised = _message_to_content(fake_msg)
        restored = _content_to_message_dict(serialised)
        assert restored["role"] == "assistant"
        assert restored["text"] == "from an object"

    def test_af_corrupted_content_falls_back(self):
        from agent_memory_sdk.adapters.agent_framework import _content_to_message_dict

        restored = _content_to_message_dict("not json at all")
        assert restored["text"] == "not json at all"
        assert restored["role"] == "unknown"

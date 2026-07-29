"""
tests/integration/test_adapters_integration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for the three framework adapters against a real Db2 instance.

Covers:
- LangChain Db2ChatMessageHistory: add_message / messages property / clear()
- LangChain Db2MemoryStore: mset / mget / mdelete / yield_keys
- OpenAI Agents SDK Db2Session: add_items / get_items / pop_item / clear_session
  and recall_episodes (episodic search)
- MCP adapter: _tool_remember, _tool_recall (with real embedding),
  _tool_forget, _tool_list — all exercised via the internal helpers used by
  the dispatcher so we don't need a running MCP server process.

These tests import the real framework packages where needed; the tests are
skipped if a required framework extra is not installed (using
``importorskip``), just like the main adapter tests skip without the marker.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# LangChain adapter
# ---------------------------------------------------------------------------


class TestDb2ChatMessageHistoryIntegration:
    """Db2ChatMessageHistory backed by a real Db2 working_memory table."""

    @pytest.fixture()
    def history(self, store, thread_scope):
        """Return a Db2ChatMessageHistory with patched import guard."""
        lc = pytest.importorskip(
            "langchain_core",
            reason="langchain-core not installed; skipping LangChain integration tests",
        )
        from unittest.mock import patch

        with patch(
            "agent_memory_sdk.adapters.langchain._require_langchain",
            return_value=lc,
        ):
            from agent_memory_sdk.adapters.langchain import Db2ChatMessageHistory

            return Db2ChatMessageHistory(store=store, scope=thread_scope)

    def _make_message(self, role: str, content: str) -> Any:
        """Build a real LangChain message object."""
        from langchain_core.messages import AIMessage, HumanMessage

        if role == "human":
            return HumanMessage(content=content)
        return AIMessage(content=content)

    def test_add_and_retrieve_messages(self, history):
        history.add_message(self._make_message("human", "Hello from integration!"))
        history.add_message(self._make_message("ai", "Hi back!"))

        msgs = history.messages
        assert len(msgs) >= 2, "Expected at least 2 messages after two add_message calls"
        contents = [m.content for m in msgs]
        assert "Hello from integration!" in contents
        assert "Hi back!" in contents

    def test_messages_chronological_order(self, history):
        """Messages must be returned oldest-first."""
        history.add_message(self._make_message("human", "first"))
        history.add_message(self._make_message("ai", "second"))

        msgs = history.messages
        # Find relative positions
        idx_first = next((i for i, m in enumerate(msgs) if m.content == "first"), -1)
        idx_second = next((i for i, m in enumerate(msgs) if m.content == "second"), -1)
        assert idx_first < idx_second, (
            f"Expected 'first' before 'second' in chronological order, "
            f"positions: first={idx_first}, second={idx_second}"
        )

    def test_clear_removes_messages(self, history):
        history.add_message(self._make_message("human", "to be cleared"))
        before = [m.content for m in history.messages]
        assert "to be cleared" in before

        history.clear()

        after = history.messages
        assert not any(m.content == "to be cleared" for m in after), (
            "clear() must remove (tombstone) all messages in the scope"
        )

    def test_add_messages_batch(self, history):
        msgs = [
            self._make_message("human", "batch msg 1"),
            self._make_message("ai", "batch msg 2"),
            self._make_message("human", "batch msg 3"),
        ]
        history.add_messages(msgs)
        stored = history.messages
        contents = [m.content for m in stored]
        for txt in ["batch msg 1", "batch msg 2", "batch msg 3"]:
            assert txt in contents, f"Batch message '{txt}' missing from history"

    def test_human_and_ai_message_types_preserved(self, history):
        """Message type must survive the DB round-trip."""
        from langchain_core.messages import AIMessage, HumanMessage

        history.add_message(HumanMessage(content="type-check human"))
        history.add_message(AIMessage(content="type-check ai"))

        msgs = history.messages
        human_msgs = [m for m in msgs if isinstance(m, HumanMessage) and m.content == "type-check human"]
        ai_msgs = [m for m in msgs if isinstance(m, AIMessage) and m.content == "type-check ai"]
        assert len(human_msgs) >= 1, "HumanMessage type not preserved after round-trip"
        assert len(ai_msgs) >= 1, "AIMessage type not preserved after round-trip"


class TestDb2MemoryStoreIntegration:
    """Db2MemoryStore backed by a real Db2 semantic_facts table."""

    @pytest.fixture()
    def mem_store(self, store, scope):
        pytest.importorskip(
            "langchain_core",
            reason="langchain-core not installed; skipping LangChain integration tests",
        )
        from unittest.mock import patch

        with patch(
            "agent_memory_sdk.adapters.langchain._require_langchain",
            return_value=MagicMock(),
        ):
            from agent_memory_sdk.adapters.langchain import Db2MemoryStore

            return Db2MemoryStore(store=store, scope=scope, namespace="facts")

    def test_mset_and_mget_round_trip(self, mem_store):
        mem_store.mset([("pref:theme", "dark"), ("pref:lang", "Python")])
        results = mem_store.mget(["pref:theme", "pref:lang"])
        assert results[0] == "dark"
        assert results[1] == "Python"

    def test_mget_returns_none_for_missing_key(self, mem_store):
        results = mem_store.mget(["key-does-not-exist"])
        assert results == [None]

    def test_mdelete_removes_key(self, mem_store):
        mem_store.mset([("del-key", "value-to-delete")])
        mem_store.mdelete(["del-key"])
        results = mem_store.mget(["del-key"])
        assert results == [None], "mdelete should tombstone the key so mget returns None"

    def test_yield_keys_returns_stored_keys(self, mem_store):
        mem_store.mset([("yield-a", "1"), ("yield-b", "2")])
        keys = mem_store.yield_keys()
        assert "yield-a" in keys
        assert "yield-b" in keys

    def test_yield_keys_prefix_filter(self, mem_store):
        mem_store.mset([("prefix:x", "x"), ("prefix:y", "y"), ("other:z", "z")])
        keys = mem_store.yield_keys(prefix="prefix:")
        assert all(k.startswith("prefix:") for k in keys), (
            "yield_keys(prefix=...) returned keys that don't match the prefix"
        )
        prefix_count = sum(1 for k in keys if k in ("prefix:x", "prefix:y"))
        assert prefix_count == 2


# ---------------------------------------------------------------------------
# OpenAI Agents SDK adapter
# ---------------------------------------------------------------------------


class TestDb2SessionIntegration:
    """Db2Session backed by a real Db2 working_memory table."""

    @pytest.fixture()
    def session(self, store, unique_agent_id):
        """Return a Db2Session with patched import guard."""
        from unittest.mock import patch

        with patch(
            "agent_memory_sdk.adapters.openai_agents._require_openai_agents",
            return_value=None,
        ):
            from agent_memory_sdk.adapters.openai_agents import Db2Session

            sess = Db2Session(
                store=store,
                agent_id=unique_agent_id,
                session_id="integ-thread-1",
            )
        return sess

    def test_add_items_and_get_items_round_trip(self, session):
        items = [
            {"role": "user", "content": "Hello Db2!"},
            {"role": "assistant", "content": "Hi there from Db2!"},
        ]
        asyncio.run(session.add_items(items))

        fetched = asyncio.run(session.get_items())
        assert len(fetched) >= 2
        contents = [m["content"] for m in fetched]
        assert "Hello Db2!" in contents
        assert "Hi there from Db2!" in contents

    def test_get_items_chronological_order(self, session):
        asyncio.run(session.add_items([{"role": "user", "content": "msg-order-1"}]))
        asyncio.run(session.add_items([{"role": "user", "content": "msg-order-2"}]))

        items = asyncio.run(session.get_items())
        idx1 = next((i for i, m in enumerate(items) if m["content"] == "msg-order-1"), -1)
        idx2 = next((i for i, m in enumerate(items) if m["content"] == "msg-order-2"), -1)
        assert idx1 < idx2, (
            f"Expected msg-order-1 before msg-order-2, got positions {idx1}, {idx2}"
        )

    def test_get_items_with_limit(self, session):
        """get_items(limit=N) returns at most N items."""
        msgs = [{"role": "user", "content": f"item {i}"} for i in range(5)]
        asyncio.run(session.add_items(msgs))

        result = asyncio.run(session.get_items(limit=3))
        assert len(result) <= 3, f"Expected ≤ 3 items with limit=3, got {len(result)}"

    def test_clear_session_removes_all(self, session):
        asyncio.run(session.add_items([{"role": "user", "content": "clear me"}]))
        before = asyncio.run(session.get_items())
        assert any(m["content"] == "clear me" for m in before)

        asyncio.run(session.clear_session())

        after = asyncio.run(session.get_items())
        assert not any(m["content"] == "clear me" for m in after), (
            "clear_session() must tombstone all messages"
        )

    def test_pop_item_returns_most_recent(self, session):
        asyncio.run(session.add_items([
            {"role": "user", "content": "pop-first"},
            {"role": "user", "content": "pop-second"},
        ]))

        # add_items are stored newest-first by DB; list_all returns newest first.
        # pop_item fetches limit=1 (newest) and tombstones it.
        popped = asyncio.run(session.pop_item())
        assert popped is not None, "pop_item() must not return None when messages exist"
        # The popped item should be "pop-second" (most recently written).
        assert popped["content"] == "pop-second", (
            f"Expected 'pop-second' as the most recent item, got {popped['content']!r}"
        )

    def test_pop_item_tombstones_row(self, session):
        """After pop_item, the popped message must not appear in get_items."""
        asyncio.run(session.add_items([{"role": "user", "content": "pop-tombstone-test"}]))
        popped = asyncio.run(session.pop_item())
        assert popped is not None

        remaining = asyncio.run(session.get_items())
        assert not any(m["content"] == "pop-tombstone-test" for m in remaining), (
            "pop_item() must tombstone the popped row"
        )

    def test_pop_item_returns_none_when_empty(self, session):
        result = asyncio.run(session.pop_item())
        assert result is None, "pop_item() on empty session must return None"

    def test_recall_episodes_searches_episodic_table(self, store, session, unique_agent_id, vec_dim):
        """recall_episodes must return episodic records via VECTOR_DISTANCE."""
        from agent_memory_sdk.models import EpisodicMemory, MemoryScope

        vec = make_unit_vec(vec_dim, 20)
        episode = EpisodicMemory(
            agent_id=unique_agent_id,
            content="Past session: user asked about Tokyo weather",
            embedding=vec,
        )
        ep_scope = MemoryScope(agent_id=unique_agent_id)
        store.episodic.create(episode, ep_scope)

        episodes = session.recall_episodes(query_embedding=vec, top_k=5)
        contents = [ep.content for ep in episodes]
        assert any("Tokyo" in c for c in contents), (
            "recall_episodes must return the episodic record matching the query embedding"
        )


# ---------------------------------------------------------------------------
# MCP adapter (tool functions only — no live MCP server process needed)
# ---------------------------------------------------------------------------


class TestMcpAdapterIntegration:
    """Exercise the MCP tool functions (_tool_remember, _tool_recall, etc.)."""

    def _run(self, coro):  # type: ignore[no-untyped-def]
        return asyncio.run(coro)

    @pytest.fixture()
    def fake_text_content(self):
        """A drop-in stub for mcp.types.TextContent, patched at the module level."""
        class _Stub:
            def __init__(self, **kw: Any) -> None:
                self.type = kw.get("type", "text")
                self.text = kw.get("text", "")
        return _Stub

    def test_tool_remember_inserts_real_row(self, store, unique_agent_id, fake_text_content):
        from unittest.mock import patch

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_text_content):
            from agent_memory_sdk.adapters.mcp_server import _tool_remember

            args: dict[str, Any] = {
                "agent_id": unique_agent_id,
                "content": "MCP integration test memory",
                "memory_type": "working",
            }
            result = self._run(_tool_remember(store, args))

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert "id" in payload
        assert payload["memory_type"] == "working"
        assert payload["content"] == "MCP integration test memory"

        # Verify the row actually exists in the DB
        from agent_memory_sdk.models import MemoryScope

        scope = MemoryScope(agent_id=unique_agent_id)
        fetched = store.working.get_by_id(payload["id"], scope)
        assert fetched is not None, "Row written by _tool_remember must be retrievable"

    def test_tool_recall_returns_inserted_row(self, store, unique_agent_id, fake_text_content, vec_dim):
        from unittest.mock import patch

        vec = make_unit_vec(vec_dim, 30)
        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_text_content):
            from agent_memory_sdk.adapters.mcp_server import _tool_recall, _tool_remember

            # Insert via remember
            remember_args: dict[str, Any] = {
                "agent_id": unique_agent_id,
                "content": "recall-test content",
                "memory_type": "episodic",
            }
            remember_result = self._run(_tool_remember(store, remember_args))
            inserted_id = json.loads(remember_result[0].text)["id"]

            # Update the embedding directly so we can search for it
            from agent_memory_sdk.models import MemoryScope

            ep_scope = MemoryScope(agent_id=unique_agent_id)
            stored = store.episodic.get_by_id(inserted_id, ep_scope)
            assert stored is not None
            stored.embedding = vec
            store.episodic.update(stored, ep_scope)

            # Recall with the same embedding
            recall_args: dict[str, Any] = {
                "agent_id": unique_agent_id,
                "memory_type": "episodic",
                "query_embedding": vec,
                "top_k": 5,
            }
            recall_result = self._run(_tool_recall(store, recall_args))

        records = json.loads(recall_result[0].text)
        ids = [r["id"] for r in records]
        assert inserted_id in ids, (
            "_tool_recall must return the row we inserted and set the embedding for"
        )

    def test_tool_forget_tombstones_row(self, store, unique_agent_id, fake_text_content):
        from unittest.mock import patch

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_text_content):
            from agent_memory_sdk.adapters.mcp_server import _tool_forget, _tool_remember

            # Insert
            args: dict[str, Any] = {
                "agent_id": unique_agent_id,
                "content": "to forget via MCP",
                "memory_type": "working",
            }
            result = self._run(_tool_remember(store, args))
            record_id = json.loads(result[0].text)["id"]

            # Forget
            forget_result = self._run(_tool_forget(store, {
                "agent_id": unique_agent_id,
                "record_id": record_id,
                "memory_type": "working",
            }))

        payload = json.loads(forget_result[0].text)
        assert payload["status"] == "forgotten"

        # Confirm it is gone from the DB
        from agent_memory_sdk.models import MemoryScope

        scope = MemoryScope(agent_id=unique_agent_id)
        fetched = store.working.get_by_id(record_id, scope)
        assert fetched is None, "_tool_forget must tombstone the row"

    def test_tool_list_returns_inserted_rows(self, store, unique_agent_id, fake_text_content):
        from unittest.mock import patch

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_text_content):
            from agent_memory_sdk.adapters.mcp_server import _tool_list, _tool_remember

            # Insert two rows
            for i in range(2):
                self._run(_tool_remember(store, {
                    "agent_id": unique_agent_id,
                    "content": f"list-test row {i}",
                    "memory_type": "facts",
                }))

            # List
            list_result = self._run(_tool_list(store, {
                "agent_id": unique_agent_id,
                "memory_type": "facts",
                "limit": 10,
            }))

        records = json.loads(list_result[0].text)
        contents = [r["content"] for r in records]
        assert "list-test row 0" in contents
        assert "list-test row 1" in contents

    def test_tool_recall_fallback_to_list_when_no_embedding(self, store, unique_agent_id, fake_text_content):
        """When no query_embedding is given, _tool_recall falls back to list_all."""
        from unittest.mock import patch

        with patch("agent_memory_sdk.adapters.mcp_server._TextContent", fake_text_content):
            from agent_memory_sdk.adapters.mcp_server import _tool_recall, _tool_remember

            self._run(_tool_remember(store, {
                "agent_id": unique_agent_id,
                "content": "fallback recall test",
                "memory_type": "working",
            }))

            recall_result = self._run(_tool_recall(store, {
                "agent_id": unique_agent_id,
                "memory_type": "working",
                # no query_embedding
            }))

        records = json.loads(recall_result[0].text)
        contents = [r["content"] for r in records]
        assert "fallback recall test" in contents, (
            "_tool_recall without embedding must fall back to list_all and return recent rows"
        )

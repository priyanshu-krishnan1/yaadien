"""
tests/integration/test_async_facade.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-17: async facade correctness.

Covers:
  1. search_async produces identical results to sync search.
  2. add_messages_async writes real rows to Db2.
  3. get_context_card_async returns a ContextCard equivalent to sync call.
  4. Concurrent async calls against two different scopes (asyncio.gather).

pytest-asyncio is NOT listed in dev dependencies, so async code is run via
asyncio.run() inside normal def test_... functions.

All tests are skipped automatically when DB2_DATABASE is not set.
Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _IndexEmbeddingProvider:
    """Deterministic embedding provider — no external service needed.

    Returns make_unit_vec(1536, hash(text) % 1500).  Two calls with the same
    text return the same unit vector; different texts at different hash buckets
    are orthogonal (cosine distance 1), making search results fully
    deterministic.
    """

    DIM = 1536

    def __call__(self, text: str) -> list[float]:
        return make_unit_vec(self.DIM, hash(text) % 1500)


# ---------------------------------------------------------------------------
# Test 1 — search_async produces identical results to sync search
# ---------------------------------------------------------------------------


class TestSearchAsync:
    """search_async must return the same results as the synchronous search()."""

    def test_search_async_matches_sync_search(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Seeded SemanticFact rows: async search == sync search (IDs, order, content)."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        provider = _IndexEmbeddingProvider()
        store = MemoryStore(
            migrated_pool,
            embedding_provider=provider,
            enable_chunking=False,
        )
        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-async-search")

        # Seed three SemanticFact rows with distinct deterministic embeddings.
        fact_texts = [
            "async facade search alpha",
            "async facade search beta",
            "async facade search gamma",
        ]
        for text in fact_texts:
            fact = SemanticFact(
                agent_id=unique_agent_id,
                user_id="user-async-search",
                content=text,
                embedding=provider(text),
            )
            store.remember(fact, scope)

        query_text = "async facade search alpha"

        # Sync call.
        sync_results = store.search(
            query_text, scope, record_types=["facts"], max_results=3
        )

        # Async call via asyncio.run().
        async_results = asyncio.run(
            store.search_async(
                query_text, scope, record_types=["facts"], max_results=3
            )
        )

        # Both must return non-empty lists.
        assert len(sync_results) >= 1
        assert len(async_results) >= 1

        # Same number of results.
        assert len(async_results) == len(sync_results)

        # Same IDs in the same order.
        assert [r.id for r in async_results] == [r.id for r in sync_results]

        # Same content in the same order.
        assert [r.content for r in async_results] == [r.content for r in sync_results]

        # The exact-match fact must rank first (cosine distance 0).
        assert async_results[0].content == query_text


# ---------------------------------------------------------------------------
# Test 2 — add_messages_async writes real rows to Db2
# ---------------------------------------------------------------------------


class TestAddMessagesAsync:
    """add_messages_async must persist rows to Db2 exactly as the sync call does."""

    def test_add_messages_async_writes_to_db(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Rows written by add_messages_async must be visible in working.list_all()."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        store = MemoryStore(migrated_pool, enable_chunking=False)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-async-add")

        # Write via the async facade.
        returned_ids = asyncio.run(
            store.add_messages_async(
                [{"role": "user", "content": "hello async"}],
                scope,
            )
        )

        assert isinstance(returned_ids, list)
        assert len(returned_ids) == 1
        assert isinstance(returned_ids[0], str)

        # Row must now be visible via sync list_all.
        rows = store.working.list_all(scope)
        assert any(r.id == returned_ids[0] for r in rows), (
            "Written row not found in working.list_all()"
        )
        written = next(r for r in rows if r.id == returned_ids[0])
        assert written.content == "hello async"
        assert written.metadata.get("role") == "user"

    def test_add_messages_async_same_row_count_as_sync(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Async and sync add_messages on isolated scopes produce the same row count."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        messages = [
            {"role": "user", "content": "first message async"},
            {"role": "assistant", "content": "second message async"},
        ]

        # Async scope.
        async_agent_id = unique_agent_id
        async_scope = MemoryScope(agent_id=async_agent_id, user_id="user-async-cmp")
        store_a = MemoryStore(migrated_pool, enable_chunking=False)
        async_ids = asyncio.run(store_a.add_messages_async(messages, async_scope))

        # Sync scope — fresh unique agent for isolation.
        sync_agent_id = f"test-agent-{uuid.uuid4()}"
        sync_scope = MemoryScope(agent_id=sync_agent_id, user_id="user-async-cmp")
        store_s = MemoryStore(migrated_pool, enable_chunking=False)
        sync_ids = store_s.add_messages(messages, sync_scope)

        # Both must return the same number of IDs.
        assert len(async_ids) == len(sync_ids) == len(messages)

        # The async-written rows must be in Db2.
        async_rows = store_a.working.list_all(async_scope)
        assert len(async_rows) == len(messages)

        # The sync-written rows must be in Db2.
        sync_rows = store_s.working.list_all(sync_scope)
        assert len(sync_rows) == len(messages)

        # Both sets of rows must carry the correct content (order from list_all
        # is newest-first, so sort by content for a stable comparison).
        async_contents = sorted(r.content for r in async_rows)
        sync_contents = sorted(r.content for r in sync_rows)
        assert async_contents == sync_contents


# ---------------------------------------------------------------------------
# Test 3 — get_context_card_async returns a ContextCard
# ---------------------------------------------------------------------------


class TestGetContextCardAsync:
    """get_context_card_async must return a ContextCard equivalent to the sync call."""

    def test_get_context_card_async_returns_context_card(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """get_context_card_async on a seeded scope returns a ContextCard with turn_count > 0."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.types import ContextCard

        store = MemoryStore(migrated_pool, enable_chunking=False)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-async-cc")

        for i in range(3):
            store.remember(
                WorkingMemory(agent_id=unique_agent_id, content=f"turn {i}"),
                scope,
            )

        card = asyncio.run(store.get_context_card_async(scope, max_turns=5))

        assert isinstance(card, ContextCard)
        assert card.turn_count > 0
        assert len(card.turns) == card.turn_count

    def test_get_context_card_async_equivalent_to_sync(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Async and sync get_context_card on the same scope return equivalent cards."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.types import ContextCard

        store = MemoryStore(migrated_pool, enable_chunking=False)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="user-async-cc-eq")

        for i in range(4):
            store.remember(
                WorkingMemory(agent_id=unique_agent_id, content=f"msg {i}"),
                scope,
            )

        sync_card = store.get_context_card(scope, max_turns=5)
        async_card = asyncio.run(store.get_context_card_async(scope, max_turns=5))

        assert isinstance(async_card, ContextCard)
        assert async_card.turn_count == sync_card.turn_count
        assert [t.id for t in async_card.turns] == [t.id for t in sync_card.turns]
        assert [t.content for t in async_card.turns] == [
            t.content for t in sync_card.turns
        ]


# ---------------------------------------------------------------------------
# Test 4 — concurrent async calls against two different scopes
# ---------------------------------------------------------------------------


class TestConcurrentAsyncCalls:
    """asyncio.gather() across two isolated scopes must complete without
    cross-contamination."""

    def test_concurrent_add_messages_two_scopes_no_cross_contamination(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Two overlapping add_messages_async calls on different scopes must each
        write only their own rows — zero cross-contamination."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope

        store = MemoryStore(migrated_pool, enable_chunking=False)

        agent_a = unique_agent_id
        agent_b = f"test-agent-{uuid.uuid4()}"

        scope_a = MemoryScope(agent_id=agent_a, user_id="user-concurrent")
        scope_b = MemoryScope(agent_id=agent_b, user_id="user-concurrent")

        messages_a = [
            {"role": "user", "content": "scope A message one"},
            {"role": "assistant", "content": "scope A message two"},
        ]
        messages_b = [
            {"role": "user", "content": "scope B message one"},
            {"role": "assistant", "content": "scope B message two"},
        ]

        async def _run() -> tuple[list[str], list[str]]:
            ids_a, ids_b = await asyncio.gather(
                store.add_messages_async(messages_a, scope_a),
                store.add_messages_async(messages_b, scope_b),
            )
            return ids_a, ids_b  # type: ignore[return-value]

        ids_a, ids_b = asyncio.run(_run())

        # Both calls must complete successfully and return the expected number of IDs.
        assert len(ids_a) == len(messages_a)
        assert len(ids_b) == len(messages_b)

        # No shared IDs between the two scopes.
        assert set(ids_a).isdisjoint(set(ids_b)), (
            "IDs from scope_a and scope_b must not overlap"
        )

        # Scope A rows are only visible in scope A.
        rows_a = store.working.list_all(scope_a)
        assert len(rows_a) == len(messages_a)
        contents_a = {r.content for r in rows_a}
        assert contents_a == {"scope A message one", "scope A message two"}

        # Scope B rows are only visible in scope B.
        rows_b = store.working.list_all(scope_b)
        assert len(rows_b) == len(messages_b)
        contents_b = {r.content for r in rows_b}
        assert contents_b == {"scope B message one", "scope B message two"}

        # Zero cross-contamination: scope A sees no scope B content and vice versa.
        assert contents_a.isdisjoint(contents_b), (
            "Scope A and scope B rows must not overlap in content"
        )

"""
tests/integration/test_thread_primitives.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-12: thread-level message/memory primitives.

Covers (in class order):
  TestAddGetDeleteMessages  — THRD-1: add_messages / get_messages / delete_message
  TestAddMemoryUserAgent    — THRD-2: add_memory / add_user / add_agent
  TestSearchFacade          — THRD-3: store.search() fan-out with HashEmbedder
  TestDeleteMemory          — THRD-8: delete_memory() table-agnostic dispatch

All tests are skipped automatically when DB2_DATABASE is not set.
Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class HashEmbedder:
    """Deterministic embedding provider — no external service needed.

    Returns make_unit_vec(1536, abs(hash(text)) % 1500).  Same text always
    produces the same unit vector; different texts with different hashes are
    orthogonal (cosine distance 1.0), so vector search is fully deterministic.
    """

    DIM = 1536

    def __call__(self, text: str) -> list[float]:
        return make_unit_vec(self.DIM, abs(hash(text)) % 1500)


# ---------------------------------------------------------------------------
# THRD-1: add_messages / get_messages / delete_message
# ---------------------------------------------------------------------------


class TestAddGetDeleteMessages:
    """THRD-1: message ingestion, chronological retrieval, soft-delete."""

    def test_add_messages_returns_three_ids(self, store, scope) -> None:
        """add_messages([3 dicts], scope) returns a list of 3 IDs."""
        ids = store.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
            scope,
        )
        assert len(ids) == 3
        # All IDs must be non-empty strings.
        for id_ in ids:
            assert isinstance(id_, str) and id_

    def test_get_messages_returns_all_in_chronological_order(
        self, store, scope
    ) -> None:
        """get_messages() returns all rows, oldest first."""
        ids = store.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
            scope,
        )

        msgs = store.get_messages(scope)

        assert len(msgs) == 3
        # Chronological order: the first message written should be index 0.
        assert msgs[0].id == ids[0]
        assert msgs[1].id == ids[1]
        assert msgs[2].id == ids[2]
        assert msgs[0].content == "msg1"
        assert msgs[1].content == "msg2"
        assert msgs[2].content == "msg3"

    def test_get_messages_slice(self, store, scope) -> None:
        """get_messages(scope, start=1, end=2) returns only the second message."""
        ids = store.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
            scope,
        )

        sliced = store.get_messages(scope, start=1, end=2)

        assert len(sliced) == 1
        assert sliced[0].id == ids[1]
        assert sliced[0].content == "msg2"

    def test_delete_message_returns_1_on_first_call(self, store, scope) -> None:
        """delete_message() returns 1 when the row is found and tombstoned."""
        ids = store.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
            scope,
        )
        msg1_id = ids[0]

        result = store.delete_message(msg1_id, scope)

        assert result == 1

    def test_get_messages_after_delete_excludes_deleted_row(
        self, store, scope
    ) -> None:
        """After deleting msg1, get_messages() returns only msg2 and msg3."""
        ids = store.add_messages(
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
            scope,
        )
        store.delete_message(ids[0], scope)

        remaining = store.get_messages(scope)

        assert len(remaining) == 2
        returned_ids = [m.id for m in remaining]
        assert ids[0] not in returned_ids
        assert ids[1] in returned_ids
        assert ids[2] in returned_ids

    def test_delete_message_idempotent_returns_0(self, store, scope) -> None:
        """Deleting an already-deleted message returns 0."""
        ids = store.add_messages(
            [{"role": "user", "content": "msg1"}],
            scope,
        )
        msg1_id = ids[0]
        store.delete_message(msg1_id, scope)  # first delete

        result = store.delete_message(msg1_id, scope)  # second delete

        assert result == 0

    def test_delete_message_nonexistent_returns_0(self, store, scope) -> None:
        """Deleting a non-existent ID returns 0."""
        result = store.delete_message("nonexistent-id-that-does-not-exist", scope)

        assert result == 0


# ---------------------------------------------------------------------------
# THRD-2: add_memory / add_user / add_agent
# ---------------------------------------------------------------------------


class TestAddMemoryUserAgent:
    """THRD-2: durable memory and identity profile convenience wrappers."""

    def test_add_memory_creates_semantic_fact(self, store, scope) -> None:
        """add_memory() persists a SemanticFact row visible via facts.list_all()."""
        fact_id = store.add_memory("The user prefers dark mode", scope)

        assert isinstance(fact_id, str) and fact_id

        all_facts = store.facts.list_all(scope)
        fact_ids = [f.id for f in all_facts]
        assert fact_id in fact_ids

    def test_add_memory_content_is_stored(self, store, scope) -> None:
        """The content passed to add_memory() is retrievable from the store."""
        content = "The user prefers dark mode"
        fact_id = store.add_memory(content, scope)

        all_facts = store.facts.list_all(scope)
        matching = [f for f in all_facts if f.id == fact_id]
        assert len(matching) == 1
        assert matching[0].content == content

    def test_add_user_creates_entity_profile(self, store, scope) -> None:
        """add_user() upserts an EntityProfile retrievable via profiles.list_all()."""
        from agent_memory_sdk.models import MemoryScope

        profile_id = store.add_user(scope.user_id, "Alice, software engineer", scope)

        assert isinstance(profile_id, str) and profile_id

        # add_user() scopes the profile to (agent_id, user_id, thread_id=None).
        profile_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            thread_id=None,
        )
        profiles = store.profiles.list_all(profile_scope)
        profile_ids = [p.id for p in profiles]
        assert profile_id in profile_ids

    def test_add_user_upsert_same_user_id(self, store, scope) -> None:
        """Second add_user() with same user_id upserts — only 1 row in profiles."""
        from agent_memory_sdk.models import MemoryScope

        store.add_user(scope.user_id, "Alice, software engineer", scope)
        store.add_user(scope.user_id, "Alice, senior engineer", scope)

        profile_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            thread_id=None,
        )
        profiles = store.profiles.list_all(profile_scope)
        # Must have exactly one row — not two.
        assert len(profiles) == 1
        assert profiles[0].content == "Alice, senior engineer"

    def test_add_agent_creates_entity_profile(self, store, scope) -> None:
        """add_agent() stores an agent-scoped EntityProfile."""
        from agent_memory_sdk.models import MemoryScope

        profile_id = store.add_agent(scope.agent_id, "AI coding assistant", scope)

        assert isinstance(profile_id, str) and profile_id

        # add_agent() scopes to (agent_id, user_id=None, thread_id=None).
        agent_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=None,
            thread_id=None,
        )
        profiles = store.profiles.list_all(agent_scope)
        profile_ids = [p.id for p in profiles]
        assert profile_id in profile_ids

    def test_add_agent_content_is_stored(self, store, scope) -> None:
        """The profile_text passed to add_agent() is retrievable from the store."""
        from agent_memory_sdk.models import MemoryScope

        content = "AI coding assistant"
        profile_id = store.add_agent(scope.agent_id, content, scope)

        agent_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=None,
            thread_id=None,
        )
        profiles = store.profiles.list_all(agent_scope)
        matching = [p for p in profiles if p.id == profile_id]
        assert len(matching) == 1
        assert matching[0].content == content


# ---------------------------------------------------------------------------
# THRD-3: search() facade
# ---------------------------------------------------------------------------


class TestSearchFacade:
    """THRD-3: store.search() fan-out across memory record types."""

    def _make_store(self, migrated_pool):
        """Return a MemoryStore wired with HashEmbedder for deterministic search."""
        from agent_memory_sdk import MemoryStore

        return MemoryStore(migrated_pool, embedding_provider=HashEmbedder())

    def test_search_returns_search_result_objects(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """search() returns a list of SearchResult instances."""
        from agent_memory_sdk import SearchResult
        from agent_memory_sdk.models import MemoryScope

        store = self._make_store(migrated_pool)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="search-user-1")

        store.add_memory("The user enjoys hiking on weekends", scope)

        results = store.search("hiking", scope, max_results=5)

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, SearchResult)

    def test_search_results_capped_at_max_results(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """search() never returns more than max_results entries."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = self._make_store(migrated_pool)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="search-user-cap")

        # Seed more facts than max_results.
        for i in range(8):
            store.remember(
                SemanticFact(
                    agent_id=scope.agent_id,
                    user_id=scope.user_id,
                    content=f"fact number {i} about preferences",
                ),
                scope,
            )

        max_results = 3
        results = store.search("preferences", scope, max_results=max_results)

        assert len(results) <= max_results

    def test_search_record_types_filter_facts_only(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Passing record_types=['facts'] returns only SemanticFact results."""
        from agent_memory_sdk.models import (
            MemoryScope,
            ProceduralMemory,
            SemanticFact,
            WorkingMemory,
        )

        store = self._make_store(migrated_pool)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="search-user-ft")

        # Seed one record of every type to check the filter is honoured.
        store.remember(
            SemanticFact(
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                content="user likes coffee",
            ),
            scope,
        )
        store.remember(
            WorkingMemory(
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                content="user likes coffee",
            ),
            scope,
        )
        store.remember(
            ProceduralMemory(
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                content="user likes coffee",
            ),
            scope,
        )

        results = store.search(
            "user likes coffee", scope, record_types=["facts"], max_results=10
        )

        assert len(results) >= 1
        for r in results:
            assert r.record_type == "facts"

    def test_search_metadata_filter_narrows_results(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """metadata_filter={'source': 'test'} returns only matching facts (ORC-3 passthrough)."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = self._make_store(migrated_pool)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="search-user-mf")

        # One fact WITH the metadata tag.
        tagged = SemanticFact(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="tagged fact about preferences",
            metadata={"source": "test"},
        )
        store.remember(tagged, scope)

        # One fact WITHOUT the metadata tag.
        untagged = SemanticFact(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="untagged fact about preferences",
            metadata={"source": "other"},
        )
        store.remember(untagged, scope)

        results = store.search(
            "preferences",
            scope,
            record_types=["facts"],
            max_results=10,
            metadata_filter={"source": "test"},
        )

        # All returned results must carry the matching metadata tag.
        for r in results:
            assert r.record.metadata.get("source") == "test"

        # The tagged fact must appear in results; the untagged one must not.
        result_ids = {r.id for r in results}
        assert tagged.id in result_ids
        assert untagged.id not in result_ids


# ---------------------------------------------------------------------------
# THRD-8: delete_memory() — table-agnostic dispatch
# ---------------------------------------------------------------------------


class TestDeleteMemory:
    """THRD-8: delete_memory() soft-deletes across facts, profiles, procedures."""

    def test_delete_fact_returns_1_and_tombstones_fact(
        self, store, scope
    ) -> None:
        """delete_memory(fact_id) tombstones the SemanticFact row."""
        fact_id = store.add_memory("Fact to be deleted", scope)

        result = store.delete_memory(fact_id, scope)

        assert result == 1
        remaining_facts = store.facts.list_all(scope)
        remaining_ids = [f.id for f in remaining_facts]
        assert fact_id not in remaining_ids

    def test_delete_profile_returns_1_and_tombstones_profile(
        self, store, scope
    ) -> None:
        """delete_memory(profile_id) tombstones the EntityProfile row."""
        from agent_memory_sdk.models import EntityProfile, MemoryScope

        profile = EntityProfile(
            agent_id=scope.agent_id,
            user_id="del-user-profile",
            content="Profile to be deleted",
        )
        profile_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id="del-user-profile",
            thread_id=None,
        )
        stored = store.remember(profile, profile_scope)
        profile_id = stored.id

        result = store.delete_memory(profile_id, scope)

        assert result == 1
        remaining_profiles = store.profiles.list_all(profile_scope)
        remaining_ids = [p.id for p in remaining_profiles]
        assert profile_id not in remaining_ids

    def test_delete_procedure_returns_1_and_tombstones_procedure(
        self, store, scope
    ) -> None:
        """delete_memory(procedure_id) tombstones the ProceduralMemory row."""
        from agent_memory_sdk.models import ProceduralMemory

        procedure = ProceduralMemory(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="Procedure to be deleted",
        )
        stored = store.remember(procedure, scope)
        procedure_id = stored.id

        result = store.delete_memory(procedure_id, scope)

        assert result == 1
        remaining_procedures = store.procedures.list_all(scope)
        remaining_ids = [p.id for p in remaining_procedures]
        assert procedure_id not in remaining_ids

    def test_delete_fact_does_not_touch_other_tables(
        self, store, scope
    ) -> None:
        """Deleting a fact leaves profiles and procedures in scope intact."""
        from agent_memory_sdk.models import EntityProfile, MemoryScope, ProceduralMemory

        # Create one record in each durable table.
        fact_id = store.add_memory("Fact - should be deleted", scope)

        proc_scope = scope  # procedures share the same scope
        procedure = ProceduralMemory(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="Procedure - must survive",
        )
        stored_proc = store.remember(procedure, proc_scope)
        procedure_id = stored_proc.id

        profile_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id="intact-profile-user",
            thread_id=None,
        )
        profile = EntityProfile(
            agent_id=scope.agent_id,
            user_id="intact-profile-user",
            content="Profile - must survive",
        )
        stored_profile = store.remember(profile, profile_scope)
        profile_id = stored_profile.id

        # Delete only the fact.
        assert store.delete_memory(fact_id, scope) == 1

        # Fact is gone.
        fact_ids = [f.id for f in store.facts.list_all(scope)]
        assert fact_id not in fact_ids

        # Procedure is still there.
        proc_ids = [p.id for p in store.procedures.list_all(proc_scope)]
        assert procedure_id in proc_ids

        # Profile is still there.
        profile_ids = [p.id for p in store.profiles.list_all(profile_scope)]
        assert profile_id in profile_ids

    def test_delete_procedure_does_not_touch_other_tables(
        self, store, scope
    ) -> None:
        """Deleting a procedure leaves facts and profiles in scope intact."""
        from agent_memory_sdk.models import EntityProfile, MemoryScope, ProceduralMemory

        fact_id = store.add_memory("Fact - must survive", scope)

        procedure = ProceduralMemory(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="Procedure - should be deleted",
        )
        stored_proc = store.remember(procedure, scope)
        procedure_id = stored_proc.id

        profile_scope = MemoryScope(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id="intact-profile-user-2",
            thread_id=None,
        )
        profile = EntityProfile(
            agent_id=scope.agent_id,
            user_id="intact-profile-user-2",
            content="Profile - must survive",
        )
        stored_profile = store.remember(profile, profile_scope)
        profile_id = stored_profile.id

        # Delete only the procedure.
        assert store.delete_memory(procedure_id, scope) == 1

        # Procedure is gone.
        proc_ids = [p.id for p in store.procedures.list_all(scope)]
        assert procedure_id not in proc_ids

        # Fact is still there.
        fact_ids = [f.id for f in store.facts.list_all(scope)]
        assert fact_id in fact_ids

        # Profile is still there.
        profile_ids = [p.id for p in store.profiles.list_all(profile_scope)]
        assert profile_id in profile_ids

    def test_delete_memory_nonexistent_returns_0(self, store, scope) -> None:
        """delete_memory() returns 0 for an ID that doesn't exist in any table."""
        result = store.delete_memory("nonexistent-id-not-in-any-table", scope)

        assert result == 0

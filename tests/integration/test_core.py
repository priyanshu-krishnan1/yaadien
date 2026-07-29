"""
tests/integration/test_core.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for the core SDK: repositories, MemoryStore, lifecycle.

Covers:
- Basic create / get_by_id round-trip for each of the five memory types.
- Vector search correctness: known nearest neighbour (unit vectors with
  deterministic cosine similarity).
- Scope isolation: rows written by agent A are invisible to agent B.
- forget() / tombstone: soft-deleted rows are excluded from reads.
- purge_expired(): tombstoned rows are hard-deleted; live rows survive.
- TTL (expires_at): expired rows are excluded from list_all / search even
  if not yet tombstoned.
- Optimistic concurrency (update() with stale version raises StaleWriteError).
- Consolidator: a custom consolidator persists derived SemanticFact rows.

Each test function uses a fresh ``unique_agent_id`` / ``scope`` so there is
no inter-test state pollution.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See INTEGRATION_TESTING.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Basic CRUD round-trips for each memory type
# ---------------------------------------------------------------------------


class TestCrudRoundTrip:
    """create() → get_by_id() for every memory type."""

    def test_working_memory_round_trip(self, store, scope):
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id,
            content="Hello from working memory",
            metadata={"role": "user"},
        )
        stored = store.remember(record, scope)
        assert stored.id
        assert stored.created_at is not None

        fetched = store.working.get_by_id(stored.id, scope)
        assert fetched is not None
        assert fetched.content == "Hello from working memory"
        assert fetched.metadata.get("role") == "user"
        assert fetched.version == 1

    def test_episodic_memory_round_trip(self, store, scope):
        from agent_memory_sdk.models import EpisodicMemory

        record = EpisodicMemory(
            agent_id=scope.agent_id,
            content="Summary of past session",
        )
        stored = store.remember(record, scope)
        fetched = store.episodic.get_by_id(stored.id, scope)
        assert fetched is not None
        assert fetched.content == "Summary of past session"

    def test_semantic_fact_round_trip(self, store, scope):
        from agent_memory_sdk.models import SemanticFact

        fact = SemanticFact(
            agent_id=scope.agent_id,
            content="User prefers Python over Java",
            metadata={"confidence": "0.95"},
        )
        stored = store.remember(fact, scope)
        fetched = store.facts.get_by_id(stored.id, scope)
        assert fetched is not None
        assert fetched.content == "User prefers Python over Java"

    def test_entity_profile_round_trip(self, store, scope):
        from agent_memory_sdk.models import EntityProfile

        profile = EntityProfile(
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            content="Power Python developer; prefers dark mode.",
        )
        stored = store.profiles.create(profile, scope)
        fetched = store.profiles.get_by_id(stored.id, scope)
        assert fetched is not None
        assert "dark mode" in fetched.content

    def test_procedural_memory_round_trip(self, store, scope):
        from agent_memory_sdk.models import ProceduralMemory

        skill = ProceduralMemory(
            agent_id=scope.agent_id,
            content="When debugging Python, check the traceback first.",
        )
        stored = store.procedures.create(skill, scope)
        fetched = store.procedures.get_by_id(stored.id, scope)
        assert fetched is not None
        assert "traceback" in fetched.content

    def test_scope_fields_denormalized_on_row(self, store, scope):
        """Scope fields written to the record must survive the round-trip."""
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(agent_id=scope.agent_id, content="scoped")
        stored = store.working.create(record, scope)
        fetched = store.working.get_by_id(stored.id, scope)
        assert fetched is not None
        assert fetched.agent_id == scope.agent_id
        assert fetched.user_id == scope.user_id


# ---------------------------------------------------------------------------
# Vector search correctness
# ---------------------------------------------------------------------------


class TestVectorSearch:
    """Verify that VECTOR_DISTANCE COSINE returns the closest neighbour."""

    def test_exact_nearest_neighbour_working(self, store, scope, vec_dim):
        """The most similar embedding must rank first in the search results."""
        from agent_memory_sdk.models import WorkingMemory

        # Two orthogonal unit vectors — cosine distance = 1.0 between them,
        # 0.0 from themselves (cosine similarity 0 vs 1).
        vec_a = make_unit_vec(vec_dim, 0)
        vec_b = make_unit_vec(vec_dim, 1)

        rec_a = WorkingMemory(
            agent_id=scope.agent_id, content="message A", embedding=vec_a
        )
        rec_b = WorkingMemory(
            agent_id=scope.agent_id, content="message B", embedding=vec_b
        )
        stored_a = store.working.create(rec_a, scope)
        store.working.create(rec_b, scope)

        results = store.working.search(
            query_embedding=vec_a,
            scope=scope,
            top_k=2,
        )

        assert len(results) >= 1, "Expected at least one result"
        # The closest match to vec_a must be stored_a
        assert results[0].id == stored_a.id, (
            f"Expected record A (id={stored_a.id}) as nearest neighbour, "
            f"got id={results[0].id} (content={results[0].content!r})"
        )

    def test_vector_search_returns_at_most_top_k(self, store, scope, vec_dim):
        """search(..., top_k=2) must return at most 2 results."""
        from agent_memory_sdk.models import EpisodicMemory

        for i in range(5):
            ep = EpisodicMemory(
                agent_id=scope.agent_id,
                content=f"episode {i}",
                embedding=make_unit_vec(vec_dim, i),
            )
            store.episodic.create(ep, scope)

        results = store.episodic.search(
            query_embedding=make_unit_vec(vec_dim, 0),
            scope=scope,
            top_k=2,
        )
        assert len(results) <= 2, f"Expected ≤ 2 results for top_k=2, got {len(results)}"

    def test_search_excludes_deleted_rows(self, store, scope, vec_dim):
        """Tombstoned rows must not appear in vector search results."""
        from agent_memory_sdk.models import WorkingMemory

        vec = make_unit_vec(vec_dim, 5)
        record = WorkingMemory(
            agent_id=scope.agent_id, content="to be deleted", embedding=vec
        )
        stored = store.working.create(record, scope)
        store.working.forget(stored.id, scope)

        results = store.working.search(
            query_embedding=vec, scope=scope, top_k=10
        )
        ids = [r.id for r in results]
        assert stored.id not in ids, (
            "Tombstoned row must not appear in vector search results"
        )

    def test_search_scope_isolation(self, store, scope, unique_agent_id, vec_dim):
        """Rows created under agent_a must not appear in agent_b search."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        vec = make_unit_vec(vec_dim, 10)
        record = WorkingMemory(
            agent_id=scope.agent_id, content="agent A secret", embedding=vec
        )
        store.working.create(record, scope)

        # Search with a different agent's scope
        other_scope = MemoryScope(agent_id=f"other-{unique_agent_id}")
        results = store.working.search(
            query_embedding=vec, scope=other_scope, top_k=10
        )
        for r in results:
            assert r.agent_id != scope.agent_id, (
                "Cross-scope vector search returned a row from a different agent"
            )


# ---------------------------------------------------------------------------
# Scope isolation (list_all / get_by_id)
# ---------------------------------------------------------------------------


class TestScopeIsolation:
    """Rows scoped to agent A must not be visible to agent B."""

    def test_list_all_scope_isolation(self, store, scope, vec_dim):
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id, content="private"
        )
        store.working.create(record, scope)

        other_scope = MemoryScope(agent_id=f"other-{scope.agent_id}")
        rows = store.working.list_all(scope=other_scope, limit=100)
        for row in rows:
            assert row.agent_id != scope.agent_id, (
                "list_all returned rows belonging to a different agent"
            )

    def test_get_by_id_cross_scope_returns_none(self, store, scope):
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id, content="owner only"
        )
        stored = store.working.create(record, scope)

        other_scope = MemoryScope(agent_id=f"other-{scope.agent_id}")
        fetched = store.working.get_by_id(stored.id, other_scope)
        assert fetched is None, (
            "get_by_id with wrong scope must return None, not the owner's row"
        )

    def test_thread_scope_isolation(self, store, unique_agent_id):
        """Thread-scoped rows must not bleed across threads."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        scope_t1 = MemoryScope(agent_id=unique_agent_id, thread_id="thread-1")
        scope_t2 = MemoryScope(agent_id=unique_agent_id, thread_id="thread-2")

        record = WorkingMemory(
            agent_id=unique_agent_id, content="thread 1 message"
        )
        store.working.create(record, scope_t1)

        rows_t2 = store.working.list_all(scope=scope_t2, limit=100)
        for row in rows_t2:
            assert row.thread_id != "thread-1", (
                "Thread-scoped row from thread-1 visible from thread-2 scope"
            )


# ---------------------------------------------------------------------------
# forget() / tombstone
# ---------------------------------------------------------------------------


class TestForgetTombstone:
    """forget() must soft-delete rows so they disappear from all read paths."""

    def test_forget_hides_row_from_get_by_id(self, store, scope):
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(agent_id=scope.agent_id, content="to forget")
        stored = store.working.create(record, scope)

        result = store.working.forget(stored.id, scope)
        assert result is True, "forget() should return True when row was found"

        fetched = store.working.get_by_id(stored.id, scope)
        assert fetched is None, "get_by_id must return None after forget()"

    def test_forget_hides_row_from_list_all(self, store, scope):
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(agent_id=scope.agent_id, content="hidden")
        stored = store.working.create(record, scope)
        store.working.forget(stored.id, scope)

        rows = store.working.list_all(scope=scope, limit=1000)
        ids = [r.id for r in rows]
        assert stored.id not in ids, "list_all must exclude tombstoned rows"

    def test_forget_returns_false_for_nonexistent_id(self, store, scope):
        result = store.working.forget("non-existent-uuid", scope)
        assert result is False

    def test_store_forget_facade(self, store, scope):
        """MemoryStore.forget() convenience method must work end-to-end."""
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(agent_id=scope.agent_id, content="facade forget")
        stored = store.remember(record, scope)
        ok = store.forget(stored.id, "working", scope)
        assert ok is True

        fetched = store.working.get_by_id(stored.id, scope)
        assert fetched is None


# ---------------------------------------------------------------------------
# purge_expired() — hard-delete of tombstoned rows
# ---------------------------------------------------------------------------


class TestPurgeExpired:
    """purge_expired() must hard-delete tombstoned rows and leave live rows."""

    def test_purge_removes_tombstoned_rows(self, store, scope):
        from agent_memory_sdk.models import WorkingMemory

        # Write two rows; tombstone one.
        live = WorkingMemory(agent_id=scope.agent_id, content="live")
        dead = WorkingMemory(agent_id=scope.agent_id, content="dead")
        stored_live = store.working.create(live, scope)
        stored_dead = store.working.create(dead, scope)
        store.working.forget(stored_dead.id, scope)

        counts = store.purge_expired(scope)
        assert counts.get("working_memory", 0) >= 1, (
            "purge_expired should have removed at least the tombstoned row"
        )

        # The live row must still be fetchable.
        fetched_live = store.working.get_by_id(stored_live.id, scope)
        assert fetched_live is not None, "Live row must survive purge_expired()"

    def test_purge_returns_zero_when_nothing_to_delete(self, store, scope):
        """A scope with no tombstoned rows must return 0 for all tables."""
        # Write a live row (not tombstoned).
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(agent_id=scope.agent_id, content="untouched")
        store.working.create(record, scope)

        counts = store.purge_expired(scope)
        for table, count in counts.items():
            assert count == 0, (
                f"Expected 0 purged rows for {table} (nothing was tombstoned), "
                f"got {count}"
            )

    def test_purge_scope_isolation(self, store, scope, unique_agent_id):
        """purge_expired() with scope A must not touch rows in scope B."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory

        # Create and tombstone a row in scope A.
        rec = WorkingMemory(agent_id=scope.agent_id, content="purge me")
        stored = store.working.create(rec, scope)
        store.working.forget(stored.id, scope)

        # Create a live row in scope B (different agent).
        scope_b = MemoryScope(agent_id=f"other-{unique_agent_id}")
        rec_b = WorkingMemory(agent_id=scope_b.agent_id, content="keep me")
        stored_b = store.working.create(rec_b, scope_b)

        # Purge only scope A.
        store.purge_expired(scope)

        # Row in scope B must still exist.
        fetched = store.working.get_by_id(stored_b.id, scope_b)
        assert fetched is not None, (
            "purge_expired() for scope A must not delete rows belonging to scope B"
        )


# ---------------------------------------------------------------------------
# TTL (expires_at)
# ---------------------------------------------------------------------------


class TestTTL:
    """Rows with expires_at in the past must be excluded from reads."""

    def test_expired_row_excluded_from_list_all(self, store, scope):
        """A row with expires_at in the past is excluded from list_all."""
        from agent_memory_sdk.models import WorkingMemory

        past = _now_utc() - timedelta(seconds=1)
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content="expired content",
            expires_at=past,
        )
        stored = store.working.create(record, scope)

        rows = store.working.list_all(scope=scope, limit=1000)
        ids = [r.id for r in rows]
        assert stored.id not in ids, (
            "Expired row (expires_at in the past) must not appear in list_all"
        )

    def test_live_row_included_in_list_all(self, store, scope):
        """A row with expires_at far in the future must appear in list_all."""
        from agent_memory_sdk.models import WorkingMemory

        future = _now_utc() + timedelta(days=365)
        record = WorkingMemory(
            agent_id=scope.agent_id,
            content="future content",
            expires_at=future,
        )
        stored = store.working.create(record, scope)

        rows = store.working.list_all(scope=scope, limit=1000)
        ids = [r.id for r in rows]
        assert stored.id in ids, (
            "Row with future expires_at must appear in list_all"
        )

    def test_no_expiry_row_always_included(self, store, scope):
        """A row with no expires_at must always appear in list_all."""
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id, content="no expiry"
        )
        stored = store.working.create(record, scope)

        rows = store.working.list_all(scope=scope, limit=1000)
        ids = [r.id for r in rows]
        assert stored.id in ids, (
            "Row without expires_at must appear in list_all"
        )


# ---------------------------------------------------------------------------
# Optimistic concurrency
# ---------------------------------------------------------------------------


class TestOptimisticConcurrency:
    """update() must increment version and raise StaleWriteError on conflict."""

    def test_update_increments_version(self, store, scope):
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id, content="original"
        )
        stored = store.working.create(record, scope)
        assert stored.version == 1

        stored.content = "updated"
        updated = store.working.update(stored, scope)
        assert updated.version == 2
        assert updated.content == "updated"

    def test_update_stale_version_raises(self, store, scope):
        from agent_memory_sdk.exceptions import StaleWriteError
        from agent_memory_sdk.models import WorkingMemory

        record = WorkingMemory(
            agent_id=scope.agent_id, content="stale test"
        )
        stored = store.working.create(record, scope)  # version=1 in DB

        # Simulate a stale read: version is behind actual DB version.
        stored.content = "update 1"
        store.working.update(stored, scope)  # mutates stored.version to 2; DB now has version=2

        # Force the object back to the old version to simulate a stale concurrent reader.
        stored.version = 1
        stored.content = "update 2"
        with pytest.raises(StaleWriteError):
            store.working.update(stored, scope)


# ---------------------------------------------------------------------------
# Consolidator integration
# ---------------------------------------------------------------------------


class TestConsolidator:
    """A custom consolidator must persist derived records when remember() fires."""

    def test_consolidator_derives_semantic_fact(self, migrated_pool, unique_agent_id):
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory

        derived_tag = f"derived-{unique_agent_id}"

        def my_consolidator(raw_memories: list[Any]) -> list[Any]:
            return [
                SemanticFact(
                    agent_id=unique_agent_id,
                    content=f"derived fact from consolidation (tag={derived_tag})",
                )
            ]

        store_with_consolidator = MemoryStore(
            migrated_pool, consolidator=my_consolidator
        )
        scope = MemoryScope(agent_id=unique_agent_id)

        record = WorkingMemory(
            agent_id=unique_agent_id, content="trigger consolidation"
        )
        store_with_consolidator.remember(record, scope)

        facts = store_with_consolidator.facts.list_all(scope=scope, limit=100)
        derived = [f for f in facts if derived_tag in f.content]
        assert len(derived) >= 1, (
            "Consolidator should have written at least one SemanticFact"
        )

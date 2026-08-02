"""
tests/integration/test_ingest_resolver.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-8: IngestResolver ADD/UPDATE/DELETE/NOOP dispatch.

Covers all four IngestAction outcomes against real Db2 rows using small
deterministic test resolvers (no LLM).

  - TestIngestResolverADD    — resolver always returns ADD; row is inserted.
  - TestIngestResolverUPDATE — resolver returns UPDATE with target_id; existing
                               row is mutated, no new row is created.
  - TestIngestResolverDELETE — resolver returns DELETE with target_id; existing
                               row is tombstoned, candidate is not written.
  - TestIngestResolverNOOP   — resolver returns NOOP; nothing is written.
  - TestIngestResolverCrossScope — similarity search in _candidate_embedding()
                               is scoped and must not leak rows across scopes.

All tests are gated behind the ``integration`` pytest marker and skipped
automatically when ``DB2_DATABASE`` is not set.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Raw-DB helper — read deleted_at for a semantic_facts row without going
# through the repository layer (which hides tombstoned rows).
# ---------------------------------------------------------------------------


def _fetch_deleted_at(pool, fact_id: str):
    """Return the raw ``deleted_at`` value for *fact_id* from semantic_facts.

    Bypasses the repository layer so tests can inspect tombstoned rows after
    a DELETE dispatch.  Returns ``None`` when the row is not tombstoned or
    does not exist.
    """
    sql = "SELECT deleted_at FROM semantic_facts WHERE id = ?"
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [fact_id])
        row = cur.fetchone()
    if row is None:
        return None
    return row[0]


# ---------------------------------------------------------------------------
# Deterministic test resolvers — no LLM
# ---------------------------------------------------------------------------


class _AlwaysAddResolver:
    """Always returns ADD regardless of the similar-records list."""

    def __call__(self, candidate, similar):
        from agent_memory_sdk.types import IngestAction, IngestDecision

        return IngestDecision(action=IngestAction.ADD)


class _UpdateFirstSimilarResolver:
    """Returns UPDATE targeting the first similar record if any exist;
    otherwise falls back to ADD."""

    def __call__(self, candidate, similar):
        from agent_memory_sdk.types import IngestAction, IngestDecision

        if similar:
            target_id = similar[0][0].id
            return IngestDecision(
                action=IngestAction.UPDATE,
                target_id=target_id,
                reason="test: merge into most-similar existing row",
            )
        return IngestDecision(action=IngestAction.ADD)


class _DeleteFirstSimilarResolver:
    """Returns DELETE targeting the first similar record if any exist;
    otherwise returns ADD (safe fallback — no target to delete)."""

    def __call__(self, candidate, similar):
        from agent_memory_sdk.types import IngestAction, IngestDecision

        if similar:
            target_id = similar[0][0].id
            return IngestDecision(
                action=IngestAction.DELETE,
                target_id=target_id,
                reason="test: tombstone most-similar existing row",
            )
        return IngestDecision(action=IngestAction.ADD)


class _AlwaysNoopResolver:
    """Always returns NOOP regardless of the similar-records list."""

    def __call__(self, candidate, similar):
        from agent_memory_sdk.types import IngestAction, IngestDecision

        return IngestDecision(action=IngestAction.NOOP)


class _CaptureResolver:
    """Records the ``similar`` list passed to it, then returns ADD.

    Used by the cross-scope isolation test to inspect what the resolver
    actually received from the similarity search.
    """

    def __init__(self):
        self.captured_similar = None

    def __call__(self, candidate, similar):
        from agent_memory_sdk.types import IngestAction, IngestDecision

        self.captured_similar = list(similar)
        return IngestDecision(action=IngestAction.ADD)


# ---------------------------------------------------------------------------
# TestIngestResolverADD
# ---------------------------------------------------------------------------


class TestIngestResolverADD:
    """resolver returns ADD → row is inserted normally."""

    def test_add_inserts_row_and_count_is_one(self, migrated_pool, unique_agent_id, vec_dim):
        """After an ADD decision the row must exist and be the only row in scope."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-ir-add")
        store = MemoryStore(
            migrated_pool,
            ingest_resolver=_AlwaysAddResolver(),
            resolver_k=5,
        )

        vec = make_unit_vec(vec_dim, 10)
        fact = SemanticFact(
            agent_id=unique_agent_id,
            content="ADD dispatch test fact",
            embedding=vec,
        )
        stored = store.remember(fact, scope)

        # Row must be fetchable by id
        fetched = store.facts.get_by_id(stored.id, scope)
        assert fetched is not None, (
            "get_by_id must return the row after an ADD dispatch"
        )
        assert fetched.content == "ADD dispatch test fact", (
            "Content must survive the ADD round-trip"
        )

        # Exactly one row in this isolated scope
        all_rows = store.facts.list_all(scope, limit=200)
        assert len(all_rows) == 1, (
            f"Expected exactly 1 row in scope after ADD, got {len(all_rows)}"
        )
        assert all_rows[0].id == stored.id, (
            "The single row in scope must be the one that was just stored"
        )


# ---------------------------------------------------------------------------
# TestIngestResolverUPDATE
# ---------------------------------------------------------------------------


class TestIngestResolverUPDATE:
    """resolver returns UPDATE → existing row is mutated, no new row is created."""

    def test_update_mutates_existing_row_no_new_row(
        self, migrated_pool, unique_agent_id, vec_dim
    ):
        """After an UPDATE dispatch the original row must hold the new content
        and the scope must still contain exactly one row."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-ir-upd")

        # Baseline store — no resolver — used to pre-insert the existing row.
        baseline_store = MemoryStore(migrated_pool)

        vec = make_unit_vec(vec_dim, 20)
        old_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="old content",
            embedding=vec,
        )
        existing = baseline_store.remember(old_fact, scope)
        existing_id = existing.id
        original_version = existing.version

        # Resolver store — will target the pre-inserted row for UPDATE.
        resolver_store = MemoryStore(
            migrated_pool,
            ingest_resolver=_UpdateFirstSimilarResolver(),
            resolver_k=5,
        )

        new_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="updated content",
            embedding=vec,  # identical embedding so similarity search finds it
        )
        resolver_store.remember(new_fact, scope)

        # Row count must still be 1 — no new row inserted
        all_rows = resolver_store.facts.list_all(scope, limit=200)
        assert len(all_rows) == 1, (
            f"Expected exactly 1 row in scope after UPDATE dispatch, "
            f"got {len(all_rows)}"
        )

        # The EXISTING row must have the new content
        updated = resolver_store.facts.get_by_id(existing_id, scope)
        assert updated is not None, (
            f"Original row id={existing_id!r} must still exist after UPDATE dispatch"
        )
        assert updated.content == "updated content", (
            f"Original row content must be updated; got {updated.content!r}"
        )

        # Version must have been bumped by the optimistic-concurrency update()
        assert updated.version > original_version, (
            f"Version must be bumped after UPDATE; was {original_version}, "
            f"got {updated.version}"
        )


# ---------------------------------------------------------------------------
# TestIngestResolverDELETE
# ---------------------------------------------------------------------------


class TestIngestResolverDELETE:
    """resolver returns DELETE → target row is tombstoned, candidate not written."""

    def test_delete_tombstones_target_not_candidate(
        self, migrated_pool, unique_agent_id, vec_dim
    ):
        """After a DELETE dispatch the target row must be tombstoned and
        list_all() must return zero rows (candidate is not written either)."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-ir-del")

        baseline_store = MemoryStore(migrated_pool)

        vec = make_unit_vec(vec_dim, 30)
        existing_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="row to be tombstoned by DELETE dispatch",
            embedding=vec,
        )
        existing = baseline_store.remember(existing_fact, scope)
        existing_id = existing.id

        resolver_store = MemoryStore(
            migrated_pool,
            ingest_resolver=_DeleteFirstSimilarResolver(),
            resolver_k=5,
        )

        triggering_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="triggering fact — must NOT be written",
            embedding=vec,  # identical embedding so similarity search finds target
        )
        resolver_store.remember(triggering_fact, scope)

        # Target row must be tombstoned (deleted_at IS NOT NULL in raw DB)
        deleted_at = _fetch_deleted_at(migrated_pool, existing_id)
        assert deleted_at is not None, (
            f"Target row id={existing_id!r} must have deleted_at set after "
            "DELETE dispatch; got None"
        )

        # list_all() must exclude the tombstoned target
        all_rows = resolver_store.facts.list_all(scope, limit=200)
        all_ids = {r.id for r in all_rows}
        assert existing_id not in all_ids, (
            "list_all() must not return the tombstoned target row after DELETE dispatch"
        )

        # The triggering candidate itself must NOT have been written
        # (scope contains zero live rows)
        assert len(all_rows) == 0, (
            f"After DELETE dispatch the scope must contain 0 live rows; "
            f"got {len(all_rows)}"
        )


# ---------------------------------------------------------------------------
# TestIngestResolverNOOP
# ---------------------------------------------------------------------------


class TestIngestResolverNOOP:
    """resolver returns NOOP → nothing is written at all."""

    def test_noop_writes_nothing(self, migrated_pool, unique_agent_id, vec_dim):
        """After a NOOP dispatch the scope must contain zero rows."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-ir-noop")

        store = MemoryStore(
            migrated_pool,
            ingest_resolver=_AlwaysNoopResolver(),
            resolver_k=5,
        )

        vec = make_unit_vec(vec_dim, 40)
        fact = SemanticFact(
            agent_id=unique_agent_id,
            content="NOOP dispatch test — must not be persisted",
            embedding=vec,
        )
        store.remember(fact, scope)

        all_rows = store.facts.list_all(scope, limit=200)
        assert len(all_rows) == 0, (
            f"Expected 0 rows in scope after NOOP dispatch; got {len(all_rows)}"
        )


# ---------------------------------------------------------------------------
# TestIngestResolverCrossScope
# ---------------------------------------------------------------------------


class TestIngestResolverCrossScope:
    """Similarity search in _candidate_embedding() must be scope-isolated."""

    def test_resolver_receives_empty_similar_when_seeded_in_different_scope(
        self, migrated_pool, unique_agent_id, vec_dim
    ):
        """Seeding an identical-embedding row in scope_A must not cause the
        IngestResolver to see it when remember() is called in scope_B."""
        import uuid

        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        # scope_A and scope_B are different agents — fully isolated.
        agent_a = unique_agent_id
        agent_b = f"scope-b-{uuid.uuid4()}"

        scope_a = MemoryScope(agent_id=agent_a, user_id="test-user-ir-scope-a")
        scope_b = MemoryScope(agent_id=agent_b, user_id="test-user-ir-scope-b")

        # Shared vector — identical in both scopes so a cross-scope leak
        # would surface as a non-empty similar list.
        vec = make_unit_vec(vec_dim, 50)

        # Seed the identical fact in scope_A first.
        seed_store = MemoryStore(migrated_pool)
        seed_fact = SemanticFact(
            agent_id=agent_a,
            content="cross-scope seed fact",
            embedding=vec,
        )
        seed_store.remember(seed_fact, scope_a)

        # Now call remember() in scope_B with the capturing resolver.
        capture_resolver = _CaptureResolver()
        resolver_store = MemoryStore(
            migrated_pool,
            ingest_resolver=capture_resolver,
            resolver_k=5,
        )

        candidate = SemanticFact(
            agent_id=agent_b,
            content="scope_B candidate — resolver must see empty similar list",
            embedding=vec,
        )
        resolver_store.remember(candidate, scope_b)

        # The resolver must have been called (captured_similar is no longer None)
        assert capture_resolver.captured_similar is not None, (
            "_CaptureResolver was never called — remember() did not invoke the resolver"
        )

        # The similar list must be empty: scope_A's row must not bleed into scope_B
        assert capture_resolver.captured_similar == [], (
            "Similarity search must not return rows from a different agent scope; "
            f"got {capture_resolver.captured_similar!r}"
        )

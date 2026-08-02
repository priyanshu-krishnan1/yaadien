"""
tests/integration/test_reconciliation.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-2: reconciliation and soft-supersession (ENH-3).

Covers:
- Core reconcile() flow: seeding contradicting facts, running a deterministic
  OldestLosesReconciler, asserting supersession fields on the loser row, and
  verifying that list_all() / search() exclude the superseded row.
- Guard assertions: entity_profiles and procedural_memory rows are unaffected
  by reconcile('facts', …); supersede() on an already-deleted row is a no-op;
  supersede() on an already-superseded row is a no-op.

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
# Deterministic test reconciler — no LLM
# ---------------------------------------------------------------------------


class OldestLosesReconciler:
    """A deterministic reconciler for testing: older fact always loses.

    Given a list of candidates, finds the oldest and newest rows by
    ``created_at`` timestamp and returns a single :class:`SupersedeDecision`
    where the oldest row is the loser and the newest row is the winner.

    If all rows have the same ``created_at`` (or there are fewer than two
    candidates), returns an empty list so reconcile() is a no-op.
    """

    def __call__(self, candidates):
        from agent_memory_sdk.types import SupersedeDecision

        if len(candidates) < 2:
            return []

        # Sort by created_at ascending; None timestamps sort first (treat as
        # oldest possible).
        def _key(fact):
            return fact.created_at if fact.created_at is not None else 0

        sorted_facts = sorted(candidates, key=_key)
        oldest = sorted_facts[0]
        newest = sorted_facts[-1]

        # If timestamps are the same (cannot distinguish), do nothing.
        if oldest.created_at == newest.created_at:
            return []

        return [
            SupersedeDecision(
                winner_id=newest.id,
                loser_id=oldest.id,
                reason="test: oldest fact superseded by newest",
            )
        ]


# ---------------------------------------------------------------------------
# Helper — raw DB SELECT to read supersession columns on any row
# ---------------------------------------------------------------------------


def _fetch_supersession_fields(pool, fact_id: str) -> dict:
    """Return the three supersession columns for *fact_id* from the raw DB row.

    Bypasses the repository layer (which filters out superseded rows) so tests
    can inspect the loser's fields after reconciliation.
    """
    sql = """
        SELECT superseded_by, superseded_at, supersede_reason
        FROM semantic_facts
        WHERE id = ?
    """
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [fact_id])
        row = cur.fetchone()
    if row is None:
        return {}
    return {
        "superseded_by": row[0],
        "superseded_at": row[1],
        "supersede_reason": row[2],
    }


# ---------------------------------------------------------------------------
# Core reconcile() flow
# ---------------------------------------------------------------------------


class TestReconcileCoreFlow:
    """reconcile('facts', scope) with a real Reconciler and a live Db2 database."""

    def test_loser_supersession_fields_set_after_reconcile(
        self, migrated_pool, unique_agent_id
    ):
        """After reconcile(), the loser row must have all three supersession fields set."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-rec")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        # Seed two facts in the same scope via a plain store (no reconciler).
        older = SemanticFact(agent_id=unique_agent_id, content="older fact — will be superseded")
        newer = SemanticFact(agent_id=unique_agent_id, content="newer fact — will be the winner")
        stored_older = store_r.facts.create(older, scope)
        stored_newer = store_r.facts.create(newer, scope)

        # Ensure we have a created_at difference (may already differ by µs).
        # We will rely on list ordering: created_at is set server-side and the
        # two INSERT calls are sequential, so stored_older.created_at ≤
        # stored_newer.created_at.  If they happen to be equal, the reconciler
        # returns [] and the test would still pass (assert no-op was applied)
        # — but that scenario is vanishingly unlikely on a live database.

        decisions = store_r.reconcile("facts", scope)

        # At least one decision should have been applied (older → newer).
        assert len(decisions) >= 1, (
            "reconcile() with OldestLosesReconciler must return at least one applied decision"
        )

        # Verify via raw DB that the loser's supersession fields are set.
        fields = _fetch_supersession_fields(migrated_pool, stored_older.id)
        assert fields.get("superseded_by") == stored_newer.id, (
            f"loser.superseded_by must equal winner id={stored_newer.id!r}; "
            f"got {fields.get('superseded_by')!r}"
        )
        assert fields.get("superseded_at") is not None, (
            "loser.superseded_at must be set (not NULL) after reconcile()"
        )
        assert fields.get("supersede_reason") is not None, (
            "loser.supersede_reason must be set (not NULL) after reconcile()"
        )

    def test_winner_supersession_fields_not_set(
        self, migrated_pool, unique_agent_id
    ):
        """After reconcile(), the winner row must NOT have any supersession fields set."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-rec-winner")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="older — will lose"),
            scope,
        )
        stored_newer = store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="newer — will win"),
            scope,
        )

        store_r.reconcile("facts", scope)

        fields = _fetch_supersession_fields(migrated_pool, stored_newer.id)
        assert fields.get("superseded_by") is None, (
            "winner.superseded_by must remain NULL"
        )
        assert fields.get("superseded_at") is None, (
            "winner.superseded_at must remain NULL"
        )
        assert fields.get("supersede_reason") is None, (
            "winner.supersede_reason must remain NULL"
        )

    def test_list_all_excludes_superseded_loser(
        self, migrated_pool, unique_agent_id
    ):
        """list_all() must not return the superseded (loser) row after reconcile()."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-list-exc")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        stored_older = store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="will be superseded — list check"),
            scope,
        )
        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="will survive — list check"),
            scope,
        )

        store_r.reconcile("facts", scope)

        all_facts = store_r.facts.list_all(scope, limit=200)
        all_ids = {f.id for f in all_facts}
        assert stored_older.id not in all_ids, (
            "list_all() must exclude the superseded row (loser) after reconcile()"
        )

    def test_search_excludes_superseded_loser(
        self, migrated_pool, unique_agent_id, vec_dim
    ):
        """search() must not return the superseded (loser) row after reconcile()."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-search-exc")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        # Use a unit vector so cosine distance is well-defined.
        vec = make_unit_vec(vec_dim, 200)

        stored_older = store_r.facts.create(
            SemanticFact(
                agent_id=unique_agent_id,
                content="will be superseded — search check",
                embedding=vec,
            ),
            scope,
        )
        store_r.facts.create(
            SemanticFact(
                agent_id=unique_agent_id,
                content="will survive — search check",
                embedding=vec,
            ),
            scope,
        )

        store_r.reconcile("facts", scope)

        results = store_r.facts.search(query_embedding=vec, scope=scope, top_k=50)
        result_ids = {r.id for r in results}
        assert stored_older.id not in result_ids, (
            "search() must exclude the superseded row (loser) after reconcile()"
        )


# ---------------------------------------------------------------------------
# Guard assertions
# ---------------------------------------------------------------------------


class TestReconcileGuards:
    """Boundary conditions and safety guards on reconcile() / supersede()."""

    def test_entity_profiles_unaffected_by_facts_reconcile(
        self, migrated_pool, unique_agent_id
    ):
        """reconcile('facts', …) must not touch entity_profiles rows."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import EntityProfile, MemoryScope, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-guard-prof")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        # Seed a profile row.
        profile = EntityProfile(
            agent_id=unique_agent_id,
            user_id=scope.user_id,
            content="profile that must survive facts reconcile",
        )
        stored_profile = store_r.profiles.create(profile, scope)

        # Also seed two facts so the reconciler actually fires.
        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="fact A for guard test"),
            scope,
        )
        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="fact B for guard test"),
            scope,
        )

        # Run reconcile on facts.
        store_r.reconcile("facts", scope)

        # The profile must still be readable and unchanged.
        fetched = store_r.profiles.get_by_id(stored_profile.id, scope)
        assert fetched is not None, (
            "entity_profiles row must still be readable after reconcile('facts', …)"
        )
        assert fetched.content == profile.content, (
            "entity_profiles row content must be unchanged by reconcile('facts', …)"
        )

    def test_procedural_memory_unaffected_by_facts_reconcile(
        self, migrated_pool, unique_agent_id
    ):
        """reconcile('facts', …) must not touch procedural_memory rows."""
        from agent_memory_sdk import MemoryStore
        from agent_memory_sdk.models import MemoryScope, ProceduralMemory, SemanticFact

        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-guard-proc")
        store_r = MemoryStore(migrated_pool, reconciler=OldestLosesReconciler())

        # Seed a procedure row.
        procedure = ProceduralMemory(
            agent_id=unique_agent_id,
            content="procedure that must survive facts reconcile",
        )
        stored_procedure = store_r.procedures.create(procedure, scope)

        # Also seed two facts so the reconciler actually fires.
        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="fact X for guard test"),
            scope,
        )
        store_r.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="fact Y for guard test"),
            scope,
        )

        # Run reconcile on facts.
        store_r.reconcile("facts", scope)

        # The procedure must still be readable and unchanged.
        fetched = store_r.procedures.get_by_id(stored_procedure.id, scope)
        assert fetched is not None, (
            "procedural_memory row must still be readable after reconcile('facts', …)"
        )
        assert fetched.content == procedure.content, (
            "procedural_memory row content must be unchanged by reconcile('facts', …)"
        )

    def test_supersede_on_already_deleted_row_is_noop(
        self, store, scope, unique_agent_id
    ):
        """supersede() on a forget()-tombstoned row must be a no-op (returns False)."""
        from agent_memory_sdk.models import SemanticFact

        # Write the "loser" and a "winner" fact.
        loser = store.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="about to be deleted then superseded"),
            scope,
        )
        winner = store.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="winner for deleted-row guard"),
            scope,
        )

        # Tombstone the loser first.
        deleted_ok = store.facts.forget(loser.id, scope)
        assert deleted_ok is True, "forget() must return True when the row exists"

        # Now attempt to supersede the already-deleted row — must be a no-op.
        superseded_ok = store.facts.supersede(
            loser_id=loser.id,
            winner_id=winner.id,
            reason="guard test: should be a no-op on deleted row",
            scope=scope,
        )
        assert superseded_ok is False, (
            "supersede() on an already-deleted row must return False (no-op)"
        )

    def test_supersede_on_already_superseded_row_is_noop(
        self, store, scope, unique_agent_id, migrated_pool
    ):
        """supersede() on an already-superseded row must not overwrite superseded_at."""
        from agent_memory_sdk.models import SemanticFact

        loser = store.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="will be double-superseded"),
            scope,
        )
        winner = store.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="first winner"),
            scope,
        )
        second_winner = store.facts.create(
            SemanticFact(agent_id=unique_agent_id, content="second winner — should be ignored"),
            scope,
        )

        # First supersession.
        first_ok = store.facts.supersede(
            loser_id=loser.id,
            winner_id=winner.id,
            reason="first supersession",
            scope=scope,
        )
        assert first_ok is True, "First supersede() must succeed"

        # Capture the timestamp set by the first supersession.
        fields_after_first = _fetch_supersession_fields(migrated_pool, loser.id)
        first_superseded_at = fields_after_first["superseded_at"]
        assert first_superseded_at is not None, "superseded_at must be set after first supersession"

        # Second supersession attempt — must be a no-op.
        second_ok = store.facts.supersede(
            loser_id=loser.id,
            winner_id=second_winner.id,
            reason="second supersession — must be ignored",
            scope=scope,
        )
        assert second_ok is False, (
            "supersede() on an already-superseded row must return False (no-op)"
        )

        # superseded_at must not have been re-written.
        fields_after_second = _fetch_supersession_fields(migrated_pool, loser.id)
        assert fields_after_second["superseded_by"] == winner.id, (
            "superseded_by must still point to the first winner, not the second"
        )
        assert fields_after_second["superseded_at"] == first_superseded_at, (
            "superseded_at must NOT be re-written by a second supersede() call"
        )

"""
tests/integration/test_dedup_confidence.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-1: confidence scoring (ENH-1) and write-time
content-hash deduplication (ENH-2).

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
# Helpers
# ---------------------------------------------------------------------------


def _count_rows_by_hash(pool, agent_id: str, content_hash: str) -> int:
    """Return the raw row count for (agent_id, content_hash) in semantic_facts,
    including soft-deleted and superseded rows — so the dedup tests can verify
    that only one physical row was ever written.
    """
    sql = """
        SELECT COUNT(*)
        FROM semantic_facts
        WHERE agent_id = ?
          AND content_hash = ?
    """
    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [agent_id, content_hash])
        row = cur.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# ENH-1: Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceFiltering:
    """list_all() and search() must respect the min_confidence predicate."""

    @pytest.fixture()
    def scope(self, unique_agent_id):
        from agent_memory_sdk.models import MemoryScope

        return MemoryScope(agent_id=unique_agent_id, user_id="test-user-conf")

    def test_list_all_min_confidence_filters_below_threshold(
        self, store, scope, unique_agent_id
    ):
        """list_all(min_confidence=0.7) must exclude rows below 0.7."""
        from agent_memory_sdk.models import SemanticFact

        contents = [
            ("conf-low-0.3", 0.3),
            ("conf-mid-0.6", 0.6),
            ("conf-high-0.95", 0.95),
            ("conf-default-1.0", 1.0),
        ]
        stored_ids = {}
        for content, conf in contents:
            fact = SemanticFact(
                agent_id=unique_agent_id,
                content=content,
                confidence=conf,
            )
            stored = store.facts.create(fact, scope)
            stored_ids[content] = stored.id

        results = store.facts.list_all(scope=scope, limit=100, min_confidence=0.7)
        result_ids = {r.id for r in results}

        # Below threshold: must be excluded
        assert stored_ids["conf-low-0.3"] not in result_ids, (
            "Fact with confidence=0.3 must be excluded when min_confidence=0.7"
        )
        assert stored_ids["conf-mid-0.6"] not in result_ids, (
            "Fact with confidence=0.6 must be excluded when min_confidence=0.7"
        )
        # At or above threshold: must be included
        assert stored_ids["conf-high-0.95"] in result_ids, (
            "Fact with confidence=0.95 must be included when min_confidence=0.7"
        )
        assert stored_ids["conf-default-1.0"] in result_ids, (
            "Fact with confidence=1.0 must be included when min_confidence=0.7"
        )

    def test_list_all_default_min_confidence_returns_all(
        self, store, scope, unique_agent_id
    ):
        """list_all() with default min_confidence=0.0 must return all rows."""
        from agent_memory_sdk.models import SemanticFact

        low_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="conf-all-0.3-default",
            confidence=0.3,
        )
        stored = store.facts.create(low_fact, scope)

        results = store.facts.list_all(scope=scope, limit=100)
        result_ids = {r.id for r in results}
        assert stored.id in result_ids, (
            "list_all() with default min_confidence must return all rows including low-confidence ones"
        )

    def test_list_all_boundary_exactly_at_min_confidence_is_included(
        self, store, scope, unique_agent_id
    ):
        """A fact at exactly min_confidence must be INCLUDED, not excluded."""
        from agent_memory_sdk.models import SemanticFact

        boundary_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="conf-boundary-0.7-exactly",
            confidence=0.7,
        )
        stored = store.facts.create(boundary_fact, scope)

        results = store.facts.list_all(scope=scope, limit=100, min_confidence=0.7)
        result_ids = {r.id for r in results}
        assert stored.id in result_ids, (
            "A fact at exactly min_confidence=0.7 must be included (boundary is inclusive)"
        )

    def test_search_min_confidence_predicate_filters_low_confidence(
        self, store, scope, unique_agent_id, vec_dim
    ):
        """search() with min_confidence must exclude below-threshold rows."""
        from agent_memory_sdk.models import SemanticFact

        # Use distinct unit vectors so the search returns both rows by proximity
        vec_high = make_unit_vec(vec_dim, 100)
        vec_low = make_unit_vec(vec_dim, 101)

        high_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="search-conf-high-0.95",
            confidence=0.95,
            embedding=vec_high,
        )
        low_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="search-conf-low-0.3",
            confidence=0.3,
            embedding=vec_low,
        )
        stored_high = store.facts.create(high_fact, scope)
        stored_low = store.facts.create(low_fact, scope)

        # Search with min_confidence=0.7 — only the high-confidence row should
        # appear. Use the high vector as query so both are candidates by proximity.
        results = store.facts.search(
            query_embedding=vec_high,
            scope=scope,
            top_k=50,
            min_confidence=0.7,
        )
        result_ids = {r.id for r in results}

        assert stored_high.id in result_ids, (
            "search() must return the high-confidence fact when min_confidence=0.7"
        )
        assert stored_low.id not in result_ids, (
            "search() must exclude the low-confidence fact when min_confidence=0.7"
        )

    def test_search_default_min_confidence_returns_all(
        self, store, scope, unique_agent_id, vec_dim
    ):
        """search() with default min_confidence=0.0 must return all rows."""
        from agent_memory_sdk.models import SemanticFact

        vec = make_unit_vec(vec_dim, 102)
        low_fact = SemanticFact(
            agent_id=unique_agent_id,
            content="search-conf-all-0.3",
            confidence=0.3,
            embedding=vec,
        )
        stored = store.facts.create(low_fact, scope)

        results = store.facts.search(
            query_embedding=vec,
            scope=scope,
            top_k=50,
        )
        result_ids = {r.id for r in results}
        assert stored.id in result_ids, (
            "search() with default min_confidence must return low-confidence rows"
        )


# ---------------------------------------------------------------------------
# ENH-2: Write-time content-hash deduplication
# ---------------------------------------------------------------------------


class TestContentHashDedup:
    """create() must return the existing row on duplicate content (same scope)."""

    @pytest.fixture()
    def scope(self, unique_agent_id):
        from agent_memory_sdk.models import MemoryScope

        return MemoryScope(agent_id=unique_agent_id, user_id="test-user-dedup")

    def test_duplicate_create_returns_same_id(
        self, store, scope, unique_agent_id
    ):
        """Two create() calls with byte-identical content must return the same id."""
        from agent_memory_sdk.models import SemanticFact

        content = f"dedup-exact-content-{unique_agent_id}"
        fact1 = SemanticFact(agent_id=unique_agent_id, content=content)
        fact2 = SemanticFact(agent_id=unique_agent_id, content=content)

        stored1 = store.facts.create(fact1, scope)
        stored2 = store.facts.create(fact2, scope)

        assert stored1.id == stored2.id, (
            "Second create() with identical content must return the existing row id"
        )

    def test_duplicate_create_inserts_only_one_physical_row(
        self, store, scope, unique_agent_id, migrated_pool
    ):
        """Only one physical row must exist in semantic_facts for duplicate content."""
        from agent_memory_sdk.models import SemanticFact
        from agent_memory_sdk.repositories.base import _content_hash

        content = f"dedup-count-{unique_agent_id}"
        fact1 = SemanticFact(agent_id=unique_agent_id, content=content)
        fact2 = SemanticFact(agent_id=unique_agent_id, content=content)

        store.facts.create(fact1, scope)
        store.facts.create(fact2, scope)

        h = _content_hash(content)
        count = _count_rows_by_hash(migrated_pool, unique_agent_id, h)
        assert count == 1, (
            f"Expected exactly 1 row in semantic_facts for this content_hash, got {count}"
        )

    def test_normalized_duplicate_returns_same_id(
        self, store, scope, unique_agent_id
    ):
        """Whitespace/case differences that normalize to the same hash must dedup."""
        from agent_memory_sdk.models import SemanticFact

        # Both normalize to the same lowercase+whitespace-collapsed string
        content_original = f"Dedup  Normalization  Test {unique_agent_id}"
        content_variant = f"dedup normalization test {unique_agent_id}"

        fact1 = SemanticFact(agent_id=unique_agent_id, content=content_original)
        fact2 = SemanticFact(agent_id=unique_agent_id, content=content_variant)

        stored1 = store.facts.create(fact1, scope)
        stored2 = store.facts.create(fact2, scope)

        assert stored1.id == stored2.id, (
            "Whitespace/case-normalized duplicate must return the same existing row id"
        )

    def test_same_content_different_scope_creates_new_row(
        self, store, scope, unique_agent_id
    ):
        """Identical content in a different scope must create a distinct new row."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        content = f"dedup-cross-scope-{unique_agent_id}"
        other_scope = MemoryScope(
            agent_id=f"other-{unique_agent_id}", user_id="test-user-dedup"
        )

        fact1 = SemanticFact(agent_id=unique_agent_id, content=content)
        fact2 = SemanticFact(agent_id=f"other-{unique_agent_id}", content=content)

        stored1 = store.facts.create(fact1, scope)
        stored2 = store.facts.create(fact2, other_scope)

        assert stored1.id != stored2.id, (
            "Same content in a different scope must produce a distinct new row"
        )

    def test_update_recomputes_content_hash(
        self, store, scope, unique_agent_id
    ):
        """update() must recompute content_hash for the new content."""
        from agent_memory_sdk.models import SemanticFact
        from agent_memory_sdk.repositories.base import _content_hash

        original_content = f"dedup-update-original-{unique_agent_id}"
        fact = SemanticFact(agent_id=unique_agent_id, content=original_content)
        stored = store.facts.create(fact, scope)

        original_hash = stored.content_hash
        assert original_hash == _content_hash(original_content), (
            "content_hash after create() must match _content_hash(content)"
        )

        # Update with different content
        new_content = f"dedup-update-new-{unique_agent_id}"
        stored.content = new_content
        updated = store.facts.update(stored, scope)

        assert updated.content_hash is not None, "content_hash must be set after update()"
        assert updated.content_hash == _content_hash(new_content), (
            "content_hash after update() must match _content_hash(new content)"
        )
        assert updated.content_hash != original_hash, (
            "content_hash must change when content changes on update()"
        )

        # Re-fetch from DB to confirm persistence
        fetched = store.facts.get_by_id(updated.id, scope)
        assert fetched is not None
        assert fetched.content_hash == _content_hash(new_content), (
            "Updated content_hash must be persisted and readable on next get_by_id()"
        )

    def test_dedup_skipped_for_soft_deleted_row(
        self, store, scope, unique_agent_id, migrated_pool
    ):
        """create() with content matching a tombstoned row must insert a fresh row."""
        from agent_memory_sdk.models import SemanticFact
        from agent_memory_sdk.repositories.base import _content_hash

        content = f"dedup-deleted-{unique_agent_id}"
        fact = SemanticFact(agent_id=unique_agent_id, content=content)
        stored = store.facts.create(fact, scope)
        original_id = stored.id

        # Tombstone the row
        ok = store.facts.forget(original_id, scope)
        assert ok is True, "forget() must return True when the row exists"

        # Create again with the same content — the deleted row must be skipped
        fact2 = SemanticFact(agent_id=unique_agent_id, content=content)
        fresh = store.facts.create(fact2, scope)

        assert fresh.id != original_id, (
            "create() after forget() must return a fresh row id, not the tombstoned one"
        )
        assert fresh.deleted_at is None, (
            "The newly created row must not be soft-deleted"
        )

        # Verify we now have 2 physical rows: the tombstoned original + the new one
        h = _content_hash(content)
        count = _count_rows_by_hash(migrated_pool, unique_agent_id, h)
        assert count == 2, (
            f"Expected 2 physical rows (deleted + fresh), got {count}"
        )

    def test_dedup_skipped_for_superseded_row(
        self, store, scope, unique_agent_id, migrated_pool
    ):
        """create() with content matching a superseded row must insert a fresh row."""
        from agent_memory_sdk.models import SemanticFact
        from agent_memory_sdk.repositories.base import _content_hash

        content = f"dedup-superseded-{unique_agent_id}"

        # Write the fact that will be superseded (the "loser")
        loser = SemanticFact(agent_id=unique_agent_id, content=content)
        stored_loser = store.facts.create(loser, scope)

        # Write a second, distinct fact to serve as the "winner"
        winner = SemanticFact(
            agent_id=unique_agent_id,
            content=f"winner-fact-{unique_agent_id}",
        )
        stored_winner = store.facts.create(winner, scope)

        # Supersede the loser
        ok = store.facts.supersede(
            loser_id=stored_loser.id,
            winner_id=stored_winner.id,
            reason="test: superseded to validate dedup skip",
            scope=scope,
        )
        assert ok is True, "supersede() must return True when the loser row exists"

        # Create again with the same content as the loser — the superseded row
        # must NOT be returned as a dedup hit
        fact_fresh = SemanticFact(agent_id=unique_agent_id, content=content)
        fresh = store.facts.create(fact_fresh, scope)

        assert fresh.id != stored_loser.id, (
            "create() with content matching a superseded row must return a fresh row id"
        )
        assert fresh.superseded_at is None, (
            "The newly created row must not itself be superseded"
        )
        assert fresh.deleted_at is None, (
            "The newly created row must not be soft-deleted"
        )

        # Verify 2 physical rows with that hash: the superseded + the new one
        h = _content_hash(content)
        count = _count_rows_by_hash(migrated_pool, unique_agent_id, h)
        assert count == 2, (
            f"Expected 2 physical rows (superseded + fresh), got {count}"
        )

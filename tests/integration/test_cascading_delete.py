"""
tests/integration/test_cascading_delete.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-16: cascading delete_user() / delete_agent()
(Epic-10).

Covers:
- delete_user(cascade=True): wipes ALL rows for the target user (across every
  table and every thread) while leaving the other user's data 100% intact.
- delete_agent(cascade=True): after a partial user deletion, wiping the full
  agent removes every remaining row for every user and every table.
- delete_user(cascade=False): only EntityProfile rows are removed; working
  memory (and other tables) are untouched.
- delete_agent(cascade=False): same as above but agent-wide — only
  EntityProfiles are removed.

Assertions are made via raw ``SELECT COUNT(*)`` queries against the live Db2
instance so there is no ambiguity about what the SDK repos filter vs. what is
physically present in the database.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Table names — matches the six tables managed by erase_all()
# ---------------------------------------------------------------------------
_ALL_TABLES = (
    "working_memory",
    "episodic_memory",
    "semantic_facts",
    "entity_profiles",
    "procedural_memory",
    "memory_chunks",
)

_DIM = 1536


# ---------------------------------------------------------------------------
# Raw-SQL helpers
# ---------------------------------------------------------------------------


def _count(pool, table: str, agent_id: str, user_id: str | None = None) -> int:
    """Return the physical row count in *table* for the given agent/user scope.

    When *user_id* is None the query matches on agent_id only (used for
    agent-level counts).  ``memory_chunks`` uses the same scope columns as
    the other tables so the same predicate applies.
    """
    if user_id is not None:
        sql = f"SELECT COUNT(*) FROM {table} WHERE agent_id = ? AND user_id = ?"  # nosec B608
        params: list = [agent_id, user_id]
    else:
        sql = f"SELECT COUNT(*) FROM {table} WHERE agent_id = ?"  # nosec B608
        params = [agent_id]

    with pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _count_agent(pool, table: str, agent_id: str) -> int:
    """Row count in *table* for a whole agent (any user)."""
    return _count(pool, table, agent_id, user_id=None)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _make_scope(agent_id: str, user_id: str, thread_id: str | None = None):
    from agent_memory_sdk.models import MemoryScope

    return MemoryScope(agent_id=agent_id, user_id=user_id, thread_id=thread_id)


def _seed_thread(store, agent_id: str, user_id: str, thread_id: str) -> None:
    """Seed one thread's worth of data: 2 WorkingMemory, 1 EpisodicMemory,
    1 SemanticFact, 1 ProceduralMemory, and 1 chunk via ChunkRepository.

    The EntityProfile is seeded once *per user* (not per thread) by the
    caller, so it is intentionally omitted here.
    """
    from agent_memory_sdk.models import (
        EpisodicMemory,
        ProceduralMemory,
        SemanticFact,
        WorkingMemory,
    )

    thread_scope = _make_scope(agent_id, user_id, thread_id)

    # 2 WorkingMemory rows
    for i in range(2):
        store.working.create(
            WorkingMemory(
                agent_id=agent_id,
                user_id=user_id,
                thread_id=thread_id,
                content=f"working turn {i} in {thread_id}",
            ),
            thread_scope,
        )

    # 1 EpisodicMemory (scoped to thread)
    store.episodic.create(
        EpisodicMemory(
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            content=f"episodic event in {thread_id}",
        ),
        thread_scope,
    )

    # 1 SemanticFact (scoped to thread)
    store.facts.create(
        SemanticFact(
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            content=f"fact in {thread_id}",
        ),
        thread_scope,
    )

    # 1 ProceduralMemory (scoped to thread)
    store.procedures.create(
        ProceduralMemory(
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            content=f"procedure in {thread_id}",
        ),
        thread_scope,
    )

    # 1 chunk inserted directly via ChunkRepository — store.chunks is None
    # when no embedding_provider is configured, so we construct a throwaway
    # ChunkRepository directly.
    from agent_memory_sdk.repositories.chunks import ChunkRepository

    chunk_repo = ChunkRepository(store._pool, embedding_dim=_DIM)
    chunk_repo.insert_chunk(
        source_table="working_memory",
        source_id=str(uuid.uuid4()),
        chunk_index=0,
        chunk_text=f"chunk text for {thread_id}",
        embedding=[0.0] * _DIM,
        scope=thread_scope,
    )


def _seed_user(store, agent_id: str, user_id: str, threads: list[str]) -> None:
    """Seed one EntityProfile for (agent_id, user_id) plus one thread's data
    per entry in *threads*.
    """
    from agent_memory_sdk.models import EntityProfile

    user_scope = _make_scope(agent_id, user_id)

    # 1 EntityProfile for this user
    store.profiles.create(
        EntityProfile(
            agent_id=agent_id,
            user_id=user_id,
            content=f"profile for {user_id}",
        ),
        user_scope,
    )

    for thread_id in threads:
        _seed_thread(store, agent_id, user_id, thread_id)


def _pre_counts_for_user(pool, agent_id: str, user_id: str) -> dict[str, int]:
    """Return raw row counts for *user_id* across all six tables."""
    return {table: _count(pool, table, agent_id, user_id) for table in _ALL_TABLES}


# ---------------------------------------------------------------------------
# TestDeleteUser
# ---------------------------------------------------------------------------


class TestDeleteUser:
    """delete_user(cascade=True) wipes all rows for the target user and
    leaves the other user's data completely intact.
    """

    def test_delete_user_cascade_true(self, store, migrated_pool, unique_agent_id):
        """
        Scenario
        --------
        agent_id with:
          user_1: threads A, B
          user_2: threads C, D

        After delete_user(user_1, cascade=True):
          - Every table must have 0 rows for user_1.
          - Every table must still have the original row count for user_2.
          - ErasureReport.total_deleted matches sum of pre-call counts for user_1.
        """
        agent_id = unique_agent_id
        user_1 = f"user-1-{uuid.uuid4()}"
        user_2 = f"user-2-{uuid.uuid4()}"

        _seed_user(store, agent_id, user_1, ["thread-A", "thread-B"])
        _seed_user(store, agent_id, user_2, ["thread-C", "thread-D"])

        # Capture counts for user_1 before deletion
        pre_u1 = _pre_counts_for_user(migrated_pool, agent_id, user_1)
        # Capture counts for user_2 before deletion (must not change)
        pre_u2 = _pre_counts_for_user(migrated_pool, agent_id, user_2)

        # Sanity: user_1 has data in all tables
        for table, cnt in pre_u1.items():
            assert cnt > 0, f"Expected pre-seed rows for user_1 in {table}, got {cnt}"

        expected_total = sum(pre_u1.values())

        # --- Act ---
        report = store.delete_user(user_id=user_1, agent_id=agent_id, cascade=True)

        # --- Assert: user_1 is fully gone ---
        for table in _ALL_TABLES:
            remaining = _count(migrated_pool, table, agent_id, user_1)
            assert remaining == 0, (
                f"Expected 0 rows for user_1 in {table} after delete_user, "
                f"got {remaining}"
            )

        # --- Assert: user_2 is completely intact ---
        for table in _ALL_TABLES:
            after_u2 = _count(migrated_pool, table, agent_id, user_2)
            assert after_u2 == pre_u2[table], (
                f"user_2 row count in {table} changed after delete_user(user_1): "
                f"expected {pre_u2[table]}, got {after_u2}"
            )

        # --- Assert: ErasureReport total matches pre-call counts ---
        assert report.total_deleted == expected_total, (
            f"ErasureReport.total_deleted={report.total_deleted} "
            f"!= expected {expected_total}"
        )


# ---------------------------------------------------------------------------
# TestDeleteAgent
# ---------------------------------------------------------------------------


class TestDeleteAgent:
    """delete_agent(cascade=True) wipes every row for the entire agent,
    regardless of user or thread.
    """

    def test_delete_agent_cascade_true(self, store, migrated_pool, unique_agent_id):
        """
        Scenario
        --------
        Fresh agent seeded with user_1 (threads A, B) and user_2 (threads C, D).
        1. delete_user(user_1, cascade=True) — user_1 is gone.
        2. delete_agent(agent_id, cascade=True) — user_2 must also be gone.
        3. Zero rows remain for agent_id in every table.
        """
        agent_id = f"{unique_agent_id}-agent-del"
        user_1 = f"user-1-{uuid.uuid4()}"
        user_2 = f"user-2-{uuid.uuid4()}"

        _seed_user(store, agent_id, user_1, ["thread-A", "thread-B"])
        _seed_user(store, agent_id, user_2, ["thread-C", "thread-D"])

        # Step 1: remove user_1 first
        store.delete_user(user_id=user_1, agent_id=agent_id, cascade=True)

        # Sanity: user_2 still has data before agent deletion
        for table in _ALL_TABLES:
            cnt = _count(migrated_pool, table, agent_id, user_2)
            assert cnt > 0, (
                f"Expected user_2 data in {table} before delete_agent, got {cnt}"
            )

        # Step 2: wipe the full agent
        report = store.delete_agent(agent_id=agent_id, cascade=True)

        # user_2's tree must be gone
        for table in _ALL_TABLES:
            remaining = _count(migrated_pool, table, agent_id, user_2)
            assert remaining == 0, (
                f"Expected 0 rows for user_2 in {table} after delete_agent, "
                f"got {remaining}"
            )

        # Zero rows remain for the whole agent
        for table in _ALL_TABLES:
            remaining_agent = _count_agent(migrated_pool, table, agent_id)
            assert remaining_agent == 0, (
                f"Expected 0 total rows for agent_id in {table} after delete_agent, "
                f"got {remaining_agent}"
            )

        # ErasureReport reflects the user_2 rows that were actually deleted
        assert report.total_deleted >= 0  # structural sanity
        assert isinstance(report.rows_deleted, dict)
        assert set(report.rows_deleted.keys()) == set(_ALL_TABLES)


# ---------------------------------------------------------------------------
# TestCascadeFalse
# ---------------------------------------------------------------------------


class TestCascadeFalse:
    """cascade=False erases only EntityProfile rows; all other tables survive."""

    def test_delete_user_cascade_false_only_removes_profile(
        self, store, migrated_pool, unique_agent_id
    ):
        """
        delete_user(cascade=False) must:
          - remove EntityProfile rows for that user
          - leave working_memory rows intact
        """
        from agent_memory_sdk.models import EntityProfile, WorkingMemory

        agent_id = f"{unique_agent_id}-cf-user"
        user_id = f"user-cf-{uuid.uuid4()}"
        scope = _make_scope(agent_id, user_id)

        # Seed one EntityProfile + two WorkingMemory rows
        store.profiles.create(
            EntityProfile(
                agent_id=agent_id,
                user_id=user_id,
                content="profile for cascade-false test",
            ),
            scope,
        )
        for i in range(2):
            store.working.create(
                WorkingMemory(
                    agent_id=agent_id,
                    user_id=user_id,
                    content=f"working row {i}",
                ),
                scope,
            )

        pre_profiles = _count(migrated_pool, "entity_profiles", agent_id, user_id)
        pre_working = _count(migrated_pool, "working_memory", agent_id, user_id)
        assert pre_profiles >= 1, "Expected at least 1 EntityProfile before test"
        assert pre_working == 2, "Expected exactly 2 working_memory rows before test"

        # Act — cascade=False
        report = store.delete_user(user_id=user_id, agent_id=agent_id, cascade=False)

        # EntityProfiles must be gone
        after_profiles = _count(migrated_pool, "entity_profiles", agent_id, user_id)
        assert after_profiles == 0, (
            f"Expected 0 EntityProfile rows after delete_user(cascade=False), "
            f"got {after_profiles}"
        )

        # WorkingMemory must be untouched
        after_working = _count(migrated_pool, "working_memory", agent_id, user_id)
        assert after_working == pre_working, (
            f"WorkingMemory rows must survive delete_user(cascade=False): "
            f"expected {pre_working}, got {after_working}"
        )

        # ErasureReport accounts only for the EntityProfile deletion
        assert report.total_deleted == pre_profiles
        assert report.rows_deleted["entity_profiles"] == pre_profiles
        assert report.rows_deleted["working_memory"] == 0

    def test_delete_agent_cascade_false_only_removes_profiles(
        self, store, migrated_pool, unique_agent_id
    ):
        """
        delete_agent(cascade=False) must:
          - remove all EntityProfile rows for that agent (any user)
          - leave working_memory rows (and other non-profile tables) intact
        """
        from agent_memory_sdk.models import EntityProfile, WorkingMemory

        agent_id = f"{unique_agent_id}-cf-agent"
        user_a = f"user-a-{uuid.uuid4()}"
        user_b = f"user-b-{uuid.uuid4()}"

        for user_id in (user_a, user_b):
            scope = _make_scope(agent_id, user_id)
            store.profiles.create(
                EntityProfile(
                    agent_id=agent_id,
                    user_id=user_id,
                    content=f"profile {user_id}",
                ),
                scope,
            )
            store.working.create(
                WorkingMemory(
                    agent_id=agent_id,
                    user_id=user_id,
                    content=f"working row for {user_id}",
                ),
                scope,
            )

        pre_profiles = _count_agent(migrated_pool, "entity_profiles", agent_id)
        pre_working = _count_agent(migrated_pool, "working_memory", agent_id)
        assert pre_profiles == 2, "Expected exactly 2 EntityProfile rows before test"
        assert pre_working == 2, "Expected exactly 2 working_memory rows before test"

        # Act — cascade=False at the agent level
        report = store.delete_agent(agent_id=agent_id, cascade=False)

        # All EntityProfiles for this agent must be gone
        after_profiles = _count_agent(migrated_pool, "entity_profiles", agent_id)
        assert after_profiles == 0, (
            f"Expected 0 EntityProfile rows after delete_agent(cascade=False), "
            f"got {after_profiles}"
        )

        # WorkingMemory rows must be untouched
        after_working = _count_agent(migrated_pool, "working_memory", agent_id)
        assert after_working == pre_working, (
            f"WorkingMemory rows must survive delete_agent(cascade=False): "
            f"expected {pre_working}, got {after_working}"
        )

        # ErasureReport reflects only the EntityProfile deletion
        assert report.total_deleted == pre_profiles
        assert report.rows_deleted["entity_profiles"] == pre_profiles
        assert report.rows_deleted["working_memory"] == 0

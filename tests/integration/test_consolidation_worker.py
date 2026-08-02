"""
tests/integration/test_consolidation_worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-3: consolidation cadence + real concurrent
claim-locking.

Three parts:
  Part 1 — consolidate_every_n cadence
  Part 2 — Real concurrent _claim_consolidated() race via Db2 row-level locking
  Part 3 — End-to-end worker script (_fetch_pending / _process_record)

All tests are gated behind the ``integration`` pytest marker and skipped
automatically when ``DB2_DATABASE`` is not set.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import concurrent.futures

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Part 1 — consolidate_every_n cadence
# ---------------------------------------------------------------------------


class CountingConsolidator:
    """Minimal consolidator that counts how many times it has been called."""

    def __init__(self) -> None:
        self.call_count: int = 0

    def __call__(self, raw_memories: list) -> list:
        self.call_count += 1
        return []


class TestConsolidateEveryNCadence:
    """MemoryStore(consolidate_every_n=3) must fire the consolidator on
    exactly the 3rd and 6th writes (not 1st, 2nd, 4th, or 5th)."""

    def test_cadence_fires_on_nth_write(self, migrated_pool, unique_agent_id):
        """Consolidator fires exactly 2 times across 6 remember() calls."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.store import MemoryStore

        counter = CountingConsolidator()
        store = MemoryStore(migrated_pool, consolidate_every_n=3, consolidator=counter)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-cadence")

        # Write 1 — should NOT fire
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 1"),
            scope,
        )
        assert counter.call_count == 0, "call 1 must not fire consolidator"

        # Write 2 — should NOT fire
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 2"),
            scope,
        )
        assert counter.call_count == 0, "call 2 must not fire consolidator"

        # Write 3 — MUST fire (Nth)
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 3"),
            scope,
        )
        assert counter.call_count == 1, "call 3 (Nth) must fire consolidator exactly once"

        # Write 4 — should NOT fire
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 4"),
            scope,
        )
        assert counter.call_count == 1, "call 4 must not fire consolidator"

        # Write 5 — should NOT fire
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 5"),
            scope,
        )
        assert counter.call_count == 1, "call 5 must not fire consolidator"

        # Write 6 — MUST fire (2×Nth)
        store.remember(
            WorkingMemory(agent_id=unique_agent_id, content="turn 6"),
            scope,
        )
        assert counter.call_count == 2, "call 6 (2×Nth) must fire consolidator — total 2"

    def test_cadence_does_not_fire_for_non_working_type(self, migrated_pool, unique_agent_id):
        """SemanticFact writes must never trigger the consolidator."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact
        from agent_memory_sdk.store import MemoryStore

        counter = CountingConsolidator()
        store = MemoryStore(migrated_pool, consolidate_every_n=1, consolidator=counter)
        scope = MemoryScope(agent_id=unique_agent_id, user_id="test-user-nonfiring")

        # SemanticFact writes with consolidate_every_n=1 should still not fire —
        # only working/episodic writes trigger the consolidator.
        for i in range(3):
            store.remember(
                SemanticFact(agent_id=unique_agent_id, content=f"fact {i}"),
                scope,
            )
        assert counter.call_count == 0, "SemanticFact writes must never trigger the consolidator"


# ---------------------------------------------------------------------------
# Part 2 — Real concurrent claim race
# ---------------------------------------------------------------------------


class TestConcurrentClaimLocking:
    """Two threads racing _claim_consolidated() on the same row must produce
    exactly one True and one False — Db2 row-level UPDATE locking must serialize
    the competing UPDATE statements.
    """

    def _insert_fresh_row(self, pool, agent_id: str):
        """Insert one WorkingMemory row with consolidated_at IS NULL and return it."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.repositories.working import WorkingMemoryRepository

        scope = MemoryScope(agent_id=agent_id, user_id="test-user-race")
        repo = WorkingMemoryRepository(pool)
        record = WorkingMemory(agent_id=agent_id, content="race candidate")
        return repo.create(record, scope), scope

    def test_exactly_one_worker_wins_per_row(self, migrated_pool, unique_agent_id):
        """Race two threads 20 times; each race must yield exactly one True."""
        from agent_memory_sdk.repositories.working import WorkingMemoryRepository

        # Two separate repository instances (simulating two worker processes)
        repo_a = WorkingMemoryRepository(migrated_pool)
        repo_b = WorkingMemoryRepository(migrated_pool)

        for iteration in range(20):
            # Give each iteration a distinct agent to prevent scope bleed
            agent_id = f"{unique_agent_id}-race-{iteration}"
            record, scope = self._insert_fresh_row(migrated_pool, agent_id)

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_a = executor.submit(repo_a._claim_consolidated, record.id, scope)
                future_b = executor.submit(repo_b._claim_consolidated, record.id, scope)
                result_a = future_a.result()
                result_b = future_b.result()

            results = [result_a, result_b]
            true_count = results.count(True)
            false_count = results.count(False)
            assert true_count == 1, (
                f"Iteration {iteration}: expected exactly 1 True, got {results}"
            )
            assert false_count == 1, (
                f"Iteration {iteration}: expected exactly 1 False, got {results}"
            )

    def test_claimed_row_has_consolidated_at_set(self, migrated_pool, unique_agent_id):
        """After a successful claim, consolidated_at must be non-NULL in Db2."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.repositories.working import WorkingMemoryRepository

        agent_id = f"{unique_agent_id}-verify"
        scope = MemoryScope(agent_id=agent_id, user_id="test-user-verify")
        repo = WorkingMemoryRepository(migrated_pool)
        record = WorkingMemory(agent_id=agent_id, content="verify claim")
        stored = repo.create(record, scope)

        claimed = repo._claim_consolidated(stored.id, scope)
        assert claimed is True

        # Verify directly via raw SELECT — consolidated_at must not be NULL
        sql = "SELECT consolidated_at FROM working_memory WHERE id = ?"
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, [stored.id])
            row = cur.fetchone()

        assert row is not None, "Row must still exist after claim"
        assert row[0] is not None, "consolidated_at must be non-NULL after successful claim"

    def test_second_claim_returns_false(self, migrated_pool, unique_agent_id):
        """A second _claim_consolidated() call on an already-claimed row must return False."""
        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.repositories.working import WorkingMemoryRepository

        agent_id = f"{unique_agent_id}-second"
        scope = MemoryScope(agent_id=agent_id, user_id="test-user-second")
        repo = WorkingMemoryRepository(migrated_pool)
        record = WorkingMemory(agent_id=agent_id, content="second claim check")
        stored = repo.create(record, scope)

        first = repo._claim_consolidated(stored.id, scope)
        second = repo._claim_consolidated(stored.id, scope)

        assert first is True, "First claim must succeed"
        assert second is False, "Second claim on already-claimed row must fail"


# ---------------------------------------------------------------------------
# Part 3 — End-to-end worker script
# ---------------------------------------------------------------------------


class TestWorkerScriptEndToEnd:
    """Import _fetch_pending / _process_record from scripts/consolidate_pending.py
    and verify that rows are claimed exactly once.
    """

    def test_worker_marks_rows_consolidated(self, migrated_pool, unique_agent_id):
        """_process_record must set consolidated_at on each row it processes."""
        from scripts.consolidate_pending import _fetch_pending, _process_record

        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.store import MemoryStore
        from agent_memory_sdk.types import NoOpConsolidator

        agent_id = f"{unique_agent_id}-e2e"
        scope = MemoryScope(agent_id=agent_id, user_id="test-user-e2e")
        store = MemoryStore(migrated_pool)

        # Insert 3 rows with consolidated_at IS NULL
        inserted_ids = []
        for i in range(3):
            record = WorkingMemory(agent_id=agent_id, content=f"e2e turn {i}")
            stored = store.working.create(record, scope)
            inserted_ids.append(stored.id)

        # Fetch pending and process each row
        consolidator = NoOpConsolidator()
        pending = _fetch_pending(store.working, scope, batch_size=50)
        assert len(pending) >= 3, "All 3 inserted rows must appear as pending"

        # Only process the rows we inserted (there may be others from prior tests)
        our_records = [r for r in pending if r.id in inserted_ids]
        assert len(our_records) == 3

        for record in our_records:
            result = _process_record(store.working, store, consolidator, scope, record)
            assert result is True, f"Row {record.id} must be claimed on first processing"

        # Verify consolidated_at is non-NULL for all three rows
        placeholders = ", ".join("?" * len(inserted_ids))
        sql = f"SELECT id, consolidated_at FROM working_memory WHERE id IN ({placeholders})"
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, inserted_ids)
            rows = {row[0]: row[1] for row in cur.fetchall()}

        for rid in inserted_ids:
            assert rows[rid] is not None, (
                f"consolidated_at must be non-NULL for row {rid} after processing"
            )

    def test_worker_does_not_reprocess_already_consolidated_rows(
        self, migrated_pool, unique_agent_id
    ):
        """Running the worker a second time must not re-update consolidated_at."""
        from scripts.consolidate_pending import _fetch_pending, _process_record

        from agent_memory_sdk.models import MemoryScope, WorkingMemory
        from agent_memory_sdk.store import MemoryStore
        from agent_memory_sdk.types import NoOpConsolidator

        agent_id = f"{unique_agent_id}-idempotent"
        scope = MemoryScope(agent_id=agent_id, user_id="test-user-idempotent")
        store = MemoryStore(migrated_pool)
        consolidator = NoOpConsolidator()

        # Insert 2 rows
        inserted_ids = []
        for i in range(2):
            record = WorkingMemory(agent_id=agent_id, content=f"idempotent turn {i}")
            stored = store.working.create(record, scope)
            inserted_ids.append(stored.id)

        # --- First pass: process all pending rows ---
        pending_first = _fetch_pending(store.working, scope, batch_size=50)
        our_records = [r for r in pending_first if r.id in inserted_ids]
        assert len(our_records) == 2

        for record in our_records:
            _process_record(store.working, store, consolidator, scope, record)

        # Capture the consolidated_at timestamps after first pass
        placeholders = ", ".join("?" * len(inserted_ids))
        sql = f"SELECT id, consolidated_at FROM working_memory WHERE id IN ({placeholders})"
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, inserted_ids)
            first_pass_timestamps = {row[0]: row[1] for row in cur.fetchall()}

        for rid in inserted_ids:
            assert first_pass_timestamps[rid] is not None, (
                f"Row {rid} must be consolidated after first pass"
            )

        # --- Second pass: rows are no longer pending ---
        pending_second = _fetch_pending(store.working, scope, batch_size=50)
        our_pending_second = [r for r in pending_second if r.id in inserted_ids]
        assert our_pending_second == [], (
            "Already-consolidated rows must not appear in _fetch_pending on second pass"
        )

        # Even if we tried to claim them directly, _claim_consolidated must return False
        for rid in inserted_ids:
            result = store.working._claim_consolidated(rid, scope)
            assert result is False, (
                f"_claim_consolidated must return False for already-claimed row {rid}"
            )

        # Verify consolidated_at timestamps are unchanged (not re-updated)
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, inserted_ids)
            second_pass_timestamps = {row[0]: row[1] for row in cur.fetchall()}

        for rid in inserted_ids:
            assert second_pass_timestamps[rid] == first_pass_timestamps[rid], (
                f"consolidated_at for row {rid} must not change on second pass"
            )

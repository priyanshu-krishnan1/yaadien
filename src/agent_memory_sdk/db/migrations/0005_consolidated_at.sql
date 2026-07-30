-- Migration 0005: consolidated_at column (ENH-4)
--
-- Adds a proper TIMESTAMP column to working_memory and episodic_memory to
-- replace the prior metadata.consolidated JSON flag approach described in
-- scripts/consolidate_pending.py's docstring as a "stand-in, not a
-- production implementation."
--
-- WHY only working_memory and episodic_memory:
--   These are the two tables that feed the consolidation pipeline.  The
--   other three tables (semantic_facts, entity_profiles, procedural_memory)
--   are *outputs* of consolidation, not inputs.  Adding consolidated_at to
--   those would be meaningless.
--
-- Design decisions:
--
--   1. TIMESTAMP (nullable, no default) rather than a boolean flag:
--      - Records exactly when the row was claimed by a worker, which is
--        useful for monitoring and debugging stalled workers.
--      - NULL means "not yet consolidated"; a non-NULL value means "claimed
--        at this time."
--      - A boolean flag can't distinguish "claimed but in-flight" from
--        "done" unless a second column is added.  The timestamp does both
--        in one column.
--
--   2. Claim-based locking (optimistic concurrency, not pessimistic locks):
--      Workers claim a row by issuing:
--
--          UPDATE <table>
--          SET consolidated_at = <now>
--          WHERE id = ? AND consolidated_at IS NULL
--
--      and checking the rowcount.  A rowcount of 0 means another worker
--      already claimed the row — the row is silently skipped.  This avoids
--      SELECT FOR UPDATE (not available in all Db2 isolation levels) and
--      avoids the need for an external distributed lock.
--
--   3. No index on consolidated_at alone; the composite index on
--      (agent_id, consolidated_at) allows the worker's eligibility scan
--      (WHERE agent_id = ? AND consolidated_at IS NULL) to use an index
--      range scan rather than a full table scan.
--
-- See project-management/DECISIONS.md ENH-4 entry for the full rationale, known limitations,
-- and the comparison to Cosmos DB's change-feed tier.

-- ============================================================
-- TABLE: working_memory
-- ============================================================
ALTER TABLE working_memory
    ADD COLUMN consolidated_at TIMESTAMP;

CREATE INDEX ix_working_memory_consolidated_at
    ON working_memory (agent_id, consolidated_at);

-- ============================================================
-- TABLE: episodic_memory
-- ============================================================
ALTER TABLE episodic_memory
    ADD COLUMN consolidated_at TIMESTAMP;

CREATE INDEX ix_episodic_memory_consolidated_at
    ON episodic_memory (agent_id, consolidated_at);

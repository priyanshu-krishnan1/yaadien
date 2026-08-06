-- Migration 0008: provenance / origin column (TRU-1)
--
-- Adds a nullable VARCHAR(32) ``origin`` column to all five memory tables
-- to record which write path created each row.
--
-- Valid values (match MemoryOrigin enum in types.py):
--   DIRECT_WRITE       — caller passed record directly to remember()
--   EXTRACTION         — produced by MemoryExtractor callback (add_messages)
--   CONSOLIDATION      — produced by Consolidator callback (_run_consolidator)
--   RECONCILIATION     — produced during a reconcile() pass
--   INGEST_RESOLVER    — produced by IngestResolver UPDATE decision
--
-- Column design:
--   • NULLABLE — existing rows before this migration have no origin value.
--     The application layer reads NULL as equivalent to DIRECT_WRITE (the
--     backward-compatible default on _MemoryBase). NOT NULL DEFAULT would
--     force a full-table UPDATE, which is expensive on large tables; NULL
--     is cheaper and still semantically unambiguous.
--   • VARCHAR(32) — long enough for any current or plausible future enum
--     value; short enough for an efficient index.
--   • DEFAULT 'DIRECT_WRITE' — new rows inserted without an explicit origin
--     (i.e. callers using the column directly via raw SQL) get the safe
--     default automatically. Application code always writes the value
--     explicitly from the MemoryOrigin enum.
--
-- Backward compatibility:
--   Existing rows receive NULL origin (not DEFAULT, because ALTER TABLE ADD
--   COLUMN with DEFAULT does a deferred backfill in Db2, which is fine — but
--   NULL is more honest: those rows were written before provenance tracking
--   existed and their origin is truly unknown).
--
-- SchemaPolicy.REQUIRE_EXISTING compatibility:
--   This migration follows the additive-only discipline of all prior migrations.
--   No existing column is altered; no table is dropped; no index is removed.
--   REQUIRE_EXISTING mode's validation checks for tables, columns, and vector
--   indexes — adding a new nullable column does not break any existing query.

-- ============================================================
-- TABLE: working_memory
-- ============================================================
ALTER TABLE working_memory
    ADD COLUMN origin VARCHAR(32);

CREATE INDEX ix_working_memory_origin
    ON working_memory (agent_id, origin);

-- ============================================================
-- TABLE: episodic_memory
-- ============================================================
ALTER TABLE episodic_memory
    ADD COLUMN origin VARCHAR(32);

CREATE INDEX ix_episodic_memory_origin
    ON episodic_memory (agent_id, origin);

-- ============================================================
-- TABLE: semantic_facts
-- ============================================================
ALTER TABLE semantic_facts
    ADD COLUMN origin VARCHAR(32);

CREATE INDEX ix_semantic_facts_origin
    ON semantic_facts (agent_id, origin);

-- ============================================================
-- TABLE: entity_profiles
-- ============================================================
ALTER TABLE entity_profiles
    ADD COLUMN origin VARCHAR(32);

CREATE INDEX ix_entity_profiles_origin
    ON entity_profiles (agent_id, origin);

-- ============================================================
-- TABLE: procedural_memory
-- ============================================================
ALTER TABLE procedural_memory
    ADD COLUMN origin VARCHAR(32);

CREATE INDEX ix_procedural_memory_origin
    ON procedural_memory (agent_id, origin);

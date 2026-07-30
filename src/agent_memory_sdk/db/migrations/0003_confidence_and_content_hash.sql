-- Migration 0003: confidence column (ENH-1) + content_hash column (ENH-2)
--
-- ENH-1 — confidence scoring
--   Adds a DOUBLE column to all five tables with a default of 1.0.
--   1.0 = fully certain / directly observed.
--   < 1.0 = inferred or derived (e.g. set by an LLM-based Consolidator).
--   DOUBLE is chosen over DECIMAL(3,2) because:
--     • Python floats are IEEE 754 doubles; no precision loss on round-trip.
--     • Db2 DOUBLE is a first-class numeric type with no fixed-precision
--       scaling overhead and maps naturally to Python float.
--     • The application enforces the 0.0-1.0 range; the DB does not need a
--       CHECK constraint (which would also be per-row overhead).
--   The column is NOT NULL with DEFAULT 1.0 so that existing rows (written
--   before this migration) are automatically graded as fully certain rather
--   than NULL, preserving backward compatibility without a backfill step.
--
-- ENH-2 — content_hash column
--   Adds VARCHAR(64) (hex SHA-256) per table plus a supporting index.
--   Kept separate from the confidence semantics but bundled in one migration
--   to minimise Db2 ALTER TABLE passes (one per table vs two).
--   NULL means "not yet hashed" (rows written before this migration).
--   The uniqueness check at write time is done in application code, not via
--   a UNIQUE constraint, because dedup is scoped to (agent_id, content_hash)
--   and allows superseded/deleted rows to share a hash with a live row.

-- ============================================================
-- TABLE: working_memory
-- ============================================================
ALTER TABLE working_memory
    ADD COLUMN confidence   DOUBLE  NOT NULL DEFAULT 1.0;

ALTER TABLE working_memory
    ADD COLUMN content_hash VARCHAR(64);

CREATE INDEX ix_working_memory_content_hash
    ON working_memory (agent_id, content_hash);

-- ============================================================
-- TABLE: episodic_memory
-- ============================================================
ALTER TABLE episodic_memory
    ADD COLUMN confidence   DOUBLE  NOT NULL DEFAULT 1.0;

ALTER TABLE episodic_memory
    ADD COLUMN content_hash VARCHAR(64);

CREATE INDEX ix_episodic_memory_content_hash
    ON episodic_memory (agent_id, content_hash);

-- ============================================================
-- TABLE: semantic_facts
-- ============================================================
ALTER TABLE semantic_facts
    ADD COLUMN confidence   DOUBLE  NOT NULL DEFAULT 1.0;

ALTER TABLE semantic_facts
    ADD COLUMN content_hash VARCHAR(64);

CREATE INDEX ix_semantic_facts_content_hash
    ON semantic_facts (agent_id, content_hash);

-- ============================================================
-- TABLE: entity_profiles
-- ============================================================
ALTER TABLE entity_profiles
    ADD COLUMN confidence   DOUBLE  NOT NULL DEFAULT 1.0;

ALTER TABLE entity_profiles
    ADD COLUMN content_hash VARCHAR(64);

CREATE INDEX ix_entity_profiles_content_hash
    ON entity_profiles (agent_id, content_hash);

-- ============================================================
-- TABLE: procedural_memory
-- ============================================================
ALTER TABLE procedural_memory
    ADD COLUMN confidence   DOUBLE  NOT NULL DEFAULT 1.0;

ALTER TABLE procedural_memory
    ADD COLUMN content_hash VARCHAR(64);

CREATE INDEX ix_procedural_memory_content_hash
    ON procedural_memory (agent_id, content_hash);

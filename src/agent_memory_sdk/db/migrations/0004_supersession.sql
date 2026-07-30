-- Migration 0004: supersession columns (ENH-3)
--
-- Adds soft-supersede support to ``semantic_facts`` only.
--
-- WHY semantic_facts only (see DECISIONS.md ENH-3 entry for full reasoning):
--
--   semantic_facts are the only table whose rows are independently-addressable
--   atomic claims that can logically contradict each other within the same
--   scope (e.g. "user prefers dark mode" later contradicted by "user switched
--   to light mode").  entity_profiles and procedural_memory are intentionally
--   excluded:
--
--     entity_profiles  — each profile is a *merged aggregate* kept current via
--       update() rather than an append of competing individual claims.  There
--       is typically one profile row per (agent_id, user_id); "supersession"
--       between two profile rows does not arise naturally in the design and
--       adding the columns would add cost with no clear query path that would
--       use them.
--
--     procedural_memory — skills/instructions are version-controlled via
--       update() (optimistic concurrency) and are not expected to contradict
--       each other within a scope in the way facts do.  If a skill changes,
--       the existing row is updated in place; a new competing row is not
--       typically written alongside the old one.
--
-- Governance distinction (see DECISIONS.md ENH-3 entry):
--   superseded_at IS NOT NULL  → "we learned this was contradicted by a newer
--                                  fact" — an AI-managed lifecycle event.
--   deleted_at IS NOT NULL     → "the user / operator asked us to forget this"
--     These two mechanisms are deliberately distinct so audit trails can tell
--     them apart.  Both cause rows to be excluded from normal reads; neither
--     hard-deletes the row.
--
-- Column definitions:
--
--   superseded_by VARCHAR(36)    — FK-style reference to the winning row's id
--                                  (no DB-level FK; FK would prevent orphan
--                                  handling when the winner is also later
--                                  superseded or deleted).
--   superseded_at TIMESTAMP      — when the supersession was recorded.
--   supersede_reason VARCHAR(255) — human-readable reason string set by the
--                                  Reconciler (e.g. "contradicts: user now
--                                  prefers light mode").

-- ============================================================
-- TABLE: semantic_facts
-- ============================================================
ALTER TABLE semantic_facts
    ADD COLUMN superseded_by     VARCHAR(36);

ALTER TABLE semantic_facts
    ADD COLUMN superseded_at     TIMESTAMP;

ALTER TABLE semantic_facts
    ADD COLUMN supersede_reason  VARCHAR(255);

-- Index to efficiently list all rows superseded by a given winner.
-- Also useful for auditing / chain-of-supersession queries.
CREATE INDEX ix_semantic_facts_superseded_by
    ON semantic_facts (agent_id, superseded_by);

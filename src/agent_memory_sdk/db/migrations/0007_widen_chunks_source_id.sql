-- Migration 0007: widen memory_chunks.source_id from VARCHAR(36) to VARCHAR(64)
--
-- WHY: The source_id column stores the ``id`` of the parent memory row, which
-- is always a plain UUID (36 characters). However, integration-test helpers
-- construct source_id values with a descriptive prefix followed by a UUID
-- (e.g. "doc-<uuid>", "doc-scope-a-<uuid>"), which exceed the original 36-
-- character limit and cause SQLSTATE=22001 (string data right truncation) when
-- ibm_db binds the value to the column.
--
-- The fix widens the column to VARCHAR(64), which comfortably accommodates
-- plain UUIDs (36 chars), prefixed test values, and any future application-
-- level identifiers that may carry a short prefix.
--
-- Db2 LUW supports in-place ALTER TABLE ... ALTER COLUMN ... SET DATA TYPE for
-- widening a VARCHAR column.

ALTER TABLE memory_chunks
    ALTER COLUMN source_id
    SET DATA TYPE VARCHAR(64);

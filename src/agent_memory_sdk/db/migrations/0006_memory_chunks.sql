-- Migration 0006: shared memory_chunks table for content chunking (ORC-2)
--
-- DESIGN CHOICE: one shared table, not five _chunks tables
--   A single ``memory_chunks`` table with a ``source_table VARCHAR(64)``
--   discriminator column was chosen over five per-type ``*_chunks`` tables.
--   Rationale: (a) the chunk rows all have the same shape — source_table,
--   source_id, chunk_index, chunk_text, embedding, scope columns; there is no
--   type-specific column that would justify splitting into five tables.
--   (b) One VECTOR INDEX services all chunk queries instead of five.
--   (c) The DDL/migration surface is smaller: one new table and one new index
--   instead of ten (five tables + five indexes).  (d) The query path — search
--   chunks, group by (source_table, source_id), resolve parents — is cleanest
--   when the chunks for ALL memory types live together: the resolver can fetch
--   parent rows from each distinct source_table in a single round-trip per
--   table rather than having to route to a different table object per type.
--   The per-type approach would only be preferable if different memory types
--   needed different chunk dimensions or different distance metrics; all five
--   types use the same VECTOR(1536,FLOAT32)/COSINE pairing today.
--
-- CHUNK STRATEGY
--   chunk_size characters with chunk_overlap characters of overlap between
--   adjacent chunks.  Defaults (configurable at MemoryStore construction):
--     chunk_size    = 800   characters
--     chunk_overlap = 200   characters
--   Chunking is gated on content length: records whose content does NOT
--   exceed chunk_threshold (default 2000 characters) are stored exactly as
--   today — single embedding on the parent row, no chunk rows created.
--   Records that DO exceed the threshold get their embedding replaced with
--   a zero-vector sentinel (to keep NOT NULL happy) and their content split
--   into overlapping chunks, each embedded separately in memory_chunks.
--
-- VECTOR COLUMN NULLABILITY
--   Same constraint as the parent tables (see 0002_memory_tables.sql):
--   "If a column is defined as VECTOR, a default value cannot be specified."
--   The application always supplies an explicit embedding on every chunk INSERT.
--
-- SCOPE COLUMNS
--   tenant_id, agent_id, user_id, thread_id are replicated here from the
--   parent row so that chunk searches can be pre-filtered by scope before
--   ranking by distance — exactly the same scope-before-vector-distance
--   pattern used in every other repository query.  Without scope columns on
--   the chunks table, every chunk search would be a full-table scan.

CREATE TABLE memory_chunks (
    id           VARCHAR(36)              NOT NULL,
    source_table VARCHAR(64)              NOT NULL,
    source_id    VARCHAR(36)             NOT NULL,
    chunk_index  INTEGER                 NOT NULL,
    chunk_text   CLOB(4096)              NOT NULL,
    embedding    VECTOR(1536, FLOAT32)            NOT NULL,
    -- scope columns (replicated from parent for pre-filtering)
    tenant_id    VARCHAR(128),
    agent_id     VARCHAR(128)            NOT NULL,
    user_id      VARCHAR(128),
    thread_id    VARCHAR(128),
    created_at   TIMESTAMP               NOT NULL DEFAULT CURRENT TIMESTAMP,
    CONSTRAINT pk_memory_chunks PRIMARY KEY (id)
);

-- Vector index (DiskANN/ANN, COSINE) — services chunk-level semantic search
CREATE VECTOR INDEX ix_memory_chunks_embedding
    ON memory_chunks (embedding)
    WITH DISTANCE COSINE;

-- Composite index for efficient scope + parent look-ups:
--   (agent_id, source_table, source_id) lets the resolver fetch all chunks
--   for a known parent in one seek-scan, and lets chunk-search queries
--   filter by agent scope before hitting the vector index.
CREATE INDEX ix_memory_chunks_parent
    ON memory_chunks (agent_id, source_table, source_id);

-- Scope index mirrors the pattern on parent tables.
CREATE INDEX ix_memory_chunks_scope
    ON memory_chunks (agent_id, tenant_id, user_id, thread_id);

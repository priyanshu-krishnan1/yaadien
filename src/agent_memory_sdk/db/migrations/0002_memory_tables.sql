-- Migration 0002: five per-type memory tables, vector indexes, scope indexes
--
-- EMBEDDING DIMENSIONS
--   All tables default to 1536 dimensions (OpenAI text-embedding-3-small /
--   ada-002 default). The SDK is dimension-agnostic at runtime — callers
--   supply embeddings via their EmbeddingProvider. The dimension encoded here
--   is only the schema default; for a different model, run a new migration
--   that ALTERs or re-creates the embedding column with the correct dimension.
--   1536 fits within Db2's row-organized FLOAT32 limit of 8168 dimensions.
--
-- VECTOR COLUMN NULLABILITY
--   Per IBM Db2 12.1 docs (https://www.ibm.com/docs/en/db2/12.1.x?topic=list-vector-values
--   and https://www.ibm.com/docs/en/db2/12.1.x?topic=statements-create-table):
--   "If a column is defined as XML or VECTOR, a default value cannot be specified
--   (SQLSTATE 42613). The only possible default is NULL."
--   VECTOR_FILL is NOT a valid DEFAULT expression; the original DDL comment was
--   incorrect. The embedding column is therefore NULLABLE (no NOT NULL, no DEFAULT).
--   The application layer (repositories/base.py) always supplies an explicit
--   embedding on INSERT: a zero-vector string passed to TO_VECTOR(?, FLOAT32) when
--   the caller has not provided a real embedding yet. Rows with NULL embeddings
--   (e.g. inserted by raw SQL without the embedding column) simply won't participate
--   in VECTOR_DISTANCE searches.
--   Note: the CREATE VECTOR INDEX below DOES require the column to be NOT NULL
--   for the ANN index to activate on Db2. With the column nullable, the index
--   is still created but Db2 will skip NULL rows during ANN traversal. Rows
--   inserted via the application layer always carry an explicit (non-null) vector.
--
-- DISTANCE METRIC CHOICES (per table)
--   working_memory  → COSINE
--     Raw conversation turns are short text snippets. Sentence-transformer
--     and similar models produce unit-normalized embeddings; cosine similarity
--     is the natural metric and the one recommended by IBM docs for
--     "text embeddings where magnitude is less important than orientation."
--   episodic_memory → COSINE
--     Summarized event/run descriptions — same rationale as working memory.
--   semantic_facts   → COSINE
--     Extracted factual statements ("User prefers dark mode"). Again short
--     text, typically unit-normalized embeddings. Cosine is standard.
--   entity_profiles  → COSINE
--     Aggregated user/entity profiles are dense summaries — cosine works
--     well for comparing semantic similarity between profiles.
--   procedural_memory → COSINE
--     Skill/instruction text. Cosine for the same reason.
--   Rationale for choosing COSINE uniformly: all five tables store natural-
--   language text whose embeddings are typically L2-normalized. EUCLIDEAN
--   and DOT differ from COSINE only when vectors have variable magnitude;
--   since embeddings from the dominant providers (OpenAI, sentence-transformers)
--   ARE normalized, EUCLIDEAN and COSINE rank identically in practice.
--   Using one metric across all tables simplifies SDK logic (no per-type
--   metric dispatch needed at query time) and aligns with IBM's own docs
--   recommendation: "Ideal for text embeddings and semantic similarity where
--   vector length is normalized."
--
-- CONTENT COLUMN TYPE
--   CLOB(65536) — chosen over VARCHAR because:
--   • Memory content can be arbitrarily long (multi-turn conversation,
--     long procedural instructions, full episode summaries).
--   • VARCHAR in Db2 maxes out at 32672 bytes; that is too small for
--     long-form memory content.
--   • CLOB(65536) = 64 KB limit, sufficient for all four memory types.
--   • CLOB columns are stored inline (up to 1 MB) without the overhead of
--     a separate LOB tablespace for small values.
--
-- METADATA COLUMN TYPE
--   VARCHAR(4096) — JSON stored as plain VARCHAR:
--   • Metadata is expected to be a small JSON object (< 4 KB): tags, source,
--     model name, run-id, etc.
--   • VARCHAR avoids CLOB overhead and supports standard JSON_VALUE /
--     JSON_EXISTS predicates without a separate BSON conversion layer.
--   • SYSTOOLS JSON UDFs are deprecated in Db2 12.1; modern JSON queries
--     use SYSIBM built-in JSON routines, which work on VARCHAR.
--   • If a caller needs to store larger metadata, a follow-up migration can
--     ALTER the column to CLOB(8192) or larger.
--
-- SCOPE COLUMNS (tenant_id, agent_id, user_id, thread_id)
--   VARCHAR(128) — UUIDs (36 chars) or opaque string identifiers.
--   tenant_id is NULLABLE (single-tenant deployments omit it; multi-tenant
--   deployments set it). agent_id is NOT NULL — every row must belong to an
--   agent. user_id and thread_id are nullable; some memory types (e.g.
--   procedural_memory) are agent-scoped rather than user/thread-scoped.

-- ============================================================
-- TABLE: working_memory
-- Purpose: raw current-session/thread turns, short-lived
-- ============================================================
CREATE TABLE working_memory (
    id          VARCHAR(36)         NOT NULL,
    tenant_id   VARCHAR(128),
    agent_id    VARCHAR(128)        NOT NULL,
    user_id     VARCHAR(128),
    thread_id   VARCHAR(128),
    content     CLOB(65536)         NOT NULL,
    metadata    VARCHAR(4096)       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536, FLOAT32),
    created_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    updated_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    expires_at  TIMESTAMP,
    version     INTEGER             NOT NULL DEFAULT 1,
    deleted_at  TIMESTAMP,
    CONSTRAINT pk_working_memory PRIMARY KEY (id)
);

-- Vector index (DiskANN/ANN, COSINE)
CREATE VECTOR INDEX ix_working_memory_embedding
    ON working_memory (embedding)
    WITH DISTANCE COSINE;

-- Scope indexes — queries always filter by these before ranking by distance
CREATE INDEX ix_working_memory_scope
    ON working_memory (agent_id, tenant_id, user_id, thread_id);
CREATE INDEX ix_working_memory_agent
    ON working_memory (agent_id);
CREATE INDEX ix_working_memory_expires
    ON working_memory (expires_at)
    WHERE expires_at IS NOT NULL;

-- ============================================================
-- TABLE: episodic_memory
-- Purpose: summarized past runs/threads/events
-- ============================================================
CREATE TABLE episodic_memory (
    id          VARCHAR(36)         NOT NULL,
    tenant_id   VARCHAR(128),
    agent_id    VARCHAR(128)        NOT NULL,
    user_id     VARCHAR(128),
    thread_id   VARCHAR(128),
    content     CLOB(65536)         NOT NULL,
    metadata    VARCHAR(4096)       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536, FLOAT32),
    created_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    updated_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    expires_at  TIMESTAMP,
    version     INTEGER             NOT NULL DEFAULT 1,
    deleted_at  TIMESTAMP,
    CONSTRAINT pk_episodic_memory PRIMARY KEY (id)
);

CREATE VECTOR INDEX ix_episodic_memory_embedding
    ON episodic_memory (embedding)
    WITH DISTANCE COSINE;

CREATE INDEX ix_episodic_memory_scope
    ON episodic_memory (agent_id, tenant_id, user_id, thread_id);
CREATE INDEX ix_episodic_memory_agent
    ON episodic_memory (agent_id);
CREATE INDEX ix_episodic_memory_expires
    ON episodic_memory (expires_at)
    WHERE expires_at IS NOT NULL;

-- ============================================================
-- TABLE: semantic_facts
-- Purpose: extracted facts (e.g. "User prefers Python over Java")
-- ============================================================
CREATE TABLE semantic_facts (
    id          VARCHAR(36)         NOT NULL,
    tenant_id   VARCHAR(128),
    agent_id    VARCHAR(128)        NOT NULL,
    user_id     VARCHAR(128),
    thread_id   VARCHAR(128),
    content     CLOB(65536)         NOT NULL,
    metadata    VARCHAR(4096)       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536, FLOAT32),
    created_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    updated_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    expires_at  TIMESTAMP,
    version     INTEGER             NOT NULL DEFAULT 1,
    deleted_at  TIMESTAMP,
    CONSTRAINT pk_semantic_facts PRIMARY KEY (id)
);

CREATE VECTOR INDEX ix_semantic_facts_embedding
    ON semantic_facts (embedding)
    WITH DISTANCE COSINE;

CREATE INDEX ix_semantic_facts_scope
    ON semantic_facts (agent_id, tenant_id, user_id, thread_id);
CREATE INDEX ix_semantic_facts_agent
    ON semantic_facts (agent_id);
CREATE INDEX ix_semantic_facts_expires
    ON semantic_facts (expires_at)
    WHERE expires_at IS NOT NULL;

-- ============================================================
-- TABLE: entity_profiles
-- Purpose: aggregated entity/user profiles
-- ============================================================
CREATE TABLE entity_profiles (
    id          VARCHAR(36)         NOT NULL,
    tenant_id   VARCHAR(128),
    agent_id    VARCHAR(128)        NOT NULL,
    user_id     VARCHAR(128),
    thread_id   VARCHAR(128),
    content     CLOB(65536)         NOT NULL,
    metadata    VARCHAR(4096)       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536, FLOAT32),
    created_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    updated_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    expires_at  TIMESTAMP,
    version     INTEGER             NOT NULL DEFAULT 1,
    deleted_at  TIMESTAMP,
    CONSTRAINT pk_entity_profiles PRIMARY KEY (id)
);

CREATE VECTOR INDEX ix_entity_profiles_embedding
    ON entity_profiles (embedding)
    WITH DISTANCE COSINE;

CREATE INDEX ix_entity_profiles_scope
    ON entity_profiles (agent_id, tenant_id, user_id, thread_id);
CREATE INDEX ix_entity_profiles_agent
    ON entity_profiles (agent_id);
CREATE INDEX ix_entity_profiles_expires
    ON entity_profiles (expires_at)
    WHERE expires_at IS NOT NULL;

-- ============================================================
-- TABLE: procedural_memory
-- Purpose: learned skills, instructions, how-to knowledge
-- Note: procedural memory is typically agent-scoped (not thread-scoped),
--       so user_id and thread_id are nullable and often unused.
-- ============================================================
CREATE TABLE procedural_memory (
    id          VARCHAR(36)         NOT NULL,
    tenant_id   VARCHAR(128),
    agent_id    VARCHAR(128)        NOT NULL,
    user_id     VARCHAR(128),
    thread_id   VARCHAR(128),
    content     CLOB(65536)         NOT NULL,
    metadata    VARCHAR(4096)       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536, FLOAT32),
    created_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    updated_at  TIMESTAMP           NOT NULL DEFAULT CURRENT TIMESTAMP,
    expires_at  TIMESTAMP,
    version     INTEGER             NOT NULL DEFAULT 1,
    deleted_at  TIMESTAMP,
    CONSTRAINT pk_procedural_memory PRIMARY KEY (id)
);

CREATE VECTOR INDEX ix_procedural_memory_embedding
    ON procedural_memory (embedding)
    WITH DISTANCE COSINE;

CREATE INDEX ix_procedural_memory_scope
    ON procedural_memory (agent_id, tenant_id, user_id, thread_id);
CREATE INDEX ix_procedural_memory_agent
    ON procedural_memory (agent_id);
CREATE INDEX ix_procedural_memory_expires
    ON procedural_memory (expires_at)
    WHERE expires_at IS NOT NULL;

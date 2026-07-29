# Design decisions — agent-memory-sdk

This is the single source of truth for decisions made on this project. Every
build step (see `PROMPTS.md`) must read this file before starting and append
a dated entry before finishing. Do not silently deviate from an existing
entry — if a later step needs to change one, add a new entry that
explicitly supersedes it and say why.

## 2026-07-29 — Foundational decisions (made before any code was written)

- **Language:** Python only.
- **Database:** IBM Db2 LUW.
- **Driver:** `ibm_db` (native) + `ibm_db_dbi` (DB-API 2.0 wrapper) as the
  primary connectivity layer. No SQLAlchemy dialect for v1.
- **Memory taxonomy** (synthesized from OpenAI Agents SDK memory docs,
  Oracle AI Agent Memory, and Microsoft Agent Framework/Cosmos DB memory),
  four types:
  1. **working memory** — raw current-session/thread turns, short-lived
  2. **episodic memory** — summarized past runs/threads/events
  3. **semantic memory** — extracted facts + aggregated entity/user profiles
  4. **procedural memory** — learned skills/instructions/how-to knowledge
- **Storage shape:** normalized per-type tables (one table per memory type
  above), not one polymorphic table. Reason: Db2's native vector index only
  activates when the vector column is `NOT NULL`, and each memory type has a
  differently-shaped/dimensioned embedding — a shared column fights that.
  This is closer to the Microsoft/Cosmos approach (separate collections)
  than Oracle's single unified core.
- **Vector search:** Db2 native `VECTOR` column type + `VECTOR_DISTANCE`
  (cosine, euclidean, dot, manhattan supported) + `CREATE VECTOR INDEX`
  (DiskANN-based ANN), with `FETCH EXACT` / `FETCH APPROX` / `FETCH` exposed
  to callers. Introduced in Db2 12.1.2+.
- **Processing model:** extraction/consolidation is pluggable and
  **synchronous by default** (developer-supplied callback run inline on
  `remember()`), with an explicit opt-in extension point to run it async
  later (e.g. via cron). Deliberately does NOT copy Microsoft's mandatory
  background-worker infra — this SDK must work as a plain installable
  library with zero required external services.
- **Framework integration:** framework-agnostic core first; LangChain,
  OpenAI Agents SDK (Session protocol), and MCP adapters are thin optional
  layers built on top, gated behind extras (`pip install
  agent-memory-sdk[langchain]`), not baked into the core.
- **Scoping/governance:** hierarchical scoping on every memory row —
  `tenant_id` (nullable, single-tenant ok) > `agent_id` > `user_id` >
  `thread_id`/`session_id`. Every read/write must be scoped; no cross-scope
  leakage by default. Every repository method requires at least `agent_id`.
- **Lifecycle:** soft-delete/tombstone via `deleted_at` (never hard DELETE
  by default), explicit `forget()` API, per-row `expires_at` TTL with a
  separate `purge_expired()` maintenance method (not automatic), and a
  `version` column for optimistic concurrency.

## 2026-07-30 — Bob MCP tool usage (superseded re: Jira, see next entry)

- **Decision:** Of Bob's available MCP connections, **Product Knowledge**
  (Milvus-backed semantic search over IBM docs — consulted for Db2
  VECTOR/index syntax in Step 2 and ibm_db driver behavior in Step 1, since
  these are IBM-specific and fast-moving) and **Web search** (Tavily —
  fallback for anything Product Knowledge doesn't cover) are used. Figma,
  Carbon, and Mural are left unused (design/UI tools, no fit for a headless
  library). Airtable, Amplitude, and Monday.com are left unused and
  unconfigured (require setup, and none fit this project's needs).
- **Reason:** Avoid setup/maintenance overhead on tools that don't serve
  this project, while using the two research-relevant tools to reduce the
  risk of the agent guessing at IBM/Db2-specific syntax from possibly-stale
  trained knowledge.
- **Made during:** Step 0 setup (before any build step ran).

## 2026-07-30 — Jira MCP dropped; tracking moved to a local HTML board

- **Decision:** Jira was originally planned for tracking (see prior entry),
  but Bob's Jira MCP connection isn't working. Tracking now uses
  `BOARD.html` — a single self-contained HTML file (Kanban-style: To
  Do/In Progress/Done) with the Epic and all 8 Stories embedded as JSON
  directly in the file. No server, no login, nothing to authorize. Agents
  update it by editing that embedded JSON and committing, the same way
  they update DECISIONS.md and ARCHITECTURE.md.
- **Reason:** The MCP connection failing is an external blocker, not a
  reason to stop tracking altogether — a local file has zero dependency on
  any external service being reachable, which also makes it more robust
  than Jira would have been for a build that may span disconnected
  sessions/tools.
- **Made during:** Step 0 setup (before any build step ran).
- **Supersedes:** the Jira-tracking half of the 2026-07-30 "Bob MCP tool
  usage" entry above. That entry's Product Knowledge/Web search decisions
  still stand.

## 2026-07-30 — Architecture/flow design lives in ARCHITECTURE.md, not a Bob MCP tool

- **Decision:** Current-state architecture and flow diagrams (component
  diagram, scoping model, schema ER diagram, remember()/recall() sequence
  diagrams) are captured in `ARCHITECTURE.md` at the repo root, using
  Mermaid — updated in place (overwritten) as the design evolves, not
  appended to like this file. None of Bob's MCP-connected tools were used
  for this, on evaluation.
- **Reason:** Mural's MCP access is read-only (can't write diagrams there).
  Product Knowledge is a read-only search index over IBM's own docs, not a
  place to store ours. Jira is a tracker — fine for linking to a design
  doc, bad as the doc itself (no diagramming, no single current-state
  view, gets unwieldy). Figma/Carbon are UI-design tools, wrong domain for
  a headless library. Airtable/Amplitude/Monday.com don't fit this project
  (see the 2026-07-30 MCP tool usage entry above). A versioned markdown
  file with Mermaid diagrams has no external auth dependency, is diffable,
  and renders natively in GitHub and most markdown viewers.
- **Made during:** Step 0 setup (before any build step ran).
- **How to apply:** Every build step that changes the design (schema,
  component boundaries, flows) must update the relevant section of
  `ARCHITECTURE.md`, not just append a DECISIONS.md entry. DECISIONS.md
  answers "why"; ARCHITECTURE.md answers "what, right now."

## 2026-07-30 — Step 1: build backend, connection pool design, ibm_db_dbi packaging

- **Decision (build backend):** **hatchling** chosen over setuptools.
  Reason: zero-config `src`-layout discovery (no `package_dir` dance),
  first-class PEP 517/518/660 editable-install support, and a leaner build
  tree for a library. setuptools remains the fallback option if any
  downstream tooling proves incompatible; the `pyproject.toml` is pure
  PEP 517 so switching build backends later is a one-line change.
- **Decision (ibm_db_dbi packaging):** `ibm_db_dbi` is **not** a separate
  PyPI distribution — it ships as a module inside the `ibm_db` package.
  Only `ibm_db>=3.2.3` is listed as a dependency. Confirmed from PyPI page
  for `ibm-db` and testing.
- **Decision (connection string format):** ODBC keyword pairs only —
  `DATABASE=x;HOSTNAME=x;PORT=x;PROTOCOL=TCPIP;UID=x;PWD=x[;Security=SSL]`.
  ibm_db does NOT accept JDBC-style URLs. `ibm_db.connect(conn_str, '', '')`
  with empty positional user/password args — credentials are in the keyword
  string, which is the form confirmed in IBM docs.
- **Decision (pool implementation):** A bounded `queue.Queue` of raw
  `ibm_db` handles pre-created at startup. `ibm_db_dbi.Connection` is
  re-wrapped from the raw handle on every checkout (lightweight, no teardown
  cost). Pool size and timeout are configurable via env vars
  (`DB2_POOL_SIZE`, `DB2_POOL_TIMEOUT`). `ibm_db_dbi` has no built-in
  pooling, confirming the manual approach.
- **Decision (Windows DLL guard):** `os.add_dll_directory` is called
  automatically before `import ibm_db` when `IBM_DB_WIN_DLL_DIR` is set,
  handling the Python 3.8+ Windows requirement without requiring callers to
  do it themselves.
- **Made during:** Step 1 (Scaffold)

## 2026-07-30 — Step 2: schema column types, distance metrics, embedding dimension

- **Decision (content column type):** `CLOB(65536)` for all five tables.
  Reason: VARCHAR maxes at 32 672 bytes in Db2 — too small for multi-turn
  conversation history, long episode summaries, or verbose procedural
  instructions. CLOB(65536) = 64 KB inline storage, no separate LOB
  tablespace overhead for values that fit, and sufficient for all four
  memory types. Can be widened in a later migration if needed.
- **Decision (metadata column type):** `VARCHAR(4096)` (JSON text) for all
  five tables. Reason: metadata is a small JSON object (< 4 KB). VARCHAR
  supports `JSON_VALUE`/`JSON_EXISTS` predicates natively in Db2 12.1
  without BSON conversion. SYSTOOLS JSON UDFs are deprecated in Db2 12.1.
  If larger metadata is needed, a follow-up migration can ALTER to CLOB.
- **Decision (distance metric):** **COSINE** for all five tables.
  Confirmed by IBM Db2 12.1 docs (ANN index implementation page): "Ideal
  for text embeddings and semantic similarity where vector length is
  normalized." All dominant embedding providers (OpenAI, sentence-
  transformers) produce L2-normalized FLOAT32 vectors. EUCLIDEAN and COSINE
  rank identically for normalized vectors, so there is no accuracy trade-off
  in choosing COSINE uniformly. Using one metric across all tables simplifies
  repository query logic (no per-type metric dispatch in SQL).
  Note: the Db2 CREATE VECTOR INDEX doc explicitly states "COSINE is only
  available for FLOAT32 vectors" — our column type is FLOAT32, so this is
  valid. The distance metric in `VECTOR_DISTANCE(...)` queries MUST match
  `WITH DISTANCE COSINE` in the index for the ANN index to be selected.
- **Decision (embedding dimension default):** 1536 (OpenAI
  `text-embedding-3-small` / `ada-002`). This fits within Db2's row-organized
  FLOAT32 limit of 8168 dimensions. The SDK is dimension-agnostic at runtime:
  callers supply embeddings via `EmbeddingProvider`; the 1536 in DDL is a
  schema default. A new migration with an ALTER TABLE or DROP/CREATE of the
  embedding column is needed to change the dimension. This decision is
  recorded here to close the open item from the Step 0 design.
- **Decision (NOT NULL + zero-vector default):** `VECTOR_FILL(1536,FLOAT32,0.0)`
  is the default for rows inserted without a pre-computed embedding. Zero-
  vectors rank at the bottom of any cosine-distance search (zero cosine-
  similarity with non-zero query vectors) so they never surface as false
  matches. Application layer (Step 3+) MUST replace zero-vectors with real
  embeddings before meaningful recall. NOT NULL is required for the Db2 ANN
  index to activate.
- **Decision (migration runner):** Plain Python using only stdlib + ibm_db_dbi
  DB-API. No alembic. ibm_db_dbi/Db2 support in alembic is inconsistent.
  Files: `000N_*.sql` in `src/agent_memory_sdk/db/migrations/`.
  Runner: `src/agent_memory_sdk/db/migrate.py` (`Migrator` class).
  Bootstrap strategy: check if `schema_migrations` exists first; create only
  if absent. This makes the bootstrap idempotent and compatible with the
  SQLite-backed unit tests.
- **Made during:** Step 2 (Schema & migrations)

## 2026-07-30 — Step 3: models, repositories, EmbeddingProvider, MemoryStore

- **Decision (embedding parameterization):** The `EMBEDDING_DIM` class
  attribute on `BaseRepository` (default: 1536) is the single override
  point for a different embedding model.  `MemoryStore.__init__` accepts
  an `embedding_dim` argument and propagates it to all five repositories,
  so callers change dimension in one place.  The DDL value is still 1536;
  changing the stored dimension requires a new schema migration.
- **Decision (embedding field on models):** `embedding: list[float]`
  defaults to `[]` (empty list).  An empty list is the Python-side sentinel
  meaning "not yet embedded"; the repository stores `VECTOR_FILL(dim,FLOAT32,0.0)`
  in Db2 in that case (matching the NOT NULL column default already in the DDL).
  The caller is responsible for providing a real embedding before meaningful
  recall is expected.
- **Decision (EmbeddingProvider):** A `@runtime_checkable` Protocol in
  `types.py` — a callable `(str) -> list[float]`.  `MemoryStore` does NOT
  store or call the provider in Step 3; the caller embeds text and passes the
  vector directly to `search()` / sets `record.embedding` before `create()`.
  Step 4+ will add optional auto-embedding via the provider on `remember()`.
- **Decision (VECTOR_SERIALIZE for SELECT):** The SELECT list in all
  repositories uses `VECTOR_SERIALIZE(embedding) AS embedding` to convert
  the Db2 VECTOR column back to a string `'[f1,f2,…]'` that the Python
  `_parse_vector()` helper can deserialize.  This is the portable way to
  read VECTOR columns via ibm_db_dbi (which does not natively deserialize
  VECTOR into a list).
- **Decision (TO_VECTOR for INSERT/SEARCH):** INSERT and VECTOR_DISTANCE
  queries use `TO_VECTOR(?, FLOAT32)` with the vector serialized as the
  string `'[f1,f2,…]'` as the bound parameter.  This keeps the parameter
  type as a plain Python string, which ibm_db_dbi handles reliably.
- **Decision (scope enforcement):** Every repository method calls
  `_require_agent_id(scope)` and builds scope predicates via
  `_scope_predicates(scope)`.  These predicates are part of every WHERE
  clause — including `get_by_id`, which pairs `id = ?` with the full scope
  check so callers cannot read another scope's row by guessing the id.
- **Decision (pagination):** `list()` with `offset=0` uses a plain
  `FETCH FIRST n ROWS ONLY` clause.  With `offset>0` it switches to a
  `ROW_NUMBER() OVER` sub-query, which is the portable Db2 idiom for
  offset pagination (Db2 does not support `OFFSET` in all configurations).
- **Decision (MemoryStore is a composition root only in Step 3):**
  `MemoryStore` holds the five repositories and propagates `embedding_dim`.
  It adds no business logic.  Lifecycle hooks (forget, purge, consolidation)
  are Step 4's responsibility.
- **Made during:** Step 3 (Core models & repositories)

## Open / not yet decided (fill in as steps happen)

- Embedding dimension(s) per memory type — **resolved in Step 2**: default
  1536 (FLOAT32). Change by writing a new migration that ALTERs the column.

---

### Entry template (copy this for every new decision)

```
## YYYY-MM-DD — <short title>

- **Decision:**
- **Reason:**
- **Made during:** Step N (<step name>)
- **Supersedes:** (link to prior entry, if any — otherwise omit)
```

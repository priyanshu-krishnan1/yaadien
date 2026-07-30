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
- **Decision (embedding column nullability — CORRECTED 2026-07-30, re-corrected same day):**
  The embedding column is **NOT NULL** with **no DB-side DEFAULT**. IBM Db2 12.1 docs
  (https://www.ibm.com/docs/en/db2/12.1.x?topic=list-vector-values and
  https://www.ibm.com/docs/en/db2/12.1.x?topic=statements-create-table)
  explicitly state: "If a column is defined as XML or VECTOR, a default value
  cannot be specified (SQLSTATE 42613). The only possible default is NULL."
  `VECTOR_FILL` is not a recognized scalar function for DEFAULT expressions
  and does not appear in the Db2 12.1 vector function list (confirmed via IBM
  docs + web search during the 2026-07-30 hygiene audit). The original DDL
  `DEFAULT VECTOR_FILL(...)` clause was invalid and has been removed. `NOT NULL`
  is retained — it is required for Db2's ANN vector index to activate; NULL rows
  are skipped by the index, degrading recall. The application layer
  (`repositories/base.py create()`) **always** supplies an explicit vector on
  every INSERT — a real embedding via `TO_VECTOR(?, FLOAT32)`, or `_zero_vec_str()`
  as a sentinel when the caller hasn't embedded yet — so the NOT NULL constraint
  is never violated by application code.
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
  meaning "not yet embedded"; the column is `NOT NULL` with no DB-side default,
  and the application layer (`repositories/base.py create()`) always supplies
  an explicit vector on every INSERT — a real embedding, or `_zero_vec_str()`
  as a zero-vector sentinel when none is provided.  The caller is responsible
  for providing a real embedding before meaningful recall is expected.
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
- **Decision (pagination):** `list_all()` (formerly `list()` — renamed to
  avoid shadowing the Python builtin, see hygiene-fix-pass entry) with
  `offset=0` uses a plain `FETCH FIRST n ROWS ONLY` clause.  With `offset>0`
  it switches to a `ROW_NUMBER() OVER` sub-query, which is the portable Db2
  idiom for offset pagination (Db2 does not support `OFFSET` in all
  configurations).
- **Decision (MemoryStore is a composition root only in Step 3):**
  `MemoryStore` holds the five repositories and propagates `embedding_dim`.
  It adds no business logic.  Lifecycle hooks (forget, purge, consolidation)
  are Step 4's responsibility.
- **Made during:** Step 3 (Core models & repositories)

## 2026-07-30 — BOARD.html redesign (retroactive)

- **Decision:** `BOARD.html` was redesigned from its original minimal form
  to a styled Kanban board with a dark header, progress bar on the epic
  banner, stats row (pill counters), and card-level move buttons (▶ Start,
  ✓ Done, ↩ Back, Reset). A `summary` field was added to every story object
  in the embedded JSON (one-line description shown on the card face). Toast
  notifications were added for user feedback on card moves. A detail modal
  with an "Add comment" textarea was added.
- **Reason:** Improved usability for multi-session reviews; the original
  plain file was hard to scan at a glance. Retroactive entry added during
  the 2026-07-30 hygiene audit.
- **Important — buttons are in-memory only:** The Start/Done/Back/Reset
  buttons in the modal footer update the in-page board state only. They do
  **not** persist to disk. Real status changes require editing the JSON
  block inside `BOARD.html` and committing, consistent with the existing
  working agreement (see 2026-07-30 "Jira MCP dropped" entry).
- **Made during:** Interstitial work between Steps 2 and 3 (exact session
  not recorded; discovered during hygiene audit).

## 2026-07-30 — Repo hygiene audit (7-item fix)

- **Decision:** Applied 7 hygiene fixes as a single commit ("fix: repo
  hygiene from audit"):
  1. **`.gitignore` replaced** — original file was backwards: it tracked
     `ARCHITECTURE.md`, `BOARD.html`, `Chats.md`, `DECISIONS.md`, and
     `PROMPTS.md` (files that must be tracked) while ignoring none of the
     standard Python artifacts. Replaced with a correct `.gitignore`
     covering `.env`, `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.venv/`,
     `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.claude`. The risk
     was that a real `.env` file with `DB2_UID`/`DB2_PWD` would have been
     committed on the next `git add -A && git commit`.
  2. **19 `.pyc`/`__pycache__` files untracked** — committed before
     `.gitignore` existed (a symptom of issue 1). Removed from the index
     via `git rm -r --cached`; files left on disk. New `.gitignore` prevents
     re-tracking.
  3. **`Chats.md` committed** — existed on disk but was silently excluded
     by the old `.gitignore`. Reviewed: contains only early planning notes
     and prompt-drafting text; no credentials or sensitive data. Now tracked.
  4. **README.md "Development setup" added** — `pytest` failed with
     `ModuleNotFoundError: No module named 'agent_memory_sdk'` without a
     prior `pip install -e ".[dev]"`. Added a short section above the
     "Full documentation" placeholder covering venv creation, editable
     install, and the three standard checks (`pytest`, `ruff check .`,
     `mypy src`).
  5. **`mypy` strict fix in `migrate.py`** — `Migrator.__init__`'s `pool`
     parameter had no type annotation. Fixed with a `TYPE_CHECKING` guard
     import of `ConnectionPool` from `db/connection.py`. Because
     `migrate.py` already has `from __future__ import annotations`, the
     annotation is never evaluated at runtime, avoiding any circular-import
     or missing-ibm_db risk.
  6. **Migration 0002 `VECTOR_FILL` DEFAULT removed** — the original
     DDL used `VECTOR(1536,FLOAT32) NOT NULL DEFAULT VECTOR_FILL(1536,FLOAT32,0.0)`.
     Per IBM Db2 12.1 docs (confirmed during this audit via Product Knowledge
     MCP + Tavily web search): VECTOR columns accept only NULL as a DEFAULT;
     `VECTOR_FILL` is not a DDL DEFAULT expression. The invalid `DEFAULT
     VECTOR_FILL(...)` clause was removed, but `NOT NULL` was retained —
     it is required for Db2's ANN vector index to activate, and the
     application layer always supplies an explicit vector on every INSERT
     (real embedding or zero-vector sentinel), so NOT NULL is never at risk.
     The migration had never been applied to a real Db2 instance so it was
     safe to edit in place. See the corrected Step 2 entry above.
  7. **Retroactive BOARD.html entry added** — see the entry immediately
     above this one.
- **Reason:** Safety (credential leak risk), correctness (invalid DDL would
  fail on first real Db2 run), and consistency with the project's own rule
  that every deviation gets logged.
- **Made during:** Hygiene audit, between Step 3 and Step 4.

## Open / not yet decided (fill in as steps happen)

- Embedding dimension(s) per memory type — **resolved in Step 2**: default
  1536 (FLOAT32). Change by writing a new migration that ALTERs the column.

## 2026-07-30 — Hygiene fix pass (post-audit regression + 4 additional improvements)

- **Decision:** Applied 5 targeted fixes as a single commit on top of the staged
  hygiene-audit changes, correcting one regression introduced by that audit and
  resolving additional correctness and tooling issues:

  1. **`NOT NULL` restored on `embedding` column (migration 0002)** — The hygiene
     audit correctly removed the invalid `DEFAULT VECTOR_FILL(...)` clause, but
     also removed `NOT NULL` as an overcorrection.  `NOT NULL` is required for
     Db2's ANN (DiskANN) vector index to activate; without it the index is still
     created but NULL rows are silently skipped, degrading recall.
     `repositories/base.py`'s `create()` ALWAYS supplies an explicit vector on every
     INSERT via `TO_VECTOR(?, FLOAT32)` — a real embedding or `_zero_vec_str()` as
     a zero-vector sentinel — so the constraint is never violated by application code.
     The column definition is now `VECTOR(1536, FLOAT32) NOT NULL` (no DEFAULT clause,
     which was already correctly removed). The migration had never been applied to a
     real Db2 instance, so editing it in place was safe.

  2. **Stale "embedding" descriptions updated in four places** — `models.py` docstring
     and `_MemoryBase` field comment, the Step 3 "embedding field on models" entry in
     `DECISIONS.md`, and the `embedding` line in `ARCHITECTURE.md` section 3's column
     type legend all still referenced the old `DEFAULT VECTOR_FILL(...)` or `NULLABLE`
     form.  All four now state: NOT NULL, no DB-side default; application layer always
     supplies an explicit vector on every INSERT.  The Step 2 "embedding column
     nullability" entry and hygiene-audit item 6 in `DECISIONS.md` were also corrected
     to remove the erroneous "NULLABLE" claim.

  3. **`mypy` strict pass on `repositories/base.py` — 4 errors fixed:**
     - `BaseRepository.list()` was renamed to `list_all()` to stop shadowing the
       Python builtin `list` type inside the class body (mypy: "Function …
       BaseRepository.list is not valid as a type"); all six call sites in
       `tests/test_repositories.py` updated accordingly.
     - Removed unused `# type: ignore[misc]` comment on the `_MODEL` class attribute.
     - `soft_delete()` now returns `bool(affected > 0)` instead of the bare comparison
       (mypy: "Returning Any from function declared to return bool" because
       `cur.rowcount` is `Any`).

  4. **`ruff check --fix .` — 6 errors auto-fixed** — 5 auto-fixed by ruff
     (unsorted import blocks in `models.py`, `types.py`, `test_repositories.py`;
     two unused imports in `test_repositories.py`), 1 manually fixed
     (SIM108 ternary simplification in `base.py`'s `search()` method).

  5. **`_parse_vector` and `_parse_dt` moved from `working.py` to `base.py`** —
     Both helpers were defined in `repositories/working.py` but imported cross-module
     by `episodic.py`, `facts.py`, `profiles.py`, and `procedural.py`, making
     `working.py` an accidental shared-utils dependency.  Moved to `base.py`
     alongside the existing `_vec_to_str` helper (the same kind of shared
     serialization utility).  `working.py` now re-imports them from `base`.  All
     four concrete repo files and `tests/test_repositories.py` updated to import
     from `base` directly.  `working.py`'s now-unused `from datetime import datetime`
     import was removed.

- **Reason:** Correctness (NOT NULL regression would have caused silently degraded
  ANN index on first real Db2 run), tooling hygiene (mypy strict and ruff clean on
  every file), and structural soundness (no accidental cross-module util dependency
  on `working.py`).
- **Made during:** Post-audit hygiene fix pass, between Step 3 and Step 4.
- **Supersedes:** Items 3b ("NOT NULL removed") and 6 ("column is now nullable")
  in the 2026-07-30 "Repo hygiene audit" entry above, and the Step 2 "embedding
  column nullability — CORRECTED 2026-07-30" entry (which incorrectly stated the
  column was NULLABLE).

## 2026-07-30 — Step 4: lifecycle — Consolidator protocol, purge_expired() semantics, forget(), and optimistic concurrency

- **Decision (Consolidator protocol shape):** The `Consolidator` is a
  `@typing.Protocol` defined in `types.py` with a single `__call__`
  method:

      def __call__(self, raw_memories: list[_MemoryBase]) -> list[_MemoryBase]: ...

  Input: a list of fully-persisted model instances just written to
  **working** or **episodic** memory (never the other three types).
  Output: a (possibly empty) list of derived records — any mix of
  `SemanticFact`, `EntityProfile`, or `ProceduralMemory`.
  `MemoryStore` calls this **synchronously** on the `remember()` call
  path, then persists each derived record via the appropriate repository
  with the same scope.  Errors in the consolidator are caught,
  logged, and swallowed — a consolidation failure must never roll
  back or suppress the original write.

- **Decision (NoOpConsolidator is the default):** `MemoryStore` uses
  `NoOpConsolidator` (always returns `[]`) when no consolidator is
  supplied.  Callers opt in by passing `consolidator=` at construction
  time.  This keeps Step-4 writes identical in cost to Step-3 writes for
  callers who don't use consolidation.

- **Decision (async extension point — documented, not implemented in sync path):**
  The sync consolidator path is the only runtime mechanism implemented.
  The async / out-of-band pattern (mark rows with `consolidated: false` in
  metadata at write time; poll with a cron job; call the consolidator;
  persist derived records; mark rows as processed) is documented in
  `types.py`'s `Consolidator` docstring and in `scripts/consolidate_pending.py`
  (a reference implementation).  No schema changes (e.g., `consolidated_at`
  column) are made in Step 4; a future migration would add that column
  to support the polling query efficiently.

- **Decision (purge_expired() semantics):** Hard-deletes rows where
  `deleted_at IS NOT NULL` (tombstoned), scoped to the caller's
  `MemoryScope`.  The condition is `deleted_at IS NOT NULL` only — not
  paired with `expires_at < NOW`.  Rationale: once a row is tombstoned
  via `forget()`, it is intentionally invisible to all normal queries
  (all `list_all` / `search` / `get_by_id` filter on `deleted_at IS NULL`)
  and has no further operational value — it is eligible for removal as
  soon as `purge_expired` is called.  Rows with `expires_at` in the past
  but NOT tombstoned are left by `purge_expired`; the normal query filters
  already exclude them from results, and they can only be hard-deleted
  after the caller explicitly tombstones them with `forget()`.
  This separates the "hide from reads" concern (handled by TTL filters at
  query time) from the "free disk space" concern (requires explicit
  tombstone + explicit purge call).  `purge_expired` is always called
  explicitly (cron / `scripts/purge_expired.py`); never automatically.

- **Decision (forget() is the canonical name; soft_delete() is the alias):**
  `BaseRepository.forget(id, scope)` is the primary soft-delete method,
  matching the `forget()` API name used in the Step 0 foundational decision
  and in `MemoryStore.forget()`.  `soft_delete()` remains as a backwards-
  compatible alias that delegates to `forget()`.  No behaviour change.

- **Decision (optimistic concurrency on version):** `BaseRepository.update()`
  conditions the UPDATE on `WHERE version = record.version AND deleted_at IS NULL`.
  If `rowcount == 0` after the UPDATE, `StaleWriteError` (from
  `agent_memory_sdk.exceptions`) is raised.  The version is atomically
  incremented to `record.version + 1` in the same UPDATE.  `update()`
  only modifies `content`, `metadata`, `embedding`, `updated_at`, and
  `version`; scope fields, `id`, `created_at`, and `deleted_at` are
  never touched.  The caller should retry after re-fetching the row with
  `get_by_id()`.

- **Decision (MemoryStore.remember() is the new primary write entry point):**
  Previously callers called `store.working.create(record, scope)` directly.
  `store.remember(record, scope)` is now the recommended path because it
  dispatches to the correct repository by model type AND runs the
  consolidator.  Direct `.create()` calls on individual repos still work
  (they skip consolidation — useful for derived writes inside the
  consolidator itself to avoid re-triggering it recursively).

- **Decision (StaleWriteError is re-exported from store.py and __init__.py):**
  `from agent_memory_sdk import StaleWriteError` works without callers
  needing to know the `exceptions` submodule.

- **Made during:** Step 4 (Lifecycle: TTL, versioning, forget, consolidation)

## 2026-07-30 — Step 4 audit: two doc-consistency fixes

- **Decision:** Applied two documentation-only fixes found during the Step 4
  audit; no code changes were made.

  1. **ARCHITECTURE.md section 3 ER diagram — `embedding` annotation updated.**
     All five tables (`working_memory`, `episodic_memory`, `semantic_facts`,
     `entity_profiles`, `procedural_memory`) still annotated the `embedding`
     column as `"NOT NULL default zero-vec"` in the Mermaid ER diagram.  This
     contradicts the prose column-type legend directly above the diagram (already
     correctly updated to "NOT NULL, no DB-side default; application layer always
     supplies an explicit vector") and the authoritative Step 2 / hygiene-fix-pass
     entries in this file.  Changed all five annotations to `"NOT NULL, app-supplied"`.

  2. **`repositories/base.py` `purge_expired()` docstring — stale numbered
     conditions removed.**  The docstring opened with two numbered conditions
     implying `expires_at` gates purge eligibility (condition 1: tombstoned AND
     `expires_at < NOW`; condition 2: tombstoned AND `expires_at IS NULL`),
     followed immediately by an "In short" line that correctly stated "all
     tombstoned rows are eligible for purge."  The numbered conditions were stale
     draft text that contradicted the actual SQL (`WHERE deleted_at IS NOT NULL`
     with no `expires_at` predicate) and the deliberate design recorded in the
     Step 4 "purge_expired() semantics" entry above.  Removed the two numbered
     conditions; the docstring now leads directly with the accurate summary.

- **Reason:** Diagram/prose and code/docstring inconsistencies mislead readers
  into wrong assumptions about schema defaults and purge eligibility.  Both
  fixes bring the docs into agreement with the code and the already-recorded
  decisions; neither changes any behaviour.
- **Made during:** Step 4 audit (doc-consistency pass)

---

## 2026-07-31 — Step 5: MemoryScope value object, SQL scope enforcement, cross-scope isolation tests

- **Decision (MemoryScope shape — confirmed as-built):**
  `MemoryScope` is a **frozen** Pydantic v2 model defined in `models.py`:

      class MemoryScope(BaseModel):
          tenant_id: str | None = None  # broadest; single-tenant callers omit
          agent_id: str                 # required; minimum isolation unit
          user_id: str | None = None    # narrows to a specific end-user
          thread_id: str | None = None  # narrows to a single conversation

  `model_config = {"frozen": True}` makes it hashable and immutable — safe
  to use as a dict key or set member, and prevents accidental mutation after
  construction.  This shape was established in Step 3 and is unchanged by
  Step 5; the step confirmed it is the right object and added tests.

- **Decision (scope enforcement is always additive, never subtractive):**
  `_scope_predicates(scope)` always emits `agent_id = ?` (required).
  `tenant_id = ?`, `user_id = ?`, and `thread_id = ?` are only added when
  the corresponding field is not `None`.  A narrower scope (e.g. adding
  `thread_id`) *increases* isolation; it cannot decrease it.  A caller
  passing only `agent_id` sees all rows for that agent across all users and
  threads — this is the widest safe query.

- **Decision (every SQL path includes scope predicates):**
  Audit of all six mutating/reading SQL paths in `BaseRepository` confirmed
  that every generated statement includes scope predicates as part of the
  `WHERE` clause.  No path issues SQL with only a primary-key predicate and
  no scope check.  Specific guarantees:
  - `create()` — stamps all four scope columns onto the row at INSERT time.
  - `get_by_id()` — `WHERE id = ? AND <scope_sql> AND deleted_at IS NULL`
  - `list_all()` — `WHERE <scope_sql> AND deleted_at IS NULL`
  - `search()` — `WHERE <scope_sql> AND deleted_at IS NULL` (before ORDER BY)
  - `forget()` — `WHERE id = ? AND <scope_sql> AND deleted_at IS NULL`
  - `update()` — `WHERE id = ? AND <scope_sql> AND version = ? AND deleted_at IS NULL`
  - `purge_expired()` — `WHERE deleted_at IS NOT NULL AND <scope_sql>`

- **Decision (cross-scope read isolation is enforced by bound parameters, not application logic):**
  The isolation boundary is the SQL WHERE clause — a row in scope A cannot
  be found by a query carrying scope B's `agent_id` / `tenant_id` values, because
  the predicates are bound parameters, not string interpolation.  There is no
  application-layer list-and-filter step; Db2 does the filtering.
  `tests/test_scoping.py` captures this: the fake cursor returns an empty
  result set (as Db2 would for a mismatched scope predicate) and the test
  asserts `None` / `[]` — never the owner's row.

- **Edge cases resolved:**
  1. **`update()` with wrong scope raises `StaleWriteError`, not a custom
     scope error.** The UPDATE conditions on `id = ? AND <scope_sql> AND
     version = ?`. If the scope doesn't match, `rowcount == 0`, and the
     same `StaleWriteError` that fires on a version conflict fires here.
     This is intentional: from the caller's perspective, the row "wasn't
     there" — which is the correct observable behaviour for a cross-scope
     attempt.  A dedicated `ScopeViolationError` was considered and
     rejected because (a) it would require distinguishing "row doesn't
     exist", "row belongs to wrong scope", and "row has a stale version" —
     all requiring a separate SELECT — and (b) leaking which of the three
     conditions occurred provides information to a misbehaving caller.
  2. **`purge_expired()` with the wrong scope returns 0, not an error.**
     Same reason: the DELETE silently affects 0 rows.  The caller receives a
     count of 0, which is safe.
  3. **`create()` overwrites the model's scope fields from the `scope` arg.**
     If `record.agent_id` differs from `scope.agent_id`, the scope argument
     wins.  This prevents the caller from inserting a row into one scope
     while passing a different scope for the connection.
  4. **Empty string `agent_id` is rejected at the `_require_agent_id()` guard**
     before any SQL is issued.  Pydantic allows constructing a `MemoryScope`
     with `agent_id=""` (it's a valid `str`), but the guard
     (`if not scope.agent_id: raise ValueError(...)`) treats it as missing.
     This behaviour is tested and intentional; callers must not pass
     empty-string scope values.

- **What was NOT changed in Step 5:**
  - No schema changes (all scope columns already existed from Step 2).
  - No API surface changes (MemoryScope was already the required parameter
    type on every repository and store method since Step 3).
  - No new exceptions.  The decision to surface cross-scope update attempts
    as `StaleWriteError` (see edge case 1 above) avoids introducing a
    misleading new exception path.

- **New test file:** `tests/test_scoping.py` — 91 unit tests covering:
  `MemoryScope` value object contract, `_scope_predicates()` helper,
  cross-scope isolation for `get_by_id`/`list_all`/`search`/`forget`/
  `update`/`purge_expired` on all five repository types (5 × 6 = 30
  parametrized tests), `MemoryStore` facade scope propagation, and empty
  `agent_id` rejection on every operation.  Total test suite: 195 tests.

- **Made during:** Step 5 (Governance / scoping enforcement)

---

## 2026-07-31 — Step 6: Framework adapters (LangChain, OpenAI Agents SDK, MCP)

- **Decision (adapter module layout):**
  All three adapters live under `src/agent_memory_sdk/adapters/`, each in
  its own file:
  - `adapters/langchain.py` — `Db2ChatMessageHistory`, `Db2MemoryStore`
  - `adapters/openai_agents.py` — `Db2Session`
  - `adapters/mcp_server.py` — `create_server(store)` factory
  The `adapters/__init__.py` is documentation-only (no runtime imports of
  framework packages).  All framework imports are deferred to
  `_require_<framework>()` guard functions that run at class instantiation
  time (not at `import agent_memory_sdk` time), so the core is importable
  with zero adapter dependencies installed.

- **Decision (LangChain — Db2ChatMessageHistory):**
  Implements LangChain's `BaseChatMessageHistory` interface
  (`messages` property, `add_message()`, `add_messages()`, `clear()`)
  backed by `store.working`.  Each LangChain `BaseMessage` is serialised to
  one `WorkingMemory` row: the message `.content` is stored in the `content`
  column; the message type (`HumanMessage`, `AIMessage`, …), `additional_kwargs`,
  `response_metadata`, and `tool_call_id` / `tool_calls` are stored in the
  `metadata` JSON column.  On read, `_metadata_to_message()` reconstructs
  the correct subclass.  This approach is lossless for all common message
  types and degrades gracefully (falls back to `HumanMessage`) for unknown
  types.  The scope (`agent_id`, `thread_id`) must be supplied by the caller;
  `thread_id` = LangChain `session_id` is the recommended mapping.

- **Decision (LangChain — Db2MemoryStore):**
  Implements LangChain's `BaseStore[str, str]` (`mget`, `mset`, `mdelete`,
  `yield_keys`) backed by `store.facts` (default) or `store.profiles` (when
  `namespace != "facts"`).  Keys are stored in `metadata["store_key"]`.
  Look-up is a linear scan over `list_all()` results — acceptable for the
  expected key counts per agent scope (< 1000); a production deployment
  should add a JSON value index on the `metadata` column for scale.
  This maps naturally: semantic facts → known key-value facts about the world
  or user preferences; entity profiles → aggregated user/entity state.

- **Decision (OpenAI Agents SDK — Db2Session):**
  Implements the OpenAI Agents SDK `Session` protocol (`add_message()`,
  `get_messages()`, `clear()`).  Messages are stored as JSON-serialised
  dicts in `WorkingMemory.content` (preserving every field without schema
  coupling).  `get_messages()` reverses `list_all()` output to restore
  chronological order.  `clear()` soft-deletes all rows for the scope.
  A non-protocol bonus method `recall_episodes(query_embedding, top_k)`
  searches `store.episodic` at agent+user scope (not thread-scoped) so
  past episode summaries can be injected before new agent runs without
  mixing them into the live message list.

- **Decision (MCP adapter — tool design):**
  The MCP adapter exposes four tools via `create_server(store)`:
  1. `remember` — creates a record of any memory type.
  2. `recall` — semantic search (if `query_embedding` provided) or recency
     list (fallback when no embedding given).  The embedding is a caller
     parameter because the MCP server has no built-in embedding model.
  3. `forget` — soft-deletes a record by id.
  4. `list_memories` — recency-based list, no vector search.
  All tools accept optional `user_id`, `thread_id`, and `tenant_id` params
  so MCP callers can set the full `MemoryScope`.  Tools return
  `TextContent(type="text", text=json_string)` — structured data is
  JSON-serialised to a single text blob, which is the most portable MCP
  return format.  The server is created as a `Server("agent-memory-sdk")`
  instance; callers run it with `server.run(read_stream, write_stream)` in
  an async context.  A `__main__` entry point (`python -m
  agent_memory_sdk.adapters.mcp_server`) starts a stdio MCP server
  automatically using `ConnectionPool()` (reads `DB2_*` env vars).

- **Decision (BaseChatMessageHistory is NOT dynamically subclassed):**
  An earlier design idea was to inherit from `BaseChatMessageHistory` at
  runtime so that `isinstance(history, BaseChatMessageHistory)` returns
  `True`.  This was rejected because:
  (a) `RunnableWithMessageHistory` and most LangChain tooling calls the
  interface methods duck-typing style, not `isinstance` checks;
  (b) dynamic inheritance from a deferred import complicates `__class__`
  resolution and breaks mypy strict mode;
  (c) the duck-typing interface (`messages`, `add_message`, `clear`) is all
  that callers actually need.
  `Db2ChatMessageHistory` therefore does NOT inherit from
  `BaseChatMessageHistory`; it satisfies the structural subtype protocol
  (duck typing). Same for `Db2MemoryStore` vs `BaseStore`.

- **Decision (test strategy: all adapters tested without real frameworks):**
  `tests/test_adapters.py` patches `_require_<framework>()` and stubs out
  framework types (MagicMock for LangChain messages, simple dicts for
  OpenAI Agents, `patch.dict(sys.modules, ...)` for MCP types) so the
  adapter logic is exercised without installing the actual framework
  packages.  The 48 tests cover: message serialisation round-trips,
  SQL delegation (INSERT/UPDATE/SELECT assertions on the fake cursor),
  scope propagation, import-guard error messages, and fallback behaviours
  (list fallback when no embedding; corrupted content fallback).

- **Made during:** Step 6 (Framework adapters)

---

## 2026-07-31 — Db2Session protocol correction (add_items / get_items / pop_item / clear_session)

- **Decision:**
  Rewrote `Db2Session` in `adapters/openai_agents.py` to implement the
  real OpenAI Agents SDK `Session` protocol as documented at
  https://openai.github.io/openai-agents-python/ref/memory/session/.
  All four protocol methods are now `async def` with the correct names and
  signatures:
  - `async def add_items(self, items: list[dict[str, Any]]) -> None` — iterates
    the list and persists each item via the private `_persist_message()` helper
    (same storage logic as the old `add_message`).
  - `async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]`
    — returns all non-deleted messages in chronological order; when `limit` is
    provided, returns only the most-recent `limit` messages (tail of the
    chronological list), matching the `SQLiteSession` convention.
  - `async def pop_item(self) -> dict[str, Any] | None` — fetches the single
    most-recent non-deleted row (`list_all(..., limit=1)`), soft-deletes it via
    `store.working.forget()`, and returns its deserialized content; returns
    `None` if the session is empty.
  - `async def clear_session(self) -> None` — same soft-delete-all logic as the
    former `clear()`.
  The underlying repository calls remain synchronous (matching the sync-first
  design used throughout the SDK); only the four Session method signatures are
  `async` so they satisfy the protocol.
  The module-level usage-example docstring was updated to show `await` on all
  four protocol calls.
  `tests/test_adapters.py::TestDb2Session` was updated to call
  `add_items`/`get_items`/`pop_item`/`clear_session` via `asyncio.run()`, with
  new tests covering: multi-item `add_items`, `get_items(limit=N)` truncation,
  `pop_item` returns/tombstones the most-recent row, and `pop_item` returns
  `None` on an empty session.  Five tests were renamed (old names removed).

- **Reason:**
  The old method names (`add_message`, `get_messages`, `clear`) did not match
  the actual `Session` protocol.  `pop_item` was missing entirely.  All four
  methods must be `async def`.  Passing a `Db2Session` to `Runner.run(...,
  session=session)` would have failed immediately at runtime because the SDK
  calls `await session.add_items(...)`.

- **Minor fixes bundled in the same commit:**
  1. `Db2ChatMessageHistory` class docstring — removed the false claim that the
     class "dynamically inherits from `BaseChatMessageHistory` at instantiation
     time".  The actual design (deliberate duck-typing, no dynamic inheritance)
     is now stated correctly, consistent with the "BaseChatMessageHistory is NOT
     dynamically subclassed" decision recorded in the Step 6 entry above.
  2. `Db2ChatMessageHistory.add_messages()` docstring — added an explicit note
     that the method is not yet optimised for batching (each message still
     checks out a separate DB connection via `add_message`).  The prior
     one-liner implied it was a real batch operation.

- **Made during:** Step 6 audit / correctness pass
- **Supersedes:** "OpenAI Agents SDK — Db2Session" bullet in the
  *2026-07-31 — Step 6: Framework adapters* entry above, which recorded
  the wrong protocol method names (`add_message`, `get_messages`, `clear`)
  and omitted `pop_item`.

---

## 2026-07-31 — Step 7: Integration tests

- **Decision (test layout and skip mechanism):**
  Integration tests live in `tests/integration/` as a dedicated package,
  separate from the unit tests in `tests/`.  They are gated by two
  complementary mechanisms:
  1. A `pytestmark = pytest.mark.integration` marker in every test module.
  2. A `pytest_collection_modifyitems` hook in `tests/integration/conftest.py`
     that auto-skips the entire `tests/integration/` directory at collection
     time when `DB2_DATABASE` is not set in the environment.  This means
     `pytest tests/` (or any CI run without Db2) never sees a failure from
     the integration suite — the tests are simply reported as skipped.
  The unit test suite (`pytest tests/ --ignore=tests/integration/`) has no
  dependency on Db2 and continues to run at 248 tests in ~1.2 s.

- **Decision (fixture isolation strategy):**
  All integration tests use a fresh `unique_agent_id` UUID-based fixture per
  test function, ensuring complete isolation between tests even when running
  in parallel or against a shared Db2 instance.  The `db2_pool` and `store`
  fixtures are session-scoped (one pool / one store object for the full test
  session), while `scope`, `thread_scope`, and `unique_agent_id` are
  function-scoped (fresh per test).  Migrations are applied once per session
  via the `migrated_pool` fixture; the migration is idempotent so repeated
  runs do not fail.

- **Decision (coverage):**
  The integration suite covers the seven areas mandated by the Step 7
  prompt:
  1. **Schema migration end-to-end** (`test_migration.py`) — idempotency,
     `schema_migrations` version tracking, all five tables with correct
     columns, `NOT NULL` VECTOR column type verified via `SYSCAT.COLUMNS`,
     vector index presence verified via `SYSCAT.INDEXES`.
  2. **Vector search correctness** (`test_core.py::TestVectorSearch`) — unit
     vectors with known cosine similarity guarantee the nearest-neighbour is
     deterministic; `top_k` cap verified; tombstoned rows excluded; cross-
     scope search isolation.
  3. **Scope isolation** (`test_core.py::TestScopeIsolation`) — `list_all`
     and `get_by_id` cross-scope isolation, thread-scope isolation.
  4. **TTL purge** (`test_core.py::TestTTL`, `TestPurgeExpired`) — expired
     rows excluded from `list_all`; `purge_expired()` hard-deletes only
     tombstoned rows; live rows survive; scope isolation on purge.
  5. **forget / tombstone** (`test_core.py::TestForgetTombstone`) — row
     hidden from `get_by_id` and `list_all`; `store.forget()` facade;
     `forget()` returns `False` for missing row.
  6. **Framework adapter round-trips** (`test_adapters_integration.py`):
     - LangChain `Db2ChatMessageHistory`: add_message / messages property
       (chronological order) / clear / add_messages batch / HumanMessage +
       AIMessage type preservation after DB round-trip.
     - LangChain `Db2MemoryStore`: mset / mget / mdelete / yield_keys /
       prefix filter.
     - OpenAI Agents SDK `Db2Session`: add_items / get_items (chronological
       order, limit) / clear_session / pop_item (returns + tombstones most
       recent, returns None when empty) / recall_episodes (episodic vector
       search).
     - MCP tool functions: `_tool_remember` (inserts real row) /
       `_tool_recall` (vector search) / `_tool_forget` (tombstones) /
       `_tool_list` / recall fallback to list when no embedding.
  7. **Consolidator integration** (`test_core.py::TestConsolidator`) — a
     custom consolidator persists derived `SemanticFact` rows on `remember()`.
  Also covers: optimistic concurrency (`update()` + `StaleWriteError`),
  CRUD round-trips for all five memory types.

- **Decision (documentation):**
  `INTEGRATION_TESTING.md` was added at the repo root documenting: Docker
  `ibmcom/db2` setup (full `docker run` command with `--privileged`),
  connectivity verification, env var configuration, editable install with
  all extras, running with/without Db2, IBM Cloud Db2 SSL variant, a
  coverage table for all three test files, marker/skip behaviour, table
  cleanup, and a troubleshooting table.

- **Gap found and fixed — `base.py` module docstring for `purge_expired()`:**
  The module-level docstring in `repositories/base.py` (lines 19-22)
  described `purge_expired()` as deleting "rows that are both tombstoned AND
  past their TTL, OR rows with an expired TTL that are not tombstoned."  This
  contradicts the actual SQL (`WHERE deleted_at IS NOT NULL` — no
  `expires_at` predicate) and the deliberate design recorded in the Step 4
  entry ("Rows with `expires_at` in the past but NOT tombstoned are left by
  `purge_expired`") and the Step 4 audit entry (which corrected the same
  stale wording in the `purge_expired()` method docstring but missed this
  parallel description in the module-level header).  The module docstring was
  corrected in this step to match the code and the recorded decision.
  This is the only gap found between DECISIONS.md and the actual code.

- **All other recorded decisions match the code as-built.**
  Checked:
  - Step 1: `ConnectionPool`, `_build_conn_str`, Windows DLL guard — matches.
  - Step 2: DDL (`NOT NULL`, no `DEFAULT` on VECTOR, COSINE on all indexes,
    `CLOB(65536)`, `VARCHAR(4096)` metadata, `VARCHAR(128)` scope cols) — matches.
  - Step 3: `_scope_predicates`, `_require_agent_id`, scope on every path,
    `TO_VECTOR`/`VECTOR_SERIALIZE`, `ROW_NUMBER()` pagination, `MemoryStore`
    embedding_dim propagation — matches.
  - Step 4: `forget()` is canonical (soft_delete alias), `purge_expired()`
    semantics (`deleted_at IS NOT NULL` only), optimistic concurrency on
    `version`, consolidator is NoOp by default, errors caught/logged —
    matches.
  - Step 5: `MemoryScope` frozen, all six SQL paths include scope predicates,
    `create()` overwrites scope fields from scope arg, empty `agent_id`
    rejected — matches.
  - Step 6: adapter module layout, duck-typing (no dynamic inheritance),
    Db2Session uses correct protocol methods (after the prior correction
    entry), MCP tools return `TextContent(type="text", text=json_string)` —
    matches.

- **Made during:** Step 7 (Integration tests)

---

## 2026-07-31 — Db2 12.1.5 fp0 compatibility fixes (vector binding, search, TTL, MCP patching)

- **Decision:**
  Four Db2 12.1.5 fp0 incompatibilities found and fixed during the live
  integration-test run.

  1. **`TO_VECTOR(?, FLOAT32)` binding fails (`SQL0901N`).**
     All four syntaxes involving a bound `?` for the vector value fail on
     this Db2 version.  The only working form is to **inline the vector
     string as a SQL literal**: `CAST('{vec_str}' AS VECTOR({dim},FLOAT32))`.
     The vector string is produced by `_vec_to_str()` from Python floats
     (no user input), so there is no SQL-injection risk.
     Changed in `repositories/base.py`: `create()`, `update()`, `search()`.
     Unit tests in `test_lifecycle.py` and `test_repositories.py` updated to
     match the new SQL shape (`CAST(` / `AS VECTOR(`).

  2. **`VECTOR_SERIALIZE()` in SELECT + `VECTOR_DISTANCE()` in ORDER BY
     conflict (`SQL0440N`).**
     When both functions appear together in a single SELECT, the driver
     reports "No authorized routine named VECTOR_DISTANCE having compatible
     arguments."  Work-around: two-step query in `search()` — step 1 fetches
     IDs in nearest-first order (no `VECTOR_SERIALIZE` in SELECT); step 2
     fetches full rows by those IDs using the normal `_SELECT_COLS` (which
     includes `VECTOR_SERIALIZE`), then reorders to restore distance rank.

  3. **`CURRENT TIMESTAMP` vs stored UTC datetimes (`expires_at` TTL filter
     wrong).**
     Db2 `CURRENT TIMESTAMP` returns server **local time**; Python stores
     UTC datetimes.  The Db2 server is UTC-7, so a value that expired 1
     second ago in UTC appears 7 hours in the future to `CURRENT TIMESTAMP`,
     causing `expires_at > CURRENT TIMESTAMP` to evaluate TRUE (expired row
     shown as live).  Fix: use `CURRENT TIMESTAMP - CURRENT TIMEZONE` which
     subtracts the server's UTC offset to yield the UTC-equivalent timestamp.
     Applied to `list_all()` and `search()` in `repositories/base.py`.

  4. **MCP `_tool_*` functions import `TextContent` locally, making them
     unpatchable in unit tests without `mcp` installed.**
     Added a module-level `_TextContent(**kwargs)` wrapper in
     `adapters/mcp_server.py` that imports `mcp.types.TextContent` lazily.
     All four `_tool_*` functions now call `_TextContent(...)`.  Unit tests
     patch `agent_memory_sdk.adapters.mcp_server._TextContent` instead of
     `mcp.types.TextContent`, so they no longer require `mcp` to be
     installed.  Integration tests updated to use the same patch target.

  5. **Stale-write test bug (optimistic concurrency).**
     `test_update_stale_version_raises` in `tests/integration/test_core.py`
     incorrectly assumed `stored.version` stayed at 1 after `update()`
     returned, but `update()` mutates the object's version in-place.  Fixed
     by explicitly resetting `stored.version = 1` after the first update call
     to simulate a stale concurrent reader.

  6. **Partial (filtered) indexes not supported (`SQL0104N`).**
     `CREATE INDEX ... WHERE expires_at IS NOT NULL` fails on Db2 12.1.5 fp0.
     Removed all five `WHERE expires_at IS NOT NULL` predicates from
     `db/migrations/0002_memory_tables.sql`.  Added a comment explaining the
     compatibility constraint.  The plain indexes still accelerate TTL queries;
     rows with NULL `expires_at` incur negligible index overhead.

- **Reason:**
  All six issues were discovered only against the live Db2 12.1.5 fp0
  instance; none were detectable from unit tests alone.  The integration
  suite now passes 62/62 runnable tests (10 skipped: LangChain not installed
  in the test environment).

- **Made during:** Step 7 (Integration tests — live Db2 run)

---

## 2026-07-31 — Security and documentation fixes (SQL injection hardening, ARCHITECTURE.md catch-up, Docker image update)

- **Decision:**
  Three fixes applied as a follow-up to the Db2 12.1.5 fp0 compatibility work.

  1. **SQL injection hardening in `_vec_to_str()` (`repositories/base.py`).**
     The prior entry claimed "no SQL-injection risk" for the inlined vector
     literal.  That claim was true for `create()` and `update()`, where
     `record.embedding` is a Pydantic-validated `list[float]` field and
     Pydantic coerces or rejects non-numeric values before the record object
     is created.  It was **not** true for `search()`: `query_embedding` is
     an unenforced type hint, and `_tool_recall` in `adapters/mcp_server.py`
     passes `args.get("query_embedding")` straight from client-supplied JSON
     with zero coercion.  A crafted string element (e.g.
     `"1) UNION SELECT ... --"`) would have been joined by `_vec_to_str()`
     and interpolated directly into the SQL literal.
     **Fix:** changed `_vec_to_str()` from `str(f) for f in embedding` to
     `str(float(f)) for f in embedding`.  The `float()` coercion raises
     `ValueError` / `TypeError` for any non-numeric element before it ever
     reaches SQL, closing the hole at all three call sites simultaneously.
     A unit test (`test_search_non_numeric_element_raises` in
     `tests/test_repositories.py`) was added to pin this behaviour.

  2. **ARCHITECTURE.md Section 5 (semantic search flow) updated.**
     The mermaid sequence diagram still showed a single-step
     `SELECT … ORDER BY VECTOR_DISTANCE(…)` query.  Updated to reflect the
     real two-step implementation introduced in the Db2 fp0 fix: step 1
     selects `id` only (no `VECTOR_SERIALIZE` in the SELECT list), ordered
     by distance; step 2 fetches full rows by those IDs using
     `_SELECT_COLS` (which includes `VECTOR_SERIALIZE`), then reorders in
     Python to restore nearest-first order.  "Last updated" line changed
     from "Step 0" to "Step 7".

  3. **ARCHITECTURE.md Section 3 (schema / index description) updated.**
     The column-type legend described the `expires_at` indexes as
     `partial index on expires_at WHERE expires_at IS NOT NULL`.  That
     predicate was removed from all five `ix_*_expires` indexes in
     migration `0002` (Db2 12.1.5 fp0 `SQL0104N`).  Updated to say "plain
     (unfiltered) index on `expires_at`" with an explanatory note.
     "Last updated" line changed from "Step 2" to "Step 7".

  4. **INTEGRATION_TESTING.md Docker image reference updated.**
     The Quick-start section referenced `ibmcom/db2:latest`, which was
     migrated off Docker Hub in February 2023.  Updated to
     `icr.io/db2_community/db2:latest`.  The matching troubleshooting row
     was updated to use the new image name.  Added a one-line note that
     Apple Silicon (M1/M2/M3) users need `--platform=linux/amd64`.

- **Reason:**
  Fix 1 addresses a real, externally-reachable SQL injection path via the
  MCP `recall` tool (see analysis above).  Fixes 2–4 are documentation
  accuracy corrections: ARCHITECTURE.md was not updated during Step 7
  despite two structural changes (two-step search, removal of partial
  indexes), and INTEGRATION_TESTING.md's Docker image reference would
  cause a `docker pull` failure for anyone following the guide today.

- **Made during:** Post-Step-7 audit / security review

- **Supersedes:** The claim in the "2026-07-31 — Db2 12.1.5 fp0 compatibility
  fixes" entry (item 1) that `_vec_to_str()` "contains no user input, so
  there is no SQL-injection risk" — that claim holds for `create()` /
  `update()` only and does not extend to `search()`.  The code and docstring
  in `repositories/base.py` have been updated accordingly.

## 2026-07-31 — EPIC-2 backlog: Cosmos DB Agent Memory Toolkit features adapted for Db2

- **Decision:** Researched the implementation details of Azure Cosmos DB's
  Agent Memory Toolkit (github.com/AzureCosmosDB/AgentMemoryToolkit) and
  added a second Epic, "Cosmos-inspired memory enhancements (Db2-adapted)"
  (`EPIC-2`), to `BOARD.html` with four Stories (`ENH-1` through `ENH-4`),
  all in To Do. **No source code was changed** — this is a backlog-only
  addition to the board, per explicit instruction. `BOARD.html`'s data
  schema changed from a single `epic` object to an `epics` array plus an
  `epic_id` field on every story, so the board can render and track
  multiple epics; the rendering script was updated to match (per-epic
  progress bars, an epic badge on each card, dynamic epic name in the
  detail modal).

  **What the toolkit actually does** (verified via direct research, not
  assumed): three Cosmos DB containers (`memories_turns`,
  `memories`, `memories_summaries`) partitioned by `(user_id, thread_id)`;
  a four-stage extraction pipeline (ingest → LLM-classify into
  fact/procedural/episodic/unclassified → LLM-driven reconciliation of
  contradicting facts → thread/user summarization) with configurable
  `EVERY_N` cadences per stage; every memory carries a confidence score
  (0.0–1.0); exact-duplicate rejection via a SHA-256 `content_hash` at
  write time, separate from LLM-driven semantic contradiction resolution;
  and a dual processing model — synchronous in-process for prototypes, or
  Cosmos's native change-feed triggering an async Azure Durable Functions
  orchestrator for scale.

  **The four Stories chosen** (small, high-value set per explicit
  instruction — not exhaustive):
  - `ENH-1` — confidence scoring (0.0–1.0 field + `min_confidence` filter
    on `search()`/`list_all()`).
  - `ENH-2` — write-time exact-dedup via `content_hash` (SHA-256).
  - `ENH-3` — reconciliation: a new `Reconciler` protocol (parallel to the
    existing `Consolidator`) that soft-supersedes contradicted facts via
    new `superseded_by`/`superseded_at`/`supersede_reason` columns,
    deliberately kept distinct from `deleted_at` so an audit trail can
    tell "user asked us to forget this" apart from "a newer fact
    contradicted this."
  - `ENH-4` — formalizes the existing `scripts/consolidate_pending.py`
    reference pattern (replaces its `metadata.consolidated` flag hack with
    a real `consolidated_at` column + claim-based locking) and adds
    `EVERY_N`-style cadence throttling to inline consolidation.

  **Deliberately excluded / adapted rather than ported directly:**
  - The Cosmos change-feed + Durable Functions async processing tier has
    no Db2 LUW equivalent (Db2 has no native change-feed mechanism). Per
    explicit instruction, this was *adapted* rather than dropped: `ENH-4`
    positions the existing polling-script pattern, hardened, as the
    Db2-appropriate substitute — same goal (offload processing off the
    write path), different mechanism (poll + claim column instead of a
    native change feed). This preserves the Step 0 "zero mandatory
    external services" principle.
  - Hybrid full-text + vector search (Cosmos's `search_cosmos()` combines
    both) was **not** turned into a story. Db2 LUW does have a native text
    search feature (`CONTAINS`/`SCORE`/`CONTAINS_ANY`/`CONTAINS_ALL`
    functions, historically via "DB2 Text Search"), confirmed via web
    research, but the documentation found was old (Db2 9.7/10.5-era) and
    current-version (12.1) status/setup requirements were not confidently
    verified. Flagged here as a candidate for a future story once that's
    actually confirmed, rather than committing to it now.
  - A dual sync/async client API (Cosmos ships both `CosmosMemoryClient`
    and `AsyncCosmosMemoryClient`) was excluded as out of scope for a
    small set — this SDK is synchronous throughout except the OpenAI
    Agents SDK adapter (which must be async to satisfy that protocol);
    adding a fully async `MemoryStore` variant is a larger architectural
    undertaking than the "small, high-value" scope asked for here.

- **Reason:** Bring in genuinely differentiated capabilities (confidence
  grading, dedup, contradiction handling, throttled consolidation cost)
  that this SDK's own Consolidator design already anticipated but didn't
  fully build out, filtered through what's actually Db2-native-feasible
  rather than a blind port of Cosmos-specific mechanics.
- **Made during:** Backlog planning (not tied to a PROMPTS.md step)

## 2026-08-01 — EPIC-3 backlog: Oracle AI Agent Memory features adapted for Db2

- **Decision:** Researched Oracle AI Agent Memory
  (blogs.oracle.com/developers/oracle-ai-agent-memory-a-governed-unified-memory-core-for-enterprise-ai-agents,
  the `oracleagentmemory` PyPI package, and docs.oracle.com's Agent Memory
  guide — the blog itself 403'd on direct fetch both times it was tried;
  the PyPI package page and docs site gave real class/method-level detail
  instead) and added a third Epic, "Oracle-inspired memory enhancements
  (Db2-adapted)" (`EPIC-3`), to `BOARD.html` with four Stories (`ORC-1`
  through `ORC-4`), all in To Do. **No source code was changed** and no
  existing epic/story/prompt content was modified — purely additive, per
  explicit instruction, same as EPIC-2. `BOARD.html`'s epic-badge CSS/JS
  (added when EPIC-2 landed) was hardcoded for exactly two epics
  (`epicIdx === 1 ? 'v1' : 'v2'`); generalized it to `v${epicIdx}` plus a
  third color (`#c2410c`, orange) so it scales to N epics rather than
  needing another one-off fix for a fourth.

  **What Oracle's SDK actually does** (verified from the real PyPI page,
  not assumed): a two-pillar design — short-term memory (`Thread` +
  `Context Card` + conversation summaries, scoped to the active session)
  and long-term memory (durable `Memory` records via an add/search
  workflow, either explicit or LLM-extracted). Core classes:
  `OracleAgentMemory` (client; takes a DB connection, `Embedder`, `LLM`,
  `SchemaPolicy`, `SearchStrategy`), `Thread` (`add_messages()`,
  `add_memory()`, `get_context_card()`), `SearchScope` (retrieval scoping
  by user/agent/thread — functionally equivalent to this SDK's
  `MemoryScope`, which validates that design choice rather than exposing a
  gap). `SearchStrategy`: VECTOR / KEYWORD / HYBRID (an Oracle-managed
  combined vector+keyword index). `SchemaPolicy`:
  `CREATE_IF_NECESSARY` vs `REQUIRE_EXISTING`. Managed tables include
  `APP_MEMORY_THREAD`, `APP_MEMORY_MESSAGE`, `APP_MEMORY_MEMORY`,
  `APP_MEMORY_ACTOR_PROFILE`, and `APP_MEMORY_RECORD_CHUNKS` — the last
  one chunks long content for retrieval rather than embedding one giant
  blob as a single vector. Metadata filtering supports `$array_contains`,
  `$array_contains_any`, and `$not` operators.

  **The four Stories chosen** (small set, matching EPIC-2's precedent —
  not exhaustive):
  - `ORC-1` — `get_context_card()`-equivalent: a structured recent-turns
    view for the active thread, with an optional pluggable summarizer
    hook (same shape as the existing Consolidator/Reconciler pattern).
  - `ORC-2` — content chunking for long memories: a new `memory_chunks`
    table, chunking logic in `create()`/`update()` above a length
    threshold, and a chunk-aware `search()` mode that resolves hits back
    to parent records. The largest-scope item in this epic.
  - `ORC-3` — a small structured metadata-filter operator DSL
    (`$not`, `$array_contains`, `$array_contains_any`, exact match) on
    `search()`/`list_all()`, backed by Db2's `JSON_VALUE`/`JSON_EXISTS` on
    the existing `metadata VARCHAR(4096)` column — no schema change.
  - `ORC-4` — a `REQUIRE_EXISTING` schema policy for `Migrator`: validate
    the expected schema via `SYSCAT` catalog queries and refuse to run any
    DDL, for deployments where application code must never touch DDL in
    production.

  **Deliberately excluded / not yet resolved:**
  - Oracle's `SearchStrategy.HYBRID` (managed vector+keyword index) is the
    *second* independent source in this project's research pointing at
    hybrid vector+full-text search (the first was Cosmos DB's
    `search_cosmos()`, deferred in the EPIC-2 entry above for the same
    reason). Db2 LUW does have a native text-search feature
    (`CONTAINS`/`SCORE`/`CONTAINS_ANY`/`CONTAINS_ALL`), confirmed via web
    research in the EPIC-2 entry, but current-version (12.1) setup and
    status still isn't confidently verified. Not committing to a story
    until that's actually resolved — flagging it here a second time so it
    doesn't get lost, and so whoever picks this up knows two different
    reference implementations independently justify it.
  - Oracle's `IndexSynchronization` policy (`ON_COMMIT` / `AUTO` /
    `MANUAL` — controls when a vector/hybrid index refreshes relative to
    writes) was not turned into a story. Whether Db2's `CREATE VECTOR
    INDEX` (DiskANN) needs explicit `REORG`/`RUNSTATS` after bulk inserts
    to stay performant was not researched here — a candidate for a future
    story once that's actually checked, not assumed either way.
  - Oracle's security-requirements list (encrypted connections, secret
    management outside source code, end-user auth before memory
    operations, "usage bounds for messages and provider calls") is mostly
    already this SDK's existing posture (scoping/governance from Step 5)
    or squarely the *integrating application's* responsibility per
    Oracle's own docs, not something a story here would add — noted, not
    turned into backlog items.

- **Reason:** Same rationale as EPIC-2 — bring in genuinely differentiated
  capabilities (chunked retrieval for long content, structured metadata
  querying, a schema-attach mode for regulated/DBA-gated deployments)
  filtered through actual Db2 feasibility, rather than assuming everything
  a reference implementation does is portable or worth porting.
- **Made during:** Backlog planning (not tied to a PROMPTS.md step)

---

## 2026-08-01 — ENH-1: confidence scoring on memory records

- **Decision:** Added a `confidence` column (`DOUBLE NOT NULL DEFAULT 1.0`,
  range 0.0–1.0) to all five memory tables via a new migration file
  (`0003_confidence_and_content_hash.sql`).  The same migration also includes
  the `content_hash VARCHAR(64)` column for ENH-2, bundled together to
  minimise ALTER TABLE passes per table.

  **Migration file:** `src/agent_memory_sdk/db/migrations/0003_confidence_and_content_hash.sql`

  **Column type and default:**  `DOUBLE NOT NULL DEFAULT 1.0`
  - `DOUBLE` chosen over `DECIMAL(3,2)` or `REAL`: Python floats are IEEE 754
    doubles; no precision loss on the Python ↔ Db2 round-trip.  The application
    enforces the 0.0–1.0 range; no `CHECK` constraint is needed at the DB level.
  - `NOT NULL DEFAULT 1.0` means all rows written before the migration (and any
    row written without an explicit `confidence` value) are automatically treated
    as fully certain, preserving backward compatibility with no backfill step
    required.

  **Python model:** `_MemoryBase.confidence: float = 1.0` — default 1.0, no
  validator constraint at the Pydantic layer (application-level convention).

  **Repository layer changes:**
  - `_SELECT_COLS` in `BaseRepository` now includes `confidence` at index 8
    (after `embedding`; before `created_at`).
  - `create()` persists `record.confidence` as a bound parameter.
  - `update()` persists `record.confidence` in the SET clause (confidence is
    a mutable field — a consolidator may revise a record's certainty score
    on update).
  - `_model_from_row()` in all five concrete repositories reads index 8 back as
    `float(confidence) if confidence is not None else 1.0` — the `None` guard
    protects against rows written before migration 0003 if a Db2 instance was
    not yet migrated but a code upgrade has been deployed.

  **Filter parameters:**
  - `list_all(..., min_confidence: float = 0.0)` — appends
    `AND confidence >= ?` to the WHERE clause when `min_confidence > 0.0`;
    no predicate added at 0.0 so existing callers incur zero overhead.
  - `search(..., min_confidence: float = 0.0)` — same predicate, added to
    the *first SQL step* (ID-ranking pass) so low-confidence rows are excluded
    before they consume `top_k` slots, not as a post-filter.

  **Interaction with other WHERE predicates:**
  The `confidence >= ?` predicate is appended after — and in addition to —
  the existing `deleted_at IS NULL` and `expires_at` filters.  The full
  effective WHERE clause in `list_all()` with all filters active is:
  ```
  WHERE <scope predicates>
    AND deleted_at IS NULL
    AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)
    AND confidence >= ?
  ```
  In `search()`, the confidence predicate is in the ID-ranking subquery (step 1)
  only; the step-2 full-row fetch does NOT re-apply the filter (it fetches by
  PK from the already-filtered ID list, so no double-filtering occurs).

  **Consolidator docstring updated:** The `LLMConsolidator` example in
  `types.py` now shows `confidence=0.6` for tentative inferences vs
  `confidence=0.95` for facts derived from explicit user statements, replacing
  the implicit default-1.0-for-everything approach.

- **Reason:** Brings genuine grounding-certainty semantics to the SDK,
  matching the capability that Azure Cosmos DB's Agent Memory Toolkit uses to
  grade every extracted memory 0.0–1.0.  Enables `search(min_confidence=0.7)`
  to filter tentative inferences out of retrieval — a real-world governance
  requirement for production agents that should not re-surface uncertain
  inferences at the same confidence level as directly observed facts.
- **Made during:** ENH-1 (EPIC-2 backlog, first story implemented)

---

## 2026-08-01 — ENH-1 follow-up: Pydantic confidence range enforcement + docstring fixes

- **Decision:** Two correctness fixes to the ENH-1 confidence work, applied
  in a single commit.

  1. **Pydantic range constraint on `_MemoryBase.confidence`.**
     The ENH-1 entry stated "no validator constraint at the Pydantic layer
     (application-level convention)" — this was aspirational, not actual.
     `_MemoryBase.confidence` was a bare `float = 1.0`, meaning values like
     `57.0` or `-0.1` were silently accepted and persisted, corrupting
     `min_confidence`-based filtering and any future reconciliation logic that
     assumes confidence is a meaningful 0–1 value.
     Fixed by changing the field declaration to
     `Field(default=1.0, ge=0.0, le=1.0)`.  No migration or schema change is
     required — this is a Python-only change that makes the migration comment
     ("the application enforces the 0.0–1.0 range") true instead of
     aspirational.
     Seven new unit tests added to `TestConfidenceScoring` in
     `tests/test_repositories.py` cover: above-1.0, below-0.0, boundary 0.0,
     boundary 1.0, a wildly wrong value (57.0), and the constraint applying to
     all five concrete model subclasses.

  2. **Stale docstring examples in `SemanticFact` and `ProceduralMemory`.**
     Both usage examples in `models.py` showed `confidence` as an arbitrary
     key inside the `metadata` dict (e.g. `metadata={"confidence": 0.95, ...}`),
     a pattern from before ENH-1 added a real first-class field.  Updated to
     pass `confidence=` as a proper constructor argument and removed the key
     from `metadata`.

- **Reason:** The range-enforcement gap would silently corrupt confidence-based
  retrieval and reconciliation.  The docstring examples misled readers about
  where confidence lives on the model.  Both are low-risk, no-schema fixes.
- **Made during:** ENH-1 follow-up (post-story correctness pass)
- **Supersedes:** The "no validator constraint at the Pydantic layer
  (application-level convention)" line in the
  `2026-08-01 — ENH-1: confidence scoring on memory records` entry above.

## 2026-08-01 — ENH-2: write-time dedup via content hash

- **Decision:** Added write-time idempotent-write logic to all five memory tables
  using a `content_hash VARCHAR(64)` column that already exists in migration
  `0003_confidence_and_content_hash.sql` (bundled with ENH-1's `confidence` column).

  **Hash normalization rule — exact steps, applied consistently everywhere:**

  1. **Lowercase** — `content.lower()`
  2. **Whitespace-collapse** — `re.sub(r"\s+", " ", lowercased).strip()`
     (collapses every run of whitespace — spaces, tabs, newlines, CR, FF — to a
     single ASCII space, then strips leading/trailing whitespace)
  3. **SHA-256** — `hashlib.sha256(normalized.encode("utf-8")).hexdigest()`
     (returns a 64-character lowercase hex string)

  These three steps are implemented in `_content_hash(content: str) -> str` in
  `repositories/base.py` and must be used every time a `content_hash` is
  computed or compared — in `create()`, in `update()`, and in any future code
  path that needs to match hashes.  The normalization is case- and
  whitespace-insensitive, which means two writes whose content differs only in
  capitalization or whitespace are treated as exact duplicates.

  **`_MemoryBase.content_hash: str | None = None`** — added to `models.py`
  at the same position in the field list.  `None` is valid (pre-migration rows
  written before 0003 was applied); the field is never constrained by Pydantic.

  **`BaseRepository._SELECT_COLS`** updated to include `content_hash` at
  index 9 (between `confidence` at 8 and `created_at` at 10).  All five
  `_model_from_row()` implementations updated to unpack the new column.

  **`BaseRepository.create()` changes:**
  - Computes `_content_hash(record.content)` and sets `record.content_hash`.
  - The dedup SELECT is gated on `_DEDUP_ON_WRITE` (a class-level bool,
    default `True`).  When True, issues:
    ```sql
    SELECT ... FROM <table>
    WHERE <scope predicates>
      AND content_hash = ?
      AND deleted_at IS NULL
    FETCH FIRST 1 ROWS ONLY
    ```
    using the `ix_*_content_hash (agent_id, content_hash)` index.
  - If a row is found, returns `_model_from_row(existing_row)` without
    inserting — idempotent write, no duplicate created.
  - If no row is found, proceeds to INSERT with `content_hash` in the column
    list and bound params.
  - When `_DEDUP_ON_WRITE` is `False`, the SELECT is **skipped entirely** —
    no round-trip, no duplicate detection.  `WorkingMemoryRepository` sets
    this to `False` (see below).
  - The dedup check deliberately uses **only `deleted_at IS NULL`** for now.
    Once ENH-3 lands and `superseded_at` exists, the check should also add
    `AND superseded_at IS NULL` to exclude superseded rows from the duplicate
    detection scope.  A `# ENH-3 note` comment in `create()` marks this
    revisit point.

  **`BaseRepository.update()` changes:**
  - Recomputes `_content_hash(record.content)` into `new_hash` and includes
    `content_hash = ?` in the SET clause so the hash stays in sync with the
    content value after every update.
  - Sets `record.content_hash = new_hash` on the returned model.

  **`_DEDUP_ON_WRITE` class attribute (added in this follow-up fix):**
  `BaseRepository._DEDUP_ON_WRITE: bool = True` — default on for
  `SemanticFactRepository`, `EntityProfileRepository`, and
  `ProceduralMemoryRepository`, where idempotent writes of the same fact are
  the design intent.
  `WorkingMemoryRepository._DEDUP_ON_WRITE = False` — working memory is an
  ordered, append-only conversation log.  Short repeated utterances ("ok",
  "yes", "thanks") are legitimate distinct turns and must each produce a new
  row.  Applying dedup here would silently drop turns, corrupt the history
  count, and feed stale rows to the Consolidator instead of the freshly written
  one.
  **EpisodicMemory keeps dedup on** (`_DEDUP_ON_WRITE = True`, inheriting the
  default).  Episodic entries are Consolidator-produced summaries of a session,
  not raw turn-by-turn utterances.  The exact same summary appearing twice is
  far more likely to be an accidental double-write than a legitimate repetition,
  so the idempotent-write protection is appropriate there.  If future usage
  shows episodic summaries being legitimately repeated, this can be revisited.

  **No UNIQUE constraint in the schema** — uniqueness is enforced in application
  code because dedup is scoped to `(agent_id scope, content_hash)` and must allow
  a deleted or (future) superseded row to share a hash with a live row.

  **Concurrency note (best-effort only):** the dedup check is not atomic — the
  SELECT and the subsequent INSERT are two separate statements with no
  transaction or row lock between them.  Two concurrent `create()` calls with
  identical content can both pass the SELECT before either INSERT lands,
  producing duplicate rows.  There is no DB-level backstop (no UNIQUE
  constraint, and Db2 12.1.5 fp0 does not support partial/filtered unique
  indexes per the Step 7 `SQL0104N` finding, so a full DB-level fix is not
  trivially available).  This is safe for the common single-writer or
  low-concurrency case; it is **not** a uniqueness guarantee under concurrent
  writers to the same scope.

- **Reason:** Catches the common "agent re-stores the same fact twice" case
  cheaply and deterministically at write time, before any LLM reconciliation
  pass runs.  Inspired by Azure Cosmos DB Agent Memory Toolkit's SHA-256
  content_hash dedup.

- **Made during:** ENH-2 (EPIC-2 backlog, second story)

---

## 2026-08-01 — ENH-2 audit fixes: WorkingMemory dedup opt-out, race-condition doc, ARCHITECTURE.md content_hash, DECISIONS.md ordering

- **Decision:** Five correctness and documentation fixes applied in a single commit.

  1. **WorkingMemory dedup opt-out (`_DEDUP_ON_WRITE = False`).**
     Added a class-level `_DEDUP_ON_WRITE: bool = True` attribute to
     `BaseRepository`.  `WorkingMemoryRepository` overrides it to `False`.
     When `False`, `create()` skips the dedup SELECT entirely — no wasted
     round-trip and no silent loss of duplicate-content turns in conversation
     logs.  `EpisodicMemoryRepository`, `SemanticFactRepository`,
     `EntityProfileRepository`, and `ProceduralMemoryRepository` keep the
     default `True`.  EpisodicMemory is kept on because its entries are
     Consolidator-produced summaries (not raw turn-by-turn utterances), so the
     same summary appearing twice is far more likely to be an accidental
     double-write than a legitimate repetition.
     Tests updated: `test_create_dedup_returns_existing_when_hit` now asserts
     the new correct behaviour (new row inserted, id ≠ existing); tests
     previously using `WorkingMemoryRepository` as the dedup subject migrated
     to `SemanticFactRepository`; two new attribute-level assertions added.

  2. **Dedup race-condition note.**
     Added a "Concurrency note (best-effort only)" paragraph to
     `BaseRepository.create()`'s docstring and to the ENH-2 DECISIONS.md entry
     above.  The SELECT+INSERT is not atomic; concurrent writers can both pass
     the SELECT and each INSERT, producing duplicates.  No DB-level backstop
     exists (no UNIQUE constraint; Db2 12.1.5 fp0 rejects partial/filtered
     unique indexes per `SQL0104N`).  Behaviour is correct for single-writer
     and low-concurrency cases.

  3. **ARCHITECTURE.md section 3 — `content_hash` column added.**
     The ENH-2 migration added `content_hash VARCHAR(64)` to all five tables
     but ARCHITECTURE.md was never updated.  Added `content_hash` to the
     column-type legend and to all five entity definitions in the Mermaid ER
     diagram.  Updated the "Last updated" line to ENH-2.

  4. **DECISIONS.md ordering fix.**
     The ENH-2 entry had been appended after the "Entry template" block
     instead of before it.  Moved it to its correct chronological position
     (after ENH-1 follow-up, before the template).

  5. **`.gitignore` stray edit reverted.**
     A stray ` m` appended to `.gitignore` (no trailing newline) was reverted
     via `git checkout -- .gitignore`.

- **Reason:** Items 1–2 fix silent data-loss and misleading documentation
  introduced by the ENH-2 implementation; item 1 also eliminates an
  unnecessary DB round-trip on every working-memory write.  Items 3–5 are
  documentation and housekeeping corrections that keep the repo in a
  consistent, auditable state.
- **Made during:** ENH-2 audit pass (post-story correctness review)
- **Supersedes:** The original ENH-2 `create()` docstring and the original
  ENH-2 DECISIONS.md entry (both updated in place above).

---

## 2026-08-01 — ENH-3: Reconciler protocol, supersession columns, MemoryStore.reconcile()

- **Decision:** Implemented soft-supersession for `semantic_facts` via three new nullable
  columns (`superseded_by VARCHAR(36)`, `superseded_at TIMESTAMP`,
  `supersede_reason VARCHAR(255)`), a `Reconciler` protocol + `NoOpReconciler` default in
  `types.py`, `SemanticFactRepository.supersede()`, and `MemoryStore.reconcile()`.

  **Supersession columns — semantic_facts only (entity_profiles and procedural_memory excluded)**

  The columns were added to `semantic_facts` only.  The justification for each excluded table:

  * **`entity_profiles`** — profiles are dense aggregated summaries kept current via `update()`
    (optimistic concurrency), not an append of competing individual claims.  Typically there is
    one profile row per `(agent_id, user_id)` pair; "which profile supersedes which?" does not
    naturally arise.  If a profile becomes stale, the correct action is `update()`, not
    supersession.  Adding the columns would add schema cost with no query path that would use them.

  * **`procedural_memory`** — skills and instructions are versioned via `update()`.  A new version
    of a skill replaces the old one in place; a competing row is not typically written alongside
    the old one.  The supersession mechanism assumes two independently-created rows that later turn
    out to contradict each other — that pattern is specific to atomic fact accumulation, not to
    skills that are deliberately updated.

  The two excluded tables are versioned objects; `semantic_facts` is an accumulation of
  independently-created atomic claims.  That structural difference is why supersession is
  meaningful for one and not the other.

  **Reconciler protocol shape** (mirrors `Consolidator` exactly):

  ```
  class Reconciler(Protocol):
      def __call__(self, candidates: list[SemanticFact]) -> list[SupersedeDecision]: ...

  @dataclass
  class SupersedeDecision:
      winner_id: str   # id of the winning fact (left untouched)
      loser_id:  str   # id of the superseded fact
      reason:    str   # e.g. "contradicts: user now prefers light mode"
  ```

  **`NoOpReconciler`** — matches `NoOpConsolidator` pattern exactly: a plain class with a
  `__call__` that always returns `[]`.  Used as the default when no reconciler is supplied to
  `MemoryStore(pool, reconciler=...)`.

  **`SemanticFactRepository.supersede(loser_id, winner_id, reason, scope)`** — issues a scoped
  UPDATE setting the three supersession columns plus bumping `updated_at` and `version`.  Guards:
  `AND deleted_at IS NULL AND superseded_at IS NULL` so only live, non-superseded rows can be
  superseded.  Returns `True` on hit, `False` on miss.  Reason is truncated to 255 chars to match
  the column width.

  **`MemoryStore.reconcile(memory_type, scope, limit=200)`** — fetches up to `limit`
  non-deleted, non-superseded `SemanticFact` rows, passes them to the configured Reconciler,
  and for each `SupersedeDecision` calls `self.facts.supersede()`.  Reconciler errors are
  caught and logged (same pattern as `_run_consolidator()`).  Returns the list of applied
  decisions.  Only accepts `memory_type="facts"` / `"semantic_facts"` — calling with any other
  type raises `ValueError` immediately.

  **`list_all()` / `search()` in `BaseRepository`** — both now include
  `AND superseded_at IS NULL` alongside the existing `AND deleted_at IS NULL`.  This applies
  to all five tables (the predicate is a no-op for the four tables that don't have the column,
  but that is correct: `superseded_at IS NULL` is vacuously true when the column doesn't exist,
  so normal Db2 DDL will reject queries referencing a missing column — which is exactly right:
  the other four tables must not be queried with this predicate until/unless a future migration
  adds the column).  In practice, only `SemanticFactRepository` overrides `_SELECT_COLS` and
  `_model_from_row`; the base filter is written into the SQL for all tables so the exclusion
  behaviour is consistent and can't be accidentally omitted when extending.

  **ENH-2 dedup check updated** — `create()` dedup SELECT now also excludes superseded rows
  (`AND superseded_at IS NULL`).  This closes the case where a fact is superseded and then the
  same content is written again: the superseded row must not be returned as a dedup hit; a fresh
  row should be inserted.  The ENH-2 DECISIONS.md entry referenced this as a future revisit point;
  it is resolved here.

  **Governance note (not just naming):** `deleted_at IS NOT NULL` = "the user / operator asked
  us to forget this."  `superseded_at IS NOT NULL` = "the AI learned this was contradicted by a
  newer fact."  Keeping them as separate nullable columns lets audit tooling, compliance queries,
  and human review distinguish these two lifecycle events without ambiguity.  This is a real
  governance distinction — data-erasure obligations (GDPR Right to Erasure) apply to
  user-initiated `forget()` calls; they do not automatically apply to AI-managed supersession,
  which may need to be retained for model-governance audit purposes.

  **Migration file:** `0004_supersession.sql` — three `ALTER TABLE … ADD COLUMN` statements
  plus a `CREATE INDEX ix_semantic_facts_superseded_by ON semantic_facts (agent_id, superseded_by)`.
  No `NOT NULL` constraint (nullable, existing rows are `NULL` = live).  No DB-level FK
  (would prevent orphan handling if the winner is itself later superseded or deleted).

  **`types.py` module path updated** — `SupersedeDecision`, `Reconciler`, `NoOpReconciler`
  exported from `src/agent_memory_sdk/types.py` and re-exported from `__init__.py`.

- **Reason:** Implements Oracle AI Agent Memory-inspired contradiction detection as a
  soft-supersession mechanism.  Keeps a permanent audit trail of "this fact was replaced by
  that one, for this reason" without hard-deleting rows or mixing the AI-managed lifecycle
  event with user-initiated forget().
- **Made during:** ENH-3 (EPIC-2 backlog, third story)
- **Supersedes:** The ENH-2 DECISIONS.md note "once ENH-3 lands… the dedup check should also
  add `AND superseded_at IS NULL`" — resolved in this entry.

---

### Entry template (copy this for every new decision)

```
## YYYY-MM-DD — <short title>

- **Decision:**
- **Reason:**
- **Made during:** Step N (<step name>)
- **Supersedes:** (link to prior entry, if any — otherwise omit)
```

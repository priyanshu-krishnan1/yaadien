# Design decisions — agent-memory-sdk

This is the single source of truth for decisions made on this project. Every
build step (see `PROMPTS.md`) must read this file before starting and append
a dated entry before finishing. Do not silently deviate from an existing
entry — if a later step needs to change one, add a new entry that
explicitly supersedes it and say why.

> **New entries go at the END of the file, after the last dated entry below — this
> template just documents the format; copy it, don't insert next to it.**

### Entry template (copy this for every new decision)

```
## YYYY-MM-DD — <short title>

- **Decision:**
- **Reason:**
- **Made during:** Step N (<step name>)
- **Supersedes:** (link to prior entry, if any — otherwise omit)
```

---

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
  `AND superseded_at IS NULL` alongside the existing `AND deleted_at IS NULL` when
  `_HAS_SUPERSESSION` is True (see below).  **Correction (ENH-3 audit fix, 2026-08-01):**
  the original entry stated that `superseded_at IS NULL` is "vacuously true when the column
  doesn't exist" — that is incorrect.  Referencing a nonexistent column is a compile-time
  error on Db2 (SQLCODE -206), not a vacuous truth.  The original implementation
  unconditionally added this predicate to all five repositories, which would have caused
  every `list_all()` / `search()` / dedup-SELECT call on `working_memory`, `episodic_memory`,
  `entity_profiles`, and `procedural_memory` to fail against a real Db2 instance.  Fixed by
  introducing `_HAS_SUPERSESSION: bool = False` (see ENH-3 audit fix entry below).

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

## 2026-08-01 — ENH-3 audit fix: _HAS_SUPERSESSION gate + reconcile() sanity guards

- **Decision:** Two bugs introduced in ENH-3 are fixed here; the erroneous claim
  in the ENH-3 DECISIONS.md entry is also corrected inline.

  **Bug 1 — superseded_at referenced on tables that don't have it (CRITICAL)**

  `BaseRepository.list_all()`, `search()`, and the `create()` dedup-SELECT all
  unconditionally appended `AND superseded_at IS NULL` to their WHERE clauses after ENH-3
  landed.  Only the `semantic_facts` table has this column (migration 0004 added it there
  only).  Referencing a nonexistent column in a Db2 query is a compile-time SQL error
  (SQLCODE -206), not a vacuous truth as stated in the original ENH-3 entry.  Every
  `list_all()` / `search()` call on `working_memory`, `episodic_memory`,
  `entity_profiles`, and `procedural_memory` — plus the `create()` dedup-SELECT on
  `EpisodicMemory`, `EntityProfile`, and `ProceduralMemory` (which all have
  `_DEDUP_ON_WRITE = True`) — would have failed against a real Db2 instance.

  *Fix:* Added `_HAS_SUPERSESSION: bool = False` to `BaseRepository`, mirroring the
  existing `_DEDUP_ON_WRITE` pattern exactly.  Overridden to `True` only in
  `SemanticFactRepository`.  In `list_all()`, `search()`, and the `create()` dedup-SELECT,
  the `AND superseded_at IS NULL` fragment is now built conditionally:
  `supersession_sql = " AND superseded_at IS NULL" if self._HAS_SUPERSESSION else ""`.
  This is the same conditional-fragment style already used for `min_confidence`'s
  `conf_sql` / `conf_params`.

  *Why not caught earlier:* the unit test suite uses mocked cursors that never
  validate SQL against a real schema.  No integration test coverage was added for
  non-facts `list_all()` / `search()` calls at the time of ENH-3.

  *Regression tests added:*
  - `TestHasSupersessionFlag` in `tests/test_repositories.py` — class-attribute checks,
    plus `list_all()`, `search()`, and `create()` dedup-SELECT assertions for every
    repository (both that non-facts repos emit no `superseded_at` anywhere in their SQL,
    and that `SemanticFactRepository` still emits `superseded_at IS NULL`).
  - `TestNonFactsReposNoSupersessionColumn` in `tests/integration/test_core.py` — live Db2
    integration test (write a row, call `list_all()` / `search()`; a SQLCODE -206 would
    propagate as an exception and fail the test immediately).  Skipped automatically when
    `DB2_DATABASE` is not set; ready to run as soon as a live instance is available.

  **Bug 2 — reconcile() applied SupersedeDecisions with no sanity checks**

  `MemoryStore.reconcile()` passed `decision.winner_id` and `decision.loser_id` directly to
  `supersede()` with no validation.  Because Reconcilers are explicitly LLM-backed, a
  hallucinated or buggy response could:
  - set a fact's `superseded_by` to its own id (self-supersession), or
  - reference a `winner_id` that doesn't exist in the scope or wasn't part of the
    candidate set, silently corrupting the audit trail.

  *Fix:* Added two guards in `reconcile()` before the `supersede()` call:
  (a) **Self-supersession guard** — if `decision.winner_id == decision.loser_id`, the
  decision is skipped and a `logger.warning()` is emitted.
  (b) **Candidate-membership guard** — a set of candidate IDs is built once from the
  list returned by `list_all()` before the loop; if `decision.winner_id` is not in that
  set, the decision is skipped and a `logger.warning()` is emitted.  Both cases are
  treated like the existing "supersede returned False" path — logged, not raised, not
  added to the `applied` list, and do not abort the rest of the batch.

  *Regression tests added:* `TestReconcileSanityGuards` in
  `tests/test_reconciliation.py` — covers self-supersession skipped, no UPDATE SQL
  issued, warning logged, winner-not-in-candidates skipped, no UPDATE SQL issued,
  warning logged, and a mixed-batch test where bad decisions are skipped and a valid
  decision in the same batch is still applied.

- **Reason:** Both bugs would have caused silent data corruption or hard SQL errors in
  production.  Bug 1 is a SQLCODE -206 crash on four of five memory types; Bug 2 is a
  silent audit-trail corruption for any LLM-backed Reconciler that produces malformed
  output (which the shipped example reconciler already has to defend against).
- **Made during:** ENH-3 audit (post-landing review)
- **Supersedes:** The erroneous "vacuously true" claim in the ENH-3 list_all()/search()
  section above (corrected inline); and the lack of reconcile() input validation in the
  ENH-3 implementation.

---

## 2026-08-01 — ENH-4: claim-based consolidation locking, consolidate_every_n cadence, --dedup-every-n worker option

- **Decision:** Three related additions that together make the consolidation pipeline production-ready.

  **1. Migration 0005 — `consolidated_at TIMESTAMP` column**

  Added a nullable `consolidated_at TIMESTAMP` column to `working_memory` and `episodic_memory`
  (migration `0005_consolidated_at.sql`).  This replaces the `metadata.consolidated: false` JSON-flag
  approach that `scripts/consolidate_pending.py`'s own docstring flagged as a stand-in unsuitable for
  production.  The column is NULL by default (not yet consolidated); the background worker sets it to
  the current timestamp when it claims a row for processing.

  A composite index `(agent_id, consolidated_at)` on both tables allows the worker's eligibility scan
  (`WHERE agent_id = ? AND consolidated_at IS NULL`) to use an index range scan rather than a full
  table scan — critical at production row counts.

  The column is only added to `working_memory` and `episodic_memory` because those are the only
  consolidation *inputs*; the other three tables (semantic_facts, entity_profiles, procedural_memory)
  are consolidation *outputs* and the concept does not apply to them.

  The `_HAS_CONSOLIDATED_AT` class attribute gate on `BaseRepository` (mirroring the existing
  `_HAS_SUPERSESSION` pattern from ENH-3) ensures that no SQL referencing this column is ever
  emitted for tables that do not have it — Db2 SQLCODE -206 (column not found) is a hard runtime
  error, not a vacuous truth.

  **2. Claim-based locking in `BaseRepository._claim_consolidated()`**

  The worker claims a row before processing it by issuing::

      UPDATE <table>
      SET consolidated_at = <now>
      WHERE id = ? AND <scope> AND consolidated_at IS NULL

  and checking the rowcount.  Rowcount 1 = this worker owns the row.  Rowcount 0 = another worker
  already claimed it; skip.

  This is best-effort optimistic concurrency, not a hard transaction.  The two UPDATEs from competing
  workers are serialized at Db2's row-level locking layer, so only one will see `consolidated_at IS NULL`
  and get rowcount 1.  Under pathological conditions (many workers, very slow processing, no heartbeat
  reset) a crashed worker could leave a row claimed but unprocessed indefinitely — this is a known
  limitation acceptable for v1 (the operator can reset stuck rows manually with
  `UPDATE ... SET consolidated_at = NULL WHERE id = ?`).

  **3. `MemoryStore.consolidate_every_n` — inline consolidation cadence throttle**

  Added an optional `consolidate_every_n: int = 1` parameter to `MemoryStore.__init__`.  Default is 1
  (fire on every write — existing behaviour, fully backward-compatible).  When set to N > 1, the
  inline synchronous consolidator fires only every Nth `remember()` call for working/episodic writes
  **per scope** (keyed by `(agent_id, user_id, thread_id)`).  This mirrors the Agent Memory Toolkit's
  `FACT_EXTRACTION_EVERY_N` / `THREAD_SUMMARY_EVERY_N` / `DEDUP_EVERY_N` env-var pattern.

  **Known limitation (documented in class docstring):** the per-scope counter is stored in-memory on
  the `MemoryStore` instance.  It resets to zero on process restart and is **not shared across multiple
  application instances** (e.g. multiple gunicorn workers or Kubernetes replicas).  Each process
  maintains its own independent counter: with N workers and `consolidate_every_n=5`, each worker
  fires every 5th write *it handles personally*, not globally every 5th write across all workers.
  This is a real production limitation worth being upfront about — not a hidden gotcha.  For
  cross-process cadence the correct tool is the background worker, not the inline consolidator.

  **4. Worker script — `--dedup-every-n` Reconciler cadence**

  `scripts/consolidate_pending.py` now accepts `--reconciler-module`, `--reconciler-class`, and
  `--dedup-every-n N` arguments.  When configured, the worker invokes the ENH-3 Reconciler
  (`store.reconcile("facts", scope)`) every N completed batches.  This mirrors the Agent Memory
  Toolkit's `DEDUP_EVERY_N` pattern: reconciliation is expensive (an LLM call) and does not need
  to run on every batch; running it periodically amortises the cost.

  **5. This worker is the Db2-appropriate substitute for Cosmos DB's change-feed tier**

  The Cosmos DB Agent Memory Toolkit uses change-feed-triggered Azure Durable Functions to process
  memories off the hot write path asynchronously.  Db2 LUW has no native change-feed mechanism.
  This polling worker (periodic scheduler + a `consolidated_at` claim column) is the
  Db2-appropriate substitute — same underlying goal (async, off-the-hot-path processing), different
  mechanism.  It builds entirely on existing infrastructure (the DB connection pool, MemoryStore,
  the Consolidator and Reconciler protocols) and introduces no new external service dependency —
  keeping the Step 0 "zero mandatory external services" principle intact.

  **Tests added:** `tests/test_enh4.py` — 35 unit tests covering the
  `_HAS_CONSOLIDATED_AT` class-attribute gate on all five repos, `_model_from_row` with the
  new column, `_claim_consolidated()` SQL shape and claim/skip logic, `consolidate_every_n` counter
  per-scope independence, counter reset-after-firing, fast-path bypass for n=1, invalid-n
  ValueError, and worker script `_fetch_pending` SQL + `_process_record` claim-gate behaviour.
  395 total unit tests pass; ruff clean.

- **Reason:** The original `consolidate_pending.py` explicitly documented itself as a stand-in
  unsuitable for production due to lack of locking, idempotency keys, and the fragile JSON-flag
  approach.  ENH-4 addresses all three gaps.  The `consolidate_every_n` throttle closes a real
  cost-efficiency gap on hot write paths (an LLM-backed Consolidator firing on every write is
  prohibitively expensive at production throughput).  The `--dedup-every-n` flag gives operators
  control over Reconciler cost in the worker, consistent with the toolkit's own cadence philosophy.
- **Made during:** ENH-4 (async worker hardening and EVERY_N cadence)
- **Supersedes:** The metadata-flag approach in the original `consolidate_pending.py` docstring
  (which explicitly said a real implementation would use `consolidated_at IS NULL` — this is that
  real implementation).

---

## 2026-08-01 — ENH-4 audit: --dedup-every-n silent no-op fix + ARCHITECTURE.md ENH-4 gaps

- **Decision:** Two related doc/correctness fixes identified in a post-ENH-4 audit.

  **1. `--dedup-every-n` validation (Option B chosen)**

  `scripts/consolidate_pending.py --dedup-every-n N` silently did nothing for
  N >= 3.  The root cause: `batches_completed` starts at 0 on every fresh
  invocation and can only reach a maximum of 2 within a single run (one
  increment per memory type processed — exactly two types: "working" and
  "episodic").  The modulo trigger `batches_completed % N == 0` can therefore
  only ever fire for N = 1 or N = 2 within a single invocation.  Any value of
  N >= 3 is permanently a no-op under normal cron-periodic usage.

  **Option A** (persist a cross-run counter somewhere) was considered.  The
  options were: (a) a new migration adding a counter table to Db2, or (b) a
  local state file.  Option A with a Db2 counter table adds schema complexity
  and a new migration for what is fundamentally a CLI cadence flag; a local
  state file would carry an explicit multi-machine-cron limitation caveat and
  would need a per-agent-scope key.  Neither is proportionate to the fix —
  operators who want a longer cadence can simply schedule a dedicated reconciler
  cron job directly, which is already supported by `store.reconcile()`.

  **Option B was chosen**: reject `--dedup-every-n` values > 2 at
  argument-parsing time with a clear error message explaining the two-type
  constraint.  This turns a silent footgun into an immediate, actionable error
  and does not add schema migrations or stateful files.  The help text and
  module docstring were updated to document the hard limit (1 or 2) and why.

  **Test fix:** `test_dedup_every_n_triggers_reconcile` was an arithmetic
  assertion in a vacuum (`[b % 3 == 0 for b in [1..6]]`) that validated the
  modulo formula but never exercised the real script.  It was replaced with
  three tests that use the real `_fetch_pending` / `_process_record` functions
  and the real trigger condition from `main()`'s batch loop:

  - `test_dedup_every_n_1_triggers_reconcile_after_each_batch` — N=1, expects
    reconcile called twice (once after "working", once after "episodic").
  - `test_dedup_every_n_2_triggers_reconcile_once_after_both_batches` — N=2,
    expects reconcile called exactly once (only after the second batch).
  - `test_dedup_every_n_3_rejected_at_argparse` — N=3, expects the subprocess
    to exit with code 2 and an error message containing "must be 1 or 2".

  **2. ARCHITECTURE.md ENH-4 gaps**

  Section 3 (`Schema`) had not been updated for ENH-4 at all: the prose column-type
  legend had no entry for `consolidated_at`, and the Mermaid ER diagram for both
  `working_memory` and `episodic_memory` was missing the column entirely.  Both
  were updated (section-3 `_Last updated` line bumped to ENH-4; `consolidated_at`
  added to the prose legend and to both table blocks in the ER diagram).

  Section 4 (`remember()` flow) still said "Last updated: Step 4" and showed the
  Consolidator being called directly after the INSERT with no throttle gate.  The
  `_should_consolidate()` / `consolidate_every_n` mechanism introduced in ENH-4
  was missing.  The sequence diagram was updated to show the throttle check as an
  explicit decision node (`alt _should_consolidate returns True / else throttled`)
  between the repository write and the Consolidator call.  The async note was also
  updated from the old metadata-flag stand-in language to the production-grade
  ENH-4 `consolidated_at IS NULL` / claim-based worker description.

- **Reason:** The silent-no-op footgun in `--dedup-every-n` was the most
  dangerous gap: an operator setting `--dedup-every-n 5` would see no error, no
  warning, and no reconciler runs — ever — with no indication anything was wrong.
  The architecture diagram gaps were a documentation correctness issue that would
  mislead anyone reading the current-state design doc.
- **Made during:** ENH-4 audit (post-merge correctness fixes)
- **Supersedes:** Part of the `--dedup-every-n` behavior described in the
  ENH-4 entry above (the N-batch cadence paragraph) — that entry described the
  intent; this entry records that N >= 3 was silently broken and documents the fix.

---

## 2026-08-01 — ORC-1: context cards over working memory + optional summarizer

- **Decision (context-card object shape):** `MemoryStore.get_context_card(scope, max_turns=20)` returns a `ContextCard` dataclass from [`src/agent_memory_sdk/types.py`](../src/agent_memory_sdk/types.py) with the exact fields:
  - `turns: list[WorkingMemory]` — recent working-memory rows in **chronological order** (oldest first)
  - `turn_count: int` — `len(turns)`
  - `latest_at: datetime | None` — timestamp of the most recent turn, or `None` when empty
  - `summary: str | None` — optional condensed narrative, `None` by default
  This is deliberately a formatting/convenience layer over [`WorkingMemoryRepository.list_all()`](../src/agent_memory_sdk/repositories/base.py) rather than a new persistence model: no schema change, no new table, no background worker requirement.

- **Decision (summarizer protocol signature):** Added `Summarizer` as a single-callable protocol parallel to `Consolidator` and `Reconciler` with the exact signature:

      def __call__(self, turns: list[WorkingMemory]) -> str: ...

  `MemoryStore.__init__(..., summarizer=None)` accepts any callable matching that shape. The shipped default is `NoOpSummarizer`, which returns `""`; [`MemoryStore.get_context_card()`](../src/agent_memory_sdk/store.py) interprets that default/no-op result as `summary=None`, so callers who do nothing get the raw-turns view with zero mandatory LLM cost.

- **Decision (ordering + failure semantics):** `get_context_card()` fetches via `store.working.list_all(scope, limit=max_turns)` (which is newest-first), reverses the returned slice to chronological order, computes `latest_at` from the newest fetched row, and then optionally calls the summarizer on the chronological list. Summarizer exceptions are logged and swallowed; the method still returns the raw card with `summary=None`.

- **ARCHITECTURE.md section 1:** Updated to include a `ContextCard / get_context_card()` box and `Summarizer` box in the core flowchart because ORC-1 adds a new first-class read-path capability on `MemoryStore`; this was substantial enough to warrant explicit representation rather than only a note here.

- **Made during:** ORC-1 (EPIC-3)

## 2026-08-01 — ORC-2: content chunking for long memories

- **Decision (chunking threshold):** Content exceeding **2000 characters** (``CHUNK_THRESHOLD = 2000``) is split into overlapping chunks at write time.  Content at or below the threshold is stored exactly as before — a single embedding on the parent row, no chunk rows created — so all pre-ORC-2 behaviour is preserved for the typical short-to-medium content case.  2000 chars was chosen as a safe upper bound for text that fits comfortably in a single embedding context window (most embedding models have a token limit of 512–8192; 2000 English characters ≈ 400–500 tokens, well within the safe zone for any provider the SDK might be used with).  The threshold is configurable at ``MemoryStore`` construction time via ``chunk_threshold=``.

- **Decision (chunk size and overlap strategy):** Chunks are fixed-size character windows of **800 characters** (``CHUNK_SIZE``) with **200 characters of overlap** (``CHUNK_OVERLAP``) between adjacent chunks (step = 600 chars).  The overlap strategy is a simple sliding window — ``_split_chunks(text, chunk_size, chunk_overlap)`` in ``repositories/base.py``.  Character-level splitting (not token-level) was chosen because: (a) the SDK is embedding-provider-agnostic and token boundaries differ per provider, and (b) character windows are reproducible and testable without a tokenizer dependency.  The 800/200 defaults give ≈25% overlap, which is a common practical default; both values are configurable at ``MemoryStore`` construction time via ``chunk_size=`` / ``chunk_overlap=``.  ``chunk_overlap`` must be strictly less than ``chunk_size`` (enforced with a ``ValueError``).

- **Decision (shared table vs. per-type tables):** A single shared **``memory_chunks``** table was chosen over five per-type ``*_chunks`` tables.  Justification:
  - All five memory types use the same ``VECTOR(1536, FLOAT32)/COSINE`` shape; there is no type-specific column that would justify five tables.
  - One ``CREATE VECTOR INDEX`` services all chunk queries instead of five.
  - DDL and migration surface is smaller (one table + three indexes, migration 0006, one repository class).
  - The chunk-search → parent-resolution query path is cleanest with all chunk types together: the resolver groups by ``source_table`` and can fetch all parents of each type in a single ``IN (...)`` round-trip per table.
  - A ``source_table VARCHAR(64)`` discriminator (e.g. ``"working_memory"``) is sufficient to route results back to the correct parent table.
  - Per-type tables would only be preferable if memory types needed different vector dimensions or distance metrics; they do not.

- **Decision (parent row embedding when chunked):** When a record is chunked, its own ``embedding`` column on the parent table is set to the **zero-vector sentinel** (the same sentinel used everywhere else in the SDK for "not yet embedded").  This satisfies the ``NOT NULL`` constraint while making it clear that this row's semantic representation lives in ``memory_chunks``, not in the parent column.  The parent's embedding is therefore intentionally useless for vector search on chunked records; callers must use ``search_chunks=True`` to reach those records semantically.

- **Decision (chunk-to-parent resolution and dedup logic — ``search(search_chunks=True)``):** Chunk-based search is a three-step pattern, reusing the two-step ID-rank → full-row-fetch pattern already established for the standard ``search()`` in Step 7 (Db2 12.1.5 fp0 compatibility workaround):
  1. **Chunk search** — ``ChunkRepository.search_chunks()`` ranks ``memory_chunks`` by ``VECTOR_DISTANCE`` (filtered to ``source_table = <this table>`` and scope), over-fetching ``top_k × 4`` rows to compensate for multiple chunks per parent.
  2. **Dedup** — collect unique ``source_id`` values, keeping the **minimum distance** seen across all chunks for that parent as the parent's representative distance.
  3. **Re-rank + resolve** — sort parents by best-chunk distance (ascending), take top ``top_k``, then fetch the full parent rows via ``IN (...)`` with the standard scope + ``deleted_at IS NULL`` + confidence predicates.  This reuses the existing reorder-after-fetch pattern so that ``VECTOR_SERIALIZE`` and ``VECTOR_DISTANCE`` are never in the same SQL statement (Db2 12.1.5 fp0 limitation).
  The over-fetch factor of 4× is a conservative heuristic.  With ``CHUNK_SIZE = 800`` and ``CHUNK_THRESHOLD = 2000``, a maximally long 64KB CLOB yields ≈106 chunks; at ``top_k = 10``, over-fetching 40 chunk rows is sufficient to cover at least 10 distinct parents in all realistic cases.

- **Decision (backward compatibility):** When no ``embedding_provider`` is passed to ``MemoryStore``, chunking is silently skipped on every write — ``_chunk_repo`` is ``None`` on all repositories and the code paths taken are identical to pre-ORC-2.  The ``search(search_chunks=True)`` flag also silently falls back to the standard path when ``_chunk_repo is None``.  Callers who construct repository objects directly (without ``MemoryStore``) are also unaffected: ``BaseRepository.__init__`` accepts ``chunk_repo=None`` as the default.

- **Decision (chunk_repo as a class-level injectable vs. constructor argument):** The ``ChunkRepository`` is injected into each per-type repository as a constructor argument rather than a class attribute or global singleton.  This keeps the pattern consistent with the existing pool injection pattern and avoids any shared-state issues between test cases.

- **Made during:** ORC-2 (EPIC-3)

## 2026-07-30 — Process/tracking docs moved out of repo root into project-management/

- **Decision:** All process- and tracking-related files that are not part of
  the shipped package — `ARCHITECTURE.md`, `BOARD.html`, `Chats.md`,
  `DECISIONS.md` (this file), `INTEGRATION_TESTING.md`, `PROMPTS.md`,
  `ai-agent-platform-competitive-analysis.md`, every `audit-prompt*.md`, and
  `beta-readiness-audit-prompt.md` — were moved (via `git mv`, history
  preserved) from the repo root into a new `project-management/` folder.
  `README.md`, `pyproject.toml`, `.env.example`, `.gitignore`, and the
  `src/`/`tests/`/`scripts/` trees stay at the repo root unchanged.
  `pyproject.toml`'s `readme = "README.md"` still resolves correctly since
  `README.md` did not move. Cross-references between the moved files that
  use bare filenames (e.g. "read DECISIONS.md") still work as-is now that
  they're read as siblings within `project-management/`; the 3 markdown
  links in this file pointing into `src/` were updated to `../src/...`;
  source-code comments in `store.py`, `repositories/base.py`, two migration
  files, `scripts/consolidate_pending.py`, and `tests/integration/*.py`
  that referenced `DECISIONS.md`/`INTEGRATION_TESTING.md` by bare name were
  updated to `project-management/<file>`; `PROMPTS.md`'s Step 0 (pasted
  fresh into every new session) and its working-agreement/tracking prose
  were updated to state the new location explicitly, since Step 0 runs
  with the agent's cwd at repo root, not inside this folder.
  `project-management/README.md` was added as an index, and it notes that
  historical entries in this file and in `audit-prompt-2.md` through
  `audit-prompt-10.md` (all already executed, dated before this entry) may
  still reference bare filenames from when those files lived at repo root —
  read those as `project-management/<file>` today. Their content was left
  otherwise unedited since rewriting completed historical instructions
  serves no future purpose.
- **Reason:** The repo root had accumulated 17 non-shipped markdown/HTML
  process files (build prompts, decision log, local Kanban board, audit
  remediation prompts, a market study) alongside the actual package files,
  making the root read as project-management clutter rather than a Python
  SDK about to ship worldwide as a public beta. None of these files were
  ever part of the wheel build (`[tool.hatch.build.targets.wheel]` only
  packages `src/agent_memory_sdk`), so this is a repo-hygiene/discoverability
  fix, not a packaging fix — but it matters more now that the project is
  headed toward a public release where first impressions of the repo root
  matter.
- **Made during:** Step 0 setup (repo-hygiene pass, not tied to any single
  build step).

## 2026-08-02 — Fix: chunked content silently unreachable + mypy strict errors

- **Decision (search_chunks auto-detect):** Changed
  [`BaseRepository.search()`](../src/agent_memory_sdk/repositories/base.py)'s
  `search_chunks` parameter from `bool = False` to `bool | None = None`.
  When `None` (the new default), the method auto-detects: chunk-aware search
  is used if and only if `self._chunk_repo is not None` (i.e. chunking is
  actually active for this store instance); otherwise the standard
  parent-embedding path is taken, identical to pre-fix behaviour.
  Explicit `True` or `False` still override the auto-detection, so callers
  who need to force one path or the other are unaffected.

- **Reason (Issue 1 — silent unreachability):** With the pre-fix default of
  `False`, any record stored as a chunked record (content > `chunk_threshold`
  when an `embedding_provider` is configured — the normal setup, since
  chunking requires a provider) had its parent-row embedding replaced with a
  zero-vector sentinel at write time (documented behaviour, ORC-2 entry above).
  That sentinel is intentionally useless for vector search.  The only way to
  find such a record semantically was `search(search_chunks=True)`, but none
  of the three adapters (`langchain.py`, `openai_agents.py`, `mcp_server.py`),
  nor any other caller, was ever updated to pass it — meaning any content
  exceeding ~2000 characters stored by a normally-configured store was silently
  unreachable through every existing search path.  The auto-detect default
  closes this gap for all current and future callers with a single change,
  rather than requiring each adapter to be individually patched and every
  future caller to remember the opt-in.

- **Decision (mypy fix a — chunks.py import):**
  [`repositories/chunks.py`](../src/agent_memory_sdk/repositories/chunks.py)
  was importing `DistanceMetric` and `SearchMode` from
  `agent_memory_sdk.repositories.base` (a re-export).  mypy strict flagged
  this as an unexported re-import.  Fixed by importing both directly from
  their canonical home, `agent_memory_sdk.types`.

- **Decision (mypy fix b — store.py splatted dict):**
  [`store.py`](../src/agent_memory_sdk/store.py) was building a plain dict
  `chunk_kwargs = dict(chunk_repo=..., chunk_threshold=..., chunk_size=...,
  chunk_overlap=...)` and forwarding it via `**chunk_kwargs` to all five
  repository constructors.  The dict's inferred value type is
  `int | ChunkRepository | None`, which mypy strict cannot verify against
  each repository's individually-typed `__init__` parameters, producing 10
  errors of the form `Argument "chunk_repo" to "WorkingMemoryRepository" has
  incompatible type "int | ChunkRepository | None"; expected ...`.
  Fixed by passing the four arguments as explicit keyword arguments to each
  of the five constructor calls — consistent with every other constructor
  call in this codebase and what mypy can actually check.

- **Validation:** After both fixes, `mypy src` reports zero errors (was 12).
  `ruff check .` reports no issues.  `pytest` passes 450 tests (77 skipped —
  integration tests require a live Db2 instance).  New test class
  `TestSearchChunksAutoDetect` in `tests/test_orc2.py` covers all three
  `search_chunks` states: `None` with no chunk_repo (standard path, unchanged),
  `None` with chunk_repo set (chunk path fires automatically), and explicit
  `True`/`False` overriding auto-detection in both directions.

- **Made during:** audit-prompt-11 bug-fix pass.

## 2026-08-02 — ORC-3: structured metadata filter operators for search()/list_all()

- **Decision:** Added a `metadata_filter: dict[str, Any] | None = None` parameter
  to [`BaseRepository.list_all()`](../src/agent_memory_sdk/repositories/base.py) and
  [`BaseRepository.search()`](../src/agent_memory_sdk/repositories/base.py), translating
  the filter dict into `JSON_VALUE` / `JSON_EXISTS` predicates appended to the existing
  WHERE clause.  No schema change — the `metadata VARCHAR(4096)` JSON column already exists
  from Step 2 and natively supports these Db2 12.1 functions.

  **Implemented operator set (deliberately small — four operators total):**

  | Operator | Example filter dict | Generated SQL |
  |---|---|---|
  | Exact match | `{"source": "support"}` | `JSON_VALUE(metadata, '$.source') = ?`  param: `"support"` |
  | `$not` | `{"status": {"$not": "archived"}}` | `JSON_VALUE(metadata, '$.status') <> ?`  param: `"archived"` |
  | `$array_contains` | `{"tags": {"$array_contains": "urgent"}}` | `JSON_EXISTS(metadata, '$.tags[*]?(@ == "urgent")') = 'true'` (value inlined; see security note) |
  | `$array_contains_any` | `{"tags": {"$array_contains_any": ["a","b"]}}` | `(JSON_EXISTS(metadata, '$.tags[*]?(@ == "a")') = 'true' OR JSON_EXISTS(metadata, '$.tags[*]?(@ == "b")') = 'true')` (values inlined) |

  Multiple fields in a single dict are combined with AND.  All predicates are appended
  **after** the existing scope, `deleted_at IS NULL`, TTL, confidence, and supersession
  predicates — they are additive, never subtractive.

  **WHERE clause order in `list_all()` (all filters active):**
  ```sql
  WHERE <scope predicates>
    AND deleted_at IS NULL
    AND superseded_at IS NULL          -- SemanticFactRepository only
    AND (expires_at IS NULL OR …)      -- when include_expired=False
    AND confidence >= ?                -- when min_confidence > 0.0
    AND JSON_VALUE(…) = ?              -- metadata_filter predicates
    …
  ```

  In `search()`, the metadata predicates are in the **first SQL step** (ID-ranking pass,
  which selects only `id` and orders by `VECTOR_DISTANCE`) so rows excluded by the filter
  do not consume `top_k` slots.  `_search_via_chunks()` (ORC-2 chunk-search path) applies
  the predicates in the step-3 parent-row resolve query.

  **Implementation files:**
  - [`src/agent_memory_sdk/repositories/base.py`](../src/agent_memory_sdk/repositories/base.py):
    `_build_metadata_filter(filter)` — pure function returning `(sql_fragment, params)`;
    `_escape_json_path_value(val)` — helper for inlining values in `JSON_EXISTS` path expressions.
  - [`src/agent_memory_sdk/exceptions.py`](../src/agent_memory_sdk/exceptions.py):
    `InvalidMetadataFilterError(ValueError)` — raised on unrecognized operators or invalid field names.
  - [`src/agent_memory_sdk/__init__.py`](../src/agent_memory_sdk/__init__.py):
    `InvalidMetadataFilterError` added to exports and `__all__`.
  - [`tests/test_orc3.py`](../tests/test_orc3.py): 52 new unit tests.

  **Security design:**

  * **Exact match / `$not`** use bound `?` parameters — the driver handles quoting
    safely; no value interpolation into SQL text.
  * **`$array_contains` / `$array_contains_any`** inline values into a Db2 JSON path
    expression string.  Db2 12.1.5 fp0 does not support binding values into path
    expressions via `?` (same constraint documented for vector literals in the
    "Db2 12.1.5 fp0 compatibility fixes" entry).  Values are escaped by
    `_escape_json_path_value()` before interpolation:
    - Strings: backslash doubled (`\\` → `\\\\`), double-quote escaped (`"` → `\"`),
      single-quote doubled (`'` → `''`).
    - Integers/floats: formatted as bare numerics — inherently safe.
    - Booleans: `true` / `false` (JSON literals, not quoted).
    - None: `null`.
  * **Field names** are validated against `^[A-Za-z_][A-Za-z0-9_.]*$` before
    interpolation.  Any field name failing this pattern raises `InvalidMetadataFilterError`
    immediately, before any SQL is built.

  **Rejection of unrecognized operators:**
  Any key inside a value dict that starts with `$` and is not in the known set
  (`$not`, `$array_contains`, `$array_contains_any`) raises `InvalidMetadataFilterError`
  with a message that lists the supported operators.  This fires before any SQL is
  executed, so there is no "silent ignore" path.

  **Backward compatibility:**
  Both `list_all()` and `search()` default `metadata_filter=None`, which produces an empty
  SQL fragment and empty params list — zero overhead for all existing callers.  The
  `_build_metadata_filter` function returns `("", [])` for `None` and for `{}`.

  **New tests:** `tests/test_orc3.py` — 52 unit tests covering:
  - `_build_metadata_filter`: no-op on None/`{}`, exact match (str/int/bool/None), `$not`,
    `$array_contains`, `$array_contains_any`, combined multi-field dicts.
  - Error cases: invalid field name, unrecognized `$` operator, non-`$` key in operator dict,
    empty list for `$array_contains_any`, unsupported operand type.
  - `_escape_json_path_value`: strings, numbers, booleans, None, SQL-injection attempts.
  - `list_all()` integration: predicate in SQL, param in params, combined with `min_confidence`,
    offset-pagination path.
  - `search()` integration: predicate in step-1 SQL, combined with `min_confidence`.
  - `InvalidMetadataFilterError` raised before SQL execution; exported from top-level package.

  **Total test suite: 502 tests.  ruff clean.  mypy strict clean.**

- **Reason:** Closes the metadata-filter gap identified in the ORC-3 story.  Inspired by
  Oracle AI Agent Memory's `metadata_filter` on `memory.search()`, adapted for Db2's
  `JSON_VALUE`/`JSON_EXISTS` functions on the existing `metadata VARCHAR(4096)` column.
  No schema change, no migration, no new infrastructure.  The operator set is deliberately
  kept at four because each operator corresponds to a distinct and commonly needed query
  pattern; additional operators can be added following the same pattern if future use cases
  require them.

- **Made during:** ORC-3 (EPIC-3 — structured metadata filters)

## 2026-08-02 — ORC-3 audit: bool case-mismatch fix, ARCHITECTURE.md metadata-filter gap, .DS_Store cleanup

- **Decision:**

  Three fixes applied in a single audit pass:

  **1. Bool exact-match / $not case mismatch (`repositories/base.py`)**

  `_build_metadata_filter()` used `str(operand)` for bool values in the
  exact-match and `$not` branches, producing `"True"` / `"False"` (Python's
  capitalized form).  Db2's `JSON_VALUE` extracting a JSON boolean returns
  lowercase `"true"` / `"false"` per standard JSON/SQL convention, so the
  bound parameter never matched the extracted value — a silent zero-row bug.

  The `$array_contains` / `$array_contains_any` path already handled this
  correctly via `_escape_json_path_value()`, which special-cases `bool` to
  return lowercase `"true"` / `"false"`.  The fix applies the same logic to
  the exact-match and `$not` branches:

  - Added a `bool`-before-`int/float` type-dispatch guard in the exact-match
    branch (Python's `bool` is a subclass of `int`; checking `isinstance(x,
    bool)` after `isinstance(x, int)` would fall into the int branch silently).
  - The `$not` branch now also applies the same bool→lowercase mapping.

  Test `test_bool_field` in `tests/test_orc3.py` previously asserted
  `params == ["True"]` — enshrining the bug.  Fixed to assert `["true"]`.
  Added:
  - `test_bool_false_field` — exact-match `False → "false"`.
  - `test_not_bool_true` / `test_not_bool_false` — `$not` bool correctness.
  - `test_bool_exact_and_array_contains_consistent` /
    `test_bool_false_exact_and_array_contains_consistent` — cross-path
    consistency: both the exact-match bound param and the `$array_contains`
    inlined value produce the same lowercase JSON boolean format for the same
    input.

  **2. ARCHITECTURE.md — metadata_filter entirely missing (ORC-3)**

  `ARCHITECTURE.md` had no mention of `metadata_filter` despite ORC-3 adding
  it to `search()` and `list_all()`.  This is the fourth instance of this
  gap across the story series (Step 7, ENH-2, ENH-4, now ORC-3) — the
  standing working agreement is to treat updating ARCHITECTURE.md as a
  checklist item, verified explicitly before marking a story Done.

  Updated:
  - Section 1 "Last updated" line: now reflects ORC-3.
  - Module paths header: now "as of ORC-3".
  - `exceptions.py` entry: now lists `InvalidMetadataFilterError` alongside
    `StaleWriteError`.
  - `repositories/base.py` entry: now lists `_build_metadata_filter()` and
    `_escape_json_path_value()`.
  - Section 5 (`recall()`) "Last updated" line and step-1 mermaid note: now
    documents that `metadata_filter` predicates are applied in step 1
    (before distance ranking), so filtered-out rows do not consume `top_k`
    slots.
  - **New section 6** ("Metadata filter — `search()` / `list_all()` (ORC-3)"):
    documents the parameter signature, all four supported operators with
    example filter dicts and generated SQL, implementation details
    (`_build_metadata_filter`, `_escape_json_path_value`,
    `InvalidMetadataFilterError`), and WHERE clause position.

  **3. `.DS_Store` cleanup**

  A macOS Finder metadata file (`.DS_Store`) was committed in the ORC-3
  commit.  Fixed by:
  - Adding `.DS_Store` to `.gitignore` (with a comment).
  - Running `git rm --cached .DS_Store` to stop tracking the file while
    leaving it on disk.

- **Reason:** Three regressions/gaps identified in an audit of ORC-3.  The
  bool case mismatch would have caused silent zero-result queries against real
  Db2 whenever a metadata field held a JSON boolean.  The ARCHITECTURE.md gap
  is a recurring pattern now explicitly addressed as a pre-Done checklist item.
  The `.DS_Store` commit is hygiene.

- **Validation:** `pytest` — **507 passed, 77 skipped** (6 new tests added
  over the previous 501-test baseline).  `ruff check .` — clean.
  `mypy src` — **clean (no issues found in 20 source files)**.

- **Made during:** ORC-3 audit pass

## 2026-08-02 — ORC-4: schema attach mode (REQUIRE_EXISTING policy)

- **Decision:**

  Added a `SchemaPolicy` enum to `db/migrate.py` with two values:

  | Value | Behaviour |
  |---|---|
  | `CREATE_IF_NECESSARY` | Default — apply pending migrations, creating tables and indexes. |
  | `REQUIRE_EXISTING`    | Read-only catalog validation; raise `SchemaPolicyError` on any missing object; never execute DDL. |

  Wired as `Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)`.
  Default is `CREATE_IF_NECESSARY` — all existing call sites are unaffected.
  `Migrator.validate()` is also callable directly for startup guard-rails.
  `SchemaPolicyError` added to `exceptions.py` and exported from the package root.

  **Exact SYSCAT queries used by `validate()`**

  All three queries filter on `TABSCHEMA = UPPER(CURRENT SCHEMA)` to restrict
  results to the application user's schema and exclude Db2 system objects.

  1. **Table presence — `SYSCAT.TABLES`**
     ```sql
     SELECT UPPER(TABNAME) FROM SYSCAT.TABLES
      WHERE TABSCHEMA = UPPER(CURRENT SCHEMA) AND TYPE = 'T'
     ```
     `TYPE = 'T'` excludes views, aliases, and nicknames.

  2. **Column presence — `SYSCAT.COLUMNS`**
     ```sql
     SELECT UPPER(TABNAME), UPPER(COLNAME) FROM SYSCAT.COLUMNS
      WHERE TABSCHEMA = UPPER(CURRENT SCHEMA)
        AND UPPER(TABNAME) IN (?, ?, …)
     ```
     Only tables confirmed present in step 1 are included in the `IN`-list,
     so a missing table does not generate a flood of spurious column-missing
     messages.

  3. **Index presence — `SYSCAT.INDEXES`**
     ```sql
     SELECT UPPER(TABNAME), UPPER(INDNAME) FROM SYSCAT.INDEXES
      WHERE TABSCHEMA = UPPER(CURRENT SCHEMA)
        AND UPPER(TABNAME) IN (?, ?, …)
     ```
     Same IN-list guard as step 2.  Primary-key system indexes are excluded
     from `_REQUIRED_INDEXES` because Db2 creates them implicitly; only
     application-named `ix_*` indexes are in the manifest.

  **Error-message format** — `SchemaPolicyError` message:
  ```
  REQUIRE_EXISTING validation failed: N object(s) are missing from the
  database schema. Create them before starting the application:

    table: MEMORY_CHUNKS
    column: WORKING_MEMORY.CONSOLIDATED_AT
    index: IX_SEMANTIC_FACTS_EMBEDDING on SEMANTIC_FACTS

  Run the standard migration runner (SchemaPolicy.CREATE_IF_NECESSARY) or
  apply the DDL manually using the .sql files in
  src/agent_memory_sdk/db/migrations/.
  ```
  Tables first, then columns (sorted by name within each table), then indexes
  (sorted by name within each table).  All names UPPER CASE to match Db2
  catalog conventions.  Single pass covers all three categories so the DBA
  can provision everything at once.

  **Schema manifest** — `_REQUIRED_TABLES`, `_REQUIRED_COLUMNS`,
  `_REQUIRED_INDEXES` are inline constants in `db/migrate.py` derived from all
  six migration files (0001–0006).  Co-located with the runner for
  auditability; must be updated whenever a new migration adds schema objects.

- **Validation:** `pytest` — **517 passed** (10 new `TestSchemaPolicy` tests
  added over the previous 507-test baseline).  Tests use a `_SyscatPool`
  SQLite fake with a `_RewritingCursor` that intercepts SYSCAT query strings
  and redirects them to in-memory stub tables — fully hermetic, no live Db2.
  `ruff check .` — clean (1 auto-fixed import ordering in `__init__.py`).
  `mypy src` — **clean (no issues found in 20 source files)**.

- **Made during:** ORC-4 implementation

}


## 2026-08-02 — VER-1: Verified STEP-1 (Scaffold)

- **Decision:** VER-1 verification PASS — no gaps, fixes, or open items found.
- **Checked:**
  - `pyproject.toml`: hatchling build backend (zero-config src-layout, PEP 517/660); `ibm_db>=3.2.3` listed once (ibm_db_dbi is bundled inside ibm_db — no separate dep needed, matching DECISIONS.md Step 1 entry); `pydantic>=2.0`; dev extras include pytest, ruff, mypy; `[tool.hatch.build.targets.wheel] packages = ["src/agent_memory_sdk"]` correct; mypy strict mode set; ruff target-version py310.
  - `db/connection.py`: `_build_conn_str()` reads DB2_DATABASE/HOSTNAME/UID/PWD (required, raises OSError on missing) and DB2_PORT/DB2_SECURITY (optional with defaults); builds ODBC keyword-pair string only (no JDBC URL); `ConnectionPool` uses `queue.Queue[ibm_db.IBM_DBConnection]` bounded by pool size; `get_connection()` is a `@contextlib.contextmanager` that checks out a raw ibm_db handle, wraps it in `ibm_db_dbi.Connection`, yields, then calls `rollback()` and `put_nowait()` on exit; Windows DLL guard calls `os.add_dll_directory(IBM_DB_WIN_DLL_DIR)` before `import ibm_db` when set.
  - `scripts/check_connection.py`: opens pool with `pool_size=1`, runs `SELECT 1 FROM SYSIBM.SYSDUMMY1`, returns exit code 0 on success.
  - `.env.example`: documents all DB2_* variables with examples.
  - Security: `_build_conn_str()` reads credentials from env vars only; the ODBC string is not user-controllable; no injection surface.
  - Tests: 10 unit tests in `tests/test_connection.py` using ibm_db mock; all pass.
  - 517 unit tests pass, ruff clean, mypy strict clean.
- **Found:** Nothing to fix.
- **Made during:** VER-1 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-2: Verified STEP-2 (Schema & migrations)

- **Decision:** VER-2 verification PASS — no gaps, fixes, or open items found.
- **Checked:**
  - `0002_memory_tables.sql`: all 5 tables present (`working_memory`, `episodic_memory`, `semantic_facts`, `entity_profiles`, `procedural_memory`). Columns: `id VARCHAR(36)`, scope cols `VARCHAR(128)` with `agent_id NOT NULL`, `content CLOB(65536) NOT NULL`, `metadata VARCHAR(4096) NOT NULL DEFAULT '{}'`, `embedding VECTOR(1536, FLOAT32) NOT NULL` (no DEFAULT clause — correct; VECTOR columns cannot have a DEFAULT expression other than NULL per IBM Db2 12.1 docs), `created_at`/`updated_at TIMESTAMP NOT NULL DEFAULT CURRENT TIMESTAMP`, `expires_at TIMESTAMP` (nullable), `version INTEGER NOT NULL DEFAULT 1`, `deleted_at TIMESTAMP` (nullable), each with a `PRIMARY KEY` constraint.
  - `CREATE VECTOR INDEX ix_<table>_embedding ON <table> (embedding) WITH DISTANCE COSINE` on all 5 tables. COSINE uniform across all — correct for L2-normalized text embeddings per IBM docs.
  - Composite scope index `(agent_id, tenant_id, user_id, thread_id)`, agent-only index `(agent_id)`, and plain (unfiltered) expires_at index per table. Comment in the SQL explicitly documents removal of the partial `WHERE expires_at IS NOT NULL` predicate due to `SQL0104N` on Db2 12.1.5 fp0.
  - `db/migrate.py`: `Migrator` reads `MIGRATIONS_DIR/*.sql` sorted lexicographically; tracks applied versions in `schema_migrations`; uses idempotent `CREATE TABLE IF NOT EXISTS` for the tracking table; splits SQL files on semicolons; applies each statement in sequence; inserts version record only after all statements succeed.
  - Schema matches ARCHITECTURE.md section 3 ER diagram.
  - Security: no user-controlled SQL; all DDL is static files.
- **Found:** Nothing to fix.
- **Made during:** VER-2 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-3: Verified STEP-3 (Core models & repositories)

- **Decision:** VER-3 verification PASS — no gaps, fixes, or open items found.
- **Checked:**
  - `models.py`: 5 Pydantic v2 models (`WorkingMemory`, `EpisodicMemory`, `SemanticFact`, `EntityProfile`, `ProceduralMemory`) all inherit `_MemoryBase` which maps 1-to-1 with DDL columns. `MemoryScope` is a frozen Pydantic model (`model_config = {"frozen": True}`) with `agent_id: str` (required), `tenant_id/user_id/thread_id: str | None = None`. `_new_uuid()` default factory used for `id`.
  - `repositories/base.py`: `_require_agent_id(scope)` raises `ValueError` when `agent_id` is falsy. `_scope_predicates(scope)` always produces `agent_id = ?`; adds `tenant_id/user_id/thread_id` only when non-None. SQL scope enforcement confirmed on all 7 read/write paths (create, get_by_id, list_all, search, forget, update, purge_expired). `create()` stamps scope fields from the `scope` arg (overrides record fields).
  - Vector SQL: uses `CAST('{vec_str}' AS VECTOR({dim},FLOAT32))` inline literal for INSERT/UPDATE/search (Db2 12.1.5 fp0 binding workaround). `VECTOR_SERIALIZE(embedding) AS embedding` in SELECT. `_vec_to_str()` coerces via `float()` — SQL injection guard.
  - `EmbeddingProvider`: `@runtime_checkable` Protocol in `types.py`.
  - `MemoryStore` in `store.py`: composes all 5 repos as `.working/.episodic/.facts/.profiles/.procedures`; propagates `embedding_dim` to all repos.
  - Pagination: offset=0 uses `FETCH FIRST n ROWS ONLY`; offset>0 uses `ROW_NUMBER() OVER (ORDER BY created_at DESC)` subquery.
  - 517 unit tests pass, ruff clean, mypy strict clean.
- **Found:** Nothing to fix.
- **Made during:** VER-3 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-4: Verified STEP-4 (Lifecycle — TTL, versioning, forget, consolidation)

- **Decision:** VER-4 verification PASS — no gaps, fixes, or open items found.
- **Checked:**
  - `BaseRepository.forget(id, scope)`: issues `UPDATE SET deleted_at = ?, updated_at = ?, version = version + 1 WHERE id = ? AND scope AND deleted_at IS NULL`; returns `bool(rowcount > 0)`. `soft_delete()` is a backwards-compatible alias delegating to `forget()`. ✓
  - `BaseRepository.purge_expired(scope)`: hard-`DELETE FROM <table> WHERE deleted_at IS NOT NULL AND <scope>`. **No `expires_at` predicate** — only tombstoned rows are deleted. TTL-expired but non-tombstoned rows are left alone. This matches the DECISIONS.md Step 4 entry exactly. ✓
  - `BaseRepository.update(record, scope)`: `UPDATE ... SET content=?, metadata=?, embedding=..., confidence=?, content_hash=?, updated_at=?, version=? WHERE id=? AND scope AND version=record.version AND deleted_at IS NULL`; if `rowcount==0` raises `StaleWriteError`. Version incremented atomically in the same SQL statement. ✓
  - `Consolidator` and `NoOpConsolidator` in `types.py`. `MemoryStore.remember()` dispatches by `_MODEL_TO_REPO_ATTR`, calls `repo.create(record, scope)`, then runs `_run_consolidator([stored], scope)` only for `working`/`episodic` writes, guarded by `_should_consolidate(scope)`. Consolidator errors are caught and logged via `logger.exception`, never raised. ✓
  - `_should_consolidate(scope)`: in-memory counter keyed by `(agent_id, user_id, thread_id)`; fires every Nth write per scope (default n=1, always True). ✓
  - `MemoryStore.forget()` and `purge_expired()` facade methods delegate to the correct repo. `StaleWriteError` re-exported from `store.py` and `__init__.py`. ✓
  - `scripts/purge_expired.py` and `scripts/consolidate_pending.py` exist. ✓
  - 517 unit tests pass, ruff clean, mypy strict clean.
- **Found:** Nothing to fix.
- **Made during:** VER-4 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-5: Verified STEP-5 (Governance / scoping enforcement) — extra scrutiny

- **Decision:** VER-5 verification PASS — extra scrutiny applied (this story never had a dedicated post-hoc audit pass in the BOARD/audit history). No gaps or vulnerabilities found.
- **Checked (re-derived from source code, not from prior audit notes):**
  - `_scope_predicates(scope)` in `repositories/base.py`: always produces `agent_id = ?` with bound param `scope.agent_id`; adds `tenant_id = ?`, `user_id = ?`, `thread_id = ?` only when the corresponding field is non-None. All values are bound `?` parameters — never string-interpolated into SQL. ✓
  - `_require_agent_id(scope)`: raises `ValueError` if `not scope.agent_id` (catches empty string). Guards all 7 SQL paths. ✓
  - **create()** (base.py lines 709–815): calls `_require_agent_id(scope)` then overwrites `record.agent_id = scope.agent_id`, `record.tenant_id = scope.tenant_id`, etc. (lines 712-715) before INSERT — the scope arg wins regardless of what fields the record was constructed with. All scope values are bound `?` params in the INSERT. ✓
  - **get_by_id()**: `WHERE id = ? AND {scope_sql} AND deleted_at IS NULL` — both the ID and the scope are bound params; a row in a different scope returns `None` even if the caller knows its UUID. ✓
  - **list_all()**: `WHERE {scope_sql} AND deleted_at IS NULL [AND ...]` — scope always present. ✓
  - **search()** (both standard and chunk path): step-1 ID-ranking SQL includes scope predicates; step-2 full-row fetch by ID still includes `AND deleted_at IS NULL` (no scope re-check needed because step 1 already filtered by scope, and `deleted_at IS NULL` is also rechecked). ✓
  - **forget()**: `WHERE id = ? AND {scope_sql} AND deleted_at IS NULL`. ✓
  - **update()**: `WHERE id = ? AND {scope_sql} AND version = ? AND deleted_at IS NULL`. Cross-scope update produces rowcount=0 → `StaleWriteError` — information leakage analysis: the caller cannot distinguish "row not found", "wrong scope", or "stale version" from this error, which is intentional (see DECISIONS.md Step 5 entry). ✓
  - **purge_expired()**: `DELETE WHERE deleted_at IS NOT NULL AND {scope_sql}`. Cross-scope purge affects 0 rows silently. ✓
  - **Vector SQL injection surface**: `_vec_to_str(embedding)` coerces every element via `str(float(f))` before interpolating into `CAST('{vec_str}' AS VECTOR({dim},FLOAT32))`. Any non-numeric element raises `ValueError/TypeError` before reaching SQL. The `query_embedding` parameter to `search()` is an unenforced type hint — this coercion is the actual security guard on that path. ✓
  - **Field name validation** (ORC-3 additions to this file): `_build_metadata_filter()` validates field names against `^[A-Za-z_][A-Za-z0-9_.]*$` before interpolation. ✓
  - `test_scoping.py`: 91 unit tests covering `MemoryScope` value object (frozen, hashable, equality), `_scope_predicates()` helper, 5 repo types × 6 operations = 30 parametrized cross-scope isolation tests (returning `None`/`[]` with the correct scope binding), `MemoryStore` facade scope propagation, and empty `agent_id` rejection. All use mocked cursors that verify SQL structure and bound params. ✓
- **Found:** Nothing to fix. The isolation boundary is correctly enforced by bound SQL parameters on all 7 SQL paths. No path allows scope bypass.
- **Made during:** VER-5 (EPIC-4 beta readiness verification — extra scrutiny pass)



## 2026-08-02 — VER-6: Verified STEP-6 (Framework adapters)

- **Decision:** VER-6 verification PASS — all three framework adapters meet STEP-6 acceptance criteria; no gaps or fixes required.
- **Checked:**
  - **LangChain adapter** (`adapters/langchain.py`): `Db2ChatMessageHistory` implements the `BaseChatMessageHistory` interface via duck-typing (`messages` property, `add_message()`, `add_messages()`, `clear()`), backed by `store.working`. `Db2MemoryStore` implements `BaseStore[str, str]` duck-typing (`mget`, `mset`, `mdelete`, `yield_keys`) backed by `store.facts` or `store.profiles` depending on namespace. Both have deferred `_require_langchain()` guard — the SDK core is importable without `langchain-core`. ✓
  - **OpenAI Agents SDK adapter** (`adapters/openai_agents.py`): `Db2Session` implements the four required `Session` protocol methods all `async`: `add_items()`, `get_items()`, `pop_item()`, `clear_session()`. `session_id` maps to `MemoryScope.thread_id` as specified. `get_items(limit=N)` correctly returns the N most-recent messages in chronological order (list_all returns newest-first, reversed to chronological, then sliced from the tail). `recall_episodes()` is an extension (not part of the protocol) that uses `store.episodic.search()` across all sessions for the agent+user. Deferred `_require_openai_agents()` guard. ✓
  - **MCP adapter** (`adapters/mcp_server.py`): `create_server()` returns an `mcp.server.Server` with `list_tools()` (remember, recall, forget, list_memories) and `call_tool()` dispatcher. All four tools handle scoping (agent_id, user_id, thread_id, tenant_id) and reject unknown memory_type values gracefully. `recall` gracefully degrades to `list_all` when `query_embedding` is omitted. Deferred `_require_mcp()` guard. CLI entry point `_main()` present. ✓
  - **Optional extras in `pyproject.toml`**: `[langchain]` → `langchain-core>=0.2`; `[openai-agents]` → `openai-agents>=0.0.10`; `[mcp]` → `mcp>=1.0`; convenience `[all]` wraps all three. ✓
  - **Tests:** 53 unit tests in `tests/test_adapters.py` — all pass. Full integration test suite in `tests/integration/test_adapters_integration.py` covering real Db2 round-trips for all three adapters. `ruff check` — clean. `mypy src` — clean (4 adapter source files). ✓
- **Found:** Nothing to fix.
- **Made during:** VER-6 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-7: Verified STEP-7 (Integration tests)

- **Decision:** VER-7 verification PASS — integration test suite meets STEP-7 acceptance criteria; no gaps or fixes required.
- **Checked:**
  - **Skip mechanism:** `pytestmark = pytest.mark.integration` in each test module; `pytest_collection_modifyitems` hook in `tests/integration/conftest.py` adds `skip` marker to all items in `tests/integration/` when `DB2_DATABASE` env var is absent. Confirmed: `pytest tests/integration/` → 77 skipped, 0 failed. `integration` marker is registered in `pyproject.toml` `[tool.pytest.ini_options].markers`. ✓
  - **Fixture isolation:** `unique_agent_id()` fixture returns a fresh `uuid.uuid4()`-based string for each test function; all `scope`/`thread_scope` fixtures are built on top. This prevents inter-test pollution with no teardown needed. ✓
  - **test_migration.py:** Migrator idempotency (second run → `[]`), `schema_migrations` tracking for all 5 version strings, `Migrator.status()` reports all as `applied`, all 5 tables exist and accept `SELECT COUNT(*)`, all expected columns present (including `CONFIDENCE`, `CONTENT_HASH` from migration 0003), `EMBEDDING` is `NOT NULL VECTOR`, vector index `IX_<TABLE>_EMBEDDING` present in SYSCAT for each table. ✓
  - **test_core.py:** CRUD round-trips for all 5 memory types, vector search nearest-neighbour correctness using unit vectors (deterministic cosine similarity), scope isolation (list_all/get_by_id/search/thread_id), `forget()`/tombstone visibility, `purge_expired()` hard-delete plus scope-safe isolation, TTL (`expires_at` in past excluded, future included, `NULL` always included), optimistic concurrency (`StaleWriteError` on stale version), custom consolidator derives `SemanticFact`, `_HAS_SUPERSESSION` regression tests verifying working/episodic/profiles/procedural repos don't emit `superseded_at IS NULL`. ✓
  - **test_adapters_integration.py:** `Db2ChatMessageHistory` (add/retrieve/clear/batch/type preservation round-trip), `Db2MemoryStore` (mset/mget/mdelete/yield_keys/prefix), `Db2Session` (add_items/get_items/limit/clear_session/pop_item/recall_episodes), MCP tool functions (remember/recall with embedding/forget/list/fallback-to-list). ✓
  - **INTEGRATION_TESTING.md:** Exists at `project-management/INTEGRATION_TESTING.md`. Documents Docker setup, IBM Cloud Db2 alternative, env vars, install with extras, run commands, skip behaviour table, cleanup SQL. ✓
- **Found:** Nothing to fix.
- **Made during:** VER-7 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-8: Verified ENH-1 (Confidence scoring)

- **Decision:** VER-8 verification PASS — confidence scoring meets ENH-1 acceptance criteria; no gaps or fixes required.
- **Checked:**
  - **Model field:** `_MemoryBase.confidence: float = Field(default=1.0, ge=0.0, le=1.0)` in `models.py`. Pydantic v2 enforces range `[0.0, 1.0]` at construction time; invalid values (< 0, > 1.0) raise `ValidationError` immediately. All five memory type subclasses inherit this. ✓
  - **Migration 0003:** `confidence DOUBLE NOT NULL DEFAULT 1.0` added to all 5 tables (`working_memory`, `episodic_memory`, `semantic_facts`, `entity_profiles`, `procedural_memory`). DOUBLE chosen over DECIMAL to match Python float (IEEE 754 double) for exact round-trip. NOT NULL with DEFAULT 1.0 avoids backfill — pre-migration rows automatically have the full-certainty default. ✓
  - **`create()` persistence:** confidence is at position 8 in `_SELECT_COLS` and appears in the INSERT VALUES list as a bound `?` parameter (`record.confidence`). ✓
  - **`update()` persistence:** `SET confidence = ?` in the UPDATE SQL, bound to `record.confidence`. ✓
  - **`_model_from_row()`:** `float(row[8]) if row[8] is not None else 1.0` — pre-migration rows returning SQL NULL for confidence map to 1.0 (verified in `WorkingMemoryRepository._model_from_row()` line 90; same pattern in all 5 repo `_model_from_row()` implementations). ✓
  - **`list_all(min_confidence=...)`:** when `> 0.0` appends `AND confidence >= ?` with bound param to WHERE clause; when `0.0` (default) no predicate is added (backward compatible). Predicate also present in the ROW_NUMBER pagination branch. ✓
  - **`search(min_confidence=...)`:** same guard — predicate applied in the first-pass ID-ranking SQL so low-confidence rows don't consume top_k slots. ✓
  - **Tests:** `TestConfidenceScoring` in `tests/test_repositories.py` — 17 tests covering default value, custom value, persistence in INSERT/UPDATE params, read-back from row, NULL handling, `min_confidence` predicate presence/absence in `list_all`/`search`, ROW_NUMBER path, Pydantic range enforcement (values `> 1.0`, `< 0`, `== 1.0`, `== 0.0`) for all 5 model subtypes. All 17 pass. ✓
- **Found:** Nothing to fix.
- **Made during:** VER-8 (EPIC-4 beta readiness verification)


## 2026-08-02 — VER-9: Verified ENH-2 (Content hash dedup)

- **Decision:** VER-9 verification PASS — write-time dedup meets ENH-2 acceptance criteria; no gaps or fixes required.
- **Checked:**
  - **`_content_hash()` normalization** (in `repositories/base.py`): step 1 lowercase (`content.lower()`), step 2 whitespace-collapse (`re.sub(r"\s+", " ", ...).strip()`), step 3 SHA-256 hex (`hashlib.sha256(...).hexdigest()`). Applied in this exact order, consistently at all sites: `create()` and `update()`. ✓
  - **`content_hash` column:** `VARCHAR(64)` nullable in migration 0003 (NULL for pre-migration rows; always populated on rows written after the migration). Supporting index `ix_<table>_content_hash ON <table> (agent_id, content_hash)` created for all 5 tables. ✓
  - **`_DEDUP_ON_WRITE` gate:** `False` for `WorkingMemoryRepository` (append-only conversation log — repeated short utterances like "ok" must produce distinct rows; also removes the wasted SELECT round-trip). `True` for the remaining 4 repositories (SemanticFact, EntityProfile, ProceduralMemory, EpisodicMemory). ✓
  - **Dedup SELECT:** `WHERE <scope predicates> AND content_hash = ? AND deleted_at IS NULL [AND superseded_at IS NULL]` — `superseded_at IS NULL` added only when `_HAS_SUPERSESSION=True` (semantic_facts). Matching row returned immediately — no new INSERT. ✓
  - **ENH-3 revisit note:** dedup check correctly gates on `_HAS_SUPERSESSION` so superseded facts don't block fresh writes of the same content. The ENH-3-era "revisit" comment in the spec is already addressed in the implementation. ✓
  - **`update()` path:** `_content_hash(record.content)` recomputed and stored as `new_hash`; SET in the UPDATE SQL as a bound `?` param; also updated on the in-memory model via `record.content_hash = new_hash`. ✓
  - **Concurrency caveat:** documented in `create()` docstring: dedup check is not atomic (SELECT + INSERT not in a transaction; no UNIQUE constraint — DECISIONS.md ENH-2 entry explains the reasoning). Acceptable for single-writer / low-concurrency case. ✓
  - **Tests:** `TestContentHash` (14 tests) + `TestHasSupersessionFlag` (2 tests) in `tests/test_repositories.py`. Covers: normalization equivalence, dedup hit returns existing row, WorkingMemory skips dedup SELECT, SemanticFact issues dedup SELECT, content_hash in INSERT/UPDATE params, content_hash read-back from row, NULL for pre-migration rows, update recomputes hash. All 17 pass. ✓
- **Found:** Nothing to fix.
- **Made during:** VER-9 (EPIC-4 beta readiness verification)


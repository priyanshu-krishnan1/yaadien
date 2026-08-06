# Agent Memory SDK — API Interface Specification

**Document Version:** 1.0  
**Scope:** EPIC-9, SDD-3  
**Last Updated:** 2026-08-15

---

## Introduction

This document is a **formal, binding interface contract** for the agent-memory-sdk public API. Every method signature, precondition, postcondition, and exception listed here is derived directly from the source code and must be respected by any implementation change or extension.

This is a reference document, not a tutorial. It contains no usage examples beyond method signatures and error conditions.

---

## Part 1: MemoryScope — Hierarchical Isolation Contract

### MemoryScope Fields

| Field | Type | Required? | Constraints |
|-------|------|-----------|------------|
| `tenant_id` | `str \| None` | No | Broadest scope level; `None` = unscoped default |
| `agent_id` | `str` | **Yes** | Required on every store/repo call; immutable default (see preconditions) |
| `user_id` | `str \| None` | No | Narrows scope to a specific user within an agent |
| `thread_id` | `str \| None` | No | Narrowest scope level; typically a conversation identifier |

### Hierarchy & Scoping Rules

**Hierarchy (broadest → narrowest):**
```
tenant_id > agent_id > user_id > thread_id
```

**Global precondition for all MemoryStore and BaseRepository calls:**
- `scope.agent_id` must be non-empty
  - Raised if missing: `ValueError("MemoryScope.agent_id is required on every repository call.")`
  - This is the immutable isolation boundary (VER-5)

**Narrowing semantics:**
- Every SQL predicate is built from provided scope fields only
- `tenant_id=None` in a scope means "match rows with no tenant"
- `user_id=None` means "match rows where user_id is NULL" (inclusive to all users in that agent)
- `thread_id=None` means "match rows where thread_id is NULL" (inclusive to all threads in that user/agent scope)

**Scope immutability on write:**
- `BaseRepository.create()` and `update()` overwrite a record's scope columns (`tenant_id`, `agent_id`, `user_id`, `thread_id`) with the *target* scope's values before persistence
- This ensures every row is self-contained with its scope denormalized into columns

---

## Part 2: MemoryStore Method Contract

### Signature Table

All methods require `scope.agent_id` to be set (see Part 1).

| Method | Signature | Preconditions | Postconditions | Exceptions Raised | Idempotent? |
|--------|-----------|---------------|-----------------|-------------------|------------|
| **remember** | `remember(record: _MemoryBase, scope: MemoryScope) → _MemoryBase` | record type must be one of: WorkingMemory, EpisodicMemory, SemanticFact, EntityProfile, ProceduralMemory | Writes record to appropriate repository; if ADD/UPDATE decision made, returns persisted row with `id`, `created_at`, `updated_at`, `version` set; calls configured consolidator on working/episodic ADD; for DELETE/NOOP decisions returns unpersisted input | `TypeError` (unknown record type); `ValueError` (missing agent_id) | No (unconditional write) |
| **forget** | `forget(record_id: str, memory_type: str, scope: MemoryScope) → bool` | memory_type must be one of: "working", "episodic", "facts" (or "semantic_facts"), "profiles" (or "entity_profiles"), "procedures" (or "procedural") | Sets `deleted_at` on matching row; returns True iff row found, False otherwise | `ValueError` (unrecognized memory_type or missing agent_id) | Yes (idempotent soft-delete) |
| **purge_expired** | `purge_expired(scope: MemoryScope) → dict[str, int]` | none (beyond agent_id) | Hard-deletes all rows matching scope where `deleted_at IS NOT NULL` across all five tables; returns dict mapping table name → count | `ValueError` (missing agent_id) | Yes (no-op if nothing to delete) |
| **erase_all** | `erase_all(scope: MemoryScope) → ErasureReport` | none (beyond agent_id) | Hard-deletes **every** row matching scope across all six tables (five memory tables + memory_chunks), regardless of `deleted_at`/`expires_at` state; returns audit record with per-table counts, total, timestamp; logs at INFO level | `ValueError` (missing agent_id) | No (irreversible action) |
| **export_scope** | `export_scope(scope: MemoryScope) → Iterator[dict[str, Any]]` | none (beyond agent_id) | Yields one JSON-serializable dict per live (non-deleted, non-superseded) row across all six tables for scope, in table order, with `"_type"` discriminator; includes TTL-expired rows; pagination internally (500 rows/batch) | `ValueError` (missing agent_id, raised lazily on first iteration) | N/A (generator) |
| **import_scope** | `import_scope(records: Iterable[dict[str, Any]], scope: MemoryScope) → dict[str, int]` | records must carry `"_type"` field; all records from single source agent_id; tenant_id/user_id/thread_id must match target scope exactly (agent_id allowed to differ for migration) | Re-inserts records via per-type `create()` or `insert_chunk()` at target scope; returns dict mapping table name → count; calls ordinary dedup logic (ENH-2) | `ValueError` ("_type" missing/unrecognized); `ScopeMismatchError` (scope mismatch on any dimension except agent_id for mixed-source stream) | No |
| **reconcile** | `reconcile(memory_type: str, scope: MemoryScope, limit: int = 200) → list[SupersedeDecision]` | memory_type must be "facts" or "semantic_facts" (only semantic_facts supports supersession); limit capped at 1000 | Fetches up to limit live facts, invokes configured reconciler, applies returned decisions via `supersede()`, logs per-decision; returns applied decisions only | `ValueError` (unsupported memory_type or missing agent_id) | No (decisions applied, state changes) |
| **get_context_card** | `get_context_card(scope: MemoryScope, max_turns: int = 20, query: str \| None = None, include_long_term: bool = False, min_results_by_type: dict[str, int] \| None = None, long_term_top_k: int = 5) → ContextCard` | max_turns ≥ 1; long_term_top_k ≥ 1; if include_long_term=True and query is set, embedding_provider must be configured; min_results_by_type keys must be valid type names and values ≥ 0 | Fetches up to max_turns working-memory rows in chronological order (oldest-first); calls summarizer if configured; if query+include_long_term, embeds query and backfills facts/profiles to per-type minimums; returns ContextCard | `ValueError` (invalid arguments, missing agent_id, missing embedding_provider when needed) | Yes (read-only, deterministic) |
| **search** | `search(query: str, scope: MemoryScope, record_types: list[str] \| None = None, max_results: int = 10, metadata_filter: dict[str, Any] \| None = None, exact_agent_match: bool = True, exact_thread_match: bool = True) → list[SearchResult]` | query must be non-empty; embedding_provider must be configured; record_types names must be valid if provided | Embeds query, fans out to per-type repositories, applies post-fetch exact-match filters on agent_id/thread_id as configured, returns up to max_results SearchResult objects | `ValueError` (empty query, no embedding_provider, unrecognized record_type, missing agent_id) | Yes (read-only, deterministic for same query/scope) |
| **add_messages** | `add_messages(messages: list[dict[str, Any]], scope: MemoryScope, extract_memories: bool = True) → list[str]` | each dict must have "content" key (string); optional "id", "metadata" keys; other keys absorbed into metadata | Writes each as WorkingMemory via `remember()`, returns list of persisted IDs; if extract_memories=True and real extractor configured, invokes it and persists derived records | `ValueError` (missing agent_id) | No (unconditional append) |
| **get_summary** | `get_summary(scope: MemoryScope, except_last: int = 0, token_budget: int \| None = None) → Summary` | except_last ≥ 0; token_budget ≥ 0 if provided | Fetches all working-memory in chronological order, formats each as "{role (-): content}", drops last except_last, truncates to token_budget (whitespace-split), returns Summary with content/message_count/truncated | `ValueError` (invalid arguments, missing agent_id) | Yes (read-only, deterministic) |

---

### Core Method Preconditions (All Methods)

1. **agent_id required:** Every call must carry `scope.agent_id` non-empty
   - Failure: `ValueError("MemoryScope.agent_id is required on every repository call.")`

2. **Consolidator error handling:** When :meth:`remember` triggers consolidation (working/episodic ADD, throttle permits), consolidator exceptions are logged but **never propagated**
   - Consolidation failure does not roll back the original write
   - Derived memories are skipped; the base write succeeds

3. **Ingest resolver error handling (PIPE-2):** When :meth:`remember` invokes IngestResolver:
   - Similarity search exceptions are logged; empty similar list used
   - Resolver exceptions are logged; fallback to ADD decision
   - Never propagates; write always proceeds with fallback

4. **Reconciler error handling:** When :meth:`reconcile` invokes Reconciler:
   - Exceptions are logged
   - Returns empty applied list; no decisions applied

5. **Summarizer error handling (get_context_card):** When configured summarizer raises:
   - Exception is logged
   - ContextCard.summary set to None; card still returned

6. **Memory extractor error handling (add_messages):** When extract_memories=True and MemoryExtractor configured:
   - Exceptions are logged
   - Derived extraction skipped; original message write succeeds

---

## Part 3: BaseRepository Shared Contract

All five per-type repositories (working, episodic, facts, profiles, procedures) implement this interface.

### BaseRepository Method Contract

| Method | Signature | Preconditions | Postconditions | Exceptions Raised |
|--------|-----------|---------------|-----------------|-------------------|
| **create** | `create(record: M, scope: MemoryScope) → M` | record.content non-empty; scope.agent_id required | Inserts row with server-assigned `id` (UUID4), `created_at`, `updated_at`, `version=1`; computes `content_hash` for dedup check (ENH-2); returns persisted record; if _DEDUP_ON_WRITE=True and matching (agent_id, content_hash) exists, returns that row instead (no new insert); on long content (>chunk_threshold) and embedding_provider set, writes chunks to memory_chunks and stores zero-vector sentinel on parent row | `ValueError` (missing agent_id) |
| **get_by_id** | `get_by_id(id: str, scope: MemoryScope) → M \| None` | scope.agent_id required | Returns matching row or None; scope predicates enforce isolation; returns None if row deleted, superseded (facts only), or outside scope | `ValueError` (missing agent_id) |
| **update** | `update(record: M, scope: MemoryScope) → M` | scope.agent_id required; record must have been fetched with matching scope | Updates row conditionally on `version=record.version` (optimistic concurrency); increments version; sets new `updated_at`; replaces content/metadata/embedding/confidence; re-computes content_hash; on chunking-gated records, rewrites chunks; returns updated row with new version/timestamp | `StaleWriteError` (another writer changed row between get/update); `ValueError` (missing agent_id) |
| **forget** | `forget(id: str, scope: MemoryScope) → bool` | scope.agent_id required | Sets `deleted_at=now()` on matching row (soft-delete); returns True if row found and updated, False if not found or already deleted; excludes row from all future searches/lists | `ValueError` (missing agent_id) |
| **purge_expired** | `purge_expired(scope: MemoryScope) → int` | scope.agent_id required; must be called explicitly, never automatic | Hard-deletes all rows matching scope where `deleted_at IS NOT NULL`; returns count deleted; does NOT touch TTL-expired rows (expires_at in past but deleted_at NULL) — caller must forget() those first | `ValueError` (missing agent_id) |
| **search** | `search(query_embedding: list[float], scope: MemoryScope, top_k: int = 5, distance_metric: DistanceMetric = DistanceMetric.COSINE, search_mode: SearchMode = SearchMode.DEFAULT, metadata_filter: dict[str, Any] \| None = None) → list[M]` | query_embedding must be non-empty list of floats; matches configured EMBEDDING_DIM; scope.agent_id required; top_k ≥ 1 | Executes `VECTOR_DISTANCE(embedding, query_embedding, '<metric>')` query; applies scope predicates; applies metadata filter (ORC-3); returns up to top_k rows ordered by ascending distance (most-similar first); uses APPROX mode if search_mode=APPROX and RUNSTATS has been run, otherwise EXACT; excludes deleted rows, superseded rows (facts only) | `ValueError` (query_embedding empty or wrong dimension, missing agent_id) |
| **list_all** | `list_all(scope: MemoryScope, limit: int \| None = None, offset: int = 0, include_expired: bool = False) → list[M]` | scope.agent_id required | Returns non-deleted rows (and non-superseded for facts) in reverse-chronological order (newest-first) by created_at; excludes TTL-expired rows unless include_expired=True; applies limit/offset for pagination; default limit=1000 | `ValueError` (missing agent_id) |
| **get_by_ids** | `get_by_ids(ids: list[str], scope: MemoryScope) → dict[str, M]` | scope.agent_id required | Returns dict mapping id → record for all matching rows; omits not-found or out-of-scope ids | `ValueError` (missing agent_id) |
| **search_chunks** | `search_chunks(query_embedding: list[float], scope: MemoryScope, source_table: str, top_k: int = 5) → list[tuple[dict, float]]` | query_embedding matches EMBEDDING_DIM; scope.agent_id required; source_table must be a valid memory table name | Returns up to top_k chunk dicts with metadata from memory_chunks table, paired with cosine distance; ORC-2 chunking API; returns [] if no chunks match or chunking disabled | `ValueError` (invalid source_table or missing agent_id) |

### Additional BaseRepository Method: SemanticFactRepository.supersede()

| Method | Signature | Preconditions | Postconditions | Exceptions Raised |
|--------|-----------|---------------|-----------------|-------------------|
| **supersede** | `supersede(loser_id: str, winner_id: str, reason: str, scope: MemoryScope) → bool` | scope.agent_id required; both loser_id and winner_id must exist in scope | Sets `superseded_by=winner_id`, `superseded_at=now()`, `supersede_reason=reason` on loser row; returns True if row found and updated, False if not found/wrong scope/already superseded; loser excluded from future reads | `ValueError` (missing agent_id) |

---

### BaseRepository Error Handling

| Exception | When Raised | Handling |
|-----------|------------|----------|
| **StaleWriteError** | Optimistic concurrency conflict on `update()` | Caller must retry: re-fetch row, apply changes, retry update (exponential backoff recommended) |
| **InvalidMetadataFilterError** | Unrecognized metadata_filter operator or invalid field name | Caller must fix filter dict; only `$not`, `$array_contains`, `$array_contains_any` operators recognized; field names must match `^[A-Za-z_][A-Za-z0-9_.]*$` |
| **ValueError** | Missing agent_id (all methods); invalid query_embedding (search) | Precondition violation; caller must fix |

---

## Part 4: Extension Interface Contract

These six protocols define injectable plugins to :class:`MemoryStore` at construction time.

### Protocol Contract Table

| Protocol | Callable Shape | When Invoked | Error Handling | NoOp Default Shipped |
|----------|---|---|---|---|
| **Consolidator** | `(raw_memories: list[_MemoryBase]) → list[_MemoryBase]` | After every `remember()` call on working/episodic memory that successfully writes a new row (ADD decision) | Exceptions logged; never propagated; original write succeeds; derived records skipped on error | `NoOpConsolidator` (always returns `[]`) |
| **Reconciler** | `(candidates: list[SemanticFact]) → list[SupersedeDecision]` | By explicit call to `store.reconcile()` only — never automatic | Exceptions logged; never propagated; returns empty decision list on error; no state changes applied | `NoOpReconciler` (always returns `[]`) |
| **IngestResolver** | `(candidate: _MemoryBase, similar: list[tuple[_MemoryBase, float]]) → IngestDecision` | During `remember()` before any write, when configured; skipped entirely if default NoOp resolver in use (zero overhead) | Exceptions logged; fallback to ADD decision; write always proceeds | `NoOpIngestResolver` (always returns `ADD` decision; search optimization skips similarity lookup) |
| **MemoryExtractor** | `(messages: list[WorkingMemory], scope: MemoryScope) → list[_MemoryBase]` | In `add_messages()` after all message writes complete, when extract_memories=True and real extractor configured | Exceptions logged; never propagated; original message write succeeds; extracted records skipped on error | `NoOpMemoryExtractor` (always returns `[]`) |
| **Summarizer** | `(turns: list[WorkingMemory]) → str` | In `get_context_card()` after turns list assembled, when configured | Exceptions logged; summary set to None; card still returned | `NoOpSummarizer` (always returns `""`) |
| **EmbeddingProvider** | `(text: str) → list[float]` | By any method that calls `search()`, `get_context_card(query=..., include_long_term=True)`, or per-chunk embedding during chunked write | Exceptions logged in context (e.g., "_write_chunks: embedding_provider raised"); fallback varies by context: search fails with ValueError, context_card degrades to recency-only, chunking falls back to zero-vector | None (caller must provide or supply None to disable embedding-dependent features) |

### Extension Invocation Details

#### Consolidator
- **When:** After write to working/episodic memory, if ADD decision (new row inserted), subject to `consolidate_every_n` throttle
- **Input:** Fully-persisted `raw_memories` list (with server-assigned id, created_at, version)
- **Output:** Any mix of SemanticFact, EntityProfile, ProceduralMemory; caller responsible for setting scope fields; store persists each via appropriate repository
- **Throttle:** `consolidate_every_n` parameter (default 1 = every write); counter is in-memory per (agent_id, user_id, thread_id) tuple; resets on process restart; not shared across multiple app instances

#### Reconciler
- **When:** Only via explicit `store.reconcile(memory_type, scope)` call
- **Input:** Live, non-deleted, non-superseded SemanticFact records (up to limit, default 200, capped at 1000)
- **Output:** SupersedeDecision objects (winner_id, loser_id, reason); self-supersession and hallucinated winner_ids are filtered by store
- **Persistence:** Store calls `facts.supersede()` on each decision; loser marked with superseded_at/superseded_by/supersede_reason

#### IngestResolver
- **When:** Every `remember()` call if non-default resolver configured; before any write
- **Input:** Candidate record (not yet persisted; id may be pre-populated default) + list of up to resolver_k most-similar (existing, record, cosine_distance) tuples, ascending distance
- **Output:** IngestDecision(action, target_id=None if action=ADD/NOOP, reason="")
- **Action semantics:**
  - ADD: repo.create(candidate, scope) — default behavior
  - UPDATE: repo.update(merged_record, scope) where merged has candidate's content/metadata/embedding/confidence copied onto target
  - DELETE: repo.forget(target_id, scope) — candidate not written
  - NOOP: nothing written
- **Fallback:** UPDATE/DELETE without target_id, or unrecognized action → logged warning → ADD

#### MemoryExtractor
- **When:** `add_messages(..., extract_memories=True)` after all message writes complete (when real extractor configured)
- **Input:** Full batch of persisted WorkingMemory rows (in order written) + scope
- **Output:** Any mix of SemanticFact, EntityProfile, ProceduralMemory; store calls `remember()` on each
- **Constraint:** Explicitly opt-in per call (`extract_memories=True`); is not automatic

#### Summarizer
- **When:** `get_context_card()` after turns list assembled (when configured)
- **Input:** Chronological list of WorkingMemory (oldest-first)
- **Output:** Plain string; empty string for empty input handled gracefully
- **Constraint:** Called only when non-default configured; default (NoOpSummarizer) → ContextCard.summary = None (no LLM call, no overhead)

#### EmbeddingProvider
- **When:** Every semantic search call, context-card query embedding, per-chunk embedding on chunked writes
- **Input:** Plain text string (content or query)
- **Output:** list[float] of exactly EMBEDDING_DIM dimension
- **Fallback:** Exception logged + degradation depends on context:
  - search(): ValueError propagated
  - get_context_card(include_long_term=True): graceful degrade to recency-only
  - chunking: zero-vector sentinel on parent row
- **Validation:** Vector dimension coerced through float() before SQL binding (SQL injection guard)

---

## Part 5: Exception & Error Contracts

### Exceptions Defined in SDK

| Exception | Parent | When Raised | Handling |
|-----------|--------|-----------|----------|
| **StaleWriteError** | Exception | Optimistic concurrency conflict: `update()` WHERE version=X affects 0 rows | Caller must retry with fresh get_by_id + reapply changes |
| **InvalidMetadataFilterError** | ValueError | Unrecognized metadata_filter operator (`$` prefix not in known set) or invalid field name | Precondition violation; caller must fix filter dict |
| **ScopeMismatchError** | ValueError | `import_scope()` detects mismatch: record scope ≠ target scope on tenant_id/user_id/thread_id (agent_id allowed to differ for migration); or mixed-source stream (multiple agent_id values) | Precondition violation; either re-export from correct scope or split call per source agent |
| **ScopeImportError** | ScopeMismatchError | Subclass of ScopeMismatchError for specific import scope violations (future extensibility) | Same handling as ScopeMismatchError |
| **SchemaPolicyError** | RuntimeError | SchemaPolicy.REQUIRE_EXISTING validation fails (missing tables/columns/indexes) | Application initialization failure; DBA must provision schema before restart |

### ValueError Conditions (MemoryStore & BaseRepository)

All methods raise **ValueError** for:
- Missing or empty `scope.agent_id`
- Invalid typed arguments (e.g., max_turns < 1, except_last < 0, token_budget < 0)
- Unrecognized string discriminators (memory_type, record_types, min_results_by_type keys)
- Configuration missing when required (e.g., embedding_provider for search)
- Empty or whitespace-only query strings (search)

### Exceptions Never Propagated (Caught & Logged)

| Context | Exception Handling |
|---------|-------------------|
| Consolidator callback (remember) | Logged with full traceback; write succeeds; derived records skipped |
| Ingest resolver callback (remember) | Logged with full traceback; fallback to ADD; write proceeds |
| Similarity search failure (PIPE-2) | Logged; empty similar list used; resolver still called (may receive empty list) |
| Reconciler callback (reconcile) | Logged with full traceback; empty decision list returned; no state changes |
| Summarizer callback (get_context_card) | Logged with full traceback; ContextCard.summary = None; card returned |
| MemoryExtractor callback (add_messages) | Logged with full traceback; original writes succeed; derived extraction skipped |
| Embedding provider (get_context_card include_long_term) | Logged; fallback to recency-only facts/profiles sections; card returned |
| Embedding provider (chunking) | Logged per chunk; chunk skipped; parent row receives zero-vector sentinel |
| Per-type repository search() (search method fan-out) | Logged; type omitted from results; other types still searched |

---

## Part 6: Data Lifecycle & State Contracts

### Tombstone (Soft-Delete) Semantics

- **forget() / delete_message() / delete_memory():** Sets `deleted_at` timestamp; row remains in database
- **Visibility:** Rows with `deleted_at IS NOT NULL` excluded from all reads (search, list_all, get_by_id)
- **Recovery:** Row still physically present; queryable directly if needed; no automatic recovery mechanism
- **Persistence:** Soft-delete row persists until `purge_expired()` hard-deletes it

### Supersession (Soft-Replacement) Semantics

- **reconcile():** Sets `superseded_at`, `superseded_by`, `supersede_reason` on loser row (SemanticFact only)
- **Visibility:** Superseded rows excluded from all reads (same as deleted)
- **Governance distinction:** `deleted_at` (user/operator request) vs. `superseded_at` (AI-detected contradiction)
- **Persistence:** Superseded row remains; audit trail preserved

### Expiration (TTL) Semantics

- **expires_at field:** Set at write time; records with `expires_at < now()` considered "expired"
- **Visibility:** Expired rows normally excluded from reads
- **Export:** `export_scope(include_expired=True)` includes expired rows
- **Purge:** `purge_expired()` hard-deletes rows where `deleted_at IS NOT NULL`, NOT rows where `expires_at < now()`
  - Caller must call `forget()` on expired rows first, then `purge_expired()`

### Consolidation State (ENH-4)

- **consolidated_at field:** Set by background worker (scripts/consolidate_pending.py), not application layer
- **Present on:** WorkingMemory, EpisodicMemory only
- **Semantics:** `NULL` = "not yet consolidated"; timestamp = "claimed by worker for processing"
- **Async path:** Caller leaves consolidator as default (NoOp), adds `consolidated_at` signal to rows, runs background script separately

### Optimistic Concurrency (Version)

- **version field:** Starts at 1 on insert
- **update() contract:** WHERE clause conditions on `version = record.version` before update
- **On conflict:** `StaleWriteError` raised (0 rows affected by update)
- **Caller responsibility:** Retry loop with exponential backoff; re-fetch row via get_by_id each iteration

### Content Hashing (ENH-2)

- **content_hash field:** Hex SHA-256 of normalized content (lowercase, whitespace-collapsed)
- **Dedup gate:** `_DEDUP_ON_WRITE=True` (default for facts/profiles/procedures) checks (agent_id scope, content_hash)
- **Behavior:** If matching hash exists and is live (not deleted, not superseded), create() returns that row instead of inserting duplicate
- **Concurrency:** Best-effort only; non-atomic SELECT + INSERT window allows duplicates under concurrent writers

---

## Part 7: Chunking (ORC-2) Contract

### Chunking Configuration

| Parameter | Type | Default | Constraint |
|-----------|------|---------|----------|
| `enable_chunking` | bool | True | When False, chunking disabled regardless of embedding_provider |
| `embedding_provider` | callable | None | When None, chunking disabled regardless of enable_chunking |
| `chunk_threshold` | int | 2000 | Content length above which chunking applies |
| `chunk_size` | int | 800 | Max characters per chunk; must be > 0 |
| `chunk_overlap` | int | 200 | Overlap in characters; must be < chunk_size |

### Chunking Semantics

- **When activated:** Content length > chunk_threshold AND embedding_provider set AND chunk_repo available
- **Behavior:**
  1. Content split into overlapping chunks (sliding window: chunk_size − chunk_overlap stride)
  2. Each chunk embedded via embedding_provider
  3. Chunk rows written to memory_chunks table with metadata (source_table, source_id, chunk_index, scope)
  4. Parent row embedding set to zero-vector sentinel (NOT NULL, but semantically "use chunks instead")
- **Search implications:** search() over parent rows finds zero-vector sentinel rows, which have no semantic rank → chunks must be searched separately via `repo.search_chunks()`
- **Update path:** On update(), existing chunks for source_id are deleted, fresh chunks written
- **ORC-2 boundary:** All long-content semantic ranking happens at chunk level, not parent level

### memory_chunks Table Interface

| Field | Type | Role |
|-------|------|------|
| `id` | str | Unique chunk identifier |
| `source_table` | str | Which memory table this chunk originated from (e.g., "working_memory") |
| `source_id` | str | FK to parent record id (soft reference, no DB-level FK) |
| `chunk_index` | int | 0-based position in source record's chunk sequence |
| `chunk_text` | str | Actual text slice |
| `embedding` | list[float] | Per-chunk embedding vector |
| scope columns | (tenant_id, agent_id, user_id, thread_id) | Inherited from parent |
| `created_at` | datetime | Persisted timestamp |

---

## Part 8: Type Discriminators & Aliases

### Memory Type Aliases (for string-based APIs)

| Primary | Aliases | Table Name |
|---------|---------|-----------|
| "working" | — | working_memory |
| "episodic" | — | episodic_memory |
| "facts" | "semantic_facts" | semantic_facts |
| "profiles" | "entity_profiles" | entity_profiles |
| "procedures" | "procedural" | procedural_memory |

### Long-Term Aliases (get_context_card min_results_by_type keys)

| Primary | Aliases |
|---------|---------|
| "facts" | "semantic_facts" |
| "profiles" | "entity_profiles" |

### Record Type String (search() return values)

Returned in `SearchResult.record_type`:
- "working", "episodic", "facts", "profiles", "procedures"

---

## Part 9: Thread Convenience Wrapper (THRD-6)

The :class:`Thread` class pre-binds a MemoryScope to a MemoryStore instance. All methods are thin pass-throughs with scope pre-applied.

### Thread Public Methods

| Method | Signature | Forwards to | Notes |
|--------|-----------|------------|-------|
| `scope` (property) | `→ MemoryScope` | N/A | Returns bound scope (read-only) |
| `add_messages` | `(messages, extract_memories=True) → list[str]` | `store.add_messages(messages, scope, extract_memories=extract_memories)` | Omits scope parameter |
| `get_messages` | `(start=0, end=None) → list[WorkingMemory]` | `store.get_messages(scope, start, end)` | Slice notation |
| `delete_message` | `(message_id) → int` | `store.delete_message(message_id, scope)` | Returns 1 if found, 0 if not |
| `add_memory` | `(content, memory_id=None, metadata=None) → str` | `store.add_memory(content, scope, memory_id=memory_id, metadata=metadata)` | Returns persisted id |
| `delete_memory` | `(memory_id) → int` | `store.delete_memory(memory_id, scope)` | Returns 1 if found, 0 if not |
| `search` | `(query, record_types=None, max_results=10, metadata_filter=None) → list[SearchResult]` | `store.search(query, scope, record_types, max_results, metadata_filter)` | Omits scope parameter |
| `get_summary` | `(except_last=0, token_budget=None) → Summary` | `store.get_summary(scope, except_last, token_budget)` | Omits scope parameter |
| `get_context_card` | `(max_turns=20, query=None, include_long_term=False, min_results_by_type=None, long_term_top_k=5) → ContextCard` | `store.get_context_card(scope, max_turns, query, include_long_term, min_results_by_type, long_term_top_k)` | Omits scope parameter |

### Thread Creation

| Factory Method | Signature | Behavior |
|---|---|---|
| `store.create_thread()` | `(thread_id, agent_id, tenant_id=None, user_id=None) → Thread` | Creates and returns a Thread bound to given scope; no DB writes (schema-less by default) |
| `store.get_thread()` | `(thread_id, agent_id, tenant_id=None, user_id=None) → Thread` | Identical to create_thread (both return a Thread handle; no schema-level distinction) |
| `store.delete_thread()` | `(scope) → ErasureReport` | Hard-deletes all rows for scope (threads with no rows are "deleted" implicitly) |

---

## Part 10: Async Facades (THRD-9)

Three async wrappers (using `asyncio.to_thread`) for methods that call embedding providers or LLM hooks:

| Async Method | Forwards to | Behavior |
|---|---|---|
| `search_async(query, scope, record_types=None, max_results=10, metadata_filter=None) → Coroutine[SearchResult]` | `search()` | Offloads embedding_provider call and all DB round-trips to thread |
| `add_messages_async(messages, scope, extract_memories=True) → Coroutine[list[str]]` | `add_messages()` | Offloads all DB writes and MemoryExtractor call to thread |
| `get_context_card_async(scope, max_turns=20, query=None, include_long_term=False, min_results_by_type=None, long_term_top_k=5) → Coroutine[ContextCard]` | `get_context_card()` | Offloads embedding_provider, Summarizer, and all DB lookups to thread |

---

## Part 11: Metadata Filter (ORC-3) Contract

### Supported Operators

Only the following operators are recognized inside a metadata field's value dict:

| Operator | Syntax | Semantics |
|----------|--------|-----------|
| (exact match) | `{"field": "value"}` | Top-level scalar equality |
| `$not` | `{"field": {"$not": "value"}}` | Scalar inequality (or IS NOT NULL if value is None) |
| `$array_contains` | `{"field": {"$array_contains": "value"}}` | Value present in JSON array field |
| `$array_contains_any` | `{"field": {"$array_contains_any": ["a", "b"]}}` | Any of supplied values present in array |

### Field Name Validation

- Pattern: `^[A-Za-z_][A-Za-z0-9_.]*$`
- Characters: alphanumeric, underscore, dot (dot for nested object paths in JSON)
- Invalid pattern: raises `InvalidMetadataFilterError`

### Implementation Notes

- All filter values are inlined as SQL string literals (not bound parameters) due to Db2 12.1.5 fp0 limitations
- Field names validated before interpolation (prevents SQL injection via field names)
- Values escaped via single-quote doubling (prevents SQL injection via values)
- Array membership uses raw LOCATE() string matching (avoids Db2 JSON_QUERY() and JSON_TABLE() bugs on this version)
- No nested object operands beyond top-level fields

### Example Filters

```python
# Exact match
store.search(query, scope, metadata_filter={"role": "user"})

# Inequality
store.search(query, scope, metadata_filter={"status": {"$not": "archived"}})

# Array membership
store.search(query, scope, metadata_filter={"tags": {"$array_contains": "urgent"}})

# Array contains any
store.search(query, scope, metadata_filter={"tags": {"$array_contains_any": ["urgent", "bug"]}})
```

---

## Part 12: Enumerations

### DistanceMetric

```python
class DistanceMetric(str, enum.Enum):
    COSINE = "COSINE"
    EUCLIDEAN = "EUCLIDEAN"
    DOT = "DOT"
    MANHATTAN = "MANHATTAN"
```

**Constraint:** All five memory tables are indexed WITH DISTANCE COSINE. Passing non-COSINE metric at query time still returns results but skips the ANN index (full scan fallback).

### SearchMode

```python
class SearchMode(str, enum.Enum):
    APPROX = "APPROX"      # Uses DiskANN index; requires RUNSTATS & metric match
    EXACT = "EXACT"        # Full sequential scan; always true top-k
    DEFAULT = "DEFAULT"    # Standard FETCH FIRST (optimizer chooses)
```

---

## Part 13: Dataclasses & Value Objects

### SupersedeDecision

```python
@dataclass
class SupersedeDecision:
    winner_id: str         # ID of fact that wins
    loser_id: str          # ID of fact superseded
    reason: str            # Human-readable explanation
```

### IngestDecision

```python
@dataclass
class IngestDecision:
    action: IngestAction               # ADD, UPDATE, DELETE, or NOOP
    target_id: str | None = None       # Required for UPDATE/DELETE
    reason: str = ""                   # Optional explanation
```

### IngestAction (Enum)

```python
class IngestAction(str, enum.Enum):
    ADD = "ADD"        # Insert candidate as new row
    UPDATE = "UPDATE"  # Merge candidate into target_id
    DELETE = "DELETE"  # Tombstone target_id; candidate not written
    NOOP = "NOOP"      # Skip write entirely
```

### ErasureReport

```python
@dataclass
class ErasureReport:
    rows_deleted: dict[str, int]   # Table name → count hard-deleted
    total_deleted: int              # Sum of all counts
    erased_at: datetime | None      # UTC timestamp when erasure completed
```

### ContextCard

```python
@dataclass
class ContextCard:
    turns: list[WorkingMemory] = field(default_factory=list)
    turn_count: int = 0
    latest_at: datetime | None = None
    summary: str | None = None
    relevant_facts: list[SemanticFact] | None = None      # PIPE-4 (None unless query+include_long_term)
    relevant_profiles: list[EntityProfile] | None = None  # PIPE-4 (None unless query+include_long_term)
```

### SearchResult

```python
@dataclass
class SearchResult:
    id: str                    # Memory record UUID
    content: str               # Text content
    record_type: str           # "working", "episodic", "facts", "profiles", or "procedures"
    distance: float | None     # Cosine distance (None if recency-only fetch)
    record: Any                # Full Pydantic model instance
```

### Summary

```python
@dataclass
class Summary:
    content: str               # Formatted transcript ("{role} (-): {content}" per line)
    message_count: int         # Number of messages included
    truncated: bool            # True if token_budget truncated the output
```

---

## Summary of Hard Rules

1. **agent_id is always required** on every `MemoryStore` and `BaseRepository` call
2. **Scope isolation (VER-5)** enforces that cross-tenant/cross-agent data is never touched
3. **Soft-delete semantics:** `forget()` sets `deleted_at`; `purge_expired()` hard-deletes
4. **Soft-supersession (ENH-3):** `reconcile()` sets `superseded_at`; distinct from deletion
5. **Content deduplication (ENH-2):** Write-time hash check on (agent_id scope, content_hash) for fact-like repositories
6. **Optimistic concurrency:** `update()` conditions on `version` field; conflicts raise `StaleWriteError`
7. **Extension error handling:** All callbacks (Consolidator, Reconciler, IngestResolver, MemoryExtractor, Summarizer) have exceptions logged but never propagated
8. **Chunking (ORC-2):** Long content (>threshold) is split into chunks, each embedded separately; parent gets zero-vector sentinel
9. **Metadata filtering (ORC-3):** Only 4 operators supported; field names validated; values inlined as SQL literals
10. **Exports (PIPE-6):** Proprietary JSONL format with `_type` discriminator; import validates scope consistency

---

End of API Interface Specification.

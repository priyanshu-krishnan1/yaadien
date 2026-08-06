# 06 — Data Governance

**EPIC:** EPIC-9  
**SDD:** SDD-6  
**Status:** Approved

---

## Overview

This document formalises the data-governance model for the agent-memory-sdk: how data
is deleted, how retention and expiry are enforced, how a scope's data can be exported
and imported, and how data quality is controlled at write time.  Every claim below
maps directly to implemented, shipped code.

---

## 1. Three-Tier Deletion Policy

The SDK exposes three distinct deletion mechanisms with different guarantees.  Each
exists for a specific operational context.  They must not be used interchangeably.

### 1.1 `forget(id, memory_type, scope)` — routine tombstone

**Source:** [`MemoryStore.forget()`](../../src/agent_memory_sdk/store.py:702)

| Property | Value |
|---|---|
| Effect | Sets `deleted_at` on a single row |
| Table coverage | One row in one table |
| Row physically removed | No — row remains in the table |
| Reversible | In principle yes — the row is still present in the database |
| Requires prior tombstone | N/A (it *is* the tombstone step) |

**Mechanism.**  `forget()` calls the target repository's `forget()` method, which
issues an `UPDATE … SET deleted_at = CURRENT TIMESTAMP WHERE id = ? AND agent_id = ?`
(plus any other scope predicates) against a single row in a single table.  The row is
never physically removed; it is excluded from normal reads because every `search()`,
`list_all()`, and `get_by_id()` query filters on `deleted_at IS NULL`.

**Use case.**  Agent-initiated forgetting of a single memory during normal operation.
For example: the user says "forget that I told you my address" — the agent calls
`store.forget(record_id, "facts", scope)`.  No data is permanently destroyed; an
operator could in principle recover the row directly from the database.

**What it does not do.**  Does not purge the row.  Does not touch any other table,
including `memory_chunks`.  Does not return a cross-table audit record.

---

### 1.2 `purge_expired(scope)` — maintenance hard-delete

**Source:** [`MemoryStore.purge_expired()`](../../src/agent_memory_sdk/store.py:737)

| Property | Value |
|---|---|
| Effect | Hard-`DELETE` of tombstoned rows (`deleted_at IS NOT NULL`) |
| Table coverage | All five memory tables (not `memory_chunks`) |
| Row physically removed | Yes — permanently |
| Reversible | No |
| Requires prior tombstone | **Yes** — only deletes rows already tombstoned by `forget()` |
| Invoked automatically | **Never** — must be called explicitly |

**Mechanism.**  `purge_expired()` iterates over all five repositories and calls each
one's `purge_expired(scope)` method, which issues a
`DELETE … WHERE agent_id = ? AND deleted_at IS NOT NULL` (plus scope predicates).
It returns a `dict[str, int]` of `table_name → rows_deleted` for monitoring.

**Use case.**  Periodic housekeeping — recovering physical storage from rows that were
previously tombstoned.  Run from a cron job (see `scripts/purge_expired.py`) on a
schedule appropriate for the deployment (e.g., nightly).

**What it does not do.**  Does not touch rows that have never been tombstoned, even if
they are TTL-expired.  Does not cover `memory_chunks` (chunk rows have no
`deleted_at` column; they are only removed via `erase_all()`).

---

### 1.3 `erase_all(scope)` — compliance-grade irreversible erasure

**Source:** [`MemoryStore.erase_all()`](../../src/agent_memory_sdk/store.py:776)

| Property | Value |
|---|---|
| Effect | Hard-`DELETE` of **every** row matching *scope* |
| Table coverage | All five memory tables **and** `memory_chunks` (six tables total) |
| Row physically removed | Yes — permanently |
| Reversible | **No** — no grace period, no recovery path except a pre-call database backup |
| Requires prior tombstone | **No** — bypasses `deleted_at` entirely |
| Invoked automatically | Never — must be called explicitly |
| Return value | [`ErasureReport`](../../src/agent_memory_sdk/types.py:926) — per-table counts, total, and UTC timestamp |

**Mechanism.**  `erase_all()` iterates over all five repositories (calling each one's
`erase_all(scope)` method, which issues an unconditional
`DELETE … WHERE agent_id = ?` plus scope predicates with no `deleted_at` condition)
and then calls `ChunkRepository.erase_by_scope(scope)` for the `memory_chunks` table.
It assembles the per-table row counts into an `ErasureReport` and logs the event at
`INFO` level before returning.

**Use case.**  Responding to a data-subject erasure request ("right to be forgotten").

---

### 1.4 Which mechanism to use for a data-subject erasure request

**Use `erase_all()`** — and only `erase_all()` — when responding to a data-subject
erasure request.

The three-tier rationale:

- `forget()` only tombstones a *single row in a single table*.  It leaves the data
  physically present in the database and does not touch `memory_chunks`.  It is not
  suitable for fulfilling a cross-table erasure obligation.
- `purge_expired()` only removes rows that were *already* tombstoned by `forget()`.
  Un-tombstoned rows are never touched, so a user whose data was never individually
  forgotten would have nothing removed.
- `erase_all()` is the correct choice because:
  1. It is **irreversible** — rows are hard-deleted, not tombstoned.
  2. It **bypasses the tombstone lifecycle** — every row for the given scope is removed
     regardless of `deleted_at` status.
  3. It covers **all six tables**, including `memory_chunks` where content-chunking
     fragments live.
  4. It returns an **`ErasureReport` audit record** documenting which tables were
     affected, how many rows were removed, and when the operation completed.

---

## 2. ErasureReport — Audit-Record Specification

**Source:** [`ErasureReport`](../../src/agent_memory_sdk/types.py:926) in
[`types.py`](../../src/agent_memory_sdk/types.py)

`ErasureReport` is a dataclass returned exclusively by `erase_all()`.  Its purpose is
to provide a durable, inspectable record of exactly what was permanently removed in
response to a compliance erasure request.

### 2.1 Fields

| Field | Type | Description |
|---|---|---|
| `rows_deleted` | `dict[str, int]` | Table name → number of rows hard-deleted for the requested scope.  Always contains an entry for **all six tables** (`working_memory`, `episodic_memory`, `semantic_facts`, `entity_profiles`, `procedural_memory`, `memory_chunks`), even when a table had zero matching rows (value `0`). |
| `total_deleted` | `int` | `sum(rows_deleted.values())` — total rows permanently removed across every table. |
| `erased_at` | `datetime \| None` | UTC timestamp captured when the erasure completed — the "when" half of the audit record. |

### 2.2 What ErasureReport proves

- A hard-`DELETE` was executed against the database for the given scope.
- Which of the six tables were targeted by that `DELETE`.
- How many rows were removed from each table.
- The UTC time at which the operation completed.

### 2.3 What ErasureReport does NOT prove

`ErasureReport` is an application-level audit record.  It does not and cannot attest to:

- **Secure erasure at the storage media level.**  The SQL `DELETE` removes the row from
  the logical database view; it does not guarantee that the bytes are overwritten on
  disk.
- **Absence of the data in Db2's WAL / undo logs.**  Db2's write-ahead log and any
  active undo segments may retain a copy of the deleted rows until those log files are
  recycled or overwritten.
- **Invalidation of application-level caches.**  Any in-process or out-of-process
  cache (query result caches, embedding caches, application-tier object caches) that
  holds a copy of the deleted data is not invalidated by `erase_all()`.  Callers must
  handle cache invalidation separately.
- **Purge of backup copies.**  Database backups taken before the `erase_all()` call
  will still contain the data.  Backup lifecycle is outside the scope of this SDK.

Callers who need media-level erasure guarantees must address Db2 storage-layer controls
(e.g., log archiving policies, secure-delete tooling, encryption-key destruction)
separately from this SDK.

### 2.4 Scope correlation requirement

`ErasureReport` carries no scope fields of its own.  Callers must log the
[`MemoryScope`](../../src/agent_memory_sdk/models.py:34) alongside the report at the
call site so that the audit trail identifies *whose* data was erased.  Example:

```python
scope = MemoryScope(agent_id="agent-001", user_id="user-42")
report = store.erase_all(scope)
audit_logger.info(
    "erasure_complete",
    scope=scope.model_dump(),
    rows_deleted=report.rows_deleted,
    total=report.total_deleted,
    erased_at=report.erased_at.isoformat(),
)
```

---

## 3. Retention and TTL Policy

### 3.1 `expires_at` column

All five memory tables (`working_memory`, `episodic_memory`, `semantic_facts`,
`entity_profiles`, `procedural_memory`) carry an optional `expires_at` timestamp
column (type `TIMESTAMP`, nullable).  The [`_MemoryBase`](../../src/agent_memory_sdk/models.py:61)
base model exposes this as `expires_at: datetime | None = None`.

Setting `expires_at` on a record is optional.  When set, it represents the logical
expiry time after which the record should be treated as no longer current.

### 3.2 Expiry is passive, not automatic

**Expired rows are not automatically deleted.**  The SDK has no background thread,
scheduler, or trigger that removes expired rows.  Expiry is enforced passively:

- `search()`, `list_all()`, and `get_by_id()` filter with
  `deleted_at IS NULL AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP)`.
- A row past its `expires_at` is therefore invisible to normal reads but remains
  physically present in the database.

**There is no automatic TTL enforcement by the SDK.**

### 3.3 Explicit purge required

To hard-delete expired rows, an operator must call `purge_expired(scope)` explicitly.
The canonical reference for scheduling this is `scripts/purge_expired.py`, which is
designed to be run as a cron job.

Note that `purge_expired()` only removes rows that are **both** expired **and**
tombstoned (`deleted_at IS NOT NULL`).  A row that has passed its `expires_at` but
has never been tombstoned by `forget()` is excluded from normal reads but will not be
hard-deleted by `purge_expired()`.  To remove all data for a scope regardless of
tombstone state, use `erase_all()`.

### 3.4 Retention policy summary

| Condition | Visible in reads | Hard-deleted by `purge_expired()` | Hard-deleted by `erase_all()` |
|---|---|---|---|
| Live, not expired | ✅ Yes | No | Yes |
| Live, TTL-expired | ❌ No | No | Yes |
| Tombstoned (`deleted_at` set), not expired | ❌ No | Yes | Yes |
| Tombstoned, TTL-expired | ❌ No | Yes | Yes |

---

## 4. Data Portability

### 4.1 `export_scope(scope)` — generator over serialized records

**Source:** [`MemoryStore.export_scope()`](../../src/agent_memory_sdk/store.py:868)

`export_scope(scope)` is a generator that yields one JSON-serializable `dict` per row
across all five memory tables plus `memory_chunks`, for the given scope.  Each dict
carries a `"_type"` discriminator field naming the source table (`"working_memory"`,
`"episodic_memory"`, `"semantic_facts"`, `"entity_profiles"`, `"procedural_memory"`,
or `"memory_chunks"`).

For the five memory-type tables, the remainder of each dict is
`record.model_dump(mode="json")` — all fields on the corresponding Pydantic model, with
`datetime` fields serialized as ISO-8601 strings and the `embedding` field as a plain
JSON array of floats.

Tables are fetched in internal pages of 500 rows via repeated
`list_all(limit=500, offset=…)` calls so large scopes do not need to be fully
materialized in memory.

**What is included:**  non-deleted, non-superseded rows, including TTL-expired rows
that have not yet been tombstoned (`include_expired=True`).  Tombstoned and superseded
rows are excluded — they represent data the operator or Reconciler already decided
should not be treated as current, and `import_scope()` has no mechanism to restore a
row directly into a tombstoned or superseded state.

### 4.2 `import_scope(data, scope)` — re-insert into a target scope

**Source:** [`MemoryStore.import_scope()`](../../src/agent_memory_sdk/store.py:980)

`import_scope(records, scope)` consumes the stream produced by `export_scope()` (or
any iterable of `"_type"`-tagged dicts in the same format) and re-inserts every record
into `scope` via the ordinary per-type `create()` methods.  It returns a
`dict[str, int]` of `table_name → count` recording how many records were processed for
each table.

The target scope's `agent_id` / `tenant_id` / `user_id` / `thread_id` values are
applied to every inserted row, making explicit agent migration (writing records
exported from one `agent_id` into a different target `agent_id`) a supported use case.
To prevent silent corruption from mixed-source streams, all records in the stream must
share the same source `agent_id`; a mismatch raises `ScopeMismatchError`.

**Known limitations:**

- `created_at`, `updated_at`, and `version` are reset to "now" / `1` by `create()` —
  an import produces fresh live rows, not an exact replica of the original row's
  lifecycle timestamps.
- The standard write-time dedup check still applies for `semantic_facts`,
  `entity_profiles`, and `procedural_memory`: if a row with the same
  `(scope, content_hash)` already exists live in the target, `create()` returns the
  existing row and does not insert a duplicate.

### 4.3 Format limitation

The export format is this SDK's own internal format.  It is **not** an
industry-standard interchange format (e.g., not JSON-LD, not any published agent-memory
interchange specification).  No cross-vendor standard for agent memory import/export
exists in the industry at the time of writing.  The format exists to close the
SDK-internal gap of having no backup or migration path at all; it is not designed for
interoperability with other implementations.

---

## 5. Data Quality Governance Controls

### 5.1 `confidence` — grounding-certainty score

**Source:** [`_MemoryBase.confidence`](../../src/agent_memory_sdk/models.py:88);
added by [migration 0003](../../src/agent_memory_sdk/db/migrations/0003_confidence_and_content_hash.sql:30)

`confidence` is a `DOUBLE NOT NULL DEFAULT 1.0` column present on all five memory
tables.  The Pydantic field is `confidence: float = Field(default=1.0, ge=0.0, le=1.0)`;
the `[0.0, 1.0]` range is enforced by Pydantic at construction time.

**Semantics:**

| Value | Meaning |
|---|---|
| `1.0` | Fully certain / directly observed or written |
| `0.95` | Explicitly stated by the user (typical Consolidator assignment) |
| `0.6` | Tentatively inferred by an LLM (typical Consolidator assignment) |
| `0.0` | Fully uncertain / speculative |

**Who sets it.**  The SDK persists and filters by `confidence` but does not
auto-compute it.  The caller — typically a `Consolidator` implementation — is
responsible for assigning a meaningful value at write time.  The `NoOpConsolidator`
(the default) never assigns a `confidence` value; all records written through it
default to `1.0`.

**How it is used.**  Repository `search()` and `list_all()` methods accept an optional
`min_confidence` parameter.  Rows with `confidence < min_confidence` are excluded from
results.

**Pre-migration rows.**  Rows written before migration 0003 were automatically given
`confidence = 1.0` by the `DEFAULT 1.0` column definition — no backfill was required.

---

### 5.2 `content_hash` — write-time deduplication

**Source:** [`_MemoryBase.content_hash`](../../src/agent_memory_sdk/models.py:97);
added by [migration 0003](../../src/agent_memory_sdk/db/migrations/0003_confidence_and_content_hash.sql:33)

`content_hash` is a `VARCHAR(64)` column (hex SHA-256, nullable) present on all five
memory tables, with a supporting index on `(agent_id, content_hash)` per table.

**Computation.**  The hash is computed at write time by `BaseRepository.create()` over
the normalized content (lowercased, whitespace-collapsed) of the record.  Rows written
before migration 0003 have `content_hash = NULL`.

**Dedup check.**  Before inserting a new row, `create()` checks whether a non-deleted
row with the same `(agent_id scope, content_hash)` already exists in the target table.
If one does, `create()` returns that existing row without inserting a duplicate.  The
check is done in application code, not via a `UNIQUE` constraint, because deduplication
is scoped to `(agent_id, content_hash)` and intentionally allows superseded or deleted
rows to share a hash with a live row.

**Tables with dedup enabled (`_DEDUP_ON_WRITE = True`):** `semantic_facts`,
`entity_profiles`, `procedural_memory`.

**Tables with dedup disabled (`_DEDUP_ON_WRITE = False`):** `working_memory`,
`episodic_memory` — conversation turns are expected to be unique by timestamp and
are deliberately not dedup-gated; every write produces a new row.

---

### 5.3 Supersession — AI-managed fact lifecycle (`SemanticFact` only)

**Source:** [`SemanticFact`](../../src/agent_memory_sdk/models.py:157) supersession
fields; added by [migration 0004](../../src/agent_memory_sdk/db/migrations/0004_supersession.sql)

#### Supersession fields

| Field | Type | Description |
|---|---|---|
| `superseded_by` | `VARCHAR(36) \| None` | `id` of the winning fact that replaced this one; `None` if live |
| `superseded_at` | `TIMESTAMP \| None` | Timestamp when the Reconciler recorded the supersession; `None` if live |
| `supersede_reason` | `VARCHAR(255) \| None` | Human-readable reason (e.g., `"contradicts: user now prefers light mode"`); `None` if live |

Supersession is only available on `semantic_facts`.  `entity_profiles` and
`procedural_memory` are intentionally excluded (profiles are kept current via
`update()`; procedural skills are version-controlled via optimistic concurrency).

#### How supersession is set

`superseded_by`, `superseded_at`, and `supersede_reason` are populated exclusively by
`SemanticFactRepository.supersede()`, which is called by `MemoryStore.reconcile()`
when a configured `Reconciler` returns a `SupersedeDecision`.  Callers must not set
these fields directly.

#### Governance distinction: supersession vs. deletion

Both `superseded_at IS NOT NULL` and `deleted_at IS NOT NULL` cause a row to be
excluded from normal reads.  They must not be treated as equivalent:

| Column | Set by | Meaning |
|---|---|---|
| `deleted_at` | `forget()` / `soft_delete()` | "The user or operator asked us to forget this." |
| `superseded_at` | `reconcile()` / `Reconciler` | "The AI learned this was contradicted by a newer, more accurate fact." |

Keeping these as separate columns lets audit tooling distinguish explicit user-directed
erasure from AI-managed knowledge-base lifecycle events.  This is a deliberate
governance design decision, documented in `project-management/DECISIONS.md` (ENH-3
entry), not merely a naming preference.

#### Why `semantic_facts` only

`semantic_facts` are independently-addressable atomic claims that can logically
contradict each other within the same scope (e.g., "user prefers dark mode" later
contradicted by "user switched to light mode").  The other memory types do not have
this property: episodic/working memory rows are time-ordered conversation turns, entity
profiles are merged aggregates kept current via `update()`, and procedural skills are
version-controlled in place.  Adding supersession columns to those tables would add
schema cost with no clear query path that would use them.

---

## 6. Scope of This Document

This document covers the governance controls implemented and shipped in the SDK as of
the migrations and source files cited above.  Controls outside the SDK boundary —
including Db2 storage-level encryption, log archiving policy, backup retention policy,
and network-layer access controls — are outside the scope of this document and must be
addressed at the infrastructure level.

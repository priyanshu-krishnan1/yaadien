# Integration Testing Guide

This document explains how to run the integration test suite against a real
IBM Db2 LUW instance.  The integration tests live in `tests/integration/`
and are **automatically skipped** when the `DB2_DATABASE` environment
variable is not set, so they never break CI environments without Db2.

---

## Quick-start: Db2 in Docker (recommended for local dev)

IBM ships a free developer edition of Db2 LUW as a Docker image.

### Prerequisites

- Docker Desktop (or Docker Engine on Linux) installed and running.
- At least 4 GB RAM allocated to Docker (Db2 developer image requires ~2 GB).

### 1 — Pull and start the container

```bash
docker run -d \
  --name db2-dev \
  -e DB2INST1_PASSWORD=passw0rd \
  -e LICENSE=accept \
  -p 50000:50000 \
  --privileged \
  icr.io/db2_community/db2:12.1.5.0
```

> **Do not pass `-e DBNAME=TESTDB`** — the auto-created database uses Db2's default 4 KB page size, which is too small to hold a `VECTOR(1536,FLOAT32)` index (each vector is 6 144 bytes).  Create the database manually with a 32 KB page size after the instance starts (step 1b below).

> **Image tag:** `12.1.5.0` is pinned here (and in `.github/workflows/ci.yml`) because `CREATE VECTOR INDEX` became GA in **Db2 12.1.5** and untagged `:latest` is not reproducible in unattended CI.  Update both places together when upgrading.

> **Note:** The `icr.io/db2_community/db2` image requires `--privileged` (or at minimum
> `--cap-add IPC_OWNER`) to start the DB2 instance inside the container.
>
> **Apple Silicon (M1/M2/M3):** add `--platform=linux/amd64` to the `docker run` command above,
> as this image is x86-64 only and requires Rosetta/QEMU emulation on ARM hosts.

The first start takes 3–5 minutes as Db2 initialises the instance.
Monitor progress with:

```bash
docker logs -f db2-dev
```

Wait until you see `(*) Setup has completed.`, then continue to step 1b.

### 1b — Create the database with 32 KB pages

Once the instance is running, create `TESTDB` manually so the page size is large enough for vector indexes:

```bash
docker exec db2-dev bash -c \
  "su - db2inst1 -c 'db2 create database TESTDB using codeset UTF-8 territory US pagesize 32768'"
```

Verify the connection works:

```bash
docker exec db2-dev bash -c "su - db2inst1 -c 'db2 connect to TESTDB'"
```

For **CI / unattended use**, the CI job polls `db2 list db directory` every 15 s for instance readiness, then creates the database, then polls `db2 connect` for TCP readiness — see `.github/workflows/ci.yml` for the exact loop.

### 2 — Verify connectivity

```bash
docker exec -it db2-dev bash -c "su - db2inst1 -c 'db2 connect to TESTDB'"
```

You should see:

```
   Database Connection Information
   Database server        = DB2/LINUXX8664 12.1.x.x
   ...
```

### 3 — Set environment variables

Copy `.env.example` to `.env` and fill in the values:

```dotenv
DB2_DATABASE=TESTDB
DB2_HOSTNAME=localhost
DB2_PORT=50000
DB2_UID=db2inst1
DB2_PWD=passw0rd
```

Or export them in your shell:

```bash
export DB2_DATABASE=TESTDB
export DB2_HOSTNAME=localhost
export DB2_PORT=50000
export DB2_UID=db2inst1
export DB2_PWD=passw0rd
```

### 4 — Install the SDK with all extras

```bash
pip install -e ".[dev,langchain,openai-agents,mcp]"
```

> The integration tests skip adapter-specific subtests automatically when
> `langchain-core` or `mcp` is not installed, so only the extras you install
> are tested.

### 5 — Run the integration tests

```bash
# Run only integration tests (skips unit tests)
pytest tests/integration/ -v -m integration

# Run everything — unit tests pass with or without Db2
pytest -v
```

On the first run the migration tests create all five memory tables and
vector indexes.  Subsequent runs are idempotent.

---

## Running against IBM Cloud Db2

Use the same env vars but set `DB2_SECURITY=SSL` and point `DB2_HOSTNAME`
at your cloud endpoint:

```dotenv
DB2_DATABASE=BLUDB
DB2_HOSTNAME=xxxx.databases.appdomain.cloud
DB2_PORT=32626
DB2_UID=ibm_cloud_user
DB2_PWD=your_password
DB2_SECURITY=SSL
```

---

## What the integration tests cover

| Test file | Coverage |
|-----------|---------|
| `test_migration.py` | Migration idempotency, `schema_migrations` tracking, all 5 tables exist with correct columns, `NOT NULL` VECTOR column, vector indexes present in SYSCAT |
| `test_core.py` | CRUD round-trips for all 5 memory types, vector search nearest-neighbour correctness (unit vectors), scope isolation (list_all / get_by_id / search), `forget()`/tombstone, `purge_expired()` (hard-delete + scope safety), TTL (`expires_at` filtering), optimistic concurrency (`StaleWriteError`), consolidator-derived record persistence |
| `test_adapters_integration.py` | LangChain `Db2ChatMessageHistory` (add/retrieve/clear/batch/type preservation), `Db2MemoryStore` (mset/mget/mdelete/yield_keys/prefix), OpenAI Agents SDK `Db2Session` (add_items/get_items/limit/clear_session/pop_item/recall_episodes), MCP tool functions (remember/recall/forget/list/fallback-to-list) |

---

## Marker and skip behaviour

All integration tests carry `pytestmark = pytest.mark.integration`.

- **Without Db2 (`DB2_DATABASE` unset):** every test in `tests/integration/`
  is marked `skip` at collection time by the `pytest_collection_modifyitems`
  hook in `tests/integration/conftest.py`.
- **With Db2:** tests run against the real instance.  Each test uses a fresh
  UUID-based `agent_id` (the `unique_agent_id` fixture) so tests are fully
  isolated even when run in parallel.

To run the unit tests only (never touch Db2):

```bash
pytest tests/ --ignore=tests/integration/ -v
# or
pytest -m "not integration" -v
```

---

## Cleaning up

The tests do **not** drop the tables after running (migrations are
session-scoped and shared).  To start fresh, drop the tables manually:

```sql
-- Connect to your test database, then:
DROP TABLE working_memory;
DROP TABLE episodic_memory;
DROP TABLE semantic_facts;
DROP TABLE entity_profiles;
DROP TABLE procedural_memory;
DROP TABLE schema_migrations;
```

Then re-run the tests; the migration suite will recreate everything.

To stop and remove the Docker container:

```bash
docker stop db2-dev && docker rm db2-dev
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: ibm_db` | `pip install ibm_db` — the clidriver is bundled |
| `Failed to connect to Db2` | Check `DB2_HOSTNAME`, port `50000`, and that the container is ready (`docker logs db2-dev`) |
| `CREATE VECTOR INDEX` fails | Requires **Db2 12.1.5+** (GA release of the VECTOR feature).  The `icr.io/db2_community/db2:12.1.5.0` image ships this version. |
| Tests skip silently | `DB2_DATABASE` env var not exported in this shell session |
| `ConnectionPoolExhausted` | Increase `DB2_POOL_SIZE` (default 5) or check for connection leaks |
| `SQLSTATE 42613` on migration | Invalid VECTOR column DDL — confirm no `DEFAULT VECTOR_FILL` clause in `0002_memory_tables.sql` (already removed) |

---

## Live-coverage matrix (EPIC-10)

> **Added:** 2026-08-05 by LIVE-19 (post-EPIC-10 audit)
>
> All 18 LIVE-* files were added by Epic-10. Combined with the original 3-file baseline (STEP-7), the integration suite now has **21 files and 252 collected tests** that exercise every public MemoryStore, Thread, and repository method against a real Db2 instance.

### New files added by Epic-10

| Test file | Story | Methods exercised / Coverage |
|---|---|---|
| `test_dedup_confidence.py` | LIVE-1 | `SemanticFact.create()` with `confidence` 0.3/0.6/0.95/1.0, `list_all(min_confidence=)`, `search(min_confidence=)`, dedup hits for byte-identical and normalized-identical content, cross-scope dedup isolation, `update()` hash recompute, dedup skip on deleted row, dedup skip on superseded row |
| `test_reconciliation.py` | LIVE-2 | `MemoryStore.reconcile('facts', scope)`, `SemanticFactRepository.supersede()`, `list_all()` excludes superseded, `search()` excludes superseded, guards: entity_profiles/procedural untouched, already-deleted noop, already-superseded noop |
| `test_consolidation_worker.py` | LIVE-3 | `MemoryStore.consolidate_every_n` throttle, `BaseRepository._claim_consolidated()` real concurrent 2-thread race (20 iterations), worker-script `_fetch_pending()` + idempotency |
| `test_context_card.py` | LIVE-4 | `MemoryStore.get_context_card(scope, max_turns=)` chronological ordering, `turn_count`, `latest_at`, empty scope, `Summarizer` hook with result and failure fallback; PIPE-4 `include_long_term=True`, `relevant_facts`/`relevant_profiles` backfill via `min_results_by_type` |
| `test_chunking.py` | LIVE-5 | `ChunkRepository.insert_chunk()`, `list_all_for_scope()`, `list_all()` pagination, `search_chunks()` nearest-neighbour, `delete_by_source()` targeted removal, `erase_by_scope()` full scope erasure |
| `test_metadata_filters_schema_policy.py` | LIVE-6 | `list_all(metadata_filter=)` exact match / `$not` / `$array_contains` / `$array_contains_any`, no-match empty result, `search(metadata_filter=)` end-to-end, `InvalidMetadataFilterError` raised live, `Migrator(schema_policy=REQUIRE_EXISTING)` passes on migrated schema |
| `test_hybrid_search.py` | LIVE-7 | `search(hybrid=True, query_text=)` reorders vs. pure vector, RRF formula verified independently, empty `query_text` degenerates to vector-only, no Text Search Extender dependency confirmed by successful execution |
| `test_ingest_resolver.py` | LIVE-8 | All four `IngestAction` outcomes: ADD (new row), UPDATE (version bumped), DELETE (tombstoned), NOOP (row count unchanged); cross-scope isolation: resolver sees empty `similar` list for foreign-scope rows |
| `test_agent_framework_integration.py` | LIVE-9 | `MemoryStoreContextProvider.before_run()` / `after_run()` real writes; `MemoryStoreHistoryProvider.save_messages()` / `get_messages()` real round-trip; scope isolation; gated behind `pytest.importorskip("agent_framework")` |
| `test_erasure.py` | LIVE-10 | `MemoryStore.erase_all()` counts match pre-call raw `SELECT COUNT(*)`, rows truly hard-deleted, sibling scope intact, `delete_thread()` thread-scoped cascade leaves other threads untouched |
| `test_export_import.py` | LIVE-11 | `export_scope()` streaming Iterator, `import_scope()` full field round-trip (content/metadata/embedding within float32 tolerance/confidence), `_type` discriminator on every record, `ScopeMismatchError` on mismatched import, vector tolerance 1e-4 |
| `test_thread_primitives.py` | LIVE-12 | `add_messages()` / `get_messages(start, end)` / `delete_message()` (THRD-1); `add_memory()` / `add_user()` upsert / `add_agent()` upsert (THRD-2); `MemoryStore.search(query_text, ...)` with real embedding provider, `record_types` filter, `metadata_filter` passthrough (THRD-3); `delete_memory()` table-agnostic dispatch to facts/profiles/procedures (THRD-8) |
| `test_thread_summary.py` | LIVE-13 | `get_summary()` full transcript, budget truncation stops before exceeding, oldest-first inclusion, `except_last` exclusion, empty scope empty string |
| `test_memory_extractor.py` | LIVE-14 | `MemoryExtractor` with `extract_memories=True` writes to `store.facts`, `extract_memories=False` skips, raising extractor caught and logged without failing `add_messages()` |
| `test_thread_facade.py` | LIVE-15 | `MemoryStore.create_thread()` schema-less, `get_thread()` reopens existing, `get_thread()` empty on nonexistent, all Thread pass-through methods (add_messages/get_messages/delete_message/add_memory/delete_memory/get_summary/get_context_card), `delete_thread()` cascade, thread-scope isolation |
| `test_cascading_delete.py` | LIVE-16 | `delete_user(cascade=True)` multi-table multi-thread cascade verified by raw counts, sibling user intact, `delete_agent(cascade=True)` clears full agent tree, `cascade=False` removes only EntityProfiles |
| `test_async_facade.py` | LIVE-17 | `search_async()` produces identical results to sync, `add_messages_async()` real Db2 write, `get_context_card_async()` round-trip, `asyncio.gather()` concurrent calls across two scopes no cross-contamination |
| `test_scope_matching_modes.py` | LIVE-18 | `exact_agent_match=True` default exhaustive leak detection (multiple agents, multiple record types), `exact_agent_match=False` fuzzy mode broader results; `exact_thread_match=True/False`; unscoped-only query returns only thread_id=None rows; static source scan confirms no hardcoded `False` in production code |

### Pre-existing baseline files (STEP-7)

| Test file | Story | Methods exercised |
|---|---|---|
| `test_migration.py` | STEP-7 | Migration idempotency, `schema_migrations` tracking, all 5 tables correct columns + vector indexes |
| `test_core.py` | STEP-7 | CRUD × 5 types, vector search, scope isolation, forget/tombstone, purge_expired, TTL, StaleWriteError, Consolidator |
| `test_adapters_integration.py` | STEP-7 | LangChain `Db2ChatMessageHistory`/`Db2MemoryStore`, OpenAI Agents `Db2Session`, MCP tools |

### Methods confirmed intentionally live-untestable

| Method | Reason |
|---|---|
| `ConnectionPool._build_conn_str()` | Pure in-process string construction; no Db2 interaction |
| `Migrator.run()` under `REQUIRE_EXISTING` with empty schema | Requires DBA-provisioned empty schema namespace; the PASS path (migrated schema) is tested; the FAIL path is documented as `xfail` in `test_metadata_filters_schema_policy.py` |
| `NoOpConsolidator` / `NoOpReconciler` / `NoOpIngestResolver` / `NoOpSummarizer` / `NoOpMemoryExtractor` | No-ops by definition; behavior is `[]` / `''` / `ADD` with no Db2 interaction |
| `scripts/check_connection.py` | A CLI script, not a library function; verified in STEP-1 |

### CI job coverage

PH-2's `integration-test` job in `.github/workflows/ci.yml` runs `pytest -m integration -v --no-cov`, which collects all 21 `tests/integration/` files (all marked `integration`) automatically. No CI YAML changes are required for the new LIVE-* files. The `agent-framework` extra is excluded from the CI install line; `test_agent_framework_integration.py` gates itself behind `pytest.importorskip` and degrades to a skip rather than an error in that environment.

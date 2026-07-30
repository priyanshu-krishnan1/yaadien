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
  -e DBNAME=TESTDB \
  -p 50000:50000 \
  --privileged \
  icr.io/db2_community/db2:12.1.2.0
```

> **Image tag:** `12.1.2.0` is pinned here (and in `.github/workflows/ci.yml`) because `CREATE VECTOR INDEX` requires **Db2 12.1.2+** and untagged `:latest` is not reproducible in unattended CI.  Update both places together when upgrading.

> **Note:** The `icr.io/db2_community/db2` image requires `--privileged` (or at minimum
> `--cap-add IPC_OWNER`) to start the DB2 instance inside the container.
>
> **Apple Silicon (M1/M2/M3):** add `--platform=linux/amd64` to the `docker run` command above,
> as this image is x86-64 only and requires Rosetta/QEMU emulation on ARM hosts.

The first start takes 3–5 minutes as Db2 initialises the database.
Monitor progress with:

```bash
docker logs -f db2-dev
```

Wait until you see:

```
(*) Setup has completed.
```

For **CI / unattended use**, poll with the connectivity check below instead of
tailing logs (the CI job retries every 15 s for up to 10 minutes):

```bash
for i in $(seq 1 40); do
  if docker exec db2-dev bash -c \
      "su - db2inst1 -c 'db2 connect to TESTDB'" \
      > /dev/null 2>&1; then
    echo "Db2 is ready (attempt $i)"; break
  fi
  echo "  attempt $i/40 — not ready yet, sleeping 15s…"; sleep 15
done
```

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
| `CREATE VECTOR INDEX` fails | Requires **Db2 12.1.2+** with the VECTOR feature enabled.  The `icr.io/db2_community/db2` image ships 12.1. |
| Tests skip silently | `DB2_DATABASE` env var not exported in this shell session |
| `ConnectionPoolExhausted` | Increase `DB2_POOL_SIZE` (default 5) or check for connection leaks |
| `SQLSTATE 42613` on migration | Invalid VECTOR column DDL — confirm no `DEFAULT VECTOR_FILL` clause in `0002_memory_tables.sql` (already removed) |

# agent-memory-sdk

[![CI](https://github.com/priyanshu-krishnan1/yaadien/actions/workflows/ci.yml/badge.svg)](https://github.com/priyanshu-krishnan1/yaadien/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/priyanshu-krishnan1/yaadien/graph/badge.svg)](https://codecov.io/gh/priyanshu-krishnan1/yaadien)
[![Project Dashboard](https://img.shields.io/badge/dashboard-GitHub%20Pages-0f62fe)](https://priyanshu-krishnan1.github.io/yaadien/)

Governed multi-type memory system for AI agents, backed by **IBM Db2 LUW**
and its native `VECTOR` column type. Framework-agnostic core (zero required
dependencies beyond `ibm_db` + `pydantic`), with thin optional adapters for
LangChain, the OpenAI Agents SDK, and MCP.

Every memory row is scoped (`tenant_id` / `agent_id` / `user_id` /
`thread_id`), versioned, soft-deletable, and TTL-aware out of the box — this
is a governed store, not just a vector database with a `remember()` label on
it.

## Features

- **Five memory types**, one normalized Db2 table each: `working`,
  `episodic`, `semantic_facts`, `entity_profiles`, `procedural`.
- **Hierarchical scoping** (`MemoryScope`) enforced in SQL on every read and
  write — no cross-tenant/agent/user/thread leakage.
- **Lifecycle governance**: soft-delete (`forget()`), TTL (`expires_at` +
  explicit `purge_expired()`), optimistic-concurrency versioning
  (`StaleWriteError`), pluggable synchronous **consolidation** (raw turns →
  derived facts/profiles/skills) with an async/background worker escape
  hatch (see [`scripts/consolidate_pending.py`](scripts/consolidate_pending.py)) and a `consolidate_every_n`
  throttle.
- **Confidence scoring** (0.0–1.0 grounding-certainty) and **content-hash
  dedup** (idempotent writes for facts/profiles/procedures).
- **Reconciliation / soft-supersession** for contradicting facts
  (`Reconciler` protocol + `MemoryStore.reconcile()`) — distinct from
  user-initiated `forget()`, so audit tooling can tell "AI decided this was
  outdated" from "user asked us to erase this."
- **Content chunking** for long memories (> 2000 chars, configurable):
  overlapping 800-char windows embedded and searched independently via
  `search(search_chunks=True)`, transparently resolved back to parent rows.
- **Structured metadata filters** on `search()` / `list_all()`
  (`{"field": val}`, `$not`, `$array_contains`, `$array_contains_any`).
- **Hybrid retrieval** — optional RRF fusion of vector search with a
  zero-infrastructure Python keyword-overlap ranker (`search(hybrid=True,
  query_text=...)`).
- **Context cards** — `MemoryStore.get_context_card()` assembles recent
  working-memory turns for a thread, with an optional pluggable
  `Summarizer`.
- **Schema policy** — attach to a pre-provisioned database with
  `SchemaPolicy.REQUIRE_EXISTING` instead of auto-creating tables.
- **Optional adapters** (each behind its own extra): LangChain
  (`Db2ChatMessageHistory`, `Db2MemoryStore`), OpenAI Agents SDK
  (`Db2Session`), and MCP (`remember`/`recall`/`forget`/`list_memories`
  tools).

## Install

```bash
pip install agent-memory-sdk

# With an adapter:
pip install "agent-memory-sdk[langchain]"
pip install "agent-memory-sdk[openai-agents]"
pip install "agent-memory-sdk[mcp]"
pip install "agent-memory-sdk[all]"      # every adapter
```

Requires Python 3.10+. The `ibm_db` dependency auto-downloads a bundled Db2
CLI driver on install (no separate manual driver install for most
platforms).

## Quickstart: Db2 in Docker

```bash
docker run -d \
  --name db2-dev \
  -e DB2INST1_PASSWORD=passw0rd \
  -e LICENSE=accept \
  -e DBNAME=TESTDB \
  -p 50000:50000 \
  --privileged \
  icr.io/db2_community/db2:12.1.5.0
```

> **Apple Silicon:** add `--platform=linux/amd64` (this image is x86-64
> only). **Image tag:** `12.1.5.0` is pinned because `CREATE VECTOR INDEX`
> became GA in Db2 12.1.5.

The first start takes 3–5 minutes. Watch `docker logs -f db2-dev` until you
see `(*) Setup has completed.` Then set your connection env vars (copy
`.env.example` to `.env`):

```bash
DB2_DATABASE=TESTDB
DB2_HOSTNAME=localhost
DB2_UID=db2inst1
DB2_PWD=passw0rd
```

Run the migrations once against the running container:

```python
from agent_memory_sdk.db.connection import ConnectionPool
from agent_memory_sdk.db.migrate import Migrator

pool = ConnectionPool()      # reads DB2_* env vars
Migrator(pool).run()         # creates all 6 tables + vector indexes
```

Then remember and recall something:

```python
from agent_memory_sdk import MemoryStore, MemoryScope, WorkingMemory

store = MemoryStore(pool)
scope = MemoryScope(agent_id="agent-001", user_id="user-42")

record = store.remember(
    WorkingMemory(agent_id=scope.agent_id, content="Hello!"),
    scope,
)

results = store.working.search(
    query_embedding=[0.1, 0.2, 0.3],  # bring your own EmbeddingProvider
    scope=scope,
    top_k=5,
)
```

See [`project-management/INTEGRATION_TESTING.md`](project-management/INTEGRATION_TESTING.md)
for the full Docker guide (IBM Cloud Db2 alternative, cleanup SQL, CI
notes) and [`examples/`](examples/) for complete runnable scripts.

## Configuration

### Connection-pool sizing guideline

`DB2_POOL_SIZE` (default 5) is the primary scalability ceiling for a single
SDK process. A concrete sizing rule derived from BM-15 sweep results:

| Pool size | Max concurrent ops/s | Queue wait P95 | Exhausted rate |
|-----------|---------------------|----------------|----------------|
| 1         | ~20 ops/s            | >500 ms        | high (>50 VUs) |
| 5         | ~100 ops/s           | <100 ms        | low (<50 VUs)  |
| 10        | ~200 ops/s           | <50 ms         | negligible     |
| 20        | ~400 ops/s           | <30 ms         | negligible     |

> **Note:** These are estimated guidelines derived from BM-15
> (`benchmarks/load/pool_saturation_user.py`). Actual values depend on your
> Db2 server hardware and network latency. Run the sweep against your instance
> to get representative numbers.
>
> Rule of thumb: set `DB2_POOL_SIZE` to the number of concurrent request
> handlers in your application (e.g. Gunicorn workers, asyncio task concurrency
> ceiling / 10). Pool size 5 is appropriate for most development and
> moderate-production workloads. Pool size 10–20 for high-concurrency production.

`ConnectionPoolExhausted` is raised (never hangs) when all connections are
checked out and `DB2_POOL_TIMEOUT` (default 30 s) elapses with no free slot.
Handle it gracefully in your application with a retry or circuit-breaker.

## The five memory types

| Type | Table | Purpose | Typical lifespan |
|---|---|---|---|
| `working` | `working_memory` | Raw current-session/thread turns | Short-lived; TTL'd |
| `episodic` | `episodic_memory` | Summarized past runs/threads/events (usually Consolidator-produced) | Durable |
| `semantic_facts` | `semantic_facts` | Individually extracted, atomic facts about the world/user | Durable; can be superseded |
| `entity_profiles` | `entity_profiles` | Aggregated, dense profile per entity (e.g. one row per user), kept current via `update()` | Durable; updated in place |
| `procedural` | `procedural_memory` | Learned skills/instructions/how-to knowledge, usually agent-scoped | Durable; updated in place |

Each has its own Pydantic model (`WorkingMemory`, `EpisodicMemory`,
`SemanticFact`, `EntityProfile`, `ProceduralMemory`) and its own repository
(`store.working`, `store.episodic`, `store.facts`, `store.profiles`,
`store.procedures`), all reachable through the `MemoryStore` facade.
`store.remember(record, scope)` dispatches to the right repository by model
type and (for `working`/`episodic`) runs the configured `Consolidator`.

## The scoping model

Every row carries four scope columns, enforced hierarchically in every SQL
`WHERE` clause:

```
tenant_id (nullable, broadest)  >  agent_id (required)  >  user_id  >  thread_id / session_id
```

`MemoryScope` is a frozen Pydantic model — `agent_id` is the only required
field; the others narrow the query further. A narrower scope only *increases*
isolation; a caller passing just `agent_id` sees all of that agent's rows
across every user/thread. Scope predicates are always bound SQL parameters,
never string interpolation — a row in scope A cannot be returned by a query
carrying scope B's values.

## Lifecycle features

- **TTL** — set `expires_at` on any record; expired rows are excluded from
  `list_all()`/`search()` reads by default (`include_expired=True` to see
  them). Expiry does not delete data on its own.
- **Forget** — `store.forget(record_id, memory_type, scope)` (or
  `store.<repo>.forget(id, scope)`) tombstones a row via `deleted_at`
  (soft-delete, never a hard `DELETE`).
- **Purge** — `store.purge_expired(scope)` hard-deletes tombstoned
  (`deleted_at IS NOT NULL`) rows. Must be called explicitly (cron /
  `scripts/purge_expired.py`); never runs automatically.
- **Versioning** — every row has a `version` column; `update()` uses
  optimistic concurrency and raises `StaleWriteError` on a version conflict
  (or a cross-scope update attempt).
- **Consolidation** — pass a `Consolidator` callable to `MemoryStore()`;
  it runs synchronously after every `working`/`episodic` write and its
  derived records (facts/profiles/procedures) are persisted automatically.
  Throttle with `consolidate_every_n=N`, or run it out-of-band via
  `scripts/consolidate_pending.py` against the `consolidated_at` column.
- **Reconciliation** — pass a `Reconciler` callable and call
  `store.reconcile("facts", scope)` to detect contradicting semantic facts
  and soft-supersede the losing row (`superseded_by`/`superseded_at`,
  distinct from `forget()`).
- **Confidence** — every record has a `confidence` field (0.0–1.0);
  `search(min_confidence=...)` / `list_all(min_confidence=...)` filter out
  low-certainty rows.

## Known limitations (beta)

- **Erasure/GDPR convenience API**: `forget()` is per-record, per-type; there
  is no `purge_user(user_id, scope)` one-call erasure-all wrapper yet.
- **Bi-temporal reasoning**: TTL + soft-supersession are present, but there
  is no full valid-time/ingestion-time provenance chain or "what did the
  agent believe at time T" query support.
- **Cost/token control**: `consolidate_every_n`, `max_turns`, `top_k`, and
  `min_confidence` bound retrieval and consolidation cost, but there is no
  automatic token-budget context compaction — callers own that above the SDK.
- **Consolidation cadence is per-process**: `consolidate_every_n`'s counter
  is in-memory per `MemoryStore` instance, not shared across multiple app
  processes/replicas.

See [`project-management/DECISIONS.md`](project-management/DECISIONS.md) for
the full, dated history of every design decision.

## Examples

Runnable, dependency-minimal scripts under [`examples/`](examples/), one per
adapter plus a plain core-SDK example — see [`examples/README.md`](examples/README.md).

## Development setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install the package and dev dependencies (editable)
pip install -e ".[dev]"

# 3. Run tests
pytest

# 4. Lint
ruff check .

# 5. Type-check
mypy src
```

> **Note:** `ibm_db` requires the Db2 CLI driver. For a live Db2 connection,
> set the env vars documented in `.env.example` before running any
> integration tests or the `scripts/check_connection.py` script.
> The unit-test suite mocks ibm_db and runs without a live Db2 instance.

## Project management docs

Build prompts, the decision log, architecture doc, and local tracking board
live under [`project-management/`](project-management/) — see
[`project-management/README.md`](project-management/README.md) for an
index. None of it ships with the package.

## Benchmarks

The benchmark suite lives in [`benchmarks/`](benchmarks/) (see
[`benchmarks/README.md`](benchmarks/README.md) for the full architecture).
CI is [`.github/workflows/benchmark-suite.yml`](.github/workflows/benchmark-suite.yml)
("Benchmarks (unified)") — **manual (`workflow_dispatch`) only**, no cron
schedule and no push-to-`main` trigger.

### Triggering a run

1. Open the repository on GitHub and navigate to the **Actions** tab.
2. Select **"Benchmarks (unified)"** in the left-hand workflow list.
3. Click **"Run workflow"**, choose a `suite`, and click **"Run workflow"** again.

`suite` picks which job(s) run:

| `suite` value | What it benches |
|---|---|
| `all` | everything below |
| `micro-benchmarks` | function-level micro-benchmarks, no database |
| `read-write-speed` | core read/write operations against Db2, small scale |
| `tenant-isolation` | correctness under concurrent multi-tenant load |
| `load-scaling` | behavior under increasing concurrent load / pool saturation |
| `memory-quality` | retrieval accuracy + agent-response quality (LLM-judged) |
| `scale-performance` | read/write performance at 500k-row scale |

Each job spins up its own Db2 container automatically — no local setup or
credentials required.

### Getting results

Every job uploads its raw output (pytest-benchmark JSON / Locust CSV) as a
workflow artifact — nothing is reformatted, summarized, or published
anywhere else. To get them: open the run page → scroll to **"Artifacts"**
at the bottom → download the one you want.


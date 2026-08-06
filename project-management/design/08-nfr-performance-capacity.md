# NFR-PERF — Performance & Capacity (EPIC-9, SDD-8)

> **Audience:** developers integrating the SDK, operators sizing deployments, reviewers
> assessing production readiness.
>
> **Empirical data lives in [`../BENCHMARKS.md`](../BENCHMARKS.md).** This document
> describes the *structural* cost model — the mechanisms that determine performance.
> Do **not** copy specific numbers here; they are updated as new benchmark runs are
> appended to that file and must not be duplicated.

---

## 1. Concurrency model

### 1.1 ConnectionPool as the hard concurrency ceiling

Every database operation issued by the SDK — reads and writes — checks out exactly one
connection from [`ConnectionPool`](../../src/agent_memory_sdk/db/connection.py:167) for
the duration of the call. The pool is pre-allocated at startup with a fixed number of raw
`ibm_db` handles held in a bounded `queue.Queue`.

**`pool_size` = maximum number of concurrent Db2 connections.**

No connection is ever shared between two concurrent callers. Each
[`get_connection()`](../../src/agent_memory_sdk/db/connection.py:221) call dequeues one
handle, wraps it in an `ibm_db_dbi.Connection` proxy, yields it to the caller, then
returns the underlying handle to the queue on exit (even if the call raises). The
`queue.Queue` itself is thread-safe; the SDK adds no additional locking.

### 1.2 Pool exhaustion behavior

When all connections are checked out and a new caller calls `get_connection()`, the call
**blocks** — it waits on the internal queue for up to `pool_timeout` seconds. If no
connection is returned within that window,
[`ConnectionPoolExhausted`](../../src/agent_memory_sdk/db/connection.py:88) is raised
immediately rather than blocking indefinitely.

```
pool_timeout   (default 30 s)
│
▼
queue.Queue.get(timeout=pool_timeout)
├── returns handle before timeout → caller proceeds
└── raises queue.Empty → ConnectionPoolExhausted raised to caller
```

Callers that receive `ConnectionPoolExhausted` should treat it as a retriable transient
error. The pool state is fully intact; the error is purely a capacity signal.

### 1.3 Sizing guidance

| Scenario | Recommended `pool_size` |
|---|---|
| Single-threaded script or notebook | 1 |
| WSGI application (e.g. Flask, gunicorn synchronous workers) | ≥ number of worker threads |
| Single gunicorn worker with `--threads N` | ≥ N |
| Multi-process deployment (gunicorn with `--workers W`, each with `--threads T`) | ≥ T per process (each process has its own pool) |
| Async framework (not natively supported; blocking calls must be offloaded) | See note below |

**Rule of thumb:** `pool_size` ≥ maximum number of concurrent
[`store.remember()`](../../src/agent_memory_sdk/store.py:459) or
[`repo.search()`](../../src/agent_memory_sdk/repositories/base.py:1532) callers that can
be in-flight simultaneously within a single process. Under-sizing the pool increases
`ConnectionPoolExhausted` frequency; over-sizing increases the Db2 server's connection
overhead (each connection holds server-side memory).

**Hard cap:** `pool_size` is capped at **20** regardless of configuration (enforced in
[`ConnectionPool.__init__`](../../src/agent_memory_sdk/db/connection.py:182)).

**Configuration:**

| Method | Key / Parameter |
|---|---|
| Environment variable | `DB2_POOL_SIZE` (integer, default `5`) |
| Constructor argument | `ConnectionPool(pool_size=N)` |
| Timeout env var | `DB2_POOL_TIMEOUT` (integer seconds, default `30`) |
| Timeout constructor arg | `ConnectionPool(pool_timeout=N)` |

Environment variables take precedence over constructor arguments when both are set (see
[`_DEFAULT_POOL_SIZE`](../../src/agent_memory_sdk/db/connection.py:97) and the
[`__init__` body](../../src/agent_memory_sdk/db/connection.py:182)).

**Async note:** The SDK makes synchronous blocking calls. Running it inside an async
framework (asyncio, Trio) on the hot path will block the event loop. Wrap calls in
`asyncio.to_thread()` or an equivalent thread-pool executor, and size `pool_size` to
match the concurrency of that executor.

### 1.4 Thread safety of MemoryStore

[`MemoryStore`](../../src/agent_memory_sdk/store.py:248) **is safe to share across
threads** for all database operations, with one documented exception:

- All repository methods (`create`, `search`, `list_all`, `forget`, etc.) acquire a
  connection from the pool per call. Connection acquisition is protected by
  `queue.Queue`'s own thread-safety. There are no additional shared mutable fields in the
  repository layer.

- `MemoryStore` itself holds one piece of mutable in-process state:
  `_consolidate_counters` — a plain Python `dict` used by the
  [`consolidate_every_n`](../../src/agent_memory_sdk/store.py:637) throttle. Under
  concurrent writes from multiple threads this dict is not guarded by a lock. The counter
  is a best-effort throttle, not a hard correctness boundary, so a race (two threads both
  reading `count` before either writes back `count+1`) produces slightly more frequent
  consolidation calls rather than data corruption. If strict per-scope call counting
  matters, use the background worker instead of the inline consolidator.

---

## 2. Search cost model

### 2.1 EXACT vs APPROX

The [`SearchMode`](../../src/agent_memory_sdk/types.py:900) enum controls which Db2
query path is used:

| | `SearchMode.EXACT` / `SearchMode.DEFAULT` | `SearchMode.APPROX` |
|---|---|---|
| **SQL suffix** | `FETCH FIRST n ROWS ONLY` | `FETCH FIRST n ROWS ONLY APPROX` |
| **Algorithm** | Full sequential scan of all eligible rows | DiskANN approximate nearest neighbor index |
| **Complexity** | O(n) per query | Sub-linear (index-dependent) |
| **Result quality** | True top-k — always the mathematically nearest neighbors | Approximate — high recall, not guaranteed exact |
| **RUNSTATS required?** | No | Yes — APPROX does not engage the index unless `RUNSTATS` has been run on the table |
| **Metric constraint** | None — works with any `DistanceMetric` | The query metric **must** match the index's `WITH DISTANCE` clause (all SDK tables use `COSINE`) |
| **Fallback behavior** | N/A | If the Db2 build does not support `APPROX` (SQL0104N), the SDK silently retries with `EXACT` |

**When to use each:**

- **EXACT** (the default): correct for all correctness-critical workloads; the only safe
  choice on tables where `RUNSTATS` has not been run; preferred for small tables where
  the sequential scan is fast enough.

- **APPROX**: appropriate for large tables where the index has been built and
  `RUNSTATS` has been run, and where slight recall approximation is acceptable in
  exchange for lower query latency. Use `DistanceMetric.COSINE` (the default) to ensure
  the metric matches the index.

### 2.2 Two-step ID-then-row fetch pattern

A Db2 12.1.5 fp0 constraint prevents combining `VECTOR_SERIALIZE()` in the `SELECT`
list with `VECTOR_DISTANCE()` in the `ORDER BY` in a single statement. As a result,
every `search()` call is implemented as **two sequential SQL queries**:

```
Step 1 — ID-rank query
  SELECT id
  FROM <table>
  WHERE <scope> AND deleted_at IS NULL [AND ...]
  ORDER BY VECTOR_DISTANCE(embedding, CAST('<vec>' AS VECTOR), COSINE)
  FETCH FIRST n ROWS ONLY [APPROX]

Step 2 — full-row fetch
  SELECT id, content, embedding, ...
  FROM <table>
  WHERE id IN (?, ?, ...)
    AND deleted_at IS NULL
```

After step 2, the result set is reordered in Python to restore the distance rank from
step 1 (the `IN (...)` clause does not preserve ordering).

**Cost implication:** every `search()` call incurs two round-trips to Db2 — one for
ranking, one for content retrieval. This is a **known, documented constraint** of Db2
12.1.5 fp0, not a design choice. The extra round-trip cost is real; plan for it in
latency budgets. See the SQL comment in
[`BaseRepository.search`](../../src/agent_memory_sdk/repositories/base.py:1754) and the
step-7 decision entry in `../DECISIONS.md` for the full rationale.

### 2.3 Hybrid search (`hybrid=True`)

When `hybrid=True` is passed to
[`search()`](../../src/agent_memory_sdk/repositories/base.py:1532), the result ranking
combines vector distance with keyword overlap via Reciprocal Rank Fusion (RRF).

**Query structure:**

1. **Vector pass** (SQL, same two-step pattern above): fetches up to `top_k × 4`
   candidates ordered by vector distance. The over-fetch gives the keyword re-ranker a
   larger pool to work from.
2. **Keyword pass** (Python, no additional SQL): tokenises each candidate's `content`
   and the `query_text` string into lowercase alphanumeric tokens; ranks candidates by
   descending overlap count.
3. **RRF fusion** (Python): fuses the two ranked lists with the standard RRF formula,
   `k = 60` (Cormack et al. 2009), then slices the top `top_k` results.

```
rrf_score(d) = 1 / (60 + vector_rank(d))
             + 1 / (60 + keyword_rank(d))
```

**Cost vs. pure vector search:** two DB round-trips (same as the non-hybrid path), plus
a Python-side tokenisation and sort over the over-fetched candidate set. No additional
SQL query, no Db2 Text Search Extender, no external search engine.

**When to use:** hybrid search improves recall for queries where an important keyword
distinguishes the target from semantically similar neighbors (e.g. exact proper names,
identifiers, version numbers). For purely semantic queries, `hybrid=False` is equivalent
or better.

**Requirement:** pass the raw query string via `query_text` when `hybrid=True`; an empty
`query_text` (the default) produces a zero keyword signal and results reduce to the pure
vector order.

---

## 3. Write cost model

### 3.1 Chunking

Long content is split into overlapping chunks at write time. The gate is controlled by
[`CHUNK_THRESHOLD`](../../src/agent_memory_sdk/repositories/base.py:94) (default 2000
characters):

```
len(content) <= CHUNK_THRESHOLD  →  single embedding on the parent row
                                     (pre-ORC-2 path, one embedding call, one INSERT)

len(content) >  CHUNK_THRESHOLD  →  zero-vector sentinel on the parent row
                                     + N chunk rows in memory_chunks
                                     (N embedding calls + N+1 INSERTs)
```

Chunk parameters (configurable via `MemoryStore` constructor or repository constructor):

| Parameter | Default | Env / Constructor arg |
|---|---|---|
| `chunk_threshold` | 2000 chars | `MemoryStore(chunk_threshold=N)` |
| `chunk_size` | 800 chars | `MemoryStore(chunk_size=N)` |
| `chunk_overlap` | 200 chars | `MemoryStore(chunk_overlap=N)` |

**Write cost scales linearly with chunk count.** For content of length `L >
CHUNK_THRESHOLD`, the number of chunks is approximately:

```
ceil((L - chunk_overlap) / (chunk_size - chunk_overlap))
```

Each chunk requires:
- One embedding provider call (`EmbeddingProvider(chunk_text)`)
- One `INSERT` into `memory_chunks`

The parent row also receives one additional `INSERT` (with a zero-vector sentinel rather
than a real embedding). Total Db2 writes for a chunked record: `N + 1`.

**Search on chunked content** uses the three-step chunk-search path described in
[`BaseRepository.search`](../../src/agent_memory_sdk/repositories/base.py:1569): query
`memory_chunks`, collect unique `source_id` values, fetch parent rows. This adds a third
SQL round-trip relative to the standard two-step path.

### 3.2 Consolidation cadence (`consolidate_every_n`)

The [`consolidate_every_n`](../../src/agent_memory_sdk/store.py:295) parameter on
`MemoryStore` controls how often the inline synchronous
[`Consolidator`](../../src/agent_memory_sdk/types.py:71) is called after writes to
`working` or `episodic` memory.

| Value | Behavior |
|---|---|
| `1` (default) | Consolidator fires on **every** `remember()` call for working/episodic memory |
| `N > 1` | Consolidator fires **every Nth** call per scope (keyed by `(agent_id, user_id, thread_id)`) |

**Cost tradeoff:** consolidation with a real (LLM-backed) `Consolidator` is expensive —
each invocation is an LLM call. `consolidate_every_n > 1` amortises that cost at the
expense of less-fresh derived memories (facts, profiles, procedures) after each write.

**Default is `1`** — full backward compatibility; no change in behavior unless the
parameter is explicitly set.

### 3.3 Known limitation: `consolidate_every_n` counter is not shared across instances (ENH-4)

> **This is a documented production capacity-planning caveat, not a hidden gotcha.**
> It is recorded in `../DECISIONS.md` under the ENH-4 entry.

The per-scope counter that governs `consolidate_every_n` is stored **in-memory on the
`MemoryStore` instance** in a plain Python `dict`
([`_consolidate_counters`](../../src/agent_memory_sdk/store.py:449)). It is **not**
persisted to Db2 and is **not shared across multiple application instances**.

Consequences:

- **Process restart resets all counters to zero.** The Nth-write cadence restarts from
  scratch after every deploy or crash.

- **Multiple gunicorn workers, Kubernetes replicas, or serverless invocations each
  maintain an independent counter.** With `W` workers and `consolidate_every_n=N`, each
  worker fires the consolidator every `N` writes *it handles personally*, not globally
  every `N` writes across all workers. Effective global consolidation frequency is
  roughly `total_writes / (W × N)` — potentially much more frequent than intended.

- **Serverless environments** (functions that spin up and tear down per request) will
  fire the consolidator on **every** write regardless of `consolidate_every_n`, because
  the counter never reaches `N` within a single invocation's lifetime.

**Mitigation for multi-process or serverless deployments:** use the background worker
(`scripts/consolidate_pending.py`) instead of the inline consolidator. The worker uses
the database-backed `consolidated_at` column (migration `0005_consolidated_at.sql`) and
a claim-based lock to coordinate across processes — it is the correct tool for
cross-process consolidation cadence control.

---

## 4. Benchmark methodology summary

Empirical performance data, measured values, and run results live in
[`../BENCHMARKS.md`](../BENCHMARKS.md). The specific numbers in that file are updated as
new runs are appended; they are intentionally not duplicated here.

The benchmark suite is organized into six suites:

| Suite | Name | What it measures |
|---|---|---|
| Suite 1 | Retrieval quality (LongMemEval-shaped) | End-to-end answer accuracy across five long-term memory categories (extraction, multi-session, temporal reasoning, knowledge update, abstention), with-SDK vs. without-SDK baseline |
| Suite 2 | Latency and cost | Per-operation p50/p95/p99 latency for `remember()`, `search()`, and `recall()` at varying table sizes and concurrency levels |
| Suite 3 | Locust isolation under load (BM-13) | Cross-scope data isolation correctness at 100 tenants × 1,000 agents × 200 concurrent virtual users; any scope-leak fires a non-zero exit |
| Suite 4 | Locust scalability sweeps (BM-14) | RPS and latency profile under user-ramp, agent-sweep, and mixed read/write load patterns |
| Suite 5 | Connection-pool saturation (BM-15) | `ConnectionPoolExhausted` behavior under deliberate over-subscription; confirms the pool raises rather than hanging at varying `pool_size` / virtual-user ratios |
| Suite 6 | Agent quality (Microsoft Foundry-shaped, EPIC-21) | LLM-judged agent-level quality metrics (coherence, groundedness, task completion) over multi-turn sessions; nightly only, never a merge gate |

Reproduce any suite by following the instructions in [`../BENCHMARKS.md`](../BENCHMARKS.md)
and `benchmarks/README.md`. All suites require a live Db2 instance; Suite 1 and Suite 6
additionally require an `EmbeddingProvider` and an LLM judge.

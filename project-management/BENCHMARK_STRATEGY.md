# Benchmarking Strategy — agent-memory-sdk

**Status:** Research & planning proposal (no implementation started)
**Date:** 2026-08-04
**Scope:** Phases 1–7 of the benchmarking research task
**Infrastructure assumed:** local Mac (Apple Silicon), GitHub Actions, one live Db2 LUW instance

---

## Executive summary

Five findings drive every recommendation below.

1. **The infrastructure constraint is softer than assumed.** `.github/workflows/ci.yml`
   already boots `icr.io/db2_community/db2:12.1.5.0` on a GHA runner, creates `TESTDB`
   with a 32 KB page size via the `/var/custom` hook, and polls readiness with a
   `CREATE VECTOR INDEX` probe before running 77 integration tests. **Db2 with working
   vector indexes is already a solved problem in CI.** Benchmarks do not need a
   dedicated server or the live Fyre instance for most tiers — they need to reuse this
   job. The live Db2 instance should be reserved for the one thing an ephemeral
   container cannot do: large, persistent, multi-day datasets.

2. **The existing `benchmarks/` folder should be mostly kept, not discarded.** It is not
   outdated so much as *mis-scoped*: it is a hand-rolled framework (custom timing,
   custom report renderer, custom runner, custom judge) doing a job three maintained
   OSS tools do better. But four of its components are genuinely reusable and should be
   carried forward (`common/embedding_providers.py`, `common/scope_gen.py`,
   `common/cost_tracking.py`, `isolation_load/run.py`). The parts to discard are the
   ones that duplicate framework functionality: `common/timing.py`,
   `common/report.py`, `common/llm_judge.py`, and the synthetic
   `retrieval_quality/dataset.py`.

3. **Do not build a benchmarking framework. Assemble three maintained ones.**
   `pytest-benchmark` + `github-action-benchmark` for micro-performance and PR
   regression gating; `Locust` (headless, library mode) for concurrency and scale;
   the official **LongMemEval** harness and dataset (Apache-2.0) for retrieval quality.
   Everything else is glue, and the glue is small because all three are Python and
   pytest-native.

4. **The current retrieval-quality suite has a methodology problem the repo already
   diagnosed.** `BENCH-1` found that Run A's scores were partly attributable to *judge
   non-determinism* — a local llama3.1:8b answering CORRECT on an empty context. A
   benchmark whose primary metric is a non-deterministic LLM verdict on a 50-question
   synthetic dataset cannot gate CI and cannot be compared to published figures. The fix
   is to **split the metric in two**: deterministic IR metrics (Recall@k, MRR, nDCG@k)
   that need no LLM and can run on every PR, and LLM-judged end-to-end accuracy on the
   *real* LongMemEval dataset that runs nightly/weekly and is never a merge gate.

5. **GHA wall-clock is too noisy to gate on directly.** Shared runners vary 2–3× on
   identical work. Absolute latency numbers must come from the Mac and the live Db2;
   CI gating must use either instruction-count measurement (CodSpeed/Valgrind, which is
   noise-free) or *relative/ratio* assertions (hybrid vs. vector, chunked vs. unchunked,
   round-trips per operation) that are invariant to runner speed.

**Bottom line:** roughly **3–4 weeks of engineering** across 7 epics and 24 stories, of
which about a third is deleting and re-pointing existing code rather than writing new code.

---

# Phase 1 — Capability inventory

Derived from a full scan of `src/` (9,764 LOC), 1,168 test functions across 28 unit and
21 integration test modules, `examples/`, `README.md`, `project-management/*.md`, the 7
migration `.sql` files, `.github/workflows/`, and git history.

## 1.1 Architecture at a glance

```
MemoryStore (facade, 2,488 LOC)
├── working    → WorkingMemoryRepository      → working_memory
├── episodic   → EpisodicMemoryRepository     → episodic_memory
├── facts      → SemanticFactRepository       → semantic_facts
├── profiles   → EntityProfileRepository      → entity_profiles
├── procedures → ProceduralMemoryRepository   → procedural_memory
└── chunks     → ChunkRepository              → memory_chunks   (shared, ORC-2)
        ↑ all inherit BaseRepository (1,976 LOC — the real hot path)

Thread            — bound-scope convenience facade (142 LOC)
ConnectionPool    — bounded queue of raw ibm_db handles, no built-in pooling in driver
Migrator          — 7 .sql migrations, SchemaPolicy {CREATE_IF_NECESSARY, REQUIRE_EXISTING}
Adapters          — LangChain, OpenAI Agents, MCP, MS Agent Framework
```

**Pluggable protocols (all `types.py`):** `EmbeddingProvider`, `Consolidator`,
`Reconciler`, `IngestResolver`, `MemoryExtractor`, `Summarizer` — each with a `NoOp*`
default. Every one is a benchmark axis: the SDK's cost profile changes completely
depending on whether a protocol is wired to a real LLM or left as `NoOp`.

## 1.2 Full capability inventory

### A. Write / storage path

| # | Capability | Location | Perf-sensitive? |
|---|---|---|---|
| A1 | `remember()` type dispatch across 5 repos | `store.py:459` | Medium |
| A2 | `create()` — INSERT with inlined `CAST('...' AS VECTOR(d,FLOAT32))` literal | `base.py:842` | **Critical** — vector literal is a ~20 KB SQL string at dim 1536 |
| A3 | Content-hash dedup (ENH-2) — pre-INSERT `SELECT ... FETCH FIRST 1` | `base.py:901` | **High** — extra round-trip per write |
| A4 | `update()` + optimistic versioning → `StaleWriteError` | `base.py:1219` | Medium |
| A5 | Chunking (ORC-2): `chunk_threshold`/`chunk_size`/`chunk_overlap`, zero-vector sentinel on parent | `base.py:934`, `chunks.py` | **Critical** — N embed calls + N INSERTs per long record |
| A6 | Chunk rewrite on `update()` (delete-by-source + re-insert) | `base.py:1332` | High |
| A7 | Ingest resolution (PIPE-2): pre-write `search(top_k=resolver_k)` → ADD/UPDATE/DELETE/NOOP | `store.py`, `types.py:438` | **Critical** — turns every write into a read+write |
| A8 | Inline consolidation + `consolidate_every_n` per-scope throttle (ENH-4) | `store.py` | High (LLM-bound when real) |
| A9 | Background consolidation worker + `_claim_consolidated()` claim protocol | `scripts/consolidate_pending.py`, `base.py:1441` | High — contention point |
| A10 | `add_messages()` + `MemoryExtractor` (THRD-1/THRD-5) | `store.py:1632` | High — batch write path |
| A11 | Convenience writes: `add_memory()`, `add_user()`, `add_agent()` (THRD-2) | `store.py:2180+` | Low |
| A12 | Confidence scoring (0.0–1.0) on write | `models.py` | Low |

### B. Read / retrieval path

| # | Capability | Location | Perf-sensitive? |
|---|---|---|---|
| B1 | `search()` vector — `VECTOR_DISTANCE` + `FETCH FIRST n ROWS ONLY` | `base.py:1518` | **Critical** |
| B2 | 4 distance metrics: COSINE, EUCLIDEAN, DOT, MANHATTAN | `types.py:876` | High |
| B3 | 3 search modes: EXACT, APPROX (index-backed ANN), DEFAULT | `types.py:900` | **Critical** — recall/latency tradeoff |
| B4 | Chunk search + parent resolution (`search_chunks=True`) | `chunks.py:search_chunks` | **Critical** |
| B5 | Hybrid retrieval — RRF (k=60) fusion of vector rank + Python token-overlap rank (PIPE-1) | `base.py:605`, `_rrf_fuse` | **Critical** — Python-side ranking is O(candidates) in-process |
| B6 | Metadata filters (ORC-3): exact, `$not`, `$array_contains`, `$array_contains_any` — implemented as `JSON_VALUE` + triple-`LOCATE` on raw text | `base.py:282` | **Critical** — `LOCATE` on `VARCHAR(4096)` is non-sargable; selectivity sweep required |
| B7 | `list_all()` — limit/offset pagination, `include_expired`, `min_confidence`, `metadata_filter` | `base.py:1051` | High — two SQL shapes (offset=0 vs. offset>0) |
| B8 | `get_by_id()` | `base.py:1019` | Low |
| B9 | Multi-type facade `store.search()` across `record_types` | `store.py:2022` | **Critical** — fan-out: N repo queries per call |
| B10 | Scope-matching modes (THRD-10): `exact_agent_match`, `exact_thread_match` | `store.py:2022` | High — changes predicate selectivity |
| B11 | `get_context_card()` (PIPE-4): recent turns + optional long-term blend, `min_results_by_type`, `long_term_top_k` | `store.py:1353` | **Critical** — composite, multi-query |
| B12 | `get_summary()` (THRD-4) + `except_last`, `token_budget` | `store.py:1926` | High |
| B13 | `get_messages(start, end)` range read | `store.py:1719` | Medium |
| B14 | On-the-fly query embedding via configured `EmbeddingProvider` | `store.py` | **Critical** — often dominates wall-clock |

### C. Lifecycle & governance

| # | Capability | Location |
|---|---|---|
| C1 | `forget()` — soft delete (`deleted_at`) | `base.py:1177` |
| C2 | TTL: `expires_at` + explicit `purge_expired()` per scope | `base.py:1340`, `store.py:737` |
| C3 | `erase_all()` + `ErasureReport` (PIPE-5) — hard erase incl. orphan chunks | `store.py:776` |
| C4 | `delete_user()` / `delete_agent()` with `cascade` (THRD-7) | `store.py:1822`, `:1876` |
| C5 | `delete_memory()` / `delete_message()` (THRD-8) | `store.py:1778`, `:1744` |
| C6 | `delete_thread()` | `store.py:2459` |
| C7 | `reconcile()` + soft-supersession (`superseded_at`) via `Reconciler` (ENH-3) | `store.py:1218` |
| C8 | `export_scope()` (streaming `Iterator`) / `import_scope()` (PIPE-6) | `store.py:868`, `:980` |
| C9 | Scope-import validation → `ScopeImportError`, `ScopeMismatchError` | `exceptions.py` |

### D. Isolation & multi-tenancy

| # | Capability |
|---|---|
| D1 | `MemoryScope` 4-level hierarchy (`tenant_id`/`agent_id`/`user_id`/`thread_id`) enforced in every SQL `WHERE` |
| D2 | `_scope_predicates()` — hierarchical predicate builder, `agent_id` mandatory |
| D3 | Zero cross-scope leakage invariant across search, list, delete, export |
| D4 | Scope replication onto `memory_chunks` for pre-filter-before-vector-distance |

### E. Concurrency & async

| # | Capability | Notes |
|---|---|---|
| E1 | `ConnectionPool` — bounded `queue.Queue`, `DB2_POOL_SIZE` (default 5, max 20), `DB2_POOL_TIMEOUT` (30 s) → `ConnectionPoolExhausted` | **Primary scalability ceiling** |
| E2 | Async facade: `search_async`, `add_messages_async`, `get_context_card_async` — `asyncio.to_thread` wrappers | Thread-pool bound, not true async |
| E3 | Thread-safety of `MemoryStore` under concurrent use | Only `_consolidate_counters` is mutable shared state |
| E4 | Background consolidation claim (`_claim_consolidated`) under contention | Row-level claim race |
| E5 | No explicit transaction API — commit granularity is per-operation | Gap worth documenting |

### F. Schema, migration, infrastructure

| # | Capability |
|---|---|
| F1 | `Migrator.run()` / `.validate()` / `.status()`, 7 ordered `.sql` files |
| F2 | `SchemaPolicy.CREATE_IF_NECESSARY` vs. `REQUIRE_EXISTING` |
| F3 | Db2 `VECTOR(1536, FLOAT32)` columns, `NOT NULL`, zero-vector sentinel |
| F4 | `CREATE VECTOR INDEX ... WITH DISTANCE COSINE` (DiskANN) on 6 tables |
| F5 | Scope composite indexes + `expires_at` indexes (non-partial — Db2 12.1.5 limitation) |
| F6 | `embedding_dim` configurable at `MemoryStore` construction |

### G. Adapters

| # | Capability |
|---|---|
| G1 | LangChain: `Db2ChatMessageHistory` (`messages`/`add_message(s)`/`clear`), `Db2MemoryStore` (`mget`/`mset`/`mdelete`/`yield_keys`) |
| G2 | OpenAI Agents: `Db2Session` (`add_items`/`get_items`/`pop_item`/`clear_session`/`recall_episodes`) |
| G3 | MCP server: 4 tools — `remember`, `recall`, `forget`, `list_memories` |
| G4 | MS Agent Framework: `MemoryStoreContextProvider` (`before_run`/`after_run`), `MemoryStoreHistoryProvider` (`get_messages`/`save_messages`) |

### H. Known performance-relevant implementation constraints

These are documented in-code and materially shape what is worth measuring:

- **Vector values cannot be bound as parameters.** Db2 12.1.5 fp0 raises `SQL0901N` for
  `TO_VECTOR(?)` / `CAST(? AS VECTOR)`. Every vector is inlined as a SQL string literal —
  at dim 1536 that is a ~20 KB statement per INSERT and per search. This is almost
  certainly a top-3 latency contributor and **has never been measured**.
- **Metadata filter values cannot be bound either** — binding `JSON_VALUE(col, path) = ?`
  segfaults the `ibm_db` C extension. All values are inlined, and `$array_contains` uses
  three `LOCATE()` calls on the raw `VARCHAR(4096)` column. Non-sargable by construction;
  cost scales with rows scanned, not rows matched.
- **`ibm_db_dbi` has no pooling.** The SDK's own bounded queue is the only pool. Pool
  size 5 (default) is the concurrency ceiling for a single process.
- **`content` is `CLOB(65536)`, `metadata` is `VARCHAR(4096)`** — CLOB read overhead on
  wide result sets is unmeasured.
- **Partial/filtered indexes are unsupported** on this Db2 build, so `ix_*_expires`
  indexes every row including `NULL`s.

---

# Phase 1b — Audit of the existing `benchmarks/` folder

2,353 LOC across 3 suites. Verdict per component:

| Component | LOC | Verdict | Rationale |
|---|---|---|---|
| `common/embedding_providers.py` | 199 | **KEEP** | Hashing / sentence-transformers / Ollama providers behind one factory. Zero-dependency `hashing` fallback is exactly what CI needs (deterministic, offline, no model download). High value, framework-independent. |
| `common/scope_gen.py` | 58 | **KEEP** | Deterministic multi-tenant scope generation. Needed by every future suite. |
| `common/cost_tracking.py` | 109 | **KEEP** | Token/cost estimation hook. Nothing off-the-shelf does this for the SDK's protocol hooks. |
| `isolation_load/run.py` | 148 | **KEEP (port)** | The cross-scope leakage assertion under real concurrent threads is the single most valuable thing in the folder — a *correctness* property that only fails under load. Port the assertions into the Locust suite; retire the bespoke thread pool. |
| `common/timing.py` | 67 | **DISCARD** | Hand-rolled percentile collection. `pytest-benchmark` does this with warmup, outlier rejection, calibration, and JSON export. |
| `common/report.py` | 296 | **DISCARD** | Hand-rolled Markdown renderer. `github-action-benchmark` + `pytest-benchmark --benchmark-json` replaces it and adds historical trend + PR alerting. |
| `common/llm_judge.py` | 194 | **DISCARD** | Non-deterministic verdicts are the documented root cause of BENCH-1's confounded Run A. Replace with LongMemEval's own judge for the offline tier and deterministic IR metrics for the CI tier. |
| `retrieval_quality/dataset.py` | 290 | **DISCARD** | Synthetic, template-generated, 50 questions. The report itself states results are "explicitly NOT comparable to vendor-reported LongMemEval figures." The real dataset is Apache-2.0 and free. |
| `retrieval_quality/run.py` | 415 | **REWRITE** | Orchestration logic is sound; it should drive the real dataset and emit IR metrics alongside judged accuracy. |
| `retrieval_quality/consolidator.py` / `reconciler.py` | 445 | **KEEP** | BENCH-3a/3b built real extraction + supersession logic. Reusable as the "SDK fully wired" configuration under any harness. |
| `latency_cost/run.py` | 112 | **DISCARD (replace)** | 50 ops, no warmup, no statistical treatment. Direct `pytest-benchmark` replacement. |
| `scripts/run_benchmarks.py` | ~200 | **DISCARD** | Bespoke CLI runner. `pytest -m benchmark` + `locust -f` replace it. |
| `tests/test_benchmarks_unit.py` | 25 tests | **PRUNE** | Tests for the discarded harness; keep only what covers retained modules. |

**Net:** ~1,100 LOC retained, ~1,250 LOC deleted, and the deleted portion is precisely
the "custom benchmarking framework" the constraints say to avoid maintaining.

---

# Phase 2 — Benchmark landscape review

Twelve candidates evaluated. Criteria: maintenance, adoption, extensibility, integration
cost, CI/GHA compatibility, local Mac support, ability to drive a live Db2 instance,
coverage of *this* SDK's capabilities, scale testing, metrics emitted, license.

### 2.1 Micro-performance & CI regression gating

| Candidate | Maintained | License | GHA | Local Mac | Drives Db2 | SDK coverage | Verdict |
|---|---|---|---|---|---|---|---|
| **pytest-benchmark** | Yes | BSD-2 | Yes | Yes | Yes (any Python callable) | Any public method | **ADOPT** |
| **github-action-benchmark** | Yes, active | MIT | Native | N/A | N/A (consumes JSON) | N/A | **ADOPT** |
| **CodSpeed / pytest-codspeed** | Yes, active | Apache-2.0 (client) | Native action | Yes | Partially — Valgrind instruction counting distorts I/O-bound work | SDK-side CPU only | **ADOPT for the no-DB tier only** |
| **Bencher (bencher.dev)** | Yes, active | Apache-2.0/MIT (core) | GH Action | Yes | N/A | N/A | **Defer** — overlaps github-action-benchmark; self-hosting is extra ops for no added capability here |
| **asv (airspeed velocity)** | Yes | BSD | Awkward | Yes | Yes | Any | **Reject** — its own env/matrix management duplicates what `uv`/pip already do here |

**Note on CodSpeed:** it measures simulated CPU cycles under Valgrind, which makes it
*noise-free* on shared runners — precisely the problem GHA wall-clock has. But it is
useless for measuring Db2 round-trips (network I/O under Valgrind is meaningless and
~20× slower). It is the right tool for exactly one tier: SDK-side CPU work with a fake
connection (SQL string building, metadata-filter compilation, RRF fusion, chunk
splitting, Pydantic validation, vector serialization). That tier is real: at dim 1536,
`_vec_to_str` builds a ~20 KB string on every single write and search.

### 2.2 Load, concurrency, and scale

| Candidate | Maintained | License | GHA | Drives Db2 via SDK | Verdict |
|---|---|---|---|---|---|
| **Locust** | Yes, very active | MIT | Yes (headless, `--csv`, exit codes) | Yes — arbitrary Python `User` classes | **ADOPT** |
| **k6** | Yes | AGPL-3.0 (OSS core) | Yes | No — Go/JS runtime cannot call the Python SDK | **Reject** |
| **JMeter** | Yes | Apache-2.0 | Heavy | Only via JDBC, bypassing the SDK | **Reject** — measures Db2, not the SDK |
| **Molotov** | Low activity | Apache-2.0 | Yes | asyncio-only; same blocking-driver issue as Locust with none of the tooling | **Reject** |
| **HammerDB** | Yes, active (v6.0) | GPL-3.0 | Poor (Tcl, GUI-oriented) | Yes — **Db2 is a first-class target** | **Reject as SDK benchmark; consider as a one-off Db2 capacity baseline** |

**On HammerDB:** it genuinely supports Db2 LUW for TPROC-C/TPROC-H. But those are
TPC-derived OLTP/analytic schemas — they measure the *database*, not this SDK's API.
The only legitimate use is a single calibration run to establish "what this Db2 instance
can do at all," so SDK numbers can be read against a known hardware ceiling. That is a
nice-to-have, not a dependency, and GPL-3.0 makes vendoring it into an Apache-2.0 repo
inadvisable (running it as an external tool is fine).

**On Locust + `ibm_db`:** Locust's concurrency model is gevent greenlets, and `ibm_db`
is a blocking C extension that gevent cannot monkeypatch. A naive Locust user would
stall the whole greenlet hub. The standard, documented workaround is to dispatch the
blocking call through `gevent.get_hub().threadpool.apply(...)` — roughly five lines in a
shared base `User` class. This is not speculative: the repo's own
`benchmarks/isolation_load/run.py` already proves `ibm_db` works correctly under Python
threads. This is the single "minimal extension" the recommendation requires.

### 2.3 Retrieval quality / memory benchmarks

| Candidate | Maintained | License | Questions | Runs offline | Integration cost | Verdict |
|---|---|---|---|---|---|---|
| **LongMemEval** (xiaowu0162, ICLR 2025) | Yes | Apache-2.0 | 500 (`_S` ≈115K tokens/40 sessions; `_M` ≈500 sessions) | Yes (local judge) | Low — dataset is JSON on HF; harness is a scorer | **ADOPT** |
| **LongMemEval-V2** (2026) | Yes, new | Apache-2.0 | Multimodal web-agent trajectories | Partly | High — multimodal, agentic | **Defer to backlog** |
| **LoCoMo** | Yes | Varies by mirror | 1,540 (single/multi-hop, temporal, open-domain) | Yes | Low–medium | **ADOPT in phase 2** |
| **BEAM** (via mem0ai/memory-benchmarks) | Yes | Apache-2.0 | 2,000+ at 100K–10M tokens | Yes | Medium — 10M-token scale is expensive | **Defer** — the right benchmark once the SDK has a scale story |
| **mem0ai/memory-benchmarks** | Yes (79★, Apache-2.0) | Apache-2.0 | LoCoMo + LongMemEval + BEAM | Yes (Ollama config) | **Medium–high** — pipeline is Mem0-client-coupled (`benchmarks/common/` assumes a Mem0 server); README invites PRs for new backends | **Borrow the shape, not the code** |
| **RAGAS** | Yes | Apache-2.0 | N/A (metrics library) | Needs an LLM | Low | **Optional** — context precision/recall for the offline tier |
| **BEIR** | Maintenance mode | Apache-2.0 | IR datasets | Yes | Medium | **Reject** — document-retrieval IR, not conversational memory |
| **OpenAI Evals** | Low activity | MIT | N/A | No (OpenAI-centric) | Medium | **Reject** — model-eval framework; wrong layer, and adds an API-key dependency |

**Key point on `mem0ai/memory-benchmarks`:** it is the closest thing to a turnkey answer,
and its README explicitly invites backend contributions. But its `Ingest → Search →
Evaluate` pipeline talks to a Mem0 server over HTTP; adapting it means either writing a
Mem0-API-compatible shim over `MemoryStore` (real work, and a permanently misleading
abstraction) or forking `benchmarks/common/`. The better deal is to take its **three-stage
pipeline shape and its per-category reporting format** — both are good design, and both
are already half-present in `benchmarks/retrieval_quality/run.py` — while pointing them
at the LongMemEval dataset directly.

### 2.4 Vector-database benchmarks

| Candidate | Maintained | License | Db2 support | Verdict |
|---|---|---|---|---|
| **VectorDBBench** (Zilliz) | Yes, active | MIT | **No** — Milvus, Zilliz, Elasticsearch, Pinecone, Qdrant, Weaviate, pgvector, Redis, Chroma | **Defer** |
| **ANN-Benchmarks** | Yes (Aumüller/Bernhardsson/Faithfull) | MIT | No — benchmarks ANN *algorithms*, not DB products | **Reject** |
| **VIBE** (2025) | New | Varies | No | **Reject** — research artifact |

**On VectorDBBench:** its client module is explicitly designed for extension, and writing
a Db2 client would answer one question nothing else answers well — *how does Db2's DiskANN
vector index behave on recall-vs-QPS at 1M+ vectors, versus pgvector/Qdrant?* That is a
genuinely valuable, publishable number. It is also a multi-day contribution that measures
**Db2, not this SDK**, and it duplicates zero of the governance/scoping/lifecycle surface
that makes this SDK what it is. **Backlog it as a strategic-marketing story, not a
validation story.**

### 2.5 Supporting instrumentation

| Tool | Purpose | License | Verdict |
|---|---|---|---|
| **psutil** | RSS / CPU% sampling during load runs | BSD-3 | **ADOPT** |
| **pytest-memray** (Bloomberg) | Per-test allocation limits, leak detection | Apache-2.0 | **ADOPT** — Linux + macOS only, which matches the constraint set exactly |
| **ranx** or hand-rolled | Recall@k / MRR / nDCG@k | Apache-2.0 | **ADOPT** (30 LOC hand-rolled is also acceptable) |
| **Db2 `MON_GET_PKG_CACHE_STMT`** | Server-side statement cost, rows read, index usage | N/A (built-in) | **ADOPT** — the only way to see whether `LOCATE` filters are scanning |
| **Counting cursor proxy** | DB round-trips per SDK call | N/A (≈40 LOC) | **BUILD** — no OSS tool does this; smallest possible custom piece |

---

# Phase 3 — Recommendation

## The stack

```
┌─ Tier 0: SDK-side CPU (no database) ────────────────────────────────┐
│  pytest-benchmark + pytest-codspeed, fake DBAPI connection          │
│  → vector serialization, metadata-filter SQL compilation, RRF       │
│    fusion, chunk splitting, Pydantic validation, scope predicates   │
│  Noise-free (instruction counting). Runs on every PR in ~90 s.      │
└─────────────────────────────────────────────────────────────────────┘
┌─ Tier 1: Single-op latency against real Db2 ────────────────────────┐
│  pytest-benchmark, `-m benchmark`, Db2 container (reuse ci.yml job) │
│  → remember/search/list_all/context_card/summary/export, per config │
│  JSON → github-action-benchmark → trend + PR alert                  │
└─────────────────────────────────────────────────────────────────────┘
┌─ Tier 2: Concurrency & scale ───────────────────────────────────────┐
│  Locust headless + gevent threadpool wrapper + psutil sampler       │
│  → user ramps, mixed R/W, pool exhaustion, long sessions,           │
│    cross-scope leak assertions under load (ported from isolation_load)│
└─────────────────────────────────────────────────────────────────────┘
┌─ Tier 3: Retrieval quality ─────────────────────────────────────────┐
│  LongMemEval (real dataset, Apache-2.0) → LoCoMo later              │
│  Deterministic IR metrics (Recall@k/MRR/nDCG) — CI-safe             │
│  LLM-judged accuracy (local Ollama) — offline/nightly only          │
└─────────────────────────────────────────────────────────────────────┘
```

## Why this is the best fit

**It reuses infrastructure that already exists and already works.** The Db2 container job
in `ci.yml` took real effort to get right — the `/var/custom` page-size hook, the
`CREATE VECTOR INDEX` catalog-agent-drain probe, the SQL1476N workaround. Every tier that
needs a database inherits that job verbatim. Nothing new to operate.

**It is pytest-native end to end.** Tiers 0 and 1 are pytest markers on functions that
look like the 1,168 tests already in the repo. Contributors do not learn a new tool; CI
does not gain a new runner; `make benchmark` becomes `pytest -m benchmark`.

**It separates deterministic from non-deterministic metrics.** This directly fixes the
methodology flaw BENCH-1 found. Deterministic metrics (latency, round-trips, Recall@k,
leak count) gate merges. Non-deterministic metrics (LLM-judged accuracy) inform, and are
reported with the judge/model/seed stamped — never as a gate.

**It gates on the right signal.** GHA wall-clock is noisy, so Tier 0 uses instruction
counting (immune), and Tiers 1–2 gate on ratios and counts (round-trips per operation,
leak count, error rate) plus generous absolute thresholds. Precise absolute latency comes
from the Mac and the live Db2, where the environment is controlled.

**Licenses are all compatible** with this Apache-2.0 repo: BSD-2 (pytest-benchmark), MIT
(Locust, github-action-benchmark), Apache-2.0 (LongMemEval, pytest-memray, CodSpeed
client), BSD-3 (psutil). The one GPL-3.0 tool (HammerDB) is only ever invoked as an
external binary, never vendored.

## What these frameworks already cover

- Warmup, calibration, outlier rejection, min/max/mean/median/stddev/IQR, rounds/iterations control — **pytest-benchmark**
- P50/P95/P99, RPS, failure rate, ramping user counts, distributed workers, CSV/HTML export, non-zero exit on threshold breach — **Locust**
- Historical trend storage on `gh-pages`, PR comment alerts, configurable `alert-threshold` and `fail-threshold` — **github-action-benchmark**
- 500 real questions across 6 memory ability categories, published comparison points, an evaluation harness and judge prompt — **LongMemEval**
- Per-test peak allocation and leak limits — **pytest-memray**
- Process RSS/CPU sampling — **psutil**

## What is genuinely missing, and must be built

Four things. All small.

1. **DB round-trip counter** (≈40 LOC). A `CountingConnection`/`CountingCursor` proxy
   wrapping the pool, exposing `execute` counts per SDK call. Nothing off the shelf knows
   what an "SDK call" is. This is the highest-value custom piece: it makes A3 (dedup
   pre-SELECT), A7 (ingest-resolver pre-search), B9 (multi-type fan-out), and B11
   (context-card composite) *countably* measurable and gateable, independent of runner
   speed.
2. **Locust gevent-threadpool `User` base class** (≈20 LOC). The `ibm_db` blocking-driver
   workaround described above.
3. **LongMemEval → `MemoryScope` adapter** (≈150 LOC). Maps a LongMemEval session/haystack
   into `add_messages()` calls under a per-question scope, and a question into a
   `store.search()` call — plus the reverse mapping to compute Recall@k against the
   dataset's labelled evidence sessions.
4. **Dataset seeding fixtures** (≈120 LOC). Deterministic, size-parameterized corpus
   generation (1k / 50k / 500k rows) with controlled metadata cardinality so filter
   selectivity can be swept. Extends the existing `common/scope_gen.py`.

**Total new infrastructure: ~330 LOC.** Everything else is configuration and test bodies.

## What must not be built from scratch

- A timing/percentile harness → `pytest-benchmark`
- A report renderer or results database → `github-action-benchmark` on `gh-pages`
- A concurrency driver / worker pool → Locust
- A memory-benchmark dataset → LongMemEval, then LoCoMo
- An LLM-judge framework → LongMemEval's own scorer
- An ANN recall/QPS harness → VectorDBBench, if and when that question matters
- A results web UI → the `github-action-benchmark` GitHub Pages chart

---

# Phase 4 — Gap analysis matrix

**Legend.** *Benchmark support*: F = framework covers it directly, F+ = framework +
thin glue, C = requires the custom pieces listed in Phase 3, N = no existing framework.
*Priority*: P0 must exist before beta, P1 before GA, P2 opportunistic.

| # | SDK capability | Bench support | Existing coverage today | Additional work required | Pri | Perf metrics | Functional validation | Scale validation |
|---|---|---|---|---|---|---|---|---|
| A1 | `remember()` dispatch (5 types) | F | `latency_cost` (n=50, working+facts only) | pytest-benchmark param over all 5 types | P0 | P50/95/99, ops/s | ✅ 1,168 unit+integration tests | Locust write ramp |
| A2 | `create()` + 20 KB inlined vector literal | F+ / C | **None** | Tier-0 bench on `_vec_to_str`; dim sweep 384/768/1536/3072 | **P0** | ns/call, bytes/stmt, P50/95/99 | ✅ existing | Row-count sweep 1k→500k |
| A3 | Content-hash dedup pre-SELECT | C | Correctness only | Round-trip counter; dedup-on vs. off delta | **P0** | Δ latency, round-trips/write | ✅ `test_dedup_confidence` | Hit-rate sweep |
| A4 | `update()` + `StaleWriteError` | F | Correctness only | pytest-benchmark; contention case in Locust | P1 | P50/95/99, conflict rate | ✅ existing | Concurrent-writer ramp |
| A5 | Chunking (ORC-2) | F+ | `test_chunking` (correctness) | Bench across content sizes 1k/5k/20k/60k chars | **P0** | Latency, embed calls, chunks/record | ✅ `test_orc2` (46 tests) | Chunked-corpus search at scale |
| A6 | Chunk rewrite on update | F+ | Correctness only | Bench update-with-chunks vs. without | P1 | Latency, delete+insert count | ✅ existing | — |
| A7 | Ingest resolution (PIPE-2) | C | Correctness only | Round-trip counter + latency with/without resolver | **P0** | Δ latency, extra round-trips | ✅ `test_pipe2` (49) | Resolver under load |
| A8 | Inline consolidation + `consolidate_every_n` | F+ | `MockConsolidator` cost demo | Bench throttle 1/5/25; cost-tracking hook retained | P1 | Latency, est. tokens/$ | ✅ `test_enh4` (37) | Throughput vs. N |
| A9 | Background consolidation worker + claim | N → C | `test_consolidation_worker` (7) | Locust scenario: k workers contending on claim | P1 | Claim conflict rate, drain rate | ✅ existing | **Multi-worker contention** |
| A10 | `add_messages()` + `MemoryExtractor` | F | `test_thrd1`/`thrd5` | Batch-size sweep 1/10/100/1000 | **P0** | Latency/msg, ops/s | ✅ existing | Batch scale |
| A11 | `add_memory`/`add_user`/`add_agent` | F | `test_thrd2` (18) | Add to bench param set | P2 | P50/95/99 | ✅ existing | — |
| A12 | Confidence scoring | F | `test_dedup_confidence` | Covered by A1 | P2 | — | ✅ existing | — |
| B1 | `search()` vector | F | `latency_cost` (n=50) | Full percentile bench; corpus-size sweep | **P0** | P50/95/99, QPS | ✅ existing | **1k→500k rows** |
| B2 | 4 distance metrics | F | Correctness only | Parametrized bench over metrics | P1 | Per-metric latency | ✅ existing | — |
| B3 | APPROX vs. EXACT vs. DEFAULT | F+ / C | Correctness only | **Recall@k measurement** — APPROX recall vs. EXACT ground truth | **P0** | Latency, **recall loss** | ⚠️ partial | **Critical at scale** |
| B4 | Chunk search + parent resolution | F+ | `test_chunking` | Bench chunk vs. parent search paths | **P0** | Latency, parent-resolve round-trips | ✅ existing (ORC-2 routing bug was found here) | Chunked corpus |
| B5 | Hybrid RRF (PIPE-1) | F | `test_pipe1` (25), integration (5) | Bench hybrid vs. vector-only; Python-side ranking cost | **P0** | Δ latency, Recall@k, nDCG@k | ✅ existing | Candidate-set growth |
| B6 | Metadata filters (ORC-3) | F+ / C | `test_orc3` (57) | **Selectivity sweep** (0.1%/1%/10%/50% match) + `MON_GET_PKG_CACHE_STMT` rows-read | **P0** | Latency vs. selectivity, rows read | ✅ existing | **Non-sargable `LOCATE` at scale** |
| B7 | `list_all()` pagination | F | `test_repositories` | Offset sweep 0/1k/10k/100k — two SQL shapes | **P0** | Latency vs. offset | ✅ existing | Deep-pagination cliff |
| B8 | `get_by_id()` | F | ✅ | Baseline bench | P2 | P50/95/99 | ✅ existing | — |
| B9 | Multi-type `store.search()` fan-out | C | `test_thrd3` (26) | Round-trip counter across `record_types` | **P0** | Latency, queries/call | ✅ existing | Fan-out at scale |
| B10 | Scope-matching modes (THRD-10) | F+ | `test_thrd10` (24), integration (23) | Bench exact vs. non-exact predicate selectivity | P1 | Latency delta | ✅ existing | Wide-scope corpus |
| B11 | `get_context_card()` (PIPE-4) | C | `test_pipe4` (19) | Round-trip counter; `include_long_term` on/off | **P0** | Latency, queries/call, tokens | ✅ existing | Long threads |
| B12 | `get_summary()` + `token_budget` | F | `test_thrd4` (22) | Bench across thread lengths 10/100/1000 turns | P1 | Latency, tokens | ✅ existing | **Long sessions** |
| B13 | `get_messages(start,end)` | F | ✅ | Range sweep | P1 | Latency vs. range | ✅ existing | Long threads |
| B14 | On-the-fly query embedding | F | Implicit | Isolate embed cost from DB cost | **P0** | ms embed vs. ms DB | ✅ existing | — |
| C1 | `forget()` soft delete | F | `test_lifecycle` (43) | Baseline bench | P1 | P50/95/99 | ✅ existing | Tombstone accumulation effect on search |
| C2 | TTL + `purge_expired()` | F | `test_lifecycle` | Bench purge over 1k/100k expired rows | P1 | Rows/s, latency | ✅ existing | **Purge at scale** |
| C3 | `erase_all()` + `ErasureReport` | F | `test_pipe5` (24) | Bench erase over large scopes | P1 | Rows/s, completeness | ✅ existing | Large-scope erase |
| C4 | `delete_user`/`delete_agent` cascade | F | `test_thrd7` (19) | Bench cascade over deep hierarchies | P1 | Rows/s, round-trips | ✅ existing | Deep hierarchy |
| C5 | `delete_memory`/`delete_message` | F | `test_thrd8` (16) | Baseline bench | P2 | P50/95/99 | ✅ existing | — |
| C6 | `delete_thread()` | F | Integration | Baseline bench | P2 | P50/95/99 | ✅ existing | — |
| C7 | `reconcile()` + supersession | F | `test_reconciliation` (52) | Bench over candidate-set sizes; supersede correctness | P1 | Latency, precision of supersession | ✅ existing | Fact-set growth |
| C8 | `export_scope`/`import_scope` | F | `test_pipe6` (33) | Bench round-trip over 1k/100k rows; streaming memory | P1 | Rows/s, **peak RSS** (pytest-memray) | ✅ existing | **Large-scope export** |
| C9 | Scope-import validation | F | `test_pipe6` | Covered by C8 | P2 | — | ✅ existing | — |
| D1–D2 | `MemoryScope` hierarchy in SQL | F+ | `test_scoping` (59) | Covered by B-tier benches | P0 | Predicate cost | ✅ existing | — |
| D3 | **Zero cross-scope leakage under concurrency** | C (port) | `isolation_load` (10 tenants × 20 workers) | Port assertions into Locust; scale to 100 tenants × 200 users | **P0** | **Leak count (must be 0)** | ✅ + load | **Primary isolation gate** |
| D4 | Chunk scope replication | F+ | `test_orc2` | Covered by B4 | P1 | — | ✅ existing | — |
| E1 | `ConnectionPool` size / timeout / exhaustion | N → F | `test_connection` (10) | Locust ramp until `ConnectionPoolExhausted`; sweep pool 1/5/10/20 | **P0** | Saturation point, queue wait, error rate | ✅ existing | **Concurrency ceiling — the headline number** |
| E2 | Async facade (`*_async`) | F+ | `test_thrd9` (15), integration (7) | asyncio-driven concurrency bench vs. sync | P1 | Throughput, event-loop block time | ✅ existing | Async concurrency ramp |
| E3 | `MemoryStore` thread-safety | C | Implicit in `isolation_load` | Explicit shared-instance concurrent scenario | **P0** | Error rate, corruption count | ⚠️ **partial** | **Shared-store load** |
| E4 | Consolidation claim under contention | C | `test_consolidation_worker` (7) | Multi-worker Locust scenario | P1 | Double-claim count (must be 0) | ⚠️ partial | Multi-worker |
| E5 | **Transactions** | **N** | **None — no API exists** | **Cannot be benchmarked. Document as a deliberate non-feature or design one.** | P1 | — | ❌ **GAP** | ❌ |
| F1 | `Migrator` run/validate/status | F | `test_migrations` (24) | Bench cold migration time (CI startup budget) | P1 | Wall-clock | ✅ existing | — |
| F2 | `SchemaPolicy` modes | F | `test_metadata_filters_schema_policy` | Covered by F1 | P2 | — | ✅ existing | — |
| F3–F4 | VECTOR columns + `CREATE VECTOR INDEX` | F+ / VectorDBBench | Integration only | Index build time vs. row count; **APPROX recall** (see B3) | **P0** | Build time, index size, recall@k | ✅ existing | **1M-vector index behavior** |
| F5 | Scope + `expires_at` indexes | C | None | `MON_GET_PKG_CACHE_STMT` — confirm index usage, not scans | P1 | Rows read vs. rows returned | ⚠️ **none** | Index-effectiveness at scale |
| F6 | `embedding_dim` configurability | F | Implicit | Dim sweep 384/768/1536/3072 (see A2) | P1 | Latency, storage/row | ✅ existing | Dim × row-count grid |
| G1 | LangChain adapter | F | `test_adapters` (77) + integration (23) | Adapter-overhead bench vs. direct SDK | P1 | Δ latency vs. direct | ✅ existing | — |
| G2 | OpenAI Agents `Db2Session` | F | Same | Same | P1 | Δ latency | ✅ existing | Long sessions |
| G3 | MCP server (4 tools) | F+ | Same | Bench tool round-trip incl. MCP framing | P1 | Δ latency, payload size | ✅ existing | — |
| G4 | MS Agent Framework provider | F | `test_agent_framework` (11) | `before_run`/`after_run` overhead | P1 | Δ latency | ✅ existing | — |
| H1 | Retrieval quality (end-to-end memory ability) | **LongMemEval** | Synthetic 50-q, judge-confounded | Replace with real 500-q dataset; add IR metrics | **P0** | Recall@k, MRR, nDCG@k, judged accuracy | ⚠️ **confounded** | `_S` → `_M` (40 → 500 sessions) |

## Capabilities that no existing benchmark can validate

Five, and each needs the small custom pieces from Phase 3 — not a framework.

| Gap | Why no framework covers it | Mitigation |
|---|---|---|
| **E5 — transactions** | There is no transaction API to benchmark. Commit granularity is per-operation. | **Not a benchmark gap — a design gap.** Decide explicitly: document per-operation commit as intended semantics, or spec a `with store.transaction():` API. Benchmarks cannot proceed on this row until that decision is made. |
| **D3/E3 — cross-scope isolation under concurrency** | Vector-DB benchmarks have no concept of tenant scoping; load tools have no concept of correctness invariants. | Custom Locust `User` asserting every returned row matches the requesting scope. Port from `isolation_load/run.py`. This is the SDK's core differentiator and its most important gate. |
| **A3/A7/B9/B11 — DB round-trips per SDK call** | This is an SDK-internal accounting question. No tool models it. | ~40 LOC counting cursor proxy. Runner-speed-invariant, so it can gate PRs where wall-clock cannot. |
| **B6/F5 — filter selectivity & index effectiveness** | Requires Db2-specific server-side monitoring. | `MON_GET_PKG_CACHE_STMT` queries after each benchmark; assert rows-read/rows-returned ratio. |
| **A8/A9 — Consolidator/Reconciler/Extractor economics** | Protocol-specific to this SDK. Existing `cost_tracking.py` is the right answer and already exists. | Keep `common/cost_tracking.py`; wire into the pytest-benchmark fixtures. |

---

# Phase 5 — Benchmark strategy

Three independent axes. Each answers a different question and fails for different reasons.

## 5.1 Functional correctness

**Principle:** correctness is already well covered at *unit* scale — 1,168 tests, 85%
coverage gate, 77 live-Db2 integration tests. The gap is correctness properties that only
break at **volume** or **concurrency**. Benchmarks must assert those, not re-test what
pytest already covers.

Correctness invariants asserted *inside* every benchmark run:

| Invariant | Where it can break | Assertion |
|---|---|---|
| Zero cross-scope leakage | Concurrent multi-tenant load | Every returned row's scope == requesting scope. Count must be 0. |
| APPROX recall floor | Large corpora — ANN index degrades | `recall@10(APPROX) / recall@10(EXACT) ≥ 0.95` at 100k rows |
| Chunk→parent resolution completeness | Chunked corpora (the ORC-2 routing bug lived here) | Every chunk hit resolves to exactly one live parent |
| Supersession exclusion | After `reconcile()` at volume | No superseded fact appears in `search()`/`list_all()` |
| TTL exclusion | After `purge_expired()` at volume | No expired row returned; purge count == expected |
| Erasure completeness | `erase_all()` on large scopes | Zero residual rows across all 6 tables incl. `memory_chunks` |
| Export/import round-trip fidelity | 100k-row scopes | Re-imported scope is byte-equal on content + metadata + confidence |
| Dedup idempotence | Repeated writes under concurrency | N identical concurrent writes → exactly 1 row |
| No double-claim | Multi-worker consolidation | Each record claimed by exactly one worker |
| Pool exhaustion is graceful | Over-saturation | Raises `ConnectionPoolExhausted`, never corrupts or hangs |

**Execution:** these are pytest assertions inside benchmark bodies (Tiers 1–2) and Locust
`User` assertions (Tier 2). A benchmark that is fast but leaks is a failed benchmark.

## 5.2 Performance

**Instrumented per operation:**

| Metric | Source | Notes |
|---|---|---|
| Latency P50 / P95 / P99 | pytest-benchmark (single-op), Locust (under load) | pytest-benchmark handles warmup + outlier rejection |
| Throughput (ops/s) | Locust | Per-operation-type RPS |
| **DB round-trips per call** | Counting cursor proxy | **Runner-speed invariant → safe to gate in CI** |
| SQL statement size | Tier-0 bench on `_vec_to_str` | The 20 KB-per-write question |
| Embed time vs. DB time | Explicit split in fixtures | Prevents embedding cost masking DB regressions |
| Peak RSS | pytest-memray (per test), psutil (per load run) | Especially `export_scope` streaming and 500k-row `list_all` |
| CPU utilization | psutil sampler in Locust | Distinguishes client-bound from DB-bound |
| Rows read / rows returned | Db2 `MON_GET_PKG_CACHE_STMT` | Detects non-sargable `LOCATE` filter scans |
| Index usage | Db2 explain / monitoring | Confirms vector + scope indexes are actually used |
| Est. LLM tokens & cost | Retained `common/cost_tracking.py` | Only meaningful when a real protocol hook is wired |

**Benchmark configurations (the matrix that matters):**

- **Corpus size:** 1k / 10k / 100k / 500k rows per scope
- **Embedding dim:** 384 / 768 / 1536 / 3072
- **Content size:** 200 / 1,000 / 5,000 / 20,000 chars (crosses `chunk_threshold`=2000)
- **Search mode:** EXACT vs. APPROX vs. DEFAULT
- **Retrieval mode:** vector-only vs. hybrid RRF
- **Filter selectivity:** none / 50% / 10% / 1% / 0.1%
- **Protocol wiring:** all-NoOp vs. resolver-on vs. consolidator-on vs. fully-wired
- **Pool size:** 1 / 5 / 10 / 20

Not every cell — a fractional design: full sweep on the P0 axes (corpus size, search
mode, selectivity), single-point elsewhere.

## 5.3 Scalability

| Dimension | Scenario | Primary metric | Tier |
|---|---|---|---|
| Increasing users | Locust ramp 1→10→50→200 concurrent users | P95 latency inflection point, error rate | 2 |
| Concurrent agents | 10 → 100 → 1,000 distinct `agent_id`s | Throughput, **leak count = 0** | 2 |
| Growing memory size | 1k → 500k rows/scope, search latency curve | P95 growth rate; is it sub-linear? | 2/3 |
| Large datasets | 1M+ total rows across tenants | Vector index build time, APPROX recall | 3 (live Db2) |
| Long-running sessions | Threads of 10 → 100 → 1,000 → 10,000 turns | `get_context_card` / `get_summary` latency vs. turn count | 2/3 |
| High write rate | 100% write, ramp to pool saturation | Max sustained writes/s, dedup + chunking cost | 2 |
| High read rate | 100% search, ramp to saturation | Max sustained QPS | 2 |
| Mixed workload | 70/30, 50/50, 30/70 read/write | Throughput vs. contention; writer starvation | 2 |
| Pool pressure | Pool 1/5/10/20 at fixed load | Queue wait, `ConnectionPoolExhausted` rate | 2 |
| Tombstone accumulation | Soft-delete 50% of a 100k corpus, re-search | Search latency delta — does `forget()` degrade reads over time? | 3 |

**Practicality on the given infrastructure:**

- **Tiers 0–1 and small Tier 2** fit comfortably in GHA (Db2 container already boots in
  ≤15 min; datasets ≤50k rows seed in ~2–4 min with the `hashing` embedding provider,
  which is offline and instant).
- **Large Tier 2 and all of Tier 3** run on the Mac against the live Db2 — that instance's
  job is to hold the persistent 500k–1M-row corpus that an ephemeral container cannot.
- **Seed once, reuse.** The large corpora are built by a `seed_corpus.py` script and left
  in place under a reserved `tenant_id`, so weekly runs measure rather than re-seed.
- **The `hashing` embedding provider** (already in `common/embedding_providers.py`) is what
  makes CI-tier seeding viable: deterministic, zero network, zero model download. Real
  embedding models are used only in Tier 3, where retrieval *quality* is the question.

---

# Phase 6 — GitHub Actions strategy

Four tiers. The design rule: **each tier's runtime budget is set by how often it runs.**

### Tier 0 — Smoke (every push & PR) · ~90 s · **blocking**

- Runner: `ubuntu-latest`, no database
- Workload: `pytest -m benchmark_micro` with a fake DBAPI connection
- Covers: `_vec_to_str` serialization, `_build_metadata_filter` SQL compilation,
  `_rrf_fuse`, `_split_chunks`, `_scope_predicates`, `_content_hash`, Pydantic model
  construction
- Measurement: **pytest-codspeed** (Valgrind instruction counting) — immune to runner noise
- Gate: **fail** on >10% instruction-count regression
- Rationale: catches an accidental O(n²) or a per-call re-compile in seconds, with zero
  flakiness and no Db2

### Tier 1 — Pull request (every PR) · ~25 min · **blocking**

- Runner: `ubuntu-latest` + the existing Db2 container job (reuse `ci.yml` verbatim)
- Dataset: 1k rows/scope, 10 scopes, `hashing` provider, dim 1536
- Workload: `pytest -m benchmark_pr` — one bench per P0 capability (A1–A3, A5, A7, A10,
  B1, B3–B7, B9, B11, B14, C2–C3, D3-small)
- Also runs: correctness invariants at 1k scale; **round-trip counts**
- Gates:
  - **fail** on any round-trip-count regression (deterministic, runner-invariant)
  - **fail** on any correctness-invariant violation (leak count > 0, etc.)
  - **alert** (comment, not fail) on >50% wall-clock regression via
    `github-action-benchmark` (`alert-threshold: 150%`, `fail-threshold: 300%`)
- Rationale: wall-clock on shared runners cannot be a hard gate; round-trip counts can,
  and they catch the regressions that actually matter (an added `SELECT` in a hot path)

### Tier 2 — Nightly (`schedule: 0 3 * * *` + `workflow_dispatch`) · ~2 h · **non-blocking**

- Runner: `ubuntu-latest` + Db2 container
- Dataset: 50k rows/scope, 50 scopes
- Workloads:
  1. Full `pytest -m benchmark` matrix (all metrics, all P0/P1 capabilities)
  2. Locust ramp 1→50 users, 15 min, mixed 70/30 read/write, with leak assertions
  3. Locust pool-saturation sweep (pool size 1/5/10/20)
  4. Filter-selectivity sweep + `MON_GET_PKG_CACHE_STMT` rows-read capture
  5. APPROX-vs-EXACT recall check at 50k
  6. **Deterministic IR metrics** on LongMemEval_S (Recall@k / MRR / nDCG@k, no LLM)
  7. pytest-memray peak-RSS checks on `export_scope` / deep `list_all`
- Output: push results to `gh-pages` via `github-action-benchmark`; open an issue on
  regression
- Rationale: everything expensive but still container-sized, run when nobody is waiting

### Tier 3 — Scale (`workflow_dispatch: tier3-scale`) · ~2 h · **non-blocking**

> **Updated (EPIC-24 / CIB-3):** Tier 3 now uses the same containerized Db2
> as Tiers 1–2 (500k-row corpus). The live-Db2 dependency has been removed.
> See `benchmarks/README.md`'s Workflow map for the current trigger/gate/output table.

- Runner: `ubuntu-latest` + containerized Db2 (`.github/actions/setup-db2`)
- Dataset: 500k-row corpus seeded at run time
- Workloads:
  1. Search-latency-vs-corpus-size curve (1k → 1M)
  2. Vector index build time and APPROX recall at 1M vectors
  3. Long-session sweep (10 → 10,000 turns) for `get_context_card` / `get_summary`
  4. Locust ramp to 200 users, 1,000 agents, 60 min soak
  5. Tombstone-accumulation degradation test
  6. **LLM-judged LongMemEval_S** (full 500 questions, local Ollama judge) — *reported,
     never gated*
  7. Large-scope `export_scope` / `erase_all` throughput + RSS
- Output: dated section appended to `project-management/BENCHMARKS.md`; results committed
- Rationale: the only tier that touches shared infrastructure, so it runs rarely,
  on a schedule, and is explicitly manually dispatchable

### Cross-cutting CI notes (updated for EPIC-24)

> See `benchmarks/README.md` → "Workflow map (post EPIC-24)" for the canonical
> trigger/gate/output table for all active benchmark workflow jobs.

- **Concurrency guard (CIB-7).** `benchmarks.yml` uses `concurrency: group: benchmarks-${{ github.ref }}, cancel-in-progress: false` so overlapping `workflow_dispatch` runs queue rather than racing on the single gh-pages writer.
- **Cost.** Tiers 0–3 are all free on public repos (containerized Db2, no external services). Tier 3 is ~2 h per dispatch.
- **Suite selector (CIB-4).** The `suite` input lets a dispatcher run only the tier they need (`tier0-codspeed`, `tier1-benchmark`, `tier2-nightly`, `tier3-scale`, `locust-isolation`, `locust-scale`, or `all`).
- **Secrets.** No `DB2_*` secrets needed for any tier — all use the containerized Db2 with hardcoded local credentials.
- **Reproducibility.** Every report stamps runner type, Db2 version, embedding provider +
  model, judge + model, seed, dataset size, and commit SHA. The existing
  `benchmarks/common/report.py` already does this well — that discipline should survive
  even though the renderer itself is replaced.

---

# Phase 7 — Project plan

## Epics

| Epic | Title | Goal | Stories | Est. |
|---|---|---|---|---|
| **EPIC-13** | Benchmark foundation — retire the bespoke harness, adopt pytest-benchmark | A pytest-native benchmark layer, fixtures, seeding, and the 4 custom primitives | 6 | ~6 d |
| **EPIC-14** | Single-operation performance coverage | Every P0 capability has a latency + round-trip benchmark | 5 | ~5 d |
| **EPIC-15** | Concurrency & scale via Locust | Ramps, mixed workloads, pool saturation, leak-under-load | 4 | ~5 d |
| **EPIC-16** | Retrieval quality on real public datasets | LongMemEval real dataset, IR metrics + judged accuracy, honest comparability | 4 | ~5 d |
| **EPIC-17** | CI tiering & regression gating | 4 GHA tiers wired, gating on the right (deterministic) signals | 3 | ~3 d |
| **EPIC-18** | Db2-specific depth | Selectivity, index effectiveness, APPROX recall, vector-literal cost | 3 | ~3 d |
| **EPIC-19** | Reporting, baselines & publication | Trend charts, BENCHMARKS.md rewrite, comparability statement | 2 | ~2 d |

**Total: 27 stories, ~29 engineer-days.** Effort scale: **S** ≤ 0.5 d, **M** ≈ 1 d,
**L** ≈ 2 d, **XL** ≈ 3 d.

---

## EPIC-13 — Benchmark foundation

### BM-1 · Decide and record the benchmark architecture · **M**
**Description.** Land a DECISIONS.md entry adopting pytest-benchmark + github-action-benchmark
+ Locust + LongMemEval, and recording the explicit rejections (VectorDBBench, HammerDB,
ANN-Benchmarks, OpenAI Evals, BEIR, mem0ai/memory-benchmarks) with rationale, so this
research is not re-litigated.
**Acceptance criteria.** DECISIONS.md entry dated and merged; `benchmarks/README.md`
rewritten to describe the new architecture; license compatibility of every adopted tool
recorded.
**Dependencies.** None.
**Deliverables.** DECISIONS.md entry, `benchmarks/README.md`.

### BM-2 · Retire the bespoke harness, retain the valuable parts · **L**
**Description.** Delete `common/timing.py`, `common/report.py`, `common/llm_judge.py`,
`retrieval_quality/dataset.py`, `latency_cost/run.py`, `scripts/run_benchmarks.py`. Retain
and relocate `common/embedding_providers.py`, `common/scope_gen.py`,
`common/cost_tracking.py`, `retrieval_quality/consolidator.py`, `retrieval_quality/reconciler.py`.
Prune `tests/test_benchmarks_unit.py` to the retained surface. Update `Makefile`.
**Acceptance criteria.** `pytest` green including the 85% coverage gate; `make benchmark`
either removed or re-pointed; no dangling imports; the retained modules have unchanged
public behavior (verified by the surviving unit tests).
**Dependencies.** BM-1.
**Deliverables.** Deleted files, relocated modules, updated `Makefile`, pruned tests.

### BM-3 · Benchmark fixtures & Db2 lifecycle · **L**
**Description.** `benchmarks/conftest.py` providing: a session-scoped `ConnectionPool`
honouring `DB2_*`; a `MemoryStore` factory parametrized over protocol wiring
(all-NoOp / resolver-on / consolidator-on / fully-wired); pool-size parametrization;
automatic reserved-`tenant_id` teardown; and pytest markers `benchmark_micro`,
`benchmark_pr`, `benchmark_nightly`, `benchmark_scale` registered in `pyproject.toml`.
**Acceptance criteria.** A trivial benchmark runs locally against the Db2 container and
against the live instance with only env-var changes; teardown leaves zero residual rows;
markers are selectable and documented.
**Dependencies.** BM-2.
**Deliverables.** `benchmarks/conftest.py`, `pyproject.toml` marker + `[dev]`/`[benchmark]`
extra updates.

### BM-4 · Deterministic corpus seeding at 1k / 50k / 500k · **L**
**Description.** `benchmarks/seed_corpus.py` — size-parametrized, seeded, resumable corpus
generation with controlled metadata cardinality (so filter selectivity is a knob), content-length
distribution spanning `chunk_threshold`, and multi-tenant/agent/user/thread fan-out. Uses the
`hashing` provider by default.
**Acceptance criteria.** 1k seeds in <30 s and 50k in <5 min on a GHA runner; identical seed
produces an identical corpus; resumable after interruption; a documented `--purge` path.
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/seed_corpus.py`, seeding docs.

### BM-5 · DB round-trip counting proxy · **M**
**Description.** `CountingConnection` / `CountingCursor` wrapping the pool, exposing
per-SDK-call `execute`/`fetch` counts, plus a `round_trips` pytest fixture and an
`assert_round_trips(n)` helper. This is the deterministic signal Tier 1 gates on.
**Acceptance criteria.** Counts verified against known-shape operations (`get_by_id` == 1);
zero measurable overhead when disabled; works with all 5 repositories and the chunk repo;
covered by unit tests.
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/common/counting.py`, unit tests.

### BM-6 · Memory & CPU instrumentation · **M**
**Description.** Wire `pytest-memray` for per-benchmark peak-allocation limits and a `psutil`
RSS/CPU sampler usable from both pytest and Locust runs.
**Acceptance criteria.** Peak RSS reported for `export_scope` over 50k rows; a deliberate
leak is detected by the memray limit; sampler adds <2% overhead.
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/common/resource_sampler.py`, memray config, `[benchmark]` extra.

---

## EPIC-14 — Single-operation performance coverage

### BM-7 · Tier-0 micro-benchmarks (no database) · **M**
**Description.** `pytest -m benchmark_micro` over `_vec_to_str` (dim sweep 384/768/1536/3072),
`_build_metadata_filter` (all 4 operators), `_rrf_fuse`, `_split_chunks`, `_scope_predicates`,
`_content_hash`, and Pydantic model construction — against a fake DBAPI connection.
**Acceptance criteria.** Runs in <90 s with no Db2; deterministic under pytest-codspeed;
statement-size in bytes reported for `_vec_to_str` at each dimension.
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/micro/test_*.py`.

### BM-8 · Write-path benchmarks (A1–A7, A10) · **L**
**Description.** `remember()` across all 5 types; `create()` with dedup on/off; chunking across
content sizes 200/1k/5k/20k/60k chars; ingest resolver on/off; `add_messages()` batch sweep
1/10/100/1000; `update()` with and without chunk rewrite. Each asserts round-trip counts.
**Acceptance criteria.** P50/P95/P99 for every configuration; round-trip counts recorded and
asserted; dedup and resolver overhead quantified as an explicit delta; JSON export consumable by
`github-action-benchmark`.
**Dependencies.** BM-4, BM-5.
**Deliverables.** `benchmarks/perf/test_write_path.py`.

### BM-9 · Read-path benchmarks (B1–B14) · **XL**
**Description.** `search()` across corpus sizes × search modes × distance metrics; chunk vs.
parent search; hybrid RRF vs. vector-only; `list_all()` offset sweep; multi-type facade fan-out;
`get_context_card()` with/without `include_long_term`; `get_summary()` across thread lengths;
`get_messages()` range sweep. Embedding time is isolated from DB time throughout.
**Acceptance criteria.** Latency curve vs. corpus size for `search()`; fan-out round-trip counts
for `store.search()` and `get_context_card()`; explicit ms-embed vs. ms-DB split; hybrid overhead
quantified.
**Dependencies.** BM-4, BM-5.
**Deliverables.** `benchmarks/perf/test_read_path.py`.

### BM-10 · Lifecycle & governance benchmarks (C1–C8) · **L**
**Description.** `forget()`, `purge_expired()` over 1k/100k expired rows, `erase_all()` on large
scopes, cascade deletes over deep hierarchies, `reconcile()` across candidate-set sizes, and
`export_scope`/`import_scope` round-trip with peak-RSS measurement.
**Acceptance criteria.** Rows/s for every bulk operation; erasure completeness asserted across all
6 tables; export peak RSS confirms the `Iterator` genuinely streams rather than materializing.
**Dependencies.** BM-4, BM-6.
**Deliverables.** `benchmarks/perf/test_lifecycle.py`.

### BM-11 · Adapter overhead benchmarks (G1–G4) · **M**
**Description.** Measure each adapter's overhead against the equivalent direct SDK call:
LangChain history + store, OpenAI Agents `Db2Session`, MCP tool round-trip (incl. framing),
MS Agent Framework `before_run`/`after_run`.
**Acceptance criteria.** Δ latency vs. direct SDK reported per adapter; MCP payload size recorded;
benchmarks skip cleanly when the optional extra is absent (matching existing test conventions).
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/perf/test_adapters.py`.

---

## EPIC-15 — Concurrency & scale

### BM-12 · Locust harness for the SDK · **L**
**Description.** `benchmarks/load/locustfile.py` with a `MemoryStoreUser` base class dispatching
blocking `ibm_db` calls through `gevent.get_hub().threadpool`, plus tasks for
search / remember / add_messages / get_context_card / list_all, and headless CSV output.
**Acceptance criteria.** 50 concurrent users sustained without greenlet-hub starvation (verified
by comparing achieved RPS against a single-user baseline × users); headless run exits non-zero on
threshold breach; CSV percentiles emitted.
**Dependencies.** BM-4.
**Deliverables.** `benchmarks/load/locustfile.py`, `benchmarks/load/README.md`.

### BM-13 · Cross-scope isolation under load (port `isolation_load`) · **L**
**Description.** Port the leakage assertions from `benchmarks/isolation_load/run.py` into a Locust
`User` that verifies every returned row's scope matches the requesting scope, scaled to 100 tenants
× 1,000 agents × 200 concurrent users. Add a shared-`MemoryStore`-instance scenario to exercise E3.
**Acceptance criteria.** Leak count is 0 at full scale; a deliberately injected scope bug is
detected; run exits non-zero on any leak; the old `isolation_load/` module is removed.
**Dependencies.** BM-12.
**Deliverables.** `benchmarks/load/test_isolation.py`, deletion of `benchmarks/isolation_load/`.

### BM-14 · Scalability sweeps · **L**
**Description.** User ramp 1→200; agent-count sweep 10→1,000; mixed read/write at 70/30, 50/50,
30/70; sustained high-write and high-read; 60-minute soak; long-session sweep 10→10,000 turns.
**Acceptance criteria.** P95 inflection point identified per dimension; sustained max write/s and
read QPS recorded; soak shows no RSS growth trend and no error-rate drift; writer starvation under
read-heavy mixes is explicitly checked.
**Dependencies.** BM-12.
**Deliverables.** `benchmarks/load/scenarios/`, results section.

### BM-15 · Connection-pool saturation characterization · **M**
**Description.** Sweep `DB2_POOL_SIZE` ∈ {1, 5, 10, 20} at fixed load; measure queue wait time,
`ConnectionPoolExhausted` rate, and throughput ceiling. Produce a pool-sizing recommendation for
the README.
**Acceptance criteria.** Throughput-vs-pool-size curve; documented saturation point; exhaustion is
shown to be graceful (raises, never hangs or corrupts); a concrete sizing guideline lands in the
README.
**Dependencies.** BM-12.
**Deliverables.** `benchmarks/load/test_pool_saturation.py`, README section.

---

## EPIC-16 — Retrieval quality

### BM-16 · Adopt the real LongMemEval dataset · **L**
**Description.** Download `longmemeval_s` / `longmemeval_oracle` from HuggingFace (Apache-2.0),
cache locally and in CI, and write the adapter mapping LongMemEval sessions → `add_messages()`
under per-question `MemoryScope`s, and questions → `store.search()`.
**Acceptance criteria.** All 500 questions ingest successfully; per-question scope isolation
verified; dataset caching keeps CI runs offline after first fetch; licensing and attribution
recorded.
**Dependencies.** BM-3.
**Deliverables.** `benchmarks/quality/longmemeval_adapter.py`, dataset cache tooling.

### BM-17 · Deterministic IR metrics (CI-safe) · **M**
**Description.** Compute Recall@k, MRR, and nDCG@k against LongMemEval's labelled evidence
sessions — no LLM anywhere in the path. This is the metric CI can actually run and trust.
**Acceptance criteria.** Metrics fully deterministic across repeated runs (bit-identical);
computed for k ∈ {5, 10, 20, 50}; reported per ability category; runs in <10 min on
`longmemeval_s`.
**Dependencies.** BM-16.
**Deliverables.** `benchmarks/quality/ir_metrics.py`, nightly integration.

### BM-18 · LLM-judged end-to-end accuracy (offline tier) · **L**
**Description.** Wire LongMemEval's own judge prompt with a local Ollama model for the full
500-question run. Report per-category accuracy with the judge, model, seed, and top-k stamped.
Never a gate.
**Acceptance criteria.** Per-category accuracy comparable *in kind* to published figures, with
every deviation from published methodology enumerated; judge variance quantified by running a
fixed subset 3× and reporting the spread (directly addressing BENCH-1's finding); results appended
to BENCHMARKS.md as a new dated run without overwriting Runs A–C.
**Dependencies.** BM-16.
**Deliverables.** `benchmarks/quality/judged_run.py`, BENCHMARKS.md section.

### BM-19 · SDK configuration comparison on real data · **L**
**Description.** Re-run BM-17/BM-18 across configurations: vector-only vs. hybrid; consolidation
off vs. on (reusing BENCH-3a's consolidator); reconciliation off vs. on (BENCH-3b's reconciler);
top-k sweep; `longmemeval_s` vs. `_m` to test the "SDK wins at scale" hypothesis BENCH-5 raised
against a synthetic proxy.
**Acceptance criteria.** A configuration matrix with IR metrics per cell; an explicit verdict —
confirmed / partially confirmed / refuted — on the at-scale hypothesis, now on real data; an
honest statement wherever a configuration does not help.
**Dependencies.** BM-17, BM-18.
**Deliverables.** BENCHMARKS.md comparison section, DECISIONS.md entry.

---

## EPIC-17 — CI tiering

### BM-20 · Tier 0 + Tier 1 workflows · **L**
**Description.** `.github/workflows/benchmark-pr.yml` — Tier 0 (codspeed, no DB) and Tier 1
(reusing the existing Db2 container job) with `github-action-benchmark` publishing to `gh-pages`,
`alert-threshold: 150%`, `fail-threshold: 300%`, and hard failure on round-trip-count or
correctness-invariant regression.
**Acceptance criteria.** Total added PR time ≤25 min; a deliberately injected extra `SELECT` fails
the round-trip gate; a deliberate 2× slowdown produces a comment but not a failure; trend chart
renders on `gh-pages`.
**Dependencies.** BM-7, BM-8, BM-9, BM-5.
**Deliverables.** `.github/workflows/benchmark-pr.yml`.

### BM-21 · Tier 2 nightly workflow · **L**
**Description.** `.github/workflows/benchmark-nightly.yml` — scheduled + dispatchable, Db2
container, 50k corpus, full pytest matrix + Locust ramp + selectivity sweep + IR metrics + memray
checks. Opens an issue on regression.
**Acceptance criteria.** Completes within 2 h; results published to `gh-pages`; regression opens a
labelled issue; `workflow_dispatch` accepts corpus-size and scenario inputs.
**Dependencies.** BM-20, BM-14, BM-17.
**Deliverables.** `.github/workflows/benchmark-nightly.yml`.

### BM-22 · Tier 3 weekly scale workflow · **M**
**Description.** `.github/workflows/benchmark-scale.yml` against the live Db2 via protected
environment secrets, with `concurrency: group: live-db2, cancel-in-progress: false`, a reserved
`tenant_id` namespace, and never triggerable from fork PRs.
**Acceptance criteria.** Two concurrent runs cannot overlap; secrets are inaccessible to
fork-triggered workflows; results are committed to BENCHMARKS.md by the workflow; a documented
manual-abort and cleanup path exists.
**Dependencies.** BM-21, BM-4.
**Deliverables.** `.github/workflows/benchmark-scale.yml`, runbook.

---

## EPIC-18 — Db2-specific depth

### BM-23 · Metadata-filter selectivity & index effectiveness · **L**
**Description.** Sweep filter selectivity (none / 50% / 10% / 1% / 0.1%) across all 4 operators at
50k and 500k rows, capturing `MON_GET_PKG_CACHE_STMT` rows-read vs. rows-returned to prove or
disprove that the `LOCATE`-based `$array_contains` path scans.
**Acceptance criteria.** Latency-vs-selectivity curve per operator; rows-read ratio captured;
scope and `expires_at` index usage confirmed; findings recorded in DECISIONS.md with a
recommendation (e.g. a generated column, or documented guidance to prefer scalar filters).
**Dependencies.** BM-4.
**Deliverables.** `benchmarks/db2/test_selectivity.py`, DECISIONS.md entry.

### BM-24 · APPROX vs. EXACT recall & vector index characterization · **L**
**Description.** Measure APPROX recall against EXACT ground truth at 1k/50k/500k/1M rows; measure
`CREATE VECTOR INDEX` build time and storage vs. row count; sweep embedding dimension.
**Acceptance criteria.** Recall@10 ratio (APPROX/EXACT) reported at every scale with a documented
floor (target ≥0.95); index build time curve; a documented guideline for when APPROX is safe;
recall regression fails the nightly tier.
**Dependencies.** BM-4.
**Deliverables.** `benchmarks/db2/test_vector_index.py`, README guidance.

### BM-25 · Vector-literal cost and dimension economics · **M**
**Description.** Quantify the cost of the `SQL0901N` workaround — inlining a ~20 KB vector literal
per statement — by comparing statement build time, network bytes, and end-to-end latency across
dimensions 384/768/1536/3072.
**Acceptance criteria.** Cost attributed between client-side string building, network transfer, and
server parse; a recommendation on default `embedding_dim` and on whether to re-test parameter
binding on newer Db2 fixpacks; findings in DECISIONS.md.
**Dependencies.** BM-7, BM-8.
**Deliverables.** `benchmarks/db2/test_vector_literal.py`, DECISIONS.md entry.

---

## EPIC-19 — Reporting & publication

### BM-26 · Rewrite BENCHMARKS.md around the new methodology · **L**
**Description.** Restructure the report: methodology and its deviations up front, deterministic
metrics separated from LLM-judged metrics, Runs A–C preserved as historical record with an
explicit note that they used a synthetic dataset and a confounded judge.
**Acceptance criteria.** Every number carries provenance (runner, Db2 version, embedding model,
judge, seed, corpus size, commit SHA); the comparability statement is unambiguous about what may
and may not be compared to vendor figures; history is preserved, not overwritten.
**Dependencies.** BM-18, BM-19.
**Deliverables.** rewritten `project-management/BENCHMARKS.md`.

### BM-27 · Baselines, budgets, and the regression policy · **M**
**Description.** Establish committed baselines for every P0 metric, define per-tier alert and fail
thresholds, and write the policy for updating a baseline (what justifies it, who approves,
where it is recorded).
**Acceptance criteria.** A committed `benchmarks/baselines.json`; documented thresholds per tier;
a written policy in `benchmarks/README.md`; the board's story template updated to require a
benchmark check for perf-sensitive work.
**Dependencies.** BM-20, BM-21.
**Deliverables.** `benchmarks/baselines.json`, policy docs.

---

## Recommended execution order

```
Week 1  — Foundation (unblocks everything)
  BM-1 → BM-2 → BM-3 → BM-4 ─┬→ BM-5
                             └→ BM-6
  Milestone: a benchmark runs locally and in the Db2 container, seeded and instrumented.

Week 2  — Value first: measure the P0 hot paths
  BM-7 → BM-8 → BM-9        (write + read paths — the bulk of the value)
  BM-20                     (Tier 0/1 CI live as soon as there is something to gate)
  Milestone: every PR is gated on round-trip counts. Regressions stop shipping.

Week 3  — Scale and Db2 depth (parallelizable)
  BM-12 → BM-13 → BM-14 → BM-15     ┐
  BM-10, BM-11                      ├ can run concurrently
  BM-23, BM-24, BM-25               ┘
  BM-21                             (nightly tier)
  Milestone: concurrency ceiling, isolation-under-load, and APPROX recall are known numbers.

Week 4  — Quality and publication
  BM-16 → BM-17 → BM-18 → BM-19
  BM-22 (weekly scale) → BM-26 → BM-27
  Milestone: real-dataset retrieval quality, published trends, committed baselines.
```

**Rationale for this order.** Foundation first because everything depends on it. Then the
*write and read hot paths*, because that is where regressions actually happen and where
Tier 1 gating pays for itself immediately — CI protection lands in week 2, not week 4.
Scale and Db2 depth follow because they answer strategic questions ("what is our
concurrency ceiling?", "is APPROX safe?") that inform the product but do not gate daily
work. Retrieval quality is last not because it is least important, but because it is the
only workstream whose *methodology* is already known-broken — doing it last means it is
built on a foundation that can support the fix properly, rather than patching the
existing confounded harness in place.

**Two things worth doing before week 1.** Decide the E5 transaction question (document
per-operation commit as intended, or spec an API) — it is the only inventory row that
cannot be benchmarked at all. And confirm whether the live Db2 instance can hold a
persistent 500k–1M-row corpus, since Tier 3's entire design depends on it.

---

## Sources

- [LongMemEval (ICLR 2025) — official repo, Apache-2.0](https://github.com/xiaowu0162/longmemeval)
- [LongMemEval-V2 — official repo](https://github.com/xiaowu0162/LongMemEval-V2)
- [mem0ai/memory-benchmarks — LoCoMo / LongMemEval / BEAM suite, Apache-2.0](https://github.com/mem0ai/memory-benchmarks)
- [AI Memory Benchmarks 2026: LoCoMo, LongMemEval & BEAM](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- [benchmark-action/github-action-benchmark](https://github.com/benchmark-action/github-action-benchmark)
- [github-action-benchmark — pytest example](https://github.com/benchmark-action/github-action-benchmark/blob/master/examples/pytest/README.md)
- [CodSpeedHQ/pytest-codspeed](https://github.com/CodSpeedHQ/pytest-codspeed)
- [CodSpeed — GitHub Actions integration docs](https://codspeed.io/docs/integrations/ci/github-actions)
- [Bencher — pytest-benchmark adapter](https://bencher.dev/learn/track-in-ci/python/pytest-benchmark/)
- [locustio/locust](https://github.com/locustio/locust)
- [zilliztech/VectorDBBench](https://github.com/zilliztech/VectorDBBench)
- [ANN-Benchmarks paper](https://arxiv.org/pdf/1807.05614)
- [HammerDB v6.0 release notes](https://www.hammerdb.com/blog/uncategorized/hammerdb-v6-0-is-now-available/)
- [HammerDB — choosing a database for TPROC-H](https://www.hammerdb.com/docs/ch11s03.html)
- [bloomberg/pytest-memray](https://github.com/bloomberg/pytest-memray)
- [Memray docs](https://bloomberg.github.io/memray/)

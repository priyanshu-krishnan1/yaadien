# Benchmarks

Not shipped in the wheel (`src/agent_memory_sdk` only) and not run by the
required-status CI checks (PH-1/PH-2). Requires a live Db2 instance for
Tiers 1–3; Tier 0 runs on any machine with no database.

> **Migration in progress (EPIC-13).** This folder is being migrated from a
> bespoke timing/report/runner framework to an assembly of four maintained OSS
> tools. The architectural decision is recorded in
> `project-management/DECISIONS.md` (entry dated 2026-08-05, "BM-1: benchmark
> architecture decision"). BM-2 executes the file changes; BM-3 through BM-6
> build the new infrastructure. Until BM-2 lands, the old harness is still
> functional — see **"Running benchmarks (current state)"** below.

---

## Architecture (target state — EPIC-13 through EPIC-19)

### The four adopted tools

| Tool | License | Role |
|---|---|---|
| **pytest-benchmark** | BSD-2-Clause | Micro-performance — warmup, calibration, outlier rejection, JSON export |
| **github-action-benchmark** | MIT | Historical trend on `gh-pages`, PR alerts; consumes `pytest-benchmark --benchmark-json` |
| **Locust** | MIT | Concurrency and scale — Python `User` classes drive the SDK directly |
| **LongMemEval** | Apache-2.0 | Real retrieval-quality dataset (500 questions, 6 ability categories) |

Supporting instrumentation: **pytest-memray** (Apache-2.0, peak-allocation
limits), **psutil** (BSD-3-Clause, RSS/CPU sampling), **pytest-codspeed**
(Apache-2.0, Valgrind instruction counting for Tier 0). All compatible with
this Apache-2.0 repository.

---

## Four-tier structure

```
Tier 0 — Smoke (every push & PR, ~90 s, blocking)
  pytest -m benchmark_micro, fake DBAPI connection, no database
  Gate: >10% instruction-count regression (pytest-codspeed — noise-free)
  Covers: _vec_to_str, metadata-filter SQL compilation, RRF fusion,
          chunk splitting, Pydantic validation, scope predicates

Tier 1 — Pull request (every PR, ~25 min, blocking)
  pytest -m benchmark_pr, Db2 container (reuses ci.yml job), 1k rows/scope
  Gate: round-trip-count regression (deterministic, runner-invariant)
        correctness invariants (leak count == 0)
  Alert (not fail): >50% wall-clock regression via github-action-benchmark

Tier 2 — Nightly (schedule: 0 3 * * *, ~2 h, non-blocking)
  pytest -m benchmark, Db2 container, 50k rows/scope
  + Locust ramp 1→50 users, mixed 70/30 R/W, leak assertions
  + LongMemEval_S IR metrics (Recall@k / MRR / nDCG@k, no LLM)
  + pytest-memray peak-RSS checks

Tier 3 — Weekly scale (schedule: 0 4 * * 0, ~4–6 h, non-blocking)
  Live Db2, pre-seeded 500k–1M-row corpus
  + Locust 200-user / 60-min soak, 1,000 agents
  + LLM-judged LongMemEval_S (full 500 questions, local Ollama — never gated)
  + Vector index build time, APPROX recall at 1M vectors
```

Wall-clock on shared runners is too noisy to gate — Tier 0 uses instruction
counting (immune), Tiers 1–2 gate on round-trip counts and correctness
invariants that are invariant to runner speed.

---

## Current file status (Phase 1b audit)

| File | LOC | Status | Story |
|---|---|---|---|
| `common/embedding_providers.py` | 199 | **KEEP** | Retained by BM-2 |
| `common/scope_gen.py` | 58 | **KEEP** | Retained by BM-2 |
| `common/cost_tracking.py` | 109 | **KEEP** | Retained by BM-2 |
| `isolation_load/run.py` | 148 | **KEEP (port to Locust)** | BM-13 (EPIC-15) |
| `retrieval_quality/consolidator.py` + `reconciler.py` | 445 | **KEEP** | Retained by BM-2 |
| `retrieval_quality/run.py` | 415 | **REWRITE** | BM-16 (EPIC-16) |
| `common/timing.py` | 67 | **DISCARD** | Deleted by BM-2 |
| `common/report.py` | 296 | **DISCARD** | Deleted by BM-2 |
| `common/llm_judge.py` | 194 | **DISCARD** | Deleted by BM-2 |
| `retrieval_quality/dataset.py` | 290 | **DISCARD** | Deleted by BM-2 |
| `latency_cost/run.py` | 112 | **DISCARD** | Deleted by BM-2 |
| `scripts/run_benchmarks.py` | ~200 | **DISCARD** | Deleted by BM-2 |

---

## Running benchmarks (current state — before BM-2 lands)

The original bespoke harness is still present and functional.

```bash
# Requires DB2_* env vars (see .env.example)
make benchmark
```

**Do not cite output of the old harness alongside vendor-reported LongMemEval
figures.** The report itself says so — it uses a synthetic 50-question dataset
and a keyword judge, not the real LongMemEval methodology.

### Options for a real retrieval-quality number (old harness)

All options are fully local and offline — no API key, no external network.

| Component | Option | Setup |
|---|---|---|
| Embeddings | `sentence-transformers` | `pip install sentence-transformers`, then `--embedding-provider sentence-transformers` |
| Embeddings | `ollama` (`nomic-embed-text`) | `pip install ollama`, start Ollama, `ollama pull nomic-embed-text`, then `--embedding-provider ollama` |
| LLM judge | `ollama` (`llama3.1:8b`) | `pip install ollama`, model pulled, then `--judge ollama` |

---

## Running benchmarks (target state — after BM-3 lands)

```bash
# Tier 0 — no database required
pytest benchmarks/ -m benchmark_micro

# Tier 1 — requires DB2_* env vars (Db2 container or live instance)
pytest benchmarks/ -m benchmark_pr --benchmark-json=results.json

# Full nightly matrix
pytest benchmarks/ -m benchmark

# Locust concurrency suite
locust -f benchmarks/locustfiles/memory_store.py --headless -u 50 -r 5 -t 15m

# LongMemEval retrieval quality (offline, no LLM — IR metrics only)
pytest benchmarks/ -m benchmark_retrieval --dataset longmemeval_s
```

## Pytest markers (added by BM-3)

| Marker | Tier | When it runs |
|---|---|---|
| `benchmark_micro` | 0 | Every push & PR (~90 s) — no database |
| `benchmark_pr` | 1 | Every PR (~25 min) — Db2 container |
| `benchmark_nightly` | 2 | Nightly schedule |
| `benchmark_scale` | 3 | Weekly / on-demand |

## What is not built from scratch

Per the DECISIONS.md BM-1 entry, these are explicitly out of scope:

- A timing/percentile harness → `pytest-benchmark`
- A report renderer or results database → `github-action-benchmark` on `gh-pages`
- A concurrency driver / worker pool → Locust
- A memory-benchmark dataset → LongMemEval
- An LLM-judge framework → LongMemEval's own scorer

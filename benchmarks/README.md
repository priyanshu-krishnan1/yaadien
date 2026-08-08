# Benchmarks

Not shipped in the wheel (`src/agent_memory_sdk` only) and not run by the
required-status CI checks (PH-1/PH-2). Requires a Db2 container for Tiers 1–3;
Tier 0 runs on any machine with no database. All tiers now use containerized
Db2 (EPIC-24 / CIB-3).

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

## Workflow map (post EPIC-24)

> **Single source of truth** for which GHA file runs when, gates what, and publishes
> where. Cross-linked from `BENCHMARK_STRATEGY.md` Phase 6 and `BENCHMARKS.md`.
> Updated by CIB-7 (EPIC-24).

| Workflow file | Trigger | Tier | Blocking? | What it gates | gh-pages output |
|:---|:---|:---|:---|:---|:---|
| `codspeed.yml` (`benchmarks` job) | `push` to `main`, `pull_request`, `workflow_dispatch` | 0 | Informational | CodSpeed CPU-simulation run, uploaded to app.codspeed.io | — |
| `benchmarks.yml` (`codspeed` job) | `workflow_dispatch` (`suite: tier0-codspeed` or `all`) | 0 | Informational | CodSpeed instruction-count smoke test (no upload) | — |
| `benchmarks.yml` (`benchmark` job) | `workflow_dispatch` (`suite: tier1-benchmark` or `all`) | 0/1 | Informational | pytest-benchmark Tier 0/1 suite, Db2 container | `benchmarks/<run_number>/` |
| `benchmarks.yml` (`locust-isolation` job) | `workflow_dispatch` (`suite: locust-isolation` or `all`) | 1 | Informational | BM-12/13 isolation gate | — |
| `benchmarks.yml` (`locust-scale` job) | `workflow_dispatch` (`suite: locust-scale` or `all`) | 1 | Informational | BM-14/15 scalability sweeps | — |
| `benchmarks.yml` (`benchmark-nightly` job) | `workflow_dispatch` (`suite: tier2-nightly` or `all`) | 2 | Informational | Full benchmark matrix + Locust + IR metrics, 50k rows | `benchmarks/nightly/<run_number>/` |
| `benchmarks.yml` (`benchmark-scale` job) | `workflow_dispatch` (`suite: tier3-scale` or `all`) | 3 | Informational | Tier 3 scale at 500k rows, containerized Db2 (CIB-3) | — |
| `benchmarks.yml` (`consolidated-report`) | Runs after all above (`if: always()`) | — | — | Fan-in summary, single gh-pages commit, optional BENCHMARKS.md commit | `index.html`, `board.html` |

**Key architectural properties (EPIC-24):**

- **Composite actions** — `setup-bench-python` and `setup-db2` (`.github/actions/`) eliminate all duplicated setup blocks (CIB-1).
- **Suite selector** — `workflow_dispatch` input `suite` lets a dispatcher run only the desired tier without paying for all 7 jobs (CIB-4).
- **Single gh-pages writer** — `consolidated-report` is the only job that pushes to `gh-pages` and commits `BENCHMARKS.md`, eliminating concurrent-write races (CIB-5).
- **Shared output schema** — every job emits `results.jsonl` with a common envelope; `scripts/render_results_summary.py` consumes all of them in one pass (CIB-6).
- **Concurrency guard** — `concurrency: group: benchmarks-${{ github.ref }}, cancel-in-progress: false` prevents overlapping dispatches from racing on the single writer (CIB-7).
- **All-container Db2** — Tier 3 now uses containerized Db2, eliminating the `live-db2` environment and `LIVE_DB2_*` secrets dependency (CIB-3).

---

## Four-tier structure

```
Tier 0 — Smoke (workflow_dispatch: tier0-codspeed, ~10 min, informational)
  pytest -m benchmark_micro, fake DBAPI connection, no database
  Gate: CodSpeed instruction-count smoke test (noise-free)
  Covers: _vec_to_str, metadata-filter SQL compilation, RRF fusion,
          chunk splitting, Pydantic validation, scope predicates

Tier 1 — Benchmark + Locust (workflow_dispatch: tier1-benchmark / locust-isolation / locust-scale)
  pytest -m benchmark_pr, containerized Db2, 1k rows/scope
  Gate: round-trip-count regression (deterministic, runner-invariant)
        correctness invariants (leak count == 0)
  Alert (not fail): >50% wall-clock regression via github-action-benchmark

Tier 2 — Nightly (~2 h, workflow_dispatch: tier2-nightly, non-blocking)
  pytest -m benchmark, containerized Db2, 50k rows/scope
  + Locust ramp 1→50 users, mixed 70/30 R/W, leak assertions
  + LongMemEval_S IR metrics (Recall@k / MRR / nDCG@k, no LLM)
  + pytest-memray peak-RSS checks

Tier 3 — Scale (~2 h, workflow_dispatch: tier3-scale, non-blocking)
  Containerized Db2 (CIB-3), pre-seeded 500k-row corpus
  + Locust 200-user, 15 min soak
  + LLM-judged LongMemEval_S (full 500 questions, local Ollama — never gated)
  + Vector index build time
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

---

## EPIC-16: Real LongMemEval dataset (BM-16 through BM-19)

### LongMemEval dataset (BM-16)

| Field | Value |
|---|---|
| **Name** | LongMemEval |
| **Authors** | Wu et al. (xiaowu0162), ICLR 2025 |
| **License** | Apache-2.0 |
| **HuggingFace repo** | `xiaowu0162/longmemeval` |
| **Paper** | arXiv 2410.10813 — *LongMemEval: Benchmarking Long-Context Language Models on Long-term Interactive Memory* |
| **Questions** | 500 (longmemeval_s / longmemeval_m / longmemeval_oracle splits) |
| **Categories** | single-session-user, single-session-assistant, multi-session, temporal-reasoning, knowledge-update, abstention |

**Attribution:** This benchmark suite uses the LongMemEval dataset distributed
under Apache-2.0. The evaluation methodology (judge prompt, scoring, category
structure) is based on the original paper. This repository's results are **not**
directly comparable to vendor-reported LongMemEval figures unless:
1. The same embedding provider is used.
2. The same judge model (GPT-4o) is used.
3. All methodology deviations are explicitly enumerated.

Every result produced by `benchmarks/quality/lme_judge.py` (BM-18) lists its
deviations from the published methodology in `deviation_notes`.

### Running the EPIC-16 suite

**Prerequisites:**

```bash
# Install dataset loader (one-time)
pip install datasets

# Warm the local cache (one-time, ~200 MB)
python -c "from benchmarks.quality.longmemeval_adapter import load_longmemeval; load_longmemeval('longmemeval_s')"

# Pull embedding and judge models (Ollama, one-time)
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

**Run the deterministic IR metrics (Tier 2 — nightly, no LLM):**

```bash
# All 500 questions, Recall@k / MRR / nDCG@k, no LLM
pytest benchmarks/quality/ -m benchmark_nightly -k "ir_config_matrix_longmemeval_s" -v
```

**Run the LLM-judged accuracy (Tier 3 — weekly, requires Ollama):**

```bash
# Full 500-question judged run + judge variance measurement
pytest benchmarks/quality/ -m benchmark_scale -k "lme_judge" -v
```

**Run the configuration matrix (Tier 2/3 — BM-19):**

```bash
# IR matrix: vector vs hybrid, consolidation on/off, reconciliation on/off
pytest benchmarks/quality/ -m benchmark_nightly -k "config_matrix_longmemeval_s" -v

# Scale hypothesis: longmemeval_m (multi-day sessions)
pytest benchmarks/quality/ -m benchmark_scale -k "config_matrix_longmemeval_m" -v
```

**Smoke test (unit tests only, no Db2, no Ollama):**

```bash
pytest benchmarks/quality/ -m benchmark_micro -v
```

### Key files

| File | Story | What it does |
|---|---|---|
| `benchmarks/quality/longmemeval_adapter.py` | BM-16 | Download/cache dataset; map sessions → `add_messages()`; evidence → Recall@k |
| `benchmarks/quality/ir_metrics.py` | BM-17 | Recall@k, MRR, nDCG@k — deterministic, no LLM, CI-safe |
| `benchmarks/quality/lme_judge.py` | BM-18 | Official LongMemEval judge prompt via Ollama; judge variance; BENCHMARKS.md append |
| `benchmarks/quality/test_config_matrix.py` | BM-19 | Configuration matrix: vector/hybrid, consolidation on/off, reconciliation on/off, top-k sweep, _s vs _m |

### Cache directory

The default cache location is `~/.cache/longmemeval/`. Override with the
`LONGMEMEVAL_CACHE_DIR` environment variable. In CI, set this to a mounted
volume or pre-warmed artifact so runs are fully offline.

# Benchmarks

On-demand measurement harness for agent-memory-sdk. **Not** shipped in the
wheel (same treatment as `project-management/` — the hatchling wheel target
only lists `src/agent_memory_sdk`) and **not** run by CI (PH-1/PH-2) — it
requires a live Db2 instance and, for the retrieval-quality suite, an
`EmbeddingProvider`/LLM judge.

## Why not CI

PH-1/PH-2 are required-status checks that run on every PR. Wiring this
harness into them would mean every PR either fails (no Db2/API credentials
in the CI environment) or burns real API cost on every push. Run it
yourself, on demand.

## Quick start (zero setup)

```bash
# Requires DB2_* env vars set (see .env.example / project-management/INTEGRATION_TESTING.md)
make benchmark
```

This runs all three suites with the dependency-free defaults:
`--embedding-provider hashing` (a feature-hashed bag-of-words vector, no
ML dependency, no network) and `--judge keyword` (a keyword-overlap
heuristic). **These defaults are explicitly NOT comparable to
vendor-reported LongMemEval figures** — the generated report says so in
bold. They exist so the harness is runnable with nothing beyond a Db2
connection, to sanity-check the code path end-to-end.

## Free-tier options for a real retrieval-quality number

| Component | Free-tier option | Setup |
|---|---|---|
| Embeddings | `sentence-transformers` (local, no API key, no rate limit) | `pip install sentence-transformers`, then `--embedding-provider sentence-transformers` |
| Embeddings | Gemini `text-embedding-004` (hosted, free tier) | `pip install google-generativeai`, set `GEMINI_API_KEY`, then `--embedding-provider gemini` |
| LLM judge | Gemini `gemini-1.5-flash` (hosted, free tier, generous quota) | `pip install google-generativeai`, set `GEMINI_API_KEY`, then `--judge gemini` |

Recommended real-number run:

```bash
pip install sentence-transformers google-generativeai
export GEMINI_API_KEY=...   # https://aistudio.google.com — free tier
make benchmark ARGS="--embedding-provider sentence-transformers --judge gemini --dataset-size 10"
```

Gemini's free tier has a requests-per-day cap — for a larger dataset,
increase `--dataset-size` gradually or spread runs across a couple of days
rather than requesting a huge batch at once.

## Suites

Run one at a time with `--suite {retrieval,latency,isolation}` (default `all`):

1. **Retrieval quality** (`benchmarks/retrieval_quality/`) — a synthetic,
   LongMemEval-shaped (arXiv 2410.10813) dataset covering the five ability
   categories (extraction, multi-session reasoning, temporal reasoning,
   knowledge updates, abstention), run through `remember()`/`search()` and
   scored by the configured judge.
2. **Latency/cost** (`benchmarks/latency_cost/`) — per-call latency
   percentiles for `remember()`/`search()`, plus estimated LLM token cost
   *only* when `--consolidator mock` wires in a cost-tracked hook (the
   default `--consolidator none` reports the SDK's real $0.00 no-op
   baseline).
3. **Isolation under load** (`benchmarks/isolation_load/`) — many concurrent
   threads across synthetic tenants/agents hammering `search()`/`list_all()`,
   asserting zero cross-scope leakage under real concurrency (not just the
   single-threaded, mocked-cursor conditions VER-5's audit checked).

## Output

Writes `project-management/BENCHMARKS.md` (override with `--output`),
stamped with the exact embedding provider, judge, dataset size, and
configuration used — see `benchmarks/common/report.py`.

## A note on the isolation suite and shared infrastructure

If your `DB2_*` env vars point at a shared dev instance rather than a
personal/local one, be mindful before cranking `--tenants`/`--workers` way
up — it's real concurrent load against a real database. The defaults
(10 tenants × 2 agents, 20 workers, 5 ops/worker = 100 write ops) are
intentionally modest.

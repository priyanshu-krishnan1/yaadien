# Running the LLM judge in GitHub Actions on the free tier

Research note, 2026-08-08. Scope: how to make BM-18 / AGQ-2 / AGQ-3 / AGQ-4
actually execute in `.github/workflows/benchmark-suite.yml` without paid
runners or paid inference credit.

---

## 0. The blocking finding: no judge is running today

`benchmark-suite.yml` invokes four judge steps with `--judge-model llama3.1:8b`
(lines ~407, ~420, ~436, plus `benchmarks/quality/` via pytest). **Nothing in
`.github/` ever installs Ollama or pulls a model** — `grep -ri ollama .github/`
returns zero hits.

Every judge entry point (`OllamaAgentJudge`, `OllamaGroundednessJudge`,
`OllamaLMEJudge`, `benchmarks/agent_quality/coherence.py`) hard-requires a live
daemon at `http://localhost:11434` and has no offline fallback. So:

| Step | Guard | Actual behaviour today |
|---|---|---|
| `Check task success rate` (AGQ-2) | none | hard-fails the job |
| `Check answers are grounded` (AGQ-3) | `continue-on-error` + `\|\| true` | silently produces no `agq3.json` |
| `Check answer coherence quality` (AGQ-4) | `continue-on-error` + `\|\| true` | silently produces no `agq4.json` |
| `Measure memory recall accuracy` (BM-18) | `continue-on-error` | falls through to the `benchmark_micro` backup path |

Any AGQ scorecard produced by a past run is therefore either empty or from the
fallback path — not judged. Fix this before tuning anything else.

---

## 1. Which free option

Three candidates, only one survives the volume requirement.

### Judge call volume (current defaults)

| Benchmark | Calls |
|---|---|
| AGQ-2 tasks | ~50 |
| AGQ-3 groundedness | 4/cat x 6 cats x 2 conditions = **48** |
| AGQ-4 coherence | 24 q x 2 dimensions x 2 conditions = **96** |
| BM-18 full `longmemeval_s` | 500 + (30 x 3 variance) = **590** |
| **Nightly total** | **~200** (AGQ tier) / **~790** (with full BM-18) |

### Option A — GitHub Models (free inference via `GITHUB_TOKEN`)

Free, no card, and the runner's `GITHUB_TOKEN` already carries `models:read`.
But the free quota is **per user per model per day**, and it is small:

| Tier | RPM | **Requests/day** | Tokens/request | Concurrent |
|---|---|---|---|---|
| Low (gpt-4o-mini class) | 15 | **150** | 8k in / 4k out | 5 |
| High (gpt-4o class) | 10 | **50** | 8k in / 4k out | 2 |

150/day does not cover a 200-call AGQ tier, let alone 790. It is also shared
with your interactive Copilot/playground usage, so a benchmark run can be
starved by unrelated activity. **Verdict: viable only as a 30-question
smoke/tie-break tier, not as the primary judge.**

### Option B — third-party free API tiers (Groq, Cerebras, Google AI Studio)

Faster and higher-volume than GitHub Models, but all three require a secret in
the repo, all have undocumented/shifting daily caps, and free-tier prompts are
generally used for training. For a benchmark that must be reproducible and
citable months later, a quota that can change without notice is a poor
foundation. **Verdict: not the primary judge.**

### Option C — Ollama on the runner (recommended)

Zero quota, zero secrets, zero data egress, and it is what the code already
assumes. The constraint is CPU-only inference on a small runner.

**Runner budget.** `ubuntu-latest` is 4 vCPU / 16 GB on public repos (free,
unlimited minutes) and 2 vCPU / 7 GB on private repos (2,000 min/month). The
`benchmark-nightly` job also runs a Db2 container in the same job, which alone
wants ~4 GB.

**Model choice — this is the single most important change.** `llama3.1:8b` at
Q4 needs ~5.5 GB resident. Alongside Db2 that OOM-kills the runner with a silent
exit 137 on a 7 GB runner, and is marginal on 16 GB. Drop to a 3B-class model:

| Model | Disk | Resident | Fits with Db2? |
|---|---|---|---|
| `llama3.1:8b` (current) | 4.7 GB | ~5.5 GB | no (7 GB), marginal (16 GB) |
| `llama3.2:3b` | 2.0 GB | ~3.5 GB | yes |
| `qwen2.5:3b-instruct` | 1.9 GB | ~3.4 GB | yes |

The judge task here is binary/ordinal classification (`CORRECT`/`INCORRECT`,
`SUCCESS`/`FAILURE`, a 1-5 score) with a supplied gold answer. That is well
within 3B capability; it is not open-ended reasoning. Re-baseline the numbers
after switching and record the model change in `deviation_notes` — the existing
`deviation_notes` field already exists for exactly this.

### Does it fit in the time budget?

RAM and disk are the easy constraints. **Wall clock is the binding one**, and it
is decided by output length, not input length — CPU generation is roughly an
order of magnitude slower than prefill.

Order-of-magnitude figures for `llama3.2:3b` Q4 on 4 vCPU, no GPU: prefill
~40-80 tok/s, generation ~6-10 tok/s. Against the actual prompts in this repo:

| Benchmark | Calls | Output tokens | Est. per call | Est. total |
|---|---|---|---|---|
| AGQ-2 tasks | ~50 | ~2 | 10-15 s | ~10 min |
| AGQ-3 groundedness | 48 | ~200 | 35-65 s | **30-50 min** |
| AGQ-4 coherence | 96 | ~20 | 15-20 s | ~30 min |
| BM-18 full `longmemeval_s` | 590 | ~2 | 8-12 s | **80-120 min** |
| **Total, serial** | | | | **~2.5-3.5 h** |

Two consequences:

1. **The nightly job's current `timeout-minutes: 120` is not enough** for the
   full set, and the hard GitHub ceiling is 6 h per job. Sharding (section 2)
   stops being a nice-to-have and becomes required for BM-18 at 500 questions.
2. **AGQ-3 costs more than BM-18 per call** despite being 12x smaller, purely
   because its prompt asks for free-form rationale before the score. Making that
   prompt score-first is the cheapest single speedup available.

These are estimates, not measurements. Before planning around them, run a
one-off `workflow_dispatch` probe job that installs Ollama and times 10 real
AGQ-3 calls on the runner — the true number depends on the runner's CPU
generation and the actual `top_k=5` context length, and it is 15 minutes of
work to replace the estimate with a fact.

---

## 2. Architecture: split generation from judging

The highest-leverage structural change, independent of which model you pick.

**Today:** seed corpus -> retrieve -> judge, all in one job that also runs Db2
and Locust for 20+ minutes. Any judge failure loses the retrieval work; any
re-judge re-seeds a 50k corpus.

**Proposed:**

```
job: generate          job: judge (matrix shard 0..N)      job: aggregate
  Db2 + seed             no Db2, no Locust                   no Db2
  retrieve               Ollama + 3B model                   merge shards
  emit predictions.json  read predictions.json               scorecard + summary
  upload artifact        emit verdicts-${shard}.json
```

Why this matters on the free tier specifically:

- **No Db2/Ollama memory contention.** The judge job has the full 16 GB.
- **Free parallelism is the real lever.** Free accounts get 20 concurrent jobs.
  Sharding 590 BM-18 questions across 10 matrix jobs turns a ~50 min judge pass
  into ~5 min of wall clock, at zero cost (public repo = unlimited minutes).
- **Re-judgeable.** Changing the judge prompt or model re-runs only the judge
  job against the stored `predictions.json` — no 2.6-hour reseed. This directly
  reduces the EPIC-34 runtime blowout.
- **Reviewable.** `predictions.json` is a diffable artifact; you can eyeball
  what the judge actually saw.

This is standard eval-harness practice (promptfoo, inspect-ai, braintrust all
separate generation from scoring) and it happens to map exactly onto free CI
constraints.

---

## 3. The Ollama setup that works in Actions

Four load-bearing details. Skipping any one of them produces an intermittent
failure that looks like something else.

### 3.1 Health-check poll (prevents the startup race)

`ollama serve &` followed immediately by `ollama pull` fails intermittently —
the pull starts before the server binds. Poll `/api/tags`.

### 3.2 Model cache (8-12 min -> ~35 s)

Runners do not persist `~/.ollama/models` between jobs. Cold pull of a 2 GB
model is 8-12 minutes; a warm `actions/cache` restore is ~35 s. Note the free
cache budget is 10 GB/repo with 7-day eviction on unused entries — a 2 GB model
is fine, an 8B model at 4.7 GB starts crowding out your uv/pip caches.

### 3.3 Hard `timeout-minutes` on every judge step

Ollama can hang indefinitely on a malformed or oversized prompt with no error.
Without a step-level timeout the hang consumes the job's full 120 min budget.

### 3.4 Explicit `num_ctx` — silent-truncation correctness bug

`OllamaAgentJudge.judge()` (`tasks.py:747`), `OllamaGroundednessJudge`
(`groundedness.py:~305`), `coherence.py:~325` and `lme_judge.py:~190` all pass
`options = {"seed": ...}` and nothing else. Ollama then applies its default
context window (4096 tokens). LongMemEval groundedness prompts embed the full
retrieved context and routinely exceed that — **the judge silently sees a
truncated prompt and returns a verdict on partial evidence.** This corrupts
scores without failing anything, which is worse than a crash.

Set explicitly on every judge call:

```python
options = {
    "seed": effective_seed,
    "temperature": 0.0,       # currently unset -> defaults to 0.8
    "num_ctx": 8192,          # must exceed the longest groundedness prompt
    "num_predict": <per-judge>,
}
```

`temperature` is currently unset, so every judge has been running at Ollama's
default 0.8. That alone explains a large share of the judge non-determinism
BM-18's variance harness is measuring. Set it to 0 for the main pass and vary
only the seed in the variance subset — otherwise you are measuring sampling
noise, not judge instability.

**`num_predict` must be set per judge, not globally.** The four prompts ask for
very different output lengths, and a blanket low cap silently truncates the
verdict line:

| Judge | Prompt asks for | `num_predict` |
|---|---|---|
| BM-18 `LME_JUDGE_PROMPT` | `CORRECT` / `INCORRECT` only | 8 |
| AGQ-2 `AGENT_JUDGE_PROMPT` | `SUCCESS` / `FAILURE` only | 8 |
| AGQ-4 coherence + fluency | score + one sentence, same line | 48 |
| AGQ-3 `GROUNDEDNESS_JUDGE_PROMPT` | free-form claim analysis, **then** `Score: <n>` on a new line | **384** |

AGQ-3 is the trap: it explicitly instructs "First, briefly explain which claims
are supported... Then output your final score on a new line." Capping it at 8
tokens cuts the response off before the `Score:` line ever appears, and
`_parse_score` then fails on every single call. If you want AGQ-3 to be cheap,
change the prompt to score-first (`Score: <n>` on line 1, rationale after) —
then a low cap is safe and you save ~30 s per call.

### 3.5 Daemon env

```yaml
env:
  OLLAMA_KEEP_ALIVE: "-1"      # never unload between calls (default 5m unload
                               # mid-run costs a full reload)
  OLLAMA_NUM_PARALLEL: "2"     # 4 vCPU; >2 thrashes
  OLLAMA_MAX_LOADED_MODELS: "1"
```

---

## 4. Drop-in workflow fragment

Add to `.github/actions/` as a composite action (`setup-ollama-judge`) so the
nightly and any future judge-only job share it.

```yaml
# .github/actions/setup-ollama-judge/action.yml
name: "Set up Ollama judge"
description: "Install Ollama, restore the model cache, and wait for readiness."

inputs:
  model:
    description: "Ollama model tag"
    default: "llama3.2:3b"

runs:
  using: composite
  steps:
    - name: Restore model cache
      uses: actions/cache@v4
      with:
        path: ~/.ollama/models
        key: ollama-${{ inputs.model }}-${{ runner.os }}
        restore-keys: |
          ollama-${{ inputs.model }}-

    - name: Install and start Ollama
      shell: bash
      env:
        OLLAMA_KEEP_ALIVE: "-1"
        OLLAMA_NUM_PARALLEL: "2"
        OLLAMA_MAX_LOADED_MODELS: "1"
      run: |
        curl -fsSL https://ollama.com/install.sh | sh
        nohup ollama serve > "${RUNNER_TEMP}/ollama.log" 2>&1 &
        for _ in $(seq 1 30); do
          curl -sf http://localhost:11434/api/tags >/dev/null && break
          sleep 2
        done
        curl -sf http://localhost:11434/api/tags >/dev/null || {
          echo "::error::Ollama daemon never became ready"
          cat "${RUNNER_TEMP}/ollama.log"
          exit 1
        }

    - name: Ensure model present
      shell: bash
      run: ollama pull "${{ inputs.model }}"

    - name: Warm the model
      shell: bash
      run: |
        curl -sf http://localhost:11434/api/generate \
          -d '{"model":"${{ inputs.model }}","prompt":"ok","stream":false,
               "options":{"num_predict":1}}' >/dev/null

    - name: Print Ollama log on failure
      if: failure()
      shell: bash
      run: cat "${RUNNER_TEMP}/ollama.log" || true
```

Then in `benchmark-nightly`, before the AGQ steps:

```yaml
      - name: Set up LLM judge
        if: inputs.scenario != 'locust'
        uses: ./.github/actions/setup-ollama-judge
        with:
          model: llama3.2:3b
```

and change every `--judge-model llama3.1:8b` to `--judge-model llama3.2:3b`,
adding `timeout-minutes: 20` to each judge step.

---

## 5. Gating policy

Judge scores fluctuate. Rules that hold up:

1. **Never gate a merge on an LLM-judge score.** Keep the PR gate on the
   deterministic IR metrics (recall@k, nDCG, exact match) that need no model.
   The repo's existing `benchmark_pr` / `benchmark_nightly` / `benchmark_scale`
   marker tiering already encodes this — keep judges out of `benchmark_pr`.
2. **Threshold on a delta, not an absolute.** Fail on
   `accuracy < baseline - 2*sigma`, where sigma comes from BM-18's own variance
   subset. An absolute floor produces flaky red builds.
3. **Report, don't fail.** Write the scorecard to `$GITHUB_STEP_SUMMARY` and,
   on regression, open an issue (the workflow already has `issues: write`).
4. **Pin and stamp the judge.** `judge_model`, `temperature`, `num_ctx`, `seed`
   and quantization tag all belong in the result JSON. A run judged by
   `llama3.2:3b` at `num_ctx=8192` is not comparable to one judged by
   `llama3.1:8b` at 4096, and without the stamp you cannot tell them apart six
   months later.

---

## 6. Recommended order of work

| # | Change | Effect |
|---|---|---|
| 1 | Add `setup-ollama-judge` composite action + wire into nightly | judges run at all |
| 2 | `llama3.1:8b` -> `llama3.2:3b` everywhere | fits alongside Db2; ~2x faster |
| 3 | Add `temperature=0`, `num_ctx=8192`, `num_predict=8` to all four judges | fixes silent truncation + most of the variance |
| 4 | Remove `\|\| true` from AGQ-3/AGQ-4 once 1-3 land | stop hiding failures |
| 5 | Split generate/judge into separate jobs + shard matrix | re-judge without reseeding; ~10x judge wall clock |
| 6 | GitHub Models as an optional 30-question cross-check tier | sanity-check the 3B judge against a frontier model, free |

Items 1-4 are small and unblock the benchmark. Item 5 is the EPIC-34 runtime
win. Item 6 is optional.

---

## Sources

- [Running Ollama in GitHub Actions CI: What Actually Works](https://brokeit.dev/posts/running-ollama-in-github-actions-ci-what-actually-works/)
- [GitHub Docs — Prototyping with AI models (rate limits table)](https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models)
- [GitHub Blog — Solving the inference problem for open source AI projects with GitHub Models](https://github.blog/ai-and-ml/llms/solving-the-inference-problem-for-open-source-ai-projects-with-github-models/)
- [GitHub Docs — Actions limits](https://docs.github.com/en/actions/reference/limits)
- [Best Free LLM API Tiers in 2026](https://wetheflywheel.com/en/ai-model-access/free-llm-api-tiers-2026/)
- [GitHub Actions for LLM Eval Pipelines](https://tenki.cloud/blog/github-actions-llm-evaluation-pipeline)

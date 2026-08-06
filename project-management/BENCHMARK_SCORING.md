# Memory Benchmark Score — Composite Scoring Model

The **Memory Benchmark Score (MBS)** is a 0-100 composite that combines three
independently-meaningful sub-scores on the Oracle (performance), Mem0 (retrieval
accuracy), and Microsoft Foundry (agent quality) axes. It is additive context — a
navigation aid — not a replacement for the underlying numbers. Per the project's
existing discipline, BENCHMARKS.md's Run B already reports per-category deltas rather
than a single average; this composite follows the same principle: the per-sub-score
numbers carry the real information, and the composite only exists to give a quick
health signal across all three axes at once.

---

## 1. Performance Sub-Score (Oracle axis)

### What it measures

How fast each instrumented SDK operation is relative to the committed baselines from
BM-27. The comparison is always *self-relative* — this SDK's micro-op latencies are not
comparable to Oracle's DB-level throughput figures and the score must not imply they
are.

### Input sources

| Source | Description |
|---|---|
| pytest-benchmark JSON output | Produced by EPIC-14/15 micro, latency, read, and write test suites |
| `benchmarks/baselines.json` | Committed baseline file created by BM-27 (EPIC-19) |

### Raw unit

Measured mean latency for each operation (seconds or ms, consistent with the
pytest-benchmark JSON).

### Normalization formula

For each operation `i` with measured mean `m_i` and committed baseline `b_i`:

```
pct_i = m_i / b_i          # ratio vs. baseline (1.0 = exactly at baseline)

op_score_i:
  pct_i ≤ 1.0  → 100       (at or faster than baseline)
  pct_i = 1.5  → 75        (alert threshold per BM-20: 150 % of baseline)
  pct_i ≥ 3.0  → 0         (fail threshold: 300 % of baseline)
  1.0 < pct_i < 3.0        → linear interpolation between the anchors above
```

Interpolation between the three anchors is piecewise-linear:

```
[1.0 → 100, 1.5 → 75]:   op_score = 100 − 25 × (pct_i − 1.0) / 0.5
  (drop of 25 over range 0.5 → slope = 50 pts per unit)
[1.5 → 75,  3.0 → 0]:    op_score = 75  − 75 × (pct_i − 1.5) / 1.5
  (drop of 75 over range 1.5 → slope = 50 pts per unit)
```

```
P-score = mean(op_score_i)   for all operations i in the BM-27 baseline set
```

### CI tier

Available from Tier 1 and Tier 2 — no live Db2 connection required for the comparison;
only the stored pytest-benchmark JSON and `benchmarks/baselines.json` are needed.

### Missing input handling

If `benchmarks/baselines.json` is absent or the pytest-benchmark JSON for any operation
is not found:

```
P-score: MISSING (BM-27 baselines not committed or pytest-benchmark JSON not found)
```

---

## 2. Retrieval-Accuracy Sub-Score (Mem0 axis)

### What it measures

How accurately the SDK retrieves the right memories when queried — both by deterministic
offline metrics and by LLM-judged end-to-end accuracy.

### Input sources

| Component | Source | CI-tier |
|---|---|---|
| **Deterministic** | BM-17 (EPIC-16): Recall@k, MRR, nDCG@k outputs | CI-gatable (Tier 2+) |
| **Judged** | BM-18 (EPIC-16): LLM-judged end-to-end accuracy | Nightly-only, non-deterministic |

### Raw units

- Deterministic: Recall@k ∈ [0, 1] (primary signal for the deterministic component)
- Judged: LLM-judge accuracy ∈ [0 %, 100 %]

### Normalization formula

```
det_score    = Recall@k × 100          (0-1 → 0-100)
judged_score = LLM-judge accuracy      (already 0-100)
```

**When both components are available:**

```
R-score = 0.5 × det_score + 0.5 × judged_score
```

Equal weight because both measure retrieval quality from complementary angles
(deterministic token-overlap vs. semantic judge agreement).

**When only the deterministic component is available** (i.e., every CI run that is
not a nightly):

```
R-score (partial, deterministic only) = det_score
```

Mark the judged component as `MISSING — nightly only` in the scorecard output.

### CI-gate rule

> **The CI-gate consumer must never receive the judged half of R-score.**
>
> `scorecard.py` must check whether BM-18 output is present before including it in
> R-score and must label the result clearly. A partial R-score is valid for the
> purposes of performance gating; an MBS that incorporates a judged R-score is
> labelled "nightly" in the scorecard output.

### Missing input handling

Report each component separately:

```
R-score (deterministic): MISSING (BM-17 output not found)
R-score (judged):        MISSING (BM-18 output not found — nightly only)
```

---

## 3. Agent-Quality Sub-Score (Microsoft axis)

### What it measures

End-to-end quality of the agent's responses when backed by the SDK, judged on
task-completion, groundedness, coherence, and fluency using the Microsoft Foundry
evaluator suite.

### Input sources

| Metric | Source | Evaluator scale |
|---|---|---|
| Pass¹ rate | AGQ-2 (EPIC-21): task-completion, first-try | Binary per query → rate ∈ [0, 1] |
| Pass⁵ rate | AGQ-2 (EPIC-21): task-completion, best-of-5 | Binary per query → rate ∈ [0, 1] — **supplementary only** |
| Groundedness mean | AGQ-3 (EPIC-21) | 1-5 Likert (Microsoft Foundry convention) |
| Coherence mean | AGQ-4 (EPIC-21) | 1-5 Likert |
| Fluency mean | AGQ-4 (EPIC-21) | 1-5 Likert |

### Normalization formula

```
pass1_pct        = Pass¹ rate × 100             (0-1 → 0-100)
groundedness_norm = groundedness_mean × 20      (1-5 → 0-100)
coherence_norm    = coherence_mean × 20         (1-5 → 0-100)
fluency_norm      = fluency_mean × 20           (1-5 → 0-100)

A-score = (pass1_pct + groundedness_norm + coherence_norm + fluency_norm) / 4
```

Pass⁵ is reported alongside the scorecard for informational purposes but is **not**
included in the formula. Pass¹ is the primary task-completion signal because it
measures first-try success without retry advantage.

### CI tier

All inputs are LLM-judged and non-deterministic. A-score is **nightly-only**; it is
never a CI gate.

### Missing input handling

```
A-score: MISSING (EPIC-21 AGQ suite not yet run)
```

---

## 4. Composite Formula

```
MBS = w_P × P-score + w_R × R-score + w_A × A-score
```

### Default weights

| Weight | Default | Axis |
|---|---|---|
| `w_P` | 1/3 ≈ 0.333 | Performance (Oracle) |
| `w_R` | 1/3 ≈ 0.333 | Retrieval accuracy (Mem0) |
| `w_A` | 1/3 ≈ 0.334 | Agent quality (Microsoft) |

### Justification for equal thirds

- No axis has been shown to be more important than the others; claiming otherwise
  without empirical evidence would be false precision.
- Equal weights make the composite easy to audit: an MBS of 70 means the three
  sub-scores average to ~70, which is immediately interpretable.
- The composite is a communication tool. The per-sub-score numbers carry the real
  information. Any deviation from equal thirds must be justified in
  [`DECISIONS.md`](./DECISIONS.md) before it is merged.

---

## 5. Configurable Weights

Weights are **not hardcoded**. They are read from `benchmarks/scoring_weights.yaml`
and consumed by `benchmarks/common/scorecard.py` (UNI-3).

### Schema

```yaml
# benchmarks/scoring_weights.yaml
weights:
  performance: 0.333
  retrieval_accuracy: 0.333
  agent_quality: 0.334  # must sum to 1.0
```

### Validation rule

`scorecard.py` must assert:

```python
assert abs(sum(weights.values()) - 1.0) <= 0.001, \
    "scoring_weights.yaml: weights must sum to 1.0 (±0.001)"
```

If the file is **missing** or the sum constraint is **violated**, `scorecard.py` must
raise a clear error and exit non-zero. There is no silent default fallback — a missing
or invalid config must be immediately visible so that an accidental change to the weight
file cannot silently alter reported scores.

---

## 6. Missing Input Policy

A missing sub-score input is **not** the same as a zero score.

| Scenario | Scorecard output |
|---|---|
| `benchmarks/baselines.json` absent, or pytest-benchmark JSON not found | `P-score: MISSING (BM-27 baselines not committed or pytest-benchmark JSON not found)` |
| BM-17 output absent | `R-score (deterministic): MISSING (BM-17 output not found)` |
| BM-18 output absent | `R-score (judged): MISSING (BM-18 output not found — nightly only)` |
| EPIC-21 AGQ suite not run | `A-score: MISSING (EPIC-21 AGQ suite not yet run)` |

**MBS is only computed when all three sub-scores are available.** If any sub-score is
MISSING, the composite is reported as:

```
MBS: INCOMPLETE — see sub-score details
```

**Partial retrieval-accuracy availability** (deterministic only, no BM-18 output):
report `R-score (partial, deterministic only): <value>` and exclude that R-score from
the MBS composite (since the partial score is not comparable to a run where both
components are available).

---

## 7. Thresholds and Alert Levels

### Composite MBS

| MBS | Status |
|---|---|
| ≥ 80 | 🟢 **Healthy** |
| 60 – 79 | 🟡 **Monitor** — review sub-scores for root cause |
| < 60 | 🔴 **Alert** — at least one sub-score likely at or below its fail threshold |

### Per-sub-score informational thresholds

These thresholds inform human review. Only P-score and the deterministic component of
R-score are CI-gatable.

| Sub-score | Threshold | Meaning |
|---|---|---|
| P-score | < 75 | At least one operation is at the 150 % baseline alert threshold (BM-20) |
| R-score (deterministic only) | < 80 | Recall@k below healthy floor |
| A-score | < 60 | Agent-quality mean below 3.0/5.0 on Foundry's pass threshold (3.0 × 20 = 60) |

---

## 8. Relationship to Existing Reports

- This composite does **not** replace BENCHMARKS.md's per-suite sections. Those remain
  the primary record.
- The MBS block is rendered as an additional "Summary" section at the top of the
  BENCHMARKS.md scorecard by `benchmarks/common/scorecard.py` (implemented in UNI-3).
- Per-category numbers from each suite are always visible alongside the composite — the
  scorecard never collapses multi-category results into a single number without also
  showing the breakdown.
- This matches BENCHMARKS.md's Run B discipline: per-category deltas are always
  present, the composite is layered on top.

---

## 9. Citation / Verification Record

Microsoft Foundry evaluator names and scales were verified live on **2026-08-08** from:

> [learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators)

Scales confirmed at that date:

| Evaluator | Scale |
|---|---|
| Coherence | 1-5 Likert |
| Fluency | 1-5 Likert |
| Groundedness | 1-5 Likert |
| Task Completion | Binary Pass/Fail |
| Task Adherence | Binary Pass/Fail |
| Intent Resolution | Binary (derived from 1-5) |
| Tool Call Accuracy | Binary (derived from 1-5) |

> ⚠️ **Re-verify before any external-facing publication.** The Foundry evaluator set
> changed twice in 2026 already. The scales above must be re-confirmed against the live
> documentation before any scorecard that references them is published externally.

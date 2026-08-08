# Making `Benchmarks (unified)` run faster on GitHub Actions

Research note, 2026-08-08. Scope: `.github/workflows/benchmarks.yml` (1298 lines, 7 jobs),
`.github/actions/setup-db2`, `.github/actions/setup-bench-python`.

Follow-on to EPIC-25 / CIB-1..CIB-7, which restructured this workflow but optimised for
*correctness and consolidation*, not wall clock. Several CIB decisions are revisited here
with new reasoning — noted inline.

---

## 1. Where the time actually goes

`suite: all` job graph:

```
db2-ready ──┬─> benchmark          (60 min cap)
            ├─> locust-isolation   (45 min cap)   ─┐
            ├─> locust-scale       (60 min cap)    ├─> consolidated-report (15 min cap)
            ├─> benchmark-nightly  (120 min cap)   │
            └─> benchmark-scale    (120 min cap)  ─┘
codspeed ──────────────────────────────────────────┘   (10 min cap, parallel)
```

Critical path ≈ `db2-ready` + `max(nightly, scale)` + `consolidated-report`.

Per-job fixed overhead, before a single benchmark executes:

> **Timings below are pre-measurement estimates** derived from reading the workflow YAML,
> not from a measured run. See §5 for the `gh` CLI command to replace these with real data
> after the next `suite: all` dispatch.
>
> **Post Phase-1 status (CIW-2..CIW-7 landed):** `db2-ready` gate deleted, healthcheck
> interval reduced to 5 s, DDL probe uses exponential backoff, uv replaces pip.
> The "pip install" and "db2-ready pull" rows are now eliminated; re-measure before
> committing to Phase-2/Phase-3 work.

| Phase | Estimated Cost | Paid by | Status after Phase 1 |
|---|---|---|---|
| `db2-ready` docker pull (~3 GB from icr.io) | 4–6 min | blocked 5 jobs | **Eliminated (CIW-2)** |
| `pip install -e ".[dev,benchmark,...]"` | 4–8 min | 6 jobs | **Replaced by uv (CIW-7)** |
| `setup-db2` — pull the image | 4–6 min | 5 jobs | Unchanged — re-measure |
| `compose up --wait` (`start_period: 300s`, `interval: 5s`) | 5–10 min | 5 jobs | Interval tightened (CIW-3) |
| DDL readiness poll (backoff 2–15 s × up to 20) | 0–5 min | 5 jobs | Backoff added (CIW-3) |

**~18–30 minutes of setup per Db2 job (estimate)**, and roughly 25 minutes of it is avoidable.

---

## 2. Findings, highest ROI first

### A. `db2-ready` warms nothing and costs 5 minutes of pure serial latency

The job's stated purpose is *"confirm the image is accessible so that all downstream jobs
can trust the image cache is warm on the runner pool."* That premise is false on
GitHub-hosted runners: **every job gets a fresh VM with an empty Docker image store.**
Nothing pulled in `db2-ready` is visible to `benchmark`, `locust-*`, `benchmark-nightly`,
or `benchmark-scale` — each one re-pulls the full ~3 GB image inside `setup-db2` anyway.

Meanwhile all five Db2 jobs sit behind `needs: [db2-ready]`, so the pull is added to the
critical path *and* duplicated.

The job's only genuine output is the string `db2_image: icr.io/db2_community/db2:12.1.5.0`,
which is a constant.

**Fix:** delete the job; promote the image reference to a workflow-level `env:`.

```yaml
env:
  DB2_IMAGE: icr.io/db2_community/db2:12.1.5.0
```

**Saving: 4–6 min off the critical path, immediately, at zero risk.**

### B. Db2 boot is over-conservative and is paid 5× concurrently

In `setup-db2/action.yml`:

```yaml
healthcheck:
  interval: 30s
  timeout: 60s
  retries: 30
  start_period: 300s
```

Two independent problems:

1. `interval: 30s` means that once Db2 *is* ready, you wait up to another 30 s to notice.
2. `start_period: 300s` is not itself a delay — but combined with the 30 s interval it
   makes detection coarse. Docker marks a container healthy the moment **any** probe
   passes, including during `start_period`; failures during `start_period` don't count
   against `retries`. So **a short interval with a long start period is free** — you only
   gain earlier detection, you lose no tolerance.

3. The DDL probe loop sleeps a flat 15 s between attempts and re-runs a full
   connect → `CREATE TABLE` → `CREATE VECTOR INDEX` → `DROP` cycle each time.

**Fix:**

```yaml
healthcheck:
  test: ["CMD-SHELL", "su - db2inst1 -c 'db2 connect to TESTDB' >/dev/null 2>&1"]
  interval: 5s
  timeout: 30s
  retries: 120
  start_period: 300s   # unchanged — it costs nothing
```

and replace the flat `sleep 15` in the DDL probe with backoff starting at 2 s.

**Saving: 2–5 min per Db2 job.** Because these jobs run in parallel, this shows up as
2–5 min off the critical path — but it's a prerequisite for (D), where it multiplies.

### C. `torch` is installed in six jobs, three of which never use it

`sentence-transformers>=5.6.1` in the `benchmark` extra pulls **`torch` — a 527 MB
manylinux x86_64 wheel** (confirmed in `uv.lock`), ~2.5 GB unpacked. Every job requests
`extras: benchmark,langchain,openai-agents,mcp`, including:

- `locust-isolation` — runs Locust only
- `locust-scale` — runs Locust only
- `consolidated-report` — reads JSON and writes HTML

The `actions/cache` on `~/.cache/pip` avoids the *download* but not the *install*: pip
still unpacks 2.5 GB of torch into site-packages on every run.

Three separate fixes, all compatible:

**C1 — Split the extras.** Add narrower extras so load-only jobs skip the ML stack:

```toml
benchmark-core = ["pytest-benchmark>=5.0", "pytest-codspeed>=2.2", "pytest-memray>=1.6.0", "psutil>=6.0"]
benchmark-load = ["agent-memory-sdk[benchmark-core]", "locust>=2.26"]
benchmark-quality = ["agent-memory-sdk[benchmark-core]", "sentence-transformers>=5.6.1", "ollama>=0.6.2", "datasets>=2.14"]
benchmark = ["agent-memory-sdk[benchmark-load]", "agent-memory-sdk[benchmark-quality]", "pygal>=3.0", "pygaljs>=1.0.2"]
```

Then `locust-isolation` / `locust-scale` use `extras: benchmark-load`, and
`consolidated-report` uses `extras: benchmark-core`.

**C2 — CPU-only torch.** The default PyPI `torch` wheel bundles CUDA kernels that are
dead weight on a GPU-less runner. Pin the CPU index for the jobs that do need it:

```
--extra-index-url https://download.pytorch.org/whl/cpu
```

Cuts the wheel from ~527 MB to ~200 MB.

**C3 — Switch to `uv`.** The repo already commits a 1.6 MB `uv.lock`, but CI installs with
`pip`. `uv` resolves and installs this dependency set roughly an order of magnitude faster,
and its cache uses hardlinks — so the "unpack torch" cost largely disappears on a cache hit
rather than merely the download.

```yaml
- uses: astral-sh/setup-uv@v5
  with:
    enable-cache: true
    cache-dependency-glob: "uv.lock"
- run: uv sync --locked --extra dev --extra ${{ inputs.extras }}
```

Also note the current cache key is `hashFiles('pyproject.toml')` with a loose
`restore-keys` fallback — that misses transitive dependency drift and can silently restore
a stale cache. **Key on `uv.lock`.**

**Saving: 3–6 min per job, on all six.**

### D. `locust-scale` runs eight load steps strictly serially

BM-14a (5 m) + BM-14b (5 m) + BM-14c (3 m) + BM-15 pool=1/5/10/20 (2 m each) +
oversaturation (1 m) = **22 minutes of pure load time**, plus per-step ramp-up, on top of a
single Db2 boot.

The obvious move is `strategy: matrix` over pool size. It is not a pure win, and the
tradeoff is worth stating explicitly:

- **BM-14a/b/c are independent scenarios.** Splitting them into a matrix is safe — they're
  reported separately and never compared to each other. → **Split these.**
- **BM-15's four pool sweeps are a comparison.** pool=1 vs pool=20 is only meaningful if
  both ran on comparable hardware. GitHub-hosted runners vary meaningfully between VMs, so
  a matrix would inject inter-runner variance directly into the axis being measured.
  → **Keep BM-15's four sweeps on one runner.**

That gives ~13 min back (BM-14) while leaving the 8-min BM-15 block intact and comparable.
Each new matrix leg pays a Db2 boot, which is why (B) must land first — at today's 12-minute
boot the matrix is roughly break-even; at a 4-minute boot it's a clear win.

Public repositories get unmetered standard runners, so the extra concurrency is free in
billing terms.

### E. The 500k seed corpus is regenerated row-by-row every scale run

`benchmarks/seed_corpus.py` writes through the SDK with a checkpoint flush every 500 rows
(`_CHECKPOINT_BATCH = 500`). Per the EPIC-25 audit, vectors cannot be parameter-bound on
this Db2 version and are **inlined as `CAST('{vec}' AS VECTOR(...))` string literals** —
so every row is a distinct, unprepared, fully-logged statement. At 500k rows this is
almost certainly the largest single step in `benchmark-scale`, and it's also why the
`LOGFILSIZ 4096 LOGPRIMARY 30 LOGSECOND 64` expansion step exists at all.

Options, in increasing order of payoff and effort:

1. **Multi-row `VALUES` batches** — commit per batch instead of per checkpoint. Cheapest
   change; probably 3–5×.
2. **`db2 LOAD` instead of `INSERT`** — generate the corpus once, `db2 EXPORT` it, cache
   the file with `actions/cache`, and `LOAD` it in CI. `LOAD` bypasses transaction logging
   entirely, which also removes the need for the log-expansion step.
3. **Bake the seeded database into the image** — see (F).

### F. Revisiting CIB-2: a pre-baked GHCR image

CIB-2 evaluated `jobs.<job>.services:` and correctly identified the blocker: GHA starts
service containers before any step runs, so the host path bind-mounted to
`/var/custom/01_create_testdb.sh` cannot be populated in time. CIB-2 considered a custom
image as the workaround and **rejected it on image-maintenance and drift grounds.**

That rejection is worth reopening, because a custom image now solves four problems at once,
not one:

1. Removes the `/var/custom` bind-mount → unblocks the `services:` migration CIB-2 shelved.
2. Bakes TESTDB creation into a layer → the entire DDL readiness poll loop disappears.
3. Bakes the `LOGFILSIZ`/`LOGPRIMARY`/`LOGSECOND` config → drops another step.
4. GHCR pulls land in the same datacenter as the runner; icr.io does not. Pull time for a
   ~3 GB image typically halves.
5. Optionally bakes the seeded corpus from (E) → scale-tier setup becomes a single pull.

The drift concern is real but manageable: a scheduled weekly rebuild workflow pinned to
`icr.io/db2_community/db2:12.1.5.0`, with the tag bumped by Renovate. That's a maintained
artifact, but a small one — the Dockerfile is ~10 lines.

**Saving: 5–10 min per Db2 job.** This is the single largest remaining win and the only
one requiring ongoing maintenance.

### G. Smaller items

- **`--benchmark-histogram` in the `benchmark` job (lines 212, 220).** Requires pygal and
  renders an SVG per benchmark. Both the Tier-0 and Tier-1 steps write to the *same*
  `${BENCH_OUT}/histogram` prefix, so Tier-0's output is likely overwritten by Tier-1
  regardless. Drop it in CI; keep it for local runs.
- **`codspeed` runs under Valgrind**, which is 10–50× slower than native. It's capped at
  10 min and has no Db2 dependency, so it's off the critical path today — but don't add
  tests to it casually.
- **`suite:` selector (CIB-4) is the best lever that already exists.** Most days you want
  `tier1-benchmark`, not `all`. Worth making that explicit in the workflow description so
  people stop reaching for `all` by default.
- `actions/checkout` already defaults to `fetch-depth: 1`. No change needed.

---

## 3. Sequenced plan

**Phase 1 — hours, no risk, no new infrastructure**

1. Delete `db2-ready`; move `db2_image` to workflow `env:`. *(A)*
2. Healthcheck `interval: 30s → 5s`, `retries: 30 → 120`. *(B)*
3. DDL probe: exponential backoff from 2 s instead of flat 15 s. *(B)*
4. Drop `--benchmark-histogram` from both CI steps. *(G)*
5. Split extras; Locust jobs stop installing torch. *(C1)*

→ **~15–25 min off the critical path.**

**Phase 2 — 1–2 days**

6. Migrate `setup-bench-python` to `uv` + `uv sync --locked`; cache keyed on `uv.lock`. *(C3)*
7. CPU-only torch index for quality jobs. *(C2)*
8. Publish `ghcr.io/<org>/db2-bench:12.1.5.0` with `/var/custom` and log config baked in;
   add a weekly rebuild workflow. *(F)*

→ **~20–30 min more.**

**Phase 3 — larger, do after Phase 2 lands**

9. `db2 LOAD` + cached export for the seed corpus. *(E)*
10. Matrix out BM-14a/b/c; leave BM-15's sweeps co-located. *(D)*

→ **~15 min more on `locust-scale`; large but unquantified win on `benchmark-scale`.**

---

## 4. Expected outcome

`suite: all` critical path: **~2–2.5 h → ~50–70 min.**

The residual is dominated by `benchmark-nightly`'s 15-minute Locust ramp and the AGQ judge
steps — that's *intended* measurement time, not overhead, and shouldn't be cut for speed.

---

## 5. Caveats

- All timings above are estimates from reading the workflow, not from measured run data.
  Before committing to Phase 2 or 3, pull actual step durations from a recent `suite: all`
  run (Actions → run → job → step timings, or `gh api /repos/{owner}/{repo}/actions/runs/{id}/jobs`)
  and re-rank. The Phase 1 items are worth doing regardless — they're cheap and the
  reasoning doesn't depend on the estimates.
- (D) and (F) both trade a maintained artifact or extra runner concurrency for wall clock.
  If the team's constraint is maintenance burden rather than latency, Phase 1 + (C3) alone
  gets most of the benefit with nothing new to own.

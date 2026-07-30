# DECISIONS.md

Architecture and implementation decisions for `agent-memory-sdk`.

---

## 2025-07-31 — CI pipeline: lint, type-check, and unit tests (PH-1)

**Workflow file:** `.github/workflows/ci.yml`

**Triggers:** `push` to `main`; all `pull_request` events.

**Python version matrix:** `3.10`, `3.11`, `3.12`
Matches `requires-python = ">=3.10"` in `pyproject.toml` and the three
`Programming Language :: Python :: 3.1x` classifiers declared there.
`fail-fast: false` so a failure on one version does not cancel the others.

**Steps each matrix entry runs:**

| Step | Command |
|---|---|
| Install | `pip install -e ".[dev]"` |
| Lint | `ruff check .` |
| Type-check | `mypy src` |
| Unit tests | `pytest` |

**Install:** Editable install with the `[dev]` extra only
(`pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.4`, `mypy>=1.10`,
`python-dotenv>=1.0`). The `[langchain]`, `[openai-agents]`, and `[mcp]`
extras are intentionally excluded — adapter-specific dependency-version
drift is out of scope for this job.

**Lint:** `ruff check .` validates all source files against the rule set
declared in `[tool.ruff.lint]` (`E`, `F`, `I`, `UP`, `B`, `SIM`, ignoring
`E501`).

**Type-check:** `mypy src` runs with the `[tool.mypy]` config in
`pyproject.toml` (`strict = true`, `ignore_missing_imports = true` for
`ibm_db` which ships no stubs).

**Unit tests:** Plain `pytest` with no extra exclusion flags. The
`tests/integration/` suite self-skips when `DB2_DATABASE` is unset via the
`pytest_collection_modifyitems` hook in
`tests/integration/conftest.py` — no `-k` or `--ignore` flag is needed.

**Pip cache:** `actions/cache@v4` keyed on
`pip-<python-version>-<sha256 of pyproject.toml>` with a version-scoped
restore key. Invalidates automatically whenever `pyproject.toml` changes
(i.e. whenever a dependency version pin is bumped).

**Status badge:** Added to `README.md` — links to the Actions run list for
the `ci.yml` workflow.

**Follow-up items already on the board (not built here):**
- PH-2: integration job with a live Db2 service container
- PH-3: coverage reporting via `pytest-cov` + Codecov badge + threshold gate
- PH-4: `pip-audit` + `bandit` security scanning
- PH-5: packaging build verification (`python -m build` + `twine check`)

---

## 2025-07-31 — CI integration job: live Db2 container (PH-2)

**Workflow file:** `.github/workflows/ci.yml` — new `integration-test` job appended
to the existing file.

**Why a separate job (not a fourth matrix entry):** Db2 boot takes 3–5 minutes.
Coupling it to the lint/type-check/unit matrix would block fast feedback on every
PR.  A parallel job lets the two concerns run concurrently and both gate the merge.

**Container image:** `icr.io/db2_community/db2:12.1.5.0`

- Tag is pinned (not `:latest`) so CI is reproducible across runner refreshes.
- `CREATE VECTOR INDEX` became GA in Db2 12.1.5; 12.1.5.0 is therefore the
  correct minimum image for this project.
- The same tag is recorded in `project-management/INTEGRATION_TESTING.md`
  so the two never drift.

**Why `docker run --privileged` (not a GitHub Actions service container):**
GitHub Actions service containers do not expose a `--privileged` flag in the
workflow syntax.  The `icr.io/db2_community/db2` image requires `--privileged`
(or at minimum `--cap-add IPC_OWNER`) to start the Db2 instance.  Running the
container ourselves in a step gives full control over flags; the hostname remains
`localhost` on the same runner.

**Wait / health-check strategy:** A polling loop retries
`docker exec db2-dev bash -c "su - db2inst1 -c 'db2 connect to TESTDB'"` every
15 seconds for up to 10 minutes (40 attempts).  This is the exact connectivity
verification step from `INTEGRATION_TESTING.md` section 2, reused verbatim
rather than inventing a different signal.  Fixed sleeps are not used.  On timeout
the step prints `docker logs db2-dev` before failing so the failure is diagnosable.

**DB2_* env vars:** Set as job-level `env:` matching `.env.example` exactly:
`DB2_DATABASE=TESTDB`, `DB2_HOSTNAME=localhost`, `DB2_PORT=50000`,
`DB2_UID=db2inst1`, `DB2_PWD=passw0rd`.  No secrets needed — this is the
throw-away developer password documented in the Docker quick-start.

**Install:** `pip install -e ".[dev,langchain,openai-agents,mcp]"` — all extras
installed so adapter integration tests (`test_adapters_integration.py`) run fully
rather than auto-skipping the framework subtests.

**Test command:** `pytest -m integration -v` — runs only the marked integration
suite, consistent with the command in `INTEGRATION_TESTING.md` section 5.

**Python version:** Fixed at 3.11 (middle of the supported 3.10–3.12 range).
Running the integration suite on all three Python versions would triple the
already-slow Db2 boot cost for negligible additional signal — the unit matrix
already covers Python compatibility.

**INTEGRATION_TESTING.md alignment (updated in this commit):**
- Pinned image tag from `:latest` → `12.1.5.0` with a note explaining why.
- Added CI polling loop to section 1 so the wait strategy is documented in one
  place and the workflow file references it rather than reimplementing it
  independently.

---

## 2025-07-31 — Coverage reporting and threshold gate (PH-3)

**Coverage tool:** `pytest-cov>=5.0` — already declared in `pyproject.toml`'s
`dev` extras; this change wires it in for the first time.

**Coverage scope:** `src/agent_memory_sdk` only.  `tests/` and `scripts/` are
explicitly excluded via `[tool.coverage.run] omit` in `pyproject.toml`.  The
`--cov=agent_memory_sdk` flag names the importable package (not the `src/`
path); `pytest-cov` resolves it correctly from the installed editable package.

**Threshold:** 85 % (`--cov-fail-under=85`).
Rationale: the VER-1..VER-10 audit confirmed the unit suite is comprehensive.
A first run against the current suite measured **87 %**, so 85 % gives a
~2 percentage-point buffer against minor fluctuation while still being a
meaningful gate.  The threshold is in `[tool.pytest.ini_options] addopts` in
`pyproject.toml` (not only in the CI command) so it is enforced identically
on local developer runs.

**Report formats:** `--cov-report=xml` (produces `coverage.xml` for upload)
and `--cov-report=term-missing` (prints uncovered lines to the CI log for
immediate diagnosis without opening a dashboard).

**Coverage reporting service:** Codecov.
- Upload via `codecov/codecov-action@v4`, gated to the `python-version ==
  '3.11'` matrix leg to avoid triple-uploading identical data.
- `fail_ci_if_error: false` — a Codecov outage does not block the build;
  the local `--cov-fail-under` threshold is the enforcement mechanism.
- Requires a `CODECOV_TOKEN` repo secret for private repos (set at
  Settings → Secrets and variables → Actions).  On a public repo the
  token is optional; the upload succeeds but is marked unverified without it.
- Badge URL pattern: `https://codecov.io/gh/<org>/<repo>/graph/badge.svg`
  Added to `README.md` on line 4, directly below the CI badge.

**`[tool.coverage.report] exclude_lines`:** Three patterns excluded:
- `pragma: no cover` — explicit opt-out, already the default.
- `if TYPE_CHECKING:` — import-time guard blocks that never execute at
  runtime; excluding them avoids penalising well-typed code.
- `raise NotImplementedError` — abstract method stubs; covered by the
  concrete subclass tests, not the stub itself.

---

## 2025-07-31 — Dependency and static security scanning (PH-4)

**Workflow file:** `.github/workflows/ci.yml` — new `security` job appended.

**Triggers:** same as PH-1: `push` to `main`; all `pull_request` events.
The job runs in parallel with the unit matrix and the integration job so
a security finding never delays fast lint/type-check/unit feedback.

### pip-audit

**Command:** `pip-audit --strict`

Audits the fully resolved dependency set installed by `pip install -e ".[dev]"`.
`--strict` causes the command to exit non-zero on any known vulnerability
regardless of severity, so the gate is unambiguous: no known CVEs with a
published advisory in the PyPI advisory database are permitted in the
resolved install.

**Accepted/ignored advisories:** none at time of writing.  When a future
advisory must be accepted (e.g. an unfixed transitive-dep vuln with no upgrade
path and a documented risk acceptance), add `--ignore-vuln <PYSEC-ID>` to the
`pip-audit` step and record it in this file with:
- the PYSEC/GHSA advisory ID,
- which package and version is affected,
- why no upgrade is available,
- the risk assessment (exploitability, actual attack surface in this project),
- the expiry date for the acceptance (i.e. when to re-evaluate).

**Why `.[dev]` only (not all extras):** `pip-audit` runs against the resolved
install.  The `[langchain]`, `[openai-agents]`, and `[mcp]` extras are
intentionally excluded here because they introduce rapidly-changing
third-party dependency graphs whose version drift is out of scope for this job
(the same rationale as PH-1 lint/type-check).  Those adapter deps are only
installed and exercised by the `integration-test` job (PH-2).

### bandit

**Command:**
```
bandit -r \
  src/agent_memory_sdk/db/ \
  src/agent_memory_sdk/repositories/ \
  src/agent_memory_sdk/store.py
```

**Why this scope:** VER-5 hand-audited all SQL construction in these three
module groups for injection safety.  Enforcing bandit over exactly this scope
turns the manual audit into a mechanical gate: any *new* SQL-construction
pattern added in the future will be flagged and must either pass cleanly or
receive a scoped suppression with a recorded rationale here.

**Findings before suppression:** 19 issues detected on first run.
All 19 were confirmed safe by the VER-5 audit.  No new `# nosec` comments
were added that represent genuine risk acceptances — every suppression is a
false-positive reclassification of a pattern whose safety was already
established and documented.

### # nosec suppressions added (PH-4) — complete register

All suppressions use scoped IDs (`# nosec B608` or `# nosec B110`) placed
on the **closing `"""` line** of each multiline f-string (or on the
`except` line for B110), because bandit v1.9.4 associates the finding with
the AST node's closing token for multiline strings.

---

#### `src/agent_memory_sdk/db/migrate.py`

**B608 — `validate()` SYSCAT.COLUMNS query (line 376)**
```python
f"   AND UPPER(TABNAME) IN ({placeholders})",  # nosec B608
```
`placeholders` is `", ".join("?" * len(present_tables))` — a literal string
of `?` characters.  The actual table names from `_REQUIRED_TABLES` (a
hardcoded module-level constant, never user-supplied) are passed as bound
parameters to `cur.execute()`.  No user data is interpolated into the SQL.

**B608 — `validate()` SYSCAT.INDEXES query (line 400)**
```python
f"   AND UPPER(TABNAME) IN ({placeholders})",  # nosec B608
```
Same as above; same `placeholders` construction; same bound-param pattern.

**B110 — `_bootstrap()` catalog probe (line ~462)**
```python
except Exception:  # nosec B110
    pass  # table is absent; fall through to create it
```
This `try/except/pass` is an intentional existence probe: `SELECT COUNT(*)
FROM schema_migrations` raises if the table doesn't exist (DB-API driver
error, not a catchable SQL error code in ibm_db_dbi).  Swallowing the
exception is the correct design — any non-empty exception means "table
absent, create it".  The subsequent `CREATE TABLE IF NOT EXISTS` makes the
handler idempotent.  The alternative (querying SYSCAT.TABLES first) would
require a second round-trip; the probe pattern is simpler and documented in
the `_bootstrap()` docstring.

---

#### `src/agent_memory_sdk/repositories/base.py`

All 12 B608 findings in this file follow one of two patterns:

**Pattern A — structural query builder with hardcoded table/column names**
Interpolated variables: `self._TABLE` (hardcoded class attribute, e.g.
`"working_memory"`), `self._SELECT_COLS` (hardcoded column list string),
`scope_sql` (output of `_scope_predicates()` which only produces literal
`"agent_id = ?"` / `"tenant_id = ?"` etc. fragments — all values bound),
`supersession_sql` / `extra` / `conf_sql` (hardcoded constant string
fragments — never user-supplied), `meta_sql` (output of
`_build_metadata_filter()` which validates field names against
`^[A-Za-z_][A-Za-z0-9_.]*$` and uses bound params for values),
`placeholders` (`",".join("?" for _ in ids)` — all literal `?` chars).
None of these originate from untrusted user input.

**Pattern B — vector literal injection guard (`_vec_to_str`)**
The only variable inlined as a literal SQL string (not as a bound param)
is the vector string `vec_str`, produced by `_vec_to_str(embedding)`:
```python
def _vec_to_str(embedding: list[float]) -> str:
    return "[" + ",".join(str(float(f)) for f in embedding) + "]"
```
Every element is coerced through `float()` before string-formatting.  Any
non-numeric value raises `ValueError`/`TypeError` before reaching SQL.  This
is the actual injection guard: for `create()`/`update()` the source is a
Pydantic-validated `list[float]` (coercion is a no-op); for `search()` the
source is the externally-reachable `query_embedding` parameter, where
coercion is the real security boundary.  This pattern was established in
VER-5 and is documented in the `repositories/base.py` module docstring.

Specifically suppressed locations:
- `create()` dedup SELECT (line ~732): Pattern A.
- `create()` INSERT (line ~786): Patterns A + B.
- `get_by_id()` SELECT (line ~841): Pattern A.
- `list_all()` FETCH FIRST SELECT (line ~931): Pattern A.
- `list_all()` ROW_NUMBER pagination SELECT (line ~948): Pattern A.
- `forget()` UPDATE (line ~1006): Pattern A.
- `update()` UPDATE (line ~1087): Patterns A + B.
- `purge_expired()` DELETE (line ~1156): Pattern A.
- `_claim_consolidated()` UPDATE (line ~1230): Pattern A.
- `search()` ID-ranking SELECT (line ~1434): Patterns A + B.
- `search()` full-row fetch SELECT (line ~1452): Pattern A.
- `_search_via_chunks()` parent-row fetch SELECT (line ~1559): Pattern A.

---

#### `src/agent_memory_sdk/repositories/chunks.py`

**B608 — `insert_chunk()` INSERT (line ~134):** Pattern B (vec_str) + table
name `"memory_chunks"` is a hardcoded string literal in this file (not a
variable); all other values bound.

**B608 — `search_chunks()` ranking SELECT (line ~276):** Pattern B (vec_str)
+ `metric.value` is a `DistanceMetric` enum member (hardcoded strings:
`"COSINE"`, `"EUCLIDEAN"`, `"INNER_PRODUCT"` — never user-supplied).

**B608 — `search_chunks()` distance SELECT (line ~304):** Same as above.

---

#### `src/agent_memory_sdk/repositories/facts.py`

**B608 — `SemanticFactRepository.supersede()` UPDATE (line ~157):** Pattern A.
`self._TABLE = "semantic_facts"` (hardcoded class constant); `scope_sql`
from `_scope_predicates()` (bound params only).

---

### Bandit configuration note

`bandit` is run with no `--skip` or `-t` flags in CI.  All suppressions are
per-site `# nosec B608` / `# nosec B110` comments placed in the source.
This keeps the full test suite active for the entire scope and means any
new finding in a future code change will surface immediately rather than
being hidden by a global skip list.

---

## 2025-08-02 — Agent-memory benchmarking harness (PH-6)

**Files added / changed:**
- `benchmarks/` package (excluded from wheel — same treatment as `project-management/`)
  - `benchmarks/__init__.py` — package docstring explaining CI exclusion
  - `benchmarks/README.md` — quick-start, free-tier provider guide, suite descriptions
  - `benchmarks/common/scope_gen.py` — run-unique UUID-prefixed scope/marker generation
  - `benchmarks/common/timing.py` — `timed()` context manager + `LatencySamples` percentiles
  - `benchmarks/common/cost_tracking.py` — `CostTrackingHook` wrapping any Consolidator/Reconciler/Summarizer hook with call-count + estimated-token accounting
  - `benchmarks/common/embedding_providers.py` — three-tier provider: `HashingEmbeddingProvider` (no deps, default), `SentenceTransformersEmbeddingProvider` (local, free), `GeminiEmbeddingProvider` (hosted, free-tier)
  - `benchmarks/common/llm_judge.py` — `KeywordMatchJudge` (fallback heuristic) + `GeminiJudge` (real LLM judge, same CORRECT/INCORRECT shape as LongMemEval's GPT-4o judge)
  - `benchmarks/common/report.py` — result dataclasses + `render_markdown()` producing the BENCHMARKS.md report
  - `benchmarks/retrieval_quality/dataset.py` — synthetic LongMemEval-shaped dataset (5 categories × n_per_category questions, seeded for reproducibility)
  - `benchmarks/retrieval_quality/run.py` — writes sessions via `remember()`, searches via `search()`, scores via judge
  - `benchmarks/latency_cost/run.py` — `LatencySamples` per-call timing + `MockConsolidator` for the `--consolidator mock` cost-tracking demo
  - `benchmarks/isolation_load/run.py` — concurrent `ThreadPoolExecutor` workers across synthetic tenant/agent scopes, zero-leakage assertion via scope-field check + marker-content check
- `scripts/run_benchmarks.py` — CLI entry point; exits 0 on success, 1 on config/Db2 error, 2 on isolation leakage
- `project-management/BENCHMARKS.md` — placeholder (populated by `make benchmark`; checked in with harness code)
- `Makefile` — `benchmark` target (`python scripts/run_benchmarks.py $(ARGS)`)
- `pyproject.toml` — `[project.optional-dependencies] benchmark` extras group (`sentence-transformers`, `google-generativeai`) for real-number runs

**Wheel exclusion:** The hatchling wheel target lists only `src/agent_memory_sdk` — `benchmarks/` is excluded by omission, identical treatment to `project-management/`. Confirmed in `[tool.hatch.build.targets.wheel] packages = ["src/agent_memory_sdk"]`.

**Suite 1 — Retrieval quality (LongMemEval-shaped):**

The dataset follows LongMemEval (Wu et al., arXiv 2410.10813, ICLR 2025) five ability categories: `extraction`, `multi_session`, `temporal_reasoning`, `knowledge_update`, `abstention`. Each question gets its own `MemoryScope` (no cross-question interference). Questions are template-generated, not the real LongMemEval 500-question dataset (which is not redistributed). The harness is designed to produce a number that is *honestly comparable in kind* to vendor-reported LongMemEval figures when run with a real embedding model and an LLM judge — the report stamps every run with the exact judge/embedding/dataset-size configuration and explicitly labels any deviation from the published methodology. Specifically:

- `--judge keyword` (default, dependency-free): a keyword/token-overlap heuristic. The report calls this out in bold as **NOT an LLM judge** and instructs not to cite it next to Oracle's 93.8%, Zep's 94.8% DMR, or any other vendor-reported figure.
- `--judge gemini` (real LLM judge, free-tier): Google Gemini `gemini-1.5-flash`, same CORRECT/INCORRECT verdict shape as LongMemEval's GPT-4o judge. Results with this judge + a real embedding model are *comparable in kind* to vendor figures, subject to the caveats in BENCHMARKS.md (synthetic dataset, configurable sample size, no graph retrieval).

Three documented deviations: (1) synthetic dataset not the real LongMemEval 500 questions; (2) configurable/small default sample size; (3) no graph retrieval (Db2 VECTOR cosine search only, not bi-temporal knowledge graph). All three are stamped in every run's report.

**Suite 2 — Latency/cost:**

Per-call wall-clock latency percentiles (mean, p50, p95, p99, max) for `remember()` and `search()` over `--latency-ops` calls. LLM cost is reported **only** when a `Consolidator`/`Reconciler`/`Summarizer` hook is configured. With the default `--consolidator none` (the SDK's default path), estimated LLM cost is $0.00 / 0 hook calls — this is the comparison point against extraction-pipeline competitors (Mem0, Bedrock, LangMem) that always run an LLM on every write. The `--consolidator mock` mode wires in a `MockConsolidator` wrapped in `CostTrackingHook` using a ~4 chars/token estimate (documented approximation, not a live API token count).

**Suite 3 — Isolation under load:**

`tenants × agents_per_tenant` synthetic scopes each write `ops_per_worker` rows then read back via `search()` and `list_all()`, all in a `ThreadPoolExecutor` with `--workers` concurrent threads. Each returned row is checked: (1) `agent_id`/`tenant_id` fields must match the querying scope; (2) content must not contain another scope's `[[MARKER:tenant:agent]]` string. Zero leakage is the assertion. This extends VER-5's static SQL audit (mocked cursors, single-threaded) to real concurrent load against a live `ConnectionPool` — measuring the "governed substrate" SWOT claim from `ai-agent-platform-competitive-analysis.md` under actual concurrency rather than only asserting it.

**Why not CI (PH-1/PH-2):** Requires live Db2 and optionally a paid/free-tier LLM API. Wiring into CI would either always fail (no credentials) or burn real cost on every push. The harness is run on demand, results checked into `project-management/BENCHMARKS.md`.

**Results at time of commit:** No live Db2/LLM run recorded yet — `project-management/BENCHMARKS.md` is a methodology-documenting placeholder. Run `make benchmark` (or `make benchmark ARGS="--embedding-provider sentence-transformers --judge gemini --dataset-size 10"` for a real number) against a Db2 instance to populate it.

**Ruff / tests:** All benchmark Python files pass `ruff check`. The benchmarks package is not imported by the `src/` package and is not covered by the unit suite (no Db2 mock available at unit-test time). The 542 existing unit tests continue to pass at 87% coverage (no regression).

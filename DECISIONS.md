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

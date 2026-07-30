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

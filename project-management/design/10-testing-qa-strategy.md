# Testing & QA Strategy

**EPIC-9 · SDD-10**

This document defines the testing and quality-assurance strategy for the
`agent-memory-sdk` project. It covers the test pyramid, coverage policy,
scope-isolation controls, static-analysis gates, and test-file naming
conventions.

---

## 1. Test Pyramid

```
              ┌───────────────────────────┐
              │        benchmarks/        │  performance characteristics
              │  (not part of the pyramid) │  (complementary, not correctness)
              └───────────────────────────┘
            ┌─────────────────────────────────┐
            │      Integration tests          │  require live Db2
            │  tests/integration/  [-m integration]  │
            └─────────────────────────────────┘
          ┌───────────────────────────────────────┐
          │             Unit tests                │  no live Db2 required
          │          tests/  (default run)        │
          └───────────────────────────────────────┘
```

### 1.1 Unit Tests

- **Location:** `tests/` (all files outside `tests/integration/`)
- **Db2 dependency:** none — every `ibm_db` call is mocked via a fake
  connection pool (see the `_FakePool` / `_FakeCursor` pattern in
  [`tests/test_scoping.py`](../tests/test_scoping.py))
- **Run command:**

  ```bash
  pytest
  # or, explicitly excluding integration:
  pytest -m "not integration" -v
  ```

- **What they cover:** repository SQL structure, scope predicate
  inclusion, model validation, store facade delegation, exception
  paths, adapter logic, and all other behaviour that can be exercised
  without a network connection.

### 1.2 Integration Tests

- **Location:** `tests/integration/`
- **Db2 dependency:** required — a live Db2 LUW 12.1.5+ instance must be
  reachable.
- **Marker:** every test carries `pytestmark = pytest.mark.integration`
  (defined in `pyproject.toml` as
  `"integration: requires a live Db2 instance (skip with -m 'not integration')"`).
- **Auto-skip behaviour:** the `pytest_collection_modifyitems` hook in
  `tests/integration/conftest.py` marks every integration test as
  `skip` at collection time when the `DB2_DATABASE` environment variable
  is not set. No integration test will fail in a CI environment that
  lacks Db2; they are simply not collected.
- **Run command:**

  ```bash
  # Integration tests only
  pytest -m integration -v

  # With coverage suppressed (CI integration job uses --no-cov)
  pytest -m integration -v --no-cov
  ```

- **Setup:** see [`project-management/INTEGRATION_TESTING.md`](../INTEGRATION_TESTING.md)
  for the full Docker quick-start, environment variables, and
  troubleshooting guide.

### 1.3 Benchmarks

`benchmarks/` is a **distinct category** that sits outside the
correctness pyramid entirely.

- Benchmarks measure **performance characteristics** (latency, throughput,
  memory, instruction count) — they do not assert correctness.
- A benchmark passing or failing has no bearing on whether a feature is
  correct. Correctness is the domain of unit and integration tests.
- Benchmarks are complementary to the pyramid, not a tier within it.
- They use their own pytest markers
  (`benchmark_micro`, `benchmark_pr`, `benchmark_nightly`,
  `benchmark_scale`) and are run via `make benchmark` or dedicated CI
  workflow jobs — not as part of the default `pytest` run.

---

## 2. Coverage Policy

### 2.1 Target

| Setting | Value |
|---------|-------|
| Minimum threshold (`--cov-fail-under`) | **85 %** |
| Measurement scope (`--cov`) | `agent_memory_sdk` |
| Source root (`[tool.coverage.run] source`) | `src/agent_memory_sdk` |

The 85 % threshold is **CI-enforced, not advisory**. A PR whose changes
cause the branch coverage to drop below 85 % fails the `test` job and
cannot be merged.

### 2.2 What Is Measured

Coverage is collected over `src/agent_memory_sdk` only — adapters that
live outside this path, test files, and scripts are excluded from
measurement via `[tool.coverage.run]`:

```toml
[tool.coverage.run]
source = ["src/agent_memory_sdk"]
omit = ["tests/*", "scripts/*"]
```

### 2.3 Excluded Lines

The following patterns are excluded from the coverage report via
`[tool.coverage.report]` and will never count against the threshold:

```toml
[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

These exclusions cover three cases:

| Pattern | Rationale |
|---------|-----------|
| `pragma: no cover` | Lines explicitly opted out (e.g. the `ibm_db` import guard that only triggers when the C extension is absent) |
| `if TYPE_CHECKING:` | Import blocks that are never executed at runtime — only used by the type checker |
| `raise NotImplementedError` | Abstract method stubs; a concrete implementation must exist to be useful, and the stub itself cannot be reached in a correctly-wired test run |

### 2.4 Coverage Reports

`pytest` produces two coverage reports on every run (configured in
`addopts`):

- `--cov-report=xml` — machine-readable report consumed by CI coverage
  upload steps.
- `--cov-report=term-missing` — human-readable terminal output showing
  which lines are uncovered.

---

## 3. Scope-Isolation Test Pattern as a Standing QA Control

### 3.1 Origin

The cross-scope-leakage test suite was introduced in **VER-5, Step 5**
and lives in [`tests/test_scoping.py`](../tests/test_scoping.py).

### 3.2 What It Tests

The suite verifies that the SDK's four-dimensional scope model
(`tenant_id`, `agent_id`, `user_id`, `thread_id`) enforces hard
isolation boundaries at the data layer:

- **Tenant isolation** — a write to `tenant-A` cannot be read by a
  caller presenting `tenant-B` credentials.
- **Agent isolation** — `agent-owner`'s rows are invisible to
  `agent-other` even when the `tenant_id` matches.
- **User isolation** — `user_id` is included as a WHERE predicate;
  a different `user_id` yields no rows.
- **Thread isolation** — `thread_id` is included as a WHERE predicate;
  a different `thread_id` yields no rows.

The tests use the `_FakePool` / `_FakeCursor` pattern to verify SQL
structure and bound parameters **without a live database**. The
fake cursor simulates the zero-row response that Db2 would return when
scope predicates filter out another tenant's rows.

Specifically, the suite asserts the isolation property across:

- All five repository types
  (`WorkingMemory`, `EpisodicMemory`, `SemanticFact`, `EntityProfile`,
  `ProceduralMemory`)
- All read and mutate operations:
  `get_by_id`, `list_all`, `search`, `forget` / `soft_delete`,
  `update`, `purge_expired`
- The `MemoryStore` facade (scope must propagate all the way down to the
  repository SQL)
- Empty `agent_id` rejection (every operation must raise `ValueError`
  when `agent_id` is `""`)

### 3.3 Why It Is a Standing QA Control

Scope enforcement is a **security property**, not just a functional one.
Any new query path added to the SDK creates a potential avenue for
cross-scope data leakage if the scope predicates are omitted or
incorrectly composed.

**Rule:** every story that introduces a new query path (a new `SELECT`,
`UPDATE`, or `DELETE` that is reachable from a public API) must add
test coverage to `tests/test_scoping.py` (or an equivalent file, such as
`tests/test_thrd10_scope_matching.py`) that verifies the new path
includes the correct scope predicates in its SQL and returns no rows
when the requesting scope does not match the stored scope.

This is not enforced by tooling — it is a design review checklist item
for every story that touches repository or store methods.

---

## 4. Static-Analysis Gates

All four gates below are **merge-blocking**: a PR cannot be merged if any
gate fails in CI.

### 4.1 `ruff` — Linting and Import Sorting

Configured in `[tool.ruff]` and `[tool.ruff.lint]` in `pyproject.toml`:

```toml
[tool.ruff]
src = ["src"]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
ignore = ["E501"]  # line-length enforced by formatter, not lint
```

Rule sets in scope: pycodestyle errors (`E`), Pyflakes (`F`), isort
import sorting (`I`), pyupgrade modernisation (`UP`), flake8-bugbear
(`B`), flake8-simplify (`SIM`).

Run locally:

```bash
ruff check src/ tests/
```

### 4.2 `mypy --strict` — Full Type Checking

Configured in `[tool.mypy]`:

```toml
[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
ignore_missing_imports = true   # ibm_db has no stubs
```

Any unresolved type, missing annotation, or `Any`-return that is not
explicitly suppressed is a CI failure. `ignore_missing_imports = true`
is a targeted carve-out for `ibm_db`, which ships no type stubs.

Run locally:

```bash
mypy src/agent_memory_sdk
```

### 4.3 `bandit` — Security Static Analysis

Scans the source tree for common Python security anti-patterns
(hard-coded credentials, unsafe deserialization, shell injection, etc.).

Run locally:

```bash
bandit -r src/agent_memory_sdk
```

### 4.4 `pip-audit` — Dependency CVE Check

Audits the installed dependency set against known vulnerability
databases. A dependency with an unresolved CVE fails the gate.

Run locally:

```bash
pip-audit
```

---

## 5. Test-File Traceability Naming Convention

Test files follow the pattern:

```
tests/test_<story-id>_<description>.py
```

Examples from the current test suite:

| File | Story |
|------|-------|
| `tests/test_scoping.py` | VER-5 (scope isolation) |
| `tests/test_orc2.py` | ORC-2 (chunking) |
| `tests/test_orc3.py` | ORC-3 |
| `tests/test_thrd1_messages.py` | THRD-1 (thread messages) |
| `tests/test_thrd9_async_facades.py` | THRD-9 (async facades) |
| `tests/test_thrd10_scope_matching.py` | THRD-10 (scope matching modes) |
| `tests/test_pipe1_hybrid.py` | PIPE-1 (hybrid search) |
| `tests/test_pipe5_erasure.py` | PIPE-5 (erasure) |

**Purpose:** the story-id prefix makes it immediately clear which story
introduced a given test file. When reviewing a story's acceptance
criteria or bisecting a regression, you can navigate directly to the
relevant test file without grepping the entire test directory.

**Enforcement:** this is a project convention, not a tooling constraint.
No CI check rejects a file with a non-conforming name. New stories are
expected to follow the convention when they create test files.

**Scope:** the convention applies to files in `tests/` (unit tests).
Files in `tests/integration/` follow their own naming pattern defined
in the integration test guide.

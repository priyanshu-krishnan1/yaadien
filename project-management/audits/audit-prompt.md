# Audit remediation prompt for Bob

This is a one-off fix pass, separate from the Step N build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. These are cross-cutting
repo hygiene issues found by an external audit of Step 1/Step 2 work, not
tied to any single step's spec. Do them as their own commit(s), before
resuming Step 2.

Paste the block below into Bob as-is.

---

```
An audit of this repo found several issues to fix. Read DECISIONS.md in
full first, like any other change here. Fix each item below, then append
one dated DECISIONS.md entry summarizing what you fixed (use the entry
template at the bottom of DECISIONS.md), and commit — keep this as its own
commit (e.g. "fix: repo hygiene from audit"), separate from Step 2 work in
progress.

1. CRITICAL — .gitignore is backwards and risks leaking credentials.
   Current .gitignore ignores ARCHITECTURE.md, BOARD.html, Chats.md,
   DECISIONS.md, and PROMPTS.md (files that must be tracked), while
   ignoring nothing that should be ignored — no .env, no __pycache__/,
   no *.pyc, no .venv/, no .pytest_cache/, no .ruff_cache/, no
   *.egg-info/. Since every step's working agreement ends in
   `git add -A && git commit`, a real .env with live Db2 credentials
   (DB2_UID/DB2_PWD) would get committed the moment one is created.
   Replace .gitignore with:

     # Environment / secrets
     .env

     # Python
     __pycache__/
     *.py[cod]
     *.egg-info/
     .eggs/
     dist/
     build/

     # Virtualenvs
     .venv/
     venv/

     # Tooling caches
     .pytest_cache/
     .ruff_cache/
     .mypy_cache/

     # Editor/tooling local config (not part of the shipped project)
     .claude

   Do NOT ignore ARCHITECTURE.md, BOARD.html, DECISIONS.md, PROMPTS.md, or
   Chats.md — they must stay tracked.

2. Untrack the 8 .pyc/__pycache__ files that got committed before
   .gitignore existed (a symptom of issue 1 — git add -A had nothing
   stopping it):
     src/agent_memory_sdk/__pycache__/__init__.cpython-314.pyc
     src/agent_memory_sdk/db/__pycache__/__init__.cpython-314.pyc
     src/agent_memory_sdk/db/__pycache__/connection.cpython-314.pyc
     src/agent_memory_sdk/db/__pycache__/migrate.cpython-314.pyc
     tests/__pycache__/__init__.cpython-314.pyc
     tests/__pycache__/conftest.cpython-314-pytest-9.1.1.pyc
     tests/__pycache__/test_connection.cpython-314-pytest-9.1.1.pyc
     tests/__pycache__/test_migrations.cpython-314-pytest-9.1.1.pyc
   Run `git rm -r --cached` on these paths (leave the actual files on
   disk — just stop tracking them; the new .gitignore will keep them out
   going forward). Do not rewrite git history — just remove them in this
   new commit, same as deleting any other now-unwanted tracked file.

3. Chats.md exists on disk but was never committed (it matched the old
   .gitignore, so `git add -A` always skipped it silently). Read it,
   confirm it contains nothing sensitive, then `git add Chats.md` and
   commit it normally now that .gitignore no longer excludes it. If you
   find anything in it that looks sensitive, stop and flag it instead of
   committing.

4. Tests don't run out of the box. `pytest` fails everywhere with
   `ModuleNotFoundError: No module named 'agent_memory_sdk'` until
   `pip install -e .` (or `pip install -e ".[dev]"`) is run — that step
   exists nowhere in the repo. Add a short "## Development setup" section
   to README.md (above the "Full documentation added in Step 8" line) —
   just enough to unblock running tests/lint now, not the full Step 8
   docs: create a venv, `pip install -e ".[dev]"`, then `pytest`,
   `ruff check .`, `mypy src`.

5. mypy strict mode fails: `src/agent_memory_sdk/db/migrate.py:84` —
   `Migrator.__init__`'s `pool` parameter has no type annotation
   (pyproject.toml sets `mypy strict = true`). Fix without adding a
   hard runtime dependency on ibm_db in this module (migrate.py's own
   docstring says it should stay usable without ibm_db installed): guard
   the import with `TYPE_CHECKING` —

     from typing import TYPE_CHECKING
     if TYPE_CHECKING:
         from agent_memory_sdk.db.connection import ConnectionPool
     ...
     def __init__(self, pool: ConnectionPool, migrations_dir: Path | None = None) -> None:

   (migrate.py already has `from __future__ import annotations`, so the
   annotation is never evaluated at runtime — no circular-import risk.)
   Confirm `mypy src` is clean after this.

6. Unverified Db2 syntax: migration 0002 uses
   `VECTOR_FILL(1536, FLOAT32, 0.0)` as the zero-vector default, per the
   Step 2 DECISIONS.md entry. Re-confirm this exact function name and
   signature using the Product Knowledge MCP tool (fall back to Web
   search if needed) against current IBM Db2 12.1 docs — this is
   new/EAP-era syntax and if the function name is wrong the migration
   will fail outright the first time it runs against real Db2, which
   hasn't happened yet. If confirmed, add a one-line note to the
   existing Step 2 DECISIONS.md entry citing the doc you checked. If it's
   wrong, fix migration 0002's SQL directly (it has never been applied to
   a real database, so editing it in place is fine — no need for a new
   migration number) and note the correction.

7. Process gap: BOARD.html was substantially redesigned (new theme, an
   added "summary" field on each story, interactive Start/Done/Back/Reset
   buttons, toast notifications) with no corresponding DECISIONS.md entry
   — inconsistent with this project's own rule that every deviation gets
   logged. Add a short retroactive entry noting the redesign, the new
   "summary" field, and that the buttons are in-memory-only (preview, not
   persistence — real status changes require editing the embedded JSON
   and committing, per the existing working agreement).

After all 7 are done: run `pytest`, `ruff check .`, and `mypy src` one
more time and confirm all three are clean before committing.
```

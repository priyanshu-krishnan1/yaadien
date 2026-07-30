# Audit remediation prompt for Bob (round 2 — post Step 3)

This is a one-off fix pass, separate from the Step N build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. It covers a regression in
the currently staged-but-uncommitted hygiene-audit work, plus mypy/ruff
issues surfaced by Step 3. Fix everything below, then commit — the staged
hygiene-audit changes and these fixes can go in one commit together (e.g.
"fix: restore NOT NULL on embedding, mypy/ruff cleanup"), since the staged
work is otherwise correct and shouldn't be split apart.

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. There's currently a hygiene-audit fix
pass staged but not committed (.gitignore, Chats.md, DECISIONS.md,
README.md, migrate.py, migration 0002, and 18 untracked .pyc files) —
that's mostly good and should stay, but it contains one regression that
needs correcting before it's committed. Fix everything below in this same
pass, append one dated DECISIONS.md entry summarizing the fixes (use the
entry template at the bottom of DECISIONS.md), then commit everything
together — the already-staged changes plus these fixes.

1. CRITICAL — restore NOT NULL on the embedding column.
   The VECTOR_FILL fix (already staged) correctly removed the invalid
   `DEFAULT VECTOR_FILL(1536,FLOAT32,0.0)` clause, but it also removed
   `NOT NULL` from the embedding column on all five tables in migration
   0002. That's an overcorrection: `repositories/base.py`'s `create()`
   method ALWAYS supplies an explicit vector on every INSERT via
   `TO_VECTOR(?, FLOAT32)` (a real embedding, or `_zero_vec_str()` as a
   sentinel when none is provided) — it never relied on a DB-side
   default. So NOT NULL was never actually at risk of being violated by
   application code, and per your own comment in the migration, NOT NULL
   is required for Db2's ANN vector index to activate — which is the
   entire reason Step 0 chose a normalized per-table schema in the first
   place. Change all five `embedding` column definitions back to:
     embedding VECTOR(1536, FLOAT32) NOT NULL
   (no DEFAULT clause — Db2 doesn't allow one on VECTOR columns; that
   part of the original fix was correct and should stay). Since the
   migration has never been applied to a real Db2 instance, editing it in
   place is fine.

2. Fix the four places that still describe the OLD (wrong) column
   definition, now that #1 restores NOT NULL but drops the DB-side
   DEFAULT — they should all say "NOT NULL, no DB-side default; the
   application layer always supplies an explicit vector (real embedding
   or zero-vector sentinel) on every INSERT":
   - `models.py` — docstring at the top and the `embedding` field comment
     in `_MemoryBase`
   - `DECISIONS.md` — the Step 3 entry's "embedding field on models"
     decision
   - `ARCHITECTURE.md` — section 3, the `embedding` line in the column
     type legend (currently still shows the old DEFAULT VECTOR_FILL
     form)

3. mypy strict fails with 4 errors in `repositories/base.py` — fix all of
   them:
   a. `BaseRepository.list()` (the CRUD method) shadows the builtin
      `list` type within the class body, breaking the `list[float]` /
      `list[M]` annotations used elsewhere in the same class (mypy:
      "Function ... BaseRepository.list is not valid as a type"). Rename
      the method to `list_all` (update all call sites: other repository
      files if any call it directly, tests, and any docstring examples
      in store.py). This doesn't break runtime today only because
      annotations are lazy strings — it will bite the moment anything
      needs to evaluate them, so rename rather than work around it.
   b. Line ~98: an unused `# type: ignore[misc]` comment on the `_MODEL`
      class attribute — remove it.
   c. `soft_delete()` returns `affected > 0` where `affected =
      cur.rowcount`, and `cur.rowcount` is typed `Any`, so mypy flags
      "Returning Any from function declared to return bool". Wrap it:
      `return bool(affected > 0)` (or assign `affected: int =
      cur.rowcount` first, whichever reads cleaner to you).
   Confirm `mypy src` is clean after these three fixes.

4. ruff reports 6 errors, all auto-fixable — just run `ruff check --fix .`
   and confirm `ruff check .` is clean after. (Covers: unsorted import
   blocks in models.py/types.py/test_repositories.py, two unused imports
   in test_repositories.py, one ternary-simplification in base.py.)

5. Structural cleanup: `_parse_vector` and `_parse_dt` are defined in
   `repositories/working.py` but imported cross-module by at least
   `facts.py` (and likely episodic.py/profiles.py/procedural.py/tests —
   check all of them). This makes working.py an accidental shared-utils
   module for all five repos, which is fragile — refactoring or deleting
   working.py would silently break the others. Move both functions to
   `repositories/base.py` (next to the existing `_vec_to_str` helper,
   which is the same kind of shared serialization utility) and update
   every import site accordingly.

After all 5 are done: run `pytest`, `ruff check .`, and `mypy src` one
more time and confirm all three are clean before committing.
```

# Audit remediation prompt for Bob (round 8 — post ENH-3)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Item 1 is critical and
must be fixed before this code ever touches a real Db2 instance — treat it
as top priority. Fix both, then commit (e.g. "fix: gate superseded_at
predicate to semantic_facts only, validate reconciler decisions").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md, inserted before that template section — not
after it), then commit.

1. CRITICAL — list_all()/search()/create() reference superseded_at
   unconditionally across all five repositories, but only semantic_facts
   has that column (migration 0004 added it there only). BaseRepository's
   shared list_all(), search(), and the create() dedup-check all now
   unconditionally append "AND superseded_at IS NULL" to their WHERE
   clauses. working_memory, episodic_memory, entity_profiles, and
   procedural_memory have no superseded_at column, and none of those four
   repository classes override anything to compensate. This means every
   list_all()/search() call on those four types — and create()'s dedup
   path for episodic/profiles/procedural, which have _DEDUP_ON_WRITE=True
   — will fail against a real Db2 instance with a "column not found"
   error (SQLCODE -206). This wasn't caught because the unit test suite
   uses mocked cursors that never validate SQL against a real schema, and
   no integration test coverage was added for this change.

   Note: the current DECISIONS.md entry justifies this with "superseded_at
   IS NULL is vacuously true when the column doesn't exist" — that's not
   how SQL works. Referencing a nonexistent column is a compile-time
   error, not a vacuous truth. Correct the DECISIONS.md text as part of
   this fix, not just the code.

   Fix: add a class-level `_HAS_SUPERSESSION: bool = False` attribute to
   `BaseRepository`, mirroring the existing `_DEDUP_ON_WRITE` pattern
   exactly. Override it to `True` only in `SemanticFactRepository`. In
   `list_all()`, `search()`, and the `create()` dedup-check, only append
   the `AND superseded_at IS NULL` fragment when `self._HAS_SUPERSESSION`
   is `True` — the same conditional-fragment-building style already used
   for `min_confidence`'s `conf_sql`/`conf_params`.

   Add a regression test proving each of the four non-supersession
   repositories' generated SQL does NOT contain "superseded_at" anywhere
   (list_all, search, and create's dedup SELECT), and that
   SemanticFactRepository's generated SQL still does. Ideally also add or
   extend an integration test in tests/integration/ that calls
   list_all()/search() against a live Db2 instance for at least one
   non-facts repository (e.g. WorkingMemoryRepository), so a future
   regression like this would be caught even without anyone remembering
   to check generated SQL by hand — note in the entry if you skip this
   because no live Db2 is available in this environment, but write the
   test so it's ready to run.

2. MemoryStore.reconcile() applies SupersedeDecisions from the configured
   Reconciler with no sanity checks. decision.winner_id and
   decision.loser_id go straight into supersede() with nothing verifying
   winner_id != loser_id (self-supersession) or that winner_id refers to
   an actual live row in the same scope. Since Reconcilers are explicitly
   meant to be LLM-backed — the shipped example reconciler already has to
   defensively catch its own LLM's malformed JSON/out-of-range indices —
   a hallucinated or buggy decision could silently set a fact's
   superseded_by to itself or to a nonexistent id, undermining the audit
   trail this feature exists to provide.

   Fix: in MemoryStore.reconcile(), before calling supersede() for each
   decision: (a) skip and log a warning if
   decision.winner_id == decision.loser_id; (b) skip and log a warning if
   decision.winner_id is not one of the ids in the candidates list that
   was passed to the Reconciler (build a set of candidate ids once before
   the loop). Both cases should be treated like the existing "supersede
   returned False" case — logged, not raised, added to neither the
   applied list nor treated as a fatal error for the rest of the batch.
   Add tests for both rejected-decision cases.

After both: run `pytest`, `ruff check .`, and `mypy src` and confirm all
three are clean before committing.
```

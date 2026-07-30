# Audit remediation prompt for Bob (round 7 — post ENH-2)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Item 1 is a real
correctness/product-behavior issue, worth treating as top priority; the
rest are smaller. Fix all of them, then commit (e.g. "fix: scope dedup
away from WorkingMemory, document race condition, doc catch-up found in
ENH-2 audit").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md — insert this entry BEFORE that template section,
not after it; see item 4 below for why that matters), then commit.

1. Write-time dedup should not apply to WorkingMemory by default.
   create()'s dedup logic (ENH-2) currently runs identically across all
   five memory types, including WorkingMemory — an ordered, append-only
   conversation log where short repeated utterances ("ok", "yes",
   "thanks") are extremely common and legitimate. When a duplicate hash
   hits, create() returns the OLD existing row instead of inserting a new
   one, which means: (a) the conversation history silently loses that
   turn — wrong count, wrong ordering; (b) MemoryStore.remember()
   triggers the Consolidator on [stored] for every working/episodic
   write, so the consolidator now reprocesses the same stale old row a
   second time as if it were freshly written. There is currently no way
   to opt out — create()'s signature has no dedupe parameter, and
   test_create_dedup_returns_existing_when_hit in test_repositories.py
   exercises exactly this scenario against WorkingMemoryRepository as
   correct, intended behavior.

   Fix: add a class-level `_DEDUP_ON_WRITE: bool = True` attribute to
   `BaseRepository` (same pattern as the existing `_TABLE`/`_MODEL`/
   `EMBEDDING_DIM` class attributes), and override it to `False` in
   `WorkingMemoryRepository`. `create()` should skip the dedup SELECT
   entirely (not just skip the "return existing" branch — skip the query)
   when `_DEDUP_ON_WRITE` is `False`, so there's no wasted round-trip for
   the type that doesn't use it. Think through whether `EpisodicMemory`
   should also opt out (it's less turn-by-turn repetitive than raw
   working-memory turns, but still somewhat log-like) and record your
   reasoning either way in DECISIONS.md. `SemanticFact`/`EntityProfile`/
   `ProceduralMemory` should keep dedup on — that's the case it was
   designed for.

   Update the existing WorkingMemory-specific dedup test
   (test_create_dedup_returns_existing_when_hit) to assert the NEW
   correct behavior (no dedup, a new row is always inserted) and move/add
   a dedup-hit test against a type where it should still apply, e.g.
   SemanticFactRepository, so the positive case stays covered.

2. Document the dedup race condition instead of leaving it silent.
   The dedup check (SELECT ... FETCH FIRST 1 ROWS ONLY, then INSERT)
   isn't atomic — two concurrent create() calls with identical content
   can both pass the check before either INSERT lands, producing
   duplicates anyway. There's no DB-level backstop (no UNIQUE constraint,
   deliberately — and Db2 on this version doesn't support partial/
   filtered unique indexes per the Step 7 SQL0104N finding, so a full
   DB-level fix isn't trivially available). Add a clear note to
   create()'s docstring and to the DECISIONS.md ENH-2 entry stating this
   is a best-effort, non-atomic check — safe for the common single-writer
   or low-concurrency case, not a uniqueness guarantee under concurrent
   writers to the same scope.

3. ARCHITECTURE.md was not updated for the new content_hash column,
   despite ENH-2 adding it (plus a supporting index) to all five tables —
   confirmed zero mentions of content_hash anywhere in the file. Update
   section 3 (schema ER diagram — both the prose column-type legend AND
   the Mermaid diagram itself, not just one or the other) to add
   content_hash to all five tables.

4. The DECISIONS.md ENH-2 entry was inserted in the wrong place — it
   landed AFTER the "### Entry template (copy this for every new
   decision)" section instead of before it. That template block is
   meant to always be the last thing in the file. Move the ENH-2 entry
   to before the template section, in its correct chronological position
   among the other dated entries.

5. Unrelated to ENH-2 but noticed during audit: there's a stray
   uncommitted change to .gitignore in the working tree (appends a
   garbage " m" with no trailing newline) that doesn't correspond to any
   actual change — looks like an accidental keystroke. Revert it
   (`git checkout -- .gitignore` or just delete the stray characters) so
   it doesn't get swept into this or a future commit.

After all five: run `pytest`, `ruff check .`, and `mypy src` and confirm
all three are clean before committing.
```

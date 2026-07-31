# Audit remediation prompt for Bob (round 12 — post ORC-3)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Fix all three, then
commit (e.g. "fix: boolean metadata filter case mismatch, ARCHITECTURE.md
catch-up, .gitignore .DS_Store").

Paste the block below into Bob as-is.

---

```
Read project-management/DECISIONS.md in full first. Fix the following,
append one dated entry summarizing the fixes (use the entry template —
it now lives near the top of the file after the audit-prompt-10 move;
insert this new entry in its correct chronological position among the
other dated entries, at the end), then commit.

1. Boolean exact-match metadata filtering has a case mismatch that will
   make it silently match zero rows against real Db2. In
   _build_metadata_filter() (repositories/base.py), the exact-match /
   $not branch does `params.append(str(operand) if operand is not None
   else "null")` — for a Python bool, str(True) produces "True"
   (capitalized). But Db2's JSON_VALUE extracting a JSON boolean renders
   it as lowercase "true" per standard JSON/SQL convention, so the bound
   parameter never matches the extracted value. The $array_contains /
   $array_contains_any path already handles this correctly —
   _escape_json_path_value() special-cases bool to return lowercase
   "true"/"false" — this exact problem was already solved once in the
   same commit, just not applied to the exact-match/$not branch.

   Fix: in the exact-match and $not branches, convert bool values to
   lowercase JSON-style strings before binding — "true"/"false" — instead
   of the bare str(operand) call, matching what
   _escape_json_path_value() already does. Watch out for Python's
   isinstance(x, int) being True for bool (bool is an int subclass) if
   you refactor the type-dispatch logic — check bool before int/float,
   the same ordering _escape_json_path_value() already uses.

   Fix test_bool_field (and any other test asserting params == ["True"]
   for a bool field) to assert the corrected lowercase value instead —
   it currently enshrines the bug rather than catching it. Add a test
   confirming the exact-match and $array_contains paths now produce
   consistent bool string formatting for the same input.

2. ARCHITECTURE.md was not updated for ORC-3 at all — zero mentions of
   metadata_filter anywhere. This is the fourth time this category of gap
   has happened across the story series (Step 7, ENH-2, ENH-4, now this)
   — at this point, before writing anything else, re-read the "Working
   agreement" section of PROMPTS.md's ARCHITECTURE.md-update instructions
   and treat updating it as a checklist item you verify explicitly before
   marking a story Done, not something to remember from memory. Update
   the relevant section(s) to document the metadata_filter parameter on
   search()/list_all(), the supported operator set, and that it's backed
   by JSON_VALUE/JSON_EXISTS on the existing metadata column (no schema
   change).

3. A .DS_Store file (macOS Finder metadata) was committed in the ORC-3
   commit — .gitignore has no entry excluding it. Add `.DS_Store` to
   .gitignore and remove the tracked file from the repo (git rm --cached
   .DS_Store), leaving it on disk (it'll be locally ignored going
   forward).

After all three: run `pytest`, `ruff check .`, and `mypy src` and confirm
all three are clean before committing — and state the mypy result
explicitly in the commit message / DECISIONS.md entry even though it's
expected to be clean, per the standing note from the last audit round.
```

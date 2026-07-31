# Audit remediation prompt for Bob (round 10 — post ORC-1)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. One item, structural
only, no code changes. Commit as "docs: move DECISIONS.md entry template
to the top to stop entries landing after it".

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. The "### Entry template (copy this for
every new decision)" section has ended up in the middle of the file
twice now — most recently, the "ENH-4: claim-based consolidation
locking..." entry and the new "ORC-1: context cards..." entry both landed
AFTER the template instead of before it, so the file no longer ends with
the template at all. This happened once before (fixed for the ENH-2
entry) and has now recurred, which means "remember to insert above the
template" isn't a reliable enough convention on its own.

Fix structurally instead of just re-sorting entries again:

1. Move the "### Entry template (copy this for every new decision)"
   section (currently sandwiched in the middle of the file) to
   immediately after the file's opening explanation paragraph, right
   before the first dated entry — i.e. move it to near the TOP of the
   file, not the bottom. Add one line above it clarifying: "New entries
   go at the END of the file, after the last dated entry below — this
   template just documents the format; copy it, don't insert next to it."

2. Re-verify chronological order of all entries top-to-bottom after the
   move — confirm every entry is now in the correct place with nothing
   still out of order from the two past incidents (the ENH-2 entry and
   this ENH-4/ORC-1 pair). Fix ordering if anything is still off.

3. Do not change the content of any existing entry — this is a pure
   reorganization. If you find an entry whose content itself looks wrong
   or contradicts a later entry while doing this pass, don't fix it here
   — just note it in your completion summary so it can be looked at
   separately.

No pytest/ruff/mypy impact expected (markdown-only change), but run all
three anyway and confirm they're still clean before committing, since
that's the standing convention.
```

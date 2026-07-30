# Audit remediation prompt for Bob (round 6 — post ENH-1)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Both items are small.
Fix both, then commit (e.g. "fix: enforce confidence range, stale docstring
examples found in ENH-1 audit").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md — this entry should note it corrects/supersedes the
"no validator constraint at the Pydantic layer (application-level
convention)" line in the ENH-1 entry), then commit.

1. The confidence field has no actual range enforcement, despite a
   comment claiming otherwise. Migration
   `0003_confidence_and_content_hash.sql`'s header comment states "The
   application enforces the 0.0-1.0 range; the DB does not need a CHECK
   constraint" — but `_MemoryBase.confidence: float = 1.0` in models.py
   has no Pydantic constraint, and `create()`/`update()` in
   `repositories/base.py` bind whatever value is on the record with no
   check. Right now `SemanticFact(agent_id=..., content=..., confidence=57.0)`
   or a negative value persists silently, which would corrupt
   `min_confidence`-based filtering (and later ENH-3's reconciliation,
   which also relies on confidence being a meaningful 0-1 value).

   Fix: add a Pydantic constraint to the `confidence` field on
   `_MemoryBase` in models.py so out-of-range values are rejected at
   construction time — `Field(default=1.0, ge=0.0, le=1.0)`. Add tests
   confirming `confidence=1.5`, `confidence=-0.1`, etc. raise a Pydantic
   `ValidationError` on model construction. This makes the migration
   comment's claim actually true instead of aspirational — no SQL/schema
   change needed, this is a Python-layer-only fix.

2. Two stale docstring examples in models.py. `EntityProfile` and
   `ProceduralMemory`'s usage-example docstrings still show
   `metadata={"confidence": 0.95, "source": "episode-xyz"}` and
   `metadata={"skill": "debugging", "confidence": 0.9}` — leftover from
   before ENH-1 added a real first-class `confidence` field, when
   "confidence" was just an arbitrary key someone might put in the
   free-form metadata dict. Now that `record.confidence` exists, these
   examples model the wrong place to put it and would mislead a reader
   who copies them. Update both examples to either drop the
   metadata-level "confidence" key (since it's redundant now) or, if you
   want to show them together, use `confidence=0.95` as a real
   constructor argument alongside `metadata={...}` without "confidence"
   inside it.

After both: run `pytest`, `ruff check .`, and `mypy src` and confirm all
three are clean before committing.
```

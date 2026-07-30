# Audit remediation prompt for Bob (round 3 — post Step 4)

This is a one-off fix pass, separate from the Step N build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Both items are doc-only
consistency fixes found in a Step 4 audit; nothing here is a functional
bug, both are quick. Fix both, then commit (e.g. "fix: doc consistency
from Step 4 audit").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix these two doc-consistency issues
found in a Step 4 audit, append one dated DECISIONS.md entry noting the
fix (use the entry template at the bottom of DECISIONS.md), then commit.

1. ARCHITECTURE.md section 3's Mermaid ER diagram still annotates the
   `embedding` column as `"NOT NULL default zero-vec"` on all five tables
   (working_memory, episodic_memory, semantic_facts, entity_profiles,
   procedural_memory) — this is stale. The prose column-type legend right
   above the diagram in the same section was already correctly updated to
   say "NOT NULL, no DB-side default; application layer always supplies
   an explicit vector." Update the five ER diagram annotations to match —
   e.g. change `"NOT NULL default zero-vec"` to `"NOT NULL, app-supplied"`
   (or similar short phrasing) so the diagram and the prose agree.

2. `repositories/base.py`'s `purge_expired()` docstring is internally
   self-contradictory. It opens with two numbered conditions implying
   `expires_at` gates purge eligibility (condition 1: tombstoned AND
   expires_at < NOW; condition 2: tombstoned AND expires_at IS NULL), but
   its own "In short" line right after says "all tombstoned rows are
   eligible for purge" — which is what the code actually does, and
   matches the deliberate, already-documented decision in DECISIONS.md
   (the "purge_expired() semantics" entry from Step 4: deleted_at IS NOT
   NULL only, no expires_at check, by design). The numbered conditions are
   stale draft text that contradicts the real behavior. Delete the two
   numbered conditions (or rewrite them to match the "in short" summary),
   so the docstring doesn't mislead a reader into thinking TTL gates
   purge eligibility when it doesn't.

After both are done, no code changes are expected — just confirm
`pytest`, `ruff check .`, and `mypy src` are still clean (they should be
unaffected) before committing.
```

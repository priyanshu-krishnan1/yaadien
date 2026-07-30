# Audit remediation prompt for Bob (round 9 — post ENH-4)

This is a one-off fix pass, separate from the Epic/Story build sequence in
`PROMPTS.md` — do not edit `PROMPTS.md` for this. Fix both, then commit
(e.g. "fix: make --dedup-every-n behave correctly, ARCHITECTURE.md
catch-up for ENH-4").

Paste the block below into Bob as-is.

---

```
Read DECISIONS.md in full first. Fix the following, append one dated
DECISIONS.md entry summarizing the fixes (use the entry template at the
bottom of DECISIONS.md, inserted before that template section), then
commit.

1. scripts/consolidate_pending.py's --dedup-every-n silently does nothing
   for N >= 3. batches_completed resets to 0 on every fresh invocation of
   the script (no state persisted across runs) and can only reach a
   maximum of 2 within a single run, since type_to_repo only maps
   "working" and "episodic" — exactly two entries. The trigger condition
   `batches_completed % args.dedup_every_n == 0` can therefore only ever
   fire for dedup_every_n of 1 or 2; any higher value can never trigger
   under realistic cron-periodic usage. This wasn't caught because
   test_dedup_every_n_triggers_reconcile validates the modulo formula in
   isolation against a synthetic batches=[1..6] list rather than the
   real script's bounded counter.

   Pick one of these two fixes and justify the choice in DECISIONS.md —
   don't leave the current silently-wrong behavior in place:

   Option A — make it actually work as documented ("every N completed
   batches" implies cadence across invocations for a cron-invoked
   script). Persist an invocation/batch counter somewhere that survives
   across script runs — e.g. a small counter table in Db2 (new migration,
   one row per agent scope tracking a running count), or a local state
   file if you think that's an acceptable simplification for a
   single-machine cron setup (call out the multi-machine-cron limitation
   explicitly if you choose this). Increment it each run, trigger the
   Reconciler when the persisted counter crosses a multiple of N.

   Option B — make the CLI honest about its real, current limits instead
   of silently no-op'ing. Validate --dedup-every-n at argument-parsing
   time: reject values > 2 with a clear error explaining why (the
   two-memory-types-per-run constraint), or reinterpret the flag's
   semantics to something that's actually achievable per-invocation (e.g.
   "run the reconciler after processing all batches in this invocation"
   as a boolean-ish behavior) and update the help text and docstring to
   match reality rather than implying a persistent N-batch cadence.

   Whichever you pick, fix or replace test_dedup_every_n_triggers_reconcile
   so it actually exercises the real constraint (call the real
   batch-processing loop / the real trigger mechanism, not an isolated
   arithmetic assertion) — a test that only checks the formula in a
   vacuum is what let this ship.

2. ARCHITECTURE.md was not updated for ENH-4 at all. Zero mentions of
   consolidated_at anywhere (section 3's schema legend and Mermaid
   diagram), and section 4's remember() flow diagram still says "Last
   updated: Step 4" — it doesn't show the new _should_consolidate() /
   consolidate_every_n gate that now sits between a write landing and the
   consolidator actually firing. Update both: add consolidated_at to
   working_memory and episodic_memory in section 3 (prose legend AND the
   Mermaid diagram, not just one), and update section 4's sequence
   diagram to show the throttle check before the consolidator is invoked.

After both: run `pytest`, `ruff check .`, and `mypy src` and confirm all
three are clean before committing.
```

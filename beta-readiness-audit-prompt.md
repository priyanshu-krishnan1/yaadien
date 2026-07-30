# Beta readiness verification prompt for Bob

This is a pre-release verification pass, separate from the Epic/Story build
sequence in `PROMPTS.md` and separate from the one-off `audit-prompt-N.md`
fix rounds — do not edit `PROMPTS.md` for this. Unlike the earlier audit
rounds (each scoped to one just-finished story), this pass re-verifies
*everything already marked Done* against the current code, checks the
feature set against the market study, and produces a go/no-go call for a
worldwide public beta. It creates its own tracking epic on `BOARD.html`
rather than folding into EPIC-1/2/3.

Paste the block below into Bob as-is.

---

```
This SDK is being considered for a worldwide public beta release. Before
that happens, every story currently marked "Done" on BOARD.html needs an
independent re-verification against the actual source code (not against
what DECISIONS.md says was done — DECISIONS.md records intent, the code is
ground truth), and the feature set needs to be checked against the
competitive market study. Read, in full, before doing anything:

1. DECISIONS.md — every decision made so far and why
2. ARCHITECTURE.md — current-state design
3. BOARD.html — the embedded JSON (epics + stories + statuses), look for
   <script id="board-data" type="application/json">
4. PROMPTS.md — original spec/acceptance criteria for each Step/ENH/ORC
   story
5. ai-agent-platform-competitive-analysis.md — market study of competing
   agent-memory platforms (Mem0, Letta, LangMem, Oracle AI Agent Memory,
   Zep/Graphiti, Redis Iris, etc.) — this is the yardstick for beta
   feature-completeness and positioning, not a build spec to implement
   against literally

## Part 1 — Create the verification epic on BOARD.html

Add a new epic to the "epics" array:
  id: "EPIC-4"
  title: "Beta release readiness — worldwide public beta verification"
  description: one or two sentences — independent re-verification of every
    Done story plus a market-fit gap check against
    ai-agent-platform-competitive-analysis.md, gating the worldwide beta
    go/no-go decision.

Add one new story per story currently "Done" on the board (STEP-1 through
STEP-7, ENH-1 through ENH-4, ORC-1 — 12 stories as of this writing; if the
board has moved on since, use whatever is actually "Done" at the time you
run this). Do NOT create verification stories for anything still "To Do" or
"In Progress" (currently STEP-8, ORC-2, ORC-3, ORC-4) — those aren't done
yet, so there's nothing to verify; call them out as open blockers in the
Part 4 report instead. For each Done story, add:
  id: "VER-<n>" (sequential, starting after the highest existing story
    number pattern — check the board for what's free)
  epic_id: "EPIC-4"
  title: "Verify: <original story title>"
  summary: one line
  status: "To Do"
  comments: []

Add exactly one more story for the market-fit check:
  id: "VER-13" (or next free number)
  epic_id: "EPIC-4"
  title: "Market-fit gap check against ai-agent-platform-competitive-analysis.md"
  summary: "Cross-check implemented features against the market study;
    flag gaps as beta blockers, documented limitations, or out of scope."
  status: "To Do"
  comments: []

Commit this as its own commit ("board: add EPIC-4 beta readiness
verification stories") before starting Part 2.

## Part 2 — Work each VER-N story, one at a time, in order

For each VER-N story:
1. Set its status to "In Progress" in BOARD.html.
2. Re-read that story's original spec in PROMPTS.md (the matching Step N /
   ENH-N / ORC-1 section) — that is the acceptance bar to check against.
3. Verify against the ACTUAL current code:
   a. The feature works as specified — trace through the real
      implementation, don't just confirm the function/class exists.
   b. Tests exist and pass (`pytest`) and actually exercise the claimed
      behavior, not just import the module.
   c. `ruff check .` and `mypy src` are clean for the files involved.
   d. Any security-sensitive surface (SQL construction, scoping/
      tenant-isolation enforcement, injection surfaces) — re-check by hand,
      line by line. Do not trust a prior audit's "fixed" note without
      re-confirming it against the code as it exists today.
   e. Docs (README.md / ARCHITECTURE.md / docstrings) still match what the
      code actually does.
4. If you find a bug, gap, or doc drift: fix it directly if it's small; if
   it's large enough to need its own scoped pass, write it up the way this
   repo's audit-prompt-N.md files already do, and stop for approval before
   starting that fix rather than scope-creeping this verification pass.
5. Append one dated DECISIONS.md entry per VER-N story (use the entry
   template near the top of DECISIONS.md) summarizing what was checked and
   what, if anything, was fixed.
6. Set the story's status to "Done" and add a comment to it summarizing the
   verification result (what was checked, what was found).
7. Commit ("verify: VER-N <short description>") before moving to the next
   story.

Give VER-5 (governance/scoping enforcement, STEP-5) extra scrutiny: as far
as BOARD.html and the audit-prompt-*.md history show, it is the one Done
story that never got a dedicated post-hoc audit pass the way STEP-3, STEP-4,
STEP-6, STEP-7, and ENH-1 through ENH-4 did. It is also the multi-tenant
isolation boundary — the exact thing a worldwide public beta with unrelated
tenants sharing infrastructure cannot get wrong. Treat it with the same
rigor as the SQL-injection fix that came out of the Step 7 audit
(audit-prompt-5.md): assume nothing, re-derive correctness from the code
itself, not from what any prior note claims.

## Part 3 — Market-fit gap check (VER-13)

Set VER-13 to "In Progress". Read ai-agent-platform-competitive-analysis.md
again with this specific question in mind: for each capability the market
study identifies as a competitive differentiator or table-stakes
expectation for an agent-memory platform (multi-tenant isolation, audit/
erasure (GDPR-style) scoping, temporal/bi-temporal fact handling, hybrid
retrieval quality, cost/token control, contradiction resolution +
supersession, deduplication), assess: does this SDK have it, partially have
it, or not have it? Build a short table (capability | have/partial/missing |
evidence in code). For anything "partial" or "missing," make an explicit
call: is it a beta blocker, a documented known-limitation acceptable to ship
beta with, or genuinely out of scope for what this SDK is trying to be —
one sentence of reasoning each. Append one DECISIONS.md entry for this,
same as the other VER-N stories, then set VER-13 to "Done".

## Part 4 — Beta readiness report

After every VER-N story is Done, append a "## Beta Readiness Report"
section to DECISIONS.md (not a new file) covering:
- Pass/fail summary for each VER-N story (one line each)
- The market-fit gap table from Part 3, with the blocker/limitation/
  out-of-scope call for each gap
- The still-outstanding non-Done stories (STEP-8, ORC-2, ORC-3, ORC-4) and
  an explicit call on each: hard blocker for a worldwide public beta, or
  acceptable to ship beta with a documented limitation
- One explicit go/no-go recommendation for worldwide beta release, with the
  reasoning laid out plainly enough that someone who wasn't in this session
  can act on it

Epics don't carry a status field on this board — don't try to mark EPIC-4
itself "Done"; just make sure every VER-N story under it is Done, or
explicitly deferred with a reason logged in its comments.

As always: read DECISIONS.md before starting, append to it as you go
(per-story, not one giant entry at the end), commit after each meaningful
unit of work, and don't touch Jira MCP — still not wired up for this
project, tracking stays on BOARD.html.
```

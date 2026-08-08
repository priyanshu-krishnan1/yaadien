# AI-Native Agile for agent-memory-sdk

**How EPIC-27..33 get planned, sharded, executed by subagents, and verified**

Status: methodology · Date: 2026-08-08 · Companion to
`project-management/design/ADVANCED_MEMORY_ARCHITECTURE.md`

---

## 0. The thesis in one paragraph

Classical agile ceremony exists to manage two scarce resources: **human attention**
and **communication bandwidth between humans**. Standups, estimation, and sprint
boundaries are all bandwidth-conservation devices. When the implementer is an agent
spawned per story, neither scarcity applies in the same form — an agent has no
memory of yesterday's standup, cannot be interrupted, and communicates only through
artifacts. What *is* scarce is **context window**, **verification budget**, and
**merge surface**. AI-native agile is what you get when you re-derive the ceremony
from those three constraints instead of the old two.

This repo has already, and mostly by accident, built most of the machinery:
the sharded JSON board exists so parallel agents don't collide on one file
(DECISIONS.md, 2026-08-05), the `[Spike]` title convention is already in use
(CIW-8, CIW-10, CIW-12), and `.ua/knowledge-graph.json` is a machine-readable
map of the codebase. This document names the method those pieces imply and
fills the gaps.

---

## 1. Three graphs, and which question each answers

An AI-native process runs on graphs, not documents. Three of them, each already
present or introduced by EPIC-27..33:

| Graph | Where it lives | Question it answers | Consumed by |
|---|---|---|---|
| **Code graph** | `.ua/knowledge-graph.json` — 123 nodes, 303 edges, 5 layers, edge types `contains / exports / calls / imports / inherits / depends_on / migrates / implements / related` | *What does this story touch, and what else touches that?* | Bob, at story-assignment time, to compute merge surface |
| **Work graph** | `project-management/board/{epics,stories}/*.json` + the `Depends on:` / `Blocks:` lines in descriptions | *What can start now, and what is a story silently blocked on?* | Bob, at wave-planning time |
| **Decision graph** | `DECISIONS.md` + spike findings in story comments | *Why is it this way, and what would reverse it?* | Every subagent, at session start |

**The one gap worth closing.** Dependencies are currently prose inside
`description` (`"Depends on STOR-1 and STOR-2"`), which is greppable but not
computable. That is fine at 217 stories and one human curator; it stops being fine
when Bob is scheduling waves automatically. The cheapest fix is a convention
Bob parses rather than a schema change: dependencies always appear as a final
paragraph matching `^Depends on: (.+)\.$` with comma-separated story ids. If that
proves too brittle, add optional `depends_on: []` / `blocks: []` arrays to the
story shape — `build.py`'s validator only checks for required fields, so extra keys
pass today and the front-end ignores them.

> **Note the recursion, because it is the reason this repo is a good testbed.**
> The work graph is a dependency-typed edge set with provenance. That is
> structurally the same object as `memory_edges` (STOR-1). The methodology and
> the product are the same shape, and a lesson learned on one transfers to the other.

---

## 2. Planning: the dependency graph is the plan; the sprint is a rendering of it

Classical sprint planning picks a set of stories and freezes it for two weeks.
That freeze exists to protect humans from re-planning cost. Agents have no
re-planning cost, so the freeze buys nothing and costs the ability to react to a
spike finding the moment it lands.

Replace the sprint with a **wave**: the maximal set of stories whose dependencies
are all satisfied and whose **merge surfaces are disjoint**. A wave ends when its
stories are merged, not when a calendar boundary arrives. Waves are recomputed
after every merge and after every spike finding.

**Wave selection algorithm** (Bob runs this; it is a topological sort with a
conflict constraint):

```
1. ready      = { s : status = "To Do" and every dep in s has status = "Done" }
2. for each s in ready:
       surface(s) = files s will touch, from the .ua code graph
                    + declared migration numbers
                    + declared board files
3. greedily select from `ready`, highest unblocking-degree first
   (how many stories does completing s unblock?),
   skipping any s whose surface intersects an already-selected surface
4. cap the wave at the number of subagents you can actually review
```

Step 3's ordering criterion is the one thing worth getting right: **prioritise by
how much a story unblocks, not by how valuable it is.** STOR-1 is not intrinsically
exciting — it is one table — but it unblocks 11 stories across four epics, so it
runs first and alone. Value ordering is what a human roadmap optimises; unblocking
degree is what a parallel machine optimises.

**Computed unblocking degree for the current arc:**

| Story | Unblocks | Wave |
|---|---|---|
| `STOR-1` | STOR-2, STOR-5, STOR-8, GRPH-1, GRPH-2, EVOL-3, EVOL-7, GOV-6 (+transitively most of EPIC-30/32) | 1, alone |
| `VIDX-1` | VIDX-5, VIDX-6, STOR-7, and the credibility of every existing APPROX benchmark number | 1, parallel |
| `ASMB-1` → `ASMB-6` | the metric every other epic is judged by | 1, parallel |
| `EVOL-2` | EVOL-1 → EVOL-3/5, GRPH-2, GRPH-5 | 1, parallel |
| `RTRV-6` | RTRV-4 tuning, EVOL-4, EVOL-5 | 2 |
| `STOR-3` | STOR-4 → GRPH-6 | 2 |

### Wave 1 (four parallel subagents, zero merge overlap)

`STOR-1` (new migration + new repository file) · `VIDX-1` (spike, read-only) ·
`ASMB-1` → `ASMB-6` (benchmarks + `store.py` card assembly) · `EVOL-2` (spike, read-only).

Two spikes and two builds. The spikes are read-only so they cannot conflict with
anything, which makes them free to run in parallel with everything — a property
worth exploiting deliberately rather than noticing accidentally.

### Why VIDX-1 and ASMB-6 are in wave 1 despite being unglamorous

Both are **instruments**, and an arc without instruments produces unfalsifiable
claims. VIDX-1 tells us whether the vector index has ever been used; ASMB-6 gives
us the denominator for the ratio this whole arc claims to optimise. Every story
that ships before them ships without evidence.

---

## 3. Story design rules, so parallel execution is safe by construction

A story is well-formed for agent execution when a subagent with **no prior context**
can complete it from the story text plus the repo. Five rules, each earning its place:

**R1 — One merge surface.** A story touches one migration, or one module, or one
benchmark, or the board — not several. Where the arc violates this (`RTRV-1` and
`VIDX-5` both touch `chunks.py`), the stories say so explicitly so Bob sequences
them rather than discovering the conflict at merge.

**R2 — Acceptance criteria are executable.** "Works correctly" is not a criterion.
"A cross-scope edge read returns nothing, proven by a test that writes under one
scope and reads under another" is. A subagent cannot ask a follow-up question; the
criterion has to carry the whole specification.

**R3 — The null path is the default.** Every new behaviour ships off by default with
the old behaviour reproduced *exactly* — and the story asserts that with a test, not
by inspection. This is what makes ablation (§5) possible and what keeps a wave of
four parallel merges from compounding into an unreviewable behaviour change.

**R4 — State the reversal condition.** Every non-obvious choice records what would
undo it, in DECISIONS.md. Six months later the reasoning is gone and only the code
remains; without the reversal condition, the next agent either cargo-cults the
decision or relitigates it from scratch. Both are expensive.

**R5 — Name the dependency in the last paragraph.** Machine-parseable, human-readable,
and it forces the author to actually think about ordering.

### The negative-space rule

Every epic in EPIC-27..33 contains a **"deliberately not doing this"** section.
This is not editorial garnish — it is load-bearing. An agent asked to "improve
retrieval" will reinvent ColBERT; an agent asked to "improve the index" will propose
an ANN parameter sweep that Db2 has no way to accept. Writing down the rejected
option *and its reason* is what stops the same proposal arriving every quarter.
EPIC-28 goes further and **retracts** a recommendation from the research doc it
descends from, because the doc was written before we knew Db2's index is already
DiskANN. Retraction in place beats a stale document.

---

## 4. The spike protocol

12 of the 52 new stories are spikes. That ratio (23%) is deliberate and reflects
how much of this arc is genuinely unknown rather than merely unbuilt.

**A story becomes a spike when it satisfies all three:**

1. There is a real fork, and both branches are defensible.
2. The evidence to choose does not exist in the repo or the docs.
3. Choosing wrong is more expensive than the investigation.

Everything else is a task with a decision already made. `[Spike]` in the title, per
the existing CIW-8/10/12 convention.

**Every spike carries five things**, and the arc's spikes all do:

- **A timebox in the description.** "One working session." A spike without one
  becomes a research project, and an agent will happily spend an unbounded budget
  being thorough.
- **The specific hypothesis under test**, not a topic. VIDX-1 does not say
  "investigate index performance"; it says the `VECTOR_SERIALIZE` predicate may
  prevent the optimizer choosing `VECIDX`, and specifies diffing the plan with and
  without that clause.
- **The comparison table pre-drawn.** STOR-7 names three storage shapes and five
  evaluation axes before any work starts, so the finding is a filled-in table rather
  than an essay.
- **An explicitly acceptable negative outcome.** VIDX-7 says out loud that "Db2
  doesn't support this, close it" is a success. Without that sentence an agent will
  find *something* to recommend, because recommending nothing feels like failure.
- **A named output artifact.** A findings comment on the story, plus a DECISIONS.md
  entry, plus any follow-up stories. A spike that ends in a chat message has produced
  nothing durable.

**Spikes gate their dependents explicitly.** EVOL-6 gates EVOL-3 and EVOL-7 —
if verbatim storage beats consolidation on our corpus, two built stories are deleted
rather than shipped. GRPH-4 gates GRPH-5 the same way. Naming the gate in the story
text is what makes "we ran the spike and ignored it" visible.

---

## 5. Ablation is the definition of done

The strongest available critique of this entire arc is empirical: a controlled
ablation ([*Verbatim Chunks Beat Extracted Artifacts*](https://arxiv.org/pdf/2601.00821))
found raw conversation chunks outperforming LLM-extracted facts for long-conversation
memory. If that replicates here, several stories in EPIC-31 are negative-value work.

The methodological response is not to argue with it. It is:

> **A feature is Done when it has won its own ablation and the numbers are recorded.
> Not when the tests pass.**

Which requires three things this repo mostly has:

1. **Every feature is independently toggleable.** R3 above. `benchmarks/quality/test_config_matrix.py`
   is the existing home for the resulting grid.
2. **The metric is accuracy per assembled token, not recall@k.** A change that
   raises recall while inflating the context card is a regression. ASMB-6 makes this
   computable; until it lands, no story in the arc can honestly claim a win.
3. **Lift is attributed, not assumed.** The Entity-Collision protocol
   ([arXiv:2605.29630](https://arxiv.org/pdf/2605.29630)) exists specifically to
   distinguish a real mechanism win from entity-overlap leakage. EVOL-6 applies it;
   any story reporting a surprising win should too.

This inherits directly from EPIC-11's benchmark-integrity work and from this repo's
existing discipline of never silently replacing a recorded run (visible in the
BENCHMARKS.md "Run C (pre-fix — re-run)" annotation). AI-native agile is not a
licence to ship faster on weaker evidence; the whole point of cheap implementation
is that it makes rigorous verification *affordable* rather than optional.

---

## 6. The harness contract

Adopting the standard vocabulary — **Agent = Model + Harness**, where the harness is
everything except the model: tools, context, verification, orchestration, permissions
([Agent Harness Engineering survey](https://picrew.github.io/LLM-Harness/main.pdf);
[Fowler, *Harness engineering for coding agent users*](https://martinfowler.com/articles/harness-engineering.html)).

Bob is the **orchestrator**; per-story subagents are **workers**. The
orchestrator-worker split is the pattern behind Anthropic's multi-agent research
system, which reports ~90% improvement over single-agent on internal evals — and
also reports roughly **15x the token consumption**, which is the honest reason
wave width is capped by review capacity rather than by parallelism.
([Anthropic engineering blog](https://www.anthropic.com/engineering/multi-agent-research-system);
[when to use multi-agent systems](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them).)

### What Bob hands a subagent (the inbound contract)

```
1. The story JSON — verbatim, not summarised. Summarising is where
   acceptance criteria get lost.
2. Its epic's description — the findings and scope boundary are the
   "why", and a worker without the why will solve the wrong problem
   correctly.
3. The .ua subgraph for the files in scope — neighbours, not the
   whole graph. This is the single biggest context-window saving
   available and the main reason .ua earns its keep.
4. Relevant DECISIONS.md entries — filtered by the files in scope,
   not the whole file.
5. Findings from any spike this story depends on.
6. The repo conventions it must not violate:
     - `# nosec B608` documentation pattern on interpolated SQL
     - scope predicates on EVERY read and write, no exceptions
     - migrations are append-only and numbered
     - board writes touch exactly one JSON file
     - no new required runtime dependency (ibm_db + pydantic only)
```

Item 6 is the one most often skipped and most expensive to skip. A subagent that
interpolates SQL without the `# nosec` justification, or that adds a required
dependency, produces a diff that looks fine and violates a property the project
has deliberately maintained.

### What comes back (the outbound contract)

```
1. The diff.
2. Test evidence — command run and output, not "tests pass".
3. Ablation numbers where the story is a feature (§5).
4. A DECISIONS.md entry for every non-obvious choice, with its
   reversal condition (R4).
5. A story comment: {"date": "YYYY-MM-DD", "text": "…"} — the
   current shape only; the two legacy shapes are accepted by the
   validator but must not be extended.
6. An explicit list of what was NOT done and why.
```

Item 6 again does disproportionate work. A worker that silently descopes produces a
story marked Done that isn't, and the next wave plans against a false premise. An
honest "I could not confirm X, so I left it" is worth more than a confident partial.

### Status semantics under parallel execution

`To Do → In Progress → Done` is unchanged, but **`In Progress` now means "a subagent
holds this story's merge surface"** — it is a lock, not a progress indicator. Two
subagents must never hold overlapping surfaces, which is what §2 step 3 enforces.
A subagent that cannot finish sets the story back to `To Do` with a comment
explaining the blocker and, per §4, opens a spike if the blocker is a genuine
unknown. It does not leave the story `In Progress` — that would hold the lock
forever.

---

## 7. Guides and sensors: the verification ladder

The most useful current frame for this is Böckeler's
([*Harness engineering for coding agent users*](https://martinfowler.com/articles/harness-engineering.html),
Thoughtworks, Apr 2026), which splits harness controls along two axes:

- **Guides (feedforward)** — steer the agent *before* it acts, raising the odds of a
  correct first attempt.
- **Sensors (feedback)** — observe *after* it acts and let it self-correct.
  Most powerful when their output is written for LLM consumption, e.g. a linter
  message that carries the fix instruction.

Crossed with:

- **Computational** — deterministic, milliseconds, CPU. Tests, linters, type checkers,
  EXPLAIN plans, recall harnesses.
- **Inferential** — semantic, slow, non-deterministic, GPU. LLM-as-judge, review agents.

Her warning is the one that matters most for this arc: *feedback-only* produces an
agent that repeats the same mistakes forever, and *feedforward-only* produces an agent
that encodes rules and never learns whether they worked. This repo currently leans
feedback-heavy — strong CI, weak written guidance (there is no `AGENTS.md` at the repo
root, despite EPIC-23 referencing one).

### What this repo has, mapped

|  | Computational | Inferential |
|---|---|---|
| **Guides** (feedforward) | `build.py` validator · migration numbering · `Makefile` targets · pre-commit hook · `.ua` code graph | epic descriptions · `DECISIONS.md` · the `# nosec` justification convention · **gap: no `AGENTS.md` / coding-rules skill** (EPIC-23 WF-4 fills this) |
| **Sensors** (feedback) | ruff · mypy · bandit · pytest · live-Db2 suite (EPIC-10) · **VIDX-3 recall harness** · **GOV-7 poisoning ASR** · EXPLAIN assertions | LLM judge (`benchmarks/quality/lme_judge.py`) · agent-quality metrics (EPIC-21) · code-review subagent |

Two of the arc's stories are pure new *computational sensors* in cells that are
currently empty, which is why they are wave-1 priorities: **VIDX-3** (is the index
engaged, and at what recall) and **GOV-7** (does a poisoned corpus reach the card).
Both convert a claim that was previously inferential-or-nothing into a deterministic
number.

### The ladder

Verification cost scales with blast radius, not with story size. Four rungs mapping
onto the existing EPIC-17 CI tiering:

| Rung | Applies to | Gate |
|---|---|---|
| **0 — Static** | every story | ruff, mypy, bandit; the `# nosec` pattern present and justified |
| **1 — Unit** | every story | pytest; coverage does not regress |
| **2 — Live Db2** | anything touching SQL, DDL, or the repository layer | the EPIC-10 live-integration suite. **Spikes about Db2 behaviour are invalid without this rung** — every VIDX finding and EVOL-2 must come from a real database, not from documentation. This repo has been wrong about Db2 behaviour from docs three times (parameter-bound vectors, `VECTOR_SERIALIZE`+`VECTOR_DISTANCE`, and the DiskANN retraction in EPIC-28) |
| **3 — Ablation** | any story claiming a quality or performance win | §5: on/off numbers, accuracy per assembled token, provenance per the EPIC-19 standard |

**Keep quality left.** Rungs 0-1 run before the subagent hands back — they are part of
its self-correction loop, not part of review. Rung 2 runs on integration. Rung 3 runs
post-integration on the schedule EPIC-17 already defines. A sensor that only fires
after human review has already spent the expensive resource.

**Instruments are verified by being used.** VIDX-3 is correct when it detects a
deliberately-degraded index; GOV-7 is correct when it detects a deliberately-poisoned
corpus. Both stories should include that negative control, because a sensor that never
fires is indistinguishable from a sensor that cannot fire — which is Böckeler's open
question and a real risk for GOV-7 in particular.

---

## 8. Anti-patterns this process is specifically designed to prevent

**Confident completion of the wrong thing.** A subagent will implement exactly what
the story says, including the parts that are wrong. Mitigated by the epic description
travelling with the story (harness contract item 2) so the worker can tell that the
letter of the story contradicts its purpose.

**Silent descoping.** Mitigated by outbound contract item 6.

**Spike drift into a research project.** Mitigated by the timebox and the pre-drawn
comparison table.

**Merge-surface collisions discovered at merge time.** Mitigated by computing surface
from `.ua` at *assignment* time. The board's shard-per-record design already
eliminates this class for board writes specifically — one story is one file, so
parallel board updates cannot conflict. Extending the same principle to code is what
the surface computation does.

**Ablation theatre.** Running the ablation, getting a negative result, and shipping
anyway. Mitigated by naming the gated stories in the spike text (EVOL-6 gates EVOL-3
and EVOL-7) so skipping the gate is visible in the board rather than only in
someone's judgement.

**Documentation drift.** Mitigated by retraction-in-place: EPIC-28 opens by
withdrawing a recommendation from the research doc rather than leaving a stale
recommendation to be discovered later. Same reason `build.py` regenerates
`BOARD.html` from the shards and the pre-commit hook stages it — a derived artifact
that can go stale, won't.

**Multi-agent for its own sake.** ~15x tokens is the documented cost. A single story
with one merge surface does not need three subagents. Wave width is bounded by review
capacity, not by how many workers can be spawned.

---

## 9. Running the arc

**Cadence.** Recompute the wave after every merge and after every spike finding.
No fixed sprint boundary. A weekly human review reads the DECISIONS.md delta and the
ablation numbers — not the diffs — because the diffs are verified by rung 0-3 and
the *judgement calls* are what need a human.

**Order.** Wave 1 as computed in §2. Thereafter: EPIC-27 and EPIC-28 to completion
(they gate almost everything), then EPIC-29 and EPIC-32 in parallel (disjoint
surfaces — retrieval vs. governance), then EPIC-31, then EPIC-33's remaining stories,
then EPIC-30 last and only if its spikes clear.

**Expected mortality, stated up front so it reads as success rather than failure:**
GRPH-5 dies if GRPH-4 finds no scope large enough. EVOL-3 and EVOL-7 die if EVOL-6
finds verbatim wins. VIDX-7 and VIDX-8 die if VIDX-2 finds Db2 doesn't expose what
they need. RTRV-8 dies if the latency budget isn't there. **That is roughly 6 of 52
stories designed to be cancelled by evidence, and cancelling them on schedule is the
process working.** An arc where every story ships is an arc whose spikes were
decorative.

---

## References

**Harness and orchestration**

- [Böckeler — Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html) — **the primary source for §7**: guides/sensors × computational/inferential, the steering loop, harnessability, Ashby's Law
- [Böckeler — Sensors for coding agents](https://martinfowler.com/articles/sensors-for-coding-agents.html) — the follow-up, on maintainability sensors specifically
- [LangChain — The anatomy of an agent harness](https://blog.langchain.com/the-anatomy-of-an-agent-harness/) — origin of `Agent = Model + Harness`
- [Agent Harness Engineering: A Survey](https://picrew.github.io/LLM-Harness/main.pdf) — prompt vs. context vs. harness engineering
- [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [OpenAI — Harness engineering](https://openai.com/index/harness-engineering/) — layered architecture enforced by custom linters and structural tests; recurring drift "garbage collection"
- [Stripe — Minions: one-shot end-to-end coding agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents) — shift-feedback-left in practice
- [From Question Answering to Task Completion: Agent System and Harness Design](https://arxiv.org/html/2606.20683v1)
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — orchestrator-worker, ~90% lift, ~15x tokens
- [Anthropic — When to use multi-agent systems (and when not to)](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them)
- [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
- [Harnessing Agent Skills: Architectural Patterns for Skill-Mediated LLM Agents](https://arxiv.org/pdf/2606.20631)

**Evidence discipline**

- [Verbatim Chunks Beat Extracted Artifacts](https://arxiv.org/pdf/2601.00821) — the ablation that gates EPIC-31
- [Entity-Collision: Attributing Retrieval Lift in Agent Memory](https://arxiv.org/pdf/2605.29630) — separating mechanism wins from leakage
- [LongMemEval](https://arxiv.org/abs/2410.10813) · [LoCoMo](https://arxiv.org/abs/2402.17753)

**Internal**

- `project-management/design/ADVANCED_MEMORY_ARCHITECTURE.md` — the seven planes this arc implements
- `project-management/design/memory-architecture-explorer.html` — browsable companion
- `project-management/board/README.md` — the shard model and its rationale
- `project-management/DECISIONS.md` — the decision graph
- `.ua/knowledge-graph.json` — the code graph
- EPIC-11 (benchmark integrity) · EPIC-17 (CI tiering) · EPIC-19 (provenance standard) · EPIC-23 (Bob's context-hub memory) · EPIC-25 (Db2 dialect seam)

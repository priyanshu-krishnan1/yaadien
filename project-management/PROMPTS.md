# Build prompts for agent-memory-sdk

This is a ready-to-use sequence of prompts for building `agent-memory-sdk` —
a Python library giving AI agents a governed, multi-type memory system
backed by IBM Db2 LUW.

Architecture decisions were made after researching three reference
implementations: the OpenAI Agents SDK memory model, Oracle AI Agent Memory
(a single unified/governed memory core), and Microsoft Agent
Framework's Cosmos DB memory (normalized collections + async background
extraction). The design below is a deliberate hybrid: normalized per-type
tables (closer to Microsoft's approach, chosen because Db2's native vector
index requires a `NOT NULL` vector column per type) with pluggable
synchronous-by-default consolidation (avoiding Microsoft's mandatory
background-worker infra, keeping this a plain installable library).

**How to use this file:** paste **Step 0** first into a fresh session with
your coding agent (Claude Code, Codex, etc.), then feed it Steps 1–8 one at
a time, in order. Each step assumes the agent can see what was built in the
previous steps (same session, or point it at the repo in a new one).

## Where these files live

This file, `BOARD.html`, `DECISIONS.md`, `ARCHITECTURE.md`,
`INTEGRATION_TESTING.md`, `Chats.md`, `BENCHMARKS.md`, the market study,
and every `audit-prompt*.md` (now under `project-management/audits/`) all
live together under **`project-management/`** at the
repo root — moved there so the repo root only shows what actually ships
(`README.md`, `pyproject.toml`, `src/`, `tests/`, `scripts/`). When a step
below says to read or update one of these files by bare name (e.g.
"`DECISIONS.md`"), and the agent's working directory is the repo root (the
normal case), that means `project-management/<file>`, not a repo-root file
of that name — it no longer exists there. Steps and audit prompts written
before this move may still say things like "read DECISIONS.md" without the
prefix; the intent is unchanged, only the path is. Source-code links (e.g.
into `src/agent_memory_sdk/`) are unaffected — those files never moved.

## Working agreement across sessions

Because a build like this often spans multiple agent sessions (or multiple
tools), every step below ends with the same two instructions: read
[`DECISIONS.md`](DECISIONS.md) before starting, and append to it before
finishing. **Do not skip these lines when pasting a step**, even if it feels
redundant within one continuous session — they're what keeps a fresh
session (or a different tool) from silently re-deciding something already
settled, or losing a decision it made that nobody wrote down.

There's also [`ARCHITECTURE.md`](ARCHITECTURE.md) — the current-state design
doc (component diagram, schema ER diagram, sequence flows, all in Mermaid).
Unlike DECISIONS.md, it's updated **in place**, not appended to: it should
always reflect what the system looks like right now, not a history. Steps
2, 3, 4, and 6 below call out when to update it; if any other step ends up
changing a boundary or flow, update it there too even if not explicitly
told to.

Also commit after each step (`git add -A && git commit -m "step N: ..."`).
That gives you a clean checkpoint to roll back to if a later step goes
sideways, without losing earlier steps.

## MCP tools available in Bob for this project

Bob has several MCP connections configured. Only some fit a headless
Python/Db2 SDK with no UI — use these deliberately, and leave the rest
alone so Bob doesn't burn time setting up things this project doesn't need:

**Use these:**
- **Product Knowledge** (ready to use, Milvus-backed semantic search over
  IBM's product knowledge bases) — check this first for anything
  IBM/Db2-specific: exact `VECTOR` type syntax, `CREATE VECTOR INDEX`
  clauses, `ibm_db`/`ibm_db_dbi` driver behavior, DiskANN parameters. It's
  more authoritative than an agent's trained knowledge on a fast-moving
  feature like Db2 vector search. Called out explicitly in Steps 1 and 2.
- **Web search** (ready to use, Tavily) — fallback for anything Product
  Knowledge doesn't cover: LangChain / OpenAI Agents SDK / MCP spec
  details, general Python packaging questions.

**Not used — explicitly out of scope for this project:**
- **Jira** — not working in this Bob setup (MCP connection unreachable).
  Tracking uses a local HTML board instead — see "Tracking: local board,
  not Jira MCP" below. Don't retry Jira MCP calls; if it starts working
  again later, that's a separate decision, not an assumption to make
  mid-build.
- **Figma, Carbon, Mural** — design/UI tools; this is a headless library
  with nothing to design. Leave disabled, don't invoke them.
- **Airtable, Amplitude, Monday.com** — require setup, and none fit this
  project (Airtable/Amplitude are structured-data and analytics tools,
  Monday.com would just duplicate the local board as a tracker). Don't set
  these up for this project.

## Tracking: local board, not Jira MCP

Jira wasn't reachable through Bob's Jira MCP connection, so tracking is a
**local, self-contained HTML board** instead: [`BOARD.html`](BOARD.html)
(at `project-management/BOARD.html` from the repo root — see "Where these
files live" above). No server, no login, nothing to authorize — open it
directly in a browser.
It's pre-populated with one Epic ("agent-memory-sdk") and one Story per
build step (STEP-1 through STEP-8), each already carrying that step's
summary.

The board's data is a plain JSON blob embedded in `BOARD.html` itself (look
for `<script id="board-data" type="application/json">`), so an agent
updates it the same way it updates `DECISIONS.md` or `ARCHITECTURE.md` —
edit the file, then commit. The working agreement is: at the *start* of a
step, edit that story's `"status"` field to `"In Progress"`; at the *end*,
alongside the DECISIONS.md append and git commit already required, set
`"status"` to `"Done"` and push a `{"date": "...", "text": "..."}` entry
into its `"comments"` array summarizing what was built — include that edit
in the same commit as the rest of the step's work (git log already has the
exact commit, no need to reference the hash). This is already folded into
each step's prompt below. Refresh the page in a browser any time to see
current status.

---

## Step 0 — Context (paste first, every time you start a new agent session on this repo)

```
We are building `agent-memory-sdk`, a Python library that gives AI agents a
governed, multi-type memory system backed by IBM Db2 LUW (using the VECTOR
data type and VECTOR_DISTANCE / vector indexes introduced in Db2 12.1.2+ for
semantic search).

DECISIONS ALREADY MADE (do not re-litigate these):
- Language: Python only.
- Database: Db2 LUW. Driver: ibm_db (native) + ibm_db_dbi (DB-API 2.0 wrapper)
  as the primary connectivity layer.
- Memory taxonomy (synthesized from OpenAI Agents SDK, Oracle AI Agent
  Memory, and Microsoft Agent Framework/Cosmos DB memory docs), four types:
    1. working memory   – raw current-session/thread turns, short-lived
    2. episodic memory   – summarized past runs/threads/events
    3. semantic memory    – extracted facts + aggregated entity/user profiles
    4. procedural memory  – learned skills/instructions/how-to knowledge
- Storage shape: NORMALIZED PER-TYPE TABLES (one table per memory type above),
  not one polymorphic table — because Db2's vector index requires a NOT NULL
  vector column, and each memory type has a differently-shaped embedding.
- Vector search: use Db2's native VECTOR column type + VECTOR_DISTANCE
  (support cosine, euclidean, dot, manhattan) + CREATE VECTOR INDEX
  (DiskANN-based ANN), with FETCH EXACT / FETCH APPROX / FETCH query options
  exposed to callers.
- Processing model: extraction/consolidation is PLUGGABLE and SYNCHRONOUS BY
  DEFAULT (a developer-supplied callback run inline on remember()), with an
  explicit opt-in hook to run it asynchronously later — the SDK must work as
  a plain library with zero mandatory background services.
- Framework integration: FRAMEWORK-AGNOSTIC CORE first. Adapters (LangChain,
  OpenAI Agents SDK Session protocol, MCP server tools) are thin layers on
  top, built after the core, not baked into it.
- Scoping/governance: hierarchical scoping columns on every memory row —
  tenant_id (nullable, for single-tenant use) > agent_id > user_id >
  thread_id/session_id. All reads/writes must be scoped; no cross-scope
  leakage by default.
- Lifecycle: soft-delete/tombstone (never hard DELETE by default), explicit
  forget() API, per-row TTL/expires_at with a sweep/purge method, and a
  version column for optimistic concurrency / audit.

Do not change these decisions. If something here seems wrong once you're in
the code, flag it explicitly and ask before deviating.

All process/tracking docs — BOARD.html, DECISIONS.md, ARCHITECTURE.md,
PROMPTS.md (this file), INTEGRATION_TESTING.md, Chats.md, BENCHMARKS.md,
the market study, and every audit-prompt*.md (under project-management/
audits/) — live under project-management/ at the repo
root, not at the repo root itself. Your working directory for git/pytest/
etc. is still the repo root; when any instruction below says "read
DECISIONS.md" or similar by bare name, that means
project-management/DECISIONS.md.

Tracking uses project-management/BOARD.html, a local self-contained HTML
board (not Jira — Jira's MCP connection isn't working). It already exists,
pre-populated with an Epic and one Story per step, all in "To Do" — no
setup needed. Open it in a browser to see current status; later steps
update its embedded JSON directly as work happens.
```

---

## Step 1 — Scaffold

```
Before starting: in BOARD.html, set STEP-1's status to "In Progress".

Scaffold the `agent-memory-sdk` Python package. Use a standard src-layout
(`src/agent_memory_sdk/`), `pyproject.toml` (build via hatchling or
setuptools, your choice — state which and why), and dependencies: ibm_db,
ibm_db_dbi, pydantic v2. Add dev deps: pytest, ruff, mypy.

Use the Product Knowledge MCP tool to confirm current best practice for
`ibm_db`/`ibm_db_dbi` connection setup and any known gotchas (e.g. required
CLI driver install steps, connection string format) before writing the
connection module — don't rely on training-data assumptions for
IBM-specific driver behavior.

Create a `Db2Connection`/connection-pool module (`db/connection.py`) that:
- reads connection params from env vars (DATABASE, HOSTNAME, PORT, UID, PWD,
  SECURITY) with a documented .env.example
- wraps ibm_db_dbi.connect with a small manual pool (a bounded queue of
  connections, since ibm_db_dbi has no built-in pooling)
- exposes a context-manager `get_connection()` for safe checkout/checkin

Write a `scripts/check_connection.py` that opens a connection and runs
`SELECT 1 FROM SYSIBM.SYSDUMMY1` to verify connectivity. Do not write any
schema or memory logic yet — this step is scaffolding + connectivity only.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry (use its entry template at the bottom) recording your build
backend choice and reason, and any other decision/deviation you made. In BOARD.html, set STEP-1's status to "Done" and add a comment summarizing
what you built. Then `git add -A && git commit -m "step 1: scaffold"`.
```

---

## Step 2 — Schema & migrations

```
Before starting: in BOARD.html, set STEP-2's status to "In Progress".

Design and write the Db2 DDL for the four per-type memory tables (working,
episodic, semantic_facts, entity_profiles, procedural), per the Step 0
decisions. For each table include: id, tenant_id, agent_id, user_id,
thread_id, content (CLOB or VARCHAR based on expected size), metadata
(JSON column), embedding (VECTOR(<dim>, FLOAT32) NOT NULL — default to a
zero-vector if none provided, document why), created_at, updated_at,
expires_at (nullable), version, deleted_at (nullable, for soft-delete).

Use the Product Knowledge MCP tool to verify the exact current `VECTOR`
column DDL syntax, `CREATE VECTOR INDEX` clause options, and DiskANN
parameters/limitations (e.g. the NOT NULL requirement for the index to be
used) against IBM's own docs before finalizing the DDL — this feature is
new enough that exact syntax matters and shouldn't be guessed. Fall back to
Web search only if Product Knowledge doesn't have it.

Add CREATE VECTOR INDEX statements per table using DiskANN with a documented
distance metric choice per type (justify cosine vs euclidean per table).
Add supporting indexes for the scoping columns (tenant_id, agent_id,
user_id, thread_id) since most queries will filter by these before ranking
by vector distance.

Build a minimal SQL migration runner (`db/migrations/`, numbered .sql files
+ a `migrate.py` that applies pending ones and tracks applied versions in a
`schema_migrations` table) — do not pull in alembic, keep it dependency-light
since ibm_db_dbi/Db2 support in alembic is inconsistent.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the distance metric you chose per table (and why),
and the content/metadata column types you chose (CLOB/VARCHAR/JSON), plus
any other deviation. Update section 3 (schema ER diagram) of
ARCHITECTURE.md to match what you actually built. In BOARD.html, set
STEP-2's status to "Done" and add a comment summarizing what you built.
Then `git add -A && git commit -m "step 2: schema"`.
```

---

## Step 3 — Core models & repositories

```
Before starting: in BOARD.html, set STEP-3's status to "In Progress".

Implement Pydantic models for the four memory types (WorkingMemory,
EpisodicMemory, SemanticFact, EntityProfile, ProceduralMemory) matching the
Step 2 schema.

Implement a repository class per type (e.g. `WorkingMemoryRepository`) with:
- create/upsert, get_by_id, list (scoped + filtered), soft_delete
- a `search(query_embedding, scope, top_k, metric, mode=EXACT|APPROX)`
  method that builds the VECTOR_DISTANCE SQL with FETCH EXACT/APPROX
- all methods REQUIRE at minimum agent_id scope; reject calls missing scope

Define an `EmbeddingProvider` protocol (a callable: text -> vector) that
callers inject — the SDK must not hard-depend on a specific embedding model.

Add a top-level `MemoryStore` facade that composes all four repositories
behind one object (`store.working`, `store.episodic`, `store.facts`,
`store.profiles`, `store.procedures`), so callers usually import one class.

Write unit tests using a fake/in-memory repository (mock ibm_db_dbi cursor)
so tests don't require a live Db2 instance.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the embedding-dimension approach (how it's
parameterized) and any repository/API-shape decisions you made. Update
section 1 (system overview) of ARCHITECTURE.md if the actual class/module
names or boundaries differ from what's drawn there. In BOARD.html, set
STEP-3's status to "Done" and add a comment summarizing what you built.
Then `git add -A && git commit -m "step 3: models and repositories"`.
```

---

## Step 4 — Lifecycle: TTL, versioning, forget, consolidation

```
Before starting: in BOARD.html, set STEP-4's status to "In Progress".

Add lifecycle features to the repositories/MemoryStore from Step 3:
- `forget(id, scope)` — sets deleted_at (tombstone), never hard-deletes by
  default; add a separate `purge_expired()` maintenance method that hard-
  deletes rows past expires_at AND already soft-deleted, callable via a
  script/cron, not automatically.
- optimistic concurrency on `version` for updates (raise on stale write)
- a `Consolidator` protocol: a pluggable callback
  `(raw_memories: list) -> list[derived_memory]` that MemoryStore can
  invoke synchronously after writes to working/episodic memory, producing
  semantic facts / entity profile updates / procedural memory. Ship a
  no-op default consolidator plus a documented example of wiring in an
  LLM-based one. Make clear in docs how a caller would instead run this
  async (e.g. call it from a cron job reading unconsolidated rows) —
  implement the sync path now, just document the async extension point.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the Consolidator protocol shape and the
purge_expired() semantics you settled on. Update section 4 (remember()
flow) of ARCHITECTURE.md if the actual consolidation trigger/timing
differs from what's drawn there. In BOARD.html, set STEP-4's status to
"Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "step 4: lifecycle"`.
```

---

## Step 5 — Governance / scoping enforcement

```
Before starting: in BOARD.html, set STEP-5's status to "In Progress".

Harden scoping across the SDK: add a `MemoryScope` value object
(tenant_id, agent_id, user_id, thread_id) that's required on every
MemoryStore call instead of loose kwargs. Ensure every generated SQL
statement includes scope predicates (never allow a query with only an id
and no scope check — this is the multi-tenant isolation boundary). Add
tests that assert cross-scope reads return nothing even if you know another
scope's row id.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the MemoryScope shape and any edge cases you had to
resolve. In BOARD.html, set STEP-5's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "step 5: scoping"`.
```

---

## Step 6 — Framework adapters

```
Before starting: in BOARD.html, set STEP-6's status to "In Progress".

Build three thin adapters on top of the Step 3-5 core, each in its own
optional-dependency submodule (agent_memory_sdk.adapters.langchain,
.openai_agents, .mcp):
1. LangChain: implement BaseChatMessageHistory backed by
   store.working, and optionally a BaseStore implementation for
   facts/profiles.
2. OpenAI Agents SDK: implement the Session protocol
   (per https://openai.github.io/openai-agents-python/sandbox/memory/)
   backed by store.working + store.episodic.
3. MCP: expose remember/recall/forget/list as MCP tools so any
   MCP-compatible agent can use the SDK without a Python import.

Keep the core package importable with zero adapter dependencies installed;
gate each adapter behind an extras_require group in pyproject.toml
(e.g. `pip install agent-memory-sdk[langchain]`).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording any adapter-specific decisions (e.g. how LangChain's
BaseStore maps onto facts vs profiles). Update section 1 (system overview)
of ARCHITECTURE.md's adapter boxes to match what you actually built. In
BOARD.html, set STEP-6's status to "Done" and add a comment summarizing
what you built. Then `git add -A && git commit -m "step 6: adapters"`.
```

---

## Step 7 — Integration tests

```
Before starting: in BOARD.html, set STEP-7's status to "In Progress".

Add integration tests that run against a real Db2 LUW instance (document
how to spin one up locally, e.g. the ibmcom/db2 Docker image) gated behind
an env var / pytest marker so they're skippable in CI without Db2. Cover:
schema migration end-to-end, vector search correctness (known nearest
neighbor), scope isolation, TTL purge, forget/tombstone, and each adapter's
basic round-trip (LangChain history, OpenAI Session, MCP tool calls).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry noting any gaps found between what DECISIONS.md says and what
the code actually does (fix or flag them). In BOARD.html, set STEP-7's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "step 7: integration tests"`.
```

---

## Step 8 — Docs & examples

```
Before starting: in BOARD.html, set STEP-8's status to "In Progress".

Write the README (install, quickstart with docker Db2, the four memory
types explained, scoping model, lifecycle features) and one runnable
example per adapter under examples/. Keep examples short — under 50 lines
each, showing store setup, a remember() call, and a recall() call.

Before starting: read DECISIONS.md in full — the README should reflect it
accurately, not the original Step 0 aspiration if anything changed along
the way. In BOARD.html, set STEP-8's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "step 8: docs and examples"`.
```

---

# Epic 2 — Cosmos-inspired memory enhancements (Db2-adapted)

Everything below is a second phase, tracked as `EPIC-2` in
[`BOARD.html`](BOARD.html) (Stories `ENH-1` through `ENH-4`), separate from
the `EPIC-1` / Step 1-8 build sequence above. It assumes Steps 1-7 are
already done — it builds directly on the existing schema, repositories,
and `Consolidator` machinery rather than starting fresh. Same working
agreement as above (read `DECISIONS.md` first, update it +
`ARCHITECTURE.md` where noted + `BOARD.html` before finishing, commit each
one separately) — paste `ENH-1` through `ENH-4` one at a time, in order,
since `ENH-2` shares a migration with `ENH-1`, `ENH-3` depends on that
migration existing, and `ENH-4`'s Reconciler-integration half depends on
`ENH-3` (its `consolidated_at`/locking half does not, and may ship first
if you want to reorder those two).

These four were chosen after researching Azure Cosmos DB's Agent Memory
Toolkit (github.com/AzureCosmosDB/AgentMemoryToolkit) and filtering its
feature set through what's actually Db2-native-feasible — see the
"2026-07-31 — EPIC-2 backlog" entry in `DECISIONS.md` for the full
research writeup, what else the toolkit does that was deliberately left
out of this set, and why.

---

## ENH-1 — Confidence scoring on memory records

```
Before starting: in BOARD.html, set ENH-1's status to "In Progress".

Add a `confidence` field (float, 0.0-1.0, default 1.0) to `_MemoryBase`
and a matching column on all five Db2 tables via a new migration (bundle
with ENH-2's content_hash column in the same migration file if you're
doing both stories back to back). Update `create()`/`update()` in
`repositories/base.py` to persist it and `_model_from_row()` to read it
back. Add an optional `min_confidence: float = 0.0` parameter to
`search()` and `list_all()` that appends an `AND confidence >= ?`
predicate to the WHERE clause.

The pluggable `Consolidator` protocol's derived records can now carry a
genuine grounding-certainty score (e.g. an LLM-based consolidator sets
confidence=0.6 for a tentative inference vs 0.95 for an explicit user
statement) instead of implicitly defaulting to 1.0 for everything it
derives — update the `Consolidator` docstring's example to show this.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the migration file name, the confidence column's
exact type/default, and how min_confidence interacts with the existing
deleted_at/expires_at filters in the WHERE clause. Update section 3
(schema ER diagram) of ARCHITECTURE.md to add the new column. In
BOARD.html, set ENH-1's status to "Done" and add a comment summarizing
what you built. Then `git add -A && git commit -m "enh-1: confidence scoring"`.
```

---

## ENH-2 — Write-time exact-duplicate rejection via content hash

```
Before starting: in BOARD.html, set ENH-2's status to "In Progress".

Add a `content_hash VARCHAR(64)` column (hex SHA-256 of normalized
content — lowercased and whitespace-collapsed before hashing) to all five
tables via the same migration as ENH-1 if not already done, plus a
supporting index on `(agent_id, content_hash)`. In `create()`, compute the
hash before INSERT; if a non-deleted, non-superseded row already exists in
the same scope with the same content_hash, return that existing row
instead of inserting a new one (an idempotent write) rather than silently
creating a duplicate.

Note: "non-superseded" only becomes a real filter once ENH-3 lands
(superseded_at doesn't exist yet if you're doing these in order) — for now
the dedup check only needs `deleted_at IS NULL`; revisit this check when
you do ENH-3 so it also excludes superseded rows.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the hash normalization rule (exact steps: lowercase,
whitespace-collapse, then SHA-256) and confirm it's applied consistently
everywhere content_hash is computed or compared. In BOARD.html, set
ENH-2's status to "Done" and add a comment summarizing what you built.
Then `git add -A && git commit -m "enh-2: write-time dedup via content hash"`.
```

---

## ENH-3 — Reconciliation: contradiction detection with supersession

```
Before starting: in BOARD.html, set ENH-3's status to "In Progress".

Add `superseded_by VARCHAR(36)`, `superseded_at TIMESTAMP`,
`supersede_reason VARCHAR(255)` (all nullable) to `semantic_facts` via a
new migration (optionally also to `entity_profiles`/`procedural_memory`,
your call — justify whichever you pick in DECISIONS.md). Add a
`Reconciler` protocol in `types.py`, parallel in shape to the existing
`Consolidator`: `(candidates: list[SemanticFact]) -> list[SupersedeDecision]`,
where each decision names a winner id, a loser id, and a reason string
(e.g. "contradicts: user now prefers light mode"). Ship a
`NoOpReconciler` default, matching the `NoOpConsolidator` pattern exactly.

Add `MemoryStore.reconcile(memory_type, scope)` that fetches recent,
non-superseded facts for a scope, runs the configured Reconciler, and for
each decision sets the loser's `superseded_by`/`superseded_at`/
`supersede_reason` — a soft-supersede, NOT a hard delete and NOT a
`forget()`-tombstone. Keep this a distinct mechanism from `deleted_at`:
it lets an audit trail tell "the user asked us to forget this" apart from
"we learned this was contradicted by a newer fact," which is a real
governance distinction, not just a naming preference.

Update `list_all()`/`search()` to also exclude `superseded_at IS NOT NULL`
rows from normal reads, the same way they already exclude
`deleted_at IS NOT NULL` rows. Go back to ENH-2's dedup check in
`create()` and have it also exclude superseded rows now that the column
exists.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the Reconciler protocol shape, the migration file
name, and why you did/didn't extend supersession to entity_profiles and
procedural_memory. Update section 3 (schema) of ARCHITECTURE.md for the
new columns. In BOARD.html, set ENH-3's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "enh-3: reconciliation and supersession"`.
```

---

## ENH-4 — Formalize the async consolidation worker + EVERY_N cadence

```
Before starting: in BOARD.html, set ENH-4's status to "In Progress".

Two related changes to the existing consolidation pipeline:

1. `scripts/consolidate_pending.py` currently finds pending rows via a
   `metadata.consolidated: false` JSON flag — its own docstring already
   flags this as a stand-in, not a production implementation. Add a
   `consolidated_at TIMESTAMP` (nullable) column to `working_memory`/
   `episodic_memory` via a new migration, switch the eligibility filter to
   `WHERE consolidated_at IS NULL`, and add a claim-based update
   (`UPDATE ... SET consolidated_at = ? WHERE id = ? AND consolidated_at
   IS NULL`, checking rowcount) so two concurrent worker instances can't
   double-process the same row — the basic idempotency/locking the
   script's own docstring says a real implementation needs.

2. Add an optional `consolidate_every_n: int = 1` setting on
   `MemoryStore` (default 1 = today's behavior — consolidate on every
   write) so the *inline* synchronous consolidator only fires every Nth
   `remember()` call per scope, reducing LLM-call cost on the hot write
   path. Track the per-scope counter however's simplest given the
   existing code (in-memory on the MemoryStore instance is fine for v1;
   note in DECISIONS.md that this resets on process restart and isn't
   shared across multiple app instances, since that's a real limitation
   worth being upfront about, not a hidden gotcha).

Also have the worker script optionally invoke the ENH-3 Reconciler every
`--dedup-every-n` batches (mirrors the toolkit's own DEDUP_EVERY_N
pattern this whole epic is inspired by).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the claim-based locking mechanism, the
consolidate_every_n counter implementation and its known limitations, and
confirm this worker is documented as the Db2-appropriate substitute for
Cosmos's change-feed-triggered async tier (no new external service
dependency — keeps the Step 0 "zero mandatory external services"
principle intact). In BOARD.html, set ENH-4's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "enh-4: async worker hardening and EVERY_N cadence"`.
```

---

# Epic 3 — Oracle-inspired memory enhancements (Db2-adapted)

A third phase, tracked as `EPIC-3` in [`BOARD.html`](BOARD.html) (Stories
`ORC-1` through `ORC-4`), independent of Epic 1 and Epic 2 above — none of
these four depend on Epic 2 having been done, only on Steps 1-5 (schema,
repositories, scoping) from Epic 1. Same working agreement as the sections
above (read `DECISIONS.md` first, update it + `ARCHITECTURE.md` where
noted + `BOARD.html` before finishing, commit each one separately). `ORC-1`,
`ORC-3`, and `ORC-4` are independent of each other and can be done in any
order; `ORC-2` (content chunking) is the largest and most self-contained —
do it on its own, not interleaved with the others.

These four were chosen after researching Oracle AI Agent Memory
(blogs.oracle.com/developers/oracle-ai-agent-memory-a-governed-unified-memory-core-for-enterprise-ai-agents
and the `oracleagentmemory` PyPI package) and filtering its feature set
through what's actually Db2-native-feasible — see the "2026-08-01 —
EPIC-3 backlog" entry in `DECISIONS.md` for the full research writeup,
what else Oracle's SDK does that was deliberately left out of this set,
and why (including a second, independent case for the hybrid-search
question already deferred in the EPIC-2 entry).

---

## ORC-1 — Context card: condensed working-memory view for the active thread

```
Before starting: in BOARD.html, set ORC-1's status to "In Progress".

Add `MemoryStore.get_context_card(scope, max_turns=20)` returning a small
structured object (not just a raw list): recent working-memory turns in
chronological order, a turn count, and the timestamp of the most recent
turn. This is a convenience/formatting layer over `store.working.list_all()`
— no new schema, no LLM call required by default.

Add an optional `summarizer` hook (same pluggable-callback shape as
`Consolidator`/`Reconciler` — a single `__call__` protocol, ship a no-op
default) so a caller who wants an actual condensed narrative (not just the
raw recent turns) can supply one. Default behavior with no summarizer
configured is the raw-turns view.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the context-card object's exact shape (fields) and
the summarizer protocol signature. Update section 1 (system overview) of
ARCHITECTURE.md if this warrants a new box, or note in the entry why it
doesn't. In BOARD.html, set ORC-1's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "orc-1: context card"`.
```

---

## ORC-2 — Content chunking for long memories

```
Before starting: in BOARD.html, set ORC-2's status to "In Progress".

For content exceeding a configurable threshold (e.g. > 2000 characters),
split it into overlapping chunks at write time and embed each chunk
separately, instead of today's one-embedding-per-row approach regardless
of length (a 64KB CLOB currently gets a single embedding, a poor semantic
representation of the whole text).

Add a new companion table via a new migration — either one shared
`memory_chunks` table (`id`, `source_table`, `source_id`, `chunk_index`,
`chunk_text`, `embedding VECTOR(...) NOT NULL`, scope columns for
isolation, `CREATE VECTOR INDEX`) or a `_chunks` table per existing type;
pick one and justify the choice in DECISIONS.md. `create()`/`update()` in
`repositories/base.py` gain chunking logic gated by the length threshold —
content under the threshold behaves exactly as today (single embedding on
the parent row, no chunk rows created).

Add a `search(..., search_chunks=True)` mode that searches the chunks
table first (finer-grained semantic match against chunk text) then
resolves and dedupes back to parent records, ranked by each parent's
best-matching chunk distance — the same reorder-after-fetch pattern
already used for the two-step search() workaround from Step 7, so reuse
that logic rather than reinventing it.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the chunking threshold, the chunk-overlap strategy,
the shared-vs-per-type table decision and why, and the chunk-to-parent
resolution/dedup logic. Update section 3 (schema) of ARCHITECTURE.md for
the new table. In BOARD.html, set ORC-2's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "orc-2: content chunking"`.
```

---

## ORC-3 — Structured metadata filter operators for search()/list_all()

```
Before starting: in BOARD.html, set ORC-3's status to "In Progress".

Add a `metadata_filter: dict | None = None` parameter to `search()` and
`list_all()` supporting a small operator set: exact match
(`{"source": "support"}`), `$not` (`{"status": {"$not": "archived"}}`),
`$array_contains` and `$array_contains_any` for list-valued metadata
fields (e.g. tags). Translate the filter dict into
`JSON_VALUE(metadata, '$.field')` / `JSON_EXISTS(metadata, ...)`
predicates appended to the existing WHERE clause, alongside the scope and
deleted_at/expires_at predicates already there. No schema change —
`metadata` is already `VARCHAR(4096)` JSON text.

Keep the operator set small and well-tested rather than building a
general query language. Reject unrecognized operator keys with a clear
error rather than silently ignoring them.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exact operator set implemented and the
JSON_VALUE/JSON_EXISTS translation for each. In BOARD.html, set ORC-3's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "orc-3: structured metadata filters"`.
```

---

## ORC-4 — Schema attach mode: REQUIRE_EXISTING policy for the migration runner

```
Before starting: in BOARD.html, set ORC-4's status to "In Progress".

Add a schema-policy concept to `Migrator`: `CREATE_IF_NECESSARY` (today's
only behavior — run pending migrations, create tables/indexes) vs
`REQUIRE_EXISTING` (validate that every expected table, column, and
vector index already exists via `SYSCAT.TABLES` / `SYSCAT.COLUMNS` /
`SYSCAT.INDEXES` catalog queries; raise one clear, actionable error
listing everything missing, and never attempt any DDL). Wire this as a
constructor argument on `Migrator`, defaulting to `CREATE_IF_NECESSARY` so
existing behavior is unchanged unless a caller opts in.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exact SYSCAT queries used for validation and
the error-message format. In BOARD.html, set ORC-4's status to "Done" and
add a comment summarizing what you built. Then
`git add -A && git commit -m "orc-4: schema attach mode"`.
```

---

# Epic 5 — Production hardening: CI, security, packaging, and benchmarking

Everything below is tracked as `EPIC-5` in [`BOARD.html`](BOARD.html)
(Stories `PH-1` through `PH-6`), separate from both the `EPIC-1` build
sequence and the `EPIC-4` beta-readiness verification pass. EPIC-4 checks
that already-built features are correct; this epic builds infrastructure
that doesn't exist yet — there is no CI today (no `.github/` directory),
`pytest-cov` is a declared dependency nobody invokes, and there is no
repeatable way to measure retrieval quality, cost, or isolation-under-load
against the numbers this project's own
`ai-agent-platform-competitive-analysis.md` cites for competing platforms.
VER-13's market-fit gap check (EPIC-4) flagged the missing README/docs
(`STEP-8`) as a hard blocker for a worldwide beta; this epic is the
companion fix for everything else a public release needs that isn't a
product feature.

Same working agreement as the other epics (read `DECISIONS.md` first,
update it + `BOARD.html` before finishing, commit each story separately).
Suggested order: `PH-1` before `PH-2` (the integration CI job builds on the
same workflow file as the base CI job); `PH-3` and `PH-4` can go in either
order or in parallel with each other; `PH-5` is independent; `PH-6` is the
largest and most optional of the six — it needs a live Db2 instance and an
LLM/embedding provider configured, and depends on nothing else here, so
it's reasonable to defer it behind the other five if time is short.

`STEP-8` (docs & examples) is not part of this epic and should not be
folded into it — it's already its own story on the board (`EPIC-1`,
currently "To Do") and remains the harder blocker of the two for a public
release.

---

## PH-1 — CI pipeline: lint, type-check, and unit tests on every PR

```
Before starting: in BOARD.html, set PH-1's status to "In Progress".

Add `.github/workflows/ci.yml` with a job matrix over Python 3.10, 3.11,
and 3.12 (matching `requires-python` and the classifiers in
pyproject.toml). For each matrix entry: install with
`pip install -e ".[dev]"`, run `ruff check .`, run `mypy src`, and run
`pytest` (unit suite only — `tests/integration/` self-skips without
`DB2_DATABASE` set via the existing `pytest_collection_modifyitems` hook
in `tests/integration/conftest.py`, so no extra exclusion flag is needed).
Cache pip dependencies keyed on the pyproject.toml hash. Trigger on push
to main and on pull_request. Once green, add a status badge to README.md.

Do not install the `[langchain]`/`[openai-agents]`/`[mcp]` extras or run
adapter tests against them beyond the default dev install in this job —
that's out of scope here.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the workflow file path, the Python version matrix,
and exactly which commands each CI step runs. In BOARD.html, set PH-1's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "ph-1: CI lint/type-check/unit-test pipeline"`.
```

---

## PH-2 — CI integration job: live Db2 service container running the marked integration suite

```
Before starting: in BOARD.html, set PH-2's status to "In Progress".

Add a second job (same workflow file as PH-1, or a separate one if the
Db2 boot time would slow down the fast unit job) that boots a Db2 LUW
container, sets the DB2_* env vars documented in .env.example, applies
migrations via the existing Migrator, and runs `pytest -m integration`.
Reuse the exact setup already documented in
project-management/INTEGRATION_TESTING.md rather than inventing a new
one; if CI needs something that doc doesn't cover (an image tag that
works unattended, a longer startup timeout), update
INTEGRATION_TESTING.md to match instead of letting the two drift. Db2
startup is slow — use a real wait/health-check loop, not a fixed sleep.

This closes the gap where ~77 integration tests exist and pass locally
but nothing outside a developer's machine has ever proven
`pytest tests/integration/` runs green.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the container image/version used, the wait
strategy, and confirm INTEGRATION_TESTING.md still matches. In
BOARD.html, set PH-2's status to "Done" and add a comment summarizing
what you built. Then
`git add -A && git commit -m "ph-2: CI integration job against live Db2"`.
```

---

## PH-3 — Coverage reporting and threshold gate

```
Before starting: in BOARD.html, set PH-3's status to "In Progress".

pytest-cov is already listed in pyproject.toml's dev extras but nothing
invokes it — no --cov in addopts, no report generated anywhere. Add
`--cov=agent_memory_sdk --cov-report=xml --cov-report=term-missing` to
the PH-1 unit-test CI step, upload the XML report to Codecov (or
Coveralls, whichever needs less setup for a not-yet-public repo), add the
resulting badge to README.md, and set a minimum threshold via
`--cov-fail-under` (propose 85%, given how thorough the VER-1..VER-10
audit notes show the existing unit suite to be) as a merge-blocking
check. Scope coverage to `src/agent_memory_sdk` only — not `tests/` or
`scripts/`.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the chosen threshold and the coverage-reporting
service used. In BOARD.html, set PH-3's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "ph-3: coverage reporting and threshold gate"`.
```

---

## PH-4 — Dependency and static security scanning

```
Before starting: in BOARD.html, set PH-4's status to "In Progress".

Add a CI job running `pip-audit` against the resolved dependency set
(fail on any known-exploitable CVE with no available fix; record any
accepted/ignored advisory with a reason). Add `bandit` scoped at minimum
to `db/`, `repositories/`, and `store.py` — the modules VER-5 hand-
verified for SQL injection safety (`_scope_predicates`, `_vec_to_str`,
`_build_metadata_filter`, and the REQUIRE_EXISTING catalog queries in
`db/migrate.py`). Where bandit flags a pattern VER-5 already established
as safe (e.g. the `float()` coercion guard in `_vec_to_str` before string
interpolation), add a scoped `# nosec` with a comment pointing at the
DECISIONS.md VER-5 entry rather than silencing the whole file — the goal
is to keep the manual audit's conclusions enforced mechanically, not to
bulk-suppress the tool.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry listing every suppression added and why, so a future reader
isn't left guessing whether a `# nosec` is a real risk acceptance or
someone silencing noise. In BOARD.html, set PH-4's status to "Done" and
add a comment summarizing what you built. Then
`git add -A && git commit -m "ph-4: dependency and static security scanning"`.
```

---

## PH-5 — Packaging build verification

```
Before starting: in BOARD.html, set PH-5's status to "In Progress".

Add a CI job (at minimum on tags/releases, ideally on every PR since it's
cheap) that: runs `python -m build` to produce sdist + wheel, runs
`twine check dist/*`, creates a fresh throwaway venv, `pip install`s the
built wheel (not the editable source tree — the point is to catch
`[tool.hatch.build.targets.wheel] packages = [...]` misconfiguration or
missing-file bugs that `pip install -e .` would never surface), and runs
a minimal smoke test that imports `agent_memory_sdk` and touches one
symbol from each of models/store/db to confirm the package layout is
intact. Also verify the `[langchain]`, `[openai-agents]`, `[mcp]`, and
`[all]` extras each install cleanly from the built wheel.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the smoke-test symbols checked and confirming all
four extras installed cleanly. In BOARD.html, set PH-5's status to "Done"
and add a comment summarizing what you built. Then
`git add -A && git commit -m "ph-5: packaging build verification"`.
```

---

## PH-6 — Agent-memory benchmarking harness: retrieval quality, latency/cost, and isolation-under-load

```
Before starting: in BOARD.html, set PH-6's status to "In Progress".

Build a `benchmarks/` harness (excluded from the wheel via the hatchling
wheel target, same treatment as `project-management/`) with three parts:

1. Retrieval quality — a LongMemEval-shaped synthetic dataset
   (multi-session conversations with planted facts, later contradictions,
   and questions covering LongMemEval's five ability categories:
   extraction, multi-session reasoning, temporal reasoning, knowledge
   updates, abstention) run through MemoryStore.remember()/search(),
   scored by an LLM judge closely enough to the LongMemEval methodology
   (arXiv 2410.10813) that the result is honestly comparable to the
   vendor figures already cited in ai-agent-platform-competitive-
   analysis.md. Document any methodology deviation explicitly rather than
   calling a number "LongMemEval" if it isn't.
2. Latency/cost — per-remember()/per-search() latency, and per-turn token
   cost only where a Consolidator/Reconciler/Summarizer hook is actually
   configured (the no-op default path should report near-zero LLM cost,
   itself a comparison point against the extraction-pipeline competitors
   in the market study).
3. Isolation-under-load — concurrent writers across many synthetic
   tenants/agents hammering search()/list_all(), asserting zero
   cross-scope result leakage under concurrent load, not just the
   single-threaded conditions VER-5's manual audit checked. This measures
   the governed-substrate claim in the market study's SWOT instead of
   only asserting it.

Requires a live Db2 instance and a configured EmbeddingProvider/LLM —
runs on demand via a `scripts/run_benchmarks.py` entry point, not in the
PH-1/PH-2 CI jobs. Publish results as `project-management/BENCHMARKS.md`
with the exact dataset size, model, and embedding provider used, caveated
the same way ai-agent-platform-competitive-analysis.md caveats the
vendor-reported figures it cites.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the dataset size/methodology and a summary of
results. In BOARD.html, set PH-6's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "ph-6: agent-memory benchmarking harness"`.
```

---

## EPIC-6 — Benchmark findings: retrieval-quality gap vs. flat-context baseline

PH-6 built the harness; this epic is downstream of it — acting on what it
actually measured. Run B in `BENCHMARKS.md` (llama3.1:8b judge,
nomic-embed-text embeddings, n=50, seed=42) shows the with-SDK path
scoring *below* a flat-context (no SDK) baseline: 84.0% vs 94.0% overall
(-10.0%), with multi_session and temporal_reasoning at -30.0% each,
extraction and knowledge_update at -10.0% each, and abstention a clear
SDK win at +30.0%. Work the stories below **in order** — BENCH-1 first,
always — since it determines whether BENCH-2/3/4 are even the right fix.

---

## BENCH-1 — Root-cause the accuracy gap with real evidence

```
Before starting: in BOARD.html, set BENCH-1's status to "In Progress".

BENCHMARKS.md's Run B analysis already guesses the cause of the
multi_session/temporal_reasoning/extraction/knowledge_update gap is
search() "returning only one of the two relevant turns" at top_k=5. Check
this against real data before accepting it: every one of those categories'
questions (benchmarks/retrieval_quality/dataset.py) plants exactly 2 turns
total in its scope, and top_k defaults to 5 — 2 <= 5, so both turns should
be retrieved every time. The existing hypothesis may be wrong.

Add temporary debug instrumentation to run_retrieval_quality()
(benchmarks/retrieval_quality/run.py) that, for every question the judge
marks INCORRECT, logs: the full ordered `results` list from
store.working.search() (content + rank + distance if available), the
`retrieved_context` string actually handed to the judge, and the matching
flat-context baseline string for the same question id. Re-run
`--suite retrieval --baseline` at Run B's exact config (embedding-provider
ollama, judge ollama:llama3.1:8b, dataset-size 10, seed 42) and inspect
every failing question in the four negative-delta categories.

Test these candidate root causes with the logged evidence, don't assume
one:
1. Recall — is a relevant turn actually missing from `results`?
2. Ordering — store.working.search() ranks by vector distance to the
   query; run_baseline()'s flat context is always in original session
   order. Compare the two join expressions in run.py directly. For
   temporal_reasoning ("before the promotion") and knowledge_update
   ("CURRENT... language"), a scrambled presentation order is a plausible
   confounder distinct from missing recall.
3. Judge non-determinism — local Ollama models aren't necessarily
   deterministic run-to-run; re-run the same failing questions 2-3x and
   see if the verdict flips.

This is a diagnostic story — land no fix beyond the instrumentation
itself, and remove or gate it behind --debug before finishing (don't
leave permanent noisy logging on the hot path).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry with the confirmed root cause per category, and correct
BENCHMARKS.md's Run B "Analysis" section if the existing hypothesis is
wrong or incomplete. In BOARD.html, set BENCH-1's status to "Done" and
add a comment summarizing the findings. Then
`git add -A && git commit -m "bench-1: root-cause retrieval-quality gap with logged evidence"`.
```

---

## BENCH-2 — Fix result ordering, if BENCH-1 confirms it's a factor

```
Before starting: in BOARD.html, set BENCH-2's status to "In Progress".
Read BENCH-1's DECISIONS.md findings first — only proceed with a code
change here if BENCH-1 confirmed ordering as a real contributor.

store.working.search() ranks by vector distance to the query;
run_retrieval_quality() joins `results` in that rank order
("\n".join(r.content for r in results)), while run_baseline() joins turns
in original session/chronological order. If BENCH-1's evidence shows this
reordering flips judge verdicts, fix it at the layer the evidence points
to:

- If it's a harness-only concern: sort `results` by created_at before
  building retrieved_context in run_retrieval_quality() — small, local,
  no SDK API change, no migration.
- If real callers of search() would hit the same problem (not just this
  synthetic benchmark): consider whether MemoryStore/BaseRepository.
  search() should support an explicit ordering option (e.g.
  order_by="relevance" default vs "chronological") — only pursue this
  larger SDK-level version if BENCH-1's evidence shows it's a general
  problem, not a benchmark-harness artifact. Justify the choice either
  way in DECISIONS.md.

Before starting: read DECISIONS.md in full, including BENCH-1's entry.
Before finishing: re-run --suite retrieval --baseline at Run B's exact
config, record the new category deltas in BENCHMARKS.md as a new dated
run (append, don't overwrite Run B), append a dated DECISIONS.md entry.
In BOARD.html, set BENCH-2's status to "Done" with a comment summarizing
the fix and the before/after delta — or, if BENCH-1 refuted the ordering
hypothesis, close it "Done" with a comment explaining why no change was
needed. Then
`git add -A && git commit -m "bench-2: fix search() result ordering in retrieval-quality suite"`.
```

---

## BENCH-3a — Build a real fact-extraction Consolidator for the benchmark

```
Before starting: in BOARD.html, set BENCH-3a's status to "In Progress".

First of three sub-stories wiring the ENH-3/ENH-4 machinery (already
built and Done in EPIC-2) into the benchmark's write path. Today
scripts/run_benchmarks.py always constructs MemoryStore with
consolidator=None for the retrieval-quality suite — the --consolidator
mock flag only wires MockConsolidator (a cost-tracking demo with no real
extraction logic) into the latency suite; it never runs for
--suite retrieval.

Build a Consolidator implementation appropriate for the benchmark's
synthetic, single-fact-per-turn sessions (an LLM-based one using the same
local Ollama model already configured as judge, or a lighter
template-matching one if that proves sufficient — justify the choice)
that, given the raw turns MemoryStore.remember() passes it, produces
SemanticFact records via store.facts. Wire it into
run_retrieval_quality()'s MemoryStore construction as a new optional
parameter (not a hardcoded default), so the suite can run with or without
consolidation for a clean before/after comparison.

Before starting: read DECISIONS.md in full, including the ENH-3/ENH-4/
PH-6 entries this depends on, and BENCH-1's findings. Before finishing:
append a dated entry describing the extraction logic and its limitations
on this synthetic dataset. In BOARD.html, set BENCH-3a's status to "Done"
with a comment. Do not change run_retrieval_quality()'s default behavior
in this story — BENCH-3c wires it in and re-scores. Then
`git add -A && git commit -m "bench-3a: real fact-extraction consolidator for benchmark suite"`.
```

---

## BENCH-3b — Wire a Reconciler so stale knowledge_update facts are superseded

```
Before starting: in BOARD.html, set BENCH-3b's status to "In Progress".

Second sub-story of the Consolidator/Reconciler wiring fix. Once
BENCH-3a's Consolidator is producing SemanticFact records, knowledge_update
(a fact stated, then explicitly contradicted in a later session) is
exactly the case the ENH-3 Reconciler protocol was built for: detect the
contradiction and call SemanticFactRepository.supersede() so the stale
fact is excluded from search()/list_all() (superseded_at IS NOT NULL,
already implemented and tested in ENH-3/VER-10) instead of handing both
facts to the judge and hoping it infers which one is "CURRENT."

Build a Reconciler for the benchmark suite (same style decision as
BENCH-3a — LLM-based via the local Ollama model, or pattern-matching
given the synthetic dataset's explicit contradiction phrasing, e.g.
"actually, I've switched") and wire MemoryStore.reconcile(memory_type,
scope) into the retrieval-quality run after each question's sessions are
written, before search() is called.

Before starting: read DECISIONS.md in full, including BENCH-3a's entry.
Before finishing: append a dated entry. In BOARD.html, set BENCH-3b's
status to "Done" with a comment. Then
`git add -A && git commit -m "bench-3b: wire reconciler for knowledge_update supersession in benchmark suite"`.
```

---

## BENCH-3c — Search consolidated facts and re-score the full suite

```
Before starting: in BOARD.html, set BENCH-3c's status to "In Progress".

Third sub-story closing out the Consolidator/Reconciler fix. Today
run_retrieval_quality() only ever calls store.working.search() — raw
turns, never store.facts. With BENCH-3a's Consolidator promoting
multi-session facts into single SemanticFact records and BENCH-3b's
Reconciler superseding stale ones, the search step needs to actually use
them: either search store.facts in addition to (or instead of)
store.working, merging/deduping results, or make the search target
configurable so both modes stay comparable.

Re-run Run B's exact configuration (--suite retrieval --baseline
--embedding-provider ollama --judge ollama:llama3.1:8b --dataset-size 10
--seed 42) with consolidation+reconciliation wired in, and record the new
category-by-category deltas as a new dated run in BENCHMARKS.md, directly
comparable to Run B — this is the number that proves or disproves whether
the ENH-3/ENH-4 wiring actually closes the gap. If it doesn't close as
expected, say so plainly rather than declaring victory.

Before starting: read DECISIONS.md in full, including BENCH-3a/3b's
entries. Before finishing: append a dated entry with the full
before/after comparison and an honest assessment. In BOARD.html, set
BENCH-3c's status to "Done" with a comment summarizing the before/after
deltas. Then
`git add -A && git commit -m "bench-3c: search consolidated facts in retrieval-quality suite, re-score"`.
```

---

## BENCH-4 — Close the extraction/knowledge_update -10% gap independent of consolidation

```
Before starting: in BOARD.html, set BENCH-4's status to "In Progress".
Check BENCH-1's findings first — this story may turn out to be redundant
with BENCH-1's root cause.

extraction and knowledge_update only regressed -10.0% each (vs -30.0%
for the multi-session categories) — a distinct, smaller-scope question
from the Consolidator/Reconciler wiring in BENCH-3a/3b/3c that shouldn't
block on it. Run the retrieval-quality suite (Run B's seed=42,
n=10-per-category) sweeping --top-k (e.g. 5, 10, 20) and comparing
--embedding-provider ollama (nomic-embed-text) against
--embedding-provider sentence-transformers, isolating which knob (if
either) closes the gap. At n=10 per category, be explicit in the
write-up about signal vs. noise — don't over-claim a fix from a couple of
flipped questions.

Before starting: read DECISIONS.md in full, including BENCH-1's findings.
Before finishing: append a dated entry with the sweep results and a
recommendation (or explicit non-recommendation) for the harness's default
top_k. In BOARD.html, set BENCH-4's status to "Done" with a comment.
Then
`git add -A && git commit -m "bench-4: top_k/embedding-provider sweep for extraction and knowledge_update gap"`.
```

---

## BENCH-5 — Validate the "SDK wins at scale" hypothesis

```
Before starting: in BOARD.html, set BENCH-5's status to "In Progress".

BENCHMARKS.md's Run B analysis claims the flat-context baseline degrades
sharply once history grows to hundreds of turns (citing the LongMemEval
paper's 30-70% figure for frontier models), while the SDK's structured
retrieval holds steady — that's asserted from the paper, not measured on
this repo's own harness. Validate it before it's used to justify shipping
a -10% overall regression as acceptable on the short-session dataset.

Add a configurable session-length/session-count knob to
benchmarks/retrieval_quality/dataset.py's generators (e.g. padding each
session with additional unrelated planted facts/turns, or generating more
sessions per question, scaling toward the hundreds-of-turns range the
paper's comparison point uses) gated behind a new CLI flag so the
existing default dataset shape is unchanged. Re-run both
run_retrieval_quality() and run_baseline() at increasing scale (small/
medium/large session counts) and record how each mode's accuracy trends
as context grows, in BENCHMARKS.md as a new section distinct from Run
A/B/C.

Sequence this after BENCH-1 through BENCH-4 land, since those affect what
the with-SDK path scores at any scale — but the dataset-generator changes
can be built independently if useful to start earlier.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry with the at-scale results and an honest verdict — confirmed,
partially confirmed, or refuted. In BOARD.html, set BENCH-5's status to
"Done" with a comment. Then
`git add -A && git commit -m "bench-5: validate SDK-vs-baseline behavior at larger session scale"`.
```

---

# Epic 7 — Next-gen memory pipeline features: fresh 2026 research on Mem0, Microsoft Agent Framework, and Oracle AI Agent Memory

Everything below is tracked as `EPIC-7` in [`BOARD.html`](BOARD.html)
(Stories `PIPE-1` through `PIPE-6`). Distinct from `EPIC-2`
(Cosmos-inspired, Done) and `EPIC-3` (Oracle-inspired, Done) — those two
were scoped from the July 2026 snapshot in
`ai-agent-platform-competitive-analysis.md`. This epic is grounded in
dedicated follow-up research (2026-07-31 — see the matching dated
`DECISIONS.md` entry for the full writeup) into the exact pipeline
mechanics of three platforms that survey only summarized at a high level:
Mem0's real-time per-write `ADD`/`UPDATE`/`DELETE`/`NOOP` classification
(a candidate fact compared via cosine similarity to top-k existing
memories, with an LLM policy routing the outcome — distinct from this
SDK's existing `ENH-3` Reconciler, which only batch-scans already-written
facts for contradictions); Microsoft Agent Framework's `ContextProvider`/
`HistoryProvider` lifecycle-hook adapter shape (`before_run`/`after_run`,
GA as of April 2026, confirmed via current Microsoft Learn docs dated
2026-07-10) — a fundamentally different integration pattern than the
`Step 6` LangChain/OpenAI-Agents/MCP adapters; and Oracle AI Agent
Memory's 26.6 release (hybrid semantic+keyword search now GA, context-card
per-type minimum-result balancing, `MemoryExtractionConfig`), which
shipped after `EPIC-3` was originally scoped. This epic also closes two
items `VER-13`'s market-fit check left as documented PARTIAL/open: hybrid
retrieval (`PIPE-1`) and ergonomic GDPR-style erasure (`PIPE-5`).

Same working agreement as every other epic (read `DECISIONS.md` first,
update it + `BOARD.html` before finishing, commit each story separately),
and the same Step 0 philosophy: Db2-only, zero mandatory new
infrastructure, developer-controlled writes by default. Every new hook
introduced here (`IngestResolver`, `hybrid=True`, the new adapter) is
opt-in and must leave today's default behavior unchanged — do not make
this epic's stories the default path.

Suggested order: `PIPE-1`, `PIPE-2`, `PIPE-5`, and `PIPE-6` are each fully
independent and can be done in any order or in parallel. `PIPE-3` is also
independent (a new adapter, touching nothing else). `PIPE-4` depends on
`ORC-1`'s `ContextCard`/`get_context_card()` (Done, `EPIC-3`) as the base
it extends — do that one last if you want the smallest possible diff to
review against a stable base, though nothing blocks starting it earlier.

---

## PIPE-1 — Hybrid retrieval: keyword scoring fused with vector search via reciprocal rank fusion

```
Before starting: in BOARD.html, set PIPE-1's status to "In Progress".

Add an optional `hybrid: bool = False` parameter to `search()` (and
`_search_via_chunks()`). When enabled, compute a keyword-overlap score per
candidate row (token-set overlap against the query string, computed in
Python over the same candidate set already fetched — no new query)
alongside the existing `VECTOR_DISTANCE` ranking, then fuse the two
ranked lists via Reciprocal Rank Fusion (RRF: score = sum(1/(k+rank))
across both rankings, k=60 as the standard RRF default) into the final
result order, rather than a hand-tuned weighted average.

Do NOT depend on Db2's Text Search Extender (`CONTAINS`/`SCORE`/
`CONTAINS_ANY`/`CONTAINS_ALL`) for this. The 2026-07-31 EPIC-2 research
entry already flagged that current-version (12.1) documentation for that
extender couldn't be confidently confirmed at the time; a fresh check for
this epic still couldn't confirm whether it ships enabled-by-default
versus requiring separate DBA-run enablement (IBM's own "How to enable
TEXT SEARCH for a DB2 database" support article describes it as an
installable extender, historically opt-in, not a core SQL feature). A
Python-side fusion keeps this zero-mandatory-infrastructure, matching the
Step 0 principle, and is directly comparable in spirit to how both Oracle
and Mem0 describe "hybrid = semantic + keyword in the same search flow"
without requiring callers to provision a separate search engine.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the RRF formula/constant used and confirming the
keyword-scoring approach taken, plus a note that Db2 Text Search Extender
remains an unconfirmed future upgrade path rather than something this
story depends on. In BOARD.html, set PIPE-1's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "pipe-1: hybrid retrieval via RRF-fused keyword+vector search"`.
```

---

## PIPE-2 — Ingest resolution: pluggable ADD/UPDATE/DELETE/NOOP classifier at write time

```
Before starting: in BOARD.html, set PIPE-2's status to "In Progress".

Add an `IngestResolver` protocol to `types.py`, parallel in shape to
`Consolidator`/`Reconciler`: `(candidate, similar: list[tuple[model,
distance]]) -> IngestDecision`, where `IngestDecision` names one of
`ADD`/`UPDATE`/`DELETE`/`NOOP` plus, for `UPDATE`/`DELETE`, the target
record id. Ship a `NoOpIngestResolver` default (always `ADD` — today's
unchanged behavior). Wire it as an optional `ingest_resolver=` constructor
arg on `MemoryStore`; when configured, `remember()` first runs `search()`
against the same-type table (scoped, `top_k=resolver_k`) to find similar
existing records, passes the candidate plus those results to the
resolver, and acts on the decision: `ADD` inserts as today, `UPDATE`
calls the existing optimistic-concurrency `update()` on the target id,
`DELETE` calls `forget()` on the target id, `NOOP` skips the write
entirely.

This is a pipeline stage the SDK doesn't have today: `ENH-3`'s Reconciler
runs later, in batches, over already-written non-superseded facts,
looking specifically for contradictions between them. This new resolver
runs once, at write time, against the top-k most-similar candidates by
cosine distance (not a batch scan), and can choose to merge/update/
discard/no-op the incoming write itself — the real-time
classify-against-existing-similar-memories step Mem0's pipeline is
actually built around.

Keep this strictly opt-in (`ingest_resolver=None` default) — the
"developer-controlled writes, not mandatory passive extraction"
positioning is a deliberate differentiator called out in
ai-agent-platform-competitive-analysis.md's SWOT, and this story must not
make the default write path any heavier.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry describing the protocol shape and confirming the default
path is unchanged when no resolver is configured. In BOARD.html, set
PIPE-2's status to "Done" and add a comment summarizing what you built.
Then
`git add -A && git commit -m "pipe-2: pluggable ingest resolver (ADD/UPDATE/DELETE/NOOP)"`.
```

---

## PIPE-3 — Framework adapter: Microsoft Agent Framework ContextProvider/HistoryProvider

```
Before starting: in BOARD.html, set PIPE-3's status to "In Progress".

Microsoft Agent Framework (GA April 3, 2026, unifying AutoGen + Semantic
Kernel) uses a fundamentally different adapter shape than the three
frameworks this SDK already integrates with (Step 6: LangChain, OpenAI
Agents SDK's Session protocol, MCP) — a lifecycle-hook pattern rather
than a store/session interface. Its Python `ContextProvider` base class
exposes `async before_run(*, agent, session, context: SessionContext,
state: dict)` (called before the model is invoked — inject retrieved
memory via `context.extend_instructions(source_id, text)`) and `async
after_run(*, agent, session, context, state)` (called after the response
— extract/persist new memory). A specialized `HistoryProvider` subclass
instead implements `async get_messages(session_id, *, state, **kwargs)
-> list[Message]` and `async save_messages(session_id, messages, *,
state, **kwargs)`.

Add `src/agent_memory_sdk/adapters/agent_framework.py` (new
`[agent-framework]` optional extra, following the exact pattern of the
existing `[langchain]`/`[openai-agents]`/`[mcp]` extras) with two
classes: `MemoryStoreContextProvider(ContextProvider)` whose `before_run`
calls `store.search()`/`store.get_context_card()` for the current scope
and injects results via `context.extend_instructions()`, and whose
`after_run` calls `store.remember()` on the turn's request/response
messages; and `MemoryStoreHistoryProvider(HistoryProvider)` whose
`get_messages()`/`save_messages()` map directly onto
`store.working.list_all()`/`store.remember()`. Session-specific state
(e.g. a memory-scope identifier) must live in the `AgentSession`/`state`
dict passed to each call, never on the provider instance itself — the
same provider instance is shared across all sessions, a constraint
Microsoft's own docs call out explicitly.

Add adapter tests in `tests/test_adapters.py` following the existing
per-adapter structure (mock the framework's `ContextProvider`/
`HistoryProvider` base classes the same way the existing
LangChain/OpenAI-Agents/MCP tests mock theirs).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exact classes/methods implemented and confirm
the new `[agent-framework]` extra installs cleanly. In BOARD.html, set
PIPE-3's status to "Done" and add a comment summarizing what you built.
Then
`git add -A && git commit -m "pipe-3: Microsoft Agent Framework ContextProvider/HistoryProvider adapter"`.
```

---

## PIPE-4 — Context card v2: blend durable long-term memory into the short-term card, with per-type minimum balancing

```
Before starting: in BOARD.html, set PIPE-4's status to "In Progress".

ORC-1's `get_context_card()` (Done, EPIC-3) returns only a raw
chronological slice of recent working-memory turns plus an optional
summarizer hook — it does not pull in any long-term memory. Oracle AI
Agent Memory's `get_context_card()` returns a richer bundle: a summary,
relevant durable records (facts/profiles retrieved by relevance to the
current thread, not just recency), retrieval topics, and recent messages
— and its 26.6 release added the ability to set a minimum result count
per record type so context assembly doesn't get dominated by one memory
type (e.g. all recent turns, zero relevant facts).

Extend `ContextCard` with optional `relevant_facts: list[SemanticFact]`
and `relevant_profiles: list[EntityProfile]` fields, populated when
`get_context_card(scope, query=..., include_long_term=True,
min_results_by_type={'facts': 2, 'profiles': 1})` is called with a query
string: run `store.facts.search()`/`store.profiles.search()` for that
scope/query, and if a type falls below its configured minimum, backfill
with its most-recent (not just most-relevant) records for that type so a
thin/early-scope conversation doesn't return an empty section. Default
behavior (no `query` passed) must stay exactly as ORC-1 left it — this is
purely additive.

Before starting: read DECISIONS.md in full, including ORC-1's entry.
Before finishing: append a dated entry describing the new fields/
parameters and confirming the no-query default path is byte-for-byte
unchanged. In BOARD.html, set PIPE-4's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "pipe-4: context card v2 with blended long-term memory and per-type minimums"`.
```

---

## PIPE-5 — Ergonomic erasure: erase_all(scope) with an ErasureReport

```
Before starting: in BOARD.html, set PIPE-5's status to "In Progress".

VER-13's market-fit check documented this SDK's erasure story as PARTIAL:
the `forget()` primitive exists (per-record soft-delete tombstone) but
there's no single user-scoped "erase everything for this person" API or
erasure report — a real GDPR-style workflow gap. Oracle AI Agent Memory's
own erasure story, per current documentation, is not a single magic API
either — it's search, list, and per-record delete operations across
memories, threads, and messages, so callers can locate records for a
subject and remove them on request, with Oracle Database's native
auditing covering the storage layer underneath. This SDK can still do
meaningfully better ergonomically without inventing something no vendor
actually ships.

Add `MemoryStore.erase_all(scope: MemoryScope) -> ErasureReport`: unlike
`forget()` (soft-delete, reversible, used for routine memory lifecycle),
this is a genuine hard-delete across all five repositories plus
`memory_chunks` for every row matching the given scope — appropriate
specifically for a compliance erasure request, not everyday forgetting.
Return an `ErasureReport` dataclass: a per-table `rows_deleted` count, a
total, and a timestamp, so the caller has an auditable record of what was
actually erased. Document clearly in the docstring that this bypasses
the tombstone/`deleted_at` lifecycle entirely and is irreversible — a
deliberately different guarantee from `forget()`.

Before starting: read DECISIONS.md in full, including the VER-13 entry.
Before finishing: append a dated entry recording the ErasureReport shape
and confirming which tables are covered. In BOARD.html, set PIPE-5's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "pipe-5: erase_all(scope) with ErasureReport for GDPR-style erasure"`.
```

---

## PIPE-6 — Memory export/import for portability and backup

```
Before starting: in BOARD.html, set PIPE-6's status to "In Progress".

ai-agent-platform-competitive-analysis.md's gap analysis (#3) notes no
standard export/interchange format exists anywhere in the industry —
migrating between vendors means rewriting, and even the feature-matrix's
"Import/export" entries for Mem0/Oracle are each proprietary to that
vendor, not interoperable with each other. This story does not attempt
to solve the unsolved cross-vendor problem; it solves this SDK's own,
narrower gap — there is currently no way to back up or migrate a
tenant/agent's memory out of Db2 at all.

Add `MemoryStore.export_scope(scope: MemoryScope) -> Iterator[dict]`
yielding one JSON-serializable record per row across all five memory
tables plus `memory_chunks` matching the scope (tagged with a `_type`
discriminator field), and `MemoryStore.import_scope(records:
Iterable[dict], scope: MemoryScope)` that re-inserts them via the
existing per-type `create()` methods (re-validating scope match on every
record — reject with a clear error if an imported record's scope doesn't
match the target scope, rather than silently rewriting it). Provide a
`scripts/export_memory.py`/`scripts/import_memory.py` pair (JSONL on
disk) as the reference CLI usage, matching the existing
`scripts/purge_expired.py`/`scripts/consolidate_pending.py` pattern.
Document explicitly that this is this SDK's own proprietary format
(embedding vectors included as raw float lists), not a cross-vendor
interchange standard — none exists industry-wide per the market study.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exported record shape and the reference
CLI scripts added. In BOARD.html, set PIPE-6's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "pipe-6: memory export/import for portability and backup"`.
```

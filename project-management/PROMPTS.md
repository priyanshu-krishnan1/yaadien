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
`INTEGRATION_TESTING.md`, `Chats.md`, the market study, and every
`audit-prompt*.md` all live together under **`project-management/`** at the
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
PROMPTS.md (this file), INTEGRATION_TESTING.md, Chats.md, the market study,
and every audit-prompt*.md — live under project-management/ at the repo
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
